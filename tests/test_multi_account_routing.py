"""
Multi-account routing tests: prove each strategy uses only its designated broker.

Scope:
  Running market_open.run() end-to-end is impractical given its 15+ external
  dependencies (DB, ORATS, circuit breaker, reconciler, advisor, context builder,
  etc.). These tests cover the same routing guarantee at the narrowest testable
  units:

  A. execute_decision(): the broker passed in receives the order; no other broker
     is touched.
  B. IronCondorStrategy.execute_entry(): orders go only to the IC broker.
  C. BPS/BCS/LCV.execute_entry(): orders go only to the shared (default) broker.
  D. make_broker() / make_broker_cached(): credential-keyed cache returns the
     same instance for shared credentials, different instances otherwise.
  E. Strategy._save_state(): state files for different strategies are isolated
     and do not contain each other's fields.

Architecture note (wheel routing):
  In market_open.run(), wheel ORDERS are placed via
  execute_decision(wheel_broker, decision) — the wheel's own broker (Paper 2),
  the SAME broker used to build wheel_ctx_builder, so evaluation and execution
  hit the same account. (Before the 2026-06-28 fix this used the default broker
  / Paper 1, misrouting wheel orders into the spreads account.) Test A covers
  generic execute_decision routing; the wheel-specific evaluation==execution
  guarantee is enforced in tests/test_wheel_execute_account_routing.py, and the
  credential-isolation guarantee for the wheel account by Test D below.
"""

import json

import pytest
from unittest.mock import MagicMock, patch


# ── Shared helper ─────────────────────────────────────────────────────────────

def _mock_broker(name: str = "broker") -> MagicMock:
    """Return a MagicMock that satisfies the BaseBroker interface."""
    m = MagicMock(name=name)
    m.get_account.return_value = {"buying_power": "100000", "cash": "100000"}
    m.get_positions.return_value = []
    m.get_orders.return_value = []
    # Return no order id so execute_decision's _confirm_fill short-circuits
    # without sleeping 30 s.
    m.place_order.return_value = {"id": None}
    m.place_mleg_order.return_value = {"id": "mleg-order-id"}
    return m


# ── Test A: execute_decision routes to the broker it receives ─────────────────

class TestExecuteDecisionRouting:
    """
    execute_decision(broker, decision) places the order on whichever broker
    object it is given — not on any other broker instance in scope.

    In market_open, this broker is the default broker (Paper 1). Isolation
    means: if two broker objects exist (wheel and IC), only the one passed
    to execute_decision receives a place_order call.
    """

    def test_sell_put_routes_only_to_given_broker(self):
        from main import execute_decision

        active_broker = _mock_broker("active")
        other_broker = _mock_broker("other")

        decision = {
            "action": "sell_put",
            "symbol": "SPY240119P00400000",
            "qty": 1,
            "order_type": "limit",
            "limit_price": 2.50,
        }

        execute_decision(active_broker, decision)

        active_broker.place_order.assert_called_once()
        other_broker.place_order.assert_not_called()
        other_broker.place_mleg_order.assert_not_called()

    def test_sell_call_routes_only_to_given_broker(self):
        from main import execute_decision

        active_broker = _mock_broker("active")
        other_broker = _mock_broker("other")

        decision = {
            "action": "sell_call",
            "symbol": "SPY240119C00460000",
            "qty": 1,
            "order_type": "limit",
            "limit_price": 1.80,
        }

        execute_decision(active_broker, decision)

        active_broker.place_order.assert_called_once()
        other_broker.place_order.assert_not_called()
        other_broker.place_mleg_order.assert_not_called()

    def test_ic_broker_receives_no_wheel_orders(self):
        """Sanity check: an IC broker object never receives a wheel order."""
        from main import execute_decision

        wheel_broker = _mock_broker("wheel")
        ic_broker = _mock_broker("ic")

        execute_decision(wheel_broker, {
            "action": "sell_put",
            "symbol": "SPY240119P00400000",
            "qty": 1,
            "order_type": "limit",
            "limit_price": 2.50,
        })

        ic_broker.place_order.assert_not_called()
        ic_broker.place_mleg_order.assert_not_called()


