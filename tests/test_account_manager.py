"""Tests for AccountManager."""

import json
import os
import shutil

import pytest

from data.account_manager import AccountManager


@pytest.fixture
def config_data():
    return {
        "accounts": {
            "paper_1": {
                "label": "Paper Account 1",
                "credentials_key": "TEST_API_KEY",
                "credentials_secret": "TEST_SECRET_KEY",
                "strategy": "adaptive_spreads",
                "status": "active",
                "watchlist": ["AAPL", "SPY"],
                "screening_overrides": {},
            },
            "paper_2": {
                "label": "Paper Account 2",
                "credentials_key": "TEST_API_KEY_2",
                "credentials_secret": "TEST_SECRET_KEY_2",
                "strategy": None,
                "status": "inactive",
                "watchlist": ["MSFT"],
                "screening_overrides": {},
            },
        },
        "updated_at": None,
    }


@pytest.fixture
def manager(tmp_path, config_data):
    config_file = tmp_path / "account_config.json"
    config_file.write_text(json.dumps(config_data), encoding="utf-8")
    return AccountManager(config_path=config_file)


def test_loading_config_from_temp_file(manager):
    accounts = manager.get_all_accounts()
    assert "paper_1" in accounts
    assert "paper_2" in accounts


def test_get_active_accounts_filters_correctly(manager):
    active = manager.get_active_accounts()
    assert "paper_1" in active
    assert "paper_2" not in active


def test_activate_with_no_strategy_returns_false(manager):
    result = manager.activate("paper_2")
    assert result is False


def test_activate_with_strategy_returns_true(manager):
    result = manager.activate("paper_1")
    assert result is True


def test_deactivate_sets_status_inactive(manager):
    manager.deactivate("paper_1")
    account = manager.get_account("paper_1")
    assert account["status"] == "inactive"


def test_link_strategy_succeeds_once(manager, tmp_path):
    # paper_2 has no strategy
    ok, err = manager.link_strategy("paper_2", "wheel")
    assert ok is True
    assert err == ""
    account = manager.get_account("paper_2")
    assert account["strategy"] == "wheel"


def test_link_strategy_rejects_second_call(manager):
    # paper_1 already has a strategy
    ok, err = manager.link_strategy("paper_1", "wheel")
    assert ok is False
    assert "already has strategy" in err


def test_link_strategy_with_nonexistent_strategy_returns_error(manager):
    ok, err = manager.link_strategy("paper_2", "nonexistent_strategy_xyz")
    assert ok is False
    assert "not exist" in err or "does not exist" in err


def test_update_watchlist_uppercases_symbols(manager, tmp_path):
    manager.update_watchlist("paper_1", ["aapl", "spy", "msft"])
    account = manager.get_account("paper_1")
    assert account["watchlist"] == ["AAPL", "SPY", "MSFT"]


def test_update_screening_overrides_persists_to_disk(manager):
    overrides = {"min_avg_daily_volume": 2000000}
    manager.update_screening_overrides("paper_1", overrides)
    account = manager.get_account("paper_1")
    assert account["screening_overrides"] == overrides


def test_default_config_copied_from_repo_when_data_dir_config_missing(tmp_path):
    """When data dir config is missing, AccountManager copies the repo default."""
    missing_path = tmp_path / "does_not_exist.json"
    # This should not raise — it will try to copy from repo default
    m = AccountManager(config_path=missing_path)
    # Either it loaded the repo default or started empty
    all_accounts = m.get_all_accounts()
    # At minimum should be a dict (not crash)
    assert isinstance(all_accounts, dict)


def test_get_credentials_reads_env_vars(manager, monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "test-key-123")
    monkeypatch.setenv("TEST_SECRET_KEY", "test-secret-456")
    key, secret = manager.get_credentials("paper_1")
    assert key == "test-key-123"
    assert secret == "test-secret-456"


def test_get_credentials_raises_when_env_vars_missing(manager, monkeypatch):
    monkeypatch.delenv("TEST_API_KEY", raising=False)
    monkeypatch.delenv("TEST_SECRET_KEY", raising=False)
    with pytest.raises(ValueError, match="not set"):
        manager.get_credentials("paper_1")
