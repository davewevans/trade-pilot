"""Tests for data.state_writer.StateWriter."""

import json
import os
from pathlib import Path

import pytest

from data.state_writer import StateWriter


@pytest.fixture
def snap_dir(tmp_path):
    """Provide a fresh temporary snapshot directory."""
    d = tmp_path / "snapshots"
    # Do NOT create it — StateWriter should handle that.
    return d


@pytest.fixture
def writer(snap_dir):
    return StateWriter(snapshot_dir=snap_dir)


# ── directory creation ──────────────────────────────────────


class TestDirectoryCreation:
    def test_creates_snapshot_dir_on_init(self, snap_dir):
        """StateWriter creates the directory if it doesn't exist."""
        assert not snap_dir.exists()
        StateWriter(snapshot_dir=snap_dir)
        assert snap_dir.exists()


# ── write_portfolio_snapshot ────────────────────────────────


class TestPortfolioSnapshot:
    def test_produces_valid_json(self, writer, snap_dir):
        writer.write_portfolio_snapshot(
            account_data={
                "portfolio_value": "125000.00",
                "buying_power":    "50000.00",
                "last_equity":     "124000.00",
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
            ],
            wheel_states={"SPY": "SHORT_PUT", "AAPL": "IDLE"},
        )

        path = snap_dir / "portfolio.json"
        assert path.exists()

        data = json.loads(path.read_text(encoding="utf-8"))
        assert "timestamp" in data
        assert data["account"]["total_equity"] == 125000.0
        assert data["account"]["buying_power"] == 50000.0
        assert data["account"]["buying_power_used_pct"] == 60.0
        assert len(data["positions"]) == 1
        assert data["positions"][0]["strike"] == 540.0
        assert data["positions"][0]["delta"] == -0.25
        assert data["wheel_states"]["SPY"] == "SHORT_PUT"
        assert "today_pnl" in data["account"]
        assert "today_pnl_pct" in data["account"]
        assert "last_equity" in data["account"]

    def test_handles_zero_equity(self, writer, snap_dir):
        writer.write_portfolio_snapshot(
            account_data={"portfolio_value": 0, "buying_power": 0},
            positions=[],
            wheel_states={},
        )
        data = json.loads((snap_dir / "portfolio.json").read_text(encoding="utf-8"))
        assert data["account"]["buying_power_used_pct"] == 0.0

    def test_handles_missing_fields(self, writer, snap_dir):
        writer.write_portfolio_snapshot(
            account_data={},
            positions=[{}],
            wheel_states={},
        )
        data = json.loads((snap_dir / "portfolio.json").read_text(encoding="utf-8"))
        assert data["account"]["total_equity"] == 0
        assert len(data["positions"]) == 1


