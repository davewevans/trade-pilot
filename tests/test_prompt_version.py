"""Tests for ClaudeAdvisor._compute_prompt_version and prompt_version attribute."""

import hashlib
from unittest.mock import MagicMock, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_advisor_with_prompts_dir(prompts_dir, monkeypatch):
    """Construct a ClaudeAdvisor pointing at a custom prompts directory."""
    import ai.claude_advisor as _mod
    monkeypatch.setattr(_mod, "_PROMPTS_DIR", prompts_dir)

    from ai.claude_advisor import ClaudeAdvisor
    with patch("ai.claude_advisor.anthropic.Anthropic"), \
         patch("ai.claude_advisor.settings"):
        advisor = ClaudeAdvisor.__new__(ClaudeAdvisor)
        advisor.client = MagicMock()
        advisor.model = "claude-sonnet-4-6"
        advisor._last_usage = None
        advisor._api_usage_repo = None
        advisor.thinking_mode = "off"
        advisor.prompt_version = "unknown"
    return advisor


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_compute_prompt_version_is_deterministic(tmp_path, monkeypatch):
    """Same directory contents → same hash on repeated calls."""
    (tmp_path / "a.md").write_text("hello world", encoding="utf-8")
    (tmp_path / "b.md").write_text("second file", encoding="utf-8")

    advisor = _make_advisor_with_prompts_dir(tmp_path, monkeypatch)

    v1 = advisor._compute_prompt_version()
    v2 = advisor._compute_prompt_version()

    assert v1 == v2
    assert len(v1) == 12
    assert v1 != "unknown"


def test_compute_prompt_version_changes_on_content_change(tmp_path, monkeypatch):
    """Adding a new .md file changes the hash."""
    (tmp_path / "base.md").write_text("base content", encoding="utf-8")

    advisor = _make_advisor_with_prompts_dir(tmp_path, monkeypatch)

    v_before = advisor._compute_prompt_version()
    (tmp_path / "extra.md").write_text("extra content", encoding="utf-8")
    v_after = advisor._compute_prompt_version()

    assert v_before != v_after
    assert len(v_before) == 12
    assert len(v_after) == 12


def test_compute_prompt_version_handles_missing_dir_gracefully(tmp_path, monkeypatch):
    """A non-existent _PROMPTS_DIR returns 'unknown' without raising."""
    missing = tmp_path / "does_not_exist"

    advisor = _make_advisor_with_prompts_dir(missing, monkeypatch)

    result = advisor._compute_prompt_version()
    assert result == "unknown"


def test_advisor_sets_prompt_version_after_load_prompts(tmp_path, monkeypatch):
    """After load_prompts(), advisor.prompt_version is a 12-char hex string."""
    # Write minimal prompt files so load_prompts() doesn't error out
    (tmp_path / "system.md").write_text("system prompt", encoding="utf-8")
    for name in (
        "wheel_idle", "wheel_short_put", "wheel_long_stock", "wheel_short_call",
    ):
        (tmp_path / f"{name}.md").write_text(f"prompt for {name}", encoding="utf-8")

    import ai.claude_advisor as _mod
    monkeypatch.setattr(_mod, "_PROMPTS_DIR", tmp_path)

    from ai.claude_advisor import ClaudeAdvisor
    from strategies.wheel_strategy import WheelState

    with patch("ai.claude_advisor.anthropic.Anthropic"), \
         patch("ai.claude_advisor.settings") as mock_settings:
        mock_settings.THINKING_MODE = "off"
        mock_settings.ANTHROPIC_API_KEY = "test-key"
        advisor = ClaudeAdvisor.__new__(ClaudeAdvisor)
        advisor.client = MagicMock()
        advisor.model = "claude-sonnet-4-6"
        advisor._last_usage = None
        advisor._api_usage_repo = None
        advisor.thinking_mode = "off"
        advisor.prompt_version = "unknown"
        advisor.load_prompts()

    assert isinstance(advisor.prompt_version, str)
    assert len(advisor.prompt_version) == 12
    assert advisor.prompt_version != "unknown"
    # Verify it's valid lowercase hex
    int(advisor.prompt_version, 16)
