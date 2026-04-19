"""Shared helpers for the adversarial LLM output test corpus.

Import patterns intentionally mirror tests/test_claude_advisor.py so both
suites use the same mock-building approach.  Changes to _make_advisor or
_make_response in that module should be reflected here.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

from tests.test_claude_advisor import _make_advisor, _make_response  # noqa: F401

_REQUIRED_FIXTURE_FIELDS = ("id", "description", "failure_mode", "advisor", "mocked_api", "assertions")
_REQUIRED_ADVISOR_FIELDS = ("method", "phase")


def load_fixture(path: Path) -> dict:
    """Load and schema-validate a fixture JSON file.

    Raises ValueError with the offending file name if any required field
    is absent — so a bad fixture fails at collection time, not silently.
    """
    try:
        fixture = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Fixture {path.name} is not valid JSON: {exc}") from exc

    for field in _REQUIRED_FIXTURE_FIELDS:
        if field not in fixture:
            raise ValueError(f"Fixture {path.name} is missing required field: {field!r}")

    for field in _REQUIRED_ADVISOR_FIELDS:
        if field not in fixture["advisor"]:
            raise ValueError(f"Fixture {path.name} advisor section missing: {field!r}")

    if "parse_layer" not in fixture["assertions"]:
        raise ValueError(f"Fixture {path.name} assertions missing 'parse_layer'")

    return fixture


def build_mock_response(fixture: dict) -> MagicMock:
    """Build a mock Anthropic response from fixture mocked_api metadata."""
    mocked_api = fixture["mocked_api"]
    stop_reason = mocked_api.get("stop_reason", "end_turn")
    payload = mocked_api.get("response_payload", {})
    return _make_response(payload, stop_reason)


def build_advisor():
    """Construct a ClaudeAdvisor using the same setup as test_claude_advisor.py."""
    return _make_advisor()


def run_advisor_from_fixture(advisor, fixture: dict) -> dict:
    """Dispatch to the correct advisor method based on fixture metadata.

    Always installs a passthrough _inject_strategy_params so prompt files
    and strategy YAMLs are not needed for corpus tests.
    """
    advisor._inject_strategy_params = lambda t, s: t

    method = fixture["advisor"]["method"]
    strategy_type = fixture["advisor"].get("strategy_type", "wheel")
    phase = fixture["advisor"]["phase"]
    context = fixture.get("context", {})

    if method == "ask":
        from strategies.wheel_strategy import WheelState
        state_map = {s.value.lower(): s for s in WheelState}
        if phase.lower() not in state_map:
            raise ValueError(f"Unknown WheelState phase: {phase!r}")
        return advisor.ask(context, state_map[phase.lower()])

    if method == "ask_spread":
        return advisor.ask_spread(context, strategy_type, phase)

    if method == "ask_turnover_wheel":
        from strategies.turnover_wheel_strategy import TurnoverWheelState
        state_map = {s.value.lower(): s for s in TurnoverWheelState}
        if phase.lower() not in state_map:
            raise ValueError(f"Unknown TurnoverWheelState phase: {phase!r}")
        if not hasattr(advisor, "turnover_wheel_phase_prompts"):
            advisor.turnover_wheel_phase_prompts = {s: "" for s in TurnoverWheelState}
        return advisor.ask_turnover_wheel(context, state_map[phase.lower()])

    raise ValueError(f"Unknown advisor method: {method!r}")


def run_guardrails_from_fixture(decision: dict, fixture: dict) -> tuple[bool, str]:
    """Dispatch to the correct Guardrails validation method for the fixture.

    Returns (True, "") when the fixture has no guardrail_layer assertions,
    matching the expected True/empty-string from a passing check so callers
    can unconditionally unpack the tuple.
    """
    guard_cfg = fixture["assertions"].get("guardrail_layer")
    if guard_cfg is None:
        return True, ""

    from strategies.guardrails import Guardrails
    g = Guardrails()

    method = fixture["advisor"]["method"]
    strategy_type = fixture["advisor"].get("strategy_type", "wheel")
    guardrail_args = fixture.get("guardrail_args", {})

    account = guardrail_args.get("account", {"buying_power": 100000})
    positions = guardrail_args.get("positions", [])
    # guardrail_args.context takes precedence; fall back to the advisor context
    context = guardrail_args.get("context", fixture.get("context", {}))
    open_spreads = guardrail_args.get("open_spreads", None)

    if method in ("ask", "ask_turnover_wheel"):
        return g.validate(decision, account, positions, context)

    if method == "ask_spread":
        _dispatch = {
            "bull_put_spread":     g.validate_bull_put_spread_entry,
            "bear_call_spread":    g.validate_bear_call_spread_entry,
            "iron_condor":         g.validate_iron_condor_entry,
            "iron_butterfly":      g.validate_iron_butterfly_entry,
            "long_call_vertical":  g.validate_long_call_vertical_entry,
            "calendar_spread":     g.validate_calendar_spread_entry,
        }
        fn = _dispatch.get(strategy_type)
        if fn is None:
            raise ValueError(f"No guardrail dispatch for strategy_type={strategy_type!r}")
        return fn(decision, context, account, open_spreads)

    raise ValueError(f"No guardrail dispatch for method={method!r}")


def classify_rejection_str(reason: str) -> str:
    """Map a free-text rejection reason to its SkipCode string value."""
    from strategies.guardrails import Guardrails
    return Guardrails.classify_rejection(reason)
