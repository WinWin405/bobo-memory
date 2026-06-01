"""Base StreamAdapter protocol and RawDoc data class."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RawDoc:
    """A parsed document ready to be landed in raw/."""

    title: str
    body: str
    source_id: str = ""          # stable id (hash/UUID); auto-generated if empty
    adapter: str = ""
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    original_path: str = ""      # if sourced from a file


class StreamAdapter(ABC):
    """Abstract base for all stream adapters."""

    name: str = ""

    @abstractmethod
    def parse(self, payload: Any, **kwargs) -> list[RawDoc]:
        """Parse *payload* into a list of RawDoc objects."""
        ...

    def parse_path(self, path: Path) -> list[RawDoc]:
        """Parse a local file path. Override for file-based adapters."""
        return self.parse(path.read_text(encoding="utf-8"))
