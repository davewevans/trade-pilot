"""Unit tests for ClaudeAdvisor with structured outputs (Story 2B).

Verifies:
- The correct schema is selected for each (strategy, phase) combo
- output_config.format is passed on every call
- safe-SKIP is returned on stop_reason refusal/max_tokens
- Both ask() and ask_spread() parse the JSON response correctly
- Schema and guardrails field names don't disagree (round-trip check)
"""

import json
from unittest.mock import MagicMock, call, patch

import pytest

from ai.schemas import get_schema, _REGISTRY
from strategies.wheel_strategy import WheelState


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_advisor():
    """Construct a ClaudeAdvisor without hitting Anthropic or reading prompts."""
    from ai.claude_advisor import ClaudeAdvisor
    with patch("ai.claude_advisor.anthropic.Anthropic"), \
         patch("ai.claude_advisor.settings"):
        advisor = ClaudeAdvisor.__new__(ClaudeAdvisor)
        advisor.client = MagicMock()
        advisor.model = "claude-sonnet-4-6"
        advisor._last_usage = None
        advisor._api_usage_repo = None
        # Load real prompts if available; fall back to empty strings
        advisor.phase_prompts = {s: "" for s in WheelState}
        advisor.spread_prompts = {k: "" for k in _REGISTRY if "_" in k and k not in
                                   ("wheel_idle", "wheel_short_put", "wheel_long_stock", "wheel_short_call")}
        advisor.system_prompt = ""
    return advisor


def _make_response(json_payload: dict, stop_reason: str = "end_turn"):
    """Build a mock Anthropic response."""
    resp = MagicMock()
    resp.stop_reason = stop_reason
    resp.content = [MagicMock()]
    resp.content[0].text = json.dumps(json_payload)
    resp.usage = MagicMock()
    resp.usage.model_dump.return_value = {}
    resp.usage.cache_read_input_tokens = 0
    resp.usage.cache_creation_input_tokens = 0
    resp.usage.input_tokens = 100
    resp.usage.output_tokens = 50
    return resp


# ── Schema registry tests ─────────────────────────────────────────────────────

def test_get_schema_wheel_idle():
    schema = get_schema("wheel", "idle")
    assert schema["type"] == "object"
    assert "sell_put" in schema["properties"]["action"]["enum"]
    assert "additionalProperties" in schema
    assert schema["additionalProperties"] is False


def test_get_schema_all_strategies():
    """Every (strategy, phase) combo should return a schema without error."""
    combos = [
        ("wheel", "idle"), ("wheel", "short_put"),
        ("wheel", "long_stock"), ("wheel", "short_call"),
        ("bull_put_spread", "idle"), ("bull_put_spread", "open"),
        ("bear_call_spread", "idle"), ("bear_call_spread", "open"),
        ("iron_condor", "idle"), ("iron_condor", "open"),
        ("iron_butterfly", "idle"), ("iron_butterfly", "open"),
        ("long_call_vertical", "idle"), ("long_call_vertical", "open"),
        ("calendar_spread", "idle"), ("calendar_spread", "open"),
    ]
    for strategy, phase in combos:
        schema = get_schema(strategy, phase)
        assert schema["type"] == "object", f"Schema for {strategy}/{phase} missing type"
        assert schema["additionalProperties"] is False, f"{strategy}/{phase} missing additionalProperties"


def test_get_schema_unknown_raises():
    with pytest.raises(KeyError):
        get_schema("unknown_strategy", "idle")


def test_all_schemas_have_additionalProperties_false():
    """Structured outputs require additionalProperties: false on every object."""
    def check_obj(schema, path="root"):
        if schema.get("type") == "object":
            assert schema.get("additionalProperties") is False, \
                f"additionalProperties missing at {path}"
        for k, v in schema.get("properties", {}).items():
            if isinstance(v, dict):
                check_obj(v, f"{path}.{k}")

    for key, schema in _REGISTRY.items():
        check_obj(schema, path=key)


# ── ask() tests ───────────────────────────────────────────────────────────────

