"""Unit tests for data.shadow_execution.classify_fillability."""

import pytest

from data.shadow_execution import classify_fillability


# ── Credit spreads (seller, receives premium) ─────────────────────────────────
# limit_magnitude is the absolute premium received.
# Fillable when net_ask ≤ limit_magnitude (seller willing to accept ≤ what market pays).

class TestCredit:
    def test_always_fillable_below_mid(self):
        # limit < net_mid → market will always pay more than you asked
        assert classify_fillability(True, 1.50, net_bid=1.00, net_mid=2.00, net_ask=3.00) == "always_fillable"

    def test_always_fillable_at_mid(self):
        assert classify_fillability(True, 2.00, net_bid=1.00, net_mid=2.00, net_ask=3.00) == "always_fillable"

    def test_sometimes_fillable_above_mid(self):
        # mid < limit ≤ ask → fillable when market is at ask
        assert classify_fillability(True, 2.50, net_bid=1.00, net_mid=2.00, net_ask=3.00) == "sometimes_fillable"

    def test_sometimes_fillable_at_ask(self):
        assert classify_fillability(True, 3.00, net_bid=1.00, net_mid=2.00, net_ask=3.00) == "sometimes_fillable"

    def test_not_fillable_above_ask(self):
        # limit > ask → market never pays this much
        assert classify_fillability(True, 3.50, net_bid=1.00, net_mid=2.00, net_ask=3.00) == "not_fillable"

    def test_not_fillable_far_above_ask(self):
        assert classify_fillability(True, 10.00, net_bid=1.00, net_mid=2.00, net_ask=3.00) == "not_fillable"


# ── Debit spreads (buyer, pays premium) ──────────────────────────────────────
# limit_magnitude is the absolute premium paid.
# Fillable when net_bid ≥ limit_magnitude (buyer willing to pay ≥ what market asks).

class TestDebit:
    def test_always_fillable_above_mid(self):
        # limit > net_mid → buyer willing to pay more than mid
        assert classify_fillability(False, 2.50, net_bid=1.00, net_mid=2.00, net_ask=3.00) == "always_fillable"

    def test_always_fillable_at_mid(self):
        assert classify_fillability(False, 2.00, net_bid=1.00, net_mid=2.00, net_ask=3.00) == "always_fillable"

    def test_sometimes_fillable_below_mid(self):
        # bid ≤ limit < mid → fillable when market is at bid
        assert classify_fillability(False, 1.50, net_bid=1.00, net_mid=2.00, net_ask=3.00) == "sometimes_fillable"

    def test_sometimes_fillable_at_bid(self):
        assert classify_fillability(False, 1.00, net_bid=1.00, net_mid=2.00, net_ask=3.00) == "sometimes_fillable"

    def test_not_fillable_below_bid(self):
        # limit < bid → market never sells this cheap
        assert classify_fillability(False, 0.50, net_bid=1.00, net_mid=2.00, net_ask=3.00) == "not_fillable"

    def test_not_fillable_zero_limit(self):
        assert classify_fillability(False, 0.00, net_bid=1.00, net_mid=2.00, net_ask=3.00) == "not_fillable"


# ── Data unavailable cases ────────────────────────────────────────────────────

class TestDataUnavailable:
    def test_none_bid(self):
        assert classify_fillability(True, 2.00, net_bid=None, net_mid=2.00, net_ask=3.00) == "data_unavailable"

    def test_none_mid(self):
        assert classify_fillability(True, 2.00, net_bid=1.00, net_mid=None, net_ask=3.00) == "data_unavailable"

    def test_none_ask(self):
        assert classify_fillability(True, 2.00, net_bid=1.00, net_mid=2.00, net_ask=None) == "data_unavailable"

    def test_all_none(self):
        assert classify_fillability(False, 2.00, net_bid=None, net_mid=None, net_ask=None) == "data_unavailable"

    def test_zero_locked_bid_eq_ask_credit(self):
        # bid == ask (zero spread) is valid — not crossed
        result = classify_fillability(True, 2.00, net_bid=2.00, net_mid=2.00, net_ask=2.00)
        assert result in ("always_fillable", "sometimes_fillable")

    def test_crossed_quotes_bid_gt_ask(self):
        # Crossed: bid > ask → data_unavailable
        assert classify_fillability(True, 2.00, net_bid=3.00, net_mid=2.50, net_ask=2.00) == "data_unavailable"

    def test_zero_bid_and_ask_credit(self):
        # Both zero → data_unavailable (no valid NBBO)
        assert classify_fillability(True, 2.00, net_bid=0.0, net_mid=0.0, net_ask=0.0) == "data_unavailable"

    def test_negative_bid_not_crossed_is_classifiable(self):
        # Negative net_bid is valid for credit spreads (e.g. iron condor legs).
        # Not crossed (bid < ask), not zero-locked → classified normally.
        # limit=2.00 > ask=1.00 → not_fillable for credit
        assert classify_fillability(True, 2.00, net_bid=-1.00, net_mid=0.00, net_ask=1.00) == "not_fillable"
