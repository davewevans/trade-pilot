"""Repository for liquidity snapshot and score tables."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone


class LiquidityRepository:
    """Read/write access to symbol_liquidity_snapshots and symbol_liquidity_scores."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    # ── Snapshot methods ───────────────────────────────────────────────────────

    def insert_snapshot(self, data: dict) -> int:
        """Insert or replace a liquidity snapshot. Returns snapshot_id.

        Required keys: symbol, strategy_type, snapshot_date, source, sample_count.
        Optional: avg_ba_spread_pct, avg_oi_at_strikes, volume_to_oi_ratio,
                  slippage_estimate, raw_metrics_json.

        Uses INSERT OR REPLACE so retries on the same
        (symbol, strategy_type, snapshot_date, source) are idempotent.
        """
        raw_metrics = data.get("raw_metrics_json")
        if isinstance(raw_metrics, dict):
            raw_metrics = json.dumps(raw_metrics, default=str)

        cur = self._conn.execute(
            """
            INSERT OR REPLACE INTO symbol_liquidity_snapshots (
                symbol, strategy_type, snapshot_date, source,
                avg_ba_spread_pct, avg_oi_at_strikes, volume_to_oi_ratio,
                slippage_estimate, sample_count, raw_metrics_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["symbol"],
                data["strategy_type"],
                data["snapshot_date"],
                data["source"],
                data.get("avg_ba_spread_pct"),
                data.get("avg_oi_at_strikes"),
                data.get("volume_to_oi_ratio"),
                data.get("slippage_estimate"),
                data["sample_count"],
                raw_metrics,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def get_snapshots(
        self,
        symbol: str,
        strategy_type: str,
        since_date: str | None = None,
    ) -> list[dict]:
        """Return snapshots for a (symbol, strategy_type), most recent first.

        since_date defaults to today minus RESEARCH_LOOKBACK_DAYS.
        """
        if since_date is None:
            from config import settings
            cutoff = (
                datetime.now(timezone.utc).date()
                - timedelta(days=settings.RESEARCH_LOOKBACK_DAYS)
            ).isoformat()
        else:
            cutoff = since_date

        rows = self._conn.execute(
            """
            SELECT * FROM symbol_liquidity_snapshots
            WHERE symbol = ? AND strategy_type = ? AND snapshot_date >= ?
            ORDER BY snapshot_date DESC
            """,
            (symbol, strategy_type, cutoff),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_recent_snapshots(
        self,
        strategy_type: str,
        since_date: str,
    ) -> list[dict]:
        """Return all snapshots for one strategy type since a given date."""
        rows = self._conn.execute(
            """
            SELECT * FROM symbol_liquidity_snapshots
            WHERE strategy_type = ? AND snapshot_date >= ?
            ORDER BY snapshot_date DESC
            """,
            (strategy_type, since_date),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Score methods ─────────────────────────────────────────────────────────

    def upsert_score(self, score: dict) -> None:
        """Insert or update a liquidity score row.

        Required: symbol, strategy_type, composite_score, tier,
                  lookback_days, snapshot_count, below_floor (bool),
                  confidence, sub_metrics (dict).
        """
        sub_metrics = score.get("sub_metrics", {})
        if isinstance(sub_metrics, dict):
            sub_metrics_json = json.dumps(sub_metrics, default=str)
        else:
            sub_metrics_json = sub_metrics

        below_floor_int = 1 if score.get("below_floor") else 0

        self._conn.execute(
            """
            INSERT INTO symbol_liquidity_scores (
                symbol, strategy_type, composite_score, tier,
                lookback_days, snapshot_count, below_floor,
                confidence, last_updated, sub_metrics_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, strategy_type) DO UPDATE SET
                composite_score = excluded.composite_score,
                tier             = excluded.tier,
                lookback_days    = excluded.lookback_days,
                snapshot_count   = excluded.snapshot_count,
                below_floor      = excluded.below_floor,
                confidence       = excluded.confidence,
                last_updated     = excluded.last_updated,
                sub_metrics_json = excluded.sub_metrics_json
            """,
            (
                score["symbol"],
                score["strategy_type"],
                score["composite_score"],
                score["tier"],
                score["lookback_days"],
                score["snapshot_count"],
                below_floor_int,
                score["confidence"],
                datetime.now(timezone.utc).isoformat(),
                sub_metrics_json,
            ),
        )
        self._conn.commit()

    def get_score(self, symbol: str, strategy_type: str) -> dict | None:
        """Return the score row for a (symbol, strategy_type), or None."""
        row = self._conn.execute(
            """
            SELECT * FROM symbol_liquidity_scores
            WHERE symbol = ? AND strategy_type = ?
            """,
            (symbol, strategy_type),
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        if d.get("sub_metrics_json"):
            try:
                d["sub_metrics"] = json.loads(d["sub_metrics_json"])
            except (TypeError, ValueError):
                d["sub_metrics"] = {}
        else:
            d["sub_metrics"] = {}
        return d

    def get_all_scores(self, strategy_type: str | None = None) -> list[dict]:
        """Return all score rows, optionally filtered by strategy_type.

        Ordered by composite_score DESC.
        """
        if strategy_type is not None:
            rows = self._conn.execute(
                """
                SELECT * FROM symbol_liquidity_scores
                WHERE strategy_type = ?
                ORDER BY composite_score DESC
                """,
                (strategy_type,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT * FROM symbol_liquidity_scores
                ORDER BY composite_score DESC
                """
            ).fetchall()

        result = []
        for row in rows:
            d = dict(row)
            if d.get("sub_metrics_json"):
                try:
                    d["sub_metrics"] = json.loads(d["sub_metrics_json"])
                except (TypeError, ValueError):
                    d["sub_metrics"] = {}
            else:
                d["sub_metrics"] = {}
            result.append(d)
        return result

    def get_multiplier(
        self,
        symbol: str,
        strategy_type: str,
    ) -> tuple[float, str, str]:
        """Return (multiplier, tier, confidence) for pre_check_entry.

        Reads settings.RESEARCH_SCORE_MULTIPLIER_ENABLED at call time.
        - Kill switch off → (1.0, 'B', 'disabled')
        - No row → (1.0, 'B', 'none')
        - Else → tier_to_multiplier(tier, confidence, True)
        """
        from config import settings
        from research.liquidity.scorer import tier_to_multiplier

        if not settings.RESEARCH_SCORE_MULTIPLIER_ENABLED:
            return (1.0, "B", "disabled")

        row = self.get_score(symbol, strategy_type)
        if row is None:
            return (1.0, "B", "none")

        tier = row["tier"]
        confidence = row["confidence"]
        multiplier = tier_to_multiplier(tier, confidence, multiplier_enabled=True)
        return (multiplier, tier, confidence)
