"""Tests for spread candidate building in ContextBuilder."""

from unittest.mock import MagicMock, patch

import pytest

from data.context_builder import (
    ContextBuilder,
    _compute_iv_overvalued_label,
    _compute_contango_label,
)


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
        "last_trade_size": 100,
    }


@pytest.fixture
def builder():
    """ContextBuilder with a mocked broker and ORATS client."""
    broker = MagicMock()
    with patch("data.context_builder._news_client"):
        cb = ContextBuilder.__new__(ContextBuilder)
        cb.broker = broker
        cb.journal = MagicMock()
        cb._regime_filter = MagicMock()
        # Return empty ORATS data so all snapshots fall back to Alpaca mock,
        # preserving existing test assertions unchanged.
        orats_client = MagicMock()
        orats_client.get_snapshots_by_strike.return_value = {}
        cb._orats_client = orats_client
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

    def test_none_oi_sets_liquidity_ok_false_and_passes_through(self, builder):
        """open_interest=None (cache miss) must not be rendered as 0 in the leg
        dict and must set liquidity_ok=False rather than crashing."""
        builder.broker.get_option_chain_with_greeks.return_value = [
            _make_contract("P530", 530, "2025-05-02"),
            _make_contract("P525", 525, "2025-05-02"),
        ]
        builder.broker.get_option_snapshots.return_value = {
            "P530": _make_snapshot(bid=3.00, ask=3.20, delta=-0.25, oi=None),
            "P525": _make_snapshot(bid=1.80, ask=2.00, delta=-0.15, oi=500),
        }

        result = builder.build_spread_candidates(
            "SPY", "bull_put_spread",
            dte_min=1, dte_max=60, wing_width_strikes=5,
            underlying_price=540.0,
        )

        assert len(result["candidates"]) == 1
        c = result["candidates"][0]
        assert c["liquidity_ok"] is False
        assert c["short_leg"]["open_interest"] is None  # None passes through, not 0
        assert result["best_candidate"] is None  # no liquid candidates


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
    # ── Unchanged regimes (still pass after data-driven rewrite) ──

    def test_bear_high_returns_bear_call_only(self):
        # bear_call_spread.json: BEAR + MODERATE/HIGH.  No other JSON allows BEAR.
        result = ContextBuilder._select_spread_strategies("BEAR", "HIGH")
        assert result == ["bear_call_spread"]

    def test_crash_returns_empty(self):
        assert ContextBuilder._select_spread_strategies("CRASH", "HIGH") == []

    # ── Data-driven fixes: iron_condor is HIGH only (not MODERATE) ──

    def test_neutral_high_includes_iron_condor_and_iron_butterfly(self):
        # Both iron_condor.json and iron_butterfly.json: NEUTRAL + HIGH.
        result = ContextBuilder._select_spread_strategies("NEUTRAL", "HIGH")
        assert "iron_condor" in result
        assert "iron_butterfly" in result

    def test_neutral_moderate_does_not_include_iron_condor(self):
        # iron_condor.json: iv_environment.allowed = ["HIGH"] only.
        result = ContextBuilder._select_spread_strategies("NEUTRAL", "MODERATE")
        assert "iron_condor" not in result
        assert "bull_put_spread" in result

    def test_neutral_moderate_includes_calendar_spread(self):
        # calendar_spread.json: NEUTRAL + LOW/MODERATE.
        result = ContextBuilder._select_spread_strategies("NEUTRAL", "MODERATE")
        assert "calendar_spread" in result

    def test_neutral_low_returns_calendar_spread_only(self):
        # Only calendar_spread.json allows LOW iv_environment.
        result = ContextBuilder._select_spread_strategies("NEUTRAL", "LOW")
        assert result == ["calendar_spread"]

    def test_bull_low_includes_long_call_vertical_and_calendar(self):
        # long_call_vertical.json: BULL + LOW.  calendar_spread.json: BULL + LOW/MODERATE.
        result = ContextBuilder._select_spread_strategies("BULL", "LOW")
        assert "long_call_vertical" in result
        assert "calendar_spread" in result

    def test_bull_high_does_not_include_calendar_spread(self):
        # calendar_spread.json: iv_environment.allowed = ["LOW", "MODERATE"] only.
        result = ContextBuilder._select_spread_strategies("BULL", "HIGH")
        assert "calendar_spread" not in result

    def test_iron_butterfly_not_returned_for_neutral_moderate(self):
        # iron_butterfly.json: iv_environment.allowed = ["HIGH"] only.
        result = ContextBuilder._select_spread_strategies("NEUTRAL", "MODERATE")
        assert "iron_butterfly" not in result


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


