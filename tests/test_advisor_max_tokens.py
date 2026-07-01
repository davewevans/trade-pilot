"""Tests for the ADVISOR_MAX_TOKENS config setting.

sonnet-5 truncates every non-thinking advisor call at the old hardcoded
2048-token cap (stop_reason=max_tokens -> forced SKIP). This setting makes
the cap configurable and raises the default; see ai/claude_advisor.py's
three ``max_tokens=16000 if thinking else settings.ADVISOR_MAX_TOKENS``
call sites.
"""

from config import Settings


def test_advisor_max_tokens_default(monkeypatch):
    monkeypatch.delenv("ADVISOR_MAX_TOKENS", raising=False)
    s = Settings()
    assert s.ADVISOR_MAX_TOKENS == 4096


def test_advisor_max_tokens_env_override(monkeypatch):
    monkeypatch.setenv("ADVISOR_MAX_TOKENS", "8192")
    s = Settings()
    assert s.ADVISOR_MAX_TOKENS == 8192
