"""
Example: bobo-memory + OpenAI chat completions API

Run:
  pip install openai
  export OPENAI_API_KEY=sk-...
  python examples/openai_demo.py
"""

from __future__ import annotations

import json
import os

from bobo_memory import MemoryClient

# Initialise the memory client
mem = MemoryClient(
    project_root=".",
    agent_type="researcher",
    scope="project",
)

# Build system prompt with memory injected
system_prompt = mem.build_system_prompt(
    "You are a helpful research assistant. "
    "Use your memory tools to remember important information."
)

# Get tools for OpenAI
tools = mem.to_openai_tools()

print(f"[demo] System prompt length: {len(system_prompt)} chars")
print(f"[demo] Available tools: {[t['function']['name'] for t in tools]}")
print()

# Simulate a conversation (no actual API call in this demo)
messages = [
    {"role": "system", "content": system_prompt},
    {"role": "user", "content": "Remember that the Q3 budget is capped at $50,000."},
]

# Simulate the agent calling memory_save
print("[demo] Simulating agent tool call: memory_save")
result = mem.dispatch_tool_call("memory_save", {
    "layer": "auto",
    "topic": "q3-budget",
    "content": "The Q3 2026 budget is capped at $50,000 total.",
    "summary": "Q3 budget cap $50k",
    "tags": ["budget", "Q3", "2026"],
})
print(f"  → {json.dumps(result, indent=2)}")
print()

# Recall
print("[demo] Simulating recall: 'budget constraints'")
pack = mem.recall("budget constraints", layers=["auto"])
print(f"  → Found {len(pack.files)} file(s)")
if pack.files:
    print(f"  → Top result: {pack.files[0].filename} (score={pack.files[0].score:.3f})")
print()

# Status
print("[demo] Current memory status:")
st = mem.status()
agent_layer = st["layers"].get("agent", {})
auto_layer = st["layers"].get("auto", {})
print(f"  agent memory: {agent_layer.get('dir')} (exists={agent_layer.get('exists')})")
print(f"  auto memory:  {auto_layer.get('dir')} (index_lines={auto_layer.get('index_lines')})")
print()
print("[demo] Done! Open .bobo/memory/ in Obsidian to see your memories.")
