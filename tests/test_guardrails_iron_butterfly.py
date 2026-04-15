"""Tests for iron butterfly guardrail validation."""

import pytest

from strategies.guardrails import Guardrails


# ── Helpers ─────────────────────────────────────────────────


def _base_context(**overrides):
    ctx = {
        "confirmed_market_regime": "NEUTRAL",
        "iv_environment": "HIGH",
        "iv_rank": 55,
        "macro": {"vix": 22},
        "fundamentals": {"days_to_earnings": 60},
        "technicals": {"current_price": 540.0},
    }
    ctx.update(overrides)
    return ctx


def _entry_decision(**overrides):
    """Return a valid iron butterfly entry decision dict."""
    d = {
        "action": "OPEN",
        "put_long_symbol": "SPY250502P00535000",
        "put_short_symbol": "SPY250502P00540000",
        "call_short_symbol": "SPY250502C00540000",
        "call_long_symbol": "SPY250502C00545000",
        "expiration": "2025-05-02",
        "dte": 30,
        "total_credit": 3.50,
        "max_loss": 150.0,
        "limit_price": -3.50,
        "center_strike": 540.0,
        "reasoning": "Valid butterfly setup",
        "skip_reason": None,
    }
    d.update(overrides)
    return d


@pytest.fixture
def guardrails():
    return Guardrails()


# ================================================================
# Valid entry
# ================================================================


class TestValidEntry:
    def test_valid_entry_passes(self, guardrails):
        ok, reason = guardrails.validate_iron_butterfly_entry(
            _entry_decision(), _base_context(), {"buying_power": "100000"}
        )
        assert ok is True
        assert reason == ""


# ================================================================
# Basic field checks
# ================================================================


class TestBasicChecks:
    def test_rejects_positive_limit_price(self, guardrails):
        ok, reason = guardrails.validate_iron_butterfly_entry(
            _entry_decision(limit_price=3.50),
            _base_context(),
            {"buying_power": "100000"},
        )
        assert ok is False
        assert "negative" in reason.lower()

    def test_rejects_credit_below_minimum(self, guardrails):
        ok, reason = guardrails.validate_iron_butterfly_entry(
            _entry_decision(total_credit=0.80),
            _base_context(),
            {"buying_power": "100000"},
        )
        assert ok is False
        assert "minimum" in reason.lower() or "$" in reason

    def test_rejects_dte_too_low(self, guardrails):
        ok, reason = guardrails.validate_iron_butterfly_entry(
            _entry_decision(dte=5),
            _base_context(),
            {"buying_power": "100000"},
        )
        assert ok is False
        assert "DTE" in reason

    def test_rejects_dte_too_high(self, guardrails):
        ok, reason = guardrails.validate_iron_butterfly_entry(
            _entry_decision(dte=60),
            _base_context(),
            {"buying_power": "100000"},
        )
        assert ok is False
        assert "DTE" in reason

    def test_rejects_max_loss_exceeds_buying_power_pct(self, guardrails):
        ok, reason = guardrails.validate_iron_butterfly_entry(
            _entry_decision(max_loss=9000),  # > 8% of 100k
            _base_context(),
            {"buying_power": "100000"},
        )
        assert ok is False
        assert "8%" in reason

    def test_rejects_invalid_occ_symbol(self, guardrails):
        ok, reason = guardrails.validate_iron_butterfly_entry(
            _entry_decision(put_short_symbol="INVALID"),
            _base_context(),
            {"buying_power": "100000"},
        )
        assert ok is False
        assert "OCC" in reason


# ================================================================
# Butterfly-specific: matching short strikes
# ================================================================


class TestMatchingShortStrikes:
    def test_rejects_mismatched_short_strikes(self, guardrails):
        """Iron butterfly requires both shorts at the same ATM strike."""
        ok, reason = guardrails.validate_iron_butterfly_entry(
            _entry_decision(
                put_short_symbol="SPY250502P00540000",  # strike 540
                call_short_symbol="SPY250502C00545000",  # strike 545 — mismatch
            ),
            _base_context(),
            {"buying_power": "100000"},
        )
        assert ok is False
        assert "short strike" in reason.lower() or "matching" in reason.lower()

    def test_accepts_matching_short_strikes(self, guardrails):
        """Same strike for both shorts is required and valid."""
        ok, reason = guardrails.validate_iron_butterfly_entry(
            _entry_decision(
                put_short_symbol="SPY250502P00540000",
                call_short_symbol="SPY250502C00540000",
            ),
            _base_context(),
            {"buying_power": "100000"},
        )
        assert ok is True


# ================================================================
# Credit-to-width ratio
# ================================================================


class TestCreditToWidthRatio:
    def test_rejects_low_credit_to_width_ratio(self, guardrails):
        """credit/width must be >= 0.20 (hard minimum)."""
        # put wing at 530, center at 540 → width = 10, credit = 1.50 → ratio = 0.15
        ok, reason = guardrails.validate_iron_butterfly_entry(
            _entry_decision(
                put_long_symbol="SPY250502P00530000",  # strike 530
                put_short_symbol="SPY250502P00540000",  # strike 540 → width = 10
                total_credit=1.50,  # 1.50 / 10 = 0.15 < 0.20
                limit_price=-1.50,
            ),
            _base_context(),
            {"buying_power": "100000"},
        )
        assert ok is False
        assert "ratio" in reason.lower() or "credit" in reason.lower()


# ================================================================
# Duplicate position check
# ================================================================


class TestDuplicatePosition:
    def test_rejects_duplicate_butterfly(self, guardrails):
        open_butterflies = [{"underlying": "SPY", "status": "open"}]
        ok, reason = guardrails.validate_iron_butterfly_entry(
            _entry_decision(),
            _base_context(),
            {"buying_power": "100000"},
            open_butterflies=open_butterflies,
        )
        assert ok is False
        assert "iron butterfly" in reason.lower()

    def test_allows_different_underlying(self, guardrails):
        open_butterflies = [{"underlying": "QQQ", "status": "open"}]
        ok, reason = guardrails.validate_iron_butterfly_entry(
            _entry_decision(),  # SPY symbols
            _base_context(),
            {"buying_power": "100000"},
            open_butterflies=open_butterflies,
        )
        assert ok is True


# ================================================================
# Earnings check
# ================================================================


class TestEarningsCheck:
    def test_rejects_earnings_within_30_days(self, guardrails):
        ok, reason = guardrails.validate_iron_butterfly_entry(
            _entry_decision(),
            _base_context(fundamentals={"days_to_earnings": 20}),
            {"buying_power": "100000"},
        )
        assert ok is False
        assert "Earnings" in reason

    def test_allows_earnings_beyond_30_days(self, guardrails):
        ok, reason = guardrails.validate_iron_butterfly_entry(
            _entry_decision(),
            _base_context(fundamentals={"days_to_earnings": 45}),
            {"buying_power": "100000"},
        )
        assert ok is True


# ================================================================
# IV Rank check
# ================================================================


class TestIVRankCheck:
    def test_rejects_ivr_below_50(self, guardrails):
        ok, reason = guardrails.validate_iron_butterfly_entry(
            _entry_decision(),
            _base_context(iv_rank=35),
            {"buying_power": "100000"},
        )
        assert ok is False
        assert "IV rank" in reason

    def test_allows_ivr_at_50(self, guardrails):
        ok, reason = guardrails.validate_iron_butterfly_entry(
            _entry_decision(),
            _base_context(iv_rank=50),
            {"buying_power": "100000"},
        )
        assert ok is True
