"""Factory for creating broker instances based on configuration."""

from brokers.base import BaseBroker


def get_broker() -> BaseBroker:
    """Return the correct broker instance based on settings.BROKER.

    Currently supports "alpaca". Raises ValueError for unknown brokers.
    """
    from config import settings
    from brokers.alpaca_broker import AlpacaBroker

    if settings.BROKER == "alpaca":
        return AlpacaBroker()
    raise ValueError(f"Unknown broker: {settings.BROKER}")
