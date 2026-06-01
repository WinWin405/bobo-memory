"""bobo-memory: universal memory middleware for LLM agents."""

from bobo_memory.client import MemoryClient
from bobo_memory.config import BoboConfig

__version__ = "0.1.1"
__all__ = ["MemoryClient", "BoboConfig"]
