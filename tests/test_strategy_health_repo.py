"""Tests for StrategyHealthRepository.get_strategy_health_funnel.

These tests exercise the funnel-bucketing logic, ISO week boundaries,
the 'silent strategy' zero-row requirement, and top-reason computation.

All tests use an in-memory SQLite database with the production schema
initialised — no mocking of the DB layer.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from database.db import Database
from database.repositories.strategy_health import (
    StrategyHealthRepository,
    _GATE_TO_BUCKET,
    _monday_of_week,
)
from strategies.skip_reasons import SkipGate


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path):
    """In-memory database with full schema."""
    db_instance = Database(path=tmp_path / "test.db")
    db_instance.init_schema()
    yield db_instance
    db_instance.close()


@pytest.fixture
def repo(db):
    return StrategyHealthRepository(db.get_connection())


# ── Seed helpers ──────────────────────────────────────────────────────────────


def _insert_decision(conn: sqlite3.Connection, **fields) -> None:
    """Insert a decision with sensible defaults."""
    defaults = {
        "timestamp": "2026-04-14T10:00:00",  # Monday
        "strategy_type": "wheel",
        "underlying": "SPY",
        "action": "SKIP",
        "skip_gate": "pre_check",
        "skip_reason_code": None,
        "reasoning": None,
    }
    defaults.update(fields)
    conn.execute(
        """
        INSERT INTO decisions (
            timestamp, strategy_type, underlying,
            action, reasoning, skip_gate, skip_reason_code
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            defaults["timestamp"],
            defaults["strategy_type"],
            defaults["underlying"],
            defaults["action"],
            defaults.get("reasoning"),
            defaults.get("skip_gate"),
            defaults.get("skip_reason_code"),
        ),
    )
    conn.commit()


