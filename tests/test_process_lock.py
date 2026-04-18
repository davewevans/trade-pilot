"""Tests for utils.process_lock — process singleton lock."""

import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from utils.process_lock import ProcessLock, ProcessLockHeld


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _lock(name: str, lock_dir: Path) -> ProcessLock:
    return ProcessLock(name, lock_dir)


# ---------------------------------------------------------------------------
# case 1: acquire succeeds when no lock exists
# ---------------------------------------------------------------------------


def test_acquire_succeeds_when_no_lock(tmp_path):
    lock = _lock("test-job", tmp_path)
    lock.acquire()
    assert lock._acquired
    assert (tmp_path / "test-job.lock").exists()
    lock.release()


# ---------------------------------------------------------------------------
# case 2: acquire raises ProcessLockHeld when lock contains a live Python PID
# ---------------------------------------------------------------------------


def test_acquire_raises_when_live_python_holds_lock(tmp_path):
    # Spawn a real, long-lived Python process so we have a live PID.
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        # Write that PID into the lock file manually.
        lock_path = tmp_path / "test-job.lock"
        lock_path.write_text(str(proc.pid), encoding="utf-8")

        lock = _lock("test-job", tmp_path)
        with pytest.raises(ProcessLockHeld) as exc_info:
            lock.acquire()

        assert exc_info.value.holder_pid == proc.pid
    finally:
        proc.kill()
        proc.wait()


# ---------------------------------------------------------------------------
# case 3: acquire succeeds when lock file contains a non-existent PID (stale)
# ---------------------------------------------------------------------------


def test_acquire_succeeds_on_stale_nonexistent_pid(tmp_path):
    # Write a PID that is virtually guaranteed not to exist.
    lock_path = tmp_path / "test-job.lock"
    lock_path.write_text("9999999", encoding="utf-8")

    lock = _lock("test-job", tmp_path)
    lock.acquire()  # should not raise
    assert lock._acquired
    lock.release()


# ---------------------------------------------------------------------------
# case 4: acquire succeeds when lock contains a PID of a live non-Python process
# ---------------------------------------------------------------------------


def test_acquire_succeeds_on_live_non_python_pid(tmp_path):
    lock_path = tmp_path / "test-job.lock"
    lock_path.write_text("12345", encoding="utf-8")

    # Patch psutil so that PID 12345 exists but has a non-Python process name.
    mock_proc = MagicMock()
    mock_proc.name.return_value = "svchost.exe"

    with patch("psutil.pid_exists", return_value=True), patch(
        "psutil.Process", return_value=mock_proc
    ):
        lock = _lock("test-job", tmp_path)
        lock.acquire()  # should not raise — treated as stale
        assert lock._acquired
        lock.release()


# ---------------------------------------------------------------------------
# case 5: release deletes the lock file
# ---------------------------------------------------------------------------


def test_release_deletes_lock_file(tmp_path):
    lock = _lock("test-job", tmp_path)
    lock.acquire()
    lock_path = tmp_path / "test-job.lock"
    assert lock_path.exists()
    lock.release()
    assert not lock_path.exists()
    assert not lock._acquired


# ---------------------------------------------------------------------------
# case 6: context manager releases on normal exit
# ---------------------------------------------------------------------------


def test_context_manager_releases_on_normal_exit(tmp_path):
    lock_path = tmp_path / "test-job.lock"
    with ProcessLock("test-job", tmp_path):
        assert lock_path.exists()
    assert not lock_path.exists()


# ---------------------------------------------------------------------------
# case 7: context manager releases on exception propagation
# ---------------------------------------------------------------------------


def test_context_manager_releases_on_exception(tmp_path):
    lock_path = tmp_path / "test-job.lock"
    with pytest.raises(ValueError):
        with ProcessLock("test-job", tmp_path):
            assert lock_path.exists()
            raise ValueError("deliberate error")
    assert not lock_path.exists()


# ---------------------------------------------------------------------------
# case 8: concurrent acquire from threads — only one wins
# ---------------------------------------------------------------------------


def test_concurrent_acquire_one_wins(tmp_path):
    acquired: list[str] = []
    blocked: list[str] = []
    barrier = threading.Barrier(2)

    def try_acquire(thread_name: str) -> None:
        lock = _lock("concurrent-job", tmp_path)
        barrier.wait()  # both threads start simultaneously
        try:
            lock.acquire()
            acquired.append(thread_name)
            time.sleep(0.05)
            lock.release()
        except ProcessLockHeld:
            blocked.append(thread_name)

    t1 = threading.Thread(target=try_acquire, args=("t1",))
    t2 = threading.Thread(target=try_acquire, args=("t2",))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert len(acquired) == 1, f"Expected 1 winner, got {acquired}"
    assert len(blocked) == 1, f"Expected 1 blocked, got {blocked}"
