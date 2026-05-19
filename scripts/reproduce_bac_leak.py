"""Reproduce the 2026-05-19 BAC cross-account context leak.

Sets up the exact scenario from the bundle:

  1. paper_2 (Standard Wheel) broker holds an open BAC short put.
  2. The bot-wide journal contains a paper_2 BAC sell_put entry from
     earlier in the same session.
  3. Build the Turnover Wheel context for paper_6 on BAC.

Pre-fix, paper_6's context.open_orders contained paper_2's BAC order
and paper_6's recent_trades referenced the paper_2 journal entry.

Post-fix, paper_6's context is empty on both surfaces. Output below
is the side-by-side context dict for both accounts.

Run:
    python scripts/reproduce_bac_leak.py

No real broker / Alpaca calls. Uses MagicMock brokers and a tmpfile
journal.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

# Make repo root importable when this script is invoked as
# `python scripts/reproduce_bac_leak.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


_STUB_PATCHES = [
    ("data.market_data.get_orats_summary", {"stock_price": 48.0, "implied_move_pct": 0.02}),
    ("data.market_data.get_orats_iv_rank", {"ivRank1y": 45.0, "ivRank1m": 47.0, "ivPct1y": 40.0, "ivPct1m": 42.0}),
    ("data.market_data.get_orats_cores", {"atm_iv_m1": 0.25, "atm_iv_m2": 0.27}),
    ("data.market_data.get_orats_monies", []),
    ("data.market_data.get_stock_technicals", {"current_price": 48.0, "avg_volume_30d": 1_000_000}),
    ("data.market_data.get_company_profile", {}),
    ("data.market_data.get_vix", 18.0),
    ("data.market_data.get_fear_greed_index", {"score": 55, "rating": "neutral"}),
    ("data.market_data.get_risk_free_rate", 0.04),
    ("data.market_data.get_finnhub_earnings_history", []),
    ("data.market_data.get_finnhub_analyst_data", {}),
    ("data.market_data.get_finnhub_news_sentiment", None),
    ("data.market_data.get_earnings_calendar", {}),
    ("data.market_data.get_ex_dividend_date", {}),
    ("data.context_builder._fetch_news", []),
]


def _mock_broker(orders=None, positions=None) -> MagicMock:
    m = MagicMock()
    m.get_account.return_value = {
        "buying_power": 95_000.0,
        "options_buying_power": 95_000.0,
        "portfolio_value": 100_000.0,
    }
    m.get_positions.return_value = positions or []
    m.get_all_positions.return_value = positions or []
    m.get_orders.return_value = orders or []
    return m


def _build_with_stubs(broker, journal, account_id, symbol="BAC", state="IDLE"):
    from data.context_builder import ContextBuilder

    started = []
    for target, value in _STUB_PATCHES:
        p = patch(target, return_value=value)
        p.start()
        started.append(p)
    p_pg = patch(
        "data.context_builder.compute_portfolio_greeks",
        return_value={"net_delta": 0.0, "net_theta": 0.0, "net_vega": 0.0, "net_gamma": 0.0},
    )
    p_pg.start()
    started.append(p_pg)
    p_book = patch(
        "data.book_exposure.compute_cross_account_book_exposure",
        return_value={"by_underlying": {}},
    )
    p_book.start()
    started.append(p_book)
    try:
        builder = ContextBuilder(broker=broker, journal=journal, account_id=account_id)
        return builder.build(symbol, state)
    finally:
        for p in started:
            try:
                p.stop()
            except Exception:
                pass


def main() -> int:
    from data.trade_journal import TradeJournal

    with TemporaryDirectory() as tmp:
        journal_path = Path(tmp) / "journal.jsonl"
        journal = TradeJournal(path=journal_path)

        # Standard Wheel (paper_2) places BAC sell_put at 14:01.
        journal.append({
            "account_id": "paper_2",
            "underlying": "BAC",
            "symbol": "BAC260618P00048000",
            "contract_symbol": "BAC260618P00048000",
            "action": "sell_put",
            "limit_price": 0.65,
            "status": "submitted",
            "strategy_type": "wheel_csp",
            "iv_rank": 45.0,
            "delta": -0.25,
            "dte": 30,
        })

        paper_2_broker = _mock_broker(orders=[{
            "id": "ord_paper_2_bac",
            "symbol": "BAC260618P00048000",
            "underlying": "BAC",
            "side": "sell",
            "qty": 1,
            "limit_price": 0.65,
            "status": "new",
        }])
        paper_6_broker = _mock_broker(orders=[])

        ctx_paper_2 = _build_with_stubs(paper_2_broker, journal, account_id="paper_2")
        ctx_paper_6 = _build_with_stubs(paper_6_broker, journal, account_id="paper_6")

    def _short(ctx):
        return {
            "account.buying_power": (ctx.get("account") or {}).get("buying_power"),
            "open_orders": ctx.get("open_orders"),
            "positions": ctx.get("positions"),
            "recent_trades": ctx.get("recent_trades"),
            "skip_history": ctx.get("skip_history"),
            "performance_stats": ctx.get("performance_stats"),
            "guardrail_rejections": ctx.get("guardrail_rejections"),
        }

    side_by_side = {
        "paper_2_wheel": _short(ctx_paper_2),
        "paper_6_turnover_wheel": _short(ctx_paper_6),
    }
    print(json.dumps(side_by_side, indent=2, default=str))

    # Self-check.
    p6_orders = ctx_paper_6.get("open_orders") or []
    p6_trades = ctx_paper_6.get("recent_trades")
    leaked = []
    if any(o.get("id") == "ord_paper_2_bac" for o in p6_orders):
        leaked.append("open_orders contains paper_2's BAC order")
    if p6_trades and "BAC260618P00048000" in p6_trades:
        leaked.append("recent_trades references paper_2's BAC sell_put")

    if leaked:
        print("\nLEAK DETECTED:")
        for line in leaked:
            print(f"  - {line}")
        return 1

    print("\nNo leak. paper_6's context contains zero paper_2 references.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
