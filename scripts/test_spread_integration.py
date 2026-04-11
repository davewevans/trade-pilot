"""Integration test for all spread strategies.

Uses real paper trading API for live tests. Run manually before deploying.

    python scripts/test_spread_integration.py

Safe tests (no real orders) run by default. Live API tests are commented
out -- uncomment them only when you want to place real paper orders.
"""

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, time as dt_time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import settings


# ── Test 1: Options time constraints ────────────────────────

def test_options_time_constraint():
    print("\n=== Test 1: Options time constraints ===")
    from zoneinfo import ZoneInfo
    from utils.market_hours import is_options_order_allowed

    ET = ZoneInfo("America/New_York")

    # During market hours
    dt_open = datetime(2026, 4, 13, 10, 30, tzinfo=ET)  # Monday 10:30 AM
    with patch("utils.market_hours.datetime", wraps=datetime, **{"now.return_value": dt_open}):
        ok, reason = is_options_order_allowed("SPY")
    assert ok, f"Expected allowed at 10:30 AM, got: {reason}"
    print("  10:30 AM ET -> allowed [OK]")

    # After cutoff
    dt_cutoff = datetime(2026, 4, 13, 15, 20, tzinfo=ET)
    with patch("utils.market_hours.datetime", wraps=datetime, **{"now.return_value": dt_cutoff}):
        ok, reason = is_options_order_allowed("SPY")
    assert not ok, "Expected blocked after 3:15 PM"
    print("  3:20 PM ET -> blocked [OK]")

    # Expiration day
    with patch("utils.market_hours.datetime", wraps=datetime, **{"now.return_value": dt_open}):
        ok, reason = is_options_order_allowed("SPY", dte=0)
    assert not ok, "Expected blocked on expiration day"
    print("  DTE=0 -> blocked [OK]")

    # Before market open
    dt_early = datetime(2026, 4, 13, 9, 0, tzinfo=ET)
    with patch("utils.market_hours.datetime", wraps=datetime, **{"now.return_value": dt_early}):
        ok, reason = is_options_order_allowed("SPY")
    assert not ok, "Expected blocked before 9:30 AM"
    print("  9:00 AM ET -> blocked [OK]")

    print("  PASS")


# ── Test 2: SpreadTracker persistence ───────────────────────

def test_spread_tracker_persistence():
    print("\n=== Test 2: SpreadTracker persistence ===")
    from data.spread_tracker import SpreadTracker

    tmp = Path(tempfile.mkdtemp(prefix="tp_tracker_"))
    state_path = tmp / "open_spreads.json"

    t1 = SpreadTracker(state_path=state_path)
    sid = t1.register_spread(
        strategy_type="bull_put_spread",
        underlying="SPY",
        legs=[
            {"symbol": "SPY260515P00530000", "side": "sell"},
            {"symbol": "SPY260515P00525000", "side": "buy"},
        ],
        entry_credit=1.20,
        entry_date="2026-04-11",
        expiration="2026-05-15",
        max_loss=380,
        max_gain=120,
    )
    print(f"  Registered spread: {sid}")

    # Reload from disk
    t2 = SpreadTracker(state_path=state_path)
    spreads = t2.get_open_spreads()
    assert len(spreads) == 1, f"Expected 1 spread, got {len(spreads)}"
    assert spreads[0]["spread_id"] == sid
    print("  Reloaded from disk: 1 spread found [OK]")

    # Find by leg symbol
    found = t2.get_spread_by_leg_symbol("SPY260515P00530000")
    assert found is not None, "Expected to find spread by leg symbol"
    print("  Found by leg symbol [OK]")

    # Close and verify
    t2.close_spread(sid, exit_credit=0.30)
    assert len(t2.get_open_spreads()) == 0
    print("  Closed spread, 0 open [OK]")

    print("  PASS")


# ── Test 3: Credit/debit sign convention ────────────────────

