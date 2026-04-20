"""Repository for shadow_executions and shadow_execution_legs tables."""

import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from config import FILL_REALISM_GATE_SAMPLE, FILL_REALISM_GATE_PCT

logger = logging.getLogger(__name__)

_LABELS = ("t0", "t30s", "t2m", "t15m", "eod")
_ASYNC_LABELS = ("t30s", "t2m", "t15m", "eod")

_DUE_OFFSETS: dict[str, timedelta] = {
    "t30s": timedelta(seconds=30),
    "t2m":  timedelta(minutes=2),
    "t15m": timedelta(minutes=15),
}


class ShadowExecutionRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def insert_submission(self, *, parent_row: dict, legs: list[dict]) -> int:
        """Insert one shadow_executions row and N leg rows atomically. Returns the new id."""
        cur = self._conn.cursor()
        cur.execute(
            """
            INSERT INTO shadow_executions (
                submitted_at, submitted_at_et, strategy_type, action,
                underlying, alpaca_order_id, order_kind, is_credit, net_limit_abs,
                t0_status, t0_attempts, t0_class,
                t0_net_bid, t0_net_mid, t0_net_ask, t0_captured_at,
                completed
            ) VALUES (
                :submitted_at, :submitted_at_et, :strategy_type, :action,
                :underlying, :alpaca_order_id, :order_kind, :is_credit, :net_limit_abs,
                :t0_status, :t0_attempts, :t0_class,
                :t0_net_bid, :t0_net_mid, :t0_net_ask, :t0_captured_at,
                0
            )
            """,
            parent_row,
        )
        exec_id = cur.lastrowid

        for leg in legs:
            cur.execute(
                """
                INSERT INTO shadow_execution_legs (
                    shadow_exec_id, contract_symbol, leg_role, side, position_intent,
                    t0_bid, t0_ask, t0_mid, t0_source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    exec_id,
                    leg["contract_symbol"],
                    leg["leg_role"],
                    leg["side"],
                    leg.get("position_intent"),
                    leg.get("t0_bid"),
                    leg.get("t0_ask"),
                    leg.get("t0_mid"),
                    leg.get("t0_source"),
                ),
            )

        self._conn.commit()
        return exec_id

    def get_due(self, label: str, *, limit: int = 100) -> list[dict]:
        """Return rows where tXX is due and still pending/retryable, with legs pre-joined."""
        if label not in _ASYNC_LABELS:
            raise ValueError(f"get_due: invalid label {label!r}")

        now_utc = datetime.now(timezone.utc)
        now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%S")

        if label == "eod":
            # EOD is due at 16:00 ET on the submitted date. We compare via submitted_at_et.
            # Rows are due when the ET date's 16:00 has passed.
            # Simplification: we compute whether now_et >= 16:00 for the submitted date.
            rows = self._conn.execute(
                f"""
                SELECT * FROM shadow_executions
                WHERE eod_status IN ('pending', 'failed_retryable')
                  AND completed = 0
                  AND (
                    -- Submitted date's 16:00 ET has passed if the submitted date < today ET
                    -- or if submitted date is today and time is past 16:00.
                    DATE(submitted_at_et) < DATE('now', 'localtime')
                    OR (
                        DATE(submitted_at_et) = DATE('now', 'localtime')
                        AND TIME('now', 'localtime') >= '16:00:00'
                    )
                  )
                ORDER BY submitted_at ASC
                LIMIT {limit}
                """,
            ).fetchall()
        else:
            offset_secs = int(_DUE_OFFSETS[label].total_seconds())
            rows = self._conn.execute(
                f"""
                SELECT * FROM shadow_executions
                WHERE {label}_status IN ('pending', 'failed_retryable')
                  AND completed = 0
                  AND datetime(submitted_at, '+{offset_secs} seconds') <= ?
                ORDER BY submitted_at ASC
                LIMIT {limit}
                """,
                (now_iso,),
            ).fetchall()

        result = []
        for row in rows:
            d = dict(row)
            leg_rows = self._conn.execute(
                "SELECT * FROM shadow_execution_legs WHERE shadow_exec_id = ?",
                (d["id"],),
            ).fetchall()
            d["legs"] = [dict(lr) for lr in leg_rows]
            result.append(d)
        return result

    def update_capture(
        self,
        shadow_exec_id: int,
        label: str,
        *,
        net_bid,
        net_mid,
        net_ask,
        class_value,
        legs_data: list[dict],
        status: str,
        attempts_increment: int = 1,
        eod_source_detail: str | None = None,
    ) -> None:
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

        set_clauses = [
            f"{label}_status = ?",
            f"{label}_attempts = {label}_attempts + ?",
            f"{label}_class = ?",
            f"{label}_net_bid = ?",
            f"{label}_net_mid = ?",
            f"{label}_net_ask = ?",
            f"{label}_captured_at = ?",
        ]
        params: list = [
            status, attempts_increment, class_value,
            net_bid, net_mid, net_ask, now_iso,
        ]

        if label == "eod" and eod_source_detail is not None:
            set_clauses.append("eod_source_detail = ?")
            params.append(eod_source_detail)

        self._conn.execute(
            f"UPDATE shadow_executions SET {', '.join(set_clauses)} WHERE id = ?",
            [*params, shadow_exec_id],
        )

        bid_col = f"{label}_bid"
        ask_col = f"{label}_ask"
        mid_col = f"{label}_mid"
        src_col = f"{label}_source"
        for leg in legs_data:
            self._conn.execute(
                f"""
                UPDATE shadow_execution_legs
                SET {bid_col} = ?, {ask_col} = ?, {mid_col} = ?, {src_col} = ?
                WHERE shadow_exec_id = ? AND contract_symbol = ?
                """,
                (
                    leg.get("bid"),
                    leg.get("ask"),
                    leg.get("mid"),
                    leg.get("source"),
                    shadow_exec_id,
                    leg["contract_symbol"],
                ),
            )

        self._conn.commit()

    def recompute_completed(self, shadow_exec_id: int) -> None:
        """Set completed=1 if every tXX_status is captured or failed_permanent."""
        terminal = ("'captured'", "'failed_permanent'")
        terminal_str = ", ".join(terminal)
        row = self._conn.execute(
            f"""
            SELECT
                t0_status IN ({terminal_str})   AND
                t30s_status IN ({terminal_str}) AND
                t2m_status IN ({terminal_str})  AND
                t15m_status IN ({terminal_str}) AND
                eod_status IN ({terminal_str})  AS all_done
            FROM shadow_executions WHERE id = ?
            """,
            (shadow_exec_id,),
        ).fetchone()
        if row and row[0]:
            self._conn.execute(
                "UPDATE shadow_executions SET completed = 1 WHERE id = ?",
                (shadow_exec_id,),
            )
            self._conn.commit()

    def get_fill_realism_aggregates(self, since_days: int = 90) -> list[dict]:
        """Return per-strategy fill-realism aggregates.

        data_unavailable rows are excluded from the t2m_realism_pct denominator.
        gate_met requires denominator >= FILL_REALISM_GATE_SAMPLE and
        t2m_realism_pct >= FILL_REALISM_GATE_PCT.
        """
        rows = self._conn.execute(
            """
            SELECT
                strategy_type,
                COUNT(*) FILTER (WHERE t2m_class IS NOT NULL)                          AS sample_size,
                COUNT(*) FILTER (WHERE t2m_class = 'always_fillable')                  AS always_count,
                COUNT(*) FILTER (WHERE t2m_class = 'sometimes_fillable')               AS sometimes_count,
                COUNT(*) FILTER (WHERE t2m_class = 'not_fillable')                     AS not_fillable_count,
                COUNT(*) FILTER (WHERE t2m_class = 'data_unavailable')                 AS data_unavailable_count,
                COUNT(*) FILTER (WHERE eod_class IS NOT NULL)                          AS eod_sample_size,
                COUNT(*) FILTER (WHERE eod_class IN ('always_fillable','sometimes_fillable')) AS eod_fillable_count,
                COUNT(*) FILTER (WHERE eod_class = 'not_fillable')                     AS eod_not_fillable_count
            FROM shadow_executions
            WHERE submitted_at >= datetime('now', ? || ' days')
              AND t2m_status = 'captured'
            GROUP BY strategy_type
            ORDER BY strategy_type
            """,
            (f"-{since_days}",),
        ).fetchall()

        result = []
        for row in rows:
            (
                strategy_type,
                sample_size,
                always_count,
                sometimes_count,
                not_fillable_count,
                data_unavailable_count,
                eod_sample_size,
                eod_fillable_count,
                eod_not_fillable_count,
            ) = (
                row[0], row[1], row[2], row[3], row[4],
                row[5], row[6], row[7], row[8],
            )

            denom = always_count + sometimes_count + not_fillable_count
            t2m_realism_pct = (
                round((always_count + sometimes_count) / denom * 100, 1)
                if denom >= FILL_REALISM_GATE_SAMPLE
                else None
            )

            eod_denom = eod_fillable_count + eod_not_fillable_count
            eod_realism_pct = (
                round(eod_fillable_count / eod_denom * 100, 1)
                if eod_denom > 0
                else None
            )

            gate_met = (
                denom >= FILL_REALISM_GATE_SAMPLE
                and t2m_realism_pct is not None
                and t2m_realism_pct >= FILL_REALISM_GATE_PCT
            )

            result.append({
                "strategy_type": strategy_type,
                "sample_size": sample_size,
                "always_count": always_count,
                "sometimes_count": sometimes_count,
                "not_fillable_count": not_fillable_count,
                "data_unavailable_count": data_unavailable_count,
                "t2m_realism_pct": t2m_realism_pct,
                "eod_sample_size": eod_sample_size,
                "eod_realism_pct": eod_realism_pct,
                "gate_met": gate_met,
            })
        return result