class TestInstrumentTypeAndEquityDelta:
    """Regression coverage for the blank Type pill / equity delta bug.

    Audit finding: an equity (stock) position row rendered a blank Type pill
    and "—" for Delta, even though compute_portfolio_greeks correctly
    aggregated Net Delta for the same shares. write_portfolio_snapshot must
    emit an "instrument_type" for every row and, for equity rows with no
    broker-supplied delta, a signed-share-count delta using the SAME sign
    convention as compute_portfolio_greeks (long -> +qty, short -> -qty).
    """

    def test_equity_position_gets_stock_type_and_signed_delta(self, writer, snap_dir):
        writer.write_portfolio_snapshot(
            account_data={"portfolio_value": 100000, "buying_power": 50000},
            positions=[
                {
                    "symbol": "VZ",
                    "underlying": "VZ",
                    "side": "short",
                    "qty": 100,
                    "delta": None,
                    "avg_entry_price": "40.00",
                    "current_price": "41.00",
                    "unrealized_pl": "-100.00",
                },
            ],
            wheel_states={},
        )

        data = json.loads((snap_dir / "portfolio.json").read_text(encoding="utf-8"))
        row = data["positions"][0]
        assert row["instrument_type"] == "stock"
        # Matches compute_portfolio_greeks's sign convention: short -> -qty.
        assert row["delta"] == -100

    def test_option_leg_keeps_explicit_delta_and_gets_put_call_type(self, writer, snap_dir):
        writer.write_portfolio_snapshot(
            account_data={"portfolio_value": 100000, "buying_power": 50000},
            positions=[
                {
                    "symbol": "SPY250502P00540000",
                    "underlying": "SPY",
                    "strike_price": "540.00",
                    "expiration_date": "2025-05-02",
                    "dte": 21,
                    "side": "short",
                    "qty": -1,
                    "delta": "-0.25",
                    "theta": "-0.04",
                },
                {
                    "symbol": "AAPL251219C00150000",
                    "underlying": "AAPL",
                    "strike_price": "150.00",
                    "expiration_date": "2025-12-19",
                    "dte": 30,
                    "side": "long",
                    "qty": 1,
                    "delta": "0.55",
                },
            ],
            wheel_states={},
        )

        data = json.loads((snap_dir / "portfolio.json").read_text(encoding="utf-8"))
        put_row, call_row = data["positions"]
        assert put_row["instrument_type"] == "put"
        assert put_row["delta"] == -0.25
        assert call_row["instrument_type"] == "call"
        assert call_row["delta"] == 0.55


# ── write_context_snapshot ──────────────────────────────────


class TestContextSnapshot:
    def test_produces_valid_json(self, writer, snap_dir):
        context = {
            "symbol": "AAPL",
            "macro": {
                "vix": 18.4,
                "fear_greed_score": 62,
                "fear_greed_rating": "Greed",
                "risk_free_rate": 0.0523,
            },
            "technicals": {"rsi_14": 55.2, "current_price": 172.50},
            "iv_rank": 48,
            "market_regime": "normal",
        }

        writer.write_context_snapshot(context)

        path = snap_dir / "context.json"
        assert path.exists()

        data = json.loads(path.read_text(encoding="utf-8"))
        assert "timestamp" in data
        assert data["macro"]["vix"] == 18.4
        assert data["symbol"] == "AAPL"
        assert data["market_regime"] == "normal"

    def test_overwrites_previous(self, writer, snap_dir):
        writer.write_context_snapshot({"round": 1})
        writer.write_context_snapshot({"round": 2})

        data = json.loads((snap_dir / "context.json").read_text(encoding="utf-8"))
        assert data["round"] == 2


# ── write_decision ──────────────────────────────────────────


class TestDecision:
    def test_produces_valid_jsonl(self, writer, snap_dir):
        writer.write_decision(
            decision_dict={
                "action": "sell_put",
                "iv_rank": 52,
                "dte": 21,
                "delta": -0.25,
                "limit_price": 3.20,
            },
            reasoning="IV rank above 50, good premium.",
            action_taken=True,
            underlying="SPY",
        )

        path = snap_dir / "decisions.jsonl"
        assert path.exists()

        record = json.loads(path.read_text(encoding="utf-8").strip())
        assert record["underlying"] == "SPY"
        assert record["action"] == "sell_put"
        assert record["action_taken"] is True
        assert record["reasoning"] == "IV rank above 50, good premium."
        assert record["key_inputs"]["iv_rank"] == 52
        assert record["key_inputs"]["delta"] == -0.25
        assert record["guardrail_rejection"] is None

    def test_appends_across_multiple_calls(self, writer, snap_dir):
        for i in range(3):
            writer.write_decision(
                decision_dict={"action": f"action_{i}"},
                reasoning=f"reason {i}",
                action_taken=i % 2 == 0,
                underlying="AAPL",
            )

        path = snap_dir / "decisions.jsonl"
        lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) == 3

        for i, line in enumerate(lines):
            record = json.loads(line)
            assert record["action"] == f"action_{i}"
            assert record["action_taken"] == (i % 2 == 0)

    def test_skip_with_reason_and_rejection(self, writer, snap_dir):
        writer.write_decision(
            decision_dict={
                "action": "skip",
                "skip_reason": "Earnings in 5 days",
            },
            reasoning="Too close to earnings.",
            action_taken=False,
            underlying="NVDA",
            guardrail_rejection="Earnings < 14 days",
        )

        record = json.loads(
            (snap_dir / "decisions.jsonl").read_text(encoding="utf-8").strip()
        )
        assert record["action"] == "skip"
        assert record["action_taken"] is False
        assert record["key_inputs"]["skip_reason"] == "Earnings in 5 days"
        assert record["guardrail_rejection"] == "Earnings < 14 days"


