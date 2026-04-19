"""Parametrized runner for the adversarial LLM output test corpus.

Each fixture file in fixtures/ is a self-contained test case describing
a pathological-but-possible Claude response and the expected handling
by the parse / safe-skip / guardrail pipeline.

Run the full corpus:
    pytest tests/adversarial_llm_outputs/

Run a single fixture by stem:
    pytest tests/adversarial_llm_outputs/ -k stop_reason_refusal__wheel__idle
"""

from pathlib import Path

import pytest

from tests.adversarial_llm_outputs._helpers import (
    build_advisor,
    build_mock_response,
    classify_rejection_str,
    load_fixture,
    run_advisor_from_fixture,
    run_guardrails_from_fixture,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
FIXTURE_PATHS = sorted(p for p in FIXTURE_DIR.glob("*.json"))


def _collect_fixtures():
    """Validate fixtures at collection time so bad JSON fails early."""
    collected = []
    for path in FIXTURE_PATHS:
        try:
            fixture = load_fixture(path)
        except ValueError as exc:
            pytest.fail(f"Fixture collection error — {exc}")
        collected.append((path.stem, fixture))
    return collected


_FIXTURES = _collect_fixtures()


@pytest.mark.parametrize("fixture_id,fixture", _FIXTURES, ids=[f[0] for f in _FIXTURES])
def test_corpus_entry(fixture_id, fixture):
    advisor = build_advisor()
    advisor.client.messages.create.return_value = build_mock_response(fixture)

    # Run the advisor — any unhandled exception is a regression
    decision = run_advisor_from_fixture(advisor, fixture)

    # ── Parse-layer assertions ─────────────────────────────────────────────────
    parse = fixture["assertions"]["parse_layer"]

    assert not parse.get("should_raise"), (
        "Fixtures with should_raise=true are not yet implemented in this runner; "
        "use pytest.raises directly in a dedicated test."
    )

    expected_action = parse["returned_action"]
    assert decision["action"] == expected_action, (
        f"[{fixture_id}] Expected action={expected_action!r}, got {decision['action']!r}"
    )

    skip_needle = parse.get("returned_skip_reason_contains")
    if skip_needle is not None:
        actual_skip = decision.get("skip_reason") or ""
        assert skip_needle in actual_skip, (
            f"[{fixture_id}] Expected skip_reason to contain {skip_needle!r}, "
            f"got {actual_skip!r}"
        )

    # ── Guardrail-layer assertions ─────────────────────────────────────────────
    guard = fixture["assertions"].get("guardrail_layer")
    if guard is not None:
        ok, reason = run_guardrails_from_fixture(decision, fixture)
        expected_ok = guard["should_validate"]

        assert ok is expected_ok, (
            f"[{fixture_id}] Guardrail.validate returned {ok}, expected {expected_ok}. "
            f"Rejection reason: {reason!r}"
        )

        for needle in guard.get("rejection_reason_contains", []):
            assert needle.lower() in reason.lower(), (
                f"[{fixture_id}] Rejection reason {reason!r} missing expected substring {needle!r}"
            )

        expected_code = guard.get("classify_rejection_to")
        if expected_code is not None and not ok:
            actual_code = classify_rejection_str(reason)
            assert actual_code == expected_code, (
                f"[{fixture_id}] classify_rejection returned {actual_code!r}, "
                f"expected {expected_code!r} for reason {reason!r}"
            )
