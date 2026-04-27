"""Tests for calendar_spread strategy in the backtesting engine (Prompt 5)."""

import pytest
from unittest.mock import MagicMock, patch
from datetime import date

from backtesting.engine import (
    BacktestEngine,
    BacktestParams,
    OpenPosition,
    SUPPORTED_STRATEGIES,
    _STRATEGY_LEG_COUNTS,
    _apply_slippage,
    _pick_closest_delta,
)


def _make_minimal_engine(slippage_model="none"):
    """Return a BacktestEngine with mocked ORATS client."""
    with patch("backtesting.engine.BacktestEngine.__init__", return_value=None):
        engine = BacktestEngine.__new__(BacktestEngine)
    engine._orats = MagicMock()
    engine._slippage_model = slippage_model
    engine._custom_slippage_pct = 0.0
    engine._total_slippage_cost = 0.0
    engine._symbol_prices = {}
    return engine


def _make_calendar_position(**overrides) -> OpenPosition:
    """Build a minimal calendar_spread OpenPosition for management tests."""
    defaults = dict(
        symbol="SPY",
        strategy="calendar_spread",
        entry_date="2024-01-02",
        expiration_date="2024-02-16",   # short leg
        long_expiration_date="2024-04-19",  # long leg
        short_strike=450.0,
        long_strike=None,
        short_strike_2=None,
        long_strike_2=None,
        option_type="put",
        option_type_2=None,
        entry_credit=-1.60,             # debit stored as negative
        entry_delta=0.50,
        entry_ivr=30.0,
        entry_regime="NEUTRAL",
        entry_iv_env="LOW",
        entry_dte=28,
        contracts=1,
    )
    defaults.update(overrides)
    return OpenPosition(**defaults)


# ── Test 1: Registration ──────────────────────────────────────────────────────

class TestCalendarSpreadRegistration:
    def test_calendar_spread_in_supported_strategies(self):
        assert "calendar_spread" in SUPPORTED_STRATEGIES

    def test_calendar_spread_leg_count_2(self):
        assert _STRATEGY_LEG_COUNTS["calendar_spread"] == 2


# ── Test 2: Debit strategy in slippage ───────────────────────────────────────

def test_calendar_spread_is_debit_strategy_in_slippage():
    """At entry calendar_spread buys, so slippage makes net cost higher than mid."""
    mid = 1.50
    slipped = _apply_slippage(mid, "calendar_spread", "entry", "orats", 0.0)
    # Debit + buy at entry → pay more than mid
    assert slipped > mid


# ── Test 3: _regime_allows_entry ─────────────────────────────────────────────

class TestCalendarRegimeAllowsEntry:
    @pytest.mark.parametrize("regime,iv_env,expected", [
        ("NEUTRAL", "LOW", True),
        ("NEUTRAL", "MODERATE", True),
        ("BULL", "LOW", True),
        ("BULL", "MODERATE", True),
        ("NEUTRAL", "HIGH", False),
        ("BULL", "HIGH", False),
        ("BEAR", "LOW", False),
        ("BEAR", "MODERATE", False),
        ("CRASH", "LOW", False),
    ])
    def test_regime_allows_entry_neutral_low_or_moderate(self, regime, iv_env, expected):
        assert BacktestEngine._regime_allows_entry("calendar_spread", regime, iv_env) is expected


# ── Test 4: Entry picks ATM strike ───────────────────────────────────────────

def test_calendar_entry_picks_atm_strike():
    """Entry selects the short leg with delta closest to -0.50 (ATM)."""
    engine = _make_minimal_engine()

    atm = {"strike": 450.0, "delta": -0.50, "mid_price": 1.20,
           "expiration_date": "2024-02-16", "dte": 28}
    otm = {"strike": 440.0, "delta": -0.25, "mid_price": 0.40,
           "expiration_date": "2024-02-16", "dte": 28}
    long_leg = {"strike": 450.0, "delta": -0.50, "mid_price": 2.80,
                "expiration_date": "2024-04-19", "dte": 75}

    def _strikes_side_effect(*args, **kwargs):
        dte_min = kwargs.get("dte_min", 0)
        # Long leg query uses dte_min=50
        if dte_min >= 50:
            return [long_leg]
        return [otm, atm]

    engine._orats.get_strikes_on_date.side_effect = _strikes_side_effect
    engine._orats.get_cores_on_date.return_value = {}

    params = BacktestParams(
        strategy="calendar_spread",
        symbols=["SPY"],
        start_date="2024-01-02",
        end_date="2024-01-02",
        dte_min=21,
        dte_max=45,
    )
    summary = {"iv_rank_1y": 30.0}
    pos = engine._try_entry(params, "SPY", "2024-01-02", summary, "NEUTRAL", "LOW")

    assert pos is not None
    assert pos.short_strike == 450.0   # ATM, not OTM


# ── Test 5: Long leg must be ≥ 30 days after short ───────────────────────────

