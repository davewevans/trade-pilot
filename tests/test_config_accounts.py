"""Tests for multi-account credential mapping."""

import os

import pytest
from unittest.mock import patch


def test_strategy_account_map_has_all_strategies():
    from config import settings
    expected = {
        "wheel", "iron_condor", "bull_put_spread",
        "bear_call_spread", "long_call_vertical",
    }
    assert set(settings.STRATEGY_ACCOUNT_MAP.keys()) == expected


def test_get_broker_credentials_wheel():
    from config import settings
    with patch.object(settings, "ALPACA_PAPER2_API_KEY", "paper2_key"), \
         patch.object(settings, "ALPACA_PAPER2_SECRET_KEY", "paper2_secret"):
        key, secret = settings.get_broker_credentials("wheel")
    assert key == "paper2_key"
    assert secret == "paper2_secret"


def test_get_broker_credentials_iron_condor():
    from config import settings
    with patch.object(settings, "ALPACA_PAPER3_API_KEY", "paper3_key"), \
         patch.object(settings, "ALPACA_PAPER3_SECRET_KEY", "paper3_secret"):
        key, secret = settings.get_broker_credentials("iron_condor")
    assert key == "paper3_key"
    assert secret == "paper3_secret"


def test_shared_account_strategies_use_same_credentials():
    """bull_put, bear_call, long_call_vert all use ALPACA_PAPER1_API_KEY."""
    from config import settings
    shared = ["bull_put_spread", "bear_call_spread", "long_call_vertical"]
    for strat in shared:
        key_var, secret_var = settings.STRATEGY_ACCOUNT_MAP[strat]
        assert key_var == "ALPACA_PAPER1_API_KEY", (
            f"{strat} should use ALPACA_PAPER1_API_KEY, got {key_var}"
        )
        assert secret_var == "ALPACA_PAPER1_SECRET_KEY"


def test_get_broker_credentials_unknown_strategy():
    from config import settings
    with pytest.raises(ValueError, match="Unknown strategy"):
        settings.get_broker_credentials("nonexistent_strategy")


def test_get_broker_credentials_missing_env_vars():
    from config import settings
    env = {"ALPACA_PAPER2_API_KEY": "", "ALPACA_PAPER2_SECRET_KEY": ""}
    with patch.dict(os.environ, env, clear=False):
        # Also patch the instance attributes to empty
        with patch.object(settings, "ALPACA_PAPER2_API_KEY", ""), \
             patch.object(settings, "ALPACA_PAPER2_SECRET_KEY", ""):
            with pytest.raises(ValueError, match="not set"):
                settings.get_broker_credentials("wheel")


def test_get_all_broker_credentials_returns_three_distinct_accounts():
    from config import settings
    env = {
        "ALPACA_PAPER1_API_KEY": "paper1_key",
        "ALPACA_PAPER1_SECRET_KEY": "paper1_secret",
        "ALPACA_PAPER2_API_KEY": "paper2_key",
        "ALPACA_PAPER2_SECRET_KEY": "paper2_secret",
        "ALPACA_PAPER3_API_KEY": "paper3_key",
        "ALPACA_PAPER3_SECRET_KEY": "paper3_secret",
    }
    with patch.dict(os.environ, env):
        with patch.object(settings, "ALPACA_PAPER1_API_KEY", "paper1_key"), \
             patch.object(settings, "ALPACA_PAPER1_SECRET_KEY", "paper1_secret"), \
             patch.object(settings, "ALPACA_PAPER2_API_KEY", "paper2_key"), \
             patch.object(settings, "ALPACA_PAPER2_SECRET_KEY", "paper2_secret"), \
             patch.object(settings, "ALPACA_PAPER3_API_KEY", "paper3_key"), \
             patch.object(settings, "ALPACA_PAPER3_SECRET_KEY", "paper3_secret"):
            creds = settings.get_all_broker_credentials()
    assert len(creds) == 3
    keys = [c[0] for c in creds]
    assert "paper1_key" in keys
    assert "paper2_key" in keys
    assert "paper3_key" in keys
