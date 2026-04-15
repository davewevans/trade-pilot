"""Tests for strategies.strategy_router.StrategyRouter."""

import pytest

from strategies.strategy_router import StrategyRouter


@pytest.fixture
def router():
    return StrategyRouter()


def _all_idle():
    return {
        "iron_condor": "IDLE",
        "bull_put_spread": "IDLE",
        "bear_call_spread": "IDLE",
        "long_call_vertical": "IDLE",
    }


def _context(**overrides):
    ctx = {
        "confirmed_market_regime": "NEUTRAL",
        "iv_environment": "MODERATE",
        "iv_rank": 55,  # >= 50 so iron_condor's iv_rank_min check passes in HIGH iv_env
        "support_bounce_signal": {"cahold_detected": False},
    }
    ctx.update(overrides)
    return ctx


# ================================================================
# Basic routing
# ================================================================


class TestBasicRouting:
    def test_wheel_always_included(self, router):
        active = router.get_active_strategies(
            _context(), _all_idle(), circuit_breaker_status="GREEN",
        )
        assert "wheel" in active

    def test_neutral_high_iv_selects_iron_condor(self, router):
        active = router.get_active_strategies(
            _context(confirmed_market_regime="NEUTRAL", iv_environment="HIGH"),
            _all_idle(),
            circuit_breaker_status="GREEN",
        )
        assert "iron_condor" in active

    def test_neutral_moderate_iv_selects_bull_put(self, router):
        """MODERATE IV + NEUTRAL: iron condor not eligible, bull_put_spread is."""
        active = router.get_active_strategies(
            _context(confirmed_market_regime="NEUTRAL", iv_environment="MODERATE"),
            _all_idle(),
            circuit_breaker_status="GREEN",
        )
        assert "bull_put_spread" in active
        assert "iron_condor" not in active

    def test_bear_high_iv_selects_bear_call(self, router):
        active = router.get_active_strategies(
            _context(confirmed_market_regime="BEAR", iv_environment="HIGH"),
            _all_idle(),
            circuit_breaker_status="GREEN",
        )
        # bear_call_spread is eligible; iron_condor needs NEUTRAL
        assert "bear_call_spread" in active

    def test_bull_low_iv_with_cahold_selects_long_call_vertical(self, router):
        active = router.get_active_strategies(
            _context(
                confirmed_market_regime="BULL",
                iv_environment="LOW",
                support_bounce_signal={"cahold_detected": True},
            ),
            _all_idle(),
            circuit_breaker_status="GREEN",
        )
        assert "long_call_vertical" in active

    def test_bull_low_iv_without_cahold_no_long_call(self, router):
        active = router.get_active_strategies(
            _context(
                confirmed_market_regime="BULL",
                iv_environment="LOW",
                support_bounce_signal={"cahold_detected": False},
            ),
            _all_idle(),
            circuit_breaker_status="GREEN",
        )
        assert "long_call_vertical" not in active


# ================================================================
# Priority order
# ================================================================


class TestPriority:
    def test_iron_condor_over_bull_put_when_both_eligible(self, router):
        """NEUTRAL + HIGH IV: both IC and BPS are eligible, IC wins."""
        active = router.get_active_strategies(
            _context(confirmed_market_regime="NEUTRAL", iv_environment="HIGH"),
            _all_idle(),
            circuit_breaker_status="GREEN",
        )
        # Only one spread should be added
        spread_active = [a for a in active if a != "wheel"]
        assert len(spread_active) == 1
        assert spread_active[0] == "iron_condor"

    def test_bull_put_over_bear_call_when_neutral_moderate(self, router):
        """NEUTRAL + MODERATE: both BPS and BCS eligible, BPS wins."""
        active = router.get_active_strategies(
            _context(confirmed_market_regime="NEUTRAL", iv_environment="MODERATE"),
            _all_idle(),
            circuit_breaker_status="GREEN",
        )
        spread_active = [a for a in active if a != "wheel"]
        assert len(spread_active) == 1
        assert spread_active[0] == "bull_put_spread"

    def test_only_one_idle_strategy_selected(self, router):
        """Even if multiple are eligible, only one is selected."""
        active = router.get_active_strategies(
            _context(confirmed_market_regime="NEUTRAL", iv_environment="HIGH"),
            _all_idle(),
            circuit_breaker_status="GREEN",
        )
        spread_active = [a for a in active if a != "wheel"]
        assert len(spread_active) == 1


