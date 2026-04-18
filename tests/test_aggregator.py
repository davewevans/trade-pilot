"""Tests for evaluation.aggregator.aggregate_scores.

Covers:
  - Empty month returns empty structure
  - Mean, median, stddev correctness on synthetic score sets
  - by_prompt_version grouping
  - Multiple strategies are separated correctly
  - Overall stats aggregate across all strategies
  - decisions_scored counts unique decision IDs (deduplicates multi-scorer rows)
  - Month filter isolates the target month
  - closed_trades_in_window counts CLOSE actions from decisions table
  - _lowest_decision_ids populated with lowest-scoring decisions first
"""

import json
import statistics

import pytest

from database.db import Database
from database.repositories.decision_scores_repository import DecisionScoresRepository
from evaluation.aggregator import aggregate_scores


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    d = Database(path=str(tmp_path / "agg_test.db"))
    d.init_schema()
    yield d
    d.close()


@pytest.fixture
def conn(db):
    return db.get_connection()


@pytest.fixture
def scores_repo(conn):
    return DecisionScoresRepository(conn)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _insert_decision(
    conn,
    strategy_type: str = "wheel",
    prompt_version: str = "v1",
    timestamp: str = "2026-04-10T10:00:00",
    action: str = "SELL_PUT",
) -> int:
    """Insert a minimal valid decision row and return its auto-assigned id."""
    cur = conn.execute(
        "INSERT INTO decisions (timestamp, strategy_type, underlying, action, prompt_version) "
        "VALUES (?, ?, ?, ?, ?)",
        (timestamp, strategy_type, "SPY", action, prompt_version),
    )
    conn.commit()
    return cur.lastrowid


def _score_row(
    decision_id: int,
    dimensions: list[tuple[str, float]],
    scorer_type: str = "programmatic",
    scored_at: str = "2026-04-15T10:00:00",
) -> dict:
    """Build a decision_scores insert dict with the given dimension scores."""
    dim_list = [
        {"dimension": dim, "score": score, "score_metadata": {}}
        for dim, score in dimensions
    ]
    avg = sum(s for _, s in dimensions) / len(dimensions) if dimensions else 0.0
    return {
        "decision_id": decision_id,
        "scorer_type": scorer_type,
        "scored_at": scored_at,
        "total_score": round(avg * 100, 2),
        "max_score": 100.0,
        "dimension_scores_json": json.dumps(dim_list),
    }


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestAggregateScoresStructure:

    def test_empty_month_returns_correct_skeleton(self, scores_repo):
        result = aggregate_scores("2026-04", scores_repo)
        assert result["month"] == "2026-04"
        assert result["by_strategy"] == {}
        assert result["overall"]["decisions_scored"] == 0
        assert result["overall"]["closed_trades_in_window"] == 0
        assert result["overall"]["by_dimension"] == {}

    def test_by_strategy_key_present_for_scored_strategy(self, conn, scores_repo):
        did = _insert_decision(conn, strategy_type="wheel")
        scores_repo.insert(_score_row(did, [("rule_adherence", 0.8)]))

        result = aggregate_scores("2026-04", scores_repo)
        assert "wheel" in result["by_strategy"]

    def test_by_dimension_contains_expected_keys(self, conn, scores_repo):
        did = _insert_decision(conn, strategy_type="wheel")
        scores_repo.insert(_score_row(did, [("rule_adherence", 0.8)]))

        dim = result_dim(aggregate_scores("2026-04", scores_repo), "wheel", "rule_adherence")
        for key in ("mean", "median", "stddev", "n", "by_prompt_version", "_lowest_decision_ids"):
            assert key in dim, f"Missing key: {key}"


