"""Tests for spread candidate building in ContextBuilder."""

from unittest.mock import MagicMock, patch

import pytest

from data.context_builder import ContextBuilder


# ── Helpers ─────────────────────────────────────────────────


def _make_contract(symbol, strike, exp, opt_type="put", oi=500):
    return {
        "symbol": symbol,
        "strike_price": strike,
        "expiration_date": exp,
        "type": opt_type,
        "open_interest": oi,
        "close_price": 2.0,
    }


def _make_snapshot(bid, ask, delta, oi=500, theta=-0.05):
    mid = round((bid + ask) / 2, 4)
    return {
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "delta": delta,
        "theta": theta,
        "vega": 0.08,
        "gamma": 0.02,
        "iv": 0.30,
        "open_interest": oi,
        "volume": 100,
    }


@pytest.fixture
def builder():
    """ContextBuilder with a mocked broker."""
    broker = MagicMock()
    with patch("data.context_builder._news_client"):
        cb = ContextBuilder.__new__(ContextBuilder)
        cb.broker = broker
        cb.journal = MagicMock()
        cb._regime_filter = MagicMock()
    return cb


# ── Bull put spread ─────────────────────────────────────────


class TestBullPutSpread:
    def test_correct_structure(self, builder):
        # Short 530 put, long 525 put (wing_width=5)
        builder.broker.get_option_chain_with_greeks.return_value = [
            _make_contract("SPY250502P00530000", 530, "2025-05-02"),
            _make_contract("SPY250502P00525000", 525, "2025-05-02"),
        ]
        builder.broker.get_option_snapshots.return_value = {
            "SPY250502P00530000": _make_snapshot(bid=3.00, ask=3.20, delta=-0.25, oi=1000),
            "SPY250502P00525000": _make_snapshot(bid=1.80, ask=2.00, delta=-0.15, oi=800),
        }

        result = builder.build_spread_candidates(
            "SPY", "bull_put_spread",
            dte_min=1, dte_max=60,
            short_delta_min=0.15, short_delta_max=0.30,
            wing_width_strikes=5,
            underlying_price=540.0,
        )

        assert result["underlying"] == "SPY"
        assert result["strategy_type"] == "bull_put_spread"
        assert len(result["candidates"]) == 1

        c = result["candidates"][0]
        assert c["short_leg"]["strike"] == 530
        assert c["long_leg"]["strike"] == 525
        assert c["net_credit"] > 0
        assert c["max_loss"] > 0
        assert c["max_gain"] > 0
        assert c["break_even"] < 530
        assert "credit_to_width_ratio" in c
        assert isinstance(c["liquidity_ok"], bool)

    def test_credit_to_width_ratio(self, builder):
        builder.broker.get_option_chain_with_greeks.return_value = [
            _make_contract("P530", 530, "2025-05-02"),
            _make_contract("P525", 525, "2025-05-02"),
        ]
        builder.broker.get_option_snapshots.return_value = {
            "P530": _make_snapshot(bid=3.00, ask=3.20, delta=-0.25, oi=500),
            "P525": _make_snapshot(bid=1.80, ask=2.00, delta=-0.15, oi=500),
        }

        result = builder.build_spread_candidates(
            "SPY", "bull_put_spread",
            dte_min=1, dte_max=60, wing_width_strikes=5,
            underlying_price=540.0,
        )

        c = result["candidates"][0]
        short_mid = (3.00 + 3.20) / 2  # 3.10
        long_mid = (1.80 + 2.00) / 2   # 1.90
        expected_credit = round(short_mid - long_mid, 4)  # 1.20
        expected_ratio = round(expected_credit / 5, 4)     # 0.24
        assert c["net_credit"] == expected_credit
        assert c["credit_to_width_ratio"] == expected_ratio

    def test_best_candidate_picks_highest_ratio(self, builder):
        # Two expirations, different credits
        builder.broker.get_option_chain_with_greeks.return_value = [
            _make_contract("P530A", 530, "2025-05-02"),
            _make_contract("P525A", 525, "2025-05-02"),
            _make_contract("P530B", 530, "2025-05-09"),
            _make_contract("P525B", 525, "2025-05-09"),
        ]
        builder.broker.get_option_snapshots.return_value = {
            "P530A": _make_snapshot(bid=3.00, ask=3.20, delta=-0.25, oi=500),
            "P525A": _make_snapshot(bid=1.80, ask=2.00, delta=-0.15, oi=500),
            "P530B": _make_snapshot(bid=4.00, ask=4.20, delta=-0.25, oi=500),
            "P525B": _make_snapshot(bid=1.80, ask=2.00, delta=-0.15, oi=500),
        }

        result = builder.build_spread_candidates(
            "SPY", "bull_put_spread",
            dte_min=1, dte_max=60, wing_width_strikes=5,
            underlying_price=540.0,
        )

        assert len(result["candidates"]) == 2
        best = result["best_candidate"]
        assert best is not None
        # The 2025-05-09 expiration has higher credit (4.10 - 1.90 = 2.20 vs 1.20)
        assert best["net_credit"] > 2.0