# ── ORATS IV overvalued label ────────────────────────────────


class TestComputeIVOvervaluedLabel:
    def test_overvalued_when_current_above_forecast(self):
        # current_iv = 0.30, iv_fcst = 0.25 → ratio = (0.30-0.25)/0.30 = 0.1667 > 0.05
        ratio, label = _compute_iv_overvalued_label(0.30, 0.25)
        assert label == "OVERVALUED"
        assert ratio == pytest.approx(0.1667, abs=0.001)

    def test_undervalued_when_current_below_forecast(self):
        # current_iv = 0.25, iv_fcst = 0.30 → ratio = (0.25-0.30)/0.25 = -0.20 < -0.05
        ratio, label = _compute_iv_overvalued_label(0.25, 0.30)
        assert label == "UNDERVALUED"
        assert ratio == pytest.approx(-0.20, abs=0.001)

    def test_fair_when_near_forecast(self):
        # current_iv = 0.285, iv_fcst = 0.280 → ratio ≈ 0.0175, within ±0.05
        ratio, label = _compute_iv_overvalued_label(0.285, 0.280)
        assert label == "FAIR"
        assert abs(ratio) < 0.05

    def test_none_when_current_iv_missing(self):
        ratio, label = _compute_iv_overvalued_label(None, 0.25)
        assert ratio is None
        assert label is None

    def test_none_when_forecast_missing(self):
        ratio, label = _compute_iv_overvalued_label(0.30, None)
        assert ratio is None
        assert label is None

    def test_none_when_current_iv_zero(self):
        ratio, label = _compute_iv_overvalued_label(0.0, 0.25)
        assert ratio is None
        assert label is None


# ── ORATS contango label ─────────────────────────────────────


class TestComputeContangoLabel:
    def test_normal_when_contango_positive(self):
        assert _compute_contango_label(0.03) == "NORMAL"
        assert _compute_contango_label(0.021) == "NORMAL"

    def test_flat_when_near_zero(self):
        assert _compute_contango_label(0.02) == "FLAT"
        assert _compute_contango_label(0.0) == "FLAT"
        assert _compute_contango_label(-0.019) == "FLAT"

    def test_backwardation_when_strongly_negative(self):
        assert _compute_contango_label(-0.02) == "BACKWARDATION"
        assert _compute_contango_label(-0.05) == "BACKWARDATION"

    def test_none_when_value_missing(self):
        assert _compute_contango_label(None) is None


# ── ORATS snapshot enrichment ────────────────────────────────


