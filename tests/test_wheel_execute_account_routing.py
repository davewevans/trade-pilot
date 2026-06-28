"""Execution-account routing regression tests for jobs/market_open.py.

Reproduces the 2026-06-28 Standard Wheel misroute: market_open evaluated the
wheel against ``wheel_broker`` (paper_2) via ``wheel_ctx_builder`` but executed
the resulting decision through the default ``broker`` (paper_1 / adaptive
spreads). Wheel orders — and at least one assignment (VZ, 100 sh) — landed in
the spreads account while Claude reasoned about paper_2.

The guarantee under test: **for every strategy, the broker handed to execution
is the same account-bound broker used to build that strategy's ContextBuilder**,
and the per-wheel Guardrails (whose covered-call check falls back to
``broker.get_equity_positions()``) are bound to that same executing broker.

Running ``market_open.run()`` end to end is impractical (15+ external
dependencies — see ``tests/test_multi_account_routing.py``), so the wiring is
asserted statically against the module's AST. ``ast.parse`` reads the source
without importing it, so none of those dependencies are touched. Each test here
fails on the pre-fix source (``execute_decision(broker, decision)`` /
``guardrails.validate`` for the wheels) and passes after the fix.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

MARKET_OPEN = Path(__file__).resolve().parent.parent / "jobs" / "market_open.py"


def _module() -> ast.Module:
    return ast.parse(MARKET_OPEN.read_text(encoding="utf-8"))


def _calls_to(tree: ast.Module, func_name: str) -> list[ast.Call]:
    """Every Call whose func is a bare Name ``func_name`` (e.g. execute_decision)."""
    return [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == func_name
    ]


def _ctor_broker_kwargs(tree: ast.Module, ctor_name: str) -> set[str]:
    """Set of ``broker=<Name>`` argument ids for every ``ctor_name(...)`` call."""
    brokers: set[str] = set()
    for n in _calls_to(tree, ctor_name):
        for kw in n.keywords:
            if kw.arg == "broker" and isinstance(kw.value, ast.Name):
                brokers.add(kw.value.id)
    return brokers


# ── Execution routing ─────────────────────────────────────────────────────


def test_no_strategy_executes_through_default_broker():
    """The bare default ``broker`` must never be passed to execute_decision.

    Pre-fix this set was {"broker", "turnover_wheel_broker"} — the Standard
    Wheel handing the default broker to execution was the bug.
    """
    tree = _module()
    calls = _calls_to(tree, "execute_decision")
    assert calls, "no execute_decision calls found in market_open.py"

    exec_brokers = [
        c.args[0].id
        for c in calls
        if c.args and isinstance(c.args[0], ast.Name)
    ]
    assert len(exec_brokers) == len(calls), (
        "every execute_decision call should take a simple broker variable as "
        "its first positional argument"
    )

    assert "broker" not in exec_brokers, (
        "Standard Wheel misroute regression: execute_decision was handed the "
        f"default `broker`. Execution brokers seen: {exec_brokers}"
    )
    assert "wheel_broker" in exec_brokers, "Standard Wheel must execute via wheel_broker"
    assert "turnover_wheel_broker" in exec_brokers, "Turnover Wheel routing changed"


def test_execution_broker_matches_a_context_builder_broker():
    """Every execution broker must also be a broker some ContextBuilder was
    built with — evaluation account == execution account.

    Pre-fix the wheel executed via ``broker``, which is never used to construct
    a ContextBuilder (the wheel's is built with ``wheel_broker``), so the
    subset check fails.
    """
    tree = _module()
    ctx_brokers = _ctor_broker_kwargs(tree, "ContextBuilder")
    exec_brokers = {
        c.args[0].id
        for c in _calls_to(tree, "execute_decision")
        if c.args and isinstance(c.args[0], ast.Name)
    }
    assert exec_brokers, "no execute_decision broker arguments resolved"
    assert exec_brokers <= ctx_brokers, (
        f"execution broker(s) {sorted(exec_brokers - ctx_brokers)} are not used "
        f"to build any ContextBuilder — evaluation/execution account mismatch. "
        f"ContextBuilder brokers: {sorted(ctx_brokers)}"
    )


# ── Guardrail account binding ──────────────────────────────────────────────


def test_wheel_guardrails_bound_to_executing_broker():
    """The wheels' Guardrails.validate sites must use per-wheel Guardrails
    bound to the SAME broker the wheel executes through — not the shared
    default-broker ``guardrails``.

    Matters because Guardrails._check_sell_call (covered-call entry) falls back
    to ``self.broker.get_equity_positions()`` to verify share ownership; binding
    it to the default broker would validate ownership against paper_1 while the
    wheel trades on paper_2 / paper_6.

    Pre-fix both wheels called ``guardrails.validate`` (bound to ``broker``), so
    the per-wheel assertions below fail.
    """
    tree = _module()

    # variable name -> broker variable it was constructed with
    g_var_to_broker: dict[str, str | None] = {}
    for n in ast.walk(tree):
        if (
            isinstance(n, ast.Assign)
            and isinstance(n.value, ast.Call)
            and isinstance(n.value.func, ast.Name)
            and n.value.func.id == "Guardrails"
        ):
            broker_var = next(
                (kw.value.id for kw in n.value.keywords
                 if kw.arg == "broker" and isinstance(kw.value, ast.Name)),
                None,
            )
            for tgt in n.targets:
                if isinstance(tgt, ast.Name):
                    g_var_to_broker[tgt.id] = broker_var

    # guardrails variables used in `<var>.validate(...)` calls
    validate_vars = {
        n.func.value.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "validate"
        and isinstance(n.func.value, ast.Name)
    }

    assert "wheel_guardrails" in validate_vars, (
        "Standard Wheel must validate via wheel_guardrails, not the shared default"
    )
    assert "turnover_wheel_guardrails" in validate_vars, (
        "Turnover Wheel must validate via turnover_wheel_guardrails"
    )
    assert g_var_to_broker.get("wheel_guardrails") == "wheel_broker"
    assert g_var_to_broker.get("turnover_wheel_guardrails") == "turnover_wheel_broker"

    for gv in validate_vars:
        assert g_var_to_broker.get(gv) != "broker", (
            f"{gv}.validate() uses Guardrails bound to the default broker — the "
            f"covered-call equity check would read the wrong account"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
