"""
AutoMemory layer — project-wide persistent memory (user + project dimension).

Stores long-lived facts that should persist across all sessions of the project:
  - user preferences
  - project external context
  - non-code knowledge
  - cross-session collaboration constraints
"""

from __future__ import annotations

import os
from pathlib import Path

from bobo_memory.core.memdir import build_memory_prompt
from bobo_memory.core.paths import auto_memory_dir
from bobo_memory.core.prompts import AUTO_MEMORY_GUIDELINES
from bobo_memory.layers.base import MemoryLayer


class AutoMemory(MemoryLayer):
    """Global, project-scoped persistent memory."""

    name = "auto"

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root

    @property
    def memory_dir(self) -> Path:
        return auto_memory_dir(self.project_root)

    def is_enabled(self) -> bool:
        for env in ("BOBO_DISABLE_AUTO_MEMORY", "BOBO_SIMPLE"):
            if os.environ.get(env, "").lower() in ("1", "true", "yes"):
                return False
        return True

    def ensure_dirs(self) -> None:
        from bobo_memory.core.atomic import ensure_dir
        ensure_dir(self.memory_dir)
        (self.memory_dir / ".trash").mkdir(parents=True, exist_ok=True)

    def build_prompt(self, *, include_instructions: bool = True) -> str:
        if not self.is_enabled():
            return ""

        try:
            self.ensure_dirs()
        except OSError:
            pass

        return build_memory_prompt(
            "Auto Memory",
            self.memory_dir,
            extra_guidelines=[AUTO_MEMORY_GUIDELINES],
            include_instructions=include_instructions,
        )
