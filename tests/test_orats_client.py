"""Unit tests for ORATSClient. No real network calls."""

import pytest
from unittest.mock import patch, MagicMock

from data.orats_client import ORATSClient, _cache


SAMPLE_SUMMARY_RESPONSE = {
    "data": [{
        "ticker": "AAPL",
        "tradeDate": "2025-04-11",
        "stockPrice": 175.42,
        "ivRank1y": 38.5,
        "ivRank1m": 42.1,
        "ivPct1y": 41.2,
        "ivPct1m": 44.8,
        "atmIvM1": 0.285,
        "atmIvM2": 0.312,
        "atmIvM3": 0.298,
        "atmIvM4": 0.275,
        "iSkewM1": 0.052,
        "iSkewM2": 0.048,
        "impliedMove": 3.1,
        "fcstMove": 2.8,
    }]
}

SAMPLE_EARNINGS_RESPONSE = {
    "data": [
        {"earnDate": "2027-07-28", "anncAfterClose": 1},
        {"earnDate": "2020-01-01", "anncAfterClose": 0},
    ]
}


SAMPLE_CORES_RESPONSE = {
    "data": [{
        "ticker": "AAPL",
        "nextErn": "2025-07-28",
        "daysToNextErn": 90,
        "absAvgErnMv": 0.048,
        "impliedEarningsMove": 0.055,
        "ivHvXernRatio": 1.15,
        "ivHvXernRatio1y": 1.10,
        "volOfVol": 0.18,
        "slopepctile": 65.0,
        "slopeavg1y": 0.05,
        "orHv20d": 0.22,
        "orHv30d": 0.24,
        "orHvXern20d": 0.20,
        "rip": 0.72,
        "bestEtf": "QQQ",
        "sectorName": "Technology",
        # New forecast fields
        "orFcst20d": 0.21,
        "orIvFcst20d": 0.26,
        "orFcstInf": 0.23,
        "exErnIv20d": 0.27,
        "exErnIv30d": 0.28,
        "slope": 0.052,
        "slopeFcst": 0.048,
        "slopeInf": 0.050,
        "contango": 0.03,
        "contangoFcst": 0.025,
        "deriv": 0.001,
        "fwdRatio2030": 1.05,
        "fwdRatio3060": 1.03,
        "fwdRatio6090": 1.02,
        "confidence": 0.85,
        "rSquared": 0.92,
    }]
}


@pytest.fixture(autouse=True)
def _clear_caches():
    _cache._fallback.clear()
    if _cache._conn is not None:
        _cache._conn.execute("DELETE FROM orats_cache")
        _cache._conn.commit()
    yield
    _cache._fallback.clear()
    if _cache._conn is not None:
        _cache._conn.execute("DELETE FROM orats_cache")
        _cache._conn.commit()


def _mock_resp(payload, status=200):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = payload
    m.raise_for_status.return_value = None
    if status >= 400:
        import requests
        m.raise_for_status.side_effect = requests.HTTPError(response=m)
    return m


@pytest.mark.xfail(
    reason=(
        "SAMPLE_SUMMARY_RESPONSE asserts on fields ORATS' /summaries endpoint "
        "does not return (ivRank1y, atmIvM1, iSkewM1, fcstMove). Real /summaries "
        "fields are stockPrice, tradeDate, impliedMove, iv20d/30d/60d/90d, etc. "
        "Test fixture and assertions will be rewritten in the /summaries field "
        "correction follow-up. See data/market_data.py::get_orats_summary docstring."
    ),
    strict=False,
)
@patch("data.orats_client.requests.get")
def test_get_summary_returns_normalized_fields(mock_get):
    mock_get.return_value = _mock_resp(SAMPLE_SUMMARY_RESPONSE)
    result = ORATSClient(api_key="k").get_summary("AAPL")

    assert result is not None
    assert result["ticker"] == "AAPL"
    assert result["iv_rank_1y"] == 38.5
    assert result["iv_rank_1m"] == 42.1
    assert result["iv_pct_1y"] == 41.2
    assert result["atm_iv_m1"] == 0.285
    assert result["atm_iv_m2"] == 0.312
    assert result["skew_m1"] == 0.052
    assert result["implied_move_pct"] == 3.1
    assert result["stock_price"] == 175.42


@patch("data.orats_client.requests.get")
def test_get_summary_calculates_term_structure_slope(mock_get):
    mock_get.return_value = _mock_resp(SAMPLE_SUMMARY_RESPONSE)
    result = ORATSClient(api_key="k").get_summary("AAPL")
    assert result["term_structure_slope"] == pytest.approx(0.027, abs=0.001)


