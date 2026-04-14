"""Tests for slippage modeling in the backtesting engine (Phase 1)."""

import pytest
from unittest.mock import MagicMock, patch

from backtesting.engine import (
    BacktestEngine,
    BacktestParams,
    BacktestResult,
    _apply_slippage,
    _STRATEGY_LEG_COUNTS,
    _ORATS_SLIPPAGE_BY_LEGS,
)


# ── _apply_slippage unit tests ────────────────────────────────────────────────

class TestApplySlippage:
    """Test the _apply_slippage helper function."""

    def test_sell_side_returns_less_than_mid(self):
        """When selling (entry of credit strategy), price should be below mid."""
        mid = 2.00
        result = _apply_slippage(mid, "bull_put_spread", "entry", slippage_model="orats")
        assert result < mid, f"Expected slipped sell price < mid ({mid}), got {result}"

    def test_buy_side_returns_more_than_mid(self):
        """When buying (exit of credit strategy), price should be above mid."""
        mid = 2.00
        result = _apply_slippage(mid, "bull_put_spread", "exit", slippage_model="orats")
        assert result > mid, f"Expected slipped buy price > mid ({mid}), got {result}"

    def test_debit_entry_returns_more_than_mid(self):
        """Entry of debit strategy is a BUY; price should be above mid."""
        mid = 2.00
        result = _apply_slippage(mid, "long_call_vertical", "entry", slippage_model="orats")
        assert result > mid, f"Expected debit entry price > mid ({mid}), got {result}"

    def test_debit_exit_returns_less_than_mid(self):
        """Exit of debit strategy is a SELL; price should be below mid."""
        mid = 2.00
        result = _apply_slippage(mid, "long_call_vertical", "exit", slippage_model="orats")
        assert result < mid, f"Expected debit exit price < mid ({mid}), got {result}"

    def test_1_leg_strategy_uses_75_pct(self):
        """wheel_csp (1 leg) should use 75% slippage."""
        mid = 2.00
        bid = mid * 0.985
        ask = mid * 1.015
        spread = ask - bid
        expected = ask - spread * 0.75  # sell side
        result = _apply_slippage(mid, "wheel_csp", "entry", slippage_model="orats")
        assert abs(result - expected) < 1e-9

    def test_2_leg_strategy_uses_66_pct(self):
        """bull_put_spread (2 legs) should use 66% slippage."""
        mid = 2.00
        bid = mid * 0.985
        ask = mid * 1.015
        spread = ask - bid
        expected = ask - spread * 0.66  # sell side at entry
        result = _apply_slippage(mid, "bull_put_spread", "entry", slippage_model="orats")
        assert abs(result - expected) < 1e-9

    def test_4_leg_strategy_uses_53_pct(self):
        """iron_condor (4 legs) should use 53% slippage."""
        mid = 2.00
        bid = mid * 0.985
        ask = mid * 1.015
        spread = ask - bid
        expected = ask - spread * 0.53  # sell side at entry
        result = _apply_slippage(mid, "iron_condor", "entry", slippage_model="orats")
        assert abs(result - expected) < 1e-9

    def test_slippage_none_returns_raw_mid(self):
        """slippage_model='none' should return the raw mid price unchanged."""
        mid = 2.00
        for strategy in ["wheel_csp", "bull_put_spread", "iron_condor", "long_call_vertical"]:
            for side in ["entry", "exit"]:
                result = _apply_slippage(mid, strategy, side, slippage_model="none")
                assert result == mid, f"Expected mid unchanged for model=none, got {result}"

    def test_custom_slippage_pct(self):
        """slippage_model='custom' should use custom_slippage_pct."""
        mid = 2.00
        custom_pct = 0.50
        bid = mid * 0.985
        ask = mid * 1.015
        spread = ask - bid
        expected = ask - spread * custom_pct  # sell side
        result = _apply_slippage(
            mid, "bull_put_spread", "entry",
            slippage_model="custom", custom_slippage_pct=custom_pct,
        )
        assert abs(result - expected) < 1e-9

    def test_real_bid_ask_used_when_provided(self):
        """When bid/ask are provided, use them instead of synthetic spread."""
        mid = 2.00
        bid = 1.80
        ask = 2.20
        spread = ask - bid
        slippage_pct = _ORATS_SLIPPAGE_BY_LEGS[2]  # bull_put_spread = 2 legs = 0.66
        expected = ask - spread * slippage_pct
        result = _apply_slippage(
            mid, "bull_put_spread", "entry", slippage_model="orats", bid=bid, ask=ask,
        )
        assert abs(result - expected) < 1e-9

    def test_zero_mid_price_returns_zero(self):
        """A zero mid price should return zero without error."""
        result = _apply_slippage(0.0, "bull_put_spread", "entry")
        assert result == 0.0


# ── BacktestEngine slippage integration tests ─────────────────────────────────