class TestORATSSnapshotEnrichment:
    """Tests for _enrich_with_orats_snapshots() on ContextBuilder."""

    def _make_contracts(self):
        return [
            _make_contract("SPY250502P00530000", 530, "2025-05-02"),
            _make_contract("SPY250502P00525000", 525, "2025-05-02"),
        ]

    def test_orats_covers_all_no_alpaca_called(self, builder):
        """When ORATS returns data for all contracts, Alpaca is not called."""
        orats_snap_530 = _make_snapshot(bid=3.00, ask=3.20, delta=-0.25)
        orats_snap_525 = _make_snapshot(bid=1.80, ask=2.00, delta=-0.15)
        builder._orats_client.get_snapshots_by_strike.return_value = {
            ("2025-05-02", 530.0): orats_snap_530,
            ("2025-05-02", 525.0): orats_snap_525,
        }

        contracts = self._make_contracts()
        result = builder._enrich_with_orats_snapshots(
            contracts=contracts,
            symbol="SPY",
            option_type="put",
            dte_min=1,
            dte_max=60,
            delta_min=0.15,
            delta_max=0.30,
        )

        assert result["SPY250502P00530000"] is orats_snap_530
        assert result["SPY250502P00525000"] is orats_snap_525
        builder.broker.get_option_snapshots.assert_not_called()

    def test_partial_miss_falls_back_to_alpaca(self, builder):
        """When ORATS misses some contracts, Alpaca is called only for those."""
        orats_snap_530 = _make_snapshot(bid=3.00, ask=3.20, delta=-0.25)
        builder._orats_client.get_snapshots_by_strike.return_value = {
            ("2025-05-02", 530.0): orats_snap_530,
            # 525 strike is absent — long leg outside delta range
        }
        alpaca_snap_525 = _make_snapshot(bid=1.80, ask=2.00, delta=-0.15)
        builder.broker.get_option_snapshots.return_value = {
            "SPY250502P00525000": alpaca_snap_525,
        }

        contracts = self._make_contracts()
        result = builder._enrich_with_orats_snapshots(
            contracts=contracts,
            symbol="SPY",
            option_type="put",
            dte_min=1,
            dte_max=60,
            delta_min=0.15,
            delta_max=0.30,
        )

        # ORATS win for the short leg
        assert result["SPY250502P00530000"] is orats_snap_530
        # Alpaca fallback for the long leg
        assert result["SPY250502P00525000"] is alpaca_snap_525
        # Alpaca was called only for the missing contract
        builder.broker.get_option_snapshots.assert_called_once_with(
            ["SPY250502P00525000"], underlying="SPY"
        )


# ── Iron Butterfly candidate building ───────────────────────


def _make_ib_contracts(exp, strikes, opt_type, oi=500):
    """Helper: make a list of chain contracts for iron butterfly tests."""
    return [
        _make_contract(f"{opt_type.upper()}{int(s)}", s, exp, opt_type, oi=oi)
        for s in strikes
    ]


def _make_ib_snapshots(exp, strikes_mids, opt_type):
    """Helper: {symbol: snapshot} for iron butterfly tests.

    ``strikes_mids`` is a list of (strike, mid) tuples.
    """
    snaps = {}
    for strike, mid in strikes_mids:
        sym = f"{opt_type.upper()}{int(strike)}"
        half = mid / 4
        snaps[sym] = _make_snapshot(bid=mid - half, ask=mid + half, delta=-0.20 if opt_type == "put" else 0.20, oi=500)
    return snaps