# ================================================================
# Circuit breaker
# ================================================================


class TestCircuitBreaker:
    def test_red_returns_only_management(self, router):
        """RED: no new entries, only OPEN-state management + wheel."""
        states = _all_idle()
        states["iron_condor"] = "OPEN"  # needs management

        active = router.get_active_strategies(
            _context(), states, circuit_breaker_status="RED",
        )
        assert "wheel" in active
        assert "iron_condor" in active  # OPEN = needs management
        assert "bull_put_spread" not in active  # IDLE = blocked

    def test_red_all_idle_returns_wheel_only(self, router):
        active = router.get_active_strategies(
            _context(), _all_idle(), circuit_breaker_status="RED",
        )
        assert active == ["wheel"]

    def test_yellow_returns_management_only(self, router):
        """YELLOW: no new entries, management for OPEN states."""
        states = _all_idle()
        states["bull_put_spread"] = "OPEN"

        active = router.get_active_strategies(
            _context(confirmed_market_regime="NEUTRAL", iv_environment="HIGH"),
            states,
            circuit_breaker_status="YELLOW",
        )
        assert "wheel" in active
        assert "bull_put_spread" in active  # OPEN = management
        assert "iron_condor" not in active  # IDLE, blocked by YELLOW


# ================================================================
# CRASH regime
# ================================================================


class TestCrashRegime:
    def test_crash_returns_only_wheel(self, router):
        states = _all_idle()
        states["iron_condor"] = "OPEN"  # even open spreads skipped in crash

        active = router.get_active_strategies(
            _context(confirmed_market_regime="CRASH"),
            states,
            circuit_breaker_status="GREEN",
        )
        assert active == ["wheel"]


# ================================================================
# OPEN state management always runs
# ================================================================


class TestOpenStateManagement:
    def test_open_strategies_always_included(self, router):
        states = _all_idle()
        states["iron_condor"] = "OPEN"
        states["bear_call_spread"] = "OPEN"

        active = router.get_active_strategies(
            _context(confirmed_market_regime="NEUTRAL", iv_environment="MODERATE"),
            states,
            circuit_breaker_status="GREEN",
        )
        assert "iron_condor" in active
        assert "bear_call_spread" in active
        # Plus one idle candidate (bull_put_spread)
        assert "bull_put_spread" in active

    def test_open_plus_idle_candidate(self, router):
        """OPEN strategies + one new candidate allowed."""
        states = _all_idle()
        states["iron_condor"] = "OPEN"

        active = router.get_active_strategies(
            _context(confirmed_market_regime="NEUTRAL", iv_environment="MODERATE"),
            states,
            circuit_breaker_status="GREEN",
        )
        assert "iron_condor" in active  # management
        assert "bull_put_spread" in active  # new entry
        assert len([a for a in active if a != "wheel"]) == 2


# ================================================================
# Edge cases
# ================================================================


class TestEdgeCases:
    def test_no_spread_candidates_returns_wheel_only(self, router):
        """EUPHORIA regime: no spread strategy matches."""
        active = router.get_active_strategies(
            _context(confirmed_market_regime="EUPHORIA", iv_environment="MODERATE"),
            _all_idle(),
            circuit_breaker_status="GREEN",
        )
        assert active == ["wheel"]

    def test_empty_strategy_states(self, router):
        active = router.get_active_strategies(
            _context(), {}, circuit_breaker_status="GREEN",
        )
        assert "wheel" in active
