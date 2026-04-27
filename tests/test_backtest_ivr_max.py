"""ivr_max field tests for BacktestParams (Prompt 4)."""

import pytest
from unittest.mock import MagicMock, patch

from backtesting.engine import BacktestEngine, BacktestParams


def _make_minimal_engine():
    with patch("backtesting.engine.BacktestEngine.__init__", return_value=None):
        engine = BacktestEngine.__new__(BacktestEngine)
    engine._orats = MagicMock()
    engine._slippage_model = "orats"
    engine._custom_slippage_pct = 0.0
    engine._total_slippage_cost = 0.0
    engine._symbol_prices = {}
    return engine


def test_ivr_max_defaults_to_none():
    p = BacktestParams()
    assert p.ivr_max is None


def test_ivr_max_can_be_set():
    p = BacktestParams(ivr_max=50.0)
    assert p.ivr_max == 50.0


def test_ivr_threshold_min_only_unchanged_behavior():
    """When ivr_max is None, only ivr_threshold gates entry (back-compat check)."""
    engine = _make_minimal_engine()
    engine._orats.get_summary_on_date.return_value = {"iv_rank_1y": 45.0}
    engine._orats.get_strikes_on_date.return_value = []
    engine._orats.get_cores_on_date.return_value = {}

    params = BacktestParams(
        strategy="bull_put_spread",
        symbols=["SPY"],
        start_date="2024-01-02",
        end_date="2024-01-02",
        ivr_threshold=30.0,
        ivr_max=None,
    )

    market_data = {
        "2024-01-02": {
            "vix": 14.0, "spy_close": 470.0,
            "above_sma_50": True, "above_sma_200": True,
            "regime": "BULL", "iv_env": "MODERATE",
        }
    }

    with (
        patch("backtesting.engine._load_market_data", return_value=market_data),
        patch("backtesting.engine._load_symbol_prices", return_value={}),
    ):
        result = engine.run(params)

    # IVR=45 >= threshold=30, no ivr_max → entry attempted (0 trades because no contracts)
    assert result.total_trades == 0


def test_ivr_max_skips_high_ivr_days():
    """When ivr_max=50, days with iv_rank=70 are skipped for entry."""
    engine = _make_minimal_engine()
    # iv_rank = 70 > ivr_max = 50 → should skip
    engine._orats.get_summary_on_date.return_value = {"iv_rank_1y": 70.0}
    engine._orats.get_strikes_on_date.return_value = [
        {"strike": 450.0, "delta": -0.25, "mid_price": 2.00,
         "expiration_date": "2024-02-16", "dte": 28}
    ]
    engine._orats.get_cores_on_date.return_value = {}
    engine._orats.find_contract_on_date.return_value = None

    params = BacktestParams(
        strategy="bull_put_spread",
        symbols=["SPY"],
        start_date="2024-01-02",
        end_date="2024-01-02",
        ivr_threshold=0.0,
        ivr_max=50.0,
    )

    market_data = {
        "2024-01-02": {
            "vix": 14.0, "spy_close": 470.0,
            "above_sma_50": True, "above_sma_200": True,
            "regime": "BULL", "iv_env": "MODERATE",
        }
    }

    with (
        patch("backtesting.engine._load_market_data", return_value=market_data),
        patch("backtesting.engine._load_symbol_prices", return_value={}),
    ):
        result = engine.run(params)

    assert result.total_trades == 0


def test_ivr_band_enforced_when_both_set():
    """ivr_threshold=30 and ivr_max=50 enforces a band; days outside skipped."""
    def _run_with_ivr(ivr_rank: float):
        engine = _make_minimal_engine()
        engine._orats.get_summary_on_date.return_value = {"iv_rank_1y": ivr_rank}
        engine._orats.get_strikes_on_date.return_value = [
            {"strike": 450.0, "delta": -0.25, "mid_price": 2.00,
             "expiration_date": "2024-02-16", "dte": 28}
        ]
        engine._orats.get_cores_on_date.return_value = {}
        engine._orats.find_contract_on_date.return_value = None

        params = BacktestParams(
            strategy="wheel_csp",
            symbols=["SPY"],
            start_date="2024-01-02",
            end_date="2024-01-02",
            ivr_threshold=30.0,
            ivr_max=50.0,
        )
        market_data = {
            "2024-01-02": {
                "vix": 14.0, "spy_close": 470.0,
                "above_sma_50": True, "above_sma_200": True,
                "regime": "NEUTRAL", "iv_env": "MODERATE",
            }
        }
        with (
            patch("backtesting.engine._load_market_data", return_value=market_data),
            patch("backtesting.engine._load_symbol_prices", return_value={}),
        ):
            return engine.run(params)

    # IVR=20 < min=30 → skipped
    result_low = _run_with_ivr(20.0)
    assert result_low.total_trades == 0

    # IVR=70 > max=50 → skipped
    result_high = _run_with_ivr(70.0)
    assert result_high.total_trades == 0
