"""Unit tests for market_data helpers. No real network calls."""

from unittest.mock import MagicMock, patch


def test_get_orats_iv_rank_returns_single_symbol_dict():
    """Single-symbol wrapper returns the entry from the batch dict."""
    fake_batch = {"AAPL": {"iv": 0.18, "ivRank1y": 42.5, "ivPct1y": 38.0}}
    with patch("data.market_data.get_orats_iv_rank_batch", return_value=fake_batch):
        from data.market_data import get_orats_iv_rank
        result = get_orats_iv_rank("AAPL")
        assert result == {"iv": 0.18, "ivRank1y": 42.5, "ivPct1y": 38.0}


def test_get_orats_iv_rank_returns_none_when_missing():
    """If the symbol isn't in the batch result, return None."""
    with patch("data.market_data.get_orats_iv_rank_batch", return_value={}):
        from data.market_data import get_orats_iv_rank
        assert get_orats_iv_rank("AAPL") is None


def test_get_orats_iv_rank_handles_empty_symbol():
    """Empty symbol returns None without making a batch call."""
    from data.market_data import get_orats_iv_rank
    assert get_orats_iv_rank("") is None


def test_get_orats_iv_rank_lowercase_symbol_normalized():
    """Lowercase input still finds the entry (batch keys are upper)."""
    fake_batch = {"AAPL": {"ivRank1y": 50.0}}
    with patch("data.market_data.get_orats_iv_rank_batch", return_value=fake_batch):
        from data.market_data import get_orats_iv_rank
        result = get_orats_iv_rank("aapl")
        assert result == {"ivRank1y": 50.0}


# ── get_ex_dividend_date: ex_dividend_data_available signal ─────────────────


def _make_ticker(info=None, raises=None):
    """Return a mock yf.Ticker whose .info property returns info or raises."""
    ticker = MagicMock()
    if raises is not None:
        type(ticker).info = property(lambda self: (_ for _ in ()).throw(raises))
    else:
        ticker.info = info if info is not None else {}
    return ticker


def test_ex_div_success_path_sets_available_true(monkeypatch):
    """Successful yfinance fetch → ex_dividend_data_available=True."""
    import time
    import data.market_data as md

    # Use a future timestamp (year 2099) so ex-div is upcoming
    future_ts = int(time.mktime((2099, 12, 31, 0, 0, 0, 0, 0, 0)))
    mock_ticker = _make_ticker(info={"exDividendDate": future_ts, "dividendYield": 0.015})
    monkeypatch.setattr(md, "yf", MagicMock(Ticker=lambda sym: mock_ticker))
    md._exdiv_cache.clear()

    result = md.get_ex_dividend_date("SPY")
    assert result["ex_dividend_data_available"] is True
    assert result["days_to_ex_dividend"] is not None


def test_ex_div_failure_path_sets_available_false(monkeypatch):
    """yfinance raises → ex_dividend_data_available=False, all data fields None."""
    import data.market_data as md

    failing_ticker = MagicMock()
    failing_ticker.info = property(lambda self: (_ for _ in ()).throw(Exception("404 quoteSummary")))

    def bad_ticker(sym):
        raise Exception("404 quoteSummary")

    monkeypatch.setattr(md, "yf", MagicMock(Ticker=bad_ticker))
    md._exdiv_cache.clear()

    result = md.get_ex_dividend_date("SPY")
    assert result["ex_dividend_data_available"] is False
    assert result["days_to_ex_dividend"] is None
    assert result["annual_dividend_yield"] is None


def test_ex_div_no_upcoming_ex_div_sets_available_true(monkeypatch):
    """Successful fetch but no exDividendDate field → available=True, days=None."""
    import data.market_data as md

    mock_ticker = _make_ticker(info={"dividendYield": 0.0})
    monkeypatch.setattr(md, "yf", MagicMock(Ticker=lambda sym: mock_ticker))
    md._exdiv_cache.clear()

    result = md.get_ex_dividend_date("NVDA")
    assert result["ex_dividend_data_available"] is True
    assert result["days_to_ex_dividend"] is None


# ── get_fundamentals: ex_dividend_data_available signal ─────────────────────


def test_fundamentals_success_path_sets_available_true(monkeypatch):
    """Successful fundamentals fetch → ex_dividend_data_available=True."""
    import data.market_data as md

    mock_ticker = MagicMock()
    mock_ticker.info = {"exDividendDate": None, "trailingPE": 20.0}
    mock_ticker.calendar = {}
    mock_ticker.recommendations = None
    monkeypatch.setattr(md, "yf", MagicMock(Ticker=lambda sym: mock_ticker))
    md._fundamentals_cache.clear()

    result = md.get_fundamentals("AAPL")
    assert result["ex_dividend_data_available"] is True


def test_fundamentals_failure_path_sets_available_false(monkeypatch):
    """yfinance raises on fundamentals fetch → ex_dividend_data_available=False."""
    import data.market_data as md

    def bad_ticker(sym):
        raise Exception("404 quoteSummary")

    monkeypatch.setattr(md, "yf", MagicMock(Ticker=bad_ticker))
    md._fundamentals_cache.clear()

    result = md.get_fundamentals("SPY")
    assert result["ex_dividend_data_available"] is False
    assert result["days_to_ex_dividend"] is None
    assert result["days_to_earnings"] is None
