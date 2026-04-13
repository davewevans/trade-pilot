"""Tests for RegimeStabilityFilter persistence & confirmation logic."""

from data.market_regime import RegimeStabilityFilter


def test_three_identical_readings_confirm(tmp_path):
    f = RegimeStabilityFilter(history_path=tmp_path / "r.json")
    assert f.record_reading("BULL") is False
    assert f.record_reading("BULL") is False
    assert f.record_reading("BULL") is True
    assert f.get_confirmed_regime() == "BULL"


def test_mixed_readings_do_not_confirm(tmp_path):
    f = RegimeStabilityFilter(history_path=tmp_path / "r.json")
    f.record_reading("BULL")
    f.record_reading("BEAR")
    f.record_reading("BULL")
    assert f.get_confirmed_regime() == "NEUTRAL"
    assert not f.is_stable()


def test_state_persists_across_instances(tmp_path):
    path = tmp_path / "r.json"
    f1 = RegimeStabilityFilter(history_path=path)
    f1.record_reading("BULL")
    f1.record_reading("BULL")

    f2 = RegimeStabilityFilter(history_path=path)
    # Third reading via the new instance should confirm
    changed = f2.record_reading("BULL")
    assert changed is True
    assert f2.get_confirmed_regime() == "BULL"


def test_corrupt_state_file_does_not_raise(tmp_path):
    path = tmp_path / "r.json"
    path.write_text("{not valid json", encoding="utf-8")
    f = RegimeStabilityFilter(history_path=path)
    # Fresh default after corrupt load
    assert f.get_confirmed_regime() == "NEUTRAL"


def test_missing_state_file_ok(tmp_path):
    f = RegimeStabilityFilter(history_path=tmp_path / "does_not_exist.json")
    assert f.get_confirmed_regime() == "NEUTRAL"


def test_reset(tmp_path):
    path = tmp_path / "r.json"
    f = RegimeStabilityFilter(history_path=path)
    f.record_reading("BULL")
    f.record_reading("BULL")
    f.record_reading("BULL")
    f.reset()
    assert f.get_confirmed_regime() == "NEUTRAL"
    assert not path.exists()
