"""Repository for the ``token_usage`` table.

Pricing constants are for Claude Sonnet 4.6 as of 2026-04.
Update these if the model or pricing changes.
"""

import logging
import sqlite3
from datetime import datetime, timezone

from database.db import db_retry

logger = logging.getLogger(__name__)

# ── Pricing: Claude Sonnet 4.6 (per million tokens) ──────────
# Base input:       $3.00
# Cache write (1h): $6.00  ← we set ttl="1h" in claude_advisor.py
# Cache hit:        $0.30
# Output:           $15.00
_PRICE_INPUT = 3.00
_PRICE_CACHE_WRITE_1H = 6.00
_PRICE_CACHE_HIT = 0.30
_PRICE_OUTPUT = 15.00


def estimate_cost_usd(
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_creation_tokens: int,
) -> float:
    """Estimate USD cost for a single API call using Claude Sonnet 4.6 pricing.

    base_input is the portion of input_tokens NOT served from cache and NOT
    written to cache (i.e. truly uncached tokens billed at the base rate).
    """
    base_input = max(0, input_tokens - cache_read_tokens - cache_creation_tokens)
    cost = (
        base_input * _PRICE_INPUT
        + cache_creation_tokens * _PRICE_CACHE_WRITE_1H
        + cache_read_tokens * _PRICE_CACHE_HIT
        + output_tokens * _PRICE_OUTPUT
    ) / 1_000_000
    return round(cost, 6)