def test_ask_passes_output_config_with_correct_schema():
    """ask() must pass output_config.format with the wheel_idle schema."""
    advisor = _make_advisor()
    decision = {
        "action": "sell_put", "symbol": "AAPL250516P00200000",
        "qty": 1, "order_type": "limit", "limit_price": -1.25,
        "reasoning": {"macro": "ok", "fundamental": "ok", "technical": "ok",
                      "volatility": "ok", "selection": "ok", "risk": "ok"},
        "confidence": "high", "skip_reason": None,
    }
    advisor.client.messages.create.return_value = _make_response(decision)
    advisor._inject_strategy_params = lambda t, s: t

    result = advisor.ask({}, WheelState.IDLE)

    call_kwargs = advisor.client.messages.create.call_args.kwargs
    assert "output_config" in call_kwargs
    fmt = call_kwargs["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"] == get_schema("wheel", "idle")
    assert result["action"] == "sell_put"


def test_ask_returns_safe_skip_on_refusal():
    advisor = _make_advisor()
    advisor.client.messages.create.return_value = _make_response({}, stop_reason="refusal")
    advisor._inject_strategy_params = lambda t, s: t

    result = advisor.ask({}, WheelState.IDLE)

    assert result["action"] == "skip"
    assert "refusal" in result["skip_reason"]
    assert isinstance(result["reasoning"], dict)


def test_ask_returns_safe_skip_on_max_tokens():
    advisor = _make_advisor()
    advisor.client.messages.create.return_value = _make_response({}, stop_reason="max_tokens")
    advisor._inject_strategy_params = lambda t, s: t

    result = advisor.ask({}, WheelState.IDLE)

    assert result["action"] == "skip"
    assert "max_tokens" in result["skip_reason"]


def test_ask_short_put_uses_correct_schema():
    advisor = _make_advisor()
    decision = {
        "action": "hold", "symbol": None, "qty": 1, "order_type": "limit",
        "limit_price": None,
        "reasoning": {"macro": "ok", "fundamental": "ok", "technical": "ok",
                      "volatility": "ok", "selection": "ok", "risk": "ok"},
        "confidence": "medium", "skip_reason": None,
    }
    advisor.client.messages.create.return_value = _make_response(decision)
    advisor._inject_strategy_params = lambda t, s: t

    advisor.ask({}, WheelState.SHORT_PUT)

    call_kwargs = advisor.client.messages.create.call_args.kwargs
    schema = call_kwargs["output_config"]["format"]["schema"]
    assert "roll" in schema["properties"]["action"]["enum"]


# ── ask_spread() tests ────────────────────────────────────────────────────────

def test_ask_spread_passes_correct_schema_bull_put_idle():
    advisor = _make_advisor()
    advisor.spread_prompts["bull_put_spread_idle"] = "test prompt"
    decision = {
        "action": "OPEN",
        "short_put_symbol": "AAPL250516P00200000",
        "long_put_symbol": "AAPL250516P00195000",
        "expiration": "2025-05-16",
        "dte": 30, "short_put_strike": 200.0, "long_put_strike": 195.0,
        "net_credit": 0.85, "max_loss": 415.0, "limit_price": -0.85,
        "reasoning": {"macro": "ok", "fundamental": "ok", "technical": "ok",
                      "volatility": "ok", "selection": "ok", "risk": "ok"},
        "confidence": "high", "skip_reason": None,
    }
    advisor.client.messages.create.return_value = _make_response(decision)
    advisor._inject_strategy_params = lambda t, s: t

    result = advisor.ask_spread({}, "bull_put_spread", "idle")

    call_kwargs = advisor.client.messages.create.call_args.kwargs
    schema = call_kwargs["output_config"]["format"]["schema"]
    assert schema == get_schema("bull_put_spread", "idle")
    assert result["action"] == "OPEN"


def test_ask_spread_passes_correct_schema_iron_condor_idle():
    advisor = _make_advisor()
    advisor.spread_prompts["iron_condor_idle"] = "test prompt"
    decision = {
        "action": "SKIP",
        "put_short_symbol": None, "put_long_symbol": None,
        "call_short_symbol": None, "call_long_symbol": None,
        "expiration": None, "dte": None,
        "total_credit": None, "max_loss": None, "limit_price": None,
        "reasoning": {"macro": "VIX too low", "fundamental": "—", "technical": "—",
                      "volatility": "IVR below threshold", "selection": "—", "risk": "—"},
        "confidence": "high", "skip_reason": "IVR below minimum",
    }
    advisor.client.messages.create.return_value = _make_response(decision)
    advisor._inject_strategy_params = lambda t, s: t

    result = advisor.ask_spread({}, "iron_condor", "idle")

    call_kwargs = advisor.client.messages.create.call_args.kwargs
    schema = call_kwargs["output_config"]["format"]["schema"]
    assert schema == get_schema("iron_condor", "idle")
    assert result["action"] == "SKIP"


