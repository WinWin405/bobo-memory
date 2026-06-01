"""Stream pipeline — adapter registry and dispatch."""

from __future__ import annotations

from typing import Any

from bobo_memory.stream.adapters.base import RawDoc, StreamAdapter
from bobo_memory.stream.adapters.markdown import MarkdownAdapter
from bobo_memory.stream.adapters.chat import ChatAdapter
from bobo_memory.stream.adapters.json_log import JsonLogAdapter
from bobo_memory.stream.adapters.web_clipper import WebClipperAdapter


class StreamPipeline:
    """Registry and dispatcher for named stream adapters."""

    global_registry: dict[str, StreamAdapter] = {
        "markdown": MarkdownAdapter(),
        "chat": ChatAdapter(),
        "json_log": JsonLogAdapter(),
        "web_clipper": WebClipperAdapter(),
    }

    @classmethod
    def get_adapter(cls, name: str) -> StreamAdapter:
        adapter = cls.global_registry.get(name)
        if adapter is None:
            raise ValueError(
                f"Unknown adapter '{name}'. "
                f"Available: {list(cls.global_registry.keys())}"
            )
        return adapter

    @classmethod
    def parse(cls, adapter_name: str, payload: Any, **kwargs) -> list[RawDoc]:
        adapter = cls.get_adapter(adapter_name)
        return adapter.parse(payload, **kwargs)
