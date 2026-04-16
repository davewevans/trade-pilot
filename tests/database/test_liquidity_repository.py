"""Tests for LiquidityRepository."""

import pytest

from database.db import Database
from database.repositories import LiquidityRepository


@pytest.fixture
def repo(tmp_path):
    db = Database(path=str(tmp_path / "test.db"))
    db.init_schema()
    r = LiquidityRepository(db.get_connection())
    yield r
    db.close()


# ── Snapshot methods ───────────────────────────────────────────────────────────

def _snap(overrides=None):
    base = {
        "symbol": "AAPL",
        "strategy_type": "bull_put_spread",
        "snapshot_date": "2026-01-10",
        "source": "cycle",
        "sample_count": 5,
        "avg_ba_spread_pct": 0.05,
        "avg_oi_at_strikes": 200,
        "volume_to_oi_ratio": 0.10,
        "slippage_estimate": 0.03,
    }
    if overrides:
        base.update(overrides)
    return base


def test_insert_snapshot_round_trip(repo):
    snap_id = repo.insert_snapshot(_snap())
    assert snap_id > 0
    rows = repo.get_snapshots("AAPL", "bull_put_spread", since_date="2026-01-01")
    assert len(rows) == 1
    assert rows[0]["avg_ba_spread_pct"] == pytest.approx(0.05)
    assert rows[0]["avg_oi_at_strikes"] == 200


def test_insert_snapshot_same_unique_key_overwrites(repo):
    repo.insert_snapshot(_snap({"avg_ba_spread_pct": 0.05}))
    repo.insert_snapshot(_snap({"avg_ba_spread_pct": 0.15}))  # same key
    rows = repo.get_snapshots("AAPL", "bull_put_spread", since_date="2026-01-01")
    assert len(rows) == 1
    assert rows[0]["avg_ba_spread_pct"] == pytest.approx(0.15)


def test_get_snapshots_filters_by_since_date(repo):
    repo.insert_snapshot(_snap({"snapshot_date": "2026-01-01"}))
    repo.insert_snapshot(_snap({"snapshot_date": "2026-01-05", "source": "scanner"}))
    rows = repo.get_snapshots("AAPL", "bull_put_spread", since_date="2026-01-04")
    assert len(rows) == 1
    assert rows[0]["snapshot_date"] == "2026-01-05"


def test_get_snapshots_orders_desc(repo):
    for day in ["2026-01-01", "2026-01-03", "2026-01-02"]:
        repo.insert_snapshot(_snap({
            "snapshot_date": day,
            "source": "cycle" if day != "2026-01-03" else "scanner",
        }))
    rows = repo.get_snapshots("AAPL", "bull_put_spread", since_date="2026-01-01")
    dates = [r["snapshot_date"] for r in rows]
    assert dates == sorted(dates, reverse=True)


def test_get_snapshots_unknown_symbol_returns_empty(repo):
    rows = repo.get_snapshots("UNKNOWN", "bull_put_spread", since_date="2026-01-01")
    assert rows == []


# ── Score methods ─────────────────────────────────────────────────────────────

def _score(overrides=None):
    base = {
        "symbol": "AAPL",
        "strategy_type": "bull_put_spread",
        "composite_score": 72.5,
        "tier": "A",
        "lookback_days": 30,
        "snapshot_count": 45,
        "below_floor": False,
        "confidence": "high",
        "sub_metrics": {"norm_ba_spread": 80.0, "norm_oi": 70.0},
    }
    if overrides:
        base.update(overrides)
    return base


def test_upsert_score_inserts_first_time(repo):
    repo.upsert_score(_score())
    row = repo.get_score("AAPL", "bull_put_spread")
    assert row is not None
    assert row["composite_score"] == pytest.approx(72.5)
    assert row["tier"] == "A"


def test_upsert_score_updates_second_time(repo):
    repo.upsert_score(_score({"composite_score": 72.5, "tier": "A"}))
    repo.upsert_score(_score({"composite_score": 55.0, "tier": "B"}))
    row = repo.get_score("AAPL", "bull_put_spread")
    assert row is not None
    assert row["composite_score"] == pytest.approx(55.0)
    assert row["tier"] == "B"


def test_get_score_returns_none_for_unknown(repo):
    assert repo.get_score("UNKNOWN", "bull_put_spread") is None


def test_get_score_parses_sub_metrics_json(repo):
    repo.upsert_score(_score({"sub_metrics": {"norm_ba_spread": 80.0, "norm_oi": 70.0}}))
    row = repo.get_score("AAPL", "bull_put_spread")
    assert row is not None
    assert isinstance(row["sub_metrics"], dict)
    assert row["sub_metrics"]["norm_ba_spread"] == pytest.approx(80.0)


def test_get_all_scores_filters_by_strategy_type(repo):
    repo.upsert_score(_score({"symbol": "AAPL", "strategy_type": "bull_put_spread"}))
    repo.upsert_score(_score({"symbol": "MSFT", "strategy_type": "bear_call_spread"}))
    rows = repo.get_all_scores(strategy_type="bull_put_spread")
    assert len(rows) == 1
    assert rows[0]["symbol"] == "AAPL"


def test_get_all_scores_sorts_by_composite_desc(repo):
    repo.upsert_score(_score({"symbol": "AAPL", "composite_score": 60.0}))
    repo.upsert_score(_score({"symbol": "MSFT", "composite_score": 80.0}))
    rows = repo.get_all_scores(strategy_type="bull_put_spread")
    assert rows[0]["composite_score"] > rows[1]["composite_score"]


# ── get_multiplier ─────────────────────────────────────────────────────────────

def test_get_multiplier_kill_switch_off(repo, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "RESEARCH_SCORE_MULTIPLIER_ENABLED", False)
    repo.upsert_score(_score({"tier": "A", "confidence": "high"}))
    mult, tier, conf = repo.get_multiplier("AAPL", "bull_put_spread")
    assert mult == pytest.approx(1.0)
    assert tier == "B"
    assert conf == "disabled"


def test_get_multiplier_no_row_returns_neutral(repo):
    mult, tier, conf = repo.get_multiplier("AAPL", "bull_put_spread")
    assert mult == pytest.approx(1.0)
    assert tier == "B"
    assert conf == "none"


def test_get_multiplier_tier_a_high_conf(repo, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "RESEARCH_SCORE_MULTIPLIER_ENABLED", True)
    repo.upsert_score(_score({"tier": "A", "confidence": "high"}))
    mult, tier, conf = repo.get_multiplier("AAPL", "bull_put_spread")
    assert mult == pytest.approx(1.20)
    assert tier == "A"
    assert conf == "high"


def test_get_multiplier_tier_d_returns_zero(repo, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "RESEARCH_SCORE_MULTIPLIER_ENABLED", True)
    repo.upsert_score(_score({"tier": "D", "confidence": "high"}))
    mult, tier, conf = repo.get_multiplier("AAPL", "bull_put_spread")
    assert mult == pytest.approx(0.0)
    assert tier == "D"


def test_get_multiplier_tier_a_low_conf_is_neutral(repo, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "RESEARCH_SCORE_MULTIPLIER_ENABLED", True)
    repo.upsert_score(_score({"tier": "A", "confidence": "low"}))
    mult, tier, conf = repo.get_multiplier("AAPL", "bull_put_spread")
    # Tier preserved, but multiplier is neutral (1.0) for low confidence
    assert mult == pytest.approx(1.0)
    assert tier == "A"
    assert conf == "low"
