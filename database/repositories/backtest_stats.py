"""Repository for backtest_trades, symbol_strategy_stats, and regime_strategy_stats."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Optional


class BacktestStatsRepository:
    """Read/write access to backtest trade log and aggregated stats tables."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    # ── Raw trade methods ─────────────────────────────────────────────────────

    def insert_trade(self, trade_data: dict) -> int:
        """Insert a single backtest trade row. Returns trade_id.

        Required keys: sweep_run_id, symbol, strategy_type, entry_date.
        Optional: exit_date, entry_credit, exit_debit, pnl, exit_reason,
                  entry_delta, entry_ivr, entry_regime, entry_iv_env,
                  holding_days, contracts, trade_json.
        """
        trade_json = trade_data.get("trade_json")
        if isinstance(trade_json, dict):
            trade_json = json.dumps(trade_json, default=str)

        cur = self._conn.execute(
            """
            INSERT INTO backtest_trades (
                sweep_run_id, symbol, strategy_type, entry_date,
                exit_date, entry_credit, exit_debit, pnl,
                exit_reason, entry_delta, entry_ivr,
                entry_regime, entry_iv_env, holding_days,
                contracts, trade_json, inserted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_data["sweep_run_id"],
                trade_data["symbol"],
                trade_data["strategy_type"],
                trade_data["entry_date"],
                trade_data.get("exit_date"),
                trade_data.get("entry_credit"),
                trade_data.get("exit_debit"),
                trade_data.get("pnl"),
                trade_data.get("exit_reason"),
                trade_data.get("entry_delta"),
                trade_data.get("entry_ivr"),
                trade_data.get("entry_regime"),
                trade_data.get("entry_iv_env"),
                trade_data.get("holding_days"),
                trade_data.get("contracts", 1),
                trade_json,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def insert_trades_batch(self, trades: list[dict], sweep_run_id: str) -> int:
        """Bulk insert trades via executemany. Returns count inserted."""
        if not trades:
            return 0

        now = datetime.now(timezone.utc).isoformat()

        rows = []
        for t in trades:
            trade_json = t.get("trade_json")
            if isinstance(trade_json, dict):
                trade_json = json.dumps(trade_json, default=str)
            rows.append((
                sweep_run_id,
                t["symbol"],
                t["strategy_type"],
                t["entry_date"],
                t.get("exit_date"),
                t.get("entry_credit"),
                t.get("exit_debit"),
                t.get("pnl"),
                t.get("exit_reason"),
                t.get("entry_delta"),
                t.get("entry_ivr"),
                t.get("entry_regime"),
                t.get("entry_iv_env"),
                t.get("holding_days"),
                t.get("contracts", 1),
                trade_json,
                now,
            ))

        self._conn.executemany(
            """
            INSERT INTO backtest_trades (
                sweep_run_id, symbol, strategy_type, entry_date,
                exit_date, entry_credit, exit_debit, pnl,
                exit_reason, entry_delta, entry_ivr,
                entry_regime, entry_iv_env, holding_days,
                contracts, trade_json, inserted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self._conn.commit()
        return len(rows)

    def get_trades(
        self,
        symbol: Optional[str] = None,
        strategy_type: Optional[str] = None,
        entry_regime: Optional[str] = None,
        since_date: Optional[str] = None,
    ) -> list[dict]:
        """Return trades with flexible filtering. Any argument may be None."""
        conditions = []
        params: list = []

        if symbol is not None:
            conditions.append("symbol = ?")
            params.append(symbol)
        if strategy_type is not None:
            conditions.append("strategy_type = ?")
            params.append(strategy_type)
        if entry_regime is not None:
            conditions.append("entry_regime = ?")
            params.append(entry_regime)
        if since_date is not None:
            conditions.append("entry_date >= ?")
            params.append(since_date)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = self._conn.execute(
            f"SELECT * FROM backtest_trades {where} ORDER BY entry_date",
            params,
        ).fetchall()

        result = []
        for row in rows:
            d = dict(row)
            if d.get("trade_json"):
                try:
                    d["trade_json"] = json.loads(d["trade_json"])
                except (TypeError, ValueError):
                    pass
            result.append(d)
        return result

    # ── Stats methods ─────────────────────────────────────────────────────────

    def upsert_symbol_stat(self, stat: dict) -> None:
        """Insert or update a symbol_strategy_stats row."""
        self._conn.execute(
            """
            INSERT INTO symbol_strategy_stats (
                symbol, strategy_type, trade_count, win_count,
                win_rate, avg_pnl_per_trade, total_pnl, max_drawdown,
                sharpe_ratio, confidence, last_updated,
                date_range_start, date_range_end
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, strategy_type) DO UPDATE SET
                trade_count       = excluded.trade_count,
                win_count         = excluded.win_count,
                win_rate          = excluded.win_rate,
                avg_pnl_per_trade = excluded.avg_pnl_per_trade,
                total_pnl         = excluded.total_pnl,
                max_drawdown      = excluded.max_drawdown,
                sharpe_ratio      = excluded.sharpe_ratio,
                confidence        = excluded.confidence,
                last_updated      = excluded.last_updated,
                date_range_start  = excluded.date_range_start,
                date_range_end    = excluded.date_range_end
            """,
            (
                stat["symbol"],
                stat["strategy_type"],
                stat["trade_count"],
                stat["win_count"],
                stat["win_rate"],
                stat["avg_pnl_per_trade"],
                stat["total_pnl"],
                stat["max_drawdown"],
                stat.get("sharpe_ratio"),
                stat["confidence"],
                datetime.now(timezone.utc).isoformat(),
                stat["date_range_start"],
                stat["date_range_end"],
            ),
        )
        self._conn.commit()

    def upsert_regime_stat(self, stat: dict) -> None:
        """Insert or update a regime_strategy_stats row."""
        self._conn.execute(
            """
            INSERT INTO regime_strategy_stats (
                entry_regime, strategy_type, trade_count, win_count,
                win_rate, avg_pnl_per_trade, total_pnl, max_drawdown,
                confidence, last_updated
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entry_regime, strategy_type) DO UPDATE SET
                trade_count       = excluded.trade_count,
                win_count         = excluded.win_count,
                win_rate          = excluded.win_rate,
                avg_pnl_per_trade = excluded.avg_pnl_per_trade,
                total_pnl         = excluded.total_pnl,
                max_drawdown      = excluded.max_drawdown,
                confidence        = excluded.confidence,
                last_updated      = excluded.last_updated
            """,
            (
                stat["entry_regime"],
                stat["strategy_type"],
                stat["trade_count"],
                stat["win_count"],
                stat["win_rate"],
                stat["avg_pnl_per_trade"],
                stat["total_pnl"],
                stat["max_drawdown"],
                stat["confidence"],
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()

    def get_symbol_stat(self, symbol: str, strategy_type: str) -> Optional[dict]:
        """Return the symbol_strategy_stats row or None."""
        row = self._conn.execute(
            "SELECT * FROM symbol_strategy_stats WHERE symbol = ? AND strategy_type = ?",
            (symbol, strategy_type),
        ).fetchone()
        return dict(row) if row else None

    def get_regime_stat(self, entry_regime: str, strategy_type: str) -> Optional[dict]:
        """Return the regime_strategy_stats row or None."""
        row = self._conn.execute(
            "SELECT * FROM regime_strategy_stats WHERE entry_regime = ? AND strategy_type = ?",
            (entry_regime, strategy_type),
        ).fetchone()
        return dict(row) if row else None

    def get_all_symbol_stats(self, strategy_type: Optional[str] = None) -> list[dict]:
        """Return all symbol_strategy_stats rows, optionally filtered by strategy."""
        if strategy_type is not None:
            rows = self._conn.execute(
                "SELECT * FROM symbol_strategy_stats WHERE strategy_type = ? ORDER BY symbol",
                (strategy_type,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM symbol_strategy_stats ORDER BY symbol, strategy_type"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_all_regime_stats(self) -> list[dict]:
        """Return all regime_strategy_stats rows."""
        rows = self._conn.execute(
            "SELECT * FROM regime_strategy_stats ORDER BY entry_regime, strategy_type"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_winrate_multiplier(
        self,
        symbol: str,
        strategy_type: str,
        current_regime: Optional[str] = None,
    ) -> tuple[float, str, str]:
        """Return (multiplier, tier_label, confidence) based on backtest win-rate.

        Kill switch: RESEARCH_WINRATE_MULTIPLIER_ENABLED.
        Logic:
          - disabled → (1.0, 'neutral', 'disabled')
          - no row    → (1.0, 'neutral', 'none')
          - low/none confidence → (1.0, tier_label, confidence)
          - high confidence (EV-gated):
              win_rate < 0.30 AND avg_pnl < 0  → (0.0,  'reject',  'high')  hard floor
              win_rate < 0.30 AND avg_pnl >= 0 → (0.7,  'poor',    'high')  downgraded, not rejected
              win_rate ≥ 0.70                  → (1.3,  'strong',  'high')
              win_rate ≥ 0.60                  → (1.15, 'good',    'high')
              win_rate ≥ 0.50                  → (1.0,  'neutral', 'high')
              win_rate ≥ 0.40                  → (0.85, 'weak',    'high')
              else (0.30 ≤ wr < 0.40)          → (0.7,  'poor',    'high')

        Return value is bounded: never < 0.7 unless exactly 0.0, never > 1.3.
        """
        from config import settings

        if not settings.RESEARCH_WINRATE_MULTIPLIER_ENABLED:
            return (1.0, "neutral", "disabled")

        stat = self.get_symbol_stat(symbol, strategy_type)
        if stat is None:
            return (1.0, "neutral", "none")

        confidence = stat["confidence"]
        win_rate = stat["win_rate"]  # stored as 0.0–1.0
        avg_pnl = float(stat.get("avg_pnl_per_trade") or 0.0)

        if confidence in ("low", "none"):
            tier_label = _winrate_to_tier_label(win_rate, avg_pnl)
            return (1.0, tier_label, confidence)

        # High confidence — apply EV-gated multiplier
        tier_label = _winrate_to_tier_label(win_rate, avg_pnl)
        multiplier = _winrate_to_multiplier(win_rate, avg_pnl)
        return (multiplier, tier_label, confidence)


def _winrate_to_tier_label(win_rate: float, avg_pnl: float = 0.0) -> str:
    if win_rate < 0.30:
        return "reject" if avg_pnl < 0 else "poor"
    if win_rate >= 0.70:
        return "strong"
    if win_rate >= 0.60:
        return "good"
    if win_rate >= 0.50:
        return "neutral"
    if win_rate >= 0.40:
        return "weak"
    return "poor"


def _winrate_to_multiplier(win_rate: float, avg_pnl: float = 0.0) -> float:
    if win_rate < 0.30:
        return 0.0 if avg_pnl < 0 else 0.7
    if win_rate >= 0.70:
        return 1.3
    if win_rate >= 0.60:
        return 1.15
    if win_rate >= 0.50:
        return 1.0
    if win_rate >= 0.40:
        return 0.85
    return 0.7
