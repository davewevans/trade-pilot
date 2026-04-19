"""Tests for the R4 skip_reasons module."""

import pytest

from strategies.skip_reasons import SkipGate, SkipReason, REASON_TO_GATE


def test_every_skip_reason_has_a_gate_mapping():
    """Every SkipReason must have a mapping in REASON_TO_GATE."""
    all_reasons = set(SkipReason)
    mapped_reasons = set(REASON_TO_GATE.keys())
    missing = all_reasons - mapped_reasons
    assert missing == set(), f"SkipReasons missing from REASON_TO_GATE: {missing}"


def test_no_orphan_gates():
    """Every SkipGate must have at least one reason mapping to it."""
    all_gates = set(SkipGate)
    mapped_gates = set(REASON_TO_GATE.values())
    orphan = all_gates - mapped_gates
    assert orphan == set(), f"SkipGates with no reason: {orphan}"


def test_reason_to_gate_values_are_skip_gate_instances():
    """Every value in REASON_TO_GATE is a SkipGate."""
    for reason, gate in REASON_TO_GATE.items():
        assert isinstance(gate, SkipGate), f"{reason} maps to non-SkipGate: {gate!r}"


def test_reason_to_gate_keys_are_skip_reason_instances():
    """Every key in REASON_TO_GATE is a SkipReason."""
    for reason in REASON_TO_GATE:
        assert isinstance(reason, SkipReason), f"Non-SkipReason key: {reason!r}"


def test_specific_gate_assignments():
    """Spot-check key gate assignments."""
    assert REASON_TO_GATE[SkipReason.EARNINGS_TOO_CLOSE] == SkipGate.PRE_CHECK
    assert REASON_TO_GATE[SkipReason.CIRCUIT_BREAKER_RED] == SkipGate.CIRCUIT_BREAKER
    assert REASON_TO_GATE[SkipReason.CIRCUIT_BREAKER_YELLOW] == SkipGate.CIRCUIT_BREAKER
    assert REASON_TO_GATE[SkipReason.LIQUIDITY_TIER_D] == SkipGate.LIQUIDITY_FLOOR
    assert REASON_TO_GATE[SkipReason.WINRATE_BELOW_FLOOR] == SkipGate.WINRATE_FLOOR
    assert REASON_TO_GATE[SkipReason.GUARDRAIL_EARNINGS] == SkipGate.GUARDRAIL
    assert REASON_TO_GATE[SkipReason.NO_CANDIDATES_FOUND] == SkipGate.NO_CANDIDATE
    assert REASON_TO_GATE[SkipReason.BOT_HALTED] == SkipGate.HALTED
    assert REASON_TO_GATE[SkipReason.CLAUDE_SKIP] == SkipGate.CLAUDE_SKIP
    assert REASON_TO_GATE[SkipReason.SCHEMA_INVALID] == SkipGate.LLM_OUTPUT