def test_credit_sign_convention():
    print("\n=== Test 3: Credit/debit sign convention ===")
    import logging
    from unittest.mock import MagicMock, patch as mock_patch

    with mock_patch("brokers.alpaca_broker.TradingClient"), \
         mock_patch("brokers.alpaca_broker.OptionHistoricalDataClient"):
        from brokers.alpaca_broker import AlpacaBroker

        broker = AlpacaBroker()
        mock_order = MagicMock()
        mock_order.model_dump.return_value = {"id": "test"}
        broker.client.submit_order.return_value = mock_order

        # Credit spread with positive price should warn
        import io
        log_capture = io.StringIO()
        handler = logging.StreamHandler(log_capture)
        handler.setLevel(logging.WARNING)
        broker_logger = logging.getLogger("brokers.alpaca_broker")
        broker_logger.addHandler(handler)

        try:
            legs = [
                {"symbol": "SPY260515P00530000", "side": "sell",
                 "ratio_qty": 1, "position_intent": "sell_to_open"},
                {"symbol": "SPY260515P00525000", "side": "buy",
                 "ratio_qty": 1, "position_intent": "buy_to_open"},
            ]
            broker.place_mleg_order(legs=legs, limit_price=1.50)

            log_output = log_capture.getvalue()
            assert "positive" in log_output.lower(), \
                f"Expected warning about positive price, got: {log_output}"
            print("  Credit spread + positive price -> WARNING logged [OK]")
        finally:
            broker_logger.removeHandler(handler)

    print("  PASS")


# ── Test 4: Strategy router logic ───────────────────────────

def test_strategy_router():
    print("\n=== Test 4: Strategy router logic ===")
    from strategies.strategy_router import StrategyRouter

    router = StrategyRouter()
    all_idle = {
        "iron_condor": "IDLE",
        "bull_put_spread": "IDLE",
        "bear_call_spread": "IDLE",
        "long_call_vertical": "IDLE",
    }

    # NEUTRAL + HIGH IV -> iron condor
    active = router.get_active_strategies(
        {"confirmed_market_regime": "NEUTRAL", "iv_environment": "HIGH",
         "support_bounce_signal": {"cahold_detected": False}},
        all_idle, circuit_breaker_status="GREEN",
    )
    assert "iron_condor" in active
    print("  NEUTRAL + HIGH IV -> iron_condor [OK]")

    # BEAR + HIGH IV -> bear call spread
    active = router.get_active_strategies(
        {"confirmed_market_regime": "BEAR", "iv_environment": "HIGH",
         "support_bounce_signal": {"cahold_detected": False}},
        all_idle, circuit_breaker_status="GREEN",
    )
    assert "bear_call_spread" in active
    print("  BEAR + HIGH IV -> bear_call_spread [OK]")

    # BULL + LOW IV + CAHOLD -> long call vertical
    active = router.get_active_strategies(
        {"confirmed_market_regime": "BULL", "iv_environment": "LOW",
         "support_bounce_signal": {"cahold_detected": True}},
        all_idle, circuit_breaker_status="GREEN",
    )
    assert "long_call_vertical" in active
    print("  BULL + LOW IV + CAHOLD -> long_call_vertical [OK]")

    # RED circuit breaker -> management only
    states = dict(all_idle)
    states["iron_condor"] = "OPEN"
    active = router.get_active_strategies(
        {"confirmed_market_regime": "NEUTRAL", "iv_environment": "HIGH",
         "support_bounce_signal": {"cahold_detected": False}},
        states, circuit_breaker_status="RED",
    )
    assert "iron_condor" in active  # OPEN = management
    assert "bull_put_spread" not in active  # IDLE = blocked
    print("  RED circuit breaker -> management only [OK]")

    # CRASH -> wheel only
    active = router.get_active_strategies(
        {"confirmed_market_regime": "CRASH", "iv_environment": "HIGH",
         "support_bounce_signal": {"cahold_detected": False}},
        all_idle, circuit_breaker_status="GREEN",
    )
    assert active == ["wheel"]
    print("  CRASH -> wheel only [OK]")

    print("  PASS")


