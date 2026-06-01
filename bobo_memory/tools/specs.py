"""
Tool specifications for bobo-memory.

Each ToolSpec bundles:
  - name: str
  - description: str
  - parameters: dict  (JSON Schema for the tool's arguments)
  - handler: callable (the actual implementation)

get_tool_specs(client) returns all specs, bound to the given MemoryClient.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from bobo_memory.client import MemoryClient


@dataclass
class ToolSpec:
    """A single tool definition with its handler."""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], dict[str, Any]] = field(repr=False)


def get_tool_specs(client: "MemoryClient") -> list[ToolSpec]:
    """Return all memory tool specs, handlers bound to *client*."""
    from bobo_memory.tools.handlers import (
        handle_memory_save,
        handle_memory_update,
        handle_memory_list,
        handle_memory_read,
        handle_memory_recall,
        handle_wiki_link,
        handle_wiki_log,
        handle_ingest_next,
    )
    from bobo_memory.tools.lifecycle import (
        handle_memory_forget,
        handle_memory_restore,
        handle_memory_purge,
    )

    def _bind(fn):
        def _handler(args: dict) -> dict:
            return fn(args, client=client)
        _handler.__name__ = fn.__name__
        return _handler

    specs = [
        ToolSpec(
            name="memory_save",
            description=(
                "Save a new memory file and update MEMORY.md index. "
                "Use this to persist important information for future sessions. "
                "Requires: layer, topic, content. Optional: tags, sources."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "layer": {
                        "type": "string",
                        "enum": ["agent", "auto", "wiki"],
                        "description": "Memory layer to save into.",
                    },
                    "topic": {
                        "type": "string",
                        "description": "Short slug for the memory file (e.g. 'budget-constraints').",
                    },
                    "content": {
                        "type": "string",
                        "description": "Markdown content of the memory.",
                    },
                    "summary": {
                        "type": "string",
                        "description": "One-line summary for the MEMORY.md index.",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional tags for this memory.",
                    },
                    "sources": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Raw source IDs this memory is derived from.",
                    },
                },
                "required": ["layer", "topic", "content"],
            },
            handler=_bind(handle_memory_save),
        ),
        ToolSpec(
            name="memory_update",
            description="Update an existing memory file. Provide the relative file path and new content.",
            parameters={
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "Relative path to memory file."},
                    "content": {"type": "string", "description": "New markdown content."},
                    "summary": {"type": "string", "description": "Updated one-line summary for the index."},
                },
                "required": ["file", "content"],
            },
            handler=_bind(handle_memory_update),
        ),
        ToolSpec(
            name="memory_list",
            description="List all memories in a layer by returning the MEMORY.md index.",
            parameters={
                "type": "object",
                "properties": {
                    "layer": {"type": "string", "enum": ["agent", "auto", "wiki", "session", "team"]},
                },
                "required": ["layer"],
            },
            handler=_bind(handle_memory_list),
        ),
        ToolSpec(
            name="memory_read",
            description="Read a specific memory file by its relative path.",
            parameters={
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "Relative path to memory file."},
                },
                "required": ["file"],
            },
            handler=_bind(handle_memory_read),
        ),
        ToolSpec(
            name="memory_recall",
            description="Find the most relevant memory files for a query using BM25 search.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "k": {"type": "integer", "default": 5, "description": "Max files to return."},
                    "layers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Layers to search. Defaults to all enabled layers.",
                    },
                    "token_budget": {
                        "type": "integer",
                        "default": 8000,
                        "description": "Max tokens of content to return.",
                    },
                },
                "required": ["query"],
            },
            handler=_bind(handle_memory_recall),
        ),
        ToolSpec(
            name="memory_forget",
            description="Soft-delete a memory file (moves it to .trash/).",
            parameters={
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "Relative path to memory file."},
                    "reason": {"type": "string", "description": "Why this memory is being forgotten."},
                },
                "required": ["file"],
            },
            handler=_bind(handle_memory_forget),
        ),
        ToolSpec(
            name="memory_restore",
            description="Restore a soft-deleted memory file from .trash/.",
            parameters={
                "type": "object",
                "properties": {
                    "trash_file": {"type": "string", "description": "Relative path to trash file."},
                },
                "required": ["trash_file"],
            },
            handler=_bind(handle_memory_restore),
        ),
        ToolSpec(
            name="memory_purge",
            description="Permanently delete a memory file. Cannot be undone. Must be explicitly confirmed.",
            parameters={
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "Relative path to memory file."},
                    "confirm": {"type": "boolean", "description": "Must be true to proceed."},
                },
                "required": ["file", "confirm"],
            },
            handler=_bind(handle_memory_purge),
        ),
        ToolSpec(
            name="wiki_link",
            description="Add a bidirectional cross-reference between two wiki pages.",
            parameters={
                "type": "object",
                "properties": {
                    "from_topic": {"type": "string"},
                    "to_topic": {"type": "string"},
                    "kind": {"type": "string", "description": "Relationship type (e.g. 'related', 'cites', 'contradicts')."},
                },
                "required": ["from_topic", "to_topic"],
            },
            handler=_bind(handle_wiki_link),
        ),
        ToolSpec(
            name="wiki_log",
            description="Append an event entry to wiki/log.md.",
            parameters={
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "description": "Event kind (e.g. 'ingest', 'query', 'lint', 'proposal')."},
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": ["kind", "title", "summary"],
            },
            handler=_bind(handle_wiki_log),
        ),
        ToolSpec(
            name="ingest_next",
            description=(
                "Pull the next pending ingest task from staging/pending.json. "
                "Returns the raw source content and instructions for integrating it into memory/wiki."
            ),
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
            handler=_bind(handle_ingest_next),
        ),
    ]
    return specs
