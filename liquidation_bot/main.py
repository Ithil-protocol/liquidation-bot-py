import asyncio
import configparser
import json
import logging
import os
from argparse import ArgumentParser
from typing import Dict

import telegram
from aiohttp import web
from aiohttp.web_runner import GracefulExit
from eth_typing.evm import ChecksumAddress
from web3 import Web3
from web3.contract import Contract
from web3.types import ABI

from liquidation_bot.constants import (
    BOT,
    ETH_BALANCE,
    LIQUIDATOR,
    MARGIN_TRADING_STRATEGY,
    STRATEGIES,
    YEARN_STRATEGY,
)
from liquidation_bot.transaction_manager import TransactionManager

logging.basicConfig(
    format="%(asctime)s %(message)s", datefmt="%m/%d/%Y %I:%M:%S %p", level=logging.INFO
)


def _get_from_config_or_env_var(
    config: Dict,
    section: str,
    key: str,
) -> str:
    value = config[section][key]
    if value == "":
        value = os.environ[key]

    return value


def deployment_contract_file_path(network: str, contract_name: str) -> str:
    return os.path.join("deployed/" + network + "/abi", contract_name + ".json")


def load_abi_from_file(path: str) -> ABI:
    with open(path, "r") as f:
        return json.load(f)


def deployment_addresses_file_path(network: str) -> str:
    return os.path.join("deployed/" + network + "/deployments/core.json")


def load_addresses_from_file(path: str) -> Dict[str, str]:
    with open(path, "r") as f:
        return json.load(f)


def make_address(addresses: Dict[str, str], contract_name: str) -> ChecksumAddress:
    return Web3.toChecksumAddress(addresses[contract_name])


def setup_web3(network: str, infura_key: str) -> Web3:
    return Web3(Web3.HTTPProvider(f"https://{network}.infura.io/v3/{infura_key}"))


def setup_contract(
    web3_handle: Web3, abi: ABI, address: ChecksumAddress
) -> Contract:
    return web3_handle.eth.contract(address=address, abi=abi)


def _setup_transaction_manager(config) -> TransactionManager:
    network = config["DEFAULT"]["NETWORK"]
    addresses = load_addresses_from_file(
        path=deployment_addresses_file_path(network),
    )
    infura_key = _get_from_config_or_env_var(config, "API", "INFURA_API_KEY")
    private_key = _get_from_config_or_env_var(config, "USER", "PRIVATE_KEY")
    web3_handle = setup_web3(network=network, infura_key=infura_key)
    liquidator = setup_contract(
        web3_handle=web3_handle,
        abi=load_abi_from_file(
            path=deployment_contract_file_path(
                network=network,
                contract_name=LIQUIDATOR,
            )
        ),
        address=make_address(addresses=addresses, contract_name=LIQUIDATOR),
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
            address=make_address(addresses=addresses, contract_name=strategy),
        )
        for strategy in STRATEGIES
    ]

    return TransactionManager(
        web3_handle=web3_handle,
        private_key=private_key,
        liquidator=liquidator,
        strategies=strategies,
    )


def _setup_telegram_bot(config) -> telegram.Bot:
    bot_token = _get_from_config_or_env_var(config, "API", "TELEGRAM_KEY")
    chatid = _get_from_config_or_env_var(config, "API", "TELEGRAM_CHAT_ID")

    bot = telegram.Bot(token=bot_token)
    bot.sendMessage(chat_id=chatid, text="Bot online")

    return bot


async def _run_liquidation_bot(app):
    parser = ArgumentParser()
    parser.add_argument(
        "configfile", metavar="configfile", type=str, help="The bot configuration file"
    )
    args = parser.parse_args()

    config = configparser.ConfigParser()
    config.read(args.configfile)
    chatid = _get_from_config_or_env_var(config, "API", "TELEGRAM_CHAT_ID")
    sleep_duration = int(
        _get_from_config_or_env_var(config, "DEFAULT", "SLEEP_DURATION_IN_SECONDS")
    )

    transaction_manager = _setup_transaction_manager(config)
    # app[ETH_BALANCE] = transaction_manager.eth_balance

    app[BOT] = _setup_telegram_bot(config)

    while True:
        transaction_manager.update_positions()
        liquidated_positions = transaction_manager.check_liquidability()
        if len(liquidated_positions) > 0:
            for val in liquidated_positions:
                app[BOT].sendMessage(chat_id=chatid, text=val)

        await asyncio.sleep(sleep_duration)


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