def _insert_trade(conn: sqlite3.Connection, **fields) -> None:
    """Insert a trade with sensible defaults."""
    import uuid

    defaults = {
        "cycle_id": str(uuid.uuid4()),
        "alpaca_order_id": str(uuid.uuid4()),
        "underlying": "SPY",
        "strategy_type": "wheel",
        "trade_type": "SELL_PUT",
        "symbol": "SPY240419P00400000",
        "limit_price": 1.50,
        "submitted_at": "2026-04-14T10:00:00",
        "fill_price": None,
        "outcome": None,
    }
    defaults.update(fields)
    conn.execute(
        """
        INSERT INTO trades (
            cycle_id, alpaca_order_id, underlying,
            strategy_type, trade_type, symbol,
            limit_price, submitted_at, fill_price, outcome
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            defaults["cycle_id"],
            defaults["alpaca_order_id"],
            defaults["underlying"],
            defaults["strategy_type"],
            defaults["trade_type"],
            defaults["symbol"],
            defaults["limit_price"],
            defaults["submitted_at"],
            defaults.get("fill_price"),
            defaults.get("outcome"),
        ),
    )
    conn.commit()


def _current_week_monday() -> datetime:
    """Monday 00:00 UTC of the current week."""
    return _monday_of_week(datetime.now(timezone.utc))


def _iso(dt: datetime) -> str:
    """Format datetime as SQLite-compatible naive ISO string."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestEmptyDb:
    def test_empty_db_returns_empty_list(self, repo):
        result = repo.get_strategy_health_funnel(weeks_back=4)
        assert result == []


class TestSingleStrategyWeek:
    """Basic bucket counting for a single (strategy, week)."""

    def test_single_strategy_single_week_counts_correctly(self, db, repo):
        conn = db.get_connection()
        monday = _current_week_monday()
        ts = _iso(monday + timedelta(hours=10))

        # pre_check skip × 3
        for _ in range(3):
            _insert_decision(conn, timestamp=ts, action="SKIP", skip_gate="pre_check")
        # claude_skip × 2
        for _ in range(2):
            _insert_decision(
                conn, timestamp=ts, action="SKIP",
                skip_gate="claude_skip", skip_reason_code="LOW_IVR",
            )
        # guardrail × 1
        _insert_decision(conn, timestamp=ts, action="SKIP", skip_gate="guardrail")
        # hold × 1
        _insert_decision(conn, timestamp=ts, action="HOLD", skip_gate=None)
        # proposed × 1
        _insert_decision(conn, timestamp=ts, action="SELL_PUT", skip_gate=None)

        rows = repo.get_strategy_health_funnel(weeks_back=1)
        assert len(rows) == 1
        r = rows[0]

        assert r["decisions_total"] == 8
        assert r["skip_pre_check"] == 3
        assert r["skip_claude"] == 2
        assert r["skip_guardrail"] == 1
        assert r["hold"] == 1
        assert r["actions_proposed"] == 1
        assert r["skip_unclassified"] == 0

    def test_guardrail_rejection_not_counted_as_skip_claude(self, db, repo):
        """Rows with skip_gate='guardrail' must land in skip_guardrail, not skip_claude."""
        conn = db.get_connection()
        ts = _iso(_current_week_monday() + timedelta(hours=10))
        _insert_decision(conn, timestamp=ts, action="SKIP", skip_gate="guardrail")

        rows = repo.get_strategy_health_funnel(weeks_back=1)
        assert len(rows) == 1
        r = rows[0]
        assert r["skip_guardrail"] == 1
        assert r["skip_claude"] == 0

    def test_hold_counted_separately_from_skip(self, db, repo):
        """HOLD action must not increment any skip bucket."""
        conn = db.get_connection()
        ts = _iso(_current_week_monday() + timedelta(hours=10))
        _insert_decision(conn, timestamp=ts, action="HOLD", skip_gate=None)

        rows = repo.get_strategy_health_funnel(weeks_back=1)
        r = rows[0]
        assert r["hold"] == 1
        total_skips = (
            r["skip_pre_check"] + r["skip_claude"] + r["skip_guardrail"]
            + r["skip_circuit_breaker"] + r["skip_liquidity_floor"]
            + r["skip_winrate_floor"] + r["skip_no_candidate"]
            + r["skip_data_missing"] + r["skip_halted"] + r["skip_unclassified"]
        )
        assert total_skips == 0


class TestTopReasonComputation:
    def test_top_claude_skip_reason_picks_most_common(self, db, repo):
        conn = db.get_connection()
        ts = _iso(_current_week_monday() + timedelta(hours=10))

        for _ in range(5):
            _insert_decision(
                conn, timestamp=ts, action="SKIP",
                skip_gate="claude_skip", skip_reason_code="LOW_IVR",
            )
        for _ in range(2):
            _insert_decision(
                conn, timestamp=ts, action="SKIP",
                skip_gate="claude_skip", skip_reason_code="LIQUIDITY_INSUFFICIENT",
            )

        rows = repo.get_strategy_health_funnel(weeks_back=1)
        assert rows[0]["top_claude_skip_reason"] == "LOW_IVR"

    def test_top_claude_skip_reason_handles_tie_deterministically(self, db, repo):
        """Tie between two reasons — result must be deterministic (same on every call)."""
        conn = db.get_connection()
        ts = _iso(_current_week_monday() + timedelta(hours=10))

        for _ in range(3):
            _insert_decision(
                conn, timestamp=ts, action="SKIP",
                skip_gate="claude_skip", skip_reason_code="REASON_A",
            )
        for _ in range(3):
            _insert_decision(
                conn, timestamp=ts, action="SKIP",
                skip_gate="claude_skip", skip_reason_code="REASON_B",
            )

        result1 = repo.get_strategy_health_funnel(weeks_back=1)[0]["top_claude_skip_reason"]
        result2 = repo.get_strategy_health_funnel(weeks_back=1)[0]["top_claude_skip_reason"]
        assert result1 == result2
        assert result1 in ("REASON_A", "REASON_B")

    def test_top_claude_skip_reason_none_when_no_claude_skips(self, db, repo):
        conn = db.get_connection()
        ts = _iso(_current_week_monday() + timedelta(hours=10))
        _insert_decision(conn, timestamp=ts, action="SKIP", skip_gate="pre_check")

        rows = repo.get_strategy_health_funnel(weeks_back=1)
        assert rows[0]["top_claude_skip_reason"] is None


class TestTradeCounts:
    def test_trade_counts_linked_by_strategy_type_and_week(self, db, repo):
        """Trades submitted this week must appear in the trade count buckets."""
        conn = db.get_connection()
        monday = _current_week_monday()
        ts = _iso(monday + timedelta(hours=10))

        # Insert a decision so the strategy appears
        _insert_decision(conn, timestamp=ts, action="SELL_PUT", skip_gate=None)
        # Trade with fill
        _insert_trade(conn, submitted_at=ts, fill_price=1.50, outcome=None)
        # Trade without fill (pending)
        _insert_trade(conn, submitted_at=ts, fill_price=None, outcome=None)

        rows = repo.get_strategy_health_funnel(weeks_back=1)
        r = rows[0]
        assert r["trades_submitted"] == 2
        assert r["trades_filled"] == 1
        assert r["trades_pending"] == 1

    def test_pending_counted_separately_from_filled_or_closed(self, db, repo):
        """fill_price IS NULL AND outcome IS NULL → pending; not filled; not closed."""
        conn = db.get_connection()
        ts = _iso(_current_week_monday() + timedelta(hours=10))
        _insert_decision(conn, timestamp=ts, action="SELL_PUT", skip_gate=None)
        _insert_trade(conn, submitted_at=ts, fill_price=None, outcome=None)  # pending

        rows = repo.get_strategy_health_funnel(weeks_back=1)
        r = rows[0]
        assert r["trades_pending"] == 1
        assert r["trades_filled"] == 0
        assert r["trades_closed_profit"] == 0
        assert r["trades_closed_loss"] == 0

    def test_breakeven_and_unknown_outcomes_each_have_their_own_bucket(self, db, repo):
        """All four outcome values land in distinct buckets."""
        conn = db.get_connection()
        ts = _iso(_current_week_monday() + timedelta(hours=10))
        _insert_decision(conn, timestamp=ts, action="SELL_PUT", skip_gate=None)

        for outcome in ("profit", "loss", "breakeven", "unknown"):
            _insert_trade(conn, submitted_at=ts, fill_price=1.50, outcome=outcome)

        rows = repo.get_strategy_health_funnel(weeks_back=1)
        r = rows[0]
        assert r["trades_closed_profit"] == 1
        assert r["trades_closed_loss"] == 1
        assert r["trades_closed_breakeven"] == 1
        assert r["trades_closed_unknown"] == 1
        assert r["trades_submitted"] == 4


class TestWeekHandling:
    def test_weeks_back_parameter_truncates_correctly(self, db, repo):
        """weeks_back=1 returns only the current partial week."""
        conn = db.get_connection()
        monday = _current_week_monday()

        # Current week
        _insert_decision(conn, timestamp=_iso(monday + timedelta(hours=10)))
        # Two weeks ago — must NOT appear
        _insert_decision(conn, timestamp=_iso(monday - timedelta(weeks=2) + timedelta(hours=10)))

        rows = repo.get_strategy_health_funnel(weeks_back=1)
        iso_weeks = {r["iso_week"] for r in rows}
        assert len(iso_weeks) == 1
        assert iso_weeks == {monday.strftime("%Y-%W")}

    def test_iso_week_boundary_monday_midnight_utc(self, db, repo):
        """Sunday 23:59:59 UTC → older week; Monday 00:00:01 UTC → newer week."""
        conn = db.get_connection()
        monday = _current_week_monday()
        sunday_end = monday - timedelta(seconds=1)   # 23:59:59 last Sunday
        monday_start = monday + timedelta(seconds=1)  # 00:00:01 this Monday

        _insert_decision(
            conn, timestamp=_iso(sunday_end),
            strategy_type="iron_condor",
        )
        _insert_decision(
            conn, timestamp=_iso(monday_start),
            strategy_type="wheel",
        )

        rows = repo.get_strategy_health_funnel(weeks_back=2)
        by_week: dict[str, list] = {}
        for r in rows:
            by_week.setdefault(r["iso_week"], []).append(r)

        # Sunday decision must be in a different week than Monday decision
        weeks_with_data = [
            iw for iw, rlist in by_week.items()
            if any(r["decisions_total"] > 0 for r in rlist)
        ]
        assert len(weeks_with_data) == 2
        assert sorted(weeks_with_data)[0] < sorted(weeks_with_data)[1]

    def test_zero_decisions_in_week_returns_row_with_zeros(self, db, repo):
        """A strategy active in week N-1 appears in week N with all-zero counts."""
        conn = db.get_connection()
        monday = _current_week_monday()

        # Insert decision ONLY in the prior week
        _insert_decision(
            conn,
            timestamp=_iso(monday - timedelta(weeks=1) + timedelta(hours=10)),
            strategy_type="wheel",
        )

        rows = repo.get_strategy_health_funnel(weeks_back=2)
        # Filter to 'wheel' strategy, current week
        current_iso = monday.strftime("%Y-%W")
        wheel_current = [r for r in rows if r["strategy_type"] == "wheel" and r["iso_week"] == current_iso]

        assert len(wheel_current) == 1, "silent week must still produce a row"
        r = wheel_current[0]
        assert r["decisions_total"] == 0
        assert r["skip_claude"] == 0
        assert r["trades_submitted"] == 0


class TestStrategyFilter:
    def test_strategy_types_filter_applied(self, db, repo):
        conn = db.get_connection()
        ts = _iso(_current_week_monday() + timedelta(hours=10))

        _insert_decision(conn, timestamp=ts, strategy_type="wheel")
        _insert_decision(conn, timestamp=ts, strategy_type="iron_condor")

        rows = repo.get_strategy_health_funnel(weeks_back=1, strategy_types=["wheel"])
        assert all(r["strategy_type"] == "wheel" for r in rows)
        assert len(rows) == 1


class TestUnclassifiedBucket:
    def test_skip_unclassified_catches_null_skip_gate(self, db, repo):
        """SKIP with skip_gate=NULL lands in skip_unclassified, nowhere else."""
        conn = db.get_connection()
        ts = _iso(_current_week_monday() + timedelta(hours=10))
        _insert_decision(conn, timestamp=ts, action="SKIP", skip_gate=None)

        rows = repo.get_strategy_health_funnel(weeks_back=1)
        r = rows[0]
        assert r["skip_unclassified"] == 1
        # Must not appear in any named bucket
        named_buckets = [
            "skip_pre_check", "skip_claude", "skip_guardrail", "skip_circuit_breaker",
            "skip_liquidity_floor", "skip_winrate_floor", "skip_no_candidate",
            "skip_data_missing", "skip_halted",
        ]
        for b in named_buckets:
            assert r[b] == 0, f"Expected 0 in {b}, got {r[b]}"

    def test_skip_unclassified_catches_unknown_skip_gate_value(self, db, repo):
        """SKIP with an unrecognised skip_gate value lands in skip_unclassified."""
        conn = db.get_connection()
        ts = _iso(_current_week_monday() + timedelta(hours=10))
        # Directly write an unrecognised gate string (not in the enum)
        conn.execute(
            """
            INSERT INTO decisions (timestamp, strategy_type, underlying, action, skip_gate)
            VALUES (?, 'wheel', 'SPY', 'SKIP', 'future_gate_not_yet_in_code')
            """,
            (ts,),
        )
        conn.commit()

        rows = repo.get_strategy_health_funnel(weeks_back=1)
        assert rows[0]["skip_unclassified"] == 1


class TestEnumCoverage:
    def test_all_skipgate_enum_values_have_a_bucket(self):
        """Every SkipGate enum value must map to exactly one bucket in _GATE_TO_BUCKET.

        This prevents silent rot: if someone adds a new SkipGate value without
        updating the funnel mapping, this test fails at CI time rather than
        silently sending rows to skip_unclassified in production.
        """
        for gate in SkipGate:
            assert gate.value in _GATE_TO_BUCKET, (
                f"SkipGate.{gate.name} (value={gate.value!r}) has no entry in "
                f"_GATE_TO_BUCKET. Add it and choose the correct funnel bucket."
            )
