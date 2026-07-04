"""
Atomic file writes + cross-platform file locking.

atomic_write():  write to a temp file in the same directory, then rename.
                 On POSIX rename is atomic; on Windows we use os.replace which
                 is as close as the OS allows.
file_lock():     context manager wrapping filelock.FileLock so callers never
                 import filelock directly.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from filelock import FileLock, Timeout

# On Windows os.replace fails with PermissionError while another process holds
# the destination open (e.g. an editor or indexer). A few short retries cover
# the common transient case without masking a real permission problem.
_REPLACE_RETRIES = 3
_REPLACE_RETRY_DELAY = 0.05


def atomic_write(
    path: Path | str,
    content: str,
    *,
    mode: int | None = None,
    encoding: str = "utf-8",
    durable: bool = True,
) -> None:
    """Write *content* to *path* atomically (tmp → fsync → rename).

    Args:
        path:     Target file path. Parent directory must exist.
        content:  UTF-8 text to write.
        mode:     Optional file permission bits (e.g. 0o600 for session files).
        encoding: Text encoding, defaults to utf-8.
        durable:  When True (default), fsync the temp file before rename so
                  the content survives a power loss.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".bobo_tmp_")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as fh:
            fh.write(content)
            if durable:
                fh.flush()
                os.fsync(fh.fileno())
        if mode is not None:
            os.chmod(tmp_path, mode)
        _replace_with_retry(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _replace_with_retry(src: str, dst: Path) -> None:
    for attempt in range(_REPLACE_RETRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == _REPLACE_RETRIES - 1:
                raise
            time.sleep(_REPLACE_RETRY_DELAY * (attempt + 1))


def _lock_path_for(target: Path) -> Path:
    """Return the lock-file path used to serialise writes to *target*.

    Locks for files under a ``.bobo`` tree are centralised in
    ``<.bobo>/locks/<hash>.lock`` — filelock cannot reliably delete lock files
    on Windows, and stray ``*.lock`` files must not pollute memory directories
    that users browse in Obsidian or commit to git. Files outside any
    ``.bobo`` tree keep the legacy beside-the-file lock so locking stays on
    the same filesystem (e.g. remote memory mounts).
    """
    resolved = target.resolve()
    key = str(resolved)
    if os.name == "nt":
        key = key.lower()  # Windows paths are case-insensitive
    for parent in resolved.parents:
        if parent.name == ".bobo":
            digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
            return parent / "locks" / f"{digest}.lock"
    return resolved.with_suffix(resolved.suffix + ".lock")


@contextmanager
def file_lock(
    path: Path | str,
    *,
    timeout: float = 10.0,
    lock_file: Path | str | None = None,
) -> Generator[None, None, None]:
    """Context manager that acquires an exclusive file lock before yielding.

    Args:
        path:      The file to be locked. The lock file is placed in
                   <.bobo>/locks/ when the file lives under a .bobo tree,
                   otherwise beside the file as <path>.lock.
        timeout:   Seconds to wait before raising Timeout.
        lock_file: Override the lock file path if needed.

    Raises:
        filelock.Timeout: if the lock cannot be acquired within *timeout* seconds.
    """
    path = Path(path)
    lf = Path(lock_file) if lock_file else _lock_path_for(path)
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
