"""Tests for CandidateUniverse."""

import json
import pytest

from research.candidates.universe import CandidateUniverse, DEFAULT_ETFS


def _make(tmp_path, data: dict) -> CandidateUniverse:
    path = tmp_path / "universe.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return CandidateUniverse(path=path)


# ── Missing file ──────────────────────────────────────────────────────────────

def test_missing_file_returns_empty_without_raising(tmp_path):
    u = CandidateUniverse(path=tmp_path / "nonexistent.json")
    data = u.load()
    assert data.symbols == []
    assert data.manual_adds == []
    assert data.manual_excludes == []


# ── bootstrap ─────────────────────────────────────────────────────────────────

def test_bootstrap_creates_base_plus_etfs(tmp_path):
    u = CandidateUniverse(path=tmp_path / "u.json")
    u.load()
    u.bootstrap(["AAPL", "MSFT"], updated_at="2026-01-01", include_default_etfs=True)
    symbols = u.all_symbols()
    assert "AAPL" in symbols
    assert "MSFT" in symbols
    for etf in DEFAULT_ETFS:
        assert etf in symbols


def test_bootstrap_without_etfs(tmp_path):
    u = CandidateUniverse(path=tmp_path / "u.json")
    u.load()
    u.bootstrap(["AAPL"], updated_at="2026-01-01", include_default_etfs=False)
    symbols = u.all_symbols()
    assert "AAPL" in symbols
    assert "SPY" not in symbols


def test_bootstrap_preserves_manual_adds_and_excludes(tmp_path):
    u = CandidateUniverse(path=tmp_path / "u.json")
    u.load()
    u.add_manual("HOOD")
    u.exclude_manual("DIA")
    # First bootstrap
    u.bootstrap(["AAPL"], updated_at="2026-01-01", include_default_etfs=False)
    assert "HOOD" in u.all_symbols()
    assert "DIA" not in u.all_symbols()
    # Second bootstrap — preserves manual lists
    u.bootstrap(["MSFT", "GOOGL"], updated_at="2026-02-01", include_default_etfs=False)
    assert "HOOD" in u.all_symbols()
    assert "DIA" not in u.all_symbols()
    # Base symbols are replaced
    assert "AAPL" not in u.all_symbols()
    assert "MSFT" in u.all_symbols()


# ── all_symbols ───────────────────────────────────────────────────────────────

def test_all_symbols_merges_deduplicates_sorts(tmp_path):
    u = _make(tmp_path, {
        "version": 1, "updated_at": "", "source": "",
        "symbols": ["MSFT", "AAPL", "AAPL"],
        "manual_adds": ["HOOD", "AAPL"],
        "manual_excludes": [],
    })
    u.load()
    result = u.all_symbols()
    assert result == sorted(set(result))
    assert result.count("AAPL") == 1


def test_manual_excludes_overrides_manual_adds(tmp_path):
    """Symbol in both manual_adds and manual_excludes → excluded."""
    u = _make(tmp_path, {
        "version": 1, "updated_at": "", "source": "",
        "symbols": [],
        "manual_adds": ["HOOD"],
        "manual_excludes": ["HOOD"],
    })
    u.load()
    assert "HOOD" not in u.all_symbols()


def test_manual_excludes_removes_base_symbol(tmp_path):
    u = _make(tmp_path, {
        "version": 1, "updated_at": "", "source": "",
        "symbols": ["AAPL", "MSFT"],
        "manual_adds": [],
        "manual_excludes": ["AAPL"],
    })
    u.load()
    assert "AAPL" not in u.all_symbols()
    assert "MSFT" in u.all_symbols()


# ── save/load round-trip ──────────────────────────────────────────────────────

def test_save_load_round_trip(tmp_path):
    path = tmp_path / "u.json"
    u = CandidateUniverse(path=path)
    u.load()
    u.bootstrap(["AAPL", "MSFT"], updated_at="2026-03-01", include_default_etfs=False)
    u.add_manual("HOOD")
    u.exclude_manual("AAPL")
    u.save()

    u2 = CandidateUniverse(path=path)
    u2.load()
    assert "MSFT" in u2.all_symbols()
    assert "HOOD" in u2.all_symbols()
    assert "AAPL" not in u2.all_symbols()


# ── case normalisation ────────────────────────────────────────────────────────

def test_case_normalisation_on_all_inputs(tmp_path):
    u = CandidateUniverse(path=tmp_path / "u.json")
    u.load()
    u.bootstrap(["aapl", "msft"], updated_at="2026-01-01", include_default_etfs=False)
    u.add_manual("hood")
    u.exclude_manual("msft")
    symbols = u.all_symbols()
    assert "AAPL" in symbols
    assert "HOOD" in symbols
    assert "MSFT" not in symbols


def test_case_normalisation_on_file_load(tmp_path):
    u = _make(tmp_path, {
        "version": 1, "updated_at": "", "source": "",
        "symbols": ["aapl", "Msft"],
        "manual_adds": ["hood"],
        "manual_excludes": [],
    })
    u.load()
    assert "AAPL" in u.all_symbols()
    assert "MSFT" in u.all_symbols()
    assert "HOOD" in u.all_symbols()


# ── count / contains ──────────────────────────────────────────────────────────

def test_count_matches_len_all_symbols(tmp_path):
    u = _make(tmp_path, {
        "version": 1, "updated_at": "", "source": "",
        "symbols": ["AAPL", "MSFT", "GOOGL"],
        "manual_adds": ["HOOD"],
        "manual_excludes": ["MSFT"],
    })
    u.load()
    assert u.count() == len(u.all_symbols())


def test_contains_case_insensitive(tmp_path):
    u = _make(tmp_path, {
        "version": 1, "updated_at": "", "source": "",
        "symbols": ["AAPL"],
        "manual_adds": [],
        "manual_excludes": [],
    })
    u.load()
    assert u.contains("aapl")
    assert u.contains("AAPL")
    assert not u.contains("MSFT")


# ── remove_manual helpers ─────────────────────────────────────────────────────

def test_remove_manual_add_is_idempotent(tmp_path):
    u = CandidateUniverse(path=tmp_path / "u.json")
    u.load()
    u.add_manual("HOOD")
    u.remove_manual_add("HOOD")
    u.remove_manual_add("HOOD")  # second call should not raise
    assert "HOOD" not in u._data.manual_adds


def test_remove_manual_exclude_is_idempotent(tmp_path):
    u = CandidateUniverse(path=tmp_path / "u.json")
    u.load()
    u.exclude_manual("AAPL")
    u.remove_manual_exclude("AAPL")
    u.remove_manual_exclude("AAPL")
    assert "AAPL" not in u._data.manual_excludes
