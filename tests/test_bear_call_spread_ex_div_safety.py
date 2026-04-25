"""Tests for the ex-dividend defensive default in validate_bear_call_spread_entry.

Covers the safety patch that rejects spreads when ex-div data is known to
have failed to fetch (ex_dividend_data_available=False in fundamentals).
"""
import pytest

from strategies.guardrails import Guardrails


def _base_decision(**overrides):
    d = {
        "action": "OPEN",
        "short_call_symbol": "SPY260515C00550000",
        "long_call_symbol": "SPY260515C00555000",
        "expiration": "2026-05-15",
        "dte": 30,
        "short_call_strike": 550.0,
        "long_call_strike": 555.0,
        "net_credit": 1.00,
        "max_loss": 400.0,
        "limit_price": -1.00,
        "bearish_rationale": "Below 50-SMA",
        "reasoning": "Good bearish setup",
        "skip_reason": None,
    }
    d.update(overrides)
    return d


def _base_context(**overrides):
    ctx = {
        "symbol": "SPY",
        "confirmed_market_regime": "BEAR",
        "iv_environment": "HIGH",
        "iv_rank": 55,
        "macro": {"vix": 25},
        "fundamentals": {
            "days_to_earnings": 50,
            "days_to_ex_dividend": None,
            "next_ex_dividend_date": None,
            "ex_dividend_data_available": True,
        },
        "technicals": {
            "current_price": 540.0,
            "above_sma_50": False,
            "above_sma_200": True,
            "rsi_14": 65,
        },
        "spread_candidates": {
            "bear_call_spread": {
                "best_candidate": {
                    "expiration": "2026-05-15",
                    "dte": 30,
                    "short_leg": {
                        "symbol": "SPY260515C00550000",
                        "strike": 550,
                        "delta": 0.25,
                        "bid": 2.80,
                        "ask": 3.00,
                        "mid": 2.90,
                        "open_interest": 600,
                        "bid_ask_spread_pct": 6.9,
                    },
                    "long_leg": {
                        "symbol": "SPY260515C00555000",
                        "strike": 555,
                        "delta": 0.18,
                        "bid": 1.80,
                        "ask": 2.00,
                        "mid": 1.90,
                        "open_interest": 500,
                    },
                    "net_credit": 1.00,
                    "max_loss": 400.0,
                    "max_gain": 100.0,
                    "break_even": 551.0,
                    "credit_to_width_ratio": 0.20,
                    "liquidity_ok": True,
                    "spread_yield": 0.00185,
                },
            },
        },
    }
    ctx.update(overrides)
    return ctx


@pytest.fixture
def guardrails():
    return Guardrails()


class TestExDividendDefensive:
    def test_rejects_when_data_unavailable(self, guardrails):
        """ex_dividend_data_available=False → reject defensively."""
        ctx = _base_context(fundamentals={
            "days_to_earnings": 50,
            "days_to_ex_dividend": None,
            "annual_dividend_yield": None,
            "ex_dividend_data_available": False,
        })
        ok, reason = guardrails.validate_bear_call_spread_entry(
            _base_decision(), ctx, {"buying_power": "100000"},
        )
        assert ok is False
        assert "Ex-dividend data fetch failed" in reason

    def test_accepts_when_data_available_no_upcoming_ex_div(self, guardrails):
        """ex_dividend_data_available=True, days_ex=None → accept (no upcoming ex-div)."""
        ctx = _base_context(fundamentals={
            "days_to_earnings": 50,
            "days_to_ex_dividend": None,
            "annual_dividend_yield": None,
            "ex_dividend_data_available": True,
        })
        ok, reason = guardrails.validate_bear_call_spread_entry(
            _base_decision(), ctx, {"buying_power": "100000"},
        )
        assert ok is True

    def test_accepts_when_ex_div_outside_dte_window(self, guardrails):
        """Ex-div date exists but is past DTE → accept."""
        ctx = _base_context(fundamentals={
            "days_to_earnings": 50,
            "days_to_ex_dividend": 60,
            "annual_dividend_yield": 0.015,
            "ex_dividend_data_available": True,
        })
        ok, reason = guardrails.validate_bear_call_spread_entry(
            _base_decision(dte=30), ctx, {"buying_power": "100000"},
        )
        assert ok is True

    def test_rejects_when_ex_div_within_dte_window(self, guardrails):
        """Original safety check: ex-div within DTE → reject. Regression guard."""
        ctx = _base_context(fundamentals={
            "days_to_earnings": 50,
            "days_to_ex_dividend": 14,
            "annual_dividend_yield": 0.015,
            "ex_dividend_data_available": True,
        })
        ok, reason = guardrails.validate_bear_call_spread_entry(
            _base_decision(dte=30), ctx, {"buying_power": "100000"},
        )
        assert ok is False
        assert "Ex-dividend" in reason
        assert "early assignment risk" in reason

    def test_backward_compat_field_missing_treated_as_unknown(self, guardrails):
        """ex_dividend_data_available absent (old context shape) → accept.

        Preserves current behavior during deployment before plumbing is
        verified in production. Once confirmed, this test can be tightened.
        """
        ctx = _base_context(fundamentals={
            "days_to_earnings": 50,
            "days_to_ex_dividend": None,
            "annual_dividend_yield": None,
            # ex_dividend_data_available intentionally absent
        })
        ok, reason = guardrails.validate_bear_call_spread_entry(
            _base_decision(), ctx, {"buying_power": "100000"},
        )
        assert ok is True
