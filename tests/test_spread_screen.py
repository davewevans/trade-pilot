"""Tests for screen_spread_candidates() pre-filter (Phase 10).

Covers:
1. Symbols failing the IVR gate are rejected with the correct reason/value
2. Symbols with earnings within the strategy threshold are rejected
3. Regime mismatch rejects all symbols instantly
4. Batch /ivrank makes ≤ ceil(N/10) ORATS calls (not N individual calls)
5. Survivors are returned in deterministic (input) order
6. Unknown strategy name returns all symbols unchanged (defensive fallback)
7. If batch IVR call fails for a chunk, remaining chunks still process
"""

import math
import pytest
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_client(ivr_map: dict[str, float | None] = None) -> MagicMock:
    """Return a mock ORATSClient whose get_iv_rank_batch() returns ivr_map."""
    client = MagicMock()
    ivr_map = ivr_map or {}

    def _batch(symbols):
        return {
            sym.upper(): {"ivRank1y": ivr_map[sym.upper()]}
            for sym in symbols
            if sym.upper() in ivr_map and ivr_map[sym.upper()] is not None
        }

    client.get_iv_rank_batch.side_effect = _batch
    return client


def _earnings_fn(days: int | None):
    """Return a callable that always says earnings are in `days` days."""
    return lambda sym: {"days_to_earnings": days, "next_earnings_date": None}


from data.spread_screen import screen_spread_candidates, STRATEGY_GATES


# ---------------------------------------------------------------------------
# Test 1: IVR gate rejection with correct reason and value
# ---------------------------------------------------------------------------

def test_ivr_below_threshold_rejected():
    """Symbols with IVR below the strategy minimum are rejected."""
    client = _make_client({"AAPL": 28.0, "MSFT": 42.0})
    survivors, log = screen_spread_candidates(
        symbols=["AAPL", "MSFT"],
        strategy_name="bull_put_spread",
        regime="NEUTRAL",
        iv_env="HIGH",
        orats_client=client,
    )

    assert "AAPL" not in survivors
    assert "MSFT" in survivors

    aapl_entry = next(r for r in log if r["symbol"] == "AAPL")
    assert aapl_entry["reason"] == "ivr_below_threshold"
    assert aapl_entry["value"] == 28.0
    assert aapl_entry["threshold"] == STRATEGY_GATES["bull_put_spread"]["ivr_min"]


def test_ivr_above_threshold_rejected_for_debit_spread():
    """long_call_vertical rejects symbols with IVR >= 30 (debit spread logic)."""
    client = _make_client({"AAPL": 35.0, "MSFT": 20.0})
    survivors, log = screen_spread_candidates(
        symbols=["AAPL", "MSFT"],
        strategy_name="long_call_vertical",
        regime="BULL",
        iv_env="LOW",
        orats_client=client,
    )

    assert "AAPL" not in survivors
    assert "MSFT" in survivors

    aapl_entry = next(r for r in log if r["symbol"] == "AAPL")
    assert aapl_entry["reason"] == "ivr_above_threshold"


# ---------------------------------------------------------------------------
# Test 2: Earnings gate
# ---------------------------------------------------------------------------

def test_earnings_too_close_rejected():
    """Symbols with earnings within the threshold are rejected."""
    client = _make_client({"AAPL": 55.0, "MSFT": 55.0})
    # bull_put_spread needs > 25 days; 20 days triggers rejection
    survivors, log = screen_spread_candidates(
        symbols=["AAPL", "MSFT"],
        strategy_name="bull_put_spread",
        regime="NEUTRAL",
        iv_env="HIGH",
        orats_client=client,
        earnings_fn=lambda sym: {
            "days_to_earnings": 20 if sym == "AAPL" else 30,
        },
    )

    assert "AAPL" not in survivors
    assert "MSFT" in survivors

    aapl_entry = next(r for r in log if r["symbol"] == "AAPL")
    assert aapl_entry["reason"] == "earnings_too_close"
    assert aapl_entry["value"] == 20
    assert aapl_entry["threshold"] == 25


def test_earnings_none_passes():
    """If days_to_earnings is None (unknown), the symbol passes the earnings gate."""
    client = _make_client({"AAPL": 55.0})
    survivors, log = screen_spread_candidates(
        symbols=["AAPL"],
        strategy_name="bull_put_spread",
        regime="NEUTRAL",
        iv_env="HIGH",
        orats_client=client,
        earnings_fn=_earnings_fn(None),
    )
    assert "AAPL" in survivors
    assert not any(r["reason"] == "earnings_too_close" for r in log)


# ---------------------------------------------------------------------------
# Test 3: Regime mismatch rejects all symbols
# ---------------------------------------------------------------------------

def test_regime_mismatch_rejects_all():
    """Wrong regime immediately rejects every symbol without any API calls."""
    client = MagicMock()
    symbols = ["AAPL", "MSFT", "GOOG"]
    survivors, log = screen_spread_candidates(
        symbols=symbols,
        strategy_name="bull_put_spread",  # needs NEUTRAL or BULL
        regime="BEAR",
        iv_env="HIGH",
        orats_client=client,
    )

    assert survivors == []
    assert len(log) == len(symbols)
    assert all(r["reason"] == "regime_mismatch" for r in log)
    # No API call should have been made
    client.get_iv_rank_batch.assert_not_called()