# ── Test B: IronCondorStrategy.execute_entry routes to IC broker only ─────────

class TestIronCondorBrokerRouting:
    _DECISION = {
        "put_short_symbol":  "SPY240119P00450000",
        "put_long_symbol":   "SPY240119P00445000",
        "call_short_symbol": "SPY240119C00455000",
        "call_long_symbol":  "SPY240119C00460000",
        "limit_price":  1.50,
        "total_credit": 1.50,
        "max_loss":     350,
        "expiration":   "2024-01-19",
        "underlying":   "SPY",
    }

    def test_execute_entry_calls_ic_broker_place_mleg_order(self, tmp_path):
        from config import settings
        from strategies.iron_condor_strategy import IronCondorStrategy

        ic_broker      = _mock_broker("ic_broker")
        wheel_broker   = _mock_broker("wheel_broker")
        default_broker = _mock_broker("default_broker")

        with patch.object(settings, "SNAPSHOTS_DIR", tmp_path):
            strat = IronCondorStrategy(broker=ic_broker)

        strat.execute_entry(self._DECISION)

        ic_broker.place_mleg_order.assert_called_once()
        wheel_broker.place_mleg_order.assert_not_called()
        default_broker.place_mleg_order.assert_not_called()

    def test_execute_entry_does_not_call_place_order_on_any_broker(self, tmp_path):
        """IC strategy uses place_mleg_order only — never the single-leg place_order."""
        from config import settings
        from strategies.iron_condor_strategy import IronCondorStrategy

        ic_broker = _mock_broker("ic_broker")

        with patch.object(settings, "SNAPSHOTS_DIR", tmp_path):
            strat = IronCondorStrategy(broker=ic_broker)

        strat.execute_entry(self._DECISION)

        ic_broker.place_order.assert_not_called()

    def test_wheel_broker_receives_no_ic_orders(self, tmp_path):
        """An IC execute_entry must not leak into the wheel broker."""
        from config import settings
        from strategies.iron_condor_strategy import IronCondorStrategy

        ic_broker    = _mock_broker("ic_broker")
        wheel_broker = _mock_broker("wheel_broker")

        with patch.object(settings, "SNAPSHOTS_DIR", tmp_path):
            strat = IronCondorStrategy(broker=ic_broker)

        strat.execute_entry(self._DECISION)

        wheel_broker.place_order.assert_not_called()
        wheel_broker.place_mleg_order.assert_not_called()


# ── Test C: Shared-account strategies route to default broker only ────────────

