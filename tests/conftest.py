"""Shared pytest configuration and fixtures."""

import os
import pytest


@pytest.fixture(autouse=True, scope="session")
def allow_orats_local():
    """Allow ORATS calls in the test suite.

    Tests run with RENDER=false (local dev), which would trigger the local
    guard in check_and_reserve() and block all ORATS calls.  This fixture
    sets ALLOW_ORATS_LOCAL=1 for the entire test session so tests can
    exercise the ledger without hitting the guard.

    This does NOT affect production behaviour — the guard only checks env
    vars at call time, so Render's environment (no ALLOW_ORATS_LOCAL set,
    RENDER=true) is unaffected.
    """
    os.environ["ALLOW_ORATS_LOCAL"] = "1"
    yield
    os.environ.pop("ALLOW_ORATS_LOCAL", None)