class TestIronButterflyCandidate:
    """Tests for ContextBuilder.build_iron_butterfly_candidates()."""

    EXP = "2025-06-20"  # ~60 days out — inside dte_min=20/dte_max=45 doesn't matter;
    # DTE filtering is done by the broker chain call, not by the builder.

    def _setup_chain(self, builder, center=540.0, put_wing=5, call_wing=5,
                     put_short_mid=3.50, call_short_mid=3.50,
                     put_long_mid=0.80, call_long_mid=0.80, oi=500):
        """Wire broker mocks for a symmetric butterfly around ``center``."""
        put_strikes = [center - put_wing, center]
        call_strikes = [center, center + call_wing]

        builder.broker.get_option_chain_with_greeks.side_effect = [
            _make_ib_contracts(self.EXP, put_strikes, "put", oi=oi),
            _make_ib_contracts(self.EXP, call_strikes, "call", oi=oi),
        ]

        put_mids = [(center - put_wing, put_long_mid), (center, put_short_mid)]
        call_mids = [(center, call_short_mid), (center + call_wing, call_long_mid)]
        snaps = {
            **_make_ib_snapshots(self.EXP, put_mids, "put"),
            **_make_ib_snapshots(self.EXP, call_mids, "call"),
        }
        builder.broker.get_option_snapshots.return_value = snaps

    def test_atm_strike_on_exact_price(self, builder):
        """When underlying_price is exactly on a listed strike, that strike is ATM."""
        self._setup_chain(builder, center=540.0)
        result = builder.build_iron_butterfly_candidates(
            "SPY", underlying_price=540.0,
        )
        assert result["iron_butterfly_legs"] is not None
        assert result["iron_butterfly_legs"]["center_strike"] == 540.0

    def test_atm_tie_breaking_prefers_higher_strike(self, builder):
        """When price is exactly between two strikes, the higher strike wins."""
        # Strikes: 537.5 and 542.5; price: 540.0 → equidistant → pick 542.5
        exp = self.EXP
        put_strikes = [537.5, 542.5]  # 542.5 will be center; 537.5 is wing
        call_strikes = [542.5, 547.5]  # center=542.5, wing=547.5

        builder.broker.get_option_chain_with_greeks.side_effect = [
            _make_ib_contracts(exp, put_strikes, "put"),
            _make_ib_contracts(exp, call_strikes, "call"),
        ]
        snaps = {
            **_make_ib_snapshots(exp, [(537.5, 1.00), (542.5, 3.50)], "put"),
            **_make_ib_snapshots(exp, [(542.5, 3.50), (547.5, 0.80)], "call"),
        }
        builder.broker.get_option_snapshots.return_value = snaps

        # price 540.0 is equidistant from 537.5 and 542.5; must pick 542.5
        result = builder.build_iron_butterfly_candidates(
            "SPY", underlying_price=540.0,
            put_wing_width=5, call_wing_width=5,
        )
        assert result["iron_butterfly_legs"] is not None
        assert result["iron_butterfly_legs"]["center_strike"] == 542.5

    def test_four_leg_structure_and_credit_math(self, builder):
        """Verifies symmetric legs, total_credit, max_loss, credit_to_width_ratio."""
        center = 540.0
        put_short_mid = 3.50
        call_short_mid = 3.50
        put_long_mid = 0.80
        call_long_mid = 0.80

        self._setup_chain(
            builder, center=center, put_wing=5, call_wing=5,
            put_short_mid=put_short_mid, call_short_mid=call_short_mid,
            put_long_mid=put_long_mid, call_long_mid=call_long_mid,
        )

        result = builder.build_iron_butterfly_candidates(
            "SPY", underlying_price=center, put_wing_width=5, call_wing_width=5,
        )

        assert result["iron_butterfly_legs"] is not None
        legs = result["iron_butterfly_legs"]

        # Short strikes must both equal center
        assert legs["put_short"]["strike"] == center
        assert legs["call_short"]["strike"] == center

        # Wing strikes
        assert legs["put_long"]["strike"] == center - 5
        assert legs["call_long"]["strike"] == center + 5

        expected_credit = round(
            put_short_mid + call_short_mid - put_long_mid - call_long_mid, 4
        )  # 3.50 + 3.50 - 0.80 - 0.80 = 5.40
        assert legs["total_credit"] == expected_credit

        max_wing = 5
        expected_max_loss = round(max_wing * 100 - expected_credit * 100, 2)
        assert legs["max_loss"] == expected_max_loss

        expected_ratio = round(expected_credit / max_wing, 4)
        assert legs["credit_to_width_ratio"] == expected_ratio

    def test_short_strikes_always_match(self, builder):
        """put_short.strike and call_short.strike must equal center_strike."""
        self._setup_chain(builder, center=540.0)
        result = builder.build_iron_butterfly_candidates("SPY", underlying_price=540.0)
        legs = result["iron_butterfly_legs"]
        assert legs is not None
        assert legs["put_short"]["strike"] == legs["call_short"]["strike"]
        assert legs["put_short"]["strike"] == legs["center_strike"]

    def test_empty_chain_returns_none_legs(self, builder):
        """When broker returns no contracts, iron_butterfly_legs and best_candidate are None."""
        builder.broker.get_option_chain_with_greeks.return_value = []
        builder.broker.get_option_snapshots.return_value = {}

        result = builder.build_iron_butterfly_candidates("SPY", underlying_price=540.0)
        assert result["iron_butterfly_legs"] is None
        assert result["best_candidate"] is None
        assert result["candidates"] == []

    def test_min_total_credit_filter_excludes_low_credit_candidate(self, builder):
        """Candidates below min_total_credit (2.00 from JSON) are excluded."""
        # Total credit will be: 1.10 + 1.10 - 0.60 - 0.60 = 1.00 < 2.00
        self._setup_chain(
            builder, center=540.0, put_wing=5, call_wing=5,
            put_short_mid=1.10, call_short_mid=1.10,
            put_long_mid=0.60, call_long_mid=0.60,
        )
        result = builder.build_iron_butterfly_candidates(
            "SPY", underlying_price=540.0, put_wing_width=5, call_wing_width=5,
        )
        assert result["candidates"] == []
        assert result["iron_butterfly_legs"] is None
        assert result["best_candidate"] is None

    def test_missing_leg_skips_expiration(self, builder):
        """If any of the 4 legs is absent from the chain, that expiration is skipped."""
        exp = self.EXP
        # Only provide put chain — call chain is empty → call_short missing
        builder.broker.get_option_chain_with_greeks.side_effect = [
            _make_ib_contracts(exp, [535.0, 540.0], "put"),
            [],  # empty call chain
        ]
        snaps = _make_ib_snapshots(exp, [(535.0, 1.00), (540.0, 3.50)], "put")
        builder.broker.get_option_snapshots.return_value = snaps

        result = builder.build_iron_butterfly_candidates("SPY", underlying_price=540.0)
        assert result["iron_butterfly_legs"] is None

    def test_iron_butterfly_legs_equals_best_candidate(self, builder):
        """iron_butterfly_legs and best_candidate are the same object."""
        self._setup_chain(builder, center=540.0)
        result = builder.build_iron_butterfly_candidates("SPY", underlying_price=540.0)
        assert result["iron_butterfly_legs"] is result["best_candidate"]


