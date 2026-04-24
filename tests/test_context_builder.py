"""Unit tests for ContextBuilder core behaviour. No real network calls."""

from unittest.mock import MagicMock, patch


def _build_ctx(summary, ivrank, cores, broker=None, journal=None):
    """Helper: build a context with all external calls mocked."""
    from data.context_builder import ContextBuilder

    if broker is None:
        broker = MagicMock()
        broker.get_account.return_value = {
            "id": "test", "buying_power": 100_000.0,
            "portfolio_value": 150_000.0, "equity": 150_000.0,
            "options_approval_level": 2,
        }
        broker.get_positions.return_value = []
        broker.get_open_orders.return_value = []
        broker.get_option_chain.return_value = {}

    if journal is None:
        journal = MagicMock()
        journal.format_for_prompt.return_value = "trade1"
        journal.format_stats_for_prompt.return_value = "win_rate: 60%"
        journal.format_skip_history_for_prompt.return_value = "0 skips"
        journal.format_rejections_for_prompt.return_value = None

    patches = [
        patch("data.market_data.get_orats_summary", return_value=summary),
        patch("data.market_data.get_orats_iv_rank", return_value=ivrank),
        patch("data.market_data.get_orats_cores", return_value=cores),
        patch("data.market_data.get_orats_monies", return_value=[]),
        patch("data.market_data.get_stock_technicals", return_value={"current_price": 175.0}),
        patch("data.market_data.get_fundamentals", return_value={}),
        patch("data.market_data.get_vix", return_value=18.5),
        patch("data.market_data.get_fear_greed_index", return_value={"score": 60, "rating": "greed"}),
        patch("data.market_data.get_risk_free_rate", return_value=0.04),
        patch("data.market_data.get_finnhub_earnings_history", return_value=[]),
        patch("data.market_data.get_finnhub_analyst_data", return_value={}),
        patch("data.market_data.get_finnhub_news_sentiment", return_value=None),
        patch("data.market_data.get_earnings_calendar", return_value={}),
        patch("data.market_data.get_vix_term_structure", return_value={}),
        patch("data.market_data.get_ex_dividend_date", return_value={}),
        patch("data.context_builder._fetch_news", return_value=[]),
    ]
    for p in patches:
        p.start()
    try:
        with patch.object(
            ContextBuilder, "_load_portfolio_patterns",
            return_value={"top_symbols": ["AAPL"], "win_rate_30d": 0.60},
        ):
            builder = ContextBuilder(broker=broker, journal=journal)
            ctx = builder.build("AAPL", "IDLE")
    finally:
        for p in patches:
            try:
                p.stop()
            except Exception:
                pass
    return ctx


_SUMMARY = {
    "ticker": "AAPL",
    "stock_price": 175.0,
    "trade_date": "2026-04-24",
    "implied_move_pct": 0.025,
}

_IVRANK = {"iv": 0.22, "ivRank1y": 47.3, "ivRank1m": 50.1, "ivPct1y": 41.0, "ivPct1m": 44.0}

_CORES = {
    "atm_iv_m1": 0.25,
    "atm_iv_m2": 0.28,
    "atm_iv_m3": 0.30,
    "atm_iv_m4": 0.31,
    "slope": 0.052,
    "or_iv_fcst_20d": 0.22,   # below atm_iv_m1=0.25 → OVERVALUED
    "skew_percentile": 65.0,
    "vol_of_vol": 0.08,
    "iv_hv_ratio": 1.15,
    "hv_20d": 0.20,
    "contango": 0.015,
}


def test_volatility_iv_rank_sourced_from_ivrank_not_summary():
    """Regression: iv_rank_1y must come from /ivrank, not /summaries.

    /summaries does not return ivRank1y. Prior to this fix, context_builder
    pulled iv_rank_1y from /summaries and got None for every symbol, blocking
    every wheel decision.
    """
    ctx = _build_ctx(_SUMMARY, _IVRANK, {})

    assert ctx["volatility"]["iv_rank_1y"] == 47.3
    assert ctx["volatility"]["iv_rank_1m"] == 50.1
    assert ctx["volatility"]["iv_pct_1y"]  == 41.0
    assert ctx["volatility"]["iv_pct_1m"]  == 44.0
    assert ctx["iv_rank"] == 47.3
    assert ctx["iv_environment"] == "MODERATE"


def test_volatility_atm_iv_sourced_from_cores():
    """B-1 regression: atm_iv_m* must come from /cores, not /summaries.

    /summaries does not return atmIvM1-M4. Prior to B-1 these were silently
    None, preventing iv_overvalued_label from ever firing.
    """
    ctx = _build_ctx(_SUMMARY, _IVRANK, _CORES)

    assert ctx["volatility"]["atm_iv_m1"] == 0.25
    assert ctx["volatility"]["atm_iv_m2"] == 0.28
    assert ctx["volatility"]["atm_iv_m3"] == 0.30
    assert ctx["volatility"]["atm_iv_m4"] == 0.31


def test_term_structure_slope_computed_from_cores():
    """B-1: term_structure_slope is derived from /cores atm IVs (m2 - m1)."""
    ctx = _build_ctx(_SUMMARY, _IVRANK, _CORES)
    # 0.28 - 0.25 = 0.03 (rounded to 4dp)
    assert ctx["volatility"]["term_structure_slope"] == 0.03


def test_term_structure_slope_none_when_cores_missing():
    """term_structure_slope is None when /cores returns no atm_iv_m* data."""
    ctx = _build_ctx(_SUMMARY, _IVRANK, {})
    assert ctx["volatility"]["term_structure_slope"] is None


def test_skew_m1_sourced_from_cores_slope():
    """B-1: skew_m1 is now cores.slope, not the broken /summaries iSkewM1 read."""
    ctx = _build_ctx(_SUMMARY, _IVRANK, _CORES)
    assert ctx["volatility"]["skew_m1"] == 0.052


def test_skew_m2_not_in_volatility():
    """B-1: skew_m2 is removed from context — no per-month skew on delayed tier."""
    ctx = _build_ctx(_SUMMARY, _IVRANK, _CORES)
    assert "skew_m2" not in ctx["volatility"]


def test_iv_overvalued_label_fires_when_atm_iv_above_forecast():
    """B-1: iv_overvalued_label is OVERVALUED when atm_iv_m1 > or_iv_fcst_20d by >5%.

    Prior to B-1, both inputs were None (atm_iv_m1 from /summaries, always null),
    so this label never fired.
    """
    ctx = _build_ctx(_SUMMARY, _IVRANK, _CORES)
    # atm_iv_m1=0.25, or_iv_fcst_20d=0.22 → (0.25-0.22)/0.22 ≈ 13.6% > 5% → OVERVALUED
    assert ctx["volatility"]["iv_overvalued_label"] == "OVERVALUED"


def test_iv_overvalued_label_none_when_cores_missing():
    """When /cores has no data, iv_overvalued_label stays None (not UNKNOWN)."""
    ctx = _build_ctx(_SUMMARY, _IVRANK, {})
    assert ctx["volatility"]["iv_overvalued_label"] is None
