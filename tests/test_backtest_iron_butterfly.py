"""Tests for iron_butterfly strategy in the backtesting engine (Prompt 2)."""

import pytest
from unittest.mock import MagicMock, patch

from backtesting.engine import (
    BacktestEngine,
    BacktestParams,
    OpenPosition,
    SUPPORTED_STRATEGIES,
    _STRATEGY_LEG_COUNTS,
)


def _make_minimal_engine():
    """Return a BacktestEngine with mocked ORATS client."""
    with patch("backtesting.engine.BacktestEngine.__init__", return_value=None):
        engine = BacktestEngine.__new__(BacktestEngine)
    engine._orats = MagicMock()
    engine._slippage_model = "orats"
    engine._custom_slippage_pct = 0.0
    engine._total_slippage_cost = 0.0
    engine._symbol_prices = {}
    return engine


def _make_ib_position(**overrides) -> OpenPosition:
    """Build a minimal iron_butterfly OpenPosition for management tests."""
    defaults = dict(
        symbol="SPY",
        strategy="iron_butterfly",
        entry_date="2024-01-02",
        expiration_date="2024-02-16",
        short_strike=450.0,
        long_strike=445.0,
        short_strike_2=450.0,
        long_strike_2=455.0,
        option_type="put",
        option_type_2="call",
        entry_credit=4.40,
        entry_delta=0.50,
        entry_ivr=65.0,
        entry_regime="NEUTRAL",
        entry_iv_env="HIGH",
        entry_dte=45,
        contracts=1,
    )
    defaults.update(overrides)
    return OpenPosition(**defaults)


# ── Test 1: iron_butterfly in SUPPORTED_STRATEGIES ────────────────────────────

class TestIronButterflyRegistration:
    def test_in_supported_strategies(self):
        assert "iron_butterfly" in SUPPORTED_STRATEGIES

    def test_leg_count_is_4(self):
        assert _STRATEGY_LEG_COUNTS["iron_butterfly"] == 4


# ── Test 2: _regime_allows_entry ─────────────────────────────────────────────

class TestRegimeAllowsEntry:
    def test_neutral_high_allowed(self):
        assert BacktestEngine._regime_allows_entry("iron_butterfly", "NEUTRAL", "HIGH") is True

    def test_neutral_moderate_blocked(self):
        assert BacktestEngine._regime_allows_entry("iron_butterfly", "NEUTRAL", "MODERATE") is False

    def test_bull_high_blocked(self):
        assert BacktestEngine._regime_allows_entry("iron_butterfly", "BULL", "HIGH") is False

    def test_crash_blocked(self):
        assert BacktestEngine._regime_allows_entry("iron_butterfly", "CRASH", "HIGH") is False


# ── Test 3: _try_entry — skip when no put contracts ──────────────────────────

class TestIronButterflyEntry:
    def test_no_entry_when_no_put_contracts(self):
        engine = _make_minimal_engine()
        engine._orats.get_strikes_on_date.return_value = []
        engine._orats.get_cores_on_date.return_value = {}

        params = BacktestParams(
            strategy="iron_butterfly",
            symbols=["SPY"],
            start_date="2024-01-02",
            end_date="2024-01-02",
            dte_min=21,
            dte_max=45,
            spread_width_strikes=5,
        )
        summary = {"iv_rank_1y": 65.0, "stockPrice": 450.0}
        result = engine._try_entry(params, "SPY", "2024-01-02", summary, "NEUTRAL", "HIGH")
        assert result is None

    def test_entry_rejected_when_net_credit_below_1_dollar(self):
        """Net credit after slippage must be > $1.00 — low credit should be rejected."""
        engine = _make_minimal_engine()
        engine._orats.get_cores_on_date.return_value = {}

        # ATM contracts priced cheaply so combined credit is < $1
        atm_contract = {"strike": 450.0, "delta": -0.50, "mid_price": 0.40, "expiration_date": "2024-02-16", "dte": 28}
        call_contract = {"strike": 450.0, "delta": 0.50, "mid_price": 0.40, "expiration_date": "2024-02-16", "dte": 28}
        wing_contract = {"strike": 445.0, "delta": -0.20, "mid_price": 0.30, "expiration_date": "2024-02-16", "dte": 28}
        call_wing = {"strike": 455.0, "delta": 0.20, "mid_price": 0.30, "expiration_date": "2024-02-16", "dte": 28}

        def _strikes(**kwargs):
            opt = kwargs.get("option_type", "put")
            d_min = kwargs.get("delta_min", 0)
            d_max = kwargs.get("delta_max", 1)
            if opt == "put" and d_min >= 0.40:
                return [atm_contract]
            if opt == "call" and d_min >= 0.35:
                return [call_contract]
            if opt == "put":
                return [wing_contract]
            return [call_wing]

        engine._orats.get_strikes_on_date.side_effect = lambda *a, **kw: _strikes(**kw)

        params = BacktestParams(
            strategy="iron_butterfly",
            symbols=["SPY"],
            start_date="2024-01-02",
            end_date="2024-01-02",
            dte_min=21,
            dte_max=45,
            spread_width_strikes=5,
        )
        summary = {"iv_rank_1y": 65.0, "stockPrice": 450.0}
        result = engine._try_entry(params, "SPY", "2024-01-02", summary, "NEUTRAL", "HIGH")
        # (0.40 + 0.40) - (0.30 + 0.30) = 0.20 → after slippage well below $1.00
        assert result is None

    def test_valid_entry_creates_open_position(self):
        """A valid iron butterfly entry with sufficient credit should return OpenPosition."""
        engine = _make_minimal_engine()
        engine._orats.get_cores_on_date.return_value = {}

        atm_put = {"strike": 450.0, "delta": -0.50, "mid_price": 5.00, "expiration_date": "2024-02-16", "dte": 28}
        atm_call = {"strike": 450.0, "delta": 0.50, "mid_price": 5.00, "expiration_date": "2024-02-16", "dte": 28}
        put_wing = {"strike": 445.0, "delta": -0.20, "mid_price": 1.50, "expiration_date": "2024-02-16", "dte": 28}
        call_wing = {"strike": 455.0, "delta": 0.20, "mid_price": 1.50, "expiration_date": "2024-02-16", "dte": 28}

        def _strikes(**kwargs):
            opt = kwargs.get("option_type", "put")
            d_min = kwargs.get("delta_min", 0)
            if opt == "put" and d_min >= 0.40:
                return [atm_put]
            if opt == "call" and d_min >= 0.35:
                return [atm_call]
            if opt == "put":
                return [put_wing]
            return [call_wing]

        engine._orats.get_strikes_on_date.side_effect = lambda *a, **kw: _strikes(**kw)

        params = BacktestParams(
            strategy="iron_butterfly",
            symbols=["SPY"],
            start_date="2024-01-02",
            end_date="2024-01-02",
            dte_min=21,
            dte_max=45,
            spread_width_strikes=5,
        )
        summary = {"iv_rank_1y": 65.0, "stockPrice": 450.0}
        pos = engine._try_entry(params, "SPY", "2024-01-02", summary, "NEUTRAL", "HIGH")

        assert pos is not None
        assert pos.strategy == "iron_butterfly"
        assert pos.short_strike == 450.0
        assert pos.long_strike == 445.0
        assert pos.short_strike_2 == 450.0
        assert pos.long_strike_2 == 455.0
        assert pos.option_type == "put"
        assert pos.option_type_2 == "call"
        # raw = (5.00 + 5.00) - (1.50 + 1.50) = 7.00 → after slippage still > $1.00
        assert pos.entry_credit > 1.00