class TokenUsageRepository:
    """Persistence for per-call token usage with cost estimation."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    @db_retry()
    def insert(self, row: dict) -> int | None:
        """Insert a token usage record. Returns the row id or None on failure."""
        try:
            cur = self._conn.execute(
                """
                INSERT INTO token_usage (
                    timestamp, strategy_type, underlying, model,
                    input_tokens, output_tokens, cache_read_tokens,
                    cache_creation_tokens, response_time_ms,
                    estimated_cost_usd, decision_action
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["timestamp"],
                    row["strategy_type"],
                    row.get("underlying"),
                    row["model"],
                    row.get("input_tokens", 0),
                    row.get("output_tokens", 0),
                    row.get("cache_read_tokens", 0),
                    row.get("cache_creation_tokens", 0),
                    row.get("response_time_ms"),
                    row.get("estimated_cost_usd"),
                    row.get("decision_action"),
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)
        except Exception:
            logger.warning(
                "Failed to insert token_usage row (strategy=%s underlying=%s)",
                row.get("strategy_type"), row.get("underlying"), exc_info=True,
            )
            return None

    def get_daily_summary(self, date_str: str) -> dict:
        """Aggregate token usage for a single date (YYYY-MM-DD).

        Returns a dict with totals, call count, avg response time,
        estimated cost, and cache hit rate.
        """
        try:
            row = self._conn.execute(
                """
                SELECT
                    ? AS date,
                    COALESCE(SUM(input_tokens), 0)          AS total_input,
                    COALESCE(SUM(output_tokens), 0)         AS total_output,
                    COALESCE(SUM(cache_read_tokens), 0)     AS total_cache_read,
                    COALESCE(SUM(cache_creation_tokens), 0) AS total_cache_creation,
                    COUNT(*)                                 AS calls_count,
                    COALESCE(AVG(response_time_ms), 0)      AS avg_response_ms,
                    COALESCE(SUM(estimated_cost_usd), 0)    AS estimated_cost_usd
                FROM token_usage
                WHERE timestamp LIKE ? || '%'
                """,
                (date_str, date_str),
            ).fetchone()
            d = dict(row) if row else {}
            total_read = d.get("total_cache_read", 0) or 0
            total_input = d.get("total_input", 0) or 0
            denom = total_read + total_input
            d["cache_hit_rate"] = round(total_read / denom * 100, 1) if denom > 0 else 0.0
            return d
        except Exception:
            logger.warning("get_daily_summary failed for %s", date_str, exc_info=True)
            return {"date": date_str, "calls_count": 0, "estimated_cost_usd": 0.0, "cache_hit_rate": 0.0}

    def get_daily_summaries(self, days: int = 30) -> list[dict]:
        """Return daily aggregates for the last N days, newest first."""
        try:
            rows = self._conn.execute(
                """
                SELECT
                    date(timestamp)                          AS date,
                    COALESCE(SUM(input_tokens), 0)          AS total_input,
                    COALESCE(SUM(output_tokens), 0)         AS total_output,
                    COALESCE(SUM(cache_read_tokens), 0)     AS total_cache_read,
                    COALESCE(SUM(cache_creation_tokens), 0) AS total_cache_creation,
                    COUNT(*)                                 AS calls_count,
                    COALESCE(AVG(response_time_ms), 0)      AS avg_response_ms,
                    COALESCE(SUM(estimated_cost_usd), 0)    AS estimated_cost_usd
                FROM token_usage
                WHERE timestamp >= datetime('now', ? || ' days')
                GROUP BY date(timestamp)
                ORDER BY date DESC
                LIMIT ?
                """,
                (f"-{days}", days),
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                total_read = d.get("total_cache_read", 0) or 0
                total_input = d.get("total_input", 0) or 0
                denom = total_read + total_input
                d["cache_hit_rate"] = round(total_read / denom * 100, 1) if denom > 0 else 0.0
                result.append(d)
            return result
        except Exception:
            logger.warning("get_daily_summaries failed", exc_info=True)
            return []

    def get_today_summary(self) -> dict:
        """Convenience: aggregate for today's date (UTC)."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.get_daily_summary(today)

    def get_lifetime_summary(self) -> dict:
        """Aggregate all-time totals."""
        try:
            row = self._conn.execute(
                """
                SELECT
                    COUNT(*)                                 AS total_calls,
                    COALESCE(SUM(input_tokens), 0)          AS total_input,
                    COALESCE(SUM(output_tokens), 0)         AS total_output,
                    COALESCE(SUM(cache_read_tokens), 0)     AS total_cache_read,
                    COALESCE(SUM(cache_creation_tokens), 0) AS total_cache_creation,
                    COALESCE(SUM(estimated_cost_usd), 0)    AS total_cost_usd,
                    MIN(timestamp)                          AS first_recorded,
                    MAX(timestamp)                          AS last_recorded
                FROM token_usage
                """
            ).fetchone()
            d = dict(row) if row else {}
            total_calls = d.get("total_calls", 0) or 0
            total_cost = d.get("total_cost_usd", 0.0) or 0.0
            d["avg_cost_per_call"] = round(total_cost / total_calls, 6) if total_calls > 0 else 0.0
            total_read = d.get("total_cache_read", 0) or 0
            total_input = d.get("total_input", 0) or 0
            denom = total_read + total_input
            d["overall_cache_hit_rate"] = round(total_read / denom * 100, 1) if denom > 0 else 0.0
            return d
        except Exception:
            logger.warning("get_lifetime_summary failed", exc_info=True)
            return {"total_calls": 0, "total_cost_usd": 0.0, "overall_cache_hit_rate": 0.0}

    def get_by_strategy(self, days: int = 30) -> list[dict]:
        """Per-strategy breakdown for the last N days."""
        try:
            rows = self._conn.execute(
                """
                SELECT
                    strategy_type,
                    COUNT(*)                                 AS calls,
                    COALESCE(SUM(input_tokens), 0)          AS total_input,
                    COALESCE(SUM(output_tokens), 0)         AS total_output,
                    COALESCE(SUM(cache_read_tokens), 0)     AS total_cache_read,
                    COALESCE(SUM(estimated_cost_usd), 0)    AS total_cost_usd
                FROM token_usage
                WHERE timestamp >= datetime('now', ? || ' days')
                GROUP BY strategy_type
                ORDER BY total_cost_usd DESC
                """,
                (f"-{days}",),
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                calls = d.get("calls", 0) or 0
                total_cost = d.get("total_cost_usd", 0.0) or 0.0
                d["avg_cost_per_call"] = round(total_cost / calls, 6) if calls > 0 else 0.0
                total_read = d.get("total_cache_read", 0) or 0
                total_input = d.get("total_input", 0) or 0
                denom = total_read + total_input
                d["cache_hit_rate"] = round(total_read / denom * 100, 1) if denom > 0 else 0.0
                result.append(d)
            return result
        except Exception:
            logger.warning("get_by_strategy failed", exc_info=True)
            return []