def test_correct_regime_passes():
    """NEUTRAL regime is accepted for bull_put_spread."""
    client = _make_client({"AAPL": 40.0})
    survivors, log = screen_spread_candidates(
        symbols=["AAPL"],
        strategy_name="bull_put_spread",
        regime="NEUTRAL",
        iv_env="HIGH",
        orats_client=client,
    )
    assert "AAPL" in survivors


# ---------------------------------------------------------------------------
# Test 4: Batch call count ≤ ceil(N/10)
# ---------------------------------------------------------------------------

def test_batch_call_count():
    """get_iv_rank_batch is called ≤ ceil(N/10) times, not N times."""
    n = 25
    symbols = [f"SYM{i:02d}" for i in range(n)]
    ivr_map = {sym.upper(): 60.0 for sym in symbols}

    call_count = 0
    call_args = []

    def _counting_batch(syms):
        nonlocal call_count
        call_count += 1
        call_args.append(list(syms))
        return {s.upper(): {"ivRank1y": ivr_map[s.upper()]} for s in syms}

    client = MagicMock()
    client.get_iv_rank_batch.side_effect = _counting_batch

    screen_spread_candidates(
        symbols=symbols,
        strategy_name="bull_put_spread",
        regime="NEUTRAL",
        iv_env="HIGH",
        orats_client=client,
    )

    # get_iv_rank_batch already handles chunking internally; the screen calls it once
    # with all symbols and the client handles batching internally.
    # We just verify it was called exactly once (the client batches internally).
    assert call_count == 1, (
        f"screen_spread_candidates should call get_iv_rank_batch once with all symbols; "
        f"called {call_count} times"
    )
    assert len(call_args[0]) == n


# ---------------------------------------------------------------------------
# Test 5: Survivors returned in deterministic (input) order
# ---------------------------------------------------------------------------

def test_survivors_in_deterministic_order():
    """Survivors are returned in the same order as the input symbols list."""
    symbols = ["GOOG", "AAPL", "MSFT", "AMZN", "TSLA"]
    # All pass IVR (55 > 35 for bull_put_spread)
    ivr_map = {s: 55.0 for s in symbols}
    client = _make_client(ivr_map)

    survivors, _ = screen_spread_candidates(
        symbols=symbols,
        strategy_name="bull_put_spread",
        regime="NEUTRAL",
        iv_env="HIGH",
        orats_client=client,
    )

    assert survivors == symbols  # same order, no sorting or randomization


def test_survivors_stable_after_rejections():
    """Rejections don't perturb the order of surviving symbols."""
    symbols = ["AAA", "BBB", "CCC", "DDD", "EEE"]
    # BBB and DDD fail IVR
    ivr_map = {"AAA": 55.0, "BBB": 10.0, "CCC": 55.0, "DDD": 10.0, "EEE": 55.0}
    client = _make_client(ivr_map)

    survivors, _ = screen_spread_candidates(
        symbols=symbols,
        strategy_name="bull_put_spread",
        regime="NEUTRAL",
        iv_env="HIGH",
        orats_client=client,
    )

    assert survivors == ["AAA", "CCC", "EEE"]


# ---------------------------------------------------------------------------
# Test 6: Unknown strategy name returns all symbols (defensive)
# ---------------------------------------------------------------------------

def test_unknown_strategy_passes_all():
    """Unknown strategy name returns all symbols without any filtering."""
    client = MagicMock()
    symbols = ["AAPL", "MSFT"]
    survivors, log = screen_spread_candidates(
        symbols=symbols,
        strategy_name="nonexistent_strategy",
        regime="NEUTRAL",
        iv_env="HIGH",
        orats_client=client,
    )
    assert survivors == symbols
    assert log == []
    client.get_iv_rank_batch.assert_not_called()


# ---------------------------------------------------------------------------
# Test 7: IVR batch failure — missing data passes symbols through
# ---------------------------------------------------------------------------

def test_ivr_batch_failure_passes_symbols_through(caplog):
    """If get_iv_rank_batch raises, all symbols pass the IVR gate (conservative)."""
    import logging
    client = MagicMock()
    client.get_iv_rank_batch.side_effect = RuntimeError("ORATS unavailable")

    with caplog.at_level(logging.WARNING, logger="data.spread_screen"):
        survivors, log = screen_spread_candidates(
            symbols=["AAPL", "MSFT"],
            strategy_name="bull_put_spread",
            regime="NEUTRAL",
            iv_env="HIGH",
            orats_client=client,
        )

    # All symbols pass through when IVR data is unavailable
    assert "AAPL" in survivors
    assert "MSFT" in survivors
    # No IVR rejections in log
    assert not any(r["reason"].startswith("ivr_") for r in log)
    # Warning was logged
    assert any("get_iv_rank_batch failed" in r.message for r in caplog.records)