def test_calendar_long_leg_at_least_30_days_after_short():
    """Long leg candidates that are < 30 days after short expiry are rejected."""
    engine = _make_minimal_engine()

    short_leg = {"strike": 450.0, "delta": -0.50, "mid_price": 1.20,
                 "expiration_date": "2024-02-16", "dte": 28}
    # Only candidate: 25 days after short leg (short=Feb 16, long=Mar 12)
    near_long = {"strike": 450.0, "delta": -0.50, "mid_price": 1.80,
                 "expiration_date": "2024-03-12", "dte": 61}

    def _strikes_side_effect(*args, **kwargs):
        dte_min = kwargs.get("dte_min", 0)
        if dte_min >= 50:
            return [near_long]
        return [short_leg]

    engine._orats.get_strikes_on_date.side_effect = _strikes_side_effect
    engine._orats.get_cores_on_date.return_value = {}

    params = BacktestParams(
        strategy="calendar_spread",
        symbols=["SPY"],
        start_date="2024-01-02",
        end_date="2024-01-02",
        dte_min=21,
        dte_max=45,
    )
    # Mar 12 - Feb 16 = 25 days < 30 → should be rejected
    assert (date(2024, 3, 12) - date(2024, 2, 16)).days == 25
    summary = {"iv_rank_1y": 30.0}
    pos = engine._try_entry(params, "SPY", "2024-01-02", summary, "NEUTRAL", "LOW")
    assert pos is None


# ── Test 6: Debit floor rejects > $2.50 ──────────────────────────────────────

def test_calendar_debit_floor_rejects_above_2_50():
    """A candidate whose net debit exceeds $2.50 (after slippage) is rejected."""
    engine = _make_minimal_engine(slippage_model="none")  # no slippage to isolate floor

    short_leg = {"strike": 450.0, "delta": -0.50, "mid_price": 0.40,
                 "expiration_date": "2024-02-16", "dte": 28}
    # raw debit = 3.00 - 0.40 = 2.60 → exceeds $2.50
    long_leg = {"strike": 450.0, "delta": -0.50, "mid_price": 3.00,
                "expiration_date": "2024-04-19", "dte": 75}

    def _strikes_side_effect(*args, **kwargs):
        dte_min = kwargs.get("dte_min", 0)
        if dte_min >= 50:
            return [long_leg]
        return [short_leg]

    engine._orats.get_strikes_on_date.side_effect = _strikes_side_effect
    engine._orats.get_cores_on_date.return_value = {}

    params = BacktestParams(
        strategy="calendar_spread",
        symbols=["SPY"],
        start_date="2024-01-02",
        end_date="2024-01-02",
        dte_min=21,
        dte_max=45,
    )
    summary = {"iv_rank_1y": 30.0}
    pos = engine._try_entry(params, "SPY", "2024-01-02", summary, "NEUTRAL", "LOW")
    assert pos is None


# ── Test 7: Exit at 50% profit ───────────────────────────────────────────────

def test_calendar_exit_at_50_pct_profit():
    """When spread value rises 50% above entry debit, close at profit_target."""
    engine = _make_minimal_engine(slippage_model="none")

    # entry_credit=-1.60 means debit paid = 1.60
    pos = _make_calendar_position(entry_credit=-1.60, entry_date="2024-01-02")

    # 60% gain: current_val = 1.60 * 1.60 = 2.56 (well above the 50% target)
    # long_mid=3.36, short_mid=0.80 → 3.36 - 0.80 = 2.56
    def _find(symbol, trade_date, strike, expiry, opt_type):
        if expiry == "2024-02-16":
            return {"mid_price": 0.80}
        if expiry == "2024-04-19":
            return {"mid_price": 3.36}
        return None

    engine._orats.find_contract_on_date.side_effect = _find

    # Use a date 3 days after entry so the periodic check triggers (% 3 == 0)
    trade = engine._manage_position(pos, "2024-01-05", {"regime": "NEUTRAL", "iv_env": "LOW"})

    assert trade is not None
    assert trade.exit_reason == "profit_target"
    # P&L: exit_val(2.56) + entry_credit(-1.60) = 0.96 × 100 = $96
    assert trade.pnl == pytest.approx(96.0, abs=0.01)


# ── Test 8: Exit at 50% loss ─────────────────────────────────────────────────

def test_calendar_exit_at_50_pct_loss():
    """When spread value falls 50% below entry debit, close at stop_loss."""
    engine = _make_minimal_engine(slippage_model="none")

    pos = _make_calendar_position(entry_credit=-2.00, entry_date="2024-01-02")

    # 50% loss: need slipped_val <= 2.00 * 0.50 = 1.00
    # long_mid=2.50, short_mid=1.50 → 2.50 - 1.50 = 1.00
    def _find(symbol, trade_date, strike, expiry, opt_type):
        if expiry == "2024-02-16":
            return {"mid_price": 1.50}
        if expiry == "2024-04-19":
            return {"mid_price": 2.50}
        return None

    engine._orats.find_contract_on_date.side_effect = _find

    trade = engine._manage_position(pos, "2024-01-05", {"regime": "NEUTRAL", "iv_env": "LOW"})

    assert trade is not None
    assert trade.exit_reason == "stop_loss"
    # P&L: exit_val(1.00) + entry_credit(-2.00) = -1.00 × 100 = -$100
    assert trade.pnl == pytest.approx(-100.0, abs=0.01)


