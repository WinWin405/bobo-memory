"""
MCP (Model Context Protocol) server for bobo-memory.

Exposes the same 11 memory tools over MCP stdio, so any MCP host
(Claude Code, IDE agents, etc.) can use bobo-memory as its memory backend.

Install extras:  pip install "bobo-memory[mcp]"
Run:             bobo-memory mcp [--project-root PATH] [--agent-type NAME] [--scope SCOPE]

All tool calls go through MemoryClient.dispatch_tool_call, i.e. the full
policy → guard → atomic → audit pipeline.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bobo_memory.client import MemoryClient

_MCP_IMPORT_ERROR = 'MCP support requires the mcp package: pip install "bobo-memory[mcp]"'


def create_mcp_server(client: "MemoryClient") -> Any:
    """Build a low-level MCP Server wired to *client*'s tool specs."""
    try:
        import mcp.types as types
        from mcp.server.lowlevel import Server
    except ImportError as exc:
        raise ImportError(_MCP_IMPORT_ERROR) from exc

    server = Server("bobo-memory")

    @server.list_tools()
    async def list_tools() -> list[Any]:
        return [
            types.Tool(
                name=spec.name,
                description=spec.description,
                inputSchema=spec.parameters,
            )
            for spec in client.tool_specs()
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[Any]:
        result = client.dispatch_tool_call(name, arguments or {})
        return [
            types.TextContent(
                type="text",
                text=json.dumps(result, ensure_ascii=False, default=str),
            )
        ]

    return server


def serve_mcp(
    project_root: str = ".",
    *,
    agent_type: str | None = None,
    scope: str | None = None,
) -> None:
    """Run the MCP server on stdio (blocking)."""
    try:
        import anyio
        from mcp.server.stdio import stdio_server
    except ImportError as exc:
        raise ImportError(_MCP_IMPORT_ERROR) from exc

    from bobo_memory.client import MemoryClient

    kwargs: dict[str, Any] = {"project_root": project_root}
    if agent_type:
        kwargs["agent_type"] = agent_type
    if scope:
        kwargs["scope"] = scope
    client = MemoryClient(**kwargs)
    server = create_mcp_server(client)

    async def _run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    anyio.run(_run)
