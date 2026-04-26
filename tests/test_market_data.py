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
    """yfinance fallback path: successful fetch → ex_dividend_data_available=True."""
    import time
    import data.market_data as md

    monkeypatch.setattr(md.settings, "USE_ALPACA_FOR_EX_DIVIDEND", False)
    future_ts = int(time.mktime((2099, 12, 31, 0, 0, 0, 0, 0, 0)))
    mock_ticker = _make_ticker(info={"exDividendDate": future_ts, "dividendYield": 0.015})
    monkeypatch.setattr(md, "yf", MagicMock(Ticker=lambda sym: mock_ticker))
    md._exdiv_cache.clear()

    result = md.get_ex_dividend_date("SPY")
    assert result["ex_dividend_data_available"] is True
    assert result["days_to_ex_dividend"] is not None


def test_ex_div_failure_path_sets_available_false(monkeypatch):
    """yfinance fallback path: yfinance raises → ex_dividend_data_available=False."""
    import data.market_data as md

    monkeypatch.setattr(md.settings, "USE_ALPACA_FOR_EX_DIVIDEND", False)

    def bad_ticker(sym):
        raise Exception("404 quoteSummary")

    monkeypatch.setattr(md, "yf", MagicMock(Ticker=bad_ticker))
    md._exdiv_cache.clear()

    result = md.get_ex_dividend_date("SPY")
    assert result["ex_dividend_data_available"] is False
    assert result["days_to_ex_dividend"] is None
    assert result["annual_dividend_yield"] is None


def test_ex_div_no_upcoming_ex_div_sets_available_true(monkeypatch):
    """yfinance fallback path: no exDividendDate field → available=True, days=None."""
    import data.market_data as md

    monkeypatch.setattr(md.settings, "USE_ALPACA_FOR_EX_DIVIDEND", False)
    mock_ticker = _make_ticker(info={"dividendYield": 0.0})
    monkeypatch.setattr(md, "yf", MagicMock(Ticker=lambda sym: mock_ticker))
    md._exdiv_cache.clear()

    result = md.get_ex_dividend_date("NVDA")
    assert result["ex_dividend_data_available"] is True
    assert result["days_to_ex_dividend"] is None


# ── get_ex_dividend_date: Alpaca primary path ────────────────────────────────

_ALPACA_CASH_DIV_RESPONSE = {
    "corporate_actions": {
        "cash_dividends": [
            {"symbol": "SPY", "ex_date": "2099-06-20", "payable_date": "2099-06-30", "rate": 1.45},
        ]
    },
    "next_page_token": None,
}

_ALPACA_EMPTY_RESPONSE = {
    "corporate_actions": {"cash_dividends": []},
    "next_page_token": None,
}


def test_ex_div_alpaca_primary_path_success(monkeypatch):
    """Alpaca returns an upcoming dividend — correct date, days, yield=None."""
    import data.market_data as md

    monkeypatch.setattr(md.settings, "USE_ALPACA_FOR_EX_DIVIDEND", True)
    mock_resp = MagicMock()
    mock_resp.json.return_value = _ALPACA_CASH_DIV_RESPONSE
    mock_resp.raise_for_status.return_value = None
    monkeypatch.setattr(md._requests, "get", MagicMock(return_value=mock_resp))
    md._exdiv_cache.clear()

    result = md.get_ex_dividend_date("SPY")
    assert result["next_ex_dividend_date"] == "2099-06-20"
    assert result["days_to_ex_dividend"] is not None
    assert result["annual_dividend_yield"] is None
    assert result["ex_dividend_data_available"] is True


def test_ex_div_alpaca_empty_result_no_fallback(monkeypatch):
    """Alpaca returns empty cash_dividends — all-None result, no yfinance call."""
    import data.market_data as md

    monkeypatch.setattr(md.settings, "USE_ALPACA_FOR_EX_DIVIDEND", True)
    mock_resp = MagicMock()
    mock_resp.json.return_value = _ALPACA_EMPTY_RESPONSE
    mock_resp.raise_for_status.return_value = None
    mock_get = MagicMock(return_value=mock_resp)
    monkeypatch.setattr(md._requests, "get", mock_get)
    mock_yf = MagicMock()
    monkeypatch.setattr(md, "yf", mock_yf)
    md._exdiv_cache.clear()

    result = md.get_ex_dividend_date("GLD")
    assert result["next_ex_dividend_date"] is None
    assert result["days_to_ex_dividend"] is None
    assert result["ex_dividend_data_available"] is True
    # yfinance must NOT have been called — empty is valid data, not an error
    mock_yf.Ticker.assert_not_called()


