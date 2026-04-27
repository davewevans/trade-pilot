"""J6: Tests for WatchlistRecommender."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from research.recommendations.recommender import WatchlistRecommender


# ── Fixtures & helpers ────────────────────────────────────────────────────────

class FakeSettings:
    RESEARCH_MAX_RECOMMENDATIONS_PER_LIST = 5
    RESEARCH_REMOVE_MIN_WEEKS_OBSERVED = 12
    RESEARCH_RECOMMENDATIONS_ENABLED = True
    DATA_DIR = Path(".")       # overridden per-test as needed
    SNAPSHOTS_DIR = Path(".")  # overridden per-test as needed


def _make_universe(symbols=None):
    u = MagicMock()
    u.all_symbols.return_value = list(symbols or ["AAPL", "MSFT", "GOOGL", "SPY", "QQQ"])
    return u


def _make_liq_repo(score=80.0, tier="A", confidence="high"):
    repo = MagicMock()
    repo.get_score.return_value = {
        "composite_score": score,
        "tier": tier,
        "confidence": confidence,
    }
    repo.get_snapshots.return_value = []
    return repo


def _make_bt_repo(multiplier=1.15, wr_tier="good", wr_conf="high"):
    repo = MagicMock()
    repo.get_winrate_multiplier.return_value = (multiplier, wr_tier, wr_conf)
    return repo


def _make_recommender(
    universe_symbols=None,
    candidate_symbols=None,
    liq_score=80.0,
    liq_tier="A",
    liq_conf="high",
    wr_mult=1.15,
    wr_tier="good",
    wr_conf="high",
    settings_obj=None,
    snapshots_count=0,
):
    universe = _make_universe(universe_symbols or ["AAPL", "MSFT", "GOOGL", "SPY", "QQQ"])
    candidate_u = _make_universe(candidate_symbols or universe.all_symbols())
    liq_repo = _make_liq_repo(liq_score, liq_tier, liq_conf)
    bt_repo = _make_bt_repo(wr_mult, wr_tier, wr_conf)

    # Simulate weeks observed
    fake_snapshots = [{"snapshot_date": f"2026-0{(i%9)+1}-01"} for i in range(snapshots_count)]
    liq_repo.get_snapshots.return_value = fake_snapshots

    return WatchlistRecommender(
        liquidity_repo=liq_repo,
        backtest_stats_repo=bt_repo,
        universe=universe,
        candidate_universe=candidate_u,
        settings_obj=settings_obj or FakeSettings(),
    )


# ── _score_candidate ──────────────────────────────────────────────────────────

class TestScoreCandidate:

    def test_high_quality_data_high_score(self):
        r = _make_recommender(liq_score=90.0, liq_conf="high", wr_tier="strong", wr_conf="high")
        score, sub, conf = r._score_candidate("AAPL", "wheel_csp", set())
        # 90*0.6 + 30 (strong) + 10 (novelty) = 54 + 30 + 10 = 94
        assert score == pytest.approx(94.0)
        assert conf == "high"

    def test_no_liquidity_data_returns_none_confidence(self):
        r = _make_recommender()
        r._liq_repo.get_score.return_value = None
        _, sub, conf = r._score_candidate("AAPL", "wheel_csp", set())
        assert conf == "none"
        assert sub["liq_tier"] == "D"

    def test_no_winrate_data_returns_none_confidence(self):
        r = _make_recommender(wr_conf="none")
        _, sub, conf = r._score_candidate("AAPL", "wheel_csp", set())
        assert conf == "none"

    def test_low_liq_conf_returns_low_confidence(self):
        r = _make_recommender(liq_conf="low", wr_conf="high")
        _, _, conf = r._score_candidate("AAPL", "wheel_csp", set())
        assert conf == "low"

    def test_already_in_watchlist_no_novelty_bonus(self):
        r = _make_recommender(liq_score=80.0, wr_tier="neutral", wr_conf="high")
        _, sub, _ = r._score_candidate("AAPL", "wheel_csp", {"AAPL"})
        assert sub["novelty_bonus"] == 0.0

    def test_not_in_watchlist_gets_novelty_bonus(self):
        r = _make_recommender(liq_score=80.0, wr_tier="neutral", wr_conf="high")
        _, sub, _ = r._score_candidate("AAPL", "wheel_csp", set())
        assert sub["novelty_bonus"] == 10.0

    def test_liq_score_weighted_correctly(self):
        r = _make_recommender(liq_score=100.0, wr_tier="neutral", wr_conf="high")
        score, _, _ = r._score_candidate("AAPL", "wheel_csp", {"AAPL"})
        # 100*0.6 + 10 (neutral) + 0 (no novelty) = 70
        assert score == pytest.approx(70.0)

    def test_exception_in_liq_returns_gracefully(self):
        r = _make_recommender()
        r._liq_repo.get_score.side_effect = RuntimeError("db error")
        score, sub, conf = r._score_candidate("AAPL", "wheel_csp", set())
        assert conf == "none"
        assert sub["liq_tier"] == "D"


# ── generate_for_watchlist ────────────────────────────────────────────────────

class TestGenerateForWatchlist:

    def test_output_shape(self):
        r = _make_recommender()
        result = r.generate_for_watchlist("wheel", ["AAPL"], ["wheel_csp"])
        assert result["watchlist_name"] == "wheel"
        assert "add" in result
        assert "remove" in result
        assert "no_change" in result
        assert "considered_but_rejected" in result
        assert isinstance(result["add"], list)
        assert isinstance(result["remove"], list)
        assert isinstance(result["no_change"], list)

    def test_add_list_respects_max_cap(self):
        settings = FakeSettings()
        settings.RESEARCH_MAX_RECOMMENDATIONS_PER_LIST = 2
        r = _make_recommender(
            candidate_symbols=["GOOGL", "SPY", "QQQ", "META", "NVDA"],
            settings_obj=settings,
        )
        result = r.generate_for_watchlist("wheel", [], ["wheel_csp"])
        assert len(result["add"]) <= 2

    def test_add_requires_high_liquidity_confidence(self):
        # Low-confidence liquidity → no adds
        r = _make_recommender(liq_conf="low", wr_conf="high")
        result = r.generate_for_watchlist("wheel", [], ["wheel_csp"])
        assert len(result["add"]) == 0

    def test_add_requires_nonzero_winrate_confidence(self):
        # No winrate data → no adds
        r = _make_recommender(liq_conf="high", wr_conf="none")
        result = r.generate_for_watchlist("wheel", [], ["wheel_csp"])
        assert len(result["add"]) == 0

    def test_add_requires_tier_a_or_b(self):
        # Tier C → not added
        r = _make_recommender(liq_tier="C", liq_conf="high", wr_conf="high")
        result = r.generate_for_watchlist("wheel", [], ["wheel_csp"])
        assert len(result["add"]) == 0

    def test_add_requires_wr_multiplier_gte_1(self):
        # Poor win rate (wr_mult=0.7) → not added
        r = _make_recommender(liq_tier="A", liq_conf="high", wr_mult=0.7, wr_tier="poor", wr_conf="high")
        result = r.generate_for_watchlist("wheel", [], ["wheel_csp"])
        assert len(result["add"]) == 0

    def test_remove_requires_min_weeks_observed(self):
        # Only 5 weeks observed → not enough to remove
        settings = FakeSettings()
        settings.RESEARCH_REMOVE_MIN_WEEKS_OBSERVED = 12
        # Low score but insufficient weeks
        r = _make_recommender(
            liq_score=10.0, liq_conf="high", wr_conf="high",
            snapshots_count=5,
            settings_obj=settings,
        )
        result = r.generate_for_watchlist("wheel", ["AAPL"], ["wheel_csp"])
        assert len(result["remove"]) == 0
        assert any(m["symbol"] == "AAPL" for m in result["no_change"])

    def test_remove_triggers_with_enough_weeks_and_low_score(self):
        settings = FakeSettings()
        settings.RESEARCH_REMOVE_MIN_WEEKS_OBSERVED = 3
        # Low score + 5 weeks → triggers removal
        r = _make_recommender(
            liq_score=5.0, liq_tier="D", liq_conf="high",
            wr_mult=0.7, wr_tier="poor", wr_conf="high",
            snapshots_count=5,
            settings_obj=settings,
        )
        result = r.generate_for_watchlist("wheel", ["AAPL"], ["wheel_csp"])
        assert any(m["symbol"] == "AAPL" for m in result["remove"])

    def test_good_members_go_to_no_change(self):
        r = _make_recommender(liq_score=85.0, liq_conf="high", wr_conf="high", snapshots_count=4)
        result = r.generate_for_watchlist("wheel", ["AAPL"], ["wheel_csp"])
        assert any(m["symbol"] == "AAPL" for m in result["no_change"])

    def test_add_items_have_required_fields(self):
        r = _make_recommender(
            candidate_symbols=["MSFT"],
            liq_conf="high", wr_conf="high", wr_tier="neutral",
        )
        result = r.generate_for_watchlist("wheel", [], ["wheel_csp"])
        for item in result["add"]:
            assert "symbol" in item
            assert "score" in item
            assert "reasoning" in item
            assert "data_confidence" in item
            assert "sub_scores" in item

    def test_current_members_not_in_add_list(self):
        r = _make_recommender(
            universe_symbols=["AAPL", "MSFT"],
            candidate_symbols=["AAPL", "MSFT"],
        )
        result = r.generate_for_watchlist("wheel", ["AAPL"], ["wheel_csp"])
        add_symbols = [x["symbol"] for x in result["add"]]
        assert "AAPL" not in add_symbols


# ── generate_all ─────────────────────────────────────────────────────────────

class TestGenerateAll:

    def test_output_has_all_five_watchlists(self, tmp_path):
        watchlist = {
            "wheel": ["AAPL"],
            "iron_condor": ["SPY"],
            "iron_butterfly": ["QQQ"],
            "spreads": ["MSFT"],
            "calendar_spread": ["AMZN"],
        }
        (tmp_path / "watchlist.json").write_text(json.dumps(watchlist), encoding="utf-8")

        settings = FakeSettings()
        settings.DATA_DIR = tmp_path
        settings.SNAPSHOTS_DIR = tmp_path / "snapshots"
        (tmp_path / "snapshots").mkdir()

        r = _make_recommender(settings_obj=settings)
        result = r.generate_all()

        assert "wheel" in result
        assert "iron_condor" in result
        assert "iron_butterfly" in result
        assert "spreads" in result
        assert "calendar_spread" in result
        assert "generated_at" in result

    def test_missing_watchlist_file_uses_empty_members(self, tmp_path):
        settings = FakeSettings()
        settings.DATA_DIR = tmp_path  # no watchlist.json here
        settings.SNAPSHOTS_DIR = tmp_path
        r = _make_recommender(settings_obj=settings)
        result = r.generate_all()

        for wl in ("wheel", "iron_condor", "iron_butterfly", "spreads", "calendar_spread"):
            assert wl in result


# ── persist ───────────────────────────────────────────────────────────────────

class TestPersist:

    def test_writes_to_db_and_json(self, tmp_path):
        from database.db import Database
        from database.repositories import RecommendationRepository

        db = Database(path=str(tmp_path / "test.db"))
        db.init_schema()
        repo = RecommendationRepository(db.get_connection())

        r = _make_recommender()
        results = {
            "generated_at": "2026-04-20T10:00:00",
            "wheel": {
                "watchlist_name": "wheel",
                "add": [
                    {
                        "symbol": "MSFT",
                        "score": 80.0,
                        "reasoning": "Tier A liquidity",
                        "data_confidence": "high",
                        "sub_scores": {"liq_score": 90.0},
                    }
                ],
                "remove": [],
                "no_change": [
                    {
                        "symbol": "AAPL",
                        "score": 75.0,
                        "reasoning": "Tier A, incumbent",
                        "data_confidence": "high",
                        "sub_scores": {},
                    }
                ],
                "considered_but_rejected": [],
            },
            "iron_condor": {
                "watchlist_name": "iron_condor",
                "add": [], "remove": [], "no_change": [], "considered_but_rejected": [],
            },
            "spreads": {
                "watchlist_name": "spreads",
                "add": [], "remove": [], "no_change": [], "considered_but_rejected": [],
            },
        }

        snapshots_dir = tmp_path / "snapshots"
        snapshots_dir.mkdir()

        settings = FakeSettings()
        settings.DATA_DIR = tmp_path
        settings.SNAPSHOTS_DIR = snapshots_dir
        r = _make_recommender(settings_obj=settings)
        r.persist(results, repo)

        # Check DB
        rows = repo.get_latest_batch()
        assert len(rows) >= 2  # add + no_change
        symbols = {row["symbol"] for row in rows}
        assert "MSFT" in symbols
        assert "AAPL" in symbols

        # Check JSON file
        json_path = snapshots_dir / "watchlist_recommendations.json"
        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert "wheel" in data

        db.close()


# ── R1 — Live performance penalty tests ──────────────────────────────────────

class FakeTradeRepo:
    """Test double for TradeRepository."""
    def __init__(self, trades: list[dict]):
        self._trades = trades

    def get_closed_trades_for_symbol(self, symbol, strategy_type, since_date):
        return [
            t for t in self._trades
            if t.get("underlying", "").upper() == symbol.upper()
            and t.get("strategy_type") == strategy_type
        ]


def _make_trade(pnl_sign: float) -> dict:
    """Make a fake trade dict for P&L testing."""
    fill_price = abs(pnl_sign) / 100.0  # contracts=1, sign via trade_type
    trade_type = "SELL_PUT" if pnl_sign > 0 else "BUY_PUT"
    return {
        "underlying": "AAPL",
        "strategy_type": "wheel_csp",
        "fill_price": fill_price,
        "contracts": 1,
        "trade_type": trade_type,
        "fill_status": "filled",
        "closed_at": "2026-04-01",
    }


def _make_recommender_with_trades(trade_repo=None):
    liq_repo = _make_liq_repo()
    bt_repo = _make_bt_repo()
    universe = _make_universe(["AAPL", "MSFT"])
    settings = FakeSettings()
    return WatchlistRecommender(
        liquidity_repo=liq_repo,
        backtest_stats_repo=bt_repo,
        universe=universe,
        candidate_universe=universe,
        settings_obj=settings,
        trade_repo=trade_repo,
    )


def test_live_penalty_no_trade_repo():
    """No trade_repo → penalty = 0."""
    r = _make_recommender_with_trades(trade_repo=None)
    penalty, sub = r._get_live_performance_penalty("AAPL", "wheel_csp")
    assert penalty == 0.0
    assert sub["live_trade_count"] == 0


def test_live_penalty_zero_trades():
    """0 live trades → penalty = 0 (below sample threshold)."""
    trade_repo = FakeTradeRepo([])
    r = _make_recommender_with_trades(trade_repo=trade_repo)
    penalty, sub = r._get_live_performance_penalty("AAPL", "wheel_csp")
    assert penalty == 0.0
    assert sub["live_trade_count"] == 0


def test_live_penalty_below_minimum_sample():
    """9 live trades → penalty = 0 (below 10-trade minimum)."""
    trades = [_make_trade(+50.0) for _ in range(9)]
    r = _make_recommender_with_trades(trade_repo=FakeTradeRepo(trades))
    penalty, sub = r._get_live_performance_penalty("AAPL", "wheel_csp")
    assert penalty == 0.0
    assert sub["live_trade_count"] == 9


def test_live_penalty_win_rate_25pct():
    """15 trades, win_rate = 0.20 → penalty = -25."""
    wins_3 = [_make_trade(+50.0) for _ in range(3)]
    losses_12 = [_make_trade(-50.0) for _ in range(12)]
    trades = wins_3 + losses_12  # 3/15 = 0.2
    r = _make_recommender_with_trades(trade_repo=FakeTradeRepo(trades))
    penalty, sub = r._get_live_performance_penalty("AAPL", "wheel_csp")
    assert penalty == pytest.approx(-25.0)
    assert sub["live_penalty_applied"] == pytest.approx(-25.0)


def test_live_penalty_win_rate_55pct_negative_avg_pnl():
    """15 trades, win_rate = 0.55, avg_pnl < 0 → penalty = -10."""
    # 8 wins of +10, 7 losses of -100 → 8/15 = 0.533, avg = (80 - 700)/15 = -41.3
    wins_8 = [{"underlying": "AAPL", "strategy_type": "wheel_csp", "fill_price": 0.10,
               "contracts": 1, "trade_type": "SELL_PUT", "fill_status": "filled",
               "closed_at": "2026-04-01"} for _ in range(8)]
    losses_7 = [{"underlying": "AAPL", "strategy_type": "wheel_csp", "fill_price": 1.00,
                "contracts": 1, "trade_type": "BUY_PUT", "fill_status": "filled",
                "closed_at": "2026-04-01"} for _ in range(7)]
    r = _make_recommender_with_trades(trade_repo=FakeTradeRepo(wins_8 + losses_7))
    penalty, sub = r._get_live_performance_penalty("AAPL", "wheel_csp")
    assert penalty == pytest.approx(-10.0)


def test_live_penalty_win_rate_70pct_positive_pnl():
    """15 trades, win_rate = 0.70, avg_pnl > 0 → penalty = 0."""
    wins_10 = [{"underlying": "AAPL", "strategy_type": "wheel_csp", "fill_price": 1.00,
                "contracts": 1, "trade_type": "SELL_PUT", "fill_status": "filled",
                "closed_at": "2026-04-01"} for _ in range(10)]
    losses_5 = [{"underlying": "AAPL", "strategy_type": "wheel_csp", "fill_price": 0.50,
                 "contracts": 1, "trade_type": "BUY_PUT", "fill_status": "filled",
                 "closed_at": "2026-04-01"} for _ in range(5)]
    r = _make_recommender_with_trades(trade_repo=FakeTradeRepo(wins_10 + losses_5))
    penalty, sub = r._get_live_performance_penalty("AAPL", "wheel_csp")
    assert penalty == pytest.approx(0.0)


def test_non_incumbent_does_not_get_penalty():
    """Non-incumbent candidate → _score_candidate called with is_incumbent=False → no penalty."""
    trade_repo = FakeTradeRepo([_make_trade(-50.0) for _ in range(20)])  # lots of bad trades
    r = _make_recommender_with_trades(trade_repo=trade_repo)
    # Score as non-incumbent (default)
    composite, sub, _ = r._score_candidate("AAPL", "wheel_csp", set(), is_incumbent=False)
    # No live penalty fields in sub_scores for non-incumbent
    assert sub.get("live_penalty_applied", 0.0) == 0.0
