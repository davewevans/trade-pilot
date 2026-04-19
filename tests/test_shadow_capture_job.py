"""Tests for jobs.shadow_capture."""

import sys
import types
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from database.db import Database
from database.repositories.shadow_execution import ShadowExecutionRepository


# ── Stub heavy transitive imports that are broken in this dev environment ─────
# scheduler.py imports all jobs → market_data → numpy (incompatible binary).
# We stub the module so _run_inner()'s `from scheduler import is_weekday` works.

@pytest.fixture(autouse=True)
def _stub_scheduler(monkeypatch):
    """Insert a minimal scheduler stub so numpy is never reached."""
    stub = types.ModuleType("scheduler")
    stub.is_weekday = lambda: True  # default: weekday
    monkeypatch.setitem(sys.modules, "scheduler", stub)
    yield stub


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _stub_market_hours(monkeypatch):
    """Patch datetime in shadow_capture to always be 10:00 ET (within market hours)."""
    import datetime as _dt_mod
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")

    class _MarketHoursDT(_dt_mod.datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None and str(tz) == "America/New_York":
                return _dt_mod.datetime(2026, 4, 21, 10, 0, tzinfo=ET)
            return _dt_mod.datetime.now(tz)

    monkeypatch.setattr("jobs.shadow_capture.datetime", _MarketHoursDT)


@pytest.fixture
def db(tmp_path):
    db = Database(path=str(tmp_path / "test.db"))
    db.init_schema()
    yield db
    db.close()


@pytest.fixture
def repo(db):
    return ShadowExecutionRepository(db.get_connection())


@pytest.fixture(autouse=True)
def _patch_db_path(tmp_path, db, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "DATABASE_PATH", tmp_path / "test.db")
    monkeypatch.setattr(settings, "SHADOW_EXECUTION_ENABLED", True)


def _seed_pending(repo, label, submitted_at=None, attempts=0):
    """Insert a shadow_executions row with the given label pending."""
    ts = submitted_at or datetime.now(timezone.utc) - timedelta(minutes=5)
    ts_iso = ts.strftime("%Y-%m-%dT%H:%M:%S")
    parent = {
        "submitted_at": ts_iso,
        "submitted_at_et": ts_iso,
        "strategy_type": "sell_put",
        "action": "sell_put",
        "underlying": "SPY",
        "alpaca_order_id": None,
        "order_kind": "single_leg",
        "is_credit": 1,
        "net_limit_abs": 1.50,
        "t0_status": "captured",
        "t0_attempts": 1,
        "t0_class": "always_fillable",
        "t0_net_bid": 1.00,
        "t0_net_mid": 1.25,
        "t0_net_ask": 1.50,
        "t0_captured_at": ts_iso,
    }
    exec_id = repo.insert_submission(
        parent_row=parent,
        legs=[
            {
                "contract_symbol": "SPY240119P00500000",
                "leg_role": "short_put",
                "side": "sell",
                "position_intent": "sell_to_open",
                "t0_bid": 1.00,
                "t0_ask": 1.50,
                "t0_mid": 1.25,
                "t0_source": "orats",
            }
        ],
    )
    # Set the specific label's attempts (insert_submission only covers t0 columns)
    if attempts > 0:
        conn = repo._conn
        conn.execute(
            f"UPDATE shadow_executions SET {label}_attempts = ? WHERE id = ?",
            (attempts, exec_id),
        )
        conn.commit()
    return exec_id


def _good_quotes():
    return {
        "SPY240119P00500000": {
            "bid": 0.90, "ask": 1.10, "mid": 1.00, "source": "orats"
        }
    }


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestEarlyReturns:
    def test_flag_off_skips_everything(self, monkeypatch, db):
        from config import settings
        monkeypatch.setattr(settings, "SHADOW_EXECUTION_ENABLED", False)
        from jobs.shadow_capture import run
        run()  # should not raise and should not touch db

    def test_weekend_skips_everything(self, monkeypatch, db, repo, _stub_scheduler):
        _seed_pending(repo, "t30s")
        _stub_scheduler.is_weekday = lambda: False
        from jobs.shadow_capture import _run_inner
        _run_inner()
        conn = db.get_connection()
        row = conn.execute("SELECT t30s_status FROM shadow_executions").fetchone()
        assert row[0] == "pending"

    def test_outside_market_hours_skips(self, monkeypatch, db, repo):
        import datetime as _dt_mod
        from zoneinfo import ZoneInfo
        _seed_pending(repo, "t30s")
        # Override autouse fixture — simulate 06:00 ET (before market hours)
        ET = ZoneInfo("America/New_York")
        class _EarlyDT(_dt_mod.datetime):
            @classmethod
            def now(cls, tz=None):
                return _dt_mod.datetime(2026, 4, 21, 6, 0, tzinfo=ET)
        monkeypatch.setattr("jobs.shadow_capture.datetime", _EarlyDT)
        from jobs.shadow_capture import _run_inner
        _run_inner()
        conn = db.get_connection()
        row = conn.execute("SELECT t30s_status FROM shadow_executions").fetchone()
        assert row[0] == "pending"


class TestCapture:
    def test_t30s_due_row_gets_captured(self, monkeypatch, db, repo):
        exec_id = _seed_pending(repo, "t30s")
        # Patch at source — job imports this inside _run_inner() so module attr is resolved then
        monkeypatch.setattr(
            "data.shadow_execution.fetch_leg_quotes_for_capture",
            lambda legs: _good_quotes(),
        )

        from jobs.shadow_capture import _run_inner
        _run_inner()

        conn = db.get_connection()
        row = conn.execute(
            "SELECT t30s_status, t30s_class FROM shadow_executions WHERE id = ?",
            (exec_id,),
        ).fetchone()
        assert row[0] == "captured"
        assert row[1] is not None

    def test_nbbo_fetch_raises_marks_failed_retryable(self, monkeypatch, db, repo):
        exec_id = _seed_pending(repo, "t30s", attempts=0)

        def _raise(legs):
            raise ConnectionError("timeout")
        monkeypatch.setattr("data.shadow_execution.fetch_leg_quotes_for_capture", _raise)

        from jobs.shadow_capture import _run_inner
        _run_inner()

        conn = db.get_connection()
        row = conn.execute(
            "SELECT t30s_status, t30s_attempts FROM shadow_executions WHERE id = ?",
            (exec_id,),
        ).fetchone()
        assert row[0] == "failed_retryable"
        assert row[1] == 1

    def test_max_attempts_marks_failed_permanent(self, monkeypatch, db, repo):
        from jobs.shadow_capture import _MAX_ATTEMPTS
        exec_id = _seed_pending(repo, "t30s", attempts=_MAX_ATTEMPTS)

        from jobs.shadow_capture import _run_inner
        _run_inner()

        conn = db.get_connection()
        row = conn.execute(
            "SELECT t30s_status FROM shadow_executions WHERE id = ?", (exec_id,)
        ).fetchone()
        assert row[0] == "failed_permanent"

    def test_past_trading_day_marks_failed_permanent(self, monkeypatch, db, repo):
        old_ts = datetime.now(timezone.utc) - timedelta(days=2)
        exec_id = _seed_pending(repo, "t30s", submitted_at=old_ts)

        from jobs.shadow_capture import _run_inner
        _run_inner()

        conn = db.get_connection()
        row = conn.execute(
            "SELECT t30s_status FROM shadow_executions WHERE id = ?", (exec_id,)
        ).fetchone()
        assert row[0] == "failed_permanent"

    def test_eod_unavailable_marks_data_unavailable_class(self, monkeypatch, db, repo):
        exec_id = _seed_pending(repo, "eod")

        def _unavail(legs):
            return {
                leg["contract_symbol"]: {
                    "bid": None, "ask": None, "mid": None, "source": "unavailable"
                }
                for leg in legs
            }
        monkeypatch.setattr("data.shadow_execution.fetch_leg_quotes_for_capture", _unavail)

        from jobs.shadow_capture import _run_inner
        _run_inner()

        conn = db.get_connection()
        row = conn.execute(
            "SELECT eod_status, eod_class, eod_source_detail FROM shadow_executions WHERE id = ?",
            (exec_id,),
        ).fetchone()
        assert row[0] == "failed_permanent"
        assert row[1] == "data_unavailable"
        assert row[2] == "unavailable"


class TestCompleted:
    def test_all_terminal_flips_completed(self, monkeypatch, db, repo):
        exec_id = _seed_pending(repo, "t30s")
        monkeypatch.setattr("data.shadow_execution.fetch_leg_quotes_for_capture",
                            lambda legs: _good_quotes())

        # Manually force all other labels to terminal so completion can flip
        conn = db.get_connection()
        conn.execute(
            """
            UPDATE shadow_executions
            SET t2m_status='captured', t15m_status='captured', eod_status='captured'
            WHERE id = ?
            """,
            (exec_id,),
        )
        conn.commit()

        from jobs.shadow_capture import _run_inner
        _run_inner()

        row = conn.execute(
            "SELECT completed FROM shadow_executions WHERE id = ?", (exec_id,)
        ).fetchone()
        assert row[0] == 1

    def test_already_completed_row_not_touched(self, monkeypatch, db, repo):
        exec_id = _seed_pending(repo, "t30s")
        conn = db.get_connection()
        # Mark completed
        conn.execute(
            "UPDATE shadow_executions SET completed = 1 WHERE id = ?", (exec_id,)
        )
        conn.commit()

        # Patch get_due to confirm it returns nothing (completed=0 filter)
        from jobs.shadow_capture import _run_inner
        _run_inner()

        # Verify row count: nothing new updated (get_due excludes completed=1)
        row = conn.execute(
            "SELECT t30s_status FROM shadow_executions WHERE id = ?", (exec_id,)
        ).fetchone()
        # t30s_status should remain as it was (pending) since the row was excluded
        assert row[0] == "pending"
