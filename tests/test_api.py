"""Tests for the FastAPI dashboard endpoints."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _patch_paths(tmp_path, monkeypatch):
    """Redirect all snapshot/journal paths to a temp directory."""
    snap = tmp_path / "snapshots"
    snap.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    journal = data / "journal.jsonl"

    import api.server as srv

    monkeypatch.setattr(srv, "SNAPSHOTS", snap)
    monkeypatch.setattr(srv, "DATA_DIR", tmp_path)
    monkeypatch.setattr(srv, "JOURNAL_PATH", journal)
    monkeypatch.setattr(srv, "LOCK_PATH", tmp_path / "HALTED.lock")

    # Expose to tests via the request fixture
    _patch_paths.snap = snap
    _patch_paths.data = data
    _patch_paths.journal = journal
    _patch_paths.tmp = tmp_path


@pytest.fixture
def client():
    from api.server import app

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def snap():
    return _patch_paths.snap


@pytest.fixture
def journal_path():
    return _patch_paths.journal


@pytest.fixture
def tmp():
    return _patch_paths.tmp


# ── /api/health ─────────────────────────────────────────────


class TestHealth:
    def test_returns_200(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert "timestamp" in body
        assert body["halted"] is False

    def test_halted_when_lock_exists(self, client, tmp):
        (tmp / "HALTED.lock").write_text("{}")
        r = client.get("/api/health")
        assert r.json()["halted"] is True


# ── /api/portfolio ──────────────────────────────────────────


class TestPortfolio:
    def test_503_when_no_snapshot(self, client):
        r = client.get("/api/portfolio")
        assert r.status_code == 503
        assert "error" in r.json()

    def test_returns_snapshot(self, client, snap):
        payload = {"timestamp": "2026-04-11", "account": {"total_equity": 100000}}
        (snap / "portfolio.json").write_text(json.dumps(payload))
        r = client.get("/api/portfolio")
        assert r.status_code == 200
        assert r.json()["account"]["total_equity"] == 100000


# ── /api/context ────────────────────────────────────────────


class TestContext:
    def test_503_when_missing(self, client):
        r = client.get("/api/context")
        assert r.status_code == 503

    def test_returns_context(self, client, snap):
        (snap / "context.json").write_text(json.dumps({"vix": 18.5}))
        r = client.get("/api/context")
        assert r.status_code == 200
        assert r.json()["vix"] == 18.5


# ── /api/circuit-breakers ──────────────────────────────────


class TestCircuitBreakers:
    def test_503_when_missing(self, client):
        r = client.get("/api/circuit-breakers")
        assert r.status_code == 503

    def test_returns_data(self, client, snap):
        (snap / "circuit_breakers.json").write_text(
            json.dumps({"status": "GREEN", "halted": False})
        )
        r = client.get("/api/circuit-breakers")
        assert r.status_code == 200
        assert r.json()["status"] == "GREEN"


# ── /api/decisions ──────────────────────────────────────────


class TestDecisions:
    def test_empty_when_no_file(self, client):
        r = client.get("/api/decisions")
        assert r.status_code == 200
        body = r.json()
        assert body["decisions"] == []
        assert body["total"] == 0

    def test_returns_decisions_most_recent_first(self, client, snap):
        lines = []
        for i in range(5):
            lines.append(json.dumps({
                "timestamp": f"2026-04-11T10:0{i}:00",
                "underlying": "SPY",
                "action": "sell_put",
                "action_taken": True,
            }))
        (snap / "decisions.jsonl").write_text("\n".join(lines) + "\n")

        r = client.get("/api/decisions")
        body = r.json()
        assert body["total"] == 5
        # Most recent first
        assert body["decisions"][0]["timestamp"] == "2026-04-11T10:04:00"

    def test_limit_parameter(self, client, snap):
        lines = [json.dumps({"action": f"a{i}", "underlying": "X"}) for i in range(20)]
        (snap / "decisions.jsonl").write_text("\n".join(lines) + "\n")

        r = client.get("/api/decisions?limit=5")
        body = r.json()
        assert len(body["decisions"]) == 5
        assert body["total"] == 20

    def test_filter_by_underlying(self, client, snap):
        lines = [
            json.dumps({"action": "sell_put", "underlying": "SPY"}),
            json.dumps({"action": "sell_put", "underlying": "AAPL"}),
            json.dumps({"action": "skip", "underlying": "SPY"}),
        ]
        (snap / "decisions.jsonl").write_text("\n".join(lines) + "\n")

        r = client.get("/api/decisions?underlying=SPY")
        body = r.json()
        assert body["total"] == 2
        assert all(d["underlying"] == "SPY" for d in body["decisions"])

    def test_filter_by_action(self, client, snap):
        lines = [
            json.dumps({"action": "skip", "underlying": "SPY", "action_taken": False}),
            json.dumps({"action": "sell_put", "underlying": "SPY", "action_taken": True}),
        ]
        (snap / "decisions.jsonl").write_text("\n".join(lines) + "\n")

        r = client.get("/api/decisions?action=SKIP")
        body = r.json()
        assert body["total"] == 1
        assert body["decisions"][0]["action"] == "skip"


# ── /api/decisions/stats ────────────────────────────────────


class TestDecisionStats:
    def test_empty_stats(self, client):
        r = client.get("/api/decisions/stats")
        assert r.status_code == 200
        body = r.json()
        assert body["total_decisions"] == 0
        assert body["win_rate"] == 0.0

    def test_computed_stats(self, client, snap, journal_path):
        decisions = [
            {"action": "sell_put", "action_taken": True, "underlying": "SPY",
             "key_inputs": {"iv_rank": 55}},
            {"action": "skip", "action_taken": False, "underlying": "AAPL",
             "key_inputs": {"skip_reason": "Earnings too close"}},
        ]
        (snap / "decisions.jsonl").write_text(
            "\n".join(json.dumps(d) for d in decisions) + "\n"
        )

        journal = [
            {"pnl": 150, "closed_at": "2026-04-10"},
            {"pnl": -50, "closed_at": "2026-04-10"},
        ]
        journal_path.write_text(
            "\n".join(json.dumps(e) for e in journal) + "\n"
        )

        r = client.get("/api/decisions/stats")
        body = r.json()
        assert body["total_decisions"] == 2
        assert body["trades"] == 1
        assert body["skips"] == 1
        assert body["win_rate"] == 50.0
        assert body["avg_iv_rank_at_entry"] == 55.0
        assert body["decisions_by_underlying"]["SPY"] == 1


# ── /api/performance ────────────────────────────────────────


class TestPerformance:
    def test_empty_when_no_journal(self, client):
        r = client.get("/api/performance")
        assert r.status_code == 200
        body = r.json()
        assert body["total_pnl"] == 0
        assert body["equity_curve"] == []

    def test_with_journal_entries(self, client, journal_path):
        entries = [
            {"timestamp": "2026-04-10T10:00:00", "pnl": 200, "fill_price": 3.2, "closed_at": "2026-04-10"},
            {"timestamp": "2026-04-11T10:00:00", "pnl": -50, "fill_price": 1.1, "closed_at": "2026-04-11"},
        ]
        journal_path.write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n"
        )
        r = client.get("/api/performance")
        body = r.json()
        assert body["total_pnl"] == 150.0
        assert body["realized_pnl"] == 150.0
        assert body["best_trade"]["pnl"] == 200
        assert body["worst_trade"]["pnl"] == -50


# ── /api/regime-history ─────────────────────────────────────


class TestRegimeHistory:
    def test_default_when_missing(self, client):
        r = client.get("/api/regime-history")
        assert r.status_code == 200
        body = r.json()
        assert body["confirmed"] == "NEUTRAL"
        assert body["readings"] == []

    def test_returns_history(self, client, snap):
        data = {"readings": ["BULL", "BULL", "BULL"], "confirmed": "BULL"}
        (snap / "regime_history.json").write_text(json.dumps(data))
        r = client.get("/api/regime-history")
        assert r.json()["confirmed"] == "BULL"