# ── write_circuit_breaker_status ────────────────────────────


class TestCircuitBreaker:
    def test_produces_valid_json(self, writer, snap_dir):
        writer.write_circuit_breaker_status({
            "daily_pnl": -250.0,
            "daily_pnl_pct": -0.2,
            "weekly_pnl": 1200.0,
            "weekly_pnl_pct": 0.96,
            "peak_equity": 126000.0,
            "current_drawdown_pct": 0.8,
            "status": "YELLOW",
            "active_rules": ["daily_loss_warn"],
            "halted": False,
        })

        path = snap_dir / "circuit_breakers.json"
        assert path.exists()

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["status"] == "YELLOW"
        assert data["daily_pnl"] == -250.0
        assert data["halted"] is False
        assert "daily_loss_warn" in data["active_rules"]
        assert "timestamp" in data

    def test_defaults(self, writer, snap_dir):
        writer.write_circuit_breaker_status({})

        data = json.loads(
            (snap_dir / "circuit_breakers.json").read_text(encoding="utf-8")
        )
        assert data["status"] == "GREEN"
        assert data["halted"] is False
        assert data["active_rules"] == []


# ── write_equity_history ────────────────────────────────────


class TestEquityHistory:
    def test_produces_valid_json(self, writer, snap_dir):
        history = {
            "timestamp":       [1700000000, 1700086400, 1700172800],
            "equity":          [100000.0, 100312.5, None],
            "profit_loss":     [0.0, 312.5, None],
            "profit_loss_pct": [0.0, 0.003125, None],
            "base_value":      100000.0,
            "timeframe":       "1D",
        }
        writer.write_equity_history(history)

        path = snap_dir / "equity_history.json"
        assert path.exists()

        data = json.loads(path.read_text(encoding="utf-8"))
        assert "timestamp" in data
        assert data["base_value"] == 100000.0
        # Null equity entry (index 2) should be skipped
        assert len(data["points"]) == 2
        assert data["points"][0]["equity"] == 100000.0
        assert data["points"][1]["pnl"] == 312.5

    def test_empty_history_writes_nothing(self, writer, snap_dir):
        writer.write_equity_history({})
        assert not (snap_dir / "equity_history.json").exists()


# ── atomic write ────────────────────────────────────────────


class TestAtomicWrite:
    def test_no_temp_files_left_behind(self, writer, snap_dir):
        writer.write_portfolio_snapshot(
            account_data={"portfolio_value": 100},
            positions=[],
            wheel_states={},
        )

        files = list(snap_dir.iterdir())
        assert all(not f.name.endswith(".tmp") for f in files)
        assert (snap_dir / "portfolio.json").exists()

    def test_overwrite_is_atomic(self, writer, snap_dir):
        """A second write fully replaces the first — no merge artifacts."""
        writer.write_circuit_breaker_status({"status": "GREEN", "halted": False})
        writer.write_circuit_breaker_status({"status": "RED", "halted": True})

        data = json.loads(
            (snap_dir / "circuit_breakers.json").read_text(encoding="utf-8")
        )
        assert data["status"] == "RED"
        assert data["halted"] is True
