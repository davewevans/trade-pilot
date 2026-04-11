"""End-to-end pipeline test: StateWriter -> snapshot files -> FastAPI endpoints.

Writes mock data, hits every API endpoint, and validates the responses.

    python scripts/test_pipeline.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def main() -> None:
    # Use a temp directory so we don't pollute the real data/
    tmp = Path(tempfile.mkdtemp(prefix="tp_pipeline_"))
    snap_dir = tmp / "snapshots"
    journal_path = tmp / "journal.jsonl"

    print(f"Working directory: {tmp}\n")

    # ── 1. Write mock snapshots ─────────────────────────────
    from data.state_writer import StateWriter

    sw = StateWriter(snapshot_dir=snap_dir)

    sw.write_portfolio_snapshot(
        account_data={
            "portfolio_value": "125000.00",
            "buying_power": "48000.00",
        },
        positions=[
            {
                "symbol": "SPY250502P00540000",
                "underlying": "SPY",
                "strategy_type": "CSP",
                "strike_price": "540.00",
                "expiration_date": "2025-05-02",
                "dte": 21,
                "qty": -1,
                "avg_entry_price": "3.20",
                "current_price": "2.10",
                "unrealized_pl": "110.00",
                "delta": "-0.25",
                "theta": "-0.04",
            },
            {
                "symbol": "AAPL250509C00185000",
                "underlying": "AAPL",
                "strategy_type": "CC",
                "strike_price": "185.00",
                "expiration_date": "2025-05-09",
                "dte": 28,
                "qty": -1,
                "avg_entry_price": "2.80",
                "current_price": "1.90",
                "unrealized_pl": "90.00",
                "delta": "0.30",
                "theta": "-0.03",
            },
        ],
        wheel_states={"SPY": "SHORT_PUT", "AAPL": "SHORT_CALL"},
    )
    print("  Wrote portfolio snapshot")

    sw.write_context_snapshot({
        "symbol": "SPY",
        "macro": {
            "vix": 18.4,
            "vix_regime": "normal",
            "fear_greed_score": 62,
            "fear_greed_rating": "Greed",
            "risk_free_rate": 0.0523,
        },
        "technicals": {
            "current_price": 542.50,
            "rsi_14": 55.2,
            "above_sma_50": True,
            "above_sma_200": True,
        },
        "iv_rank": 48,
        "confirmed_market_regime": "BULL",
    })
    print("  Wrote context snapshot")

    for i in range(5):
        is_skip = i % 2 == 0
        sw.write_decision(
            decision_dict={
                "action": "skip" if is_skip else "sell_put",
                "iv_rank": 45 + i,
                "dte": 21 + i,
                "delta": -0.25,
                "limit_price": 3.20 if not is_skip else None,
                "skip_reason": "IV rank too low" if is_skip else None,
            },
            reasoning=f"Test reasoning for decision {i}",
            action_taken=not is_skip,
            underlying="SPY" if i < 3 else "AAPL",
            guardrail_rejection="Earnings too close" if i == 4 else None,
        )
    print("  Wrote 5 decisions")

    sw.write_circuit_breaker_status({
        "daily_pnl": 150.0,
        "daily_pnl_pct": 0.12,
        "weekly_pnl": 800.0,
        "weekly_pnl_pct": 0.64,
        "peak_equity": 125500.0,
        "current_drawdown_pct": 0.4,
        "status": "GREEN",
        "active_rules": [],
        "halted": False,
    })
    print("  Wrote circuit breaker status")

    # Write regime history
    (snap_dir / "regime_history.json").write_text(json.dumps({
        "readings": ["BULL", "BULL", "BULL"],
        "confirmed": "BULL",
    }))
    print("  Wrote regime history")

    # Write a mock journal for performance endpoint
    with open(journal_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "timestamp": "2026-04-10T10:00:00",
            "symbol": "SPY250502P00540000",
            "underlying": "SPY",
            "action": "sell_put",
            "fill_price": 3.20,
            "pnl": 210.0,
            "closed_at": "2026-04-10T15:00:00",
        }) + "\n")
        f.write(json.dumps({
            "timestamp": "2026-04-11T10:00:00",
            "symbol": "AAPL250509C00185000",
            "underlying": "AAPL",
            "action": "sell_call",
            "fill_price": 2.80,
            "pnl": -45.0,
            "closed_at": "2026-04-11T15:00:00",
        }) + "\n")
    print("  Wrote mock journal\n")

    # ── 2. Patch API server paths and hit endpoints ─────────
    import api.server as srv

    srv.SNAPSHOTS = snap_dir
    srv.DATA_DIR = tmp
    srv.JOURNAL_PATH = journal_path
    srv.LOCK_PATH = tmp / "HALTED.lock"

    from fastapi.testclient import TestClient

    client = TestClient(srv.app, raise_server_exceptions=False)

    endpoints = [
        ("/api/health", 200),
        ("/api/portfolio", 200),
        ("/api/context", 200),
        ("/api/circuit-breakers", 200),
        ("/api/decisions", 200),
        ("/api/decisions?limit=3", 200),
        ("/api/decisions?underlying=SPY", 200),
        ("/api/decisions?action=SKIP", 200),
        ("/api/decisions/stats", 200),
        ("/api/performance", 200),
        ("/api/regime-history", 200),
    ]

    passed = 0
    failed = 0

    for path, expected_status in endpoints:
        r = client.get(path)
        try:
            body = r.json()
            is_json = True
        except Exception:
            body = None
            is_json = False

        ok = r.status_code == expected_status and is_json
        icon = "PASS" if ok else "FAIL"
        print(f"  [{icon}] {path} -> {r.status_code}")

        if ok:
            passed += 1
        else:
            failed += 1
            if not is_json:
                print(f"         Response was not valid JSON")
            if r.status_code != expected_status:
                print(f"         Expected {expected_status}, got {r.status_code}")

    # ── 3. Spot-check specific response values ──────────────
    print()

    r = client.get("/api/portfolio")
    portfolio = r.json()
    assert portfolio["account"]["total_equity"] == 125000.0, "Portfolio equity mismatch"
    assert len(portfolio["positions"]) == 2, "Expected 2 positions"
    print("  Portfolio data verified: 2 positions, $125,000 equity")

    r = client.get("/api/decisions")
    decisions = r.json()
    assert decisions["total"] == 5, "Expected 5 decisions"
    assert decisions["decisions"][0]["underlying"] in ("SPY", "AAPL"), "Bad underlying"
    print(f"  Decisions verified: {decisions['total']} total")

    r = client.get("/api/decisions?limit=3")
    limited = r.json()
    assert len(limited["decisions"]) == 3, "Limit not respected"
    print(f"  Limit=3 verified: got {len(limited['decisions'])} decisions")

    r = client.get("/api/decisions/stats")
    stats = r.json()
    assert stats["total_decisions"] == 5
    assert stats["skips"] == 3  # decisions 0, 2, 4 are skips
    assert stats["trades"] == 2
    print(f"  Stats verified: {stats['trades']} trades, {stats['skips']} skips")

    r = client.get("/api/performance")
    perf = r.json()
    assert perf["total_pnl"] == 165.0, f"Expected 165.0, got {perf['total_pnl']}"
    print(f"  Performance verified: total P&L ${perf['total_pnl']}")

    r = client.get("/api/regime-history")
    regime = r.json()
    assert regime["confirmed"] == "BULL"
    print(f"  Regime history verified: confirmed={regime['confirmed']}")

    # ── Summary ─────────────────────────────────────────────
    print(f"\n{'=' * 50}")
    if failed == 0:
        print(f"  All {passed} endpoints returned valid data [OK]")
    else:
        print(f"  {passed} passed, {failed} FAILED")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
