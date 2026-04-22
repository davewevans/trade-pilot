"""Process singleton lock using PID lock files.

Prevents multiple instances of the same job from running concurrently,
regardless of how they're launched (manual terminal, Claude Code task,
Render shell, scheduler bug).

Usage::

    from utils.process_lock import ProcessLock, ProcessLockHeld

    try:
        with ProcessLock("job-weekly_research", settings.DATA_DIR / "locks"):
            run_the_job()
    except ProcessLockHeld as e:
        sys.exit(2)
"""

import atexit
import logging
import os
import signal
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Serializes the check-then-write acquire sequence within a single process so
# that concurrent threads (test suite, thread pools) can't both slip through
# the file-existence check at the same instant.
_ACQUIRE_LOCK = threading.Lock()


class ProcessLockHeld(Exception):
    """Raised when another live Python process holds the requested lock."""

    def __init__(self, message: str, holder_pid: int) -> None:
        super().__init__(message)
        self.holder_pid = holder_pid


class ProcessLock:
    """File-based process singleton lock.

    Acquires an exclusive lock by writing this process's PID to a lock file.
    Stale locks (PID doesn't exist or belongs to a non-Python process) are
    automatically taken over.

    Works on Windows (Git Bash) and Linux (Render).
    """

    def __init__(self, name: str, lock_dir: Path) -> None:
        self._name = name
        self._lock_dir = Path(lock_dir)
        self._lock_path = self._lock_dir / f"{name}.lock"
        self._acquired = False
        self._pid = os.getpid()
        self._prev_sigterm: Optional[object] = None
        self._prev_sigint: Optional[object] = None

    def acquire(self) -> None:
        """Acquire the lock.

        Raises:
            ProcessLockHeld: if a live Python process already holds the lock.
        """
        self._lock_dir.mkdir(parents=True, exist_ok=True)

        with _ACQUIRE_LOCK:
            if self._lock_path.exists():
                existing_pid, existing_create_time = self._read_lock()
                if existing_pid is not None and self._is_live_python(existing_pid, existing_create_time):
                    raise ProcessLockHeld(
                        f"Lock '{self._name}' is held by PID {existing_pid} "
                        f"(lock file: {self._lock_path})",
                        holder_pid=existing_pid,
                    )
                # Stale lock — take it over
                logger.info(
                    "Stale lock detected for '%s' (PID %s) — taking over",
                    self._name,
                    existing_pid,
                )

            self._write_lock()
            self._acquired = True

        atexit.register(self._atexit_release)
        self._install_signal_handlers()
        logger.info(
            "Process lock acquired: %s (PID %d)", self._lock_path, self._pid
        )

    def release(self) -> None:
        """Release the lock if this process owns it."""
        if not self._acquired:
            return
        try:
            if self._lock_path.exists():
                current_pid = self._read_pid()
                if current_pid == self._pid:
                    self._lock_path.unlink()
                    logger.info("Process lock released: %s", self._lock_path)
        except Exception:
            logger.warning(
                "Error releasing process lock: %s",
                self._lock_path,
                exc_info=True,
            )
        finally:
            self._acquired = False
            self._restore_signal_handlers()

    def __enter__(self) -> "ProcessLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()

    # ── internals ────────────────────────────────────────────────────────

    def _write_lock(self) -> None:
        """Atomically write PID and process creation time to the lock file.

        Writes to a sibling temp file then uses os.replace() so the
        final path is either absent or contains a complete, valid entry —
        never a partial write.

        Format: "{pid}:{create_time}" where create_time is a float from
        psutil (seconds since epoch). Falls back to "{pid}" if psutil is
        unavailable.
        """
        try:
            import psutil

            create_time = psutil.Process(self._pid).create_time()
            content = f"{self._pid}:{create_time}"
        except Exception:
            content = str(self._pid)

        tmp_path = self._lock_path.parent / (self._lock_path.name + ".tmp")
        tmp_path.write_text(content, encoding="utf-8")
        os.replace(str(tmp_path), str(self._lock_path))

    def _read_lock(self) -> tuple[Optional[int], Optional[float]]:
        """Read PID and optional create_time from lock file.

        Returns (None, None) on any read/parse failure.
        Returns (pid, None) for old-format lock files that lack a create_time.
        """
        try:
            content = self._lock_path.read_text(encoding="utf-8").strip()
            if ":" in content:
                pid_str, ct_str = content.split(":", 1)
                return int(pid_str), float(ct_str)
            return int(content), None
        except (OSError, ValueError):
            return None, None

    def _read_pid(self) -> Optional[int]:
        """Read PID from lock file; returns None on failure."""
        pid, _ = self._read_lock()
        return pid

    def _is_live_python(self, pid: int, expected_create_time: Optional[float] = None) -> bool:
        """Return True if *pid* is the same live Python process that wrote the lock.

        Checks the process creation time when available — this prevents a
        reused PID (e.g. a uvicorn worker assigned the same PID after a
        container restart) from being mistaken for the original scheduler.

        Returns False (treat as stale) if psutil is unavailable, the PID
        doesn't exist, the creation time doesn't match, or any check raises
        an unexpected exception.
        """
        try:
            import psutil  # optional dependency

            if not psutil.pid_exists(pid):
                return False
            proc = psutil.Process(pid)
            if "python" not in proc.name().lower():
                return False
            if expected_create_time is not None:
                # Allow 1 s of float imprecision; a reused PID will differ by
                # at least several seconds.
                return abs(proc.create_time() - expected_create_time) < 1.0
            # Old-format lock (no create_time stored) — treat as stale so that
            # a container restart always wins over a potentially dead process.
            logger.info(
                "Lock '%s' has no creation-time stamp — treating as stale",
                self._name,
            )
            return False
        except Exception:
            return False

    def _atexit_release(self) -> None:
        """atexit handler: release lock on normal process exit."""
        self.release()

    def _install_signal_handlers(self) -> None:
        """Install SIGTERM and SIGINT handlers that release the lock then exit.

        Saves previous handlers so they can be restored on release() (important
        for test suites that call acquire/release in a loop).

        signal.signal() must be called from the main thread; if we're in a
        worker thread the ValueError is silently swallowed.
        """

        def _handler(signum, frame):
            self.release()
            os._exit(1)

        try:
            self._prev_sigterm = signal.signal(signal.SIGTERM, _handler)
        except (OSError, ValueError):
            pass

        try:
            self._prev_sigint = signal.signal(signal.SIGINT, _handler)
        except (OSError, ValueError):
            pass

    def _restore_signal_handlers(self) -> None:
        """Restore signal handlers saved in _install_signal_handlers()."""
        if self._prev_sigterm is not None:
            try:
                signal.signal(signal.SIGTERM, self._prev_sigterm)
            except (OSError, ValueError):
                pass
            self._prev_sigterm = None

        if self._prev_sigint is not None:
            try:
                signal.signal(signal.SIGINT, self._prev_sigint)
            except (OSError, ValueError):
                pass
            self._prev_sigint = None
