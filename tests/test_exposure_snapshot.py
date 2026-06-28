"""Tests for data/exposure_snapshot.py and jobs/publish_exposure.py.

The builder is read-only aggregation: it must assemble per-account positions
(with derived root + sector) plus the book-level exposure view, swallow missing
per-account snapshots, and never raise. The job is data exhaust: flag-gated, and
it must never crash the scheduler. These tests mock the publisher entirely — no
real GitHub call is ever made.

Covered:
- build_exposure_snapshot: assembles accounts + sectors + book_exposure; missing
  per-account file -> empty account; unknown symbol -> "unknown" sector; equity
  (non-OCC) root falls back to the plain ticker; never raises when account
  enumeration or the book view fails.
- publish_exposure.run(): flag-off short-circuit (no build, no publish); flag-on
  publishes both exposure/latest.json and a dated path; never raises on failure.
"""

from __future__ import annotations

import json
import re
from unittest.mock import patch

import pytest

import data.exposure_snapshot as es
import jobs.publish_exposure as pe
from config import settings

DATED_RE = re.compile(r"^exposure/\d{4}-\d{2}-\d{2}-\d{4}\.json$")

_BOOK = {
    "computed_at": "2026-06-28T12:00:00+00:00",
    "by_underlying": {"AAPL": {"positions": [], "families": {}}},
    "sources": {"wheel_state": None, "turnover_wheel_state": None, "open_spreads": None},
}


