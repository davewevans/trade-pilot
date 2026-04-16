"""Tests for scripts/bootstrap_sp500.py"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Make the scripts directory importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.bootstrap_sp500 import (
    SANITY_FLOOR,
    _parse_github_csv,
    run,
)

# ── CSV parsing ───────────────────────────────────────────────────────────────

FAKE_CSV = """\
Symbol,Name,Sector
AAPL,Apple Inc,Technology
MSFT,Microsoft Corp,Technology
GOOGL,Alphabet Inc,Technology
brk.b,Berkshire Hathaway,Financials
"""


def test_parse_github_csv_basic():
    symbols = _parse_github_csv(FAKE_CSV)
    assert "AAPL" in symbols
    assert "MSFT" in symbols
    assert "GOOGL" in symbols


def test_parse_github_csv_uppercase():
    symbols = _parse_github_csv(FAKE_CSV)
    # 'brk.b' in CSV should come back as 'BRK.B' (uppercase, not replaced here —
    # that's the GitHub format where tickers are already correct)
    assert all(s == s.upper() for s in symbols)


def test_parse_github_csv_empty():
    symbols = _parse_github_csv("Symbol,Name,Sector\n")
    assert symbols == []


# ── run() — mock HTTP ──────────────────────────────────────────────────────────

def _make_big_csv(n: int = 505) -> str:
    """Generate a fake CSV with n symbols."""
    lines = ["Symbol,Name,Sector"]
    for i in range(n):
        lines.append(f"SYM{i:04d},Company {i},Sector")
    return "\n".join(lines)


@pytest.fixture
def universe_path(tmp_path):
    p = tmp_path / "candidate_universe.json"
    p.write_text(json.dumps({
        "version": 1,
        "updated_at": "2026-01-01",
        "source": "default_etfs",
        "symbols": ["SPY", "QQQ"],
        "manual_adds": ["HOOD"],
        "manual_excludes": ["AMD"],
    }), encoding="utf-8")
    return p


def test_bootstrap_called_with_parsed_symbols(universe_path):
    """Bootstrap receives the symbols fetched from the mocked source."""
    big_csv = _make_big_csv(505)
    with patch("scripts.bootstrap_sp500._fetch_url", return_value=big_csv):
        code = run(dry_run=False, source="github", universe_path=universe_path)
    assert code == 0

    data = json.loads(universe_path.read_text())
    # All SYM#### symbols should be in the saved universe
    assert "SYM0000" in data["symbols"]
    assert "SYM0504" in data["symbols"]


def test_manual_adds_preserved(universe_path):
    """manual_adds carry through bootstrap."""
    big_csv = _make_big_csv(505)
    with patch("scripts.bootstrap_sp500._fetch_url", return_value=big_csv):
        code = run(dry_run=False, source="github", universe_path=universe_path)
    assert code == 0

    data = json.loads(universe_path.read_text())
    assert "HOOD" in data["manual_adds"]


def test_manual_excludes_preserved(universe_path):
    """manual_excludes carry through bootstrap."""
    big_csv = _make_big_csv(505)
    with patch("scripts.bootstrap_sp500._fetch_url", return_value=big_csv):
        code = run(dry_run=False, source="github", universe_path=universe_path)
    assert code == 0

    data = json.loads(universe_path.read_text())
    assert "AMD" in data["manual_excludes"]


def test_sanity_check_triggers_below_450(universe_path):
    """Fewer than 450 symbols → exit code 1 without --force."""
    small_csv = _make_big_csv(100)
    with patch("scripts.bootstrap_sp500._fetch_url", return_value=small_csv):
        code = run(dry_run=False, source="github", force=False, universe_path=universe_path)
    assert code == 1
    # File should NOT have been overwritten
    data = json.loads(universe_path.read_text())
    assert set(data["symbols"]) == {"QQQ", "SPY"}  # original content preserved


def test_sanity_check_force_overrides(universe_path):
    """--force lets a small list through."""
    small_csv = _make_big_csv(100)
    with patch("scripts.bootstrap_sp500._fetch_url", return_value=small_csv):
        code = run(dry_run=False, source="github", force=True, universe_path=universe_path)
    assert code == 0


def test_dry_run_does_not_write(universe_path):
    """--dry-run reports but leaves the file untouched."""
    original_text = universe_path.read_text()
    big_csv = _make_big_csv(505)
    with patch("scripts.bootstrap_sp500._fetch_url", return_value=big_csv):
        code = run(dry_run=True, source="github", universe_path=universe_path)
    assert code == 0
    assert universe_path.read_text() == original_text


def test_both_sources_fail_returns_nonzero(universe_path):
    """If all sources fail, return exit code 1."""
    with patch("scripts.bootstrap_sp500._fetch_url", side_effect=OSError("network down")):
        code = run(dry_run=False, source="auto", universe_path=universe_path)
    assert code == 1
