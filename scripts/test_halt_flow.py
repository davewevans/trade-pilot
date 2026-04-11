"""Verify the circuit breaker halt flow end-to-end.

    python scripts/test_halt_flow.py
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="tp_halt_"))
    data_dir = tmp / "data"
    snap_dir = data_dir / "snapshots"
    lock_path = data_dir / "HALTED.lock"

    print(f"Working directory: {tmp}\n")

    from strategies.circuit_breaker import CircuitBreaker

    cb = CircuitBreaker(
        data_dir=data_dir,
        snapshots_dir=snap_dir,
        daily_loss_halt_pct=3.0,
        daily_loss_reduce_pct=1.5,
        weekly_loss_halt_pct=5.0,
        drawdown_halt_pct=10.0,
        drawdown_lock_pct=15.0,
    )

    # Step 1: Set peak equity
    status = cb.update(100_000)
    print(f"  1. Initial equity: $100,000 -> status={status.status}")
    assert status.status == "GREEN"
    assert not lock_path.exists()

    # Step 2: Drop 15% to trigger DRAWDOWN_LOCK
    status = cb.update(85_000)
    print(f"  2. Equity dropped to $85,000 -> status={status.status}")
    print(f"     Active rules: {status.active_rules}")
    assert "drawdown_lock" in status.active_rules
    assert lock_path.exists(), "HALTED.lock should exist"
    print(f"     HALTED.lock written: {lock_path}")

    # Step 3: Verify is_halted()
    assert cb.is_halted() is True
    print(f"  3. is_halted() = True [OK]")

    # Step 4: Verify multiplier is 0
    assert cb.get_position_size_multiplier() == 0.0
    print(f"  4. position_size_multiplier = 0.0 [OK]")

    # Step 5: Equity recovers — still halted (lock persists)
    status = cb.update(105_000)
    assert cb.is_halted() is True
    print(f"  5. Equity recovered to $105,000 — still halted [OK]")

    # Step 6: Delete lock file manually
    lock_path.unlink()
    assert cb.is_halted() is False
    print(f"  6. Lock file deleted — is_halted() = False [OK]")

    # Step 7: Multiplier is back to normal
    status = cb.update(105_000)
    assert cb.get_position_size_multiplier() == 1.0
    print(f"  7. position_size_multiplier = 1.0 [OK]")

    print(f"\n{'=' * 50}")
    print(f"  Halt flow works correctly [OK]")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
