"""Tests for utils.retry."""

import pytest
from unittest.mock import patch

from utils.retry import retry_on_transient


def test_retries_and_succeeds():
    calls = {"n": 0}

    @retry_on_transient(max_retries=3, base_delay=0.0, max_delay=0.0)
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("boom")
        return "ok"

    with patch("time.sleep"):
        assert flaky() == "ok"
    assert calls["n"] == 3


def test_respects_max_retries():
    calls = {"n": 0}

    @retry_on_transient(max_retries=2, base_delay=0.0, max_delay=0.0)
    def always_fails():
        calls["n"] += 1
        raise RuntimeError("boom")

    with patch("time.sleep"):
        with pytest.raises(RuntimeError):
            always_fails()
    # 1 initial + 2 retries = 3 total
    assert calls["n"] == 3


def test_does_not_retry_auth_errors():
    calls = {"n": 0}

    class AuthErr(Exception):
        status_code = 401

    @retry_on_transient(max_retries=5, base_delay=0.0, max_delay=0.0)
    def auth_fail():
        calls["n"] += 1
        raise AuthErr("forbidden")

    with pytest.raises(AuthErr):
        auth_fail()
    assert calls["n"] == 1


def test_does_not_retry_403():
    calls = {"n": 0}

    class Forbidden(Exception):
        code = 403

    @retry_on_transient(max_retries=5, base_delay=0.0)
    def fn():
        calls["n"] += 1
        raise Forbidden()

    with pytest.raises(Forbidden):
        fn()
    assert calls["n"] == 1
