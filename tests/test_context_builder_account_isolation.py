"""Cross-account context isolation regression tests.

Reproduces the 2026-05-19 BAC leak where the Turnover Wheel (paper_6)
context contained the Standard Wheel's (paper_2) open BAC order, because
the bot shared a single ContextBuilder bound to the default broker
(paper_1) and a single bot-wide journal whose reads filtered only by
symbol.

Two leak surfaces are exercised independently:

1. open_orders / positions / account — sourced from ``self.broker``;
   the regression is that all strategies shared one builder and thus
   one broker. Fix: each strategy constructs its own ContextBuilder
   with its own broker.

2. recent_trades / skip_history / performance_stats / guardrail_rejections
   — sourced from the bot-wide ``journal.jsonl``; the regression is that
   reads filtered only by symbol. Fix: every read accepts ``account_id``
   with strict-equality semantics. Pre-fix entries (no ``account_id`` key)
   do not match any explicit account_id query.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ── Shared scaffolding ────────────────────────────────────────────────────


def _stub_market_data():
    """Return a list of context manager patches stubbing every market_data
    call ContextBuilder.build() makes. Caller pushes them with ExitStack
    so the test body stays uncluttered."""
    return [
        patch("data.market_data.get_orats_summary", return_value={"stock_price": 48.0, "implied_move_pct": 0.02}),
        patch("data.market_data.get_orats_iv_rank", return_value={"ivRank1y": 45.0, "ivRank1m": 47.0, "ivPct1y": 40.0, "ivPct1m": 42.0}),
        patch("data.market_data.get_orats_cores", return_value={"atm_iv_m1": 0.25, "atm_iv_m2": 0.27}),
        patch("data.market_data.get_orats_monies", return_value=[]),
        patch("data.market_data.get_stock_technicals", return_value={"current_price": 48.0, "avg_volume_30d": 1_000_000}),
        patch("data.market_data.get_company_profile", return_value={}),
        patch("data.market_data.get_vix", return_value=18.0),
        patch("data.market_data.get_fear_greed_index", return_value={"score": 55, "rating": "neutral"}),
        patch("data.market_data.get_risk_free_rate", return_value=0.04),
        patch("data.market_data.get_finnhub_earnings_history", return_value=[]),
        patch("data.market_data.get_finnhub_analyst_data", return_value={}),
        patch("data.market_data.get_finnhub_news_sentiment", return_value=None),
        patch("data.market_data.get_earnings_calendar", return_value={}),
        patch("data.market_data.get_ex_dividend_date", return_value={}),
        patch("data.context_builder._fetch_news", return_value=[]),
        # Greeks read from per-account snapshot files; suppress to avoid
        # filesystem dependence.
        patch(
            "data.context_builder.compute_portfolio_greeks",
            return_value={"net_delta": 0.0, "net_theta": 0.0, "net_vega": 0.0, "net_gamma": 0.0},
        ),
        # book_exposure is intentionally cross-account; stub to a stable value.
        patch(
            "data.book_exposure.compute_cross_account_book_exposure",
            return_value={"by_underlying": {}},
        ),
    ]


def _mock_broker(orders: list[dict] | None = None, positions: list[dict] | None = None) -> MagicMock:
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


def _build(broker, journal, account_id, symbol="BAC", state="IDLE"):
    """Run ContextBuilder.build with all external calls stubbed."""
    from data.context_builder import ContextBuilder

    patches = _stub_market_data()
    for p in patches:
        p.start()
    try:
        builder = ContextBuilder(broker=broker, journal=journal, account_id=account_id)
        return builder.build(symbol, state)
    finally:
        for p in patches:
            try:
                p.stop()
            except Exception:
                pass


# ── Leak surface 1: open_orders / positions / account ─────────────────────


def test_open_orders_isolated_when_each_strategy_uses_its_own_broker(tmp_path):
    """Account A's open orders MUST NOT appear in account B's context.

    Pre-fix: market_open.py shared one ctx_builder constructed with the
    default broker (paper_1); every strategy saw paper_1's open_orders.
    Post-fix: each strategy constructs its own ContextBuilder with its
    own broker — paper_2's broker for the wheel, paper_6 for turnover.
    """
    from data.trade_journal import TradeJournal

    journal_path = tmp_path / "journal.jsonl"
    journal = TradeJournal(path=journal_path)

    # Standard Wheel's broker (paper_2) holds the open BAC short put.
    paper_2_broker = _mock_broker(orders=[{
        "id": "ord_paper_2_bac",
        "symbol": "BAC260618P00048000",
        "underlying": "BAC",
        "side": "sell",
        "qty": 1,
        "limit_price": 0.65,
        "status": "new",
    }])

    # Turnover Wheel's broker (paper_6) has nothing on BAC.
    paper_6_broker = _mock_broker(orders=[])

    ctx_wheel = _build(paper_2_broker, journal, account_id="paper_2")
    ctx_turnover = _build(paper_6_broker, journal, account_id="paper_6")

    # Wheel sees its own order.
    wheel_orders = ctx_wheel.get("open_orders") or []
    assert any(o.get("id") == "ord_paper_2_bac" for o in wheel_orders), (
        "paper_2's ContextBuilder should surface paper_2's BAC open order"
    )

    # Turnover Wheel must see zero of paper_2's orders.
    turnover_orders = ctx_turnover.get("open_orders") or []
    assert all(o.get("id") != "ord_paper_2_bac" for o in turnover_orders), (
        f"paper_6's context leaked paper_2's open orders: {turnover_orders}"
    )
    assert turnover_orders == [], (
        f"paper_6's broker had no BAC orders; context.open_orders must be empty, got {turnover_orders}"
    )


# ── Leak surface 2: bot-wide journal fields ───────────────────────────────


def test_recent_trades_filter_by_account_id_strict_equality(tmp_path):
    """A journal entry tagged account_id='paper_2' must NOT appear in
    context.recent_trades when ContextBuilder is built for paper_6.

    This is the direct repro for the BAC leak via the recent_trades
    surface. Even if the broker leak is fixed, the journal would
    continue to expose cross-strategy activity without this filter.
    """
    from data.trade_journal import TradeJournal

    journal_path = tmp_path / "journal.jsonl"
    journal = TradeJournal(path=journal_path)

    # Standard Wheel logs its BAC sell_put (paper_2).
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

    # Build context for paper_6. Broker doesn't matter for this assertion;
    # the journal is the source under test.
    ctx_paper_6 = _build(_mock_broker(), journal, account_id="paper_6")
    assert ctx_paper_6.get("recent_trades") in (None, ""), (
        f"paper_6's recent_trades must be empty when only paper_2 has "
        f"BAC history; got: {ctx_paper_6.get('recent_trades')!r}"
    )

    # Sanity: paper_2 itself DOES see the entry.
    ctx_paper_2 = _build(_mock_broker(), journal, account_id="paper_2")
    assert "BAC260618P00048000" in (ctx_paper_2.get("recent_trades") or ""), (
        "paper_2's recent_trades should include its own BAC sell_put"
    )


def test_pre_fix_journal_entries_excluded_from_account_id_queries(tmp_path):
    """Entries written before this fix have no account_id key. Under
    strict-equality semantics they MUST NOT match any explicit account_id
    query — otherwise the cross-account leak persists until the journal
    is rotated.

    None acts as "bot-wide" only when the QUERY is None.
    """
    from data.trade_journal import TradeJournal

    journal_path = tmp_path / "journal.jsonl"
    journal = TradeJournal(path=journal_path)

    # Legacy entry written before account_id existed.
    journal.append({
        "underlying": "BAC",
        "symbol": "BAC260618P00048000",
        "contract_symbol": "BAC260618P00048000",
        "action": "sell_put",
        "limit_price": 0.65,
        "status": "submitted",
        "strategy_type": "wheel_csp",
    })

    # Strict equality: querying for paper_2 must NOT match the legacy entry.
    paper_2_view = journal.format_for_prompt("BAC", account_id="paper_2")
    assert paper_2_view == "", (
        "Legacy entry with no account_id leaked into a strict paper_2 query"
    )

    # Bot-wide query (account_id=None) is unchanged — legacy entry visible.
    bot_wide_view = journal.format_for_prompt("BAC")
    assert "BAC260618P00048000" in bot_wide_view, (
        "Legacy entry must still be visible to bot-wide (account_id=None) reads"
    )


def test_skip_history_and_rejections_also_scope_by_account_id(tmp_path):
    """Defence in depth: every account-sensitive journal surface must
    filter by account_id, not just recent_trades."""
    from data.trade_journal import TradeJournal

    journal_path = tmp_path / "journal.jsonl"
    journal = TradeJournal(path=journal_path)

    # paper_2 records a skip.
    journal.append({
        "account_id": "paper_2",
        "underlying": "BAC",
        "action": "skip",
        "status": "skipped",
        "skip_code": "LOW_IVR",
        "skip_reason": "ivr_below_threshold",
        "reasoning": "IV rank 18 below threshold",
        "strategy_type": "wheel_csp",
    })
    # paper_2 records a guardrail rejection.
    journal.append({
        "account_id": "paper_2",
        "underlying": "BAC",
        "action": "skip",
        "status": "rejected",
        "action_proposed": "sell_put",
        "skip_code": "DELTA_OUT_OF_RANGE",
        "rejection_reason": "delta -0.38 out of range",
        "strategy_type": "wheel_csp",
    })

    assert journal.format_skip_history_for_prompt("BAC", account_id="paper_6") == ""
    assert journal.format_rejections_for_prompt("BAC", account_id="paper_6") == ""
    # paper_2 sees both.
    assert "LOW_IVR" in journal.format_skip_history_for_prompt("BAC", account_id="paper_2")
    assert "DELTA_OUT_OF_RANGE" in journal.format_rejections_for_prompt("BAC", account_id="paper_2")


# ── Pre-fix code MUST fail one of these tests (sanity for the regression) ─
# (Documented in the PR description, not enforced here — pytest can't
# bisect; instead the diff shows the journal filter additions and the
# per-strategy ContextBuilder construction.)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