# ── Test 4: _compute_ib_combined_exit ────────────────────────────────────────

class TestComputeIbCombinedExit:
    def test_returns_none_when_put_short_missing(self):
        engine = _make_minimal_engine()
        engine._orats.find_contract_on_date.return_value = None
        pos = _make_ib_position()
        result = engine._compute_ib_combined_exit(pos, "2024-01-10")
        assert result is None

    def test_combined_exit_calculation(self):
        """(put_short + call_short) - (put_long + call_long) = (2.0 + 2.0) - (0.5 + 0.5) = 3.0."""
        engine = _make_minimal_engine()
        pos = _make_ib_position()

        def _find(symbol, trade_date, strike, expiry, opt_type):
            prices = {
                ("put", 450.0): 2.0,
                ("call", 450.0): 2.0,
                ("put", 445.0): 0.5,
                ("call", 455.0): 0.5,
            }
            price = prices.get((opt_type, strike))
            if price is None:
                return None
            return {"mid_price": price}

        engine._orats.find_contract_on_date.side_effect = _find
        result = engine._compute_ib_combined_exit(pos, "2024-01-10")
        assert result == pytest.approx(3.0, abs=0.01)


# ── Test 5: _manage_position — profit target ─────────────────────────────────

class TestIronButterflyManagement:
    def test_profit_target_at_50_pct_of_credit(self):
        """When combined exit is ≤ 50% of entry credit, close at profit_target."""
        engine = _make_minimal_engine()
        # entry_credit=4.40 → target ≤ 2.20
        pos = _make_ib_position(entry_credit=4.40, entry_date="2024-01-02")

        def _find(symbol, trade_date, strike, expiry, opt_type):
            # Combined = (1.0 + 1.0) - (0.1 + 0.1) = 1.80 (< 2.20)
            prices = {
                ("put", 450.0): 1.0,
                ("call", 450.0): 1.0,
                ("put", 445.0): 0.1,
                ("call", 455.0): 0.1,
            }
            price = prices.get((opt_type, strike))
            return {"mid_price": price} if price is not None else None

        engine._orats.find_contract_on_date.side_effect = _find

        day_info = {"regime": "NEUTRAL", "iv_env": "HIGH"}
        # Use a date 3 days after entry so %3 == 0
        trade = engine._manage_position(pos, "2024-01-05", day_info)

        assert trade is not None
        assert trade.exit_reason == "profit_target"

    def test_dte_expiry_uses_combined_exit(self):
        """When DTE ≤ 7, iron_butterfly uses _compute_ib_combined_exit."""
        engine = _make_minimal_engine()
        pos = _make_ib_position(
            entry_date="2024-01-02",
            expiration_date="2024-01-10",  # DTE from Jan 3 = 7
        )

        def _find(symbol, trade_date, strike, expiry, opt_type):
            prices = {
                ("put", 450.0): 0.5,
                ("call", 450.0): 0.5,
                ("put", 445.0): 0.05,
                ("call", 455.0): 0.05,
            }
            price = prices.get((opt_type, strike))
            return {"mid_price": price} if price is not None else None

        engine._orats.find_contract_on_date.side_effect = _find

        day_info = {"regime": "NEUTRAL", "iv_env": "HIGH"}
        trade = engine._manage_position(pos, "2024-01-03", day_info)

        assert trade is not None
        assert trade.exit_reason == "dte_expired"