# ── Bear call spread ────────────────────────────────────────


class TestBearCallSpread:
    def test_correct_structure(self, builder):
        builder.broker.get_option_chain_with_greeks.return_value = [
            _make_contract("C550", 550, "2025-05-02", "call"),
            _make_contract("C555", 555, "2025-05-02", "call"),
        ]
        builder.broker.get_option_snapshots.return_value = {
            "C550": _make_snapshot(bid=2.80, ask=3.00, delta=0.25, oi=600),
            "C555": _make_snapshot(bid=1.50, ask=1.70, delta=0.15, oi=400),
        }

        result = builder.build_spread_candidates(
            "SPY", "bear_call_spread",
            dte_min=1, dte_max=60, wing_width_strikes=5,
            underlying_price=540.0,
        )

        assert len(result["candidates"]) == 1
        c = result["candidates"][0]
        assert c["short_leg"]["strike"] == 550
        assert c["long_leg"]["strike"] == 555
        assert c["net_credit"] > 0
        assert c["break_even"] > 550  # short_strike + net_credit for calls


# ── Iron condor ─────────────────────────────────────────────


class TestIronCondor:
    def test_combines_put_and_call_spreads(self, builder):
        builder.broker.get_option_chain_with_greeks.side_effect = [
            # First call: put chain
            [
                _make_contract("P530", 530, "2025-05-02", "put"),
                _make_contract("P525", 525, "2025-05-02", "put"),
            ],
            # Second call: call chain
            [
                _make_contract("C550", 550, "2025-05-02", "call"),
                _make_contract("C555", 555, "2025-05-02", "call"),
            ],
        ]
        builder.broker.get_option_snapshots.side_effect = [
            # Put snapshots
            {
                "P530": _make_snapshot(bid=3.00, ask=3.20, delta=-0.20, oi=500),
                "P525": _make_snapshot(bid=1.80, ask=2.00, delta=-0.10, oi=500),
            },
            # Call snapshots
            {
                "C550": _make_snapshot(bid=2.80, ask=3.00, delta=0.20, oi=500),
                "C555": _make_snapshot(bid=1.50, ask=1.70, delta=0.10, oi=500),
            },
        ]

        result = builder.build_spread_candidates(
            "SPY", "iron_condor",
            dte_min=1, dte_max=60, wing_width_strikes=5,
            underlying_price=540.0,
        )

        assert result["iron_condor_legs"] is not None
        ic = result["iron_condor_legs"]
        assert "put_spread" in ic
        assert "call_spread" in ic
        assert ic["total_credit"] > 0
        assert ic["total_max_loss"] > 0
        assert ic["total_credit"] == round(
            ic["put_spread"]["net_credit"] + ic["call_spread"]["net_credit"], 4
        )


# ── Long call vertical ─────────────────────────────────────


class TestLongCallVertical:
    def test_correct_structure(self, builder):
        builder.broker.get_option_chain_with_greeks.return_value = [
            _make_contract("C540", 540, "2025-05-02", "call"),
            _make_contract("C545", 545, "2025-05-02", "call"),
        ]
        builder.broker.get_option_snapshots.return_value = {
            "C540": _make_snapshot(bid=5.00, ask=5.40, delta=0.50, oi=800),
            "C545": _make_snapshot(bid=3.00, ask=3.20, delta=0.35, oi=600),
        }

        result = builder.build_spread_candidates(
            "SPY", "long_call_vertical",
            dte_min=1, dte_max=60, wing_width_strikes=5,
            underlying_price=540.0,
        )

        assert len(result["candidates"]) == 1
        c = result["candidates"][0]
        assert c["long_leg"]["strike"] == 540  # ATM, delta 0.50
        assert c["short_leg"]["strike"] == 545  # OTM
        assert c["net_debit"] > 0
        assert c["max_loss"] == round(c["net_debit"] * 100, 2)
        assert c["break_even"] == round(540 + c["net_debit"], 4)


# ── Liquidity filter ────────────────────────────────────────


class TestLiquidityFilter:
    def test_low_oi_excluded_from_best(self, builder):
        builder.broker.get_option_chain_with_greeks.return_value = [
            _make_contract("P530", 530, "2025-05-02"),
            _make_contract("P525", 525, "2025-05-02"),
        ]
        builder.broker.get_option_snapshots.return_value = {
            "P530": _make_snapshot(bid=3.00, ask=3.20, delta=-0.25, oi=50),  # low OI
            "P525": _make_snapshot(bid=1.80, ask=2.00, delta=-0.15, oi=500),
        }

        result = builder.build_spread_candidates(
            "SPY", "bull_put_spread",
            dte_min=1, dte_max=60, wing_width_strikes=5,
            underlying_price=540.0,
        )

        assert len(result["candidates"]) == 1
        assert result["candidates"][0]["liquidity_ok"] is False
        assert result["best_candidate"] is None  # no liquid candidates

    def test_wide_spread_excluded_from_best(self, builder):
        builder.broker.get_option_chain_with_greeks.return_value = [
            _make_contract("P530", 530, "2025-05-02"),
            _make_contract("P525", 525, "2025-05-02"),
        ]
        # bid=1.00, ask=3.00 -> spread_pct = (2.00/2.00)*100 = 100%
        builder.broker.get_option_snapshots.return_value = {
            "P530": _make_snapshot(bid=1.00, ask=3.00, delta=-0.25, oi=500),
            "P525": _make_snapshot(bid=0.50, ask=1.50, delta=-0.15, oi=500),
        }

        result = builder.build_spread_candidates(
            "SPY", "bull_put_spread",
            dte_min=1, dte_max=60, wing_width_strikes=5,
            underlying_price=540.0,
        )

        assert len(result["candidates"]) == 1
        assert result["candidates"][0]["liquidity_ok"] is False
        assert result["best_candidate"] is None


