"""
Email notification backend — STUB.

Implementation deferred to a future PR. This file exists to lock in the
contract so call sites don't need to change when email is wired.
"""
import logging

logger = logging.getLogger(__name__)


class EmailBackend:
    def send(self, title: str, message: str, tags: list[str], priority: int = 3) -> None:
        logger.debug("Email backend not yet wired; message dropped: %s", title)
