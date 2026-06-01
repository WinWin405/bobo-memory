"""
MemoryLayer protocol — the common interface that all memory layers implement.

Each layer exposes:
  - memory_dir: Path        → where its markdown files live
  - build_prompt() → str    → the system-prompt fragment for this layer
  - is_enabled() → bool     → whether this layer is active
  - ensure_dirs()            → idempotent directory setup
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class MemoryLayer(ABC):
    """Abstract base for all memory layers."""

    name: str = ""  # overridden by subclasses

    @property
    @abstractmethod
    def memory_dir(self) -> Path:
        """Absolute path to this layer's memory directory."""
        ...

    @abstractmethod
    def build_prompt(self) -> str:
        """Return the system-prompt fragment that teaches an LLM about this layer."""
        ...

    @abstractmethod
    def is_enabled(self) -> bool:
        """Return True if this layer should be injected into prompts and tool lists."""
        ...

    def ensure_dirs(self) -> None:
        """Create required directories (idempotent). Called at client init."""
        self.memory_dir.mkdir(parents=True, exist_ok=True)

    def entrypoint_path(self) -> Path:
        """Return the MEMORY.md path for this layer."""
        from bobo_memory.core.paths import ENTRYPOINT_NAME
        return self.memory_dir / ENTRYPOINT_NAME