# ── Empty candidates ────────────────────────────────────────


class TestEmptyCandidates:
    def test_no_contracts_returns_empty(self, builder):
        builder.broker.get_option_chain_with_greeks.return_value = []

        result = builder.build_spread_candidates(
            "SPY", "bull_put_spread",
            dte_min=1, dte_max=60, wing_width_strikes=5,
            underlying_price=540.0,
        )

        assert result["candidates"] == []
        assert result["best_candidate"] is None
        assert result["iron_condor_legs"] is None

    def test_no_matching_deltas_returns_empty(self, builder):
        builder.broker.get_option_chain_with_greeks.return_value = [
            _make_contract("P530", 530, "2025-05-02"),
            _make_contract("P525", 525, "2025-05-02"),
        ]
        # Delta outside range
        builder.broker.get_option_snapshots.return_value = {
            "P530": _make_snapshot(bid=3.00, ask=3.20, delta=-0.05, oi=500),
            "P525": _make_snapshot(bid=1.80, ask=2.00, delta=-0.02, oi=500),
        }

        result = builder.build_spread_candidates(
            "SPY", "bull_put_spread",
            dte_min=1, dte_max=60, wing_width_strikes=5,
            underlying_price=540.0,
        )

        assert result["candidates"] == []
        assert result["best_candidate"] is None

    def test_iron_condor_no_legs_returns_none(self, builder):
        builder.broker.get_option_chain_with_greeks.return_value = []
        builder.broker.get_option_snapshots.return_value = {}

        result = builder.build_spread_candidates(
            "SPY", "iron_condor",
            dte_min=1, dte_max=60, wing_width_strikes=5,
            underlying_price=540.0,
        )

        assert result["iron_condor_legs"] is None
        assert result["best_candidate"] is None


# ── Strategy selection ──────────────────────────────────────


class TestSelectSpreadStrategies:
    def test_bear_regime(self):
        assert ContextBuilder._select_spread_strategies("BEAR", "HIGH") == ["bear_call_spread"]

    def test_neutral_high_iv(self):
        assert ContextBuilder._select_spread_strategies("NEUTRAL", "HIGH") == ["iron_condor"]

    def test_neutral_moderate_iv(self):
        result = ContextBuilder._select_spread_strategies("NEUTRAL", "MODERATE")
        assert "bull_put_spread" in result
        assert "iron_condor" in result

    def test_bull_low_iv(self):
        assert ContextBuilder._select_spread_strategies("BULL", "LOW") == ["long_call_vertical"]

    def test_crash_returns_empty(self):
        assert ContextBuilder._select_spread_strategies("CRASH", "HIGH") == []


# ── calculate_current_spread_value ──────────────────────────


class TestCalculateCurrentSpreadValue:
    def test_returns_correct_values(self, builder):
        builder.broker.get_option_snapshots.return_value = {
            "SHORT_SYM": {"mid": 1.20, "bid": 1.10, "ask": 1.30},
            "LONG_SYM": {"mid": 0.40, "bid": 0.35, "ask": 0.45},
        }
        builder.journal.get_open_positions.return_value = []

        result = builder.calculate_current_spread_value("SHORT_SYM", "LONG_SYM")

        assert result["short_current_mid"] == 1.20
        assert result["long_current_mid"] == 0.40
        assert result["current_spread_value"] == 0.80

    def test_handles_missing_snapshots(self, builder):
        builder.broker.get_option_snapshots.return_value = {}
        builder.journal.get_open_positions.return_value = []

        result = builder.calculate_current_spread_value("X", "Y")
        assert result["short_current_mid"] is None
        assert result["current_spread_value"] is None

    def test_pnl_pct_from_journal(self, builder):
        builder.broker.get_option_snapshots.return_value = {
            "SHORT": {"mid": 0.50},
            "LONG": {"mid": 0.10},
        }
        builder.journal.get_open_positions.side_effect = [
            [{"limit_price": 2.00}],  # original credit
            [],
        ]

        result = builder.calculate_current_spread_value("SHORT", "LONG")
        # current_spread_value = 0.50 - 0.10 = 0.40
        # captured = 2.00 - 0.40 = 1.60
        # pnl_pct = 1.60 / 2.00 * 100 = 80.0
        assert result["original_credit"] == 2.00
        assert result["pnl_pct"] == 80.0
