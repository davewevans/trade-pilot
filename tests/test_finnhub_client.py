"""Unit tests for FinnhubClient. No real network calls."""

from datetime import date, timedelta
from unittest.mock import patch, MagicMock

import finnhub

from data.finnhub_client import (
    FinnhubClient,
    _earnings_cache,
    _earnings_history_cache,
    _analyst_cache,
    _sentiment_cache,
    _ENDPOINT_DISABLED,
)


def _future(days=30):
    return str(date.today() + timedelta(days=days))


def _past(days=30):
    return str(date.today() - timedelta(days=days))


def _make_client_with_mock_sdk() -> tuple[FinnhubClient, MagicMock]:
    """Return a FinnhubClient whose underlying SDK client is a MagicMock."""
    client = FinnhubClient(api_key="k")
    sdk = MagicMock()
    client._client = sdk
    return client, sdk


# ── get_next_earnings ───────────────────────────────────────


def test_get_next_earnings_returns_future_date():
    _earnings_cache.clear()
    future = _future(28)
    client, sdk = _make_client_with_mock_sdk()
    sdk.earnings_calendar.return_value = {
        "earningsCalendar": [
            {"date": _past(60), "symbol": "AAPL", "epsEstimate": 1.50, "revenueEstimate": 90e9},
            {"date": future, "symbol": "AAPL", "epsEstimate": 1.65, "revenueEstimate": 95e9},
        ]
    }
    result = client.get_next_earnings("AAPL")
    assert result["next_earnings_date"] == future
    assert result["eps_estimate"] == 1.65
    assert 27 <= result["days_to_earnings"] <= 29


def test_get_next_earnings_returns_none_when_no_future():
    _earnings_cache.clear()
    client, sdk = _make_client_with_mock_sdk()
    sdk.earnings_calendar.return_value = {
        "earningsCalendar": [
            {"date": _past(30), "symbol": "AAPL", "epsEstimate": 1.50, "revenueEstimate": 90e9},
        ]
    }
    result = client.get_next_earnings("AAPL")
    assert result["next_earnings_date"] is None
    assert result["days_to_earnings"] is None


def test_get_next_earnings_returns_none_on_failure():
    _earnings_cache.clear()
    client, sdk = _make_client_with_mock_sdk()
    sdk.earnings_calendar.side_effect = Exception("network error")
    assert client.get_next_earnings("AAPL") is None


def test_get_next_earnings_caches_result():
    _earnings_cache.clear()
    future = _future(28)
    client, sdk = _make_client_with_mock_sdk()
    sdk.earnings_calendar.return_value = {
        "earningsCalendar": [
            {"date": future, "symbol": "AAPL", "epsEstimate": 1.5, "revenueEstimate": 90e9}
        ]
    }
    client.get_next_earnings("AAPL")
    client.get_next_earnings("AAPL")
    assert sdk.earnings_calendar.call_count == 1
    _earnings_cache.clear()


# ── get_earnings_history ────────────────────────────────────


def test_get_earnings_history_sorts_filters_and_limits():
    _earnings_history_cache.clear()
    client, sdk = _make_client_with_mock_sdk()
    sdk.company_earnings.return_value = [
        {"period": "2024-12-31", "actual": 1.10, "estimate": 1.00,
         "surprise": 0.10, "surprisePercent": 10.0},
        {"period": "2024-09-30", "actual": 0.95, "estimate": 1.00,
         "surprise": -0.05, "surprisePercent": -5.0},
        # Unconfirmed future estimate — should be filtered out.
        {"period": "2025-03-31", "actual": None, "estimate": 1.20,
         "surprise": None, "surprisePercent": None},
        {"period": "2024-06-30", "actual": 1.05, "estimate": 1.02,
         "surprise": 0.03, "surprisePercent": 2.94},
    ]
    history = client.get_earnings_history("AAPL", limit=2)
    assert len(history) == 2
    # Most recent confirmed quarter first
    assert history[0]["period"] == "2024-12-31"
    assert history[0]["surprise_pct"] == 10.0
    assert history[1]["period"] == "2024-09-30"
    # No unconfirmed entries
    assert all(h["actual"] is not None for h in history)


def test_get_earnings_history_returns_empty_on_failure():
    _earnings_history_cache.clear()
    client, sdk = _make_client_with_mock_sdk()
    sdk.company_earnings.side_effect = Exception("boom")
    assert client.get_earnings_history("AAPL") == []


# ── get_analyst_data ────────────────────────────────────────


def test_get_analyst_data_full_success():
    _analyst_cache.clear()
    client, sdk = _make_client_with_mock_sdk()
    _ENDPOINT_DISABLED["price_target"] = False
    _ENDPOINT_DISABLED["upgrade_downgrade"] = False
    sdk.recommendation_trends.return_value = [
        {"period": "2025-01-01", "strongBuy": 5, "buy": 10,
         "hold": 3, "sell": 1, "strongSell": 0},
    ]
    sdk.price_target.return_value = {
        "targetMean": 200.0, "targetHigh": 250.0,
        "targetLow": 150.0, "targetMedian": 195.0,
    }
    # gradeTime is unix seconds; pick something deterministic.
    ts = 1_700_000_000
    sdk.upgrade_downgrade.return_value = [
        {"gradeTime": ts, "action": "up",
         "fromGrade": "Hold", "toGrade": "Buy", "company": "FirmA"},
    ]
    data = client.get_analyst_data("AAPL")

    assert data["recommendation"]["strong_buy"] == 5
    assert data["recommendation"]["period"] == "2025-01-01"
    assert data["price_target"]["mean"] == 200.0
    assert data["price_target"]["median"] == 195.0
    assert len(data["recent_rating_changes"]) == 1
    assert data["recent_rating_changes"][0]["from_grade"] == "Hold"
    assert data["recent_rating_changes"][0]["firm"] == "FirmA"
    # Date string was converted from unix timestamp
    assert data["recent_rating_changes"][0]["date"]


