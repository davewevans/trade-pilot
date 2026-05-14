"""Regression tests for /api/decisions/stats and _strategy_filter.

Covers the paper_N account routing bug (Sentry events 5b82fc8e, 14678632):
- _strategy_filter must resolve paper_N accounts via AccountManager
- _decisions_stats_from_db must return zero counts (not raise) for empty filter
- fetchone() None guard must not crash on pathological DB responses
"""

import sqlite3
from unittest.mock import MagicMock, patch

import pytest


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mem_conn():
    """In-memory SQLite with row_factory and the decisions + trades tables."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE decisions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT NOT NULL,
            strategy_type   TEXT NOT NULL,
            underlying      TEXT NOT NULL,
            cycle_id        TEXT,
            wheel_state     TEXT,
            action          TEXT NOT NULL,
            reasoning       TEXT,
            confidence      REAL,
            alpaca_order_id TEXT,
            prompt_version  TEXT,
            context_json    TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE trades (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id         TEXT NOT NULL,
            decision_id      INTEGER,
            alpaca_order_id  TEXT NOT NULL UNIQUE,
            underlying       TEXT NOT NULL,
            strategy_type    TEXT NOT NULL,
            trade_type       TEXT NOT NULL,
            symbol           TEXT NOT NULL,
            fill_status      TEXT,
            fill_price       REAL,
            limit_price      REAL,
            filled_at        TEXT,
            submitted_at     TEXT,
            contracts        INTEGER NOT NULL DEFAULT 1
        )
    """)
    conn.commit()
    return conn


def _insert(conn, strategy_type: str, underlying: str, action: str, reasoning: str = ""):
    conn.execute(
        "INSERT INTO decisions (timestamp, strategy_type, underlying, action, reasoning) "
        "VALUES (datetime('now'), ?, ?, ?, ?)",
        (strategy_type, underlying, action, reasoning),
    )
    conn.commit()


# ── _strategy_filter tests ───────────────────────────────────────────────────


_FAKE_ACCOUNT_CONFIG = {
    "paper_1": {"strategy": "adaptive_spreads", "status": "active"},
    "paper_2": {"strategy": "wheel", "status": "active"},
    "paper_3": {"strategy": "iron_condor", "status": "active"},
    "paper_4": {"strategy": "iron_butterfly", "status": "active"},
    "paper_5": {"strategy": "calendar_spread", "status": "active"},
    "paper_6": {"strategy": "turnover_wheel", "status": "active"},
}


@pytest.fixture(autouse=True)
def _patch_account_manager():
    mock_mgr = MagicMock()
    mock_mgr.get_account.side_effect = lambda acct: _FAKE_ACCOUNT_CONFIG.get(acct)
    with patch("data.account_manager.AccountManager", return_value=mock_mgr):
        yield


def test_strategy_filter_none_returns_none():
    from api.server import _strategy_filter
    assert _strategy_filter(None) is None


def test_strategy_filter_empty_string_returns_none():
    from api.server import _strategy_filter
    assert _strategy_filter("") is None


def test_strategy_filter_legacy_wheel():
    from api.server import _strategy_filter
    assert _strategy_filter("wheel") == ["wheel"]


def test_strategy_filter_legacy_spreads():
    from api.server import _strategy_filter
    result = _strategy_filter("spreads")
    assert set(result) == {"bull_put_spread", "bear_call_spread", "long_call_vertical"}


def test_strategy_filter_paper_2_wheel():
    from api.server import _strategy_filter
    assert _strategy_filter("paper_2") == ["wheel"]


def test_strategy_filter_paper_3_iron_condor():
    from api.server import _strategy_filter
    assert _strategy_filter("paper_3") == ["iron_condor"]


def test_strategy_filter_paper_4_iron_butterfly():
    from api.server import _strategy_filter
    assert _strategy_filter("paper_4") == ["iron_butterfly"]


def test_strategy_filter_paper_5_calendar_spread():
    from api.server import _strategy_filter
    assert _strategy_filter("paper_5") == ["calendar_spread"]


def test_strategy_filter_paper_6_turnover_wheel():
    from api.server import _strategy_filter
    assert _strategy_filter("paper_6") == ["turnover_wheel"]