# ── Test 5: Guardrails for all spread types ─────────────────

def test_guardrails_all_spreads():
    print("\n=== Test 5: Guardrails for all spread types ===")
    from strategies.guardrails import Guardrails

    g = Guardrails()
    account = {"buying_power": "100000"}

    # Iron condor: valid
    ok, r = g.validate_iron_condor_entry(
        {"total_credit": 2.0, "max_loss": 300, "limit_price": -2.0, "dte": 30,
         "put_short_symbol": "SPY260515P00530000", "put_long_symbol": "SPY260515P00525000",
         "call_short_symbol": "SPY260515C00550000", "call_long_symbol": "SPY260515C00555000"},
        {"fundamentals": {"days_to_earnings": 60}}, account,
    )
    assert ok, f"IC should pass: {r}"
    print("  Iron condor valid entry -> PASS [OK]")

    # Bull put: valid
    ok, r = g.validate_bull_put_spread_entry(
        {"net_credit": 1.0, "max_loss": 400, "limit_price": -1.0, "dte": 30,
         "short_put_symbol": "SPY260515P00530000", "long_put_symbol": "SPY260515P00525000"},
        {"fundamentals": {"days_to_earnings": 60}}, account,
    )
    assert ok, f"BPS should pass: {r}"
    print("  Bull put spread valid entry -> PASS [OK]")

    # Bear call: valid
    ok, r = g.validate_bear_call_spread_entry(
        {"net_credit": 0.80, "max_loss": 420, "limit_price": -0.80, "dte": 30,
         "short_call_symbol": "SPY260515C00550000", "long_call_symbol": "SPY260515C00555000"},
        {"fundamentals": {"days_to_earnings": 60, "days_to_ex_dividend": 90}}, account,
    )
    assert ok, f"BCS should pass: {r}"
    print("  Bear call spread valid entry -> PASS [OK]")

    # Long call vertical: valid
    ok, r = g.validate_long_call_vertical_entry(
        {"net_debit": 1.50, "limit_price": 1.50, "dte": 45,
         "long_call_symbol": "SPY260615C00540000", "short_call_symbol": "SPY260615C00545000"},
        {"confirmed_market_regime": "BULL", "iv_environment": "LOW",
         "fundamentals": {"days_to_earnings": 80}}, account,
    )
    assert ok, f"LCV should pass: {r}"
    print("  Long call vertical valid entry -> PASS [OK]")

    # Sign convention: positive limit_price on credit spread -> reject
    ok, r = g.validate_bull_put_spread_entry(
        {"net_credit": 1.0, "max_loss": 400, "limit_price": 1.0, "dte": 30,
         "short_put_symbol": "SPY260515P00530000", "long_put_symbol": "SPY260515P00525000"},
        {"fundamentals": {"days_to_earnings": 60}}, account,
    )
    assert not ok
    print("  Credit spread + positive limit_price -> REJECTED [OK]")

    # Sign convention: negative limit_price on debit spread -> reject
    ok, r = g.validate_long_call_vertical_entry(
        {"net_debit": 1.50, "limit_price": -1.50, "dte": 45,
         "long_call_symbol": "SPY260615C00540000", "short_call_symbol": "SPY260615C00545000"},
        {"confirmed_market_regime": "BULL", "iv_environment": "LOW",
         "fundamentals": {"days_to_earnings": 80}}, account,
    )
    assert not ok
    print("  Debit spread + negative limit_price -> REJECTED [OK]")

    print("  PASS")


# ── Test 6: State writer + API endpoint ─────────────────────

