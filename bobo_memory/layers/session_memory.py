"""
SessionMemory layer — current-session summary for long-context stability.

Mirrors src/services/SessionMemory/sessionMemory.ts from the reference.

Responsibilities:
  - should_extract(messages): heuristic threshold check
  - build_extract_prompt(messages): returns a prompt to fork a summary subagent
  - path(session_id): returns the 0o600-mode session file path
  - canonical_can_use_tool(path): sandbox predicate (only edit exact file)

Thresholds (aligned with reference implementation):
  MINIMUM_INIT_TOKENS      = 10_000
  MINIMUM_UPDATE_TOKENS    = 5_000
  TOOL_CALLS_BETWEEN       = 3
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

from bobo_memory.core.paths import session_memory_dir, session_memory_path
from bobo_memory.core.prompts import session_extract_prompt
from bobo_memory.layers.base import MemoryLayer

MINIMUM_INIT_TOKENS = 10_000
MINIMUM_UPDATE_TOKENS = 5_000
TOOL_CALLS_BETWEEN = 3


def _estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Rough token count — 1 token ≈ 4 characters."""
    total = 0
    for msg in messages:
        content = msg.get("content") or ""
        if isinstance(content, str):
            total += len(content) // 4
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total += len(str(block.get("text") or "")) // 4
    return total


def _count_tool_calls(
    messages: list[dict[str, Any]],
    since_uuid: str | None = None,
) -> int:
    """Count tool_call / tool_use messages, optionally since a message uuid."""
    count = 0
    counting = since_uuid is None
    for msg in messages:
        if since_uuid and msg.get("id") == since_uuid:
            counting = True
            continue
        if not counting:
            continue
        role = msg.get("role", "")
        if role == "assistant":
            content = msg.get("content") or []
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") in (
                        "tool_use", "tool_call"
                    ):
                        count += 1
            elif isinstance(content, str):
                pass
        if msg.get("tool_calls"):
            count += len(msg["tool_calls"])
    return count


def _last_assistant_has_tool_call(messages: list[dict[str, Any]]) -> bool:
    """Return True if the last assistant message contains a tool call."""
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            content = msg.get("content") or []
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") in (
                        "tool_use", "tool_call"
                    ):
                        return True
            if msg.get("tool_calls"):
                return True
            return False
    return False


class SessionMemory(MemoryLayer):
    """Heuristic-driven current-session summary layer."""

    name = "session"

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self._initialized = False
        self._last_message_id: str | None = None
        self._last_token_count: int = 0
        self._session_id: str = str(uuid.uuid4())

    @property
    def memory_dir(self) -> Path:
        return session_memory_dir(self.project_root)

    def path(self, session_id: str | None = None) -> Path:
        sid = session_id or self._session_id
        return session_memory_path(sid, self.project_root)

    def is_enabled(self) -> bool:
        if os.environ.get("BOBO_DISABLE_SESSION_MEMORY", "").lower() in ("1", "true", "yes"):
            return False
        return True

    def ensure_dirs(self) -> None:
        from bobo_memory.core.atomic import secure_dir
        secure_dir(self.memory_dir)

    def build_prompt(self) -> str:
        return ""  # Session Memory is not injected into system prompt directly

    # ------------------------------------------------------------------ #
    # Heuristic extraction trigger                                         #
    # ------------------------------------------------------------------ #

    def should_extract(self, messages: list[dict[str, Any]]) -> bool:
        """Return True if a session memory extraction should be triggered now.

        Mirrors shouldExtractMemory() from sessionMemory.ts:
          - Not triggered until session reaches MINIMUM_INIT_TOKENS.
          - After init: triggered when token threshold AND (tool call threshold
            OR natural break point) are both met.
          - Never triggers mid tool_use chain.
        """
        if not self.is_enabled():
            return False

        current_tokens = _estimate_tokens(messages)

        if not self._initialized:
            if current_tokens < MINIMUM_INIT_TOKENS:
                return False
            self._initialized = True

        token_growth = current_tokens - self._last_token_count
        has_met_token = token_growth >= MINIMUM_UPDATE_TOKENS
        if not has_met_token:
            return False

        tool_calls_since = _count_tool_calls(messages, since_uuid=self._last_message_id)
        has_met_tool_calls = tool_calls_since >= TOOL_CALLS_BETWEEN
        at_natural_break = not _last_assistant_has_tool_call(messages)

        should = has_met_tool_calls or at_natural_break

        if should:
            self._last_token_count = current_tokens
            if messages:
                self._last_message_id = messages[-1].get("id")
        return should

    def build_extract_prompt(
        self,
        messages: list[dict[str, Any]],
        session_id: str | None = None,
    ) -> str:
        """Return the prompt to give a sandboxed summary subagent."""
        sp = self.path(session_id)
        return session_extract_prompt(sp)

    def canonical_can_use_tool(
        self,
        session_id: str | None = None,
    ):
        """Return the whitelist predicate for the sandboxed summary subagent."""
        from bobo_memory.core.guard import MemoryGuard

        guard = MemoryGuard(self.project_root)
        return guard.canonical_can_use_tool(self.path(session_id))