def test_strategy_filter_paper_1_adaptive_spreads_expands():
    from api.server import _strategy_filter
    result = _strategy_filter("paper_1")
    assert set(result) == {"bull_put_spread", "bear_call_spread", "long_call_vertical"}


def test_strategy_filter_unknown_returns_empty_list():
    from api.server import _strategy_filter
    result = _strategy_filter("does_not_exist")
    assert result == []


# ── _decisions_stats_from_db tests ───────────────────────────────────────────


def test_stats_from_db_empty_strategy_filter_returns_zeros(mem_conn):
    """Empty strategy_types (unknown account) must return zeros without querying."""
    from api.server import _decisions_stats_from_db
    # Insert rows that should NOT be counted.
    _insert(mem_conn, "wheel", "AAPL", "OPEN")
    _insert(mem_conn, "wheel", "AAPL", "SKIP")

    result = _decisions_stats_from_db(mem_conn, strategy_types=[])

    assert result["total_decisions"] == 0
    assert result["skips"] == 0
    assert result["trades"] == 0


def test_stats_from_db_paper_3_iron_condor(mem_conn):
    """paper_3 → iron_condor: only iron_condor rows should be counted."""
    from api.server import _decisions_stats_from_db
    _insert(mem_conn, "iron_condor", "SPY", "OPEN")
    _insert(mem_conn, "iron_condor", "QQQ", "SKIP", "IV too high")
    _insert(mem_conn, "wheel", "AAPL", "OPEN")  # different strategy — excluded

    result = _decisions_stats_from_db(mem_conn, strategy_types=["iron_condor"])

    assert result["total_decisions"] == 2
    assert result["skips"] == 1
    assert result["trades"] == 1  # total - skips - holds


def test_stats_from_db_paper_1_adaptive_spreads(mem_conn):
    """paper_1 (adaptive_spreads) sums across all three sub-strategies."""
    from api.server import _decisions_stats_from_db
    _insert(mem_conn, "bull_put_spread", "AAPL", "OPEN")
    _insert(mem_conn, "bear_call_spread", "MSFT", "OPEN")
    _insert(mem_conn, "long_call_vertical", "NVDA", "SKIP")
    _insert(mem_conn, "wheel", "SPY", "OPEN")  # excluded

    result = _decisions_stats_from_db(
        mem_conn,
        strategy_types=["bull_put_spread", "bear_call_spread", "long_call_vertical"],
    )

    assert result["total_decisions"] == 3
    assert result["skips"] == 1
    assert result["trades"] == 2


def test_stats_from_db_none_filter_returns_all(mem_conn):
    """None strategy_types means no filter — all rows counted."""
    from api.server import _decisions_stats_from_db
    _insert(mem_conn, "wheel", "AAPL", "OPEN")
    _insert(mem_conn, "iron_condor", "SPY", "OPEN")

    result = _decisions_stats_from_db(mem_conn, strategy_types=None)
    assert result["total_decisions"] == 2


def test_stats_from_db_unknown_account_via_filter(mem_conn):
    """_strategy_filter('does_not_exist') → [] → zero counts, no exception."""
    from api.server import _decisions_stats_from_db, _strategy_filter
    _insert(mem_conn, "wheel", "AAPL", "OPEN")

    result = _decisions_stats_from_db(mem_conn, strategy_types=_strategy_filter("does_not_exist"))
    assert result["total_decisions"] == 0
    assert result["skips"] == 0
    assert result["trades"] == 0


# ── End-to-end: GET /api/decisions/stats?account=paper_N ────────────────────
# Per-account routing through the FastAPI test client. Pins the full
# auth → handler → _strategy_filter → _decisions_stats_from_db chain.


