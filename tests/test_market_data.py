"""Unit tests for market_data helpers. No real network calls."""

from unittest.mock import patch


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