# ── Test 9: Exit at short DTE ≤ 7 ────────────────────────────────────────────

def test_calendar_exit_at_short_dte_7():
    """When short leg has 7 DTE, close at dte_expired using current spread value."""
    engine = _make_minimal_engine(slippage_model="none")

    # expiration_date is the short leg's expiry; DTE = (Jan 9 - Jan 2) = 7
    pos = _make_calendar_position(
        entry_credit=-1.60,
        entry_date="2024-01-02",
        expiration_date="2024-01-09",
        long_expiration_date="2024-03-15",
    )

    def _find(symbol, trade_date, strike, expiry, opt_type):
        if expiry == "2024-01-09":
            return {"mid_price": 0.50}
        if expiry == "2024-03-15":
            return {"mid_price": 2.00}
        return None

    engine._orats.find_contract_on_date.side_effect = _find

    trade = engine._manage_position(pos, "2024-01-02", {"regime": "NEUTRAL", "iv_env": "LOW"})

    assert trade is not None
    assert trade.exit_reason == "dte_expired"


# ── Test 10: ivr_max gates calendar entries ───────────────────────────────────

def test_calendar_skips_when_iv_rank_above_ivr_max():
    """When iv_rank > ivr_max, no entry should be attempted for calendar_spread."""
    engine = _make_minimal_engine()
    engine._orats.get_summary_on_date.return_value = {"iv_rank_1y": 70.0}
    engine._orats.get_strikes_on_date.return_value = []
    engine._orats.get_cores_on_date.return_value = {}

    params = BacktestParams(
        strategy="calendar_spread",
        symbols=["SPY"],
        start_date="2024-01-02",
        end_date="2024-01-02",
        ivr_threshold=0.0,
        ivr_max=50.0,
    )

    market_data = {
        "2024-01-02": {
            "vix": 12.0, "spy_close": 470.0,
            "above_sma_50": True, "above_sma_200": True,
            "regime": "NEUTRAL", "iv_env": "LOW",
        }
    }

    with (
        patch("backtesting.engine._load_market_data", return_value=market_data),
        patch("backtesting.engine._load_symbol_prices", return_value={}),
    ):
        result = engine.run(params)

    assert result.total_trades == 0


# ── Test 11: Earnings between expirations → skip ──────────────────────────────

def test_calendar_earnings_between_expirations_skipped():
    """Earnings falling between short and long expiry causes entry to be skipped."""
    engine = _make_minimal_engine()

    # short expiry Jan 22, long expiry Mar 15 → earnings Jan 25 falls in between
    short_leg = {"strike": 450.0, "delta": -0.50, "mid_price": 1.20,
                 "expiration_date": "2024-01-22", "dte": 20}
    long_leg = {"strike": 450.0, "delta": -0.50, "mid_price": 2.80,
                "expiration_date": "2024-03-15", "dte": 72}

    def _strikes_side_effect(*args, **kwargs):
        dte_min = kwargs.get("dte_min", 0)
        if dte_min >= 50:
            return [long_leg]
        return [short_leg]

    engine._orats.get_strikes_on_date.side_effect = _strikes_side_effect
    engine._orats.get_cores_on_date.return_value = {"earn_date": "2024-01-25"}

    params = BacktestParams(
        strategy="calendar_spread",
        symbols=["SPY"],
        start_date="2024-01-02",
        end_date="2024-01-02",
        dte_min=21,
        dte_max=45,
    )
    summary = {"iv_rank_1y": 30.0}
    pos = engine._try_entry(params, "SPY", "2024-01-02", summary, "NEUTRAL", "LOW")
    assert pos is None


# ── Boundary: ATM tie-breaker picks lower strike ──────────────────────────────

def test_calendar_atm_tie_breaker_picks_lower_strike():
    """When underlying is exactly between two strikes, pick the lower one.

    _pick_closest_delta uses strict-less-than update, so the first contract
    in the list wins in a tie. Calendar passes lower-strike contracts first
    because ORATS returns chains in ascending strike order.
    """
    # Both contracts equidistant from -0.50 delta
    lower = {"strike": 448.0, "delta": -0.48, "mid_price": 1.10,
             "expiration_date": "2024-02-16", "dte": 28}
    higher = {"strike": 452.0, "delta": -0.52, "mid_price": 1.30,
              "expiration_date": "2024-02-16", "dte": 28}

    # lower listed first → tie goes to lower strike
    result = _pick_closest_delta([lower, higher], -0.50)
    assert result is not None
    assert result["strike"] == 448.0
