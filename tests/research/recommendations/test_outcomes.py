"""Tests for R2 OutcomeComputer."""

from __future__ import annotations

import pytest
from datetime import date, timedelta
from unittest.mock import MagicMock

from research.recommendations.outcomes import OutcomeComputer, WINDOW_DAYS


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_rec(rec_id, action, op_decision, days_ago=91, watchlist="wheel", symbol="AAPL"):
    generated_at = (date.today() - timedelta(days=days_ago)).isoformat()
    decided_at = generated_at
    return {
        "recommendation_id": rec_id,
        "action": action,
        "operator_decision": op_decision,
        "generated_at": generated_at,
        "operator_decided_at": decided_at,
        "watchlist_name": watchlist,
        "symbol": symbol,
    }


def _make_outcome_repo(existing_ids=None):
    existing_ids = set(existing_ids or [])
    repo = MagicMock()
    repo.exists.side_effect = lambda rec_id, window: rec_id in existing_ids
    return repo


def _make_trade_repo(pnls=None):
    """Trade repo returning fake closed trades."""
    pnls = pnls or []
    repo = MagicMock()

    def _get_closed(symbol, strategy_type, start, end):
        result = []
        for i, pnl in enumerate(pnls):
            fp = abs(pnl) / 100.0
            trade_type = "SELL_PUT" if pnl > 0 else "BUY_PUT"
            result.append({
                "fill_price": fp,
                "contracts": 1,
                "trade_type": trade_type,
                "fill_status": "filled",
                "closed_at": "2026-04-01",
            })
        return result

    repo.get_closed_trades_in_window.side_effect = _get_closed
    return repo


def _make_computer(recs, trade_pnls=None, existing_outcome_ids=None, use_mock_engine=True):
    rec_repo = MagicMock()
    rec_repo.get_history.return_value = recs

    trade_repo = _make_trade_repo(trade_pnls)
    outcome_repo = _make_outcome_repo(existing_outcome_ids)

    if use_mock_engine:
        mock_result = MagicMock()
        mock_trade = MagicMock()
        mock_trade.pnl = 50.0
        mock_result.trades = [mock_trade, mock_trade]  # 2 wins
        engine = MagicMock()
        engine.run.return_value = mock_result
    else:
        engine = None

    return OutcomeComputer(
        trade_repo=trade_repo,
        backtest_engine=engine,
        outcome_repo=outcome_repo,
        rec_repo=rec_repo,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_accepted_add_uses_ground_truth_live():
    """Accepted add → ground_truth_live outcome type."""
    recs = [_make_rec(1, "add", "accepted")]
    computer = _make_computer(recs, trade_pnls=[100, 100, -50])
    result = computer.compute_pending_outcomes()
    assert result["computed"] == 1
    assert result["errors"] == 0

    inserted = computer._outcome_repo.insert.call_args[0][0]
    assert inserted["outcome_type"] == "ground_truth_live"
    assert inserted["recommendation_id"] == 1
    assert inserted["window_days"] == 90


def test_rejected_add_uses_proxy_backtest():
    """Rejected add → proxy_backtest outcome type."""
    recs = [_make_rec(1, "add", "rejected")]
    computer = _make_computer(recs)
    result = computer.compute_pending_outcomes()
    assert result["computed"] == 1

    inserted = computer._outcome_repo.insert.call_args[0][0]
    assert inserted["outcome_type"] == "proxy_backtest"
    assert inserted["proxy_params_json"] is not None


def test_accepted_remove_uses_proxy_backtest():
    """Accepted remove → proxy_backtest."""
    recs = [_make_rec(1, "remove", "accepted")]
    computer = _make_computer(recs)
    result = computer.compute_pending_outcomes()
    assert result["computed"] == 1

    inserted = computer._outcome_repo.insert.call_args[0][0]
    assert inserted["outcome_type"] == "proxy_backtest"


def test_rejected_remove_uses_ground_truth_live():
    """Rejected remove → ground_truth_live."""
    recs = [_make_rec(1, "remove", "rejected")]
    computer = _make_computer(recs, trade_pnls=[100, 100])
    result = computer.compute_pending_outcomes()
    assert result["computed"] == 1

    inserted = computer._outcome_repo.insert.call_args[0][0]
    assert inserted["outcome_type"] == "ground_truth_live"


def test_proxy_params_include_required_fields():
    """Proxy params must include delta, dte_min, dte_max, ivr_threshold, strategy_params_version."""
    recs = [_make_rec(1, "add", "rejected")]
    computer = _make_computer(recs)
    computer.compute_pending_outcomes()

    inserted = computer._outcome_repo.insert.call_args[0][0]
    pp = inserted["proxy_params_json"]
    assert pp is not None
    assert "delta" in pp
    assert "dte_min" in pp
    assert "dte_max" in pp
    assert "ivr_threshold" in pp
    assert "strategy_params_version" in pp


def test_already_computed_outcomes_not_recomputed():
    """Existing outcomes are skipped (idempotent)."""
    recs = [_make_rec(1, "add", "accepted")]
    computer = _make_computer(recs, existing_outcome_ids=[1])
    result = computer.compute_pending_outcomes()
    assert result["computed"] == 0
    assert result["skipped"] >= 1
    computer._outcome_repo.insert.assert_not_called()


def test_recent_recommendations_skipped():
    """Recommendations younger than WINDOW_DAYS are skipped."""
    recs = [_make_rec(1, "add", "accepted", days_ago=30)]
    computer = _make_computer(recs)
    result = computer.compute_pending_outcomes()
    assert result["computed"] == 0
    assert result["skipped"] >= 1


def test_pending_recommendations_skipped():
    """Recommendations with operator_decision=None are skipped."""
    recs = [_make_rec(1, "add", None)]
    computer = _make_computer(recs)
    result = computer.compute_pending_outcomes()
    assert result["computed"] == 0


def test_expired_recommendations_skipped():
    """Recommendations with operator_decision='expired' are skipped."""
    recs = [_make_rec(1, "add", "expired")]
    computer = _make_computer(recs)
    result = computer.compute_pending_outcomes()
    assert result["computed"] == 0
