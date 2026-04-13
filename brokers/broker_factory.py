"""Factory for creating broker instances based on configuration."""

import logging

from brokers.base import BaseBroker

logger = logging.getLogger(__name__)

# Cache broker instances by (api_key, secret_key) to avoid recreating
# HTTP clients on every job run. Safe in a single-threaded scheduler.
_broker_cache: dict[tuple[str, str], BaseBroker] = {}


def make_broker_cached(api_key: str, secret_key: str) -> BaseBroker:
    """Return a cached broker instance for the given credentials."""
    from config import settings
    from brokers.alpaca_broker import AlpacaBroker

    cache_key = (api_key or "", secret_key or "")
    if cache_key not in _broker_cache:
        _broker_cache[cache_key] = AlpacaBroker(
            api_key=api_key,
            secret_key=secret_key,
            paper=settings.ALPACA_PAPER,
        )
        suffix = (api_key or "")[-4:]
        logger.debug("Created and cached broker for key ending ...%s", suffix)
    return _broker_cache[cache_key]


def get_broker() -> BaseBroker:
    """Return the default broker instance (cached)."""
    from config import settings
    if settings.BROKER != "alpaca":
        raise ValueError(f"Unknown broker: {settings.BROKER}")
    return make_broker_cached(settings.ALPACA_API_KEY, settings.ALPACA_SECRET_KEY)


def make_broker(strategy_name: str) -> BaseBroker:
    """Return a broker using the account credentials for *strategy_name* (cached)."""
    from config import settings
    api_key, secret_key = settings.get_broker_credentials(strategy_name)
    return make_broker_cached(api_key, secret_key)


def clear_broker_cache() -> None:
    """Clear the broker cache. Useful in tests."""
    _broker_cache.clear()
