"""Tests for the _inject_strategy_params prompt template mechanism."""

from unittest.mock import MagicMock, patch

import pytest


def _make_advisor():
    """Create a ClaudeAdvisor without hitting Anthropic or loading prompts from disk."""
    with patch("anthropic.Anthropic"):
        from ai.claude_advisor import ClaudeAdvisor
        advisor = ClaudeAdvisor.__new__(ClaudeAdvisor)
        advisor.client = MagicMock()
        advisor.model = "claude-sonnet-4-6"
        advisor._last_usage = None
        # Skip load_prompts — we don't need disk files for injection tests
        advisor.phase_prompts = {}
        advisor.spread_prompts = {}
        # Load the real system prompt if needed
        advisor.system_prompt = ""
    return advisor


def test_known_keys_are_replaced():
    advisor = _make_advisor()
    prompt = "DTE between {{dte_min}} and {{dte_max}}"
    result = advisor._inject_strategy_params(prompt, "bull_put_spread")
    assert "{{dte_min}}" not in result
    assert "{{dte_max}}" not in result
    assert "21" in result  # dte_min from definition


def test_unknown_keys_left_as_is():
    advisor = _make_advisor()
    prompt = "Some {{unknown_key_xyz}} here"
    result = advisor._inject_strategy_params(prompt, "bull_put_spread")
    assert "{{unknown_key_xyz}}" in result


def test_prompt_with_no_variables_returned_unchanged():
    advisor = _make_advisor()
    prompt = "No template variables here at all."
    result = advisor._inject_strategy_params(prompt, "wheel")
    assert result == prompt


def test_replacement_with_actual_prompt_file_and_definition():
    advisor = _make_advisor()
    # Read the actual prompt file
    from pathlib import Path
    prompt_path = Path(__file__).resolve().parent.parent / "prompts" / "bull_put_spread_idle.md"
    if not prompt_path.exists():
        pytest.skip("bull_put_spread_idle.md not found")
    prompt_text = prompt_path.read_text(encoding="utf-8")

    result = advisor._inject_strategy_params(prompt_text, "bull_put_spread")

    # After injection, the numeric template vars from the strategy definition should be replaced
    from strategies.strategy_loader import get_strategy_entry_params
    params = get_strategy_entry_params("bull_put_spread")

    # Verify some specific replacements
    dte_min = str(params.get("dte_min", ""))
    dte_max = str(params.get("dte_max", ""))
    if dte_min:
        assert f"{{{{dte_min}}}}" not in result
    if dte_max:
        assert f"{{{{dte_max}}}}" not in result


def test_nonexistent_strategy_returns_prompt_unchanged():
    advisor = _make_advisor()
    prompt = "DTE between {{dte_min}} and {{dte_max}}"
    result = advisor._inject_strategy_params(prompt, "nonexistent_strategy_xyz")
    # Should return the prompt unchanged (FileNotFoundError logged as warning)
    assert result == prompt


def test_wheel_nested_params_are_flattened():
    advisor = _make_advisor()
    # Wheel entry has nested csp and cc dicts
    prompt = "CSP delta {{csp_delta_min}} to {{csp_delta_max}}, CC delta {{cc_delta_min}}"
    result = advisor._inject_strategy_params(prompt, "wheel")
    assert "{{csp_delta_min}}" not in result
    assert "{{csp_delta_max}}" not in result
    assert "{{cc_delta_min}}" not in result
    assert "0.2" in result  # csp_delta_min = 0.20
