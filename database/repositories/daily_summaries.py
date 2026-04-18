"""Repository for the ``daily_summaries`` table."""

import json
import sqlite3

from database.db import db_retry


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    if d.get("skip_reasons_json"):
        try:
            d["skip_reasons"] = json.loads(d["skip_reasons_json"])
        except (TypeError, ValueError):
            d["skip_reasons"] = None
    return d


class DailySummaryRepository:
    """Persistence for end-of-day rollups."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    @db_retry()
    def upsert(self, date: str, data: dict) -> None:
        """Insert or replace the row for the given date."""
        skip_reasons_json = data.get("skip_reasons_json")
        if skip_reasons_json is None and data.get("skip_reasons") is not None:
            skip_reasons_json = json.dumps(data["skip_reasons"], default=str)

        self._conn.execute(
            """
            INSERT INTO daily_summaries (
                date, decisions_total, skips, trades_executed,
                premium_collected, skip_reasons_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                decisions_total   = excluded.decisions_total,
                skips             = excluded.skips,
                trades_executed   = excluded.trades_executed,
                premium_collected = excluded.premium_collected,
                skip_reasons_json = excluded.skip_reasons_json
            """,
            (
                date,
                int(data.get("decisions_total", 0)),
                int(data.get("skips", 0)),
                int(data.get("trades_executed", 0)),
                float(data.get("premium_collected", 0.0)),
                skip_reasons_json,
            ),
        )
        self._conn.commit()

    def get_recent(self, days: int = 30) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM daily_summaries ORDER BY date DESC LIMIT ?",
            (days,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