@patch("data.orats_client.requests.get")
def test_get_summary_returns_none_on_empty_data(mock_get):
    mock_get.return_value = _mock_resp({"data": []})
    assert ORATSClient(api_key="k").get_summary("AAPL") is None


@patch("data.orats_client.requests.get")
def test_get_summary_returns_none_on_http_error(mock_get):
    mock_get.return_value = _mock_resp({}, status=500)
    assert ORATSClient(api_key="k").get_summary("AAPL") is None


@patch("data.orats_client.requests.get")
def test_get_summary_caches_result(mock_get):
    mock_get.return_value = _mock_resp(SAMPLE_SUMMARY_RESPONSE)
    c = ORATSClient(api_key="k")
    c.get_summary("AAPL")
    c.get_summary("AAPL")
    assert mock_get.call_count == 1


@patch("data.orats_client.requests.get")
def test_get_earnings_filters_to_future_only(mock_get):
    mock_get.return_value = _mock_resp(SAMPLE_EARNINGS_RESPONSE)
    result = ORATSClient(api_key="k").get_earnings("AAPL")
    assert result is not None
    assert result["next_earnings_date"] == "2027-07-28"
    assert result["after_close"] is True


@patch("data.orats_client.requests.get")
def test_get_earnings_returns_none_date_when_no_future(mock_get):
    mock_get.return_value = _mock_resp({"data": [
        {"earnDate": "2020-01-01", "anncAfterClose": 0}
    ]})
    result = ORATSClient(api_key="k").get_earnings("AAPL")
    assert result["next_earnings_date"] is None


def test_classify_iv_environment_low():
    assert ORATSClient.classify_iv_environment(15.0) == "LOW"
    assert ORATSClient.classify_iv_environment(29.9) == "LOW"


def test_classify_iv_environment_moderate():
    assert ORATSClient.classify_iv_environment(30.0) == "MODERATE"
    assert ORATSClient.classify_iv_environment(49.9) == "MODERATE"


def test_classify_iv_environment_high():
    assert ORATSClient.classify_iv_environment(50.0) == "HIGH"
    assert ORATSClient.classify_iv_environment(95.0) == "HIGH"


def test_classify_iv_environment_unknown():
    assert ORATSClient.classify_iv_environment(None) == "UNKNOWN"


# ── get_cores() new forecast fields ─────────────────────────


@patch("data.orats_client.requests.get")
def test_get_cores_returns_new_forecast_fields(mock_get):
    mock_get.return_value = _mock_resp(SAMPLE_CORES_RESPONSE)
    result = ORATSClient(api_key="k").get_cores("AAPL")

    assert result is not None
    assert result["or_fcst_20d"] == 0.21
    assert result["or_iv_fcst_20d"] == 0.26
    assert result["or_fcst_inf"] == 0.23
    assert result["ex_ern_iv_20d"] == 0.27
    assert result["ex_ern_iv_30d"] == 0.28
    assert result["slope"] == 0.052
    assert result["slope_fcst"] == 0.048
    assert result["slope_inf"] == 0.050
    assert result["contango"] == 0.03
    assert result["contango_fcst"] == 0.025
    assert result["deriv"] == 0.001
    assert result["fwd_ratio_20_30"] == 1.05
    assert result["fwd_ratio_30_60"] == 1.03
    assert result["fwd_ratio_60_90"] == 1.02
    assert result["confidence"] == 0.85
    assert result["r_squared"] == 0.92


@patch("data.orats_client.requests.get")
def test_get_cores_missing_forecast_fields_return_none(mock_get):
    """When new fields are absent from the API response, they should be None."""
    mock_get.return_value = _mock_resp({"data": [{
        "ticker": "AAPL",
        "nextErn": None,
        "daysToNextErn": 90,
        # no orFcst20d, orIvFcst20d, etc.
    }]})
    result = ORATSClient(api_key="k").get_cores("AAPL")

    assert result is not None
    assert result["or_fcst_20d"] is None
    assert result["or_iv_fcst_20d"] is None
    assert result["contango"] is None
    assert result["confidence"] is None
    assert result["r_squared"] is None


@patch("data.orats_client.requests.get")
def test_get_cores_existing_fields_still_present(mock_get):
    """Existing fields are unaffected by the new additions."""
    mock_get.return_value = _mock_resp(SAMPLE_CORES_RESPONSE)
    result = ORATSClient(api_key="k").get_cores("AAPL")

    assert result["skew_percentile"] == 65.0
    assert result["hv_20d"] == 0.22
    assert result["sector_name"] == "Technology"
    assert result["best_etf"] == "QQQ"