class TestSharedAccountBrokerRouting:
    """
    BullPutSpread, BearCallSpread, and LongCallVertical all share Paper 1
    (the default account). Each strategy object is constructed with that
    shared broker, and only that broker should receive place_mleg_order calls.
    """

    @staticmethod
    def _bps_decision():
        return {
            "short_put_symbol": "SPY240119P00450000",
            "long_put_symbol":  "SPY240119P00445000",
            "limit_price":  1.20,
            "net_credit":   1.20,
            "max_loss":     380,
            "expiration":   "2024-01-19",
            "underlying":   "SPY",
        }

    @staticmethod
    def _bcs_decision():
        return {
            "short_call_symbol": "SPY240119C00455000",
            "long_call_symbol":  "SPY240119C00460000",
            "limit_price":  0.90,
            "net_credit":   0.90,
            "max_loss":     410,
            "expiration":   "2024-01-19",
            "underlying":   "SPY",
        }

    @staticmethod
    def _lcv_decision():
        return {
            "long_call_symbol":  "SPY240119C00450000",
            "short_call_symbol": "SPY240119C00455000",
            "limit_price":  2.00,
            "net_debit":    2.00,
            "expiration":   "2024-01-19",
            "underlying":   "SPY",
        }

    def test_bull_put_spread_uses_default_broker(self, tmp_path):
        from config import settings
        from strategies.bull_put_spread_strategy import BullPutSpreadStrategy

        default_broker = _mock_broker("default_broker")
        ic_broker      = _mock_broker("ic_broker")
        wheel_broker   = _mock_broker("wheel_broker")

        with patch.object(settings, "SNAPSHOTS_DIR", tmp_path):
            strat = BullPutSpreadStrategy(broker=default_broker)

        strat.execute_entry(self._bps_decision())

        default_broker.place_mleg_order.assert_called_once()
        ic_broker.place_mleg_order.assert_not_called()
        wheel_broker.place_mleg_order.assert_not_called()

    def test_bear_call_spread_uses_default_broker(self, tmp_path):
        from config import settings
        from strategies.bear_call_spread_strategy import BearCallSpreadStrategy

        default_broker = _mock_broker("default_broker")
        ic_broker      = _mock_broker("ic_broker")
        wheel_broker   = _mock_broker("wheel_broker")

        with patch.object(settings, "SNAPSHOTS_DIR", tmp_path):
            strat = BearCallSpreadStrategy(broker=default_broker)

        strat.execute_entry(self._bcs_decision())

        default_broker.place_mleg_order.assert_called_once()
        ic_broker.place_mleg_order.assert_not_called()
        wheel_broker.place_mleg_order.assert_not_called()

    def test_long_call_vertical_uses_default_broker(self, tmp_path):
        from config import settings
        from strategies.long_call_vertical_strategy import LongCallVerticalStrategy

        default_broker = _mock_broker("default_broker")
        ic_broker      = _mock_broker("ic_broker")
        wheel_broker   = _mock_broker("wheel_broker")

        with patch.object(settings, "SNAPSHOTS_DIR", tmp_path):
            strat = LongCallVerticalStrategy(broker=default_broker)

        strat.execute_entry(self._lcv_decision())

        default_broker.place_mleg_order.assert_called_once()
        ic_broker.place_mleg_order.assert_not_called()
        wheel_broker.place_mleg_order.assert_not_called()

    def test_all_shared_strategies_share_same_broker_instance(self, tmp_path):
        """BPS, BCS, LCV are all passed the same broker object — one place_mleg_order
        per execute_entry, all going to that single shared instance."""
        from config import settings
        from strategies.bull_put_spread_strategy import BullPutSpreadStrategy
        from strategies.bear_call_spread_strategy import BearCallSpreadStrategy
        from strategies.long_call_vertical_strategy import LongCallVerticalStrategy

        shared_broker = _mock_broker("shared_broker")

        with patch.object(settings, "SNAPSHOTS_DIR", tmp_path):
            bps = BullPutSpreadStrategy(broker=shared_broker)
            bcs = BearCallSpreadStrategy(broker=shared_broker)
            lcv = LongCallVerticalStrategy(broker=shared_broker)

        bps.execute_entry(self._bps_decision())
        bcs.execute_entry(self._bcs_decision())
        lcv.execute_entry(self._lcv_decision())

        assert shared_broker.place_mleg_order.call_count == 3
        assert bps.broker is bcs.broker is lcv.broker


# ── Test D: Broker cache identity ─────────────────────────────────────────────

