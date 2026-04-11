"""Unit tests for ORATSClient. No real network calls."""

import pytest
from unittest.mock import patch, MagicMock

from data.orats_client import ORATSClient, _summary_cache, _earnings_cache


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


@pytest.fixture(autouse=True)
def _clear_caches():
    _summary_cache.clear()
    _earnings_cache.clear()
    yield
    _summary_cache.clear()
    _earnings_cache.clear()


def _mock_resp(payload, status=200):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = payload
    m.raise_for_status.return_value = None
    if status >= 400:
        import requests
        m.raise_for_status.side_effect = requests.HTTPError(response=m)
    return m


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
