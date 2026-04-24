"""Unit tests for ContextBuilder core behaviour. No real network calls."""

from unittest.mock import MagicMock, patch


def test_volatility_iv_rank_sourced_from_ivrank_not_summary():
    """Regression: iv_rank_1y must come from /ivrank, not /summaries.

    /summaries does not return ivRank1y. Prior to this fix, context_builder
    pulled iv_rank_1y from /summaries and got None for every symbol, blocking
    every wheel decision.
    """
    from data.context_builder import ContextBuilder

    # /summaries — has stock_price, no IV rank (real ORATS shape)
    summary = {
        "ticker": "AAPL",
        "stock_price": 175.0,
        "trade_date": "2026-04-24",
        "implied_move_pct": 0.025,
    }
    # /ivrank — has IV rank (real ORATS shape)
    ivrank = {"iv": 0.22, "ivRank1y": 47.3, "ivRank1m": 50.1, "ivPct1y": 41.0, "ivPct1m": 44.0}

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
    mock_journal.format_for_prompt.return_value = "trade1"
    mock_journal.format_stats_for_prompt.return_value = "win_rate: 60%"
    mock_journal.format_skip_history_for_prompt.return_value = "0 skips"
    mock_journal.format_rejections_for_prompt.return_value = None

    patches = [
        patch("data.market_data.get_orats_summary", return_value=summary),
        patch("data.market_data.get_orats_iv_rank", return_value=ivrank),
        patch("data.market_data.get_orats_cores", return_value={}),
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
            builder = ContextBuilder(broker=mock_broker, journal=mock_journal)
            ctx = builder.build("AAPL", "IDLE")
    finally:
        for p in patches:
            try:
                p.stop()
            except Exception:
                pass

    assert ctx["volatility"]["iv_rank_1y"] == 47.3
    assert ctx["volatility"]["iv_rank_1m"] == 50.1
    assert ctx["volatility"]["iv_pct_1y"]  == 41.0
    assert ctx["volatility"]["iv_pct_1m"]  == 44.0
    assert ctx["iv_rank"] == 47.3

    # 47.3 is in the MODERATE range (30–50)
    assert ctx["iv_environment"] == "MODERATE"