def test_ex_div_alpaca_error_falls_back_to_yfinance(monkeypatch):
    """Alpaca raises → falls back to yfinance and returns yfinance result."""
    import time
    import data.market_data as md

    monkeypatch.setattr(md.settings, "USE_ALPACA_FOR_EX_DIVIDEND", True)
    monkeypatch.setattr(md._requests, "get", MagicMock(side_effect=Exception("connection refused")))
    future_ts = int(time.mktime((2099, 12, 31, 0, 0, 0, 0, 0, 0)))
    mock_ticker = _make_ticker(info={"exDividendDate": future_ts, "dividendYield": 0.015})
    monkeypatch.setattr(md, "yf", MagicMock(Ticker=lambda sym: mock_ticker))
    md._exdiv_cache.clear()

    result = md.get_ex_dividend_date("SPY")
    assert result["next_ex_dividend_date"] is not None
    assert result["days_to_ex_dividend"] is not None
    assert result["ex_dividend_data_available"] is True


# ── get_company_profile: ETF-aware Finnhub wrapper ───────────────────────────


def test_get_company_profile_equity_passthrough():
    """Equity: FinnhubClient result passed through; is_etf=False."""
    import data.market_data as md
    from data.finnhub_client import _profile_cache

    _profile_cache.clear()
    fake_profile = {
        "sector": "Technology", "market_cap": 3_979_469.6,
        "pe_ratio": 33.5, "annual_dividend_yield": 0.0038,
        "fifty_two_week_high": 237.0, "fifty_two_week_low": 164.0,
        "profile_data_available": True, "metric_data_available": True,
    }
    with patch("data.market_data.FinnhubClient") as MockClient:
        MockClient.return_value.get_company_profile.return_value = fake_profile
        result = md.get_company_profile("AAPL")

    assert result["sector"] == "Technology"
    assert result["is_etf"] is False
    assert result["profile_data_available"] is True


def test_get_company_profile_sector_etf_overrides_sector():
    """Sector ETF (XLE): sector=None from Finnhub overridden by SECTOR_ETF_MAP."""
    import data.market_data as md
    from data.finnhub_client import _profile_cache

    _profile_cache.clear()
    fake_profile = {
        "sector": None, "market_cap": None,
        "pe_ratio": None, "annual_dividend_yield": None,
        "fifty_two_week_high": 95.0, "fifty_two_week_low": 70.0,
        "profile_data_available": False, "metric_data_available": True,
    }
    with patch("data.market_data.FinnhubClient") as MockClient:
        MockClient.return_value.get_company_profile.return_value = fake_profile
        result = md.get_company_profile("XLE")

    assert result["sector"] == "Energy"   # overridden from SECTOR_ETF_MAP
    assert result["is_etf"] is True
    assert result["fifty_two_week_high"] == 95.0


def test_get_company_profile_broad_etf_sector_none():
    """Broad-market ETF (SPY): sector stays None (not in SECTOR_ETF_MAP)."""
    import data.market_data as md
    from data.finnhub_client import _profile_cache

    _profile_cache.clear()
    fake_profile = {
        "sector": None, "market_cap": None,
        "pe_ratio": None, "annual_dividend_yield": None,
        "fifty_two_week_high": 598.0, "fifty_two_week_low": 480.0,
        "profile_data_available": False, "metric_data_available": True,
    }
    with patch("data.market_data.FinnhubClient") as MockClient:
        MockClient.return_value.get_company_profile.return_value = fake_profile
        result = md.get_company_profile("SPY")

    assert result["sector"] is None   # SPY not in SECTOR_ETF_MAP
    assert result["is_etf"] is True


def test_get_company_profile_non_etf_empty_profile_logs_warning(caplog):
    """Non-ETF symbol with empty profile data → warning logged."""
    import logging
    import data.market_data as md
    from data.finnhub_client import _profile_cache

    _profile_cache.clear()
    fake_profile = {
        "sector": None, "market_cap": None,
        "pe_ratio": None, "annual_dividend_yield": None,
        "fifty_two_week_high": None, "fifty_two_week_low": None,
        "profile_data_available": False, "metric_data_available": False,
    }
    with patch("data.market_data.FinnhubClient") as MockClient:
        MockClient.return_value.get_company_profile.return_value = fake_profile
        with caplog.at_level(logging.WARNING, logger="data.market_data"):
            result = md.get_company_profile("AAPL")

    assert result["is_etf"] is False
    assert any("ETF_SYMBOLS" in msg for msg in caplog.messages)


def test_get_company_profile_no_api_key_fast_path(monkeypatch):
    """No FINNHUB_API_KEY → fast-path return without FinnhubClient instantiation."""
    import data.market_data as md

    monkeypatch.setattr(md.settings, "FINNHUB_API_KEY", "")
    with patch("data.market_data.FinnhubClient") as MockClient:
        result = md.get_company_profile("AAPL")
        MockClient.assert_not_called()

    assert result["profile_data_available"] is False
    assert result["metric_data_available"] is False
    assert result["is_etf"] is False