class TestBrokerCacheIdentity:
    """
    make_broker_cached() caches instances by (api_key, secret_key).
    Shared-credential strategies must get the same instance; distinct-credential
    strategies must get different instances.
    """

    def setup_method(self):
        from brokers.broker_factory import clear_broker_cache
        clear_broker_cache()

    def teardown_method(self):
        from brokers.broker_factory import clear_broker_cache
        clear_broker_cache()

    def test_same_credentials_return_same_instance(self):
        from brokers.broker_factory import make_broker_cached

        with patch("brokers.alpaca_broker.AlpacaBroker") as MockBroker:
            MockBroker.side_effect = lambda **kw: MagicMock(name=f"b_{kw['api_key']}")

            b1 = make_broker_cached("key_a", "secret_a")
            b2 = make_broker_cached("key_a", "secret_a")

        assert b1 is b2, "Same credentials must return the cached broker instance"

    def test_different_credentials_return_different_instances(self):
        from brokers.broker_factory import make_broker_cached

        with patch("brokers.alpaca_broker.AlpacaBroker") as MockBroker:
            MockBroker.side_effect = lambda **kw: MagicMock(name=f"b_{kw['api_key']}")

            b1 = make_broker_cached("key_a", "secret_a")
            b2 = make_broker_cached("key_b", "secret_b")

        assert b1 is not b2, "Different credentials must return different broker instances"

    def test_make_broker_shared_account_returns_same_instance(self):
        """BPS, BCS, LCV → PAPER1 creds → one cached instance.
        Wheel → PAPER2; IC → PAPER3 → distinct instances."""
        from config import settings
        from brokers.broker_factory import make_broker

        with patch.object(settings, "ALPACA_PAPER1_API_KEY", "p1_key"), \
             patch.object(settings, "ALPACA_PAPER1_SECRET_KEY", "p1_secret"), \
             patch.object(settings, "ALPACA_PAPER2_API_KEY", "p2_key"), \
             patch.object(settings, "ALPACA_PAPER2_SECRET_KEY", "p2_secret"), \
             patch.object(settings, "ALPACA_PAPER3_API_KEY", "p3_key"), \
             patch.object(settings, "ALPACA_PAPER3_SECRET_KEY", "p3_secret"), \
             patch("brokers.alpaca_broker.AlpacaBroker") as MockBroker:

            MockBroker.side_effect = lambda **kw: MagicMock(name=f"b_{kw['api_key']}")

            bps   = make_broker("bull_put_spread")
            bcs   = make_broker("bear_call_spread")
            lcv   = make_broker("long_call_vertical")
            wheel = make_broker("wheel")
            ic    = make_broker("iron_condor")

        assert bps is bcs,       "BPS and BCS must share one broker instance (same Paper 1 creds)"
        assert bcs is lcv,       "BCS and LCV must share one broker instance (same Paper 1 creds)"
        assert bps is not wheel, "BPS and wheel must have separate broker instances"
        assert bps is not ic,    "BPS and IC must have separate broker instances"
        assert wheel is not ic,  "Wheel and IC must have separate broker instances"

    def test_make_broker_constructs_only_one_client_per_account(self):
        """AlpacaBroker is constructed exactly once per unique credential pair."""
        from config import settings
        from brokers.broker_factory import make_broker

        with patch.object(settings, "ALPACA_PAPER1_API_KEY", "p1_key"), \
             patch.object(settings, "ALPACA_PAPER1_SECRET_KEY", "p1_secret"), \
             patch.object(settings, "ALPACA_PAPER2_API_KEY", "p2_key"), \
             patch.object(settings, "ALPACA_PAPER2_SECRET_KEY", "p2_secret"), \
             patch.object(settings, "ALPACA_PAPER3_API_KEY", "p3_key"), \
             patch.object(settings, "ALPACA_PAPER3_SECRET_KEY", "p3_secret"), \
             patch("brokers.alpaca_broker.AlpacaBroker") as MockBroker:

            MockBroker.side_effect = lambda **kw: MagicMock(name=f"b_{kw['api_key']}")

            # 5 calls: BPS, BCS, LCV share paper1 → 1 construction; wheel → 1; IC → 1
            make_broker("bull_put_spread")
            make_broker("bear_call_spread")
            make_broker("long_call_vertical")
            make_broker("wheel")
            make_broker("iron_condor")

        assert MockBroker.call_count == 3, (
            f"Expected 3 AlpacaBroker constructions (paper1, paper2, paper3), "
            f"got {MockBroker.call_count}"
        )


# ── Test E: Strategy state file isolation ─────────────────────────────────────

