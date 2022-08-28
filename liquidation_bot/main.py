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
from web3 import Web3

from liquidation_bot.constants import (BOT, ETH_BALANCE, LIQUIDATOR,
                                       MARGIN_TRADING_STRATEGY, YEARN_STRATEGY)
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


def setup_web3(network: str, infura_key: str) -> Web3:
    return Web3(Web3.HTTPProvider(f"https://{network}.infura.io/v3/{infura_key}"))


def _setup_transaction_manager(config) -> TransactionManager:
    network = config["DEFAULT"]["NETWORK"]

    margintrading_abi_file = os.path.join(
        "deployed/" + network + "/abi", MARGIN_TRADING_STRATEGY + ".json"
    )
    liquidator_abi_file = os.path.join(
        "deployed/" + network + "/abi", LIQUIDATOR + ".json"
    )
    addresses_file = os.path.join("deployed/" + network + "/deployments/core.json")

    infura_key = _get_from_config_or_env_var(config, "API", "INFURA_API_KEY")
    private_key = _get_from_config_or_env_var(config, "USER", "PRIVATE_KEY")

    with open(margintrading_abi_file, "r") as abi_margintrading, open(
        liquidator_abi_file, "r"
    ) as abi_liquidator, open(addresses_file, "r") as addresses_f:
        margintrading_abi_parsed = json.load(abi_margintrading)
        liquidator_abi_parsed = json.load(abi_liquidator)
        addresses_json = json.load(addresses_f)

        liquidator_address_str = addresses_json[LIQUIDATOR]
        liquidator_address = Web3.toChecksumAddress(liquidator_address_str)

        margintrading_address_str = addresses_json[MARGIN_TRADING_STRATEGY]
        margintrading_address = Web3.toChecksumAddress(margintrading_address_str)

        yearn_address_str = addresses_json[YEARN_STRATEGY]
        yearn_address = Web3.toChecksumAddress(yearn_address_str)

        strategies = [margintrading_address, yearn_address]

        web3_handle = setup_web3(network=network, infura_key=infura_key)

        return TransactionManager(
            private_key=private_key,
            strategies_addresses=strategies,
            strategies_abi=margintrading_abi_parsed,
            liquidator_address=liquidator_address,
            liquidator_abi=liquidator_abi_parsed,
            web3_handle=web3_handle,
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