def test_ask_spread_returns_safe_skip_on_refusal():
    advisor = _make_advisor()
    advisor.spread_prompts["bull_put_spread_idle"] = "test"
    advisor.client.messages.create.return_value = _make_response({}, stop_reason="refusal")
    advisor._inject_strategy_params = lambda t, s: t

    result = advisor.ask_spread({}, "bull_put_spread", "idle")

    assert result["action"] == "SKIP"
    assert "refusal" in result["skip_reason"]
    assert isinstance(result["reasoning"], dict)


def test_ask_spread_returns_safe_skip_on_api_error():
    advisor = _make_advisor()
    advisor.spread_prompts["iron_condor_idle"] = "test"
    advisor.client.messages.create.side_effect = Exception("Network error")
    advisor._inject_strategy_params = lambda t, s: t

    result = advisor.ask_spread({}, "iron_condor", "idle")

    assert result["action"] == "SKIP"
    assert result["skip_reason"] == "Claude API error"


def test_ask_spread_missing_prompt_returns_safe_skip():
    advisor = _make_advisor()
    # don't set spread_prompts["nonexistent_idle"]
    advisor.spread_prompts.pop("nonexistent_idle", None)

    result = advisor.ask_spread({}, "nonexistent", "idle")

    assert result["action"] == "SKIP"
    assert "missing" in result["skip_reason"].lower() or "no spread prompt" in result["skip_reason"].lower()


# ── Schema vs guardrails field-name agreement ─────────────────────────────────

def test_bull_put_spread_schema_provides_all_guardrail_fields():
    """Fields read by validate_bull_put_spread_entry must exist in the schema."""
    schema = get_schema("bull_put_spread", "idle")
    props = schema["properties"]
    # From guardrails.validate_bull_put_spread_entry
    for field in ("action", "limit_price", "net_credit", "max_loss", "dte",
                  "short_put_symbol", "long_put_symbol"):
        assert field in props, f"Schema missing guardrail field: {field}"


def test_iron_condor_schema_provides_all_guardrail_fields():
    """Fields read by validate_iron_condor_entry must exist in the schema."""
    schema = get_schema("iron_condor", "idle")
    props = schema["properties"]
    # From guardrails.validate_iron_condor_entry
    for field in ("action", "total_credit", "max_loss", "limit_price", "dte",
                  "put_short_symbol", "put_long_symbol",
                  "call_short_symbol", "call_long_symbol"):
        assert field in props, f"Schema missing guardrail field: {field}"


def test_bear_call_spread_schema_provides_all_guardrail_fields():
    """Fields read by validate_bear_call_spread_entry must exist in the schema."""
    schema = get_schema("bear_call_spread", "idle")
    props = schema["properties"]
    for field in ("action", "limit_price", "net_credit", "max_loss", "dte",
                  "short_call_symbol", "long_call_symbol"):
        assert field in props, f"Schema missing guardrail field: {field}"


def test_long_call_vertical_schema_provides_all_guardrail_fields():
    """Fields read by validate_long_call_vertical_entry must exist in the schema."""
    schema = get_schema("long_call_vertical", "idle")
    props = schema["properties"]
    for field in ("action", "limit_price", "net_debit", "dte",
                  "long_call_symbol", "short_call_symbol"):
        assert field in props, f"Schema missing guardrail field: {field}"


def test_wheel_idle_schema_provides_all_guardrail_fields():
    """Fields read by _check_sell_put / universal check must exist in the schema."""
    schema = get_schema("wheel", "idle")
    props = schema["properties"]
    for field in ("action", "symbol", "qty", "order_type", "limit_price"):
        assert field in props, f"Schema missing guardrail field: {field}"