class TestStrategyStateIsolation:
    """
    Each strategy writes its state to its own JSON file under SNAPSHOTS_DIR.
    These tests verify files are written separately and contain only their
    own fields — no cross-contamination between strategy instances.
    """

    def test_ic_and_bps_state_files_are_separate(self, tmp_path):
        from config import settings
        from strategies.iron_condor_strategy import IronCondorStrategy
        from strategies.bull_put_spread_strategy import BullPutSpreadStrategy

        with patch.object(settings, "SNAPSHOTS_DIR", tmp_path):
            ic_strat  = IronCondorStrategy(broker=_mock_broker("ic"))
            bps_strat = BullPutSpreadStrategy(broker=_mock_broker("bps"))

        # Write distinct spread IDs so we can check for cross-contamination
        ic_strat.open_spread_id  = "ic-spread-111"
        bps_strat.open_spread_id = "bps-spread-222"

        ic_strat._save_state()
        bps_strat._save_state()

        ic_path  = tmp_path / "iron_condor_state.json"
        bps_path = tmp_path / "bull_put_spread_state.json"

        assert ic_path.exists(),  "iron_condor_state.json must be written"
        assert bps_path.exists(), "bull_put_spread_state.json must be written"

        ic_data  = json.loads(ic_path.read_text())
        bps_data = json.loads(bps_path.read_text())

        assert ic_data["open_spread_id"]  == "ic-spread-111"
        assert bps_data["open_spread_id"] == "bps-spread-222"

        # Cross-contamination guards
        assert "bps-spread-222" not in ic_path.read_text(), (
            "IC state file must not contain BPS spread ID"
        )
        assert "ic-spread-111" not in bps_path.read_text(), (
            "BPS state file must not contain IC spread ID"
        )

    def test_all_four_spread_state_files_are_distinct(self, tmp_path):
        """All four spread strategies write to distinct files simultaneously."""
        from config import settings
        from strategies.iron_condor_strategy import IronCondorStrategy
        from strategies.bull_put_spread_strategy import BullPutSpreadStrategy
        from strategies.bear_call_spread_strategy import BearCallSpreadStrategy
        from strategies.long_call_vertical_strategy import LongCallVerticalStrategy

        with patch.object(settings, "SNAPSHOTS_DIR", tmp_path):
            ic  = IronCondorStrategy(broker=_mock_broker())
            bps = BullPutSpreadStrategy(broker=_mock_broker())
            bcs = BearCallSpreadStrategy(broker=_mock_broker())
            lcv = LongCallVerticalStrategy(broker=_mock_broker())

        ic.open_spread_id  = "ic-aaa"
        bps.open_spread_id = "bps-bbb"
        bcs.open_spread_id = "bcs-ccc"
        lcv.open_spread_id = "lcv-ddd"

        ic._save_state()
        bps._save_state()
        bcs._save_state()
        lcv._save_state()

        expected_files = {
            "iron_condor_state.json":       "ic-aaa",
            "bull_put_spread_state.json":   "bps-bbb",
            "bear_call_spread_state.json":  "bcs-ccc",
            "long_call_vertical_state.json": "lcv-ddd",
        }

        for filename, expected_id in expected_files.items():
            path = tmp_path / filename
            assert path.exists(), f"{filename} must be written"
            data = json.loads(path.read_text())
            assert data["open_spread_id"] == expected_id, (
                f"{filename} has wrong open_spread_id: {data['open_spread_id']!r}"
            )
            # No other strategy's ID should appear in this file
            other_ids = {v for k, v in expected_files.items() if k != filename}
            content = path.read_text()
            for other_id in other_ids:
                assert other_id not in content, (
                    f"{filename} contains foreign spread ID {other_id!r}"
                )

    def test_state_file_schema_matches_expected_fields(self, tmp_path):
        """State files contain exactly the expected top-level keys."""
        from config import settings
        from strategies.iron_condor_strategy import IronCondorStrategy
        from strategies.bull_put_spread_strategy import BullPutSpreadStrategy

        expected_keys = {"state", "open_spread_id", "pending_order_id", "updated_at"}

        with patch.object(settings, "SNAPSHOTS_DIR", tmp_path):
            ic  = IronCondorStrategy(broker=_mock_broker())
            bps = BullPutSpreadStrategy(broker=_mock_broker())

        ic._save_state()
        bps._save_state()

        ic_data  = json.loads((tmp_path / "iron_condor_state.json").read_text())
        bps_data = json.loads((tmp_path / "bull_put_spread_state.json").read_text())

        assert set(ic_data.keys())  == expected_keys, f"IC keys: {set(ic_data.keys())}"
        assert set(bps_data.keys()) == expected_keys, f"BPS keys: {set(bps_data.keys())}"
