"""
Memory-worthiness triggers — rule-based, zero LLM calls.

This is the "piggyback" auto-capture mechanism:

  1. detect_memory_signal(messages)   — cheap pattern/heuristic scan of recent
                                        user messages; returns what fired.
  2. MemoryClient.memory_nudge(...)   — when a signal fires (and the cooldown
                                        allows), returns a one-line reminder to
                                        append to the NEXT request's system
                                        prompt. The main model then decides and
                                        calls memory_save in its normal turn —
                                        no extra LLM call is ever made.

The framework never invokes an LLM itself (BYO-LLM). Hosts that want a
dedicated extractor can build one on top of session.build_extract_prompt().
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Patterns that suggest the user just said something worth persisting.
# Grouped by reason so hosts can log / filter what fired.
DEFAULT_SIGNAL_PATTERNS: dict[str, list[str]] = {
    "explicit_remember": [
        r"记住|记一下|别忘了|下次注意",
        r"\bremember\b|\bdon'?t forget\b|\bkeep in mind\b",
    ],
    "correction": [
        r"不对|不是这样|错了|应该是|改成",
        r"\bthat'?s (wrong|incorrect)\b|\bactually,?\s|\bshould be\b",
    ],
    "preference": [
        r"以后(都|请|要)|我(更)?(喜欢|倾向|习惯)|我们(约定|规定|要求)",
        r"\bfrom now on\b|\bi prefer\b|\balways use\b|\bnever use\b",
    ],
    "decision": [
        r"(我们|就)(决定|定下来|敲定|采用)|最终(方案|结论)",
        r"\bwe (decided|agreed|settled) (on|to)\b|\bfinal decision\b",
    ],
}


@dataclass
class MemorySignal:
    """Result of a memory-worthiness scan."""

    triggered: bool = False
    reasons: list[str] = field(default_factory=list)
    matched_text: list[str] = field(default_factory=list)


def _iter_recent_user_texts(messages: list[dict[str, Any]], lookback: int) -> list[str]:
    """Return the plain text of the last *lookback* user messages (oldest first)."""
    texts: list[str] = []
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content") or ""
        if isinstance(content, list):
            content = " ".join(
                str(block.get("text") or "")
                for block in content
                if isinstance(block, dict)
            )
        if isinstance(content, str) and content.strip():
            texts.append(content)
        if len(texts) >= lookback:
            break
    return list(reversed(texts))


def detect_memory_signal(
    messages: list[dict[str, Any]],
    *,
    lookback: int = 4,
    patterns: dict[str, list[str]] | None = None,
) -> MemorySignal:
    """Scan the last *lookback* user messages for memory-worthy signals.

    Pure regex — deterministic, no LLM, safe to call on every turn.
    """
    signal = MemorySignal()
    active = patterns if patterns is not None else DEFAULT_SIGNAL_PATTERNS

    for text in _iter_recent_user_texts(messages, lookback):
        for reason, pats in active.items():
            if reason in signal.reasons:
                continue
            for pat in pats:
                m = re.search(pat, text, re.IGNORECASE)
                if m:
                    signal.triggered = True
                    signal.reasons.append(reason)
                    signal.matched_text.append(m.group(0))
                    break
    return signal


NUDGE_TEMPLATE = (
    "Before finishing this turn, review the recent conversation ({reasons}): "
    "if it contains a durable preference, correction, decision or fact, save it "
    "with `memory_save` (check `memory_recall` first and prefer `memory_update` "
    "if a similar memory already exists). If nothing is worth keeping, do not save."
)


def build_nudge(signal: MemorySignal) -> str:
    """Format a one-line system-prompt nudge for a triggered signal."""
    if not signal.triggered:
        return ""
    return NUDGE_TEMPLATE.format(reasons=", ".join(signal.reasons))
