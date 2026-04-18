"""Tests for wheel state migration from source-relative to SNAPSHOTS_DIR (Phase 11).

Covers:
1. Old state file is copied to new persistent path on first load
2. Old file is deleted after successful copy
3. If both old and new exist, new wins (old is not copied, new is not clobbered)
4. If neither exists, normal init path runs (no error, starts fresh)
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Fixture: isolate STATE_FILE paths so tests don't touch real disk locations
# ---------------------------------------------------------------------------

@pytest.fixture()
def wheel_paths(tmp_path):
    """Return (old_path, new_path) pointing to tmp_path so no real files are touched."""
    old = tmp_path / "strategies" / ".." / "data" / "wheel_state.json"
    old = (tmp_path / "old_wheel_state.json")
    new = tmp_path / "snapshots" / "wheel_state.json"
    return old, new


def _sample_state():
    return {
        "symbol": "AAPL",
        "state": "SHORT_PUT",
        "open_position": None,
        "cost_basis": 150.0,
        "total_premium_collected": 2.5,
        "roll_count": 0,
        "updated_at": "2026-01-01T00:00:00",
    }


def _make_wheel(old_path: Path, new_path: Path):
    """Instantiate WheelStrategy with patched STATE_FILE and _LEGACY_STATE_FILE."""
    import strategies.wheel_strategy as mod

    mock_broker = MagicMock()
    with (
        patch.object(mod, "STATE_FILE", str(new_path)),
        patch.object(mod, "_LEGACY_STATE_FILE", old_path),
        patch("strategies.wheel_strategy.market_data"),
    ):
        instance = mod.WheelStrategy.__new__(mod.WheelStrategy)
        instance.symbol = None
        instance.state = mod.WheelState.IDLE
        instance.open_position = None
        instance.cost_basis = None
        instance.total_premium_collected = 0.0
        instance.roll_count = 0
        instance._load_state()
    return instance


# ---------------------------------------------------------------------------
# Test 1 + 2: migration copies old file to new path and deletes old
# ---------------------------------------------------------------------------

def test_migration_copies_and_deletes_old(tmp_path):
    """Old state file is copied to new path; old file is deleted afterward."""
    old = tmp_path / "old_wheel_state.json"
    new = tmp_path / "snapshots" / "wheel_state.json"

    state = _sample_state()
    old.write_text(json.dumps(state))

    wheel = _make_wheel(old, new)

    assert new.exists(), "New path should have been created by migration"
    assert not old.exists(), "Old path should have been deleted after migration"
    assert wheel.symbol == "AAPL"
    assert wheel.state.value == "SHORT_PUT"


# ---------------------------------------------------------------------------
# Test 3: both old and new exist — new wins, old is not copied
# ---------------------------------------------------------------------------

def test_migration_new_wins_when_both_exist(tmp_path):
    """If both old and new state files exist, new is kept and old is not copied."""
    old = tmp_path / "old_wheel_state.json"
    new = tmp_path / "snapshots" / "wheel_state.json"
    new.parent.mkdir(parents=True, exist_ok=True)

    old_state = {**_sample_state(), "symbol": "OLD_SYMBOL"}
    new_state = {**_sample_state(), "symbol": "NEW_SYMBOL"}

    old.write_text(json.dumps(old_state))
    new.write_text(json.dumps(new_state))

    wheel = _make_wheel(old, new)

    # New path takes precedence — symbol should be from new_state
    assert wheel.symbol == "NEW_SYMBOL", "New state should not be overwritten by old"
    assert old.exists(), "Old file should be left untouched when new already exists"


# ---------------------------------------------------------------------------
# Test 4: neither old nor new exists — starts fresh, no error
# ---------------------------------------------------------------------------

def test_migration_neither_exists_starts_fresh(tmp_path):
    """If no state files exist, WheelStrategy initialises fresh without error."""
    old = tmp_path / "nonexistent_old.json"
    new = tmp_path / "snapshots" / "nonexistent_new.json"

    wheel = _make_wheel(old, new)

    assert wheel.symbol is None
    assert wheel.state.value == "IDLE"
    assert not new.exists(), "No file should be created if there was nothing to migrate"
