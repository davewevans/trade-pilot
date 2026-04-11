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
    with patch.object(settings, "ALPACA_WHEEL_API_KEY", "wheel_key"), \
         patch.object(settings, "ALPACA_WHEEL_SECRET_KEY", "wheel_secret"):
        key, secret = settings.get_broker_credentials("wheel")
    assert key == "wheel_key"
    assert secret == "wheel_secret"


def test_get_broker_credentials_iron_condor():
    from config import settings
    with patch.object(settings, "ALPACA_IRON_CONDOR_API_KEY", "ic_key"), \
         patch.object(settings, "ALPACA_IRON_CONDOR_SECRET_KEY", "ic_secret"):
        key, secret = settings.get_broker_credentials("iron_condor")
    assert key == "ic_key"
    assert secret == "ic_secret"


def test_shared_account_strategies_use_same_credentials():
    """bull_put, bear_call, long_call_vert all use ALPACA_API_KEY."""
    from config import settings
    shared = ["bull_put_spread", "bear_call_spread", "long_call_vertical"]
    for strat in shared:
        key_var, secret_var = settings.STRATEGY_ACCOUNT_MAP[strat]
        assert key_var == "ALPACA_API_KEY", (
            f"{strat} should use ALPACA_API_KEY, got {key_var}"
        )
        assert secret_var == "ALPACA_SECRET_KEY"


def test_get_broker_credentials_unknown_strategy():
    from config import settings
    with pytest.raises(ValueError, match="Unknown strategy"):
        settings.get_broker_credentials("nonexistent_strategy")


def test_get_broker_credentials_missing_env_vars():
    from config import settings
    env = {"ALPACA_WHEEL_API_KEY": "", "ALPACA_WHEEL_SECRET_KEY": ""}
    with patch.dict(os.environ, env, clear=False):
        # Also patch the instance attributes to empty
        with patch.object(settings, "ALPACA_WHEEL_API_KEY", ""), \
             patch.object(settings, "ALPACA_WHEEL_SECRET_KEY", ""):
            with pytest.raises(ValueError, match="not set"):
                settings.get_broker_credentials("wheel")


def test_get_all_broker_credentials_returns_three_distinct_accounts():
    from config import settings
    env = {
        "ALPACA_API_KEY": "default_key",
        "ALPACA_SECRET_KEY": "default_secret",
        "ALPACA_WHEEL_API_KEY": "wheel_key",
        "ALPACA_WHEEL_SECRET_KEY": "wheel_secret",
        "ALPACA_IRON_CONDOR_API_KEY": "ic_key",
        "ALPACA_IRON_CONDOR_SECRET_KEY": "ic_secret",
    }
    with patch.dict(os.environ, env):
        with patch.object(settings, "ALPACA_API_KEY", "default_key"), \
             patch.object(settings, "ALPACA_SECRET_KEY", "default_secret"), \
             patch.object(settings, "ALPACA_WHEEL_API_KEY", "wheel_key"), \
             patch.object(settings, "ALPACA_WHEEL_SECRET_KEY", "wheel_secret"), \
             patch.object(settings, "ALPACA_IRON_CONDOR_API_KEY", "ic_key"), \
             patch.object(settings, "ALPACA_IRON_CONDOR_SECRET_KEY", "ic_secret"):
            creds = settings.get_all_broker_credentials()
    assert len(creds) == 3
    keys = [c[0] for c in creds]
    assert "default_key" in keys
    assert "wheel_key" in keys
    assert "ic_key" in keys
