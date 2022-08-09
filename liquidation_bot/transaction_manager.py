import logging
from typing import List, Dict
import web3
from eth_typing import ChecksumAddress
from web3 import Web3
from web3.middleware import construct_sign_and_send_raw_middleware, geth_poa_middleware
from web3.gas_strategies.rpc import rpc_gas_price_strategy

class TransactionManager:
    def __init__(
        self,
        infura_key: str,
        network: str,
        private_key: str,
        strategies_addresses: List[ChecksumAddress],
        strategies_abi: Dict,
        liquidator_address: ChecksumAddress,
        liquidator_abi: Dict,
    ):
        self.eth_balance = 0.0
        self.infura_key = infura_key
        self.network = network
        self.private_key = private_key
        self.strategies_addresses = strategies_addresses
        self.strategies_abi = strategies_abi
        self.liquidator_address = liquidator_address
        self.liquidator_abi = liquidator_abi
        self.liquidator = {}
        self.strategies = []
        self.open_event_filters = []
        self.close_event_filters = []
        self.liquidation_event_filters = []
        self.open_positions = [[] for i in range(len(strategies_addresses))]

        self._init_web_handle()
        self._init_account()
        self._init_contracts()
        self._init_filters()

        logging.info("Created TransactionManager")


    def _init_web_handle(self) -> None:
        self.web3Handle = Web3(
            Web3.HTTPProvider(
                f"https://{self.network}.infura.io/v3/{self.infura_key}"
            )
        )


    def _init_account(self) -> None:
        self.account = web3.eth.Account.privateKeyToAccount(self.private_key)
        self.web3Handle.middleware_onion.add(
            construct_sign_and_send_raw_middleware(self.account)
        )
        self.web3Handle.middleware_onion.inject(geth_poa_middleware, layer=0)
        self.web3Handle.eth.set_gas_price_strategy(rpc_gas_price_strategy)


    def _init_contracts(self) -> None:
        self.liquidator = self.web3Handle.eth.contract(
            address = self.liquidator_address,
            abi = self.liquidator_abi,
        )

        for i in range(len(self.strategies_addresses)):
            contract = self.web3Handle.eth.contract(
                address = self.strategies_addresses[i],
                abi = self.strategies_abi,
            )
            self.strategies.append(contract)

    def _init_filters(self) -> None:
        for i in range(len(self.strategies)):
            self.open_event_filters.append(
                self.strategies[i].events.PositionWasOpened.createFilter(fromBlock=0)
            )
            self.close_event_filters.append(
                self.strategies[i].events.PositionWasClosed.createFilter(fromBlock=0)
            )
            self.liquidation_event_filters.append(
                self.strategies[i].events.PositionWasLiquidated.createFilter(fromBlock=0)
            )


    def update_positions(self):
        for i in range(len(self.strategies)):
            try:
                self.open_positions[i][0]

                for PositionWasOpened in self.open_event_filters[i].get_new_entries():
                    self.open_positions[i].append(PositionWasOpened['args']['id'])

                for PositionWasClosed in self.close_event_filters[i].get_new_entries():
                    self.open_positions[i].remove(PositionWasClosed['args']['id'])

                for PositionWasLiquidated in self.liquidation_event_filters[i].get_new_entries():
                    self.open_positions[i].remove(PositionWasLiquidated['args']['id'])
            except:
                for PositionWasOpened in self.open_event_filters[i].get_all_entries():
                    self.open_positions[i].append(PositionWasOpened['args']['id'])

                for PositionWasClosed in self.close_event_filters[i].get_all_entries():
                    self.open_positions[i].remove(PositionWasClosed['args']['id'])

                for PositionWasLiquidated in self.liquidation_event_filters[i].get_all_entries():
                    self.open_positions[i].remove(PositionWasLiquidated['args']['id'])


    def check_liquidability(self) -> List:
        liquidated_positions = []

        for i in range(len(self.strategies)):
            logging.info(
                f"Open positions: {len(self.open_positions[i])}"
            )

            for j in range(len(self.open_positions[i])):
                position = self.strategies[i].functions.positions(self.open_positions[i][j]).call()
                score = self.strategies[i].functions.computeLiquidationScore(position).call()[0]
                if(score > 0):
                    logging.info(
                        f"Preparing to liquidate position #{self.open_positions[i][j]}"
                    )
                    txn = self.liquidator.functions.liquidateSingle(self.strategies_addresses[i], self.open_positions[i][j])
                    if(self.sign_and_send(txn)):
                        strategy = "MarginTradingStrategy" if i == 0 else "YearnStrategy"
                        liquidated_positions.append(f"Position #{self.open_positions[i][j]} of {strategy} was liquidated")

        return liquidated_positions


    def sign_and_send(self, txn) -> bool:
        logging.info("Preparing to transact")

        try:
            account_address = self.account.address
            logging.info(
                f"Account balance in ETH before: {Web3.fromWei(self.web3Handle.eth.getBalance(account_address), 'ether')}"
            )
            assert self.web3Handle.eth.getBalance(account_address) > 0
            nonce = self.web3Handle.eth.getTransactionCount(account_address)
            txn_dict = txn.buildTransaction({
                "nonce": nonce,
                "gasPrice": self.web3Handle.eth.generate_gas_price() * 2,
            })
            logging.info(
                f"Estimated gas in ETH for current transaction: {Web3.fromWei(self.web3Handle.eth.estimateGas(txn_dict)*10**9, 'ether')}"
            )
            signed_txn = self.web3Handle.eth.account.signTransaction(
                txn_dict, private_key=self.private_key
            )
            result = self.web3Handle.eth.sendRawTransaction(signed_txn.rawTransaction)
            self.web3Handle.eth.wait_for_transaction_receipt(result.hex())
            eth_balance_after = Web3.fromWei(self.web3Handle.eth.getBalance(account_address), "ether")
            logging.info(
                f"Account balance in ETH after: {eth_balance_after}"
            )
            self.eth_balance = eth_balance_after

            return True
        except Exception as e:
            logging.error(e)

            return False