# ── Calendar spread routing via build() ─────────────────────


class TestCalendarSpreadInBuild:
    """Verifies that build() routes calendar_spread through build_calendar_candidates
    and stores the result under context["spread_candidates"]["calendar_spread"]."""

    def _build_with_calendar_routing(self, builder):
        """Call build() with enough mocking to reach the spread candidates section,
        with _select_spread_strategies forced to return ["calendar_spread"] and
        build_calendar_candidates returning a fixed result."""
        cal_result = {
            "underlying": "SPY",
            "underlying_price": 540.0,
            "strategy_type": "calendar_spread",
            "candidates": [{"strike": 540, "net_debit": 1.20, "liquidity_ok": True}],
            "best_candidate": {"strike": 540, "net_debit": 1.20},
        }

        with (
            patch("data.context_builder.market_data") as md,
            patch("data.context_builder.derive_market_regime") as drm,
            patch("data.context_builder.compute_portfolio_greeks", return_value={}),
            patch("data.context_builder._fetch_news", return_value=[]),
            patch.object(
                ContextBuilder, "_select_spread_strategies", return_value=["calendar_spread"]
            ),
            patch.object(builder, "build_calendar_candidates", return_value=cal_result),
        ):
            md.get_stock_technicals.return_value = {"current_price": 540.0}
            md.get_vix.return_value = 20.0
            md.get_fear_greed_index.return_value = {"score": 50, "rating": "Neutral"}
            md.get_risk_free_rate.return_value = 0.05
            # orats_summary=None → iv_env="UNKNOWN" (fine; _select_spread is patched)
            md.get_orats_summary.return_value = None
            md.get_orats_cores.return_value = {}
            md.get_orats_monies.return_value = []
            md.get_finnhub_earnings_history.return_value = []
            md.get_finnhub_analyst_data.return_value = {}
            md.get_finnhub_news_sentiment.return_value = {}
            md.get_earnings_calendar.return_value = {"days_to_earnings": 90}
            md.get_ex_dividend_date.return_value = {}
            md.interpret_vix.return_value = "NEUTRAL"

            def _set_regime(ctx):
                ctx["market_regime"] = "NEUTRAL"
                ctx["confirmed_market_regime"] = "NEUTRAL"
                ctx["regime_stable"] = True

            drm.side_effect = _set_regime

            # regime_filter is a MagicMock from fixture; set explicit return values
            builder._regime_filter.get_confirmed_regime.return_value = "NEUTRAL"
            builder._regime_filter.is_stable.return_value = True

            builder.broker.get_account.return_value = {
                "buying_power": 10000, "options_buying_power": 5000,
                "options_approved_level": 2, "options_trading_level": 2,
                "portfolio_value": 20000,
            }
            builder.broker.get_all_positions.return_value = []
            builder.broker.get_orders.return_value = []
            builder.broker.get_option_chain_with_greeks.return_value = []

            builder.journal.format_for_prompt.return_value = ""
            builder.journal.format_stats_for_prompt.return_value = ""
            builder.journal.format_skip_history_for_prompt.return_value = ""
            builder.journal.format_rejections_for_prompt.return_value = ""

            context = builder.build("SPY", "IDLE")

        return context, cal_result

    def test_build_includes_calendar_spread_in_spread_candidates(self, builder):
        """build() populates context['spread_candidates']['calendar_spread']."""
        context, cal_result = self._build_with_calendar_routing(builder)
        assert "spread_candidates" in context
        assert "calendar_spread" in context["spread_candidates"]

    def test_build_calendar_best_candidate_is_correct(self, builder):
        """context['spread_candidates']['calendar_spread']['best_candidate'] matches."""
        context, cal_result = self._build_with_calendar_routing(builder)
        cs = context["spread_candidates"]["calendar_spread"]
        assert cs["best_candidate"] == cal_result["best_candidate"]

    def test_build_calls_build_calendar_candidates_with_put_option_type(self, builder):
        """build() passes option_type='put' (from calendar_spread.json) to builder."""
        with (
            patch("data.context_builder.market_data") as md,
            patch("data.context_builder.derive_market_regime") as drm,
            patch("data.context_builder.compute_portfolio_greeks", return_value={}),
            patch("data.context_builder._fetch_news", return_value=[]),
            patch.object(
                ContextBuilder, "_select_spread_strategies", return_value=["calendar_spread"]
            ),
            patch.object(
                builder, "build_calendar_candidates", return_value={
                    "underlying": "SPY", "underlying_price": 540.0,
                    "strategy_type": "calendar_spread", "candidates": [],
                    "best_candidate": None,
                }
            ) as mock_cal,
        ):
            md.get_stock_technicals.return_value = {"current_price": 540.0}
            md.get_vix.return_value = 20.0
            md.get_fear_greed_index.return_value = {"score": 50}
            md.get_risk_free_rate.return_value = 0.05
            md.get_orats_summary.return_value = None
            md.get_orats_cores.return_value = {}
            md.get_orats_monies.return_value = []
            md.get_finnhub_earnings_history.return_value = []
            md.get_finnhub_analyst_data.return_value = {}
            md.get_finnhub_news_sentiment.return_value = {}
            md.get_earnings_calendar.return_value = {"days_to_earnings": 90}
            md.get_ex_dividend_date.return_value = {}
            md.interpret_vix.return_value = "NEUTRAL"
            drm.side_effect = lambda ctx: ctx.update({
                "market_regime": "NEUTRAL",
                "confirmed_market_regime": "NEUTRAL",
                "regime_stable": True,
            })
            builder._regime_filter.get_confirmed_regime.return_value = "NEUTRAL"
            builder._regime_filter.is_stable.return_value = True
            builder.broker.get_account.return_value = {
                "buying_power": 10000, "options_buying_power": 5000,
                "options_approved_level": 2, "options_trading_level": 2,
                "portfolio_value": 20000,
            }
            builder.broker.get_all_positions.return_value = []
            builder.broker.get_orders.return_value = []
            builder.broker.get_option_chain_with_greeks.return_value = []
            builder.journal.format_for_prompt.return_value = ""
            builder.journal.format_stats_for_prompt.return_value = ""
            builder.journal.format_skip_history_for_prompt.return_value = ""
            builder.journal.format_rejections_for_prompt.return_value = ""

            builder.build("SPY", "IDLE")

        # Verify option_type="put" was passed (from calendar_spread.json)
        _, kwargs = mock_cal.call_args
        assert kwargs.get("option_type") == "put"