class TestAggregateScoresStats:

    def test_single_score_stats(self, conn, scores_repo):
        did = _insert_decision(conn, strategy_type="wheel", prompt_version="v1")
        scores_repo.insert(_score_row(did, [("rule_adherence", 0.8)]))

        dim = result_dim(aggregate_scores("2026-04", scores_repo), "wheel", "rule_adherence")
        assert dim["n"] == 1
        assert dim["mean"] == pytest.approx(0.8, abs=1e-4)
        assert dim["median"] == pytest.approx(0.8, abs=1e-4)
        assert dim["stddev"] == pytest.approx(0.0, abs=1e-4)

    def test_mean_median_stddev_correctness(self, conn, scores_repo):
        raw_scores = [0.6, 0.8, 1.0]
        for i, s in enumerate(raw_scores):
            did = _insert_decision(
                conn, strategy_type="wheel",
                timestamp=f"2026-04-{i + 1:02d}T10:00:00"
            )
            scores_repo.insert(_score_row(did, [("rule_adherence", s)]))

        dim = result_dim(aggregate_scores("2026-04", scores_repo), "wheel", "rule_adherence")
        assert dim["n"] == 3
        assert dim["mean"] == pytest.approx(statistics.mean(raw_scores), abs=1e-4)
        assert dim["median"] == pytest.approx(statistics.median(raw_scores), abs=1e-4)
        assert dim["stddev"] == pytest.approx(statistics.stdev(raw_scores), abs=1e-4)

    def test_stddev_zero_for_single_observation(self, conn, scores_repo):
        did = _insert_decision(conn)
        scores_repo.insert(_score_row(did, [("rule_adherence", 0.7)]))
        dim = result_dim(aggregate_scores("2026-04", scores_repo), "wheel", "rule_adherence")
        assert dim["stddev"] == 0.0

    def test_by_prompt_version_splits_correctly(self, conn, scores_repo):
        # Two v1 decisions and one v2 decision
        for i, (pv, score) in enumerate([("v1", 0.9), ("v1", 0.7), ("v2", 0.5)]):
            did = _insert_decision(
                conn, strategy_type="wheel", prompt_version=pv,
                timestamp=f"2026-04-{i + 1:02d}T10:00:00",
            )
            scores_repo.insert(_score_row(did, [("rule_adherence", score)]))

        agg = aggregate_scores("2026-04", scores_repo)
        by_pv = agg["by_strategy"]["wheel"]["by_dimension"]["rule_adherence"]["by_prompt_version"]

        assert "v1" in by_pv
        assert "v2" in by_pv
        assert by_pv["v1"]["n"] == 2
        assert by_pv["v1"]["mean"] == pytest.approx(0.8, abs=1e-4)
        assert by_pv["v2"]["n"] == 1
        assert by_pv["v2"]["mean"] == pytest.approx(0.5, abs=1e-4)
        assert by_pv["v2"]["stddev"] == 0.0

    def test_multiple_dimensions_in_single_score_row(self, conn, scores_repo):
        did = _insert_decision(conn, strategy_type="wheel")
        scores_repo.insert(_score_row(did, [
            ("rule_adherence", 0.9),
            ("skip_validity_structural", 1.0),
        ]))

        agg = aggregate_scores("2026-04", scores_repo)
        wheel = agg["by_strategy"]["wheel"]
        assert "rule_adherence" in wheel["by_dimension"]
        assert "skip_validity_structural" in wheel["by_dimension"]


class TestAggregateScoresCounts:

    def test_decisions_scored_single(self, conn, scores_repo):
        did = _insert_decision(conn, strategy_type="wheel")
        scores_repo.insert(_score_row(did, [("rule_adherence", 0.8)]))

        agg = aggregate_scores("2026-04", scores_repo)
        assert agg["by_strategy"]["wheel"]["decisions_scored"] == 1

    def test_decisions_scored_deduplicates_multi_scorer_rows(self, conn, scores_repo):
        """Same decision scored by two scorers must count as 1 unique decision."""
        did = _insert_decision(conn, strategy_type="wheel")
        scores_repo.insert(
            _score_row(did, [("rule_adherence", 0.8)], scorer_type="programmatic")
        )
        scores_repo.insert(
            _score_row(did, [("reasoning_groundedness", 0.7)], scorer_type="judge")
        )

        agg = aggregate_scores("2026-04", scores_repo)
        assert agg["by_strategy"]["wheel"]["decisions_scored"] == 1

    def test_decisions_scored_multiple_decisions(self, conn, scores_repo):
        for i in range(5):
            did = _insert_decision(
                conn, strategy_type="wheel",
                timestamp=f"2026-04-{i + 1:02d}T10:00:00",
            )
            scores_repo.insert(_score_row(did, [("rule_adherence", 0.8)]))

        agg = aggregate_scores("2026-04", scores_repo)
        assert agg["by_strategy"]["wheel"]["decisions_scored"] == 5

    def test_overall_decisions_scored_sums_across_strategies(self, conn, scores_repo):
        for strategy in ["wheel", "bull_put_spread"]:
            did = _insert_decision(conn, strategy_type=strategy)
            scores_repo.insert(_score_row(did, [("rule_adherence", 0.8)]))

        agg = aggregate_scores("2026-04", scores_repo)
        assert agg["overall"]["decisions_scored"] == 2

    def test_closed_trades_in_window(self, conn, scores_repo):
        # 3 CLOSE decisions in April for wheel
        for i in range(3):
            _insert_decision(
                conn, strategy_type="wheel",
                timestamp=f"2026-04-{i + 1:02d}T10:00:00",
                action="CLOSE",
            )
        # One scored SELL_PUT so wheel appears in by_strategy
        did = _insert_decision(conn, strategy_type="wheel", action="SELL_PUT")
        scores_repo.insert(_score_row(did, [("rule_adherence", 0.8)]))

        agg = aggregate_scores("2026-04", scores_repo)
        assert agg["by_strategy"]["wheel"]["closed_trades_in_window"] == 3

    def test_closed_trades_excludes_other_months(self, conn, scores_repo):
        # CLOSE in May — should NOT appear in April aggregate
        _insert_decision(
            conn, strategy_type="wheel",
            timestamp="2026-05-01T10:00:00",
            action="CLOSE",
        )
        did = _insert_decision(conn, strategy_type="wheel", action="SELL_PUT")
        scores_repo.insert(_score_row(did, [("rule_adherence", 0.8)]))

        agg = aggregate_scores("2026-04", scores_repo)
        assert agg["by_strategy"]["wheel"]["closed_trades_in_window"] == 0


