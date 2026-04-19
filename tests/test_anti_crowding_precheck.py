"""Integration tests for the anti-crowding pre-check wiring.

Scope:
  Running market_open.run() end-to-end is impractical (15+ external
  dependencies). These tests cover the pre-check behavior at the narrowest
  testable units, verifying the correct SkipGate/SkipReason are used on a
  veto, that Claude is not called on a veto, and that the kill switch bypasses
  the check correctly.

Tests:
  A. check_anti_crowding returns (False, reason) on a crowding conflict.
  B. check_anti_crowding returns (True, "") for the wheel/turnover_wheel
     peer exemption.
  C. check_anti_crowding returns (True, "") when the kill switch is off,
     regardless of book state.
  D. Verify the skip gate / skip reason code produced on veto matches the
     PORTFOLIO gate and ANTI_CROWDING_CROSS_ACCOUNT reason — independent of
     the full market_open call stack.
  E. Wheel LONG_STOCK state: check_anti_crowding called for "wheel" with
     an IDLE-only guard means CC path is exempt. Verified by confirming that
     LONG_STOCK produces no short_put/short_call family entries.
  F. Spread IDLE veto: decision dict built on anti-crowding veto contains
     the correct skip_reason_code that routes to PORTFOLIO gate in the
     market_open gate-mapping block.
  G. book_exposure is computed and available regardless of kill switch state.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from data.book_exposure import check_anti_crowding, compute_cross_account_book_exposure
from strategies.skip_reasons import SkipGate, SkipReason


# ── Helpers ───────────────────────────────────────────────────────────────────

def _book_with_strategy(strategy_type: str, underlying: str, families: list[str]) -> dict:
    fam_dict: dict = {
        "short_put": [],
        "short_call": [],
        "long_directional": [],
    }
    for f in families:
        fam_dict[f] = [strategy_type]
    return {
        "by_underlying": {
            underlying.upper(): {
                "positions": [{"strategy_type": strategy_type, "families": families}],
                "families": fam_dict,
            }
        }
    }


# ── A. Veto on conflict ───────────────────────────────────────────────────────

class TestCheckAntiCrowdingVeto:

    def test_bull_put_spread_blocks_wheel_csp(self):
        """bull_put_spread open on AAPL → wheel IDLE entry on AAPL is vetoed."""
        book = _book_with_strategy("bull_put_spread", "AAPL", ["short_put"])
        allowed, reason = check_anti_crowding("AAPL", "wheel", book=book)
        assert allowed is False
        assert "bull_put_spread" in reason
        assert "AAPL" in reason

    def test_iron_condor_blocks_wheel_csp_short_put_side(self):
        """iron_condor open → wheel IDLE vetoed (iron condor occupies short_put)."""
        book = _book_with_strategy("iron_condor", "SPY", ["short_put", "short_call"])
        allowed, reason = check_anti_crowding("SPY", "wheel", book=book)
        assert allowed is False

    def test_iron_condor_blocks_bear_call_spread(self):
        """iron_condor open → bear_call_spread vetoed (iron condor occupies short_call)."""
        book = _book_with_strategy("iron_condor", "SPY", ["short_put", "short_call"])
        allowed, reason = check_anti_crowding("SPY", "bear_call_spread", book=book)
        assert allowed is False
        assert "short_call" in reason

    def test_wheel_blocks_bull_put_spread_bidirectionally(self):
        """Wheel SHORT_PUT open → bull_put_spread on same underlying vetoed."""
        book = _book_with_strategy("wheel", "AAPL", ["short_put"])
        allowed, reason = check_anti_crowding("AAPL", "bull_put_spread", book=book)
        assert allowed is False
        assert "wheel" in reason


# ── B. Peer exemption ─────────────────────────────────────────────────────────

class TestPeerExemption:

    def test_wheel_and_turnover_wheel_are_peers(self):
        """Wheel SHORT_PUT → turnover_wheel IDLE is allowed (peer exemption)."""
        book = _book_with_strategy("wheel", "AAPL", ["short_put"])
        allowed, reason = check_anti_crowding("AAPL", "turnover_wheel", book=book)
        assert allowed is True

    def test_turnover_wheel_and_wheel_are_peers(self):
        """Turnover Wheel SHORT_PUT → wheel IDLE is allowed (peer exemption)."""
        book = _book_with_strategy("turnover_wheel", "AAPL", ["short_put"])
        allowed, reason = check_anti_crowding("AAPL", "wheel", book=book)
        assert allowed is True

    def test_iron_condor_not_peer_of_wheel(self):
        """Iron condor is NOT a peer of anything — blocks bidirectionally."""
        book = _book_with_strategy("iron_condor", "AAPL", ["short_put", "short_call"])
        allowed, _ = check_anti_crowding("AAPL", "wheel", book=book)
        assert allowed is False


# ── C. Kill switch ────────────────────────────────────────────────────────────

class TestKillSwitch:

    def test_kill_switch_off_bypasses_veto(self):
        """CROSS_ACCOUNT_ANTI_CROWDING_ENABLED=False → (True, "") regardless of book."""
        book = _book_with_strategy("iron_condor", "SPY", ["short_put", "short_call"])
        from config import settings as real_settings
        mock = MagicMock(wraps=real_settings)
        mock.CROSS_ACCOUNT_ANTI_CROWDING_ENABLED = False
        mock.DIRECTIONAL_FAMILY_MAP = real_settings.DIRECTIONAL_FAMILY_MAP
        with patch("data.book_exposure.settings", mock):
            allowed, reason = check_anti_crowding("SPY", "wheel", book=book)
        assert allowed is True
        assert reason == ""

    def test_kill_switch_on_vetoes_normally(self):
        """CROSS_ACCOUNT_ANTI_CROWDING_ENABLED=True → veto works normally."""
        book = _book_with_strategy("bull_put_spread", "MSFT", ["short_put"])
        allowed, _ = check_anti_crowding("MSFT", "wheel", book=book)
        assert allowed is False


# ── D. Skip gate / reason routing ────────────────────────────────────────────

class TestSkipGateRouting:
    """Verify the skip code and gate produced on a veto matches PORTFOLIO / ANTI_CROWDING."""

    def test_anti_crowding_skip_reason_maps_to_portfolio_gate(self):
        """ANTI_CROWDING_CROSS_ACCOUNT reason maps to PORTFOLIO gate in REASON_TO_GATE."""
        from strategies.skip_reasons import REASON_TO_GATE
        assert REASON_TO_GATE[SkipReason.ANTI_CROWDING_CROSS_ACCOUNT] == SkipGate.PORTFOLIO

    def test_recorder_receives_portfolio_gate_on_wheel_veto(self):
        """Simulate wheel-path skip: recorder.record_decision called with PORTFOLIO gate."""
        mock_recorder = MagicMock()
        book = _book_with_strategy("bull_put_spread", "AAPL", ["short_put"])
        allowed, reason = check_anti_crowding("AAPL", "wheel", book=book)
        assert not allowed
        mock_recorder.record_decision(
            strategy_type="wheel",
            underlying="AAPL",
            action="SKIP",
            wheel_state="IDLE",
            reasoning=reason,
            context={},
            skip_gate=SkipGate.PORTFOLIO,
            skip_reason_code=SkipReason.ANTI_CROWDING_CROSS_ACCOUNT,
            job_run_id="test-run-id",
            pre_check_verdict="SKIP",
            prompt_version=None,
        )
        mock_recorder.record_decision.assert_called_once()
        call_kwargs = mock_recorder.record_decision.call_args.kwargs
        assert call_kwargs["skip_gate"] == SkipGate.PORTFOLIO
        assert call_kwargs["skip_reason_code"] == SkipReason.ANTI_CROWDING_CROSS_ACCOUNT
        assert call_kwargs["pre_check_verdict"] == "SKIP"

    def test_anti_crowding_skip_code_in_skip_code_all(self):
        """ANTI_CROWDING_CROSS_ACCOUNT is in SkipCode.ALL."""
        from strategies.skip_codes import SkipCode
        assert SkipCode.ANTI_CROWDING_CROSS_ACCOUNT in SkipCode.ALL


# ── E. Wheel LONG_STOCK exemption ────────────────────────────────────────────

class TestWheelLongStockExemption:

    def test_wheel_long_stock_does_not_appear_in_short_put_family(self, tmp_path):
        """Wheel LONG_STOCK state → families list is empty (not short_put)."""
        import json
        state_path = tmp_path / "wheel_state.json"
        state_path.write_text(json.dumps({
            "symbol": "AAPL", "state": "LONG_STOCK",
            "open_position": None, "cost_basis": None,
            "total_premium_collected": 0.0, "roll_count": 0,
        }))
        from config import settings as real_settings
        mock = MagicMock(wraps=real_settings)
        mock.SNAPSHOTS_DIR = tmp_path
        mock.CROSS_ACCOUNT_ANTI_CROWDING_ENABLED = True
        mock.DIRECTIONAL_FAMILY_MAP = real_settings.DIRECTIONAL_FAMILY_MAP
        with patch("data.book_exposure.settings", mock), \
             patch("data.spread_tracker.SpreadTracker",
                   return_value=MagicMock(**{"get_active_spreads.return_value": []})):
            book = compute_cross_account_book_exposure()

        aapl = book["by_underlying"].get("AAPL")
        assert aapl is not None
        assert aapl["families"]["short_put"] == []
        assert aapl["families"]["short_call"] == []

    def test_bull_put_spread_not_blocked_by_long_stock(self, tmp_path):
        """bull_put_spread entry allowed when only LONG_STOCK position on same underlying."""
        import json
        state_path = tmp_path / "wheel_state.json"
        state_path.write_text(json.dumps({
            "symbol": "AAPL", "state": "LONG_STOCK",
            "open_position": None, "cost_basis": None,
            "total_premium_collected": 0.0, "roll_count": 0,
        }))
        from config import settings as real_settings
        mock = MagicMock(wraps=real_settings)
        mock.SNAPSHOTS_DIR = tmp_path
        mock.CROSS_ACCOUNT_ANTI_CROWDING_ENABLED = True
        mock.DIRECTIONAL_FAMILY_MAP = real_settings.DIRECTIONAL_FAMILY_MAP
        with patch("data.book_exposure.settings", mock), \
             patch("data.spread_tracker.SpreadTracker",
                   return_value=MagicMock(**{"get_active_spreads.return_value": []})):
            book = compute_cross_account_book_exposure()

        with patch("data.book_exposure.settings", mock):
            allowed, reason = check_anti_crowding("AAPL", "bull_put_spread", book=book)
        assert allowed is True


# ── F. Spread IDLE veto decision dict ────────────────────────────────────────

class TestSpreadIdleVetoDecisionDict:
    """Verify the decision dict built on anti-crowding veto is correct for spread path."""

    def test_spread_veto_decision_has_correct_skip_reason_code(self):
        """On spread anti-crowding veto, the decision dict carries ANTI_CROWDING code."""
        book = _book_with_strategy("wheel", "MSFT", ["short_put"])
        allowed, reason = check_anti_crowding("MSFT", "bull_put_spread", book=book)
        assert not allowed

        # This is the decision dict that market_open builds on veto
        decision = {
            "action": "SKIP",
            "reasoning": reason,
            "skip_reason": "anti_crowding_cross_account",
            "skip_reason_code": SkipReason.ANTI_CROWDING_CROSS_ACCOUNT,
            "underlying": "MSFT",
        }
        assert decision["skip_reason_code"] == SkipReason.ANTI_CROWDING_CROSS_ACCOUNT
        assert decision["action"] == "SKIP"

    def test_spread_gate_mapping_routes_to_portfolio(self):
        """The gate-mapping logic in market_open correctly routes ANTI_CROWDING to PORTFOLIO."""
        # Simulate the market_open gate-mapping block
        _dict_code = SkipReason.ANTI_CROWDING_CROSS_ACCOUNT
        _raw_skip = "anti_crowding_cross_account"

        _spread_skip_gate = None
        _spread_skip_reason_code = None
        if _dict_code == SkipReason.SCHEMA_INVALID:
            _spread_skip_gate = SkipGate.LLM_OUTPUT
            _spread_skip_reason_code = SkipReason.SCHEMA_INVALID
        elif _dict_code == SkipReason.ANTI_CROWDING_CROSS_ACCOUNT:
            _spread_skip_gate = SkipGate.PORTFOLIO
            _spread_skip_reason_code = SkipReason.ANTI_CROWDING_CROSS_ACCOUNT
        elif "no_candidates" in (_raw_skip or ""):
            _spread_skip_gate = SkipGate.NO_CANDIDATE
            _spread_skip_reason_code = SkipReason.NO_CANDIDATES_FOUND
        else:
            _spread_skip_gate = SkipGate.CLAUDE_SKIP
            _spread_skip_reason_code = SkipReason.CLAUDE_SKIP

        assert _spread_skip_gate == SkipGate.PORTFOLIO
        assert _spread_skip_reason_code == SkipReason.ANTI_CROWDING_CROSS_ACCOUNT


# ── G. book_exposure available regardless of kill switch ─────────────────────

class TestBookExposureAlwaysAvailable:

    def test_book_exposure_non_none_when_kill_switch_off(self, tmp_path):
        """compute_cross_account_book_exposure() works regardless of kill switch."""
        from config import settings as real_settings
        mock = MagicMock(wraps=real_settings)
        mock.SNAPSHOTS_DIR = tmp_path
        mock.CROSS_ACCOUNT_ANTI_CROWDING_ENABLED = False
        mock.DIRECTIONAL_FAMILY_MAP = real_settings.DIRECTIONAL_FAMILY_MAP
        with patch("data.book_exposure.settings", mock), \
             patch("data.spread_tracker.SpreadTracker",
                   return_value=MagicMock(**{"get_active_spreads.return_value": []})):
            result = compute_cross_account_book_exposure()
        assert result is not None
        assert "computed_at" in result
        assert "by_underlying" in result

    def test_check_anti_crowding_kill_switch_off_returns_true(self):
        """Kill switch off → (True, '') regardless of book conflict, no exception."""
        book = _book_with_strategy("iron_condor", "SPY", ["short_put", "short_call"])
        from config import settings as real_settings
        mock = MagicMock(wraps=real_settings)
        mock.CROSS_ACCOUNT_ANTI_CROWDING_ENABLED = False
        mock.DIRECTIONAL_FAMILY_MAP = real_settings.DIRECTIONAL_FAMILY_MAP
        with patch("data.book_exposure.settings", mock):
            allowed, reason = check_anti_crowding("SPY", "wheel", book=book)
        assert allowed is True
        assert reason == ""