@pytest.fixture
def e2e_db(tmp_path):
    """A file-backed SQLite DB. Yields ``(conn, insert_fn, db_path)``.

    ``conn`` is used only by ``insert_fn`` (test-thread writes). The handler
    reads through ``_open_db()``, which since the InterfaceError fix opens a
    fresh per-request connection — ``_e2e_client`` mirrors that.
    """
    db_path = tmp_path / "stats_e2e.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE decisions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT NOT NULL,
            strategy_type   TEXT NOT NULL,
            underlying      TEXT NOT NULL,
            cycle_id        TEXT,
            wheel_state     TEXT,
            action          TEXT NOT NULL,
            reasoning       TEXT,
            confidence      REAL,
            alpaca_order_id TEXT,
            prompt_version  TEXT,
            context_json    TEXT
        );
        CREATE TABLE trades (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id         TEXT NOT NULL,
            decision_id      INTEGER,
            alpaca_order_id  TEXT NOT NULL UNIQUE,
            underlying       TEXT NOT NULL,
            strategy_type    TEXT NOT NULL,
            trade_type       TEXT NOT NULL,
            symbol           TEXT NOT NULL,
            fill_status      TEXT,
            fill_price       REAL,
            limit_price      REAL,
            filled_at        TEXT,
            submitted_at     TEXT,
            contracts        INTEGER NOT NULL DEFAULT 1
        );
    """)
    conn.commit()

    def insert(strategy_type: str, underlying: str, action: str, reasoning: str = ""):
        conn.execute(
            "INSERT INTO decisions (timestamp, strategy_type, underlying, action, reasoning) "
            "VALUES (datetime('now'), ?, ?, ?, ?)",
            (strategy_type, underlying, action, reasoning),
        )
        conn.commit()

    yield conn, insert, str(db_path)
    conn.close()


def _e2e_client(db_path):
    """Build a TestClient + patches that route ``_open_db`` at the test DB.

    Returns ``(client, stop)``; the caller must invoke ``stop()`` to release
    the patches.

    Since the InterfaceError fix, ``_open_db()`` returns a fresh per-request
    ``sqlite3.Connection``. We mirror that here: each call opens a new
    connection to the test DB file, so the handler's ``finally: conn.close()``
    does a real close — exactly as in production.
    """
    from fastapi.testclient import TestClient
    from api import server as srv

    def _fresh():
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        return c

    open_patch = patch.object(srv, "_open_db", side_effect=_fresh)
    auth_patch = patch.object(srv, "_is_authenticated", return_value=True)
    open_patch.start()
    auth_patch.start()
    client = TestClient(srv.app, raise_server_exceptions=True)

    def stop():
        auth_patch.stop()
        open_patch.stop()

    return client, stop


def test_e2e_stats_per_account_routes_to_correct_strategy(e2e_db):
    """Insert 3 decisions across 2 paper_N accounts; per-account stats returns
    the strategy-correct subset, and the no-account view returns the full set.

    Pins what the dashboard's per-AccountCard render relies on: paper_2 sees
    its wheel decisions, paper_3 sees its iron_condor decisions, neither
    crosses over.
    """
    conn, insert, db_path = e2e_db
    # paper_2 → wheel; paper_3 → iron_condor (per _FAKE_ACCOUNT_CONFIG above)
    insert("wheel", "AAPL", "SELL_PUT")
    insert("wheel", "MSFT", "SKIP", "IV too low")
    insert("iron_condor", "SPY", "OPEN")

    client, stop = _e2e_client(db_path)
    try:
        # paper_2 — wheel only
        r = client.get("/api/decisions/stats?account=paper_2")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total_decisions"] == 2
        assert body["skips"] == 1
        assert body["trades"] == 1

        # paper_3 — iron_condor only
        r = client.get("/api/decisions/stats?account=paper_3")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total_decisions"] == 1
        assert body["skips"] == 0
        assert body["trades"] == 1

        # No account — all rows
        r = client.get("/api/decisions/stats")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total_decisions"] == 3
    finally:
        stop()


def test_stats_helper_diag_captures_pragma_state(mem_conn):
    """The diagnostic snapshot used when COUNT(*) returns None (theoretically
    unreachable) must capture enough state to distinguish hypotheses without
    needing a Render shell next time."""
    from api.server import _stats_helper_diag
    diag = _stats_helper_diag(
        mem_conn, "SELECT COUNT(*) FROM decisions", [],
    )
    assert diag["sql"] == "SELECT COUNT(*) FROM decisions"
    assert diag["params"] == []
    assert "database_list" in diag
    assert "journal_mode" in diag
    # Unfiltered probe must succeed even on an empty table
    assert diag["unfiltered_count_probe"] == 0
    assert diag["unfiltered_count_probe_was_none"] is False
    assert diag["row_factory"] == "Row"


def test_e2e_stats_unknown_account_returns_zeros(e2e_db):
    """An unknown account must not 500 — returns the zero-shape body."""
    conn, insert, db_path = e2e_db
    insert("wheel", "AAPL", "SELL_PUT")
    client, stop = _e2e_client(db_path)
    try:
        r = client.get("/api/decisions/stats?account=does_not_exist")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total_decisions"] == 0
        assert body["skips"] == 0
        assert body["trades"] == 0
    finally:
        stop()


# ── InterfaceError regression: per-request connection isolation ──────────────
# Sentry 7465748118, 7465783250, 7466323729. The API server shared one
# sqlite3.Connection (the check_same_thread=False process singleton) across
# FastAPI's threadpool. Concurrent lazy-cursor iteration in
# _decisions_stats_from_db raised "InterfaceError: bad parameter or other API
# misuse" mid-iteration. _open_db() now returns a fresh connection per request.


def _seed_concurrent_db(db) -> None:
    """Seed iron_butterfly + calendar_spread decision rows (paper_4 / paper_5)."""
    rows = [
        ("iron_butterfly", "SPY", "OPEN", ""),
        ("iron_butterfly", "QQQ", "SKIP", "spread too wide"),
        ("iron_butterfly", "IWM", "HOLD", ""),
        ("calendar_spread", "AAPL", "OPEN", ""),
        ("calendar_spread", "MSFT", "SKIP", "IV term structure flat"),
        ("wheel", "T", "SELL_PUT", ""),
    ]
    for st, ul, act, reasoning in rows:
        db._conn.execute(
            "INSERT INTO decisions (timestamp, strategy_type, underlying, action, reasoning) "
            "VALUES (datetime('now'), ?, ?, ?, ?)",
            (st, ul, act, reasoning),
        )
    db._conn.commit()


def test_open_db_returns_fresh_independent_connection(tmp_path):
    """_open_db() must return a new connection each call, independently
    closeable, and never the get_db() singleton's connection."""
    from database.db import Database, _set_db_for_testing
    from api import server as srv

    db = Database(path=str(tmp_path / "fresh.db"))
    db.init_schema()
    _set_db_for_testing(db)
    try:
        c1 = srv._open_db()
        c2 = srv._open_db()
        assert c1 is not c2
        assert c1 is not db.get_connection()
        assert c2 is not db.get_connection()
        # close() must be a *real* close — the old _ConnectionProxy swallowed
        # it so the singleton stayed alive across requests.
        c1.close()
        with pytest.raises(sqlite3.ProgrammingError):
            c1.execute("SELECT 1")
        # c2 is a wholly independent connection — c1's close does not touch it.
        assert c2.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
        c2.close()
    finally:
        _set_db_for_testing(None)
        db.close()


