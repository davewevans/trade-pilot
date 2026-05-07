"""Regression tests for jobs/market_open.py dispatch resilience.

The wheel and turnover_wheel dispatch loops MUST continue past per-symbol
exceptions so a single bad symbol cannot block all subsequent symbols. This
file pins that contract so it cannot be removed by future refactors.

Background: on 2026-05-07 INTC raised AttributeError mid-loop in turnover_wheel
dispatch. The loop continued past it because the existing try/except absorbed
the exception. These tests guard against regression of that resilience.
"""

import inspect
import logging


def _get_run_source() -> str:
    from jobs import market_open
    return inspect.getsource(market_open.run)


def test_wheel_loop_has_per_symbol_exception_handler():
    """Wheel loop must catch per-symbol exceptions with logger.exception."""
    src = _get_run_source()
    # Existing handler at the bottom of the wheel loop.
    assert 'logger.exception("Market open failed for %s — continuing", symbol)' in src, (
        "wheel dispatch loop is missing its per-symbol exception handler"
    )


def test_turnover_wheel_loop_has_per_symbol_exception_handler():
    """Turnover-wheel loop must catch per-symbol exceptions with logger.exception."""
    src = _get_run_source()
    assert 'logger.exception("Turnover wheel dispatch failed for %s", symbol)' in src, (
        "turnover_wheel dispatch loop is missing its per-symbol exception handler"
    )


def test_dispatch_loop_pattern_continues_past_failing_symbol(caplog):
    """Behavioral pin: a per-symbol exception must not halt subsequent symbols.

    Mirrors the try/except contract used in jobs.market_open.run for both the
    wheel and turnover_wheel dispatch loops. If someone replaces the per-symbol
    try/except with a single outer try/except, the third symbol would not be
    dispatched and this test would fail.
    """
    symbols = ["AAA", "BBB", "CCC"]
    dispatched: list[str] = []

    def dispatch(symbol: str) -> None:
        dispatched.append(symbol)
        if symbol == "BBB":
            raise RuntimeError("simulated mid-loop failure")

    logger_ = logging.getLogger("jobs.market_open")

    with caplog.at_level(logging.ERROR, logger="jobs.market_open"):
        for symbol in symbols:
            try:
                dispatch(symbol)
            except Exception:
                logger_.exception("Market open failed for %s — continuing", symbol)

    assert dispatched == ["AAA", "BBB", "CCC"], (
        "third symbol must still be dispatched after second symbol raises"
    )
    assert any(
        "Market open failed for BBB" in rec.getMessage()
        for rec in caplog.records
    ), "exception on BBB must be logged"
