"""File watcher adapter — monitors a directory for new files."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bobo_memory.client import MemoryClient


def start_watch(
    directory: Path,
    *,
    adapter_name: str = "markdown",
    client: "MemoryClient",
    extensions: tuple[str, ...] = (".md", ".txt", ".json"),
) -> None:
    """Start a background thread watching *directory* for new files.

    When a new file appears, it is automatically ingested via `client.ingest()`.
    """
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        raise ImportError("Install 'watchdog' to use file watching: pip install watchdog")

    class _Handler(FileSystemEventHandler):
        def on_created(self, event: Any) -> None:
            if event.is_directory:
                return
            path = Path(event.src_path)
            if path.suffix.lower() in extensions:
                try:
                    client.ingest(adapter=adapter_name, path=path)
                except Exception as e:
                    print(f"[bobo-memory watch] Error ingesting {path}: {e}")

    observer = Observer()
    observer.schedule(_Handler(), str(directory), recursive=False)
    observer.daemon = True

    t = threading.Thread(target=observer.start, daemon=True)
    t.start()
    print(f"[bobo-memory] Watching {directory} (adapter={adapter_name})")
