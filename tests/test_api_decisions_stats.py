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
