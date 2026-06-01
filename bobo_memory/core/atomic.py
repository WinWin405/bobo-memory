"""
Atomic file writes + cross-platform file locking.

atomic_write():  write to a temp file in the same directory, then rename.
                 On POSIX rename is atomic; on Windows we use os.replace which
                 is as close as the OS allows.
file_lock():     context manager wrapping filelock.FileLock so callers never
                 import filelock directly.
"""

from __future__ import annotations

import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from filelock import FileLock, Timeout


def atomic_write(
    path: Path | str,
    content: str,
    *,
    mode: int | None = None,
    encoding: str = "utf-8",
) -> None:
    """Write *content* to *path* atomically (tmp → rename).

    Args:
        path:     Target file path. Parent directory must exist.
        content:  UTF-8 text to write.
        mode:     Optional file permission bits (e.g. 0o600 for session files).
        encoding: Text encoding, defaults to utf-8.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".bobo_tmp_")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as fh:
            fh.write(content)
        if mode is not None:
            os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


@contextmanager
def file_lock(
    path: Path | str,
    *,
    timeout: float = 10.0,
    lock_file: Path | str | None = None,
) -> Generator[None, None, None]:
    """Context manager that acquires an exclusive file lock before yielding.

    Args:
        path:      The file to be locked (lock file defaults to <path>.lock).
        timeout:   Seconds to wait before raising Timeout.
        lock_file: Override the lock file path if needed.

    Raises:
        filelock.Timeout: if the lock cannot be acquired within *timeout* seconds.
    """
    path = Path(path)
    lf = Path(lock_file) if lock_file else path.with_suffix(path.suffix + ".lock")
    lf.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(lf), timeout=timeout)
    try:
        with lock:
            yield
    except Timeout:
        raise Timeout(
            f"Could not acquire lock on {path} within {timeout}s — "
            "another process may be writing to this file."
        )


def ensure_dir(path: Path | str, *, mode: int = 0o755) -> Path:
    """Ensure a directory exists, creating it with the given mode if necessary."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p, mode)
    except (NotImplementedError, PermissionError):
        pass
    return p


def secure_dir(path: Path | str) -> Path:
    """Create a directory readable/writable only by the owner (0o700)."""
    return ensure_dir(path, mode=0o700)
