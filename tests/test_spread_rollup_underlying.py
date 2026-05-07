"""Regression tests for the spread cycle-rollup decision row.

Background: on 2026-05-07 the daily bundle showed a single misleading
``bull_put_spread`` SKIP entry headed "AAPL · bull_put_spread · No qualifying
candidates across SPREAD_WATCHLIST (last skip: Underlying below 50-day SMA)".
The fix in this PR:

1. Stops falling back to ``settings.WATCHLIST[0]`` for the rollup row's
   underlying — writes NULL instead so per-underlying queries cannot pick
   up the rollup as if it were a per-symbol decision.
2. Builds the rollup reasoning from aggregate counts across the screen and
   pre-check passes instead of just the last-processed symbol's reason.

Tests pin (a) the schema migration (NOT NULL → NULL on existing tables is
idempotent), (b) the recorder accepting ``underlying=None``, and (c) the
aggregate reasoning helper.
"""

import sqlite3

import pytest

from database.db import Database
from database.recorder import TradeRecorder
from database.repositories import DecisionRepository
from jobs.market_open import _build_no_candidates_reason


# ── Schema migration: NOT NULL → NULL is idempotent ────────────────────────


def _legacy_schema(db_path: str) -> sqlite3.Connection:
    """Build a fresh SQLite file with the old NOT NULL underlying constraint."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
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
        """
    )
    conn.commit()
    return conn


def test_migration_relaxes_underlying_to_nullable(tmp_path):
    """An existing DB with NOT NULL underlying must be rebuilt to allow NULL."""
    db_path = tmp_path / "legacy.db"
    legacy = _legacy_schema(str(db_path))
    legacy.execute(
        "INSERT INTO decisions (timestamp, strategy_type, underlying, action) "
        "VALUES (?, ?, ?, ?)",
        ("2026-05-01T10:00:00", "wheel", "AAPL", "SKIP"),
    )
    legacy.commit()
    legacy.close()

    db = Database(path=str(db_path))
    db.init_schema()  # runs migrations including the underlying nullable rebuild
    conn = db.get_connection()

    cols = conn.execute("PRAGMA table_info(decisions)").fetchall()
    underlying = next(c for c in cols if c[1] == "underlying")
    # PRAGMA table_info: cid, name, type, notnull, dflt_value, pk
    assert underlying[3] == 0, "underlying must be nullable after migration"

    # Existing row preserved
    row = conn.execute(
        "SELECT underlying FROM decisions WHERE strategy_type='wheel'"
    ).fetchone()
    assert row[0] == "AAPL"
    db.close()


def test_migration_is_idempotent(tmp_path):
    """Running init_schema twice must not error or duplicate rows."""
    db_path = tmp_path / "idempotent.db"
    db = Database(path=str(db_path))
    db.init_schema()
    db.init_schema()  # second run — should be a no-op
    conn = db.get_connection()
    cols = conn.execute("PRAGMA table_info(decisions)").fetchall()
    underlying = next(c for c in cols if c[1] == "underlying")
    assert underlying[3] == 0
    db.close()


def test_fresh_install_has_nullable_underlying(tmp_path):
    """A freshly created DB has nullable underlying without needing migration."""
    db = Database(path=str(tmp_path / "fresh.db"))
    db.init_schema()
    conn = db.get_connection()
    cols = conn.execute("PRAGMA table_info(decisions)").fetchall()
    underlying = next(c for c in cols if c[1] == "underlying")
    assert underlying[3] == 0
    db.close()


# ── Recorder accepts underlying=None for rollup rows ────────────────────────


@pytest.fixture
def repo(tmp_path):
    db = Database(path=str(tmp_path / "rollup.db"))
    db.init_schema()
    rec = TradeRecorder(db.get_connection())
    yield rec, DecisionRepository(db.get_connection()), db.get_connection()
    db.close()


def test_record_decision_writes_null_underlying_for_rollup(repo):
    """A cycle-level rollup SKIP must persist with NULL underlying."""
    rec, decisions, conn = repo
    rec.record_decision(
        strategy_type="bull_put_spread",
        underlying=None,
        action="SKIP",
        reasoning="No qualifying candidates across SPREAD_WATCHLIST (30 symbols).",
    )
    row = conn.execute(
        "SELECT underlying, strategy_type, action FROM decisions "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["underlying"] is None
    assert row["strategy_type"] == "bull_put_spread"
    assert row["action"] == "SKIP"


def test_per_underlying_query_excludes_rollup(repo):
    """``WHERE underlying = 'X'`` must not match NULL-underlying rollup rows."""
    rec, decisions, conn = repo
    # One rollup, one real per-symbol decision
    rec.record_decision(
        strategy_type="bull_put_spread", underlying=None,
        action="SKIP", reasoning="rollup",
    )
    rec.record_decision(
        strategy_type="wheel", underlying="AAPL",
        action="SELL_PUT", reasoning="real",
    )
    rows = decisions.get_by_underlying("AAPL")
    assert len(rows) == 1
    assert rows[0]["underlying"] == "AAPL"


# ── Aggregate reasoning builder ─────────────────────────────────────────────


def test_aggregate_reasoning_uses_screen_and_precheck_counts():
    reason = _build_no_candidates_reason(
        wl_name="SPREAD_WATCHLIST",
        watchlist_size=30,
        screen_survivors=20,
        screen_rejections=[
            {"symbol": "X", "reason": "outside_iv_env"},
            {"symbol": "Y", "reason": "outside_iv_env"},
            {"symbol": "Z", "reason": "earnings_too_close"},
        ],
        precheck_rejected={
            "Underlying below 50-day SMA": 12,
            "IV/HV ratio 0.83 < 0.90": 5,
            "Earnings in 18 days (need > 25)": 3,
        },
    )
    assert "SPREAD_WATCHLIST (30 symbols)" in reason
    assert "Pre-screen rejections (3/30)" in reason
    assert "2 outside_iv_env" in reason
    assert "Deep-eval rejections (20/20 survivors)" in reason
    assert "12 Underlying below 50-day SMA" in reason
    # No reference to the buggy "last skip" framing
    assert "last skip" not in reason


def test_aggregate_reasoning_surfaces_unaccounted_residual():
    """When counts don't sum to watchlist size, the gap is surfaced explicitly.

    Symbols can drop out for reasons not captured in either bucket (data fetch
    failure, exception). The operator should see this rather than have it hidden.
    """
    reason = _build_no_candidates_reason(
        wl_name="SPREAD_WATCHLIST",
        watchlist_size=30,
        screen_survivors=20,
        screen_rejections=[{"symbol": "A", "reason": "outside_iv_env"}] * 10,
        precheck_rejected={"Underlying below 50-day SMA": 15},
        # 10 + 15 = 25; watchlist is 30 → 5 unaccounted
    )
    assert "Unaccounted: 5" in reason


def test_aggregate_reasoning_handles_zero_buckets():
    """Empty rejection buckets render cleanly without crashing."""
    reason = _build_no_candidates_reason(
        wl_name="SPREAD_WATCHLIST",
        watchlist_size=10,
        screen_survivors=0,
        screen_rejections=[],
        precheck_rejected={},
    )
    assert "0/10" in reason
    assert "none" in reason  # both buckets empty
