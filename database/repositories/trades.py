"""Repository for the ``trades`` table."""

import sqlite3


class TradeRepository:
    """Persistence for Alpaca orders submitted by the bot."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def insert(self, trade: dict) -> int:
        """Insert a new trade row. Returns the new ``id``.

        Required keys: cycle_id, alpaca_order_id, underlying,
        strategy_type, trade_type, symbol, limit_price, submitted_at.
        ``contracts`` defaults to 1 and ``fill_status`` to 'pending'
        if omitted.
        """
        cur = self._conn.execute(
            """
            INSERT INTO trades (
                cycle_id, decision_id, alpaca_order_id, underlying,
                strategy_type, trade_type, symbol, strike, expiration,
                dte_at_entry, contracts, limit_price, fill_price,
                fill_status, submitted_at, filled_at, filled_qty,
                premium_credit, delta_at_entry, iv_rank_at_entry
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade["cycle_id"],
                trade.get("decision_id"),
                trade["alpaca_order_id"],
                trade["underlying"],
                trade["strategy_type"],
                trade["trade_type"],
                trade["symbol"],
                trade.get("strike"),
                trade.get("expiration"),
                trade.get("dte_at_entry"),
                trade.get("contracts", 1),
                trade["limit_price"],
                trade.get("fill_price"),
                trade.get("fill_status", "pending"),
                trade["submitted_at"],
                trade.get("filled_at"),
                trade.get("filled_qty"),
                trade.get("premium_credit"),
                trade.get("delta_at_entry"),
                trade.get("iv_rank_at_entry"),
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def get_pending(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM trades WHERE fill_status = 'pending' ORDER BY submitted_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def update_fill(self, alpaca_order_id: str, fill_data: dict) -> None:
        """Update fill fields for a single order. Silently no-ops
        if the order_id does not exist.
        """
        self._conn.execute(
            """
            UPDATE trades
               SET fill_price     = COALESCE(?, fill_price),
                   fill_status    = COALESCE(?, fill_status),
                   filled_at      = COALESCE(?, filled_at),
                   filled_qty     = COALESCE(?, filled_qty),
                   premium_credit = COALESCE(?, premium_credit)
             WHERE alpaca_order_id = ?
            """,
            (
                fill_data.get("fill_price"),
                fill_data.get("fill_status"),
                fill_data.get("filled_at"),
                fill_data.get("filled_qty"),
                fill_data.get("premium_credit"),
                alpaca_order_id,
            ),
        )
        self._conn.commit()

    def get_by_cycle(self, cycle_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM trades WHERE cycle_id = ? ORDER BY submitted_at ASC",
            (cycle_id,),
        ).fetchall()
        return [dict(r) for r in rows]