def _write_snapshot(snapshots_dir, account_id: str, positions: list[dict],
                    timestamp: str = "2026-06-28T10:00:00") -> None:
    payload = {
        "timestamp": timestamp,
        "account_name": account_id,
        "account": {},
        "positions": positions,
    }
    (snapshots_dir / f"portfolio_{account_id}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


@pytest.fixture
def snapshots_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SNAPSHOTS_DIR", tmp_path, raising=False)
    return tmp_path


# ── build_exposure_snapshot ────────────────────────────────────────────────


def test_build_assembles_accounts_sectors_and_book(snapshots_dir):
    _write_snapshot(snapshots_dir, "paper_1", [
        # OCC option, underlying field present -> Technology
        {"symbol": "AAPL260515P00260000", "underlying": "AAPL", "quantity": -1, "side": "short"},
        # OCC option, underlying empty -> root parsed from OCC -> Energy
        {"symbol": "XOM260515C00120000", "underlying": "", "quantity": -2, "side": "short"},
    ])

    active = {
        "paper_1": {"strategy": "adaptive_spreads", "label": "Paper Account 1", "status": "active"},
        "paper_2": {"strategy": "wheel", "label": "Paper Account 2", "status": "active"},
    }

    with patch("data.account_manager.AccountManager") as MockAM, \
         patch("data.book_exposure.compute_cross_account_book_exposure", return_value=_BOOK):
        MockAM.return_value.get_active_accounts.return_value = active
        snap = es.build_exposure_snapshot()

    assert "computed_at" in snap
    assert snap["book_exposure"] == _BOOK
    assert snap["sources"]["book_exposure"] == _BOOK["sources"]

    p1 = snap["accounts"]["paper_1"]
    assert p1["strategy"] == "adaptive_spreads"
    assert p1["snapshot_timestamp"] == "2026-06-28T10:00:00"
    by_root = {pos["root"]: pos for pos in p1["positions"]}
    assert by_root["AAPL"]["sector"] == "Technology"
    assert by_root["AAPL"]["side"] == "short"
    assert by_root["AAPL"]["qty"] == -1
    assert by_root["XOM"]["sector"] == "Energy"  # root parsed from OCC, underlying was empty

    # paper_2 has no snapshot file -> empty, no raise.
    p2 = snap["accounts"]["paper_2"]
    assert p2["positions"] == []
    assert p2["snapshot_timestamp"] is None
    assert snap["sources"]["account_snapshots"]["paper_2"] is None


def test_missing_snapshot_file_yields_empty_account_no_raise(snapshots_dir):
    active = {"paper_3": {"strategy": "iron_condor", "label": "Paper Account 3", "status": "active"}}

    with patch("data.account_manager.AccountManager") as MockAM, \
         patch("data.book_exposure.compute_cross_account_book_exposure", return_value=_BOOK):
        MockAM.return_value.get_active_accounts.return_value = active
        snap = es.build_exposure_snapshot()

    assert snap["accounts"]["paper_3"]["positions"] == []
    assert snap["accounts"]["paper_3"]["snapshot_timestamp"] is None


def test_unknown_symbol_sector_is_unknown(snapshots_dir):
    _write_snapshot(snapshots_dir, "paper_1", [
        {"symbol": "ZZZZ260515P00050000", "underlying": "ZZZZ", "quantity": -1, "side": "short"},
    ])
    active = {"paper_1": {"strategy": "adaptive_spreads", "label": "P1", "status": "active"}}

    with patch("data.account_manager.AccountManager") as MockAM, \
         patch("data.book_exposure.compute_cross_account_book_exposure", return_value=_BOOK):
        MockAM.return_value.get_active_accounts.return_value = active
        snap = es.build_exposure_snapshot()

    pos = snap["accounts"]["paper_1"]["positions"][0]
    assert pos["root"] == "ZZZZ"
    assert pos["sector"] == "unknown"


def test_equity_position_root_from_plain_symbol(snapshots_dir):
    # Equity row: plain ticker, not an OCC symbol, no underlying field.
    _write_snapshot(snapshots_dir, "paper_2", [
        {"symbol": "AAPL", "underlying": "", "quantity": 100, "side": "long"},
    ])
    active = {"paper_2": {"strategy": "wheel", "label": "P2", "status": "active"}}

    with patch("data.account_manager.AccountManager") as MockAM, \
         patch("data.book_exposure.compute_cross_account_book_exposure", return_value=_BOOK):
        MockAM.return_value.get_active_accounts.return_value = active
        snap = es.build_exposure_snapshot()

    pos = snap["accounts"]["paper_2"]["positions"][0]
    assert pos["root"] == "AAPL"
    assert pos["sector"] == "Technology"


def test_build_never_raises_on_account_manager_failure(snapshots_dir):
    with patch("data.account_manager.AccountManager", side_effect=RuntimeError("boom")), \
         patch("data.book_exposure.compute_cross_account_book_exposure", return_value=_BOOK):
        snap = es.build_exposure_snapshot()  # must not raise

    assert snap["accounts"] == {}
    assert snap["book_exposure"] == _BOOK


def test_build_never_raises_on_book_failure(snapshots_dir):
    active = {"paper_1": {"strategy": "adaptive_spreads", "label": "P1", "status": "active"}}

    with patch("data.account_manager.AccountManager") as MockAM, \
         patch("data.book_exposure.compute_cross_account_book_exposure",
               side_effect=RuntimeError("state gone")):
        MockAM.return_value.get_active_accounts.return_value = active
        snap = es.build_exposure_snapshot()  # must not raise

    assert snap["book_exposure"] == {}
    assert snap["sources"]["book_exposure"] is None
    assert "paper_1" in snap["accounts"]


# ── publish_exposure.run() ─────────────────────────────────────────────────


def test_run_flag_off_short_circuits_without_building(monkeypatch):
    monkeypatch.setattr(settings, "HERMES_PUBLISH_ENABLED", False, raising=False)

    with patch("data.exposure_snapshot.build_exposure_snapshot") as mock_build, \
         patch("data.report_publisher.publish_report") as mock_pub:
        pe.run()

    mock_build.assert_not_called()
    mock_pub.assert_not_called()


def test_run_flag_on_publishes_latest_and_dated(monkeypatch):
    monkeypatch.setattr(settings, "HERMES_PUBLISH_ENABLED", True, raising=False)

    with patch("data.exposure_snapshot.build_exposure_snapshot", return_value={"computed_at": "x"}), \
         patch("data.report_publisher.publish_report", return_value=True) as mock_pub:
        pe.run()

    assert mock_pub.call_count == 2
    published_paths = [call.args[0] for call in mock_pub.call_args_list]
    assert "exposure/latest.json" in published_paths
    dated = [p for p in published_paths if DATED_RE.match(p)]
    assert len(dated) == 1
    # Both publishes carry the same JSON-serialized content.
    for call in mock_pub.call_args_list:
        assert isinstance(call.args[1], str)
        json.loads(call.args[1])  # valid JSON


def test_run_never_raises_when_publish_raises(monkeypatch):
    monkeypatch.setattr(settings, "HERMES_PUBLISH_ENABLED", True, raising=False)

    with patch("data.exposure_snapshot.build_exposure_snapshot", return_value={"computed_at": "x"}), \
         patch("data.report_publisher.publish_report", side_effect=RuntimeError("boom")):
        pe.run()  # must not raise


def test_run_never_raises_when_build_raises(monkeypatch):
    monkeypatch.setattr(settings, "HERMES_PUBLISH_ENABLED", True, raising=False)

    with patch("data.exposure_snapshot.build_exposure_snapshot", side_effect=RuntimeError("db gone")), \
         patch("data.report_publisher.publish_report") as mock_pub:
        pe.run()  # must not raise

    mock_pub.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