def test_get_analyst_data_partial_failure():
    """One sub-call fails, the other two still populate."""
    _analyst_cache.clear()
    client, sdk = _make_client_with_mock_sdk()
    _ENDPOINT_DISABLED["price_target"] = False
    _ENDPOINT_DISABLED["upgrade_downgrade"] = False
    sdk.recommendation_trends.return_value = [
        {"period": "2025-01-01", "strongBuy": 1, "buy": 2,
         "hold": 3, "sell": 4, "strongSell": 5},
    ]
    sdk.price_target.side_effect = Exception("price target down")
    sdk.upgrade_downgrade.return_value = []

    data = client.get_analyst_data("AAPL")
    assert data["recommendation"]["strong_sell"] == 5
    # Price target fails → all None defaults
    assert data["price_target"] == {
        "mean": None, "high": None, "low": None, "median": None,
    }
    assert data["recent_rating_changes"] == []


# ── get_news_sentiment ──────────────────────────────────────


def test_get_news_sentiment_computes_buzz_ratio():
    _sentiment_cache.clear()
    client, sdk = _make_client_with_mock_sdk()
    _ENDPOINT_DISABLED["news_sentiment"] = False
    sdk.news_sentiment.return_value = {
        "sentiment": {"bullishPercent": 0.62, "bearishPercent": 0.38},
        "buzz": {"articlesInLastWeek": 30, "weeklyAverage": 12.0},
        "companyNewsScore": 0.71,
    }
    s = client.get_news_sentiment("AAPL")
    assert s["bullish_pct"] == 0.62
    assert s["bearish_pct"] == 0.38
    assert s["company_score"] == 0.71
    assert s["articles_this_week"] == 30
    assert s["weekly_avg_articles"] == 12.0
    assert s["buzz_ratio"] == round(30 / 12.0, 3)


def test_get_news_sentiment_handles_zero_weekly_average():
    _sentiment_cache.clear()
    client, sdk = _make_client_with_mock_sdk()
    _ENDPOINT_DISABLED["news_sentiment"] = False
    sdk.news_sentiment.return_value = {
        "sentiment": {"bullishPercent": 0.5, "bearishPercent": 0.5},
        "buzz": {"articlesInLastWeek": 5, "weeklyAverage": 0},
        "companyNewsScore": 0.5,
    }
    s = client.get_news_sentiment("AAPL")
    assert s["buzz_ratio"] is None


def test_get_news_sentiment_returns_none_on_failure():
    _sentiment_cache.clear()
    client, sdk = _make_client_with_mock_sdk()
    _ENDPOINT_DISABLED["news_sentiment"] = False
    sdk.news_sentiment.side_effect = Exception("rate limited")
    assert client.get_news_sentiment("AAPL") is None


def _make_403_exception():
    """Build a FinnhubAPIException with status_code=403."""
    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_resp.json.return_value = {"error": "Forbidden"}
    return finnhub.FinnhubAPIException(mock_resp)


def test_price_target_short_circuits_after_403(monkeypatch):
    """After one 403, subsequent calls skip the API entirely."""
    _analyst_cache.clear()
    client, sdk = _make_client_with_mock_sdk()
    _ENDPOINT_DISABLED["price_target"] = False
    _ENDPOINT_DISABLED["upgrade_downgrade"] = False

    sdk.price_target.side_effect = _make_403_exception()
    sdk.recommendation_trends.return_value = []
    sdk.upgrade_downgrade.return_value = []

    # First call — hits API, gets 403, sets flag, returns empty
    result = client.get_analyst_data("AAPL")
    assert result["price_target"] == {"mean": None, "high": None, "low": None, "median": None}
    assert _ENDPOINT_DISABLED["price_target"] is True

    # Second call — flag is set so API is NOT called
    _analyst_cache.clear()
    sdk.price_target.reset_mock()
    client.get_analyst_data("MSFT")
    sdk.price_target.assert_not_called()


def test_news_sentiment_short_circuits_after_403():
    """After one 403, subsequent calls to news_sentiment return None without hitting API."""
    _sentiment_cache.clear()
    client, sdk = _make_client_with_mock_sdk()
    _ENDPOINT_DISABLED["news_sentiment"] = False

    sdk.news_sentiment.side_effect = _make_403_exception()

    # First call — hits API, gets 403, sets flag, returns None
    result = client.get_news_sentiment("AAPL")
    assert result is None
    assert _ENDPOINT_DISABLED["news_sentiment"] is True

    # Second call — flag set, API not called
    _sentiment_cache.clear()
    sdk.news_sentiment.reset_mock()
    result2 = client.get_news_sentiment("MSFT")
    assert result2 is None
    sdk.news_sentiment.assert_not_called()


def test_finnhub_paid_tier_false_preemptively_disables():
    """FINNHUB_PAID_TIER=false (default) disables all three premium endpoints at startup."""
    _ENDPOINT_DISABLED["price_target"] = False
    _ENDPOINT_DISABLED["upgrade_downgrade"] = False
    _ENDPOINT_DISABLED["news_sentiment"] = False

    # Instantiate a new client (FINNHUB_PAID_TIER defaults to false in settings)
    FinnhubClient(api_key="k")
    # All three should now be disabled
    assert _ENDPOINT_DISABLED["price_target"] is True
    assert _ENDPOINT_DISABLED["upgrade_downgrade"] is True
    assert _ENDPOINT_DISABLED["news_sentiment"] is True
