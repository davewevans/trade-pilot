"""Verify the regime stability filter logic.

    python scripts/test_regime_stability.py
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="tp_regime_"))
    history_path = tmp / "regime_history.json"

    print(f"Working directory: {tmp}\n")

    from data.market_regime import RegimeStabilityFilter

    f = RegimeStabilityFilter(history_path=history_path, required_readings=3)

    # Step 1: First BULL reading — not enough to confirm
    changed = f.record_reading("BULL")
    print(f"  1. Reading: BULL -> confirmed={f.get_confirmed_regime()}, changed={changed}")
    assert f.get_confirmed_regime() == "NEUTRAL"  # default
    assert changed is False

    # Step 2: Second BULL reading — still not enough
    changed = f.record_reading("BULL")
    print(f"  2. Reading: BULL -> confirmed={f.get_confirmed_regime()}, changed={changed}")
    assert f.get_confirmed_regime() == "NEUTRAL"
    assert changed is False

    # Step 3: Third BULL reading — now confirmed!
    changed = f.record_reading("BULL")
    print(f"  3. Reading: BULL -> confirmed={f.get_confirmed_regime()}, changed={changed}")
    assert f.get_confirmed_regime() == "BULL"
    assert changed is True
    print(f"     [OK] Regime confirmed as BULL after 3 consistent readings")

    # Step 4: Single BEAR reading — stays BULL
    changed = f.record_reading("BEAR")
    print(f"  4. Reading: BEAR -> confirmed={f.get_confirmed_regime()}, changed={changed}")
    assert f.get_confirmed_regime() == "BULL"
    assert changed is False
    print(f"     [OK] Single contrary reading does not change confirmed regime")

    # Step 5: Second BEAR reading — still BULL (readings are BULL, BEAR, BEAR)
    changed = f.record_reading("BEAR")
    print(f"  5. Reading: BEAR -> confirmed={f.get_confirmed_regime()}, changed={changed}")
    assert f.get_confirmed_regime() == "BULL"
    assert changed is False
    print(f"     [OK] Two BEAR readings not enough (window has mixed readings)")

    # Step 6: Third BEAR reading — now all 3 are BEAR -> confirmed!
    changed = f.record_reading("BEAR")
    print(f"  6. Reading: BEAR -> confirmed={f.get_confirmed_regime()}, changed={changed}")
    assert f.get_confirmed_regime() == "BEAR"
    assert changed is True
    print(f"     [OK] Regime changed to BEAR after 3 consistent readings")

    # Step 7: Verify stability flag
    assert f.is_stable() is True
    print(f"  7. is_stable() = True [OK]")

    # Step 8: Single disruption makes it unstable
    f.record_reading("NEUTRAL")
    assert f.is_stable() is False
    assert f.get_confirmed_regime() == "BEAR"  # still BEAR
    print(f"  8. After 1 NEUTRAL: stable=False, confirmed still BEAR [OK]")

    # Step 9: Verify persistence
    f2 = RegimeStabilityFilter(history_path=history_path, required_readings=3)
    assert f2.get_confirmed_regime() == "BEAR"
    print(f"  9. New instance loaded confirmed=BEAR from disk [OK]")

    print(f"\n{'=' * 50}")
    print(f"  Regime stability filter works correctly [OK]")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