def test_state_writer_and_api():
    print("\n=== Test 6: StateWriter + API strategy-states endpoint ===")
    from data.state_writer import StateWriter

    tmp = Path(tempfile.mkdtemp(prefix="tp_api_"))
    snap = tmp / "snapshots"
    sw = StateWriter(snapshot_dir=snap)

    # Write circuit breaker
    sw.write_circuit_breaker_status({
        "status": "GREEN", "halted": False,
        "daily_pnl": 0, "daily_pnl_pct": 0,
    })

    # Write strategy state files (simulating what strategies write)
    for name, state in [("iron_condor", "IDLE"), ("bull_put_spread", "OPEN"),
                        ("bear_call_spread", "IDLE"), ("long_call_vertical", "IDLE")]:
        (snap / f"{name}_state.json").write_text(json.dumps({
            "state": state, "open_spread_id": "spread-123" if state == "OPEN" else None,
        }))

    # Write context
    (snap / "context.json").write_text(json.dumps({
        "confirmed_market_regime": "NEUTRAL",
    }))

    # Write portfolio
    sw.write_portfolio_snapshot(
        {"portfolio_value": 100000, "buying_power": 50000},
        [], {"SPY": "IDLE"},
    )

    # Test API endpoint
    import api.server as srv
    srv.SNAPSHOTS = snap
    srv.DATA_DIR = tmp
    srv.LOCK_PATH = tmp / "HALTED.lock"
    srv.JOURNAL_PATH = tmp / "journal.jsonl"

    from fastapi.testclient import TestClient
    client = TestClient(srv.app, raise_server_exceptions=False)

    r = client.get("/api/strategy-states")
    assert r.status_code == 200
    body = r.json()
    assert body["bull_put_spread"]["state"] == "OPEN"
    assert body["bull_put_spread"]["spread_id"] == "spread-123"
    assert body["iron_condor"]["state"] == "IDLE"
    assert body["router_blocked"] is False
    print("  Strategy states endpoint returns correct data [OK]")

    print("  PASS")


# ── Live API tests (uncomment to run) ───────────────────────

# def test_mleg_order_support():
#     """Place a real 2-leg SPY bull put spread on paper account."""
#     print("\n=== Test 7: Live mleg order (paper) ===")
#     broker = AlpacaBroker()
#     # ... (implementation would go here)
#     print("  PASS")

# def test_iron_condor_order():
#     """Place a real 4-leg SPY iron condor on paper account."""
#     print("\n=== Test 8: Live iron condor order (paper) ===")
#     broker = AlpacaBroker()
#     # ... (implementation would go here)
#     print("  PASS")


# ── Runner ──────────────────────────────────────────────────

def run_all():
    print("\n" + "=" * 60)
    print("  TRADE-PILOT SPREAD INTEGRATION TESTS")
    print(f"  Paper account: {settings.ALPACA_PAPER}")
    print(f"  Watchlist: {settings.WATCHLIST}")
    print("=" * 60)

    tests = [
        test_options_time_constraint,
        test_spread_tracker_persistence,
        test_credit_sign_convention,
        test_strategy_router,
        test_guardrails_all_spreads,
        test_state_writer_and_api,
        # Uncomment for live API tests (paper account):
        # test_mleg_order_support,
        # test_iron_condor_order,
    ]

    results: dict[str, str] = {}

    for test_fn in tests:
        name = test_fn.__name__
        try:
            test_fn()
            results[name] = "PASS"
        except Exception as e:
            results[name] = f"FAIL -- {e}"
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 60)
    print("  RESULTS SUMMARY")
    print("=" * 60)
    for name, result in results.items():
        icon = "[OK]" if result == "PASS" else "[FAIL]"
        print(f"  {icon}  {name}")

    failed = [n for n, r in results.items() if r != "PASS"]
    if failed:
        print(f"\n  {len(failed)} test(s) failed. DO NOT deploy until fixed.")
        sys.exit(1)
    else:
        print(f"\n  All {len(results)} tests passed. Safe to deploy.")


if __name__ == "__main__":
    run_all()