def _make_minimal_engine():
    """Return a BacktestEngine with a mocked ORATS client."""
    with patch("backtesting.engine.BacktestEngine.__init__", return_value=None):
        engine = BacktestEngine.__new__(BacktestEngine)
    engine._orats = MagicMock()
    engine._slippage_model = "orats"
    engine._custom_slippage_pct = 0.0
    engine._total_slippage_cost = 0.0
    engine._symbol_prices = {}
    return engine


class TestBacktestEngineSlippageIntegration:
    """Integration-level tests for slippage applied during a full backtest run."""

    def _build_run_mocks(self, strategy: str = "bull_put_spread"):
        """Return (market_data, put_contract, long_contract) mocks for a 1-day backtest."""
        market_data = {
            "2024-01-02": {
                "vix": 14.0,
                "spy_close": 470.0,
                "above_sma_50": True,
                "above_sma_200": True,
                "regime": "BULL",
                "iv_env": "MODERATE",
            }
        }
        short_contract = {
            "strike": 450.0,
            "delta": -0.25,
            "mid_price": 2.00,
            "expiration_date": "2024-02-16",
            "dte": 28,
        }
        long_contract = {
            "strike": 445.0,
            "delta": -0.10,
            "mid_price": 0.80,
            "expiration_date": "2024-02-16",
            "dte": 28,
        }
        return market_data, short_contract, long_contract

    def _make_full_engine(self, strategy: str = "bull_put_spread") -> BacktestEngine:
        """Return a BacktestEngine wired for a 1-trade scenario."""
        engine = _make_minimal_engine()
        market_data, short_c, long_c = self._build_run_mocks(strategy)

        engine._orats.get_summary_on_date.return_value = {
            "iv_rank_1y": 45.0,
            "iv_current": 0.25,
        }
        engine._orats.get_strikes_on_date.side_effect = (
            lambda *a, **kw: [short_c] if kw.get("delta_min", 0) > 0.15 else [long_c]
        )
        engine._orats.get_cores_on_date.return_value = {}
        # No exit data → position force-closed at 0 (expires worthless)
        engine._orats.find_contract_on_date.return_value = None
        return engine

    def test_slippage_orats_reduces_credit_received(self):
        """A backtest with orats slippage should have lower P&L than one without."""
        params_no_slip = BacktestParams(
            strategy="bull_put_spread",
            symbols=["SPY"],
            start_date="2024-01-02",
            end_date="2024-01-02",
            slippage_model="none",
        )
        params_with_slip = BacktestParams(
            strategy="bull_put_spread",
            symbols=["SPY"],
            start_date="2024-01-02",
            end_date="2024-01-02",
            slippage_model="orats",
        )

        with (
            patch("backtesting.engine._load_market_data") as mock_mkt,
            patch("backtesting.engine._load_symbol_prices", return_value={}),
        ):
            mock_mkt.return_value = {
                "2024-01-02": {
                    "vix": 14.0,
                    "spy_close": 470.0,
                    "above_sma_50": True,
                    "above_sma_200": True,
                    "regime": "BULL",
                    "iv_env": "MODERATE",
                }
            }

            engine_no_slip = self._make_full_engine()
            engine_no_slip._slippage_model = "none"
            result_no_slip = engine_no_slip.run(params_no_slip)

            engine_with_slip = self._make_full_engine()
            result_with_slip = engine_with_slip.run(params_with_slip)

        assert result_no_slip.total_trades == result_with_slip.total_trades, (
            "Both runs should produce the same number of trades"
        )

        if result_no_slip.total_trades > 0:
            assert result_with_slip.total_pnl <= result_no_slip.total_pnl, (
                "Slippage should reduce total P&L"
            )
            assert result_with_slip.total_slippage_cost >= 0, (
                "Slippage cost should be non-negative"
            )
            assert result_with_slip.total_slippage_cost > 0, (
                "There should be some slippage cost with orats model"
            )
            assert result_no_slip.total_slippage_cost == 0.0, (
                "No slippage model should report zero slippage cost"
            )

    def test_total_slippage_cost_in_result(self):
        """BacktestResult.total_slippage_cost is accessible and numeric."""
        result = BacktestResult(params={}, trades=[])
        assert isinstance(result.total_slippage_cost, float)
        assert result.total_slippage_cost == 0.0

    def test_slippage_model_none_stored_in_result_params(self):
        """slippage_model field round-trips through BacktestParams -> asdict."""
        from dataclasses import asdict
        params = BacktestParams(slippage_model="none", custom_slippage_pct=0.0)
        d = asdict(params)
        assert d["slippage_model"] == "none"
        assert d["custom_slippage_pct"] == 0.0

    def test_iron_condor_lower_slippage_pct_than_csp(self):
        """Iron condor (4-leg, 53%) loses less per dollar of credit than CSP (1-leg, 75%)."""
        mid = 2.00
        csp_result = _apply_slippage(mid, "wheel_csp", "entry", slippage_model="orats")
        ic_result = _apply_slippage(mid, "iron_condor", "entry", slippage_model="orats")
        # CSP gets worse slippage (75%) so receives less credit
        assert csp_result < ic_result, (
            f"CSP (75%) should yield less than iron_condor (53%) when selling: "
            f"{csp_result} vs {ic_result}"
        )
