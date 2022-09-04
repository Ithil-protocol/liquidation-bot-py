import asyncio
import json
import logging
import os
from typing import Dict

import telegram
from aiohttp import web
from aiohttp.web_runner import GracefulExit
from dotenv import load_dotenv
from eth_typing.evm import ChecksumAddress
from web3 import Web3
from web3.contract import Contract
from web3.types import ABI

from liquidation_bot.config import Configuration
from liquidation_bot.constants import (
    BOT,
    DEFAULT_SLEEP_DURATION_IN_SECONDS,
    ETH_BALANCE,
    LIQUIDATOR,
    STRATEGIES,
)
from liquidation_bot.transaction_manager import TransactionManager

logging.basicConfig(
    format="%(asctime)s %(message)s", datefmt="%m/%d/%Y %I:%M:%S %p", level=logging.INFO
)


def load_configuration() -> Configuration:
    load_dotenv()

    return Configuration(
        infura_api_key=os.environ["INFURA_API_KEY"],
        network=os.environ["NETWORK"],
        private_key=os.environ["PRIVATE_KEY"],
        sleep_duration_in_seconds=int(
            os.environ.get(
                "SLEEP_DURATION_IN_SECONDS", DEFAULT_SLEEP_DURATION_IN_SECONDS
            )
        ),
        telegram_chat_id=os.environ["TELEGRAM_CHAT_ID"],
        telegram_key=os.environ["TELEGRAM_KEY"],
    )


def deployment_contract_file_path(network: str, contract_name: str) -> str:
    return os.path.join("deployed/" + network + "/abi", contract_name + ".json")


def load_abi_from_file(path: str) -> ABI:
    with open(path, "r") as f:
        return json.load(f)


def deployment_addresses_file_path(network: str) -> str:
    return os.path.join("deployed/" + network + "/deployments/core.json")


def make_address(addresses: Dict[str, str], contract_name: str) -> ChecksumAddress:
    return Web3.toChecksumAddress(addresses[contract_name])


def setup_web3(network: str, infura_api_key: str) -> Web3:
    return Web3(Web3.HTTPProvider(f"https://{network}.infura.io/v3/{infura_api_key}"))


def load_contract_address(network: str, contract_name: str) -> ChecksumAddress:
    path = f"deployed/{network}/deployments/{contract_name}.json"
    with open(path, "r") as f:
        data = json.load(f)
        address = data["address"]
        return Web3.toChecksumAddress(address)


def setup_contract(web3_handle: Web3, abi: ABI, address: ChecksumAddress) -> Contract:
    return web3_handle.eth.contract(address=address, abi=abi)


def _setup_transaction_manager(
    network: str,
    infura_api_key: str,
    private_key: str,
) -> TransactionManager:
    web3_handle = setup_web3(network=network, infura_api_key=infura_api_key)
    liquidator = setup_contract(
        web3_handle=web3_handle,
        abi=load_abi_from_file(
            path=deployment_contract_file_path(
                network=network,
                contract_name=LIQUIDATOR,
            )
        ),
        address=load_contract_address(network=network, contract_name=LIQUIDATOR),
    )
    strategies = [
        setup_contract(
            web3_handle=web3_handle,
            abi=load_abi_from_file(
                path=deployment_contract_file_path(
                    network=network,
                    contract_name=strategy,
                ),
            ),
            address=load_contract_address(network=network, contract_name=strategy),
        )
        for strategy in STRATEGIES
    ]

    return TransactionManager(
        web3_handle=web3_handle,
        private_key=private_key,
        liquidator=liquidator,
        strategies=strategies,
    )


def _setup_telegram_bot(telegram_chat_id: str, telegram_key: str) -> telegram.Bot:
    bot = telegram.Bot(token=telegram_key)
    bot.sendMessage(chat_id=telegram_chat_id, text="Bot online")

    return bot


async def _run_liquidation_bot(app):
    config = load_configuration()

    transaction_manager = _setup_transaction_manager(
        network=config.network,
        infura_api_key=config.infura_api_key,
        private_key=config.private_key,
    )

    app[BOT] = _setup_telegram_bot(
        telegram_chat_id=config.telegram_chat_id,
        telegram_key=config.telegram_key,
    )

    transaction_manager.init_positions()

    while True:
        transaction_manager.update_positions()
        liquidated_positions = transaction_manager.check_liquidability()
        for val in liquidated_positions:
            app[BOT].sendMessage(chat_id=config.telegram_chat_id, text=val)
        await asyncio.sleep(config.sleep_duration_in_seconds)


async def run_liquidation_bot(app):
    try:
        await _run_liquidation_bot(app)
    except:
        app[BOT].sendMessage(chat_id="-1001557806519", text="Bot offline")

        raise GracefulExit()


async def start_liquidation_bot(app):
    app["liquidation_bot_task"] = asyncio.create_task(run_liquidation_bot(app))


async def handle_http_request(request: web.Request) -> web.Response:
    balance = request.app[ETH_BALANCE]
    return web.Response(
        text=f"""

Balance:    {balance} ETH

    """
    )


def run_app():
    app = web.Application()
    app.on_startup.append(start_liquidation_bot)
    app.add_routes([web.get("/", handle_http_request)])

    app[ETH_BALANCE] = 0.0

    web.run_app(app, port=8080)


if __name__ == "__main__":
    run_app()
