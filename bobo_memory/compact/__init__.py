"""
bobo_memory compact — large-context compaction utilities.

Pure functions + file reads. Zero LLM calls.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bobo_memory.client import MemoryClient


class CompactHelper:
    """Facade for all compact operations, bound to a MemoryClient."""

    def __init__(self, client: "MemoryClient") -> None:
        self._client = client

    def should_compact(self, messages, token_budget=180_000):
        from bobo_memory.compact.threshold import should_compact
        return should_compact(messages, token_budget=token_budget)

    def compact_with_session_memory(self, messages, *, session_path=None):
        from bobo_memory.compact.session_swap import compact_with_session_memory
        sp = session_path or self._client.session.path()
        return compact_with_session_memory(messages, session_path=sp)

    def calculate_keep_index(self, messages, *, min_tail_tokens=10_000):
        from bobo_memory.compact.slicer import calculate_keep_index
        return calculate_keep_index(messages, min_tail_tokens=min_tail_tokens)

    def adjust_index_to_preserve_invariants(self, messages, idx):
        from bobo_memory.compact.invariants import adjust_index_to_preserve_invariants
        return adjust_index_to_preserve_invariants(messages, idx)

    def create_compact_boundary(self, summary):
        from bobo_memory.compact.boundary import create_compact_boundary
        return create_compact_boundary(summary)

    def build_post_compact_attachments(self, *, tool_specs=None, active_files=None):
        from bobo_memory.compact.boundary import build_post_compact_attachments
        return build_post_compact_attachments(tool_specs=tool_specs, active_files=active_files)

    def truncate_head_for_ptl_retry(self, messages, *, rounds_to_drop=2):
        from bobo_memory.compact.slicer import truncate_head_for_ptl_retry
        return truncate_head_for_ptl_retry(messages, rounds_to_drop=rounds_to_drop)
