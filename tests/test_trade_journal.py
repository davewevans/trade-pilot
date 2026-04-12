"""Tests for data.trade_journal.TradeJournal."""

from datetime import date, timedelta

import pytest

from data.trade_journal import TradeJournal


@pytest.fixture
def journal(tmp_path):
    return TradeJournal(path=tmp_path / "journal.jsonl")


def _today_iso():
    return date.today().isoformat() + "T10:00:00"


def _old_iso():
    return (date.today() - timedelta(days=120)).isoformat() + "T10:00:00"


def test_get_symbol_stats_empty(journal):
    stats = journal.get_symbol_stats("SPY")
    assert stats["total_decisions"] == 0
    assert stats["win_rate"] is None
    assert stats["total_pnl"] == 0.0
    assert stats["note"] == "No history in lookback window"


def test_get_symbol_stats_counts_and_win_rate(journal):
    # 2 winning closed trades, 1 losing closed trade, 1 open trade,
    # 1 skip, 1 hold, 1 stale entry outside lookback window.
    entries = [
        # wins
        {"underlying": "SPY", "action": "sell_put", "status": "filled",
         "closed_at": _today_iso(), "pnl": 100, "iv_rank": 40, "delta": -0.30},
        {"underlying": "SPY", "action": "sell_call", "status": "filled",
         "closed_at": _today_iso(), "pnl": 50, "iv_rank": 50, "delta": 0.20},
        # loss
        {"underlying": "SPY", "action": "sell_put", "status": "filled",
         "closed_at": _today_iso(), "pnl": -30, "iv_rank": 60, "delta": -0.25},
        # open trade (no pnl yet)
        {"underlying": "SPY", "action": "sell_put", "status": "submitted",
         "iv_rank": 30, "delta": -0.20},
        # skip + hold
        {"underlying": "SPY", "action": "skip", "status": "skipped",
         "skip_reason": "guardrail"},
        {"underlying": "SPY", "action": "hold", "status": "hold"},
        # other symbol — should not count
        {"underlying": "AAPL", "action": "sell_put", "status": "filled",
         "closed_at": _today_iso(), "pnl": 999},
    ]
    for e in entries:
        journal.append(e)

    # Manually inject one stale entry with old timestamp
    import json
    with open(journal.path, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "timestamp": _old_iso(), "underlying": "SPY",
            "action": "sell_put", "status": "filled",
            "closed_at": _old_iso(), "pnl": 9999,
        }) + "\n")

    stats = journal.get_symbol_stats("SPY", days=30)

    assert stats["symbol"] == "SPY"
    assert stats["total_decisions"] == 6  # excludes AAPL + stale
    assert stats["trades"] == 4  # 3 closed + 1 open
    assert stats["skips"] == 2  # skip + hold
    assert stats["wins"] == 2
    assert stats["losses"] == 1
    assert stats["win_rate"] == round(2 / 3 * 100, 1)
    assert stats["total_pnl"] == 120.0
    assert stats["avg_iv_rank_at_entry"] == round((40 + 50 + 60 + 30) / 4, 1)
    assert stats["avg_delta_at_entry"] == round((-0.30 + 0.20 - 0.25 - 0.20) / 4, 3)


def test_format_stats_for_prompt_empty(journal):
    assert journal.format_stats_for_prompt("SPY") == ""


def test_format_stats_for_prompt_renders(journal):
    journal.append({
        "underlying": "SPY", "action": "sell_put", "status": "filled",
        "closed_at": _today_iso(), "pnl": 100, "iv_rank": 40, "delta": -0.30,
    })
    out = journal.format_stats_for_prompt("SPY", days=30)
    assert "<performance_stats>" in out
    assert "Performance on SPY (last 30 days)" in out
    assert "Win rate:" in out
    assert "+$100" in out
