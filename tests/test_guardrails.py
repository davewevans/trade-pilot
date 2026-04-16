"""Tests for Guardrails.classify_rejection and skip_code integration."""

import pytest

from strategies.guardrails import Guardrails
from strategies.skip_codes import SkipCode, normalize_skip_code


class TestClassifyRejection:
    """Verify every canonical guardrail rejection reason maps to the right code."""

    def test_earnings_too_close(self):
        assert Guardrails.classify_rejection(
            "Earnings in 5 days — must be > 21 days away to sell a CSP"
        ) == SkipCode.EARNINGS_TOO_CLOSE

    def test_earnings_hard_block(self):
        assert Guardrails.classify_rejection(
            "Earnings in 18 days (hard block: need > 21)"
        ) == SkipCode.EARNINGS_TOO_CLOSE

    def test_ex_dividend(self):
        assert Guardrails.classify_rejection(
            "Ex-dividend in 12 days within DTE 21 — early assignment risk"
        ) == SkipCode.EX_DIVIDEND_IN_WINDOW

    def test_buying_power(self):
        assert Guardrails.classify_rejection(
            "Position cost $15,000 (strike 150 × 100) exceeds 10% of buying power $12,000"
        ) == SkipCode.BUYING_POWER_INSUFFICIENT

    def test_already_have_open_put(self):
        assert Guardrails.classify_rejection(
            "Already have an open short put on AAPL: AAPL260620P00150000"
        ) == SkipCode.POSITION_LIMIT_REACHED

    def test_sector_concentration(self):
        assert Guardrails.classify_rejection(
            "Already have 3 short-put positions in Technology sector (max 3)"
        ) == SkipCode.POSITION_LIMIT_REACHED

    def test_already_have_condor(self):
        assert Guardrails.classify_rejection(
            "Already have an open iron condor on SPY"
        ) == SkipCode.POSITION_LIMIT_REACHED

    def test_iv_rank_minimum(self):
        assert Guardrails.classify_rejection(
            "IV rank 22 < 50 minimum for iron condor entry"
        ) == SkipCode.LOW_IVR

    def test_dte_out_of_range(self):
        assert Guardrails.classify_rejection(
            "DTE 15 outside allowed range 20-50"
        ) == SkipCode.DTE_OUT_OF_RANGE

    def test_net_credit_too_low(self):
        assert Guardrails.classify_rejection(
            "Net credit $0.45 <= $0.50 minimum"
        ) == SkipCode.CREDIT_TOO_LOW

    def test_total_credit_too_low(self):
        assert Guardrails.classify_rejection(
            "Total credit $0.95 <= $1.00 minimum"
        ) == SkipCode.CREDIT_TOO_LOW

    def test_credit_to_width_ratio(self):
        assert Guardrails.classify_rejection(
            "Credit-to-width ratio 0.12 < 0.20 minimum (credit=$1.20, width=$10)"
        ) == SkipCode.CREDIT_TOO_LOW

    def test_net_debit_too_high(self):
        assert Guardrails.classify_rejection(
            "Net debit $2.60 >= $2.50 maximum"
        ) == SkipCode.DEBIT_TOO_HIGH

    def test_net_debit_outside_range(self):
        assert Guardrails.classify_rejection(
            "Net debit $3.00 outside allowed range $0.50–$2.50"
        ) == SkipCode.DEBIT_TOO_HIGH

    def test_regime_mismatch(self):
        assert Guardrails.classify_rejection(
            "Market regime must be BULL for debit spread, got NEUTRAL"
        ) == SkipCode.REGIME_MISMATCH

    def test_iv_environment_mismatch(self):
        assert Guardrails.classify_rejection(
            "IV environment must be LOW for debit spread, got HIGH"
        ) == SkipCode.IV_ENV_MISMATCH

    def test_circuit_breaker(self):
        assert Guardrails.classify_rejection(
            "circuit breaker tripped — no new entries"
        ) == SkipCode.CIRCUIT_BREAKER_ACTIVE

    def test_empty_string(self):
        assert Guardrails.classify_rejection("") == SkipCode.OTHER

    def test_none(self):
        assert Guardrails.classify_rejection(None) == SkipCode.OTHER

    def test_structural_error_falls_back_to_other(self):
        assert Guardrails.classify_rejection(
            "Invalid OCC put symbol: 'AAPL...'"
        ) == SkipCode.OTHER


class TestNormalizeSkipCode:
    def test_known_code_returned_unchanged(self):
        assert normalize_skip_code("LOW_IVR") == "LOW_IVR"
        assert normalize_skip_code("EARNINGS_TOO_CLOSE") == "EARNINGS_TOO_CLOSE"
        assert normalize_skip_code("OTHER") == "OTHER"

    def test_unknown_code_becomes_other(self):
        assert normalize_skip_code("BOGUS_CODE") == SkipCode.OTHER

    def test_none_becomes_other(self):
        assert normalize_skip_code(None) == SkipCode.OTHER

    def test_empty_string_becomes_other(self):
        assert normalize_skip_code("") == SkipCode.OTHER


class TestSkipCodeEnum:
    def test_all_tuple_covers_all_codes(self):
        # Every string class var that is upper-case should appear in ALL
        codes = {v for k, v in vars(SkipCode).items()
                 if not k.startswith("_") and isinstance(v, str)}
        for code in codes:
            assert code in SkipCode.ALL, f"{code!r} missing from SkipCode.ALL"