class TestAggregateScoresFiltering:

    def test_month_filter_isolates_score_rows(self, conn, scores_repo):
        # April decision with April score
        did_apr = _insert_decision(
            conn, strategy_type="wheel", timestamp="2026-04-10T10:00:00"
        )
        scores_repo.insert(
            _score_row(did_apr, [("rule_adherence", 0.9)], scored_at="2026-04-15T10:00:00")
        )
        # May decision with May score
        did_may = _insert_decision(
            conn, strategy_type="wheel", timestamp="2026-05-10T10:00:00"
        )
        scores_repo.insert(
            _score_row(did_may, [("rule_adherence", 0.1)], scored_at="2026-05-15T10:00:00")
        )

        agg = aggregate_scores("2026-04", scores_repo)
        wheel = agg["by_strategy"]["wheel"]
        assert wheel["decisions_scored"] == 1
        assert result_dim(agg, "wheel", "rule_adherence")["mean"] == pytest.approx(0.9, abs=1e-4)

    def test_multiple_strategies_separated(self, conn, scores_repo):
        for strategy in ["wheel", "iron_condor", "bull_put_spread"]:
            did = _insert_decision(conn, strategy_type=strategy)
            scores_repo.insert(_score_row(did, [("rule_adherence", 0.8)]))

        agg = aggregate_scores("2026-04", scores_repo)
        for strategy in ["wheel", "iron_condor", "bull_put_spread"]:
            assert strategy in agg["by_strategy"]
        # Each strategy should have exactly 1 decision
        for strategy in ["wheel", "iron_condor", "bull_put_spread"]:
            assert agg["by_strategy"][strategy]["decisions_scored"] == 1


class TestAggregateScoresOverall:

    def test_overall_aggregates_across_strategies(self, conn, scores_repo):
        for strategy, score in [("wheel", 0.9), ("bull_put_spread", 0.7)]:
            did = _insert_decision(conn, strategy_type=strategy)
            scores_repo.insert(_score_row(did, [("rule_adherence", score)]))

        agg = aggregate_scores("2026-04", scores_repo)
        overall = agg["overall"]
        assert overall["decisions_scored"] == 2
        dim = overall["by_dimension"]["rule_adherence"]
        assert dim["n"] == 2
        assert dim["mean"] == pytest.approx(0.8, abs=1e-4)

    def test_overall_closed_trades_sums_all_strategies(self, conn, scores_repo):
        for strategy, n_closes in [("wheel", 2), ("iron_condor", 1)]:
            for i in range(n_closes):
                _insert_decision(
                    conn, strategy_type=strategy,
                    timestamp=f"2026-04-{i + 1:02d}T10:00:00",
                    action="CLOSE",
                )
            did = _insert_decision(conn, strategy_type=strategy)
            scores_repo.insert(_score_row(did, [("rule_adherence", 0.8)]))

        agg = aggregate_scores("2026-04", scores_repo)
        assert agg["overall"]["closed_trades_in_window"] == 3


class TestLowestDecisionIds:

    def test_lowest_decision_ids_sorted_ascending_by_score(self, conn, scores_repo):
        scored_pairs = [(0.4, None), (0.6, None), (0.8, None), (1.0, None), (0.2, None)]
        dids = []
        for i, (score, _) in enumerate(scored_pairs):
            did = _insert_decision(
                conn, strategy_type="wheel",
                timestamp=f"2026-04-{i + 1:02d}T10:00:00",
            )
            scores_repo.insert(_score_row(did, [("rule_adherence", score)]))
            scored_pairs[i] = (score, did)
            dids.append((score, did))

        agg = aggregate_scores("2026-04", scores_repo)
        lowest = result_dim(agg, "wheel", "rule_adherence")["_lowest_decision_ids"]

        assert len(lowest) <= 5
        # Lowest score is 0.2 — that decision should appear first
        worst_did = min(dids, key=lambda x: x[0])[1]
        assert lowest[0] == worst_did

    def test_lowest_decision_ids_capped_at_five(self, conn, scores_repo):
        for i in range(8):
            did = _insert_decision(
                conn, strategy_type="wheel",
                timestamp=f"2026-04-{i + 1:02d}T10:00:00",
            )
            scores_repo.insert(_score_row(did, [("rule_adherence", float(i) / 10)]))

        agg = aggregate_scores("2026-04", scores_repo)
        lowest = result_dim(agg, "wheel", "rule_adherence")["_lowest_decision_ids"]
        assert len(lowest) == 5


# ── Shared helpers ────────────────────────────────────────────────────────────

def result_dim(agg: dict, strategy: str, dimension: str) -> dict:
    """Convenience: extract a dimension stats dict from an aggregate."""
    return agg["by_strategy"][strategy]["by_dimension"][dimension]
