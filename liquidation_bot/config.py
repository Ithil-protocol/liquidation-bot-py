import dataclasses


@dataclasses.dataclass
class Configuration:
    infura_api_key: str
    network: str
    private_key: str
    sleep_duration_in_seconds: int
    telegram_chat_id: str
    telegram_key: str
