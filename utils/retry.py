"""Retry decorator for transient API failures."""

import logging
import time
from functools import wraps

logger = logging.getLogger(__name__)


def retry_on_transient(max_retries: int = 3, base_delay: float = 1.0, max_delay: float = 30.0):
    """Decorator that retries on transient HTTP/network errors.

    Uses exponential backoff: base_delay * 2^attempt, capped at max_delay.
    Auth errors (401, 403) are never retried.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    status = getattr(e, "status_code", None) or getattr(e, "code", None)
                    if status in (401, 403):
                        raise
                    if attempt == max_retries:
                        raise
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    logger.warning(
                        "Retrying %s (attempt %d/%d) after %.1fs: %s",
                        func.__name__, attempt + 1, max_retries, delay, e,
                    )
                    time.sleep(delay)
            raise last_exception  # pragma: no cover
        return wrapper
    return decorator
