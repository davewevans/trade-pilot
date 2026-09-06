"""Tests for PUBLIC_DASHBOARD read-only mode.

The flag lets anonymous visitors read the dashboard (portfolio/demo use) while
every mutating method stays behind the session gate.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _patch_paths(tmp_path, monkeypatch):
    snap = tmp_path / "snapshots"
    snap.mkdir()
    data = tmp_path / "data"
    data.mkdir()

    import api.server as srv

    monkeypatch.setattr(srv, "SNAPSHOTS", snap)
    monkeypatch.setattr(srv, "DATA_DIR", data)
    monkeypatch.setattr(srv, "JOURNAL_PATH", data / "journal.jsonl")
    monkeypatch.setattr(srv, "LOCK_PATH", data / "HALTED.lock")
    monkeypatch.setattr(srv, "DB_PATH", tmp_path / "test.db")


@pytest.fixture
def client():
    from api.server import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def authed_client():
    from api.server import app, _issue_session, _SESSION_COOKIE
    tc = TestClient(app, raise_server_exceptions=False)
    token, _ = _issue_session()
    tc.cookies.set(_SESSION_COOKIE, token)
    return tc


@pytest.fixture
def public(monkeypatch):
    """Turn public mode on for the duration of a test."""
    from config import settings
    monkeypatch.setattr(settings, "PUBLIC_DASHBOARD", True)


# ── Flag OFF: behaviour must be identical to before this feature ──────────────

class TestFlagOffIsUnchanged:
    def test_read_api_requires_auth(self, client):
        assert client.get("/api/decisions").status_code == 401

    def test_mutating_api_requires_auth(self, client):
        assert client.post("/api/halt", json={"reason": "x"}).status_code == 401

    def test_root_serves_login_page(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "password" in r.text.lower()

    def test_config_reports_auth_required(self, client):
        body = client.get("/api/config").json()
        assert body["auth_required"] is True
        assert body["authenticated"] is False


# ── Flag ON: reads open, writes closed ───────────────────────────────────────

class TestPublicModeReads:
    @pytest.mark.parametrize("path", [
        "/api/decisions",
        "/api/performance",
        "/api/trades",
        "/api/equity-history",
        "/api/regime-history",
        "/api/circuit-breakers",
        "/api/watchlist",
    ])
    def test_anonymous_get_allowed(self, client, public, path):
        # Any status except 401 proves the auth gate let it through. Endpoints
        # with no seeded data legitimately answer 503/404.
        assert client.get(path).status_code != 401


class TestPublicModeWritesStillBlocked:
    """The whole point of the flag design: reads open, writes closed."""

    def test_halt_blocked(self, client, public):
        assert client.post("/api/halt", json={"reason": "pwn"}).status_code == 401

    def test_resume_blocked(self, client, public):
        assert client.post("/api/resume").status_code == 401

    def test_reset_circuit_breaker_blocked(self, client, public):
        assert client.post("/api/admin/reset-circuit-breaker").status_code == 401

    def test_orats_toggle_blocked(self, client, public):
        assert client.post("/api/admin/orats/disable").status_code == 401

    def test_account_activate_blocked(self, client, public):
        assert client.post("/api/accounts/paper_1/activate").status_code == 401

    def test_account_watchlist_put_blocked(self, client, public):
        r = client.put("/api/accounts/paper_1/watchlist", json={"watchlist": ["AAPL"]})
        assert r.status_code == 401

    def test_backtest_blocked(self, client, public):
        assert client.post("/api/backtest", json={}).status_code == 401

    def test_same_path_get_public_post_blocked(self, client, public):
        """/api/watchlist proves the gate is on METHOD, not just path."""
        assert client.get("/api/watchlist").status_code != 401
        assert client.post("/api/watchlist", json={
            "wheel": ["PWN"], "iron_condor": [], "iron_butterfly": [],
            "spreads": [], "calendar_spread": [],
        }).status_code == 401

    def test_options_blocked(self, client, public):
        """Only GET/HEAD are treated as safe."""
        assert client.options("/api/decisions").status_code == 401


class TestPublicModeSpaAndConfig:
    def test_config_reports_public(self, client, public):
        body = client.get("/api/config").json()
        assert body["auth_required"] is False
        assert body["authenticated"] is False

    def test_config_reports_authenticated_with_cookie(self, authed_client, public):
        assert authed_client.get("/api/config").json()["authenticated"] is True

    def test_root_is_not_the_login_page(self, client, public):
        import api.server as srv
        if not getattr(srv, "_STATIC_READY", False):
            pytest.skip("frontend not built in this environment")
        r = client.get("/")
        assert r.status_code == 200
        assert "Session expired" not in r.text

    def test_authenticated_writes_still_work(self, authed_client, public):
        """Public mode must not break the operator's own admin access."""
        assert authed_client.post("/api/resume").status_code != 401


# ── Flag parsing ─────────────────────────────────────────────────────────────

class TestFlagParsing:
    def test_unset_defaults_false(self, monkeypatch):
        from config import _env_bool
        monkeypatch.delenv("PUBLIC_DASHBOARD", raising=False)
        assert _env_bool("PUBLIC_DASHBOARD", default=False) is False

    def test_empty_string_defaults_false(self, monkeypatch):
        """The footgun this guards: os.getenv(...,'false')=='true' style parsing."""
        from config import _env_bool
        monkeypatch.setenv("PUBLIC_DASHBOARD", "")
        assert _env_bool("PUBLIC_DASHBOARD", default=False) is False

    @pytest.mark.parametrize("raw", ["true", "1", "yes", "TRUE", " true "])
    def test_truthy_values(self, monkeypatch, raw):
        from config import _env_bool
        monkeypatch.setenv("PUBLIC_DASHBOARD", raw)
        assert _env_bool("PUBLIC_DASHBOARD", default=False) is True

    @pytest.mark.parametrize("raw", ["false", "0", "no", "off"])
    def test_falsy_values(self, monkeypatch, raw):
        from config import _env_bool
        monkeypatch.setenv("PUBLIC_DASHBOARD", raw)
        assert _env_bool("PUBLIC_DASHBOARD", default=False) is False