def test_concurrent_decisions_stats_no_interface_error(tmp_path):
    """N threads hammering /api/decisions/stats concurrently must all return
    200 — no InterfaceError from a shared connection / shared cursor.

    Before the per-request-connection fix in _open_db() this reliably
    produced 500s (Sentry 7465748118 / 7465783250)."""
    import threading
    from fastapi.testclient import TestClient
    from database.db import Database, _set_db_for_testing
    from api import server as srv

    db = Database(path=str(tmp_path / "concurrent.db"))
    db.init_schema()
    _seed_concurrent_db(db)
    _set_db_for_testing(db)

    statuses: list[int] = []
    errors: list[str] = []
    lock = threading.Lock()

    try:
        with patch.object(srv, "_is_authenticated", return_value=True):
            client = TestClient(srv.app, raise_server_exceptions=False)
            accounts = ["paper_4", "paper_5", "paper_3", "paper_2", None]

            def hit(acct):
                url = "/api/decisions/stats"
                if acct:
                    url += f"?account={acct}"
                try:
                    r = client.get(url)
                    with lock:
                        statuses.append(r.status_code)
                        if r.status_code != 200:
                            errors.append(f"{acct}: {r.status_code} {r.text[:200]}")
                except Exception as e:  # pragma: no cover - defensive
                    with lock:
                        errors.append(f"{acct}: raised {e!r}")

            threads = [
                threading.Thread(target=hit, args=(accounts[i % len(accounts)],))
                for i in range(60)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert not errors, f"concurrent stats requests failed: {errors}"
        assert len(statuses) == 60
        assert all(s == 200 for s in statuses)
    finally:
        _set_db_for_testing(None)
        db.close()
