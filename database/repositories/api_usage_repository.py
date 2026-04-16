"""Repository for the ``claude_api_calls`` table."""

import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class ApiUsageRepository:
    """Persistence for per-call Claude API usage stats."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def record_api_call(
        self,
        *,
        strategy: str | None,
        phase: str | None,
        model: str,
        input_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
        output_tokens: int,
        latency_ms: int,
    ) -> int | None:
        """Insert a row into ``claude_api_calls``. Returns the new id or None.

        Wraps the DB write in try/except and logs on failure so a DB error
        never breaks a trading decision.
        """
        try:
            created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            cur = self._conn.execute(
                """
                INSERT INTO claude_api_calls (
                    created_at, strategy, phase, model,
                    input_tokens, cache_read_tokens, cache_write_tokens,
                    output_tokens, latency_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    created_at, strategy, phase, model,
                    input_tokens, cache_read_tokens, cache_write_tokens,
                    output_tokens, latency_ms,
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)
        except Exception:
            logger.warning(
                "Failed to record API call in DB (strategy=%s phase=%s)",
                strategy, phase, exc_info=True,
            )
            return None

    def get_recent(self, days: int = 7) -> list[dict]:
        """Return all rows from the last ``days`` days, oldest first."""
        try:
            rows = self._conn.execute(
                """
                SELECT * FROM claude_api_calls
                WHERE created_at >= datetime('now', ? || ' days')
                ORDER BY created_at ASC
                """,
                (f"-{days}",),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            logger.warning("Failed to query api_usage rows", exc_info=True)
            return []
