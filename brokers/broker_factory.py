"""Factory for creating broker instances based on configuration."""

from brokers.base import BaseBroker


def get_broker() -> BaseBroker:
    """Return the correct broker instance based on settings.BROKER.

    Uses the default ALPACA_API_KEY credentials.
    Currently supports "alpaca". Raises ValueError for unknown brokers.
    """
    from config import settings
    from brokers.alpaca_broker import AlpacaBroker

    if settings.BROKER == "alpaca":
        return AlpacaBroker()
    raise ValueError(f"Unknown broker: {settings.BROKER}")


def make_broker(strategy_name: str) -> BaseBroker:
    """Return a broker using the account credentials for *strategy_name*.

    Looks up credentials via ``settings.get_broker_credentials()``.
    """
    from config import settings
    from brokers.alpaca_broker import AlpacaBroker

    api_key, secret_key = settings.get_broker_credentials(strategy_name)
    return AlpacaBroker(
        api_key=api_key,
        secret_key=secret_key,
        paper=settings.ALPACA_PAPER,
    )
