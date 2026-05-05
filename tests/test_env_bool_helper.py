"""Tests for config._env_bool — the boolean env var parser."""

import pytest
from config import _env_bool


def test_unset_returns_default_true(monkeypatch):
    monkeypatch.delenv("TEST_FLAG", raising=False)
    assert _env_bool("TEST_FLAG", default=True) is True


def test_unset_returns_default_false(monkeypatch):
    monkeypatch.delenv("TEST_FLAG", raising=False)
    assert _env_bool("TEST_FLAG", default=False) is False


def test_empty_string_returns_default(monkeypatch):
    """The footgun this helper exists to fix."""
    monkeypatch.setenv("TEST_FLAG", "")
    assert _env_bool("TEST_FLAG", default=True) is True
    assert _env_bool("TEST_FLAG", default=False) is False


def test_whitespace_only_returns_default(monkeypatch):
    monkeypatch.setenv("TEST_FLAG", "   ")
    assert _env_bool("TEST_FLAG", default=True) is True


def test_explicit_false_values(monkeypatch):
    for value in ("false", "False", "FALSE", "0", "no", "No", "NO", "off", "OFF"):
        monkeypatch.setenv("TEST_FLAG", value)
        assert _env_bool("TEST_FLAG", default=True) is False, f"Expected False for {value!r}"


def test_explicit_false_with_whitespace(monkeypatch):
    monkeypatch.setenv("TEST_FLAG", "  false  ")
    assert _env_bool("TEST_FLAG", default=True) is False


def test_true_values(monkeypatch):
    for value in ("true", "True", "TRUE", "1", "yes", "on", "enabled"):
        monkeypatch.setenv("TEST_FLAG", value)
        assert _env_bool("TEST_FLAG", default=True) is True


def test_unrecognized_value_returns_true_when_default_true(monkeypatch):
    """Default-on safety: anything not explicitly false is treated as true."""
    monkeypatch.setenv("TEST_FLAG", "banana")
    assert _env_bool("TEST_FLAG", default=True) is True


def test_unrecognized_value_returns_true_even_when_default_false(monkeypatch):
    """Explicit non-false values flip a default-false flag to True.

    This is intentional: the helper treats anything-not-explicitly-false as True.
    Callers who need strict default-disabled semantics should not use _env_bool.
    """
    monkeypatch.setenv("TEST_FLAG", "yes")
    assert _env_bool("TEST_FLAG", default=False) is True
