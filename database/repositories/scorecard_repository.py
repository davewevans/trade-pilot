"""Repository for recommendation accuracy scorecard (4-cell matrix)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta


class ScorecardRepository:
    """Computes the 4-cell recommendation accuracy scorecard.

    The four cells represent the 2×2 cross of operator_decision × action:
      - accepted_add:    ground_truth_live  (we added it, see if it traded profitably)
      - rejected_remove: ground_truth_live  (we kept it, see if it continues to trade)
      - rejected_add:    proxy_backtest     (we rejected the add, backtest estimates opportunity cost)
      - accepted_remove: proxy_backtest     (we removed it, backtest estimates if that was right)
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get_scorecard(self, window_days: int = 90, since_days: int = 180) -> dict:
        """Join watchlist_recommendations + recommendation_outcomes.

        Returns a dict with 4 cell keys each containing outcome metrics.
        """
        since_cutoff = (datetime.utcnow() - timedelta(days=since_days)).isoformat()

        # Cell definitions: (cell_name, operator_decision, action, outcome_type, correct_if_pnl_positive)
        CELLS = [
            ("accepted_add",    "accepted", "add",    "ground_truth_live", True),
            ("rejected_add",    "rejected", "add",    "proxy_backtest",    True),
            ("accepted_remove", "accepted", "remove", "proxy_backtest",    False),
            ("rejected_remove", "rejected", "remove", "ground_truth_live", False),
        ]

        result_cells: dict[str, dict] = {}

        for cell_name, op_decision, action, outcome_type, correct_if_positive in CELLS:
            rows = self.conn.execute(
                """
                SELECT
                    r.recommendation_id,
                    r.symbol,
                    o.pnl_per_trade
                FROM watchlist_recommendations r
                JOIN recommendation_outcomes o
                    ON r.recommendation_id = o.recommendation_id
                WHERE r.operator_decision = ?
                  AND r.action = ?
                  AND o.outcome_type = ?
                  AND o.window_days = ?
                  AND r.generated_at >= ?
                """,
                (op_decision, action, outcome_type, window_days, since_cutoff),
            ).fetchall()

            rows = [dict(r) for r in rows]
            count = len(rows)

            if count == 0:
                result_cells[cell_name] = {
                    "outcome_type": outcome_type,
                    "count": 0,
                    "recommender_correct_pct": None,
                    "avg_pnl_per_trade": None,
                    "symbols": [],
                }
                continue

            pnl_values = [r["pnl_per_trade"] for r in rows if r["pnl_per_trade"] is not None]
            symbols = list({r["symbol"] for r in rows})

            if correct_if_positive:
                correct = sum(1 for v in pnl_values if v is not None and v > 0)
            else:
                correct = sum(1 for v in pnl_values if v is not None and v <= 0)

            correct_pct = round(correct / len(pnl_values) * 100, 1) if pnl_values else None
            avg_pnl = round(sum(pnl_values) / len(pnl_values), 2) if pnl_values else None

            result_cells[cell_name] = {
                "outcome_type": outcome_type,
                "count": count,
                "recommender_correct_pct": correct_pct,
                "avg_pnl_per_trade": avg_pnl,
                "symbols": sorted(symbols),
            }

        return result_cells
