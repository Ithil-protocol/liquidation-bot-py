import logging
from typing import Dict, List, Set

import web3
from eth_typing.evm import ChecksumAddress
from web3 import Web3
from web3.contract import Contract
from web3.gas_strategies.rpc import rpc_gas_price_strategy
from web3.middleware.geth_poa import geth_poa_middleware
from web3.middleware.signing import construct_sign_and_send_raw_middleware
from web3.types import LogReceipt
from web3._utils.filters import LogFilter


class TransactionManager:
    def __init__(
        self,
        web3_handle: Web3,
        private_key: str,
        strategies: List[Contract],
        liquidator: Contract,
    ):
        self.eth_balance = 0.0
        self.private_key = private_key
        self.strategies = strategies
        self.liquidator = liquidator
        self.open_event_filters: Dict[ChecksumAddress, LogFilter] = {}
        self.close_event_filters: Dict[ChecksumAddress, LogFilter] = {}
        self.liquidation_event_filters: Dict[ChecksumAddress, LogFilter] = {}
        self.open_positions: Dict[ChecksumAddress, Set[int]] = {
            strategy.address: set() for strategy in strategies
        }
        self.web3_handle = web3_handle

        self._init_account()
        self._init_filters()

        logging.info("Created TransactionManager")

    def _init_account(self) -> None:
        self.account = web3.eth.Account.privateKeyToAccount(self.private_key)
        self.web3_handle.middleware_onion.add(
            construct_sign_and_send_raw_middleware(self.account)
        )
        self.web3_handle.middleware_onion.inject(geth_poa_middleware, layer=0)
        self.web3_handle.eth.set_gas_price_strategy(rpc_gas_price_strategy)

    def _init_filters(self) -> None:
        for strategy in self.strategies:
            self.open_event_filters[
                strategy.address
            ] = strategy.events.PositionWasOpened.createFilter(fromBlock=0)
            self.close_event_filters[
                strategy.address
            ] = strategy.events.PositionWasClosed.createFilter(fromBlock=0)
            self.liquidation_event_filters[
                strategy.address
            ] = strategy.events.PositionWasLiquidated.createFilter(fromBlock=0)

    def get_position_id(self, log_receipt: LogReceipt) -> int:
        # XXX we ignore the type hint here as the actual type of log receipt was overloaded
        return log_receipt["args"]["id"] # type: ignore

    def init_positions(self):
        for strategy in self.strategies:
            for position_was_opened in self.open_event_filters[
                strategy.address
            ].get_all_entries():
                position_id = self.get_position_id(position_was_opened)
                self.open_positions[strategy.address].add(position_id)

            for position_was_closed in self.close_event_filters[
                strategy.address
            ].get_all_entries():
                position_id = self.get_position_id(position_was_closed)
                self.open_positions[strategy.address].remove(position_id)

            for position_was_liquidated in self.liquidation_event_filters[
                strategy.address
            ].get_all_entries():
                position_id = self.get_position_id(position_was_liquidated)
                self.open_positions[strategy.address].remove(position_id)

    def update_positions(self):
        for strategy in self.strategies:
            for position_was_opened in self.open_event_filters[
                strategy.address
            ].get_new_entries():
                position_id = self.get_position_id(position_was_opened)
                self.open_positions[strategy.address].add(position_id)

            for position_was_closed in self.close_event_filters[
                strategy.address
            ].get_new_entries():
                position_id = self.get_position_id(position_was_closed)
                self.open_positions[strategy.address].remove(position_id)

            for position_was_liquidated in self.liquidation_event_filters[
                strategy.address
            ].get_new_entries():
                position_id = self.get_position_id(position_was_liquidated)
                self.open_positions[strategy.address].remove(position_id)

    def check_liquidability(self) -> List:
        liquidated_positions = []

        for strategy in self.strategies:
            for open_position in self.open_positions[strategy.address]:
                position = strategy.functions.positions(open_position).call()
                score = strategy.functions.computeLiquidationScore(position).call()[0]
                if score > 0:
                    logging.info(f"Preparing to liquidate position #{open_position}")
                    txn = self.liquidator.functions.liquidateSingle(
                        strategy.address, open_position
                    )
                    if self.sign_and_send(txn):
                        liquidated_positions.append(
                            f"Position #{open_position} of strategy {strategy.address} was liquidated"
                        )

        return liquidated_positions

    def sign_and_send(self, txn) -> bool:
        try:
            account_address = self.account.address
            logging.info(
                f"Account balance in ETH before: {Web3.fromWei(self.web3_handle.eth.getBalance(account_address), 'ether')}"
            )
            assert self.web3_handle.eth.getBalance(account_address) > 0
            nonce = self.web3_handle.eth.getTransactionCount(account_address)
            txn_dict = txn.buildTransaction(
                {
                    "nonce": nonce,
                    "gasPrice": self.web3_handle.eth.generate_gas_price() * 2,
                    "from": account_address,
                }
            )
            logging.info(
                f"Estimated gas in ETH for current transaction: {Web3.fromWei(self.web3_handle.eth.estimateGas(txn_dict)*10**9, 'ether')}"
            )
            signed_txn = self.web3_handle.eth.account.signTransaction(
                txn_dict, private_key=self.private_key
            )
            result = self.web3_handle.eth.sendRawTransaction(signed_txn.rawTransaction)
            self.web3_handle.eth.wait_for_transaction_receipt(result.hex())
            eth_balance_after = Web3.fromWei(
                self.web3_handle.eth.getBalance(account_address), "ether"
            )
            logging.info(f"Account balance in ETH after: {eth_balance_after}")
            self.eth_balance = eth_balance_after

            return True
        except Exception as e:
            logging.error(e)

            return False
