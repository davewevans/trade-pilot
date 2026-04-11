"""Unit tests for FinnhubClient. No real network calls."""

from datetime import date, timedelta
from unittest.mock import patch, MagicMock

from data.finnhub_client import FinnhubClient, _earnings_cache


def _future(days=30):
    return str(date.today() + timedelta(days=days))


def _past(days=30):
    return str(date.today() - timedelta(days=days))


def _mock_resp(payload, status=200):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = payload
    m.raise_for_status.return_value = None
    return m


@patch("data.finnhub_client._earnings_cache", {})
@patch("data.finnhub_client.requests.get")
def test_get_next_earnings_returns_future_date(mock_get):
    future = _future(28)
    mock_get.return_value = _mock_resp({
        "earningsCalendar": [
            {"date": _past(60), "symbol": "AAPL", "epsEstimate": 1.50, "revenueEstimate": 90e9},
            {"date": future, "symbol": "AAPL", "epsEstimate": 1.65, "revenueEstimate": 95e9},
        ]
    })
    result = FinnhubClient(api_key="k").get_next_earnings("AAPL")
    assert result["next_earnings_date"] == future
    assert result["eps_estimate"] == 1.65
    assert 27 <= result["days_to_earnings"] <= 29


@patch("data.finnhub_client._earnings_cache", {})
@patch("data.finnhub_client.requests.get")
def test_get_next_earnings_returns_none_when_no_future(mock_get):
    mock_get.return_value = _mock_resp({
        "earningsCalendar": [
            {"date": _past(30), "symbol": "AAPL", "epsEstimate": 1.50, "revenueEstimate": 90e9},
        ]
    })
    result = FinnhubClient(api_key="k").get_next_earnings("AAPL")
    assert result["next_earnings_date"] is None
    assert result["days_to_earnings"] is None


@patch("data.finnhub_client._earnings_cache", {})
@patch("data.finnhub_client.requests.get")
def test_get_next_earnings_returns_none_on_failure(mock_get):
    mock_get.side_effect = Exception("network error")
    result = FinnhubClient(api_key="k").get_next_earnings("AAPL")
    assert result is None


@patch("data.finnhub_client.requests.get")
def test_get_next_earnings_caches_result(mock_get):
    _earnings_cache.clear()
    future = _future(28)
    mock_get.return_value = _mock_resp({
        "earningsCalendar": [{"date": future, "symbol": "AAPL",
                               "epsEstimate": 1.5, "revenueEstimate": 90e9}]
    })
    client = FinnhubClient(api_key="k")
    client.get_next_earnings("AAPL")
    client.get_next_earnings("AAPL")
    assert mock_get.call_count == 1
    _earnings_cache.clear()
