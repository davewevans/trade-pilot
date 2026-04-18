"""Repository for the ``trades`` table."""

import sqlite3

from database.db import db_retry


_SELL_TYPES = {"SELL_PUT", "SELL_CALL", "SELL_BULL_PUT_SPREAD", "SELL_BEAR_CALL_SPREAD",
               "SELL_IRON_CONDOR", "SELL_LONG_CALL_VERTICAL"}
_BUY_TYPES = {"BUY_PUT", "BUY_CALL", "BUY_BULL_PUT_SPREAD", "BUY_BEAR_CALL_SPREAD",
              "BUY_IRON_CONDOR", "BUY_LONG_CALL_VERTICAL"}


def trade_pnl(trade: dict) -> float | None:
    """Cash-flow P&L for one filled trade row.

    Returns fill_price * contracts * 100 * sign, or None if fill_price is NULL.
    SELL trades are +1 sign (cash in); BUY trades are -1 (cash out).
    """
    fp = trade.get("fill_price")
    if fp is None:
        return None
    tt = (trade.get("trade_type") or "").upper()
    if tt in _SELL_TYPES:
        sign = 1
    elif tt in _BUY_TYPES:
        sign = -1
    else:
        return None
    try:
        return float(fp) * int(trade.get("contracts") or 1) * 100 * sign
    except (TypeError, ValueError):
        return None


class TradeRepository:
    """Persistence for Alpaca orders submitted by the bot."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    @db_retry()
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

    @db_retry()
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

    def get_closed_trades_for_symbol(
        self,
        symbol: str,
        strategy_type: str,
        since_date: str,
    ) -> list[dict]:
        """Return filled, closed live trades for (symbol, strategy_type) since a date.

        'Closed' means fill_status = 'filled' AND closed_at IS NOT NULL.
        'symbol' matches the `underlying` column (the ticker, not the OCC symbol).
        """
        rows = self._conn.execute(
            """
            SELECT * FROM trades
             WHERE UPPER(underlying) = UPPER(?)
               AND strategy_type = ?
               AND fill_status = 'filled'
               AND closed_at IS NOT NULL
               AND closed_at >= ?
             ORDER BY closed_at ASC
            """,
            (symbol, strategy_type, since_date),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_closed_trades_in_window(
        self,
        symbol: str,
        strategy_type: str,
        start_date: str,
        end_date: str,
    ) -> list[dict]:
        """Return filled, closed live trades in a date window.

        Used by R2 OutcomeComputer for ground-truth outcome computation.
        """
        rows = self._conn.execute(
            """
            SELECT * FROM trades
             WHERE UPPER(underlying) = UPPER(?)
               AND strategy_type = ?
               AND fill_status = 'filled'
               AND closed_at IS NOT NULL
               AND closed_at >= ?
               AND closed_at < ?
             ORDER BY closed_at ASC
            """,
            (symbol, strategy_type, start_date, end_date),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_filled(
        self,
        strategy_types: list[str] | None = None,
        underlying: str | None = None,
    ) -> list[dict]:
        """Trades with fill_status='filled' AND a known fill_price.

        Ordered by ``filled_at`` ascending so the API can build a
        chronological equity curve. Rows with NULL ``fill_price`` are
        excluded — a pending/unfilled trade has no realized P&L yet
        and must not be counted as a $0 trade. ``strategy_types``
        scopes results to a list of strategies; ``underlying`` filters
        to a single ticker (case-insensitive).
        """
        params: list = []
        extra = ""
        if strategy_types:
            placeholders = ",".join(["?"] * len(strategy_types))
            extra += f" AND strategy_type IN ({placeholders})"
            params.extend(strategy_types)
        if underlying:
            extra += " AND UPPER(underlying) = UPPER(?)"
            params.append(underlying)
        rows = self._conn.execute(
            f"""
            SELECT * FROM trades
             WHERE fill_status = 'filled'
               AND fill_price IS NOT NULL
               {extra}
             ORDER BY filled_at ASC, id ASC
            """,
            params,
        ).fetchall()
        return [dict(r) for r in rows]
