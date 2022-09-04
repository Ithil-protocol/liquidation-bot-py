DEFAULT_SLEEP_DURATION_IN_SECONDS = 5

ETH_BALANCE = "eth_balance"


MARGIN_TRADING_STRATEGY = "MarginTradingStrategy"


YEARN_STRATEGY = "YearnStrategy"


STRATEGIES = frozenset(
    {
        MARGIN_TRADING_STRATEGY,
        YEARN_STRATEGY,
    }
)


LIQUIDATOR = "Liquidator"


BOT = "telegram"
