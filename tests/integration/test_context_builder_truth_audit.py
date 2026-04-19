"""Truth audit: verify that context_builder.build() populates fields the dashboard
advertises as live.

If a field IS populated → test passes normally.
If a field is NOT populated → test is marked xfail with a TODO note.

DO NOT fix context_builder.py to make tests pass — this file surfaces gaps.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _nested_get(d: dict, path: str):
    """Traverse a dot-separated path through nested dicts. Returns None if missing."""
    keys = path.split(".")
    for k in keys:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


# ── Realistic fake data for mocks ────────────────────────────────────────────


_FAKE_TECHNICALS = {
    "current_price": 150.0,
    "rsi_14": 55.0,
    "sma_20": 148.0,
    "sma_50": 145.0,
    "sma_200": 140.0,
    "above_sma_20": True,
    "above_sma_50": True,
    "above_sma_200": True,
    "golden_cross": True,
    "macd": 1.2,
    "macd_signal": 0.9,
    "macd_bullish": True,
    "atr_14": 2.5,
    "bb_upper": 158.0,
    "bb_lower": 142.0,
    "volume_10d_avg": 5_000_000,
    "volume_30d_avg": 4_800_000,
}

_FAKE_FUNDAMENTALS = {
    "next_earnings_date": "2026-07-01",
    "days_to_earnings": 75,
    "pe_ratio": 25.0,
    "market_cap": 2_000_000_000,
    "sector": "Technology",
    "industry": "Software",
    "avg_daily_volume": 5_000_000,
    "week_52_high": 200.0,
    "week_52_low": 120.0,
    "next_ex_div_date": None,
    "days_to_ex_div": None,
    "annual_div_yield": 0.0,
}

_FAKE_ORATS_SUMMARY = {
    "ticker": "SPY",
    "iv_rank_1y": 45.0,
    "iv_rank_1m": 40.0,
    "iv_pct_1y": 50.0,
    "iv_pct_1m": 45.0,
    "atm_iv_m1": 0.18,
    "atm_iv_m2": 0.20,
    "atm_iv_m3": 0.22,
    "atm_iv_m4": 0.24,
    "skew_m1": 0.02,
    "skew_m2": 0.018,
    "implied_move_pct": 3.5,
    "forecast_move_pct": 3.2,
    "term_structure_slope": 0.02,
    "ex_ern_iv_30d": 0.15,
    "contango": 0.015,
    "skewing": 0.003,
    "implied_earnings_move": 0.04,
    "rip": 0.01,
}

_FAKE_ORATS_CORES = {
    "iv_hv_ratio": 1.15,
    "vol_of_vol": 0.08,
    "skew_percentile": 65.0,
    "hv_20d": 0.16,
    "hv_30d": 0.17,
    "hv_ex_earnings_20d": 0.155,
    "abs_avg_earnings_move": 0.035,
    "implied_earnings_move": 0.04,
    "or_iv_fcst_20d": 0.175,
    "or_fcst_20d": 0.165,
    "slope": 0.02,
    "contango": 0.015,
    "rip": 0.01,
    "confidence": 0.85,
    "next_earnings_date": "2026-07-01",
    "days_to_next_earnings": 75,
}

_FAKE_EARNINGS = {
    "next_earnings_date": "2026-07-01",
    "days_to_earnings": 75,
    "eps_estimate": 1.25,
    "revenue_estimate": 10_000_000,
    "source": "finnhub",
}

_FAKE_FEAR_GREED = {"score": 55, "rating": "Greed"}

_FAKE_EX_DIVIDEND = {"next_ex_dividend_date": None, "days_to_ex_dividend": None, "annual_dividend_yield": 0.0}

_FAKE_VIX_TERM = {"vix9d": 14.0, "vix3m": 17.0, "vix6m": 18.5, "contango": 3.0, "term_slope_m1_m3": 4.5}


@pytest.fixture
def mock_context(tmp_path):
    """
    Return a built context dict with all external calls mocked to return
    realistic-shaped, non-empty data.
    """
    mock_broker = MagicMock()
    mock_broker.get_account.return_value = {
        "id": "test",
        "buying_power": 100_000.0,
        "portfolio_value": 150_000.0,
        "equity": 150_000.0,
        "options_approval_level": 2,
    }
    mock_broker.get_positions.return_value = []
    mock_broker.get_open_orders.return_value = []
    mock_broker.get_option_chain.return_value = {}

    mock_journal = MagicMock()
    mock_journal.format_for_prompt.return_value = "trade1, trade2"
    mock_journal.format_stats_for_prompt.return_value = "win_rate: 60%"
    mock_journal.format_skip_history_for_prompt.return_value = "3 skips in last 30 days"
    mock_journal.format_rejections_for_prompt.return_value = None

    patches = {
        "data.market_data.get_stock_technicals": MagicMock(return_value=_FAKE_TECHNICALS),
        "data.market_data.get_fundamentals": MagicMock(return_value=_FAKE_FUNDAMENTALS),
        "data.market_data.get_vix": MagicMock(return_value=18.5),
        "data.market_data.get_fear_greed_index": MagicMock(return_value=_FAKE_FEAR_GREED),
        "data.market_data.get_risk_free_rate": MagicMock(return_value=0.052),
        "data.market_data.get_orats_summary": MagicMock(return_value=_FAKE_ORATS_SUMMARY),
        "data.market_data.get_orats_cores": MagicMock(return_value=_FAKE_ORATS_CORES),
        "data.market_data.get_orats_monies": MagicMock(return_value=[]),
        "data.market_data.get_finnhub_earnings_history": MagicMock(return_value=[]),
        "data.market_data.get_finnhub_analyst_data": MagicMock(return_value={}),
        "data.market_data.get_finnhub_news_sentiment": MagicMock(return_value={}),
        "data.market_data.get_earnings_calendar": MagicMock(return_value=_FAKE_EARNINGS),
        "data.market_data.get_vix_term_structure": MagicMock(return_value=_FAKE_VIX_TERM),
        "data.market_data.get_ex_dividend_date": MagicMock(return_value=_FAKE_EX_DIVIDEND),
        "data.market_data.interpret_vix": MagicMock(return_value="MODERATE"),
        "data.context_builder._fetch_news": MagicMock(
            return_value=[{"headline": "Test news", "source": "Test", "url": "http://x", "created_at": "2026-04-01"}]
        ),
    }

    # Apply all patches individually
    applied_patches = []
    for target, mock_val in patches.items():
        module, attr = target.rsplit(".", 1)
        p = patch(f"{module}.{attr}", mock_val)
        applied_patches.append(p)
        p.start()

    try:
        from data.context_builder import ContextBuilder

        # Patch _load_portfolio_patterns to return realistic data
        with patch.object(
            ContextBuilder,
            "_load_portfolio_patterns",
            return_value={"top_symbols": ["SPY", "AAPL"], "win_rate_30d": 0.60},
        ):
            builder = ContextBuilder(broker=mock_broker, journal=mock_journal)
            ctx = builder.build("SPY", "IDLE")
    finally:
        for p in applied_patches:
            try:
                p.stop()
            except Exception:
                pass

    return ctx


# ── Parametrized truth audit tests ────────────────────────────────────────────


@pytest.mark.parametrize("field_path,dashboard_source", [
    (
        "iv_rank",
        "DataSources.tsx: ORATS listed as live IV rank source (/summaries endpoint, iv_rank_1y)",
    ),
    (
        "volatility",
        "DataSources.tsx: volatility surface and ORATS analytics listed as live",
    ),
    (
        "macro.vix",
        "ResearchGuide.tsx + DataSources.tsx: VIX is in Claude's macro context via yfinance",
    ),
    (
        "macro.fear_greed_score",
        "DataSources.tsx: CNN Fear & Greed listed as live, score goes into macro",
    ),
    (
        "macro.risk_free_rate",
        "DataSources.tsx: FRED risk-free rate listed as live input to Claude",
    ),
    (
        "earnings",
        "DataSources.tsx: Finnhub listed as live earnings source",
    ),
    (
        "technicals",
        "DataSources.tsx: yfinance listed for technical indicators",
    ),
    (
        "fundamentals",
        "DataSources.tsx: yfinance listed for fundamentals",
    ),
    (
        "portfolio_patterns",
        "ResearchGuide.tsx + system.md: portfolio patterns are in Claude's context",
    ),
    (
        "recent_trades",
        "ReasoningExplorer.tsx shows recent_trades as a context field",
    ),
    (
        "performance_stats",
        "ReasoningExplorer.tsx shows performance_stats as a context field",
    ),
    (
        "news",
        "DataSources.tsx: Alpaca News listed as live source (5 most recent headlines)",
    ),
    (
        "volatility.iv_rank_1y",
        "DataSources.tsx: IV rank is a primary entry filter from ORATS /summaries",
    ),
    (
        "macro.fear_greed_rating",
        "DataSources.tsx: CNN Fear & Greed rating label also fetched",
    ),
    (
        "earnings.days_to_earnings",
        "DataSources.tsx: days to earnings is used in the earnings risk skip gates",
    ),
    (
        "ex_dividend",
        "DataSources.tsx + skip_reasons: ex-dividend date is a skip gate",
    ),
    (
        "volatility.iv_hv_ratio",
        "DataSources.tsx: IV/HV ratio is from ORATS /cores",
    ),
    (
        "volatility.vol_of_vol",
        "DataSources.tsx: vol-of-vol from ORATS /cores is a warning signal",
    ),
    (
        "confirmed_market_regime",
        "ResearchGuide.tsx: confirmed market regime classification is in context",
    ),
    (
        "volatility.term_structure_slope",
        "DataSources.tsx: term structure slope from ORATS /summaries",
    ),
])
def test_advertised_context_field_populated(field_path, dashboard_source, mock_context):
    """Each field that the dashboard advertises as live must be present in the context."""
    value = _nested_get(mock_context, field_path)
    assert value is not None, (
        f"Dashboard advertises '{field_path}' as live but context_builder doesn't populate it. "
        f"Source: {dashboard_source}"
    )
