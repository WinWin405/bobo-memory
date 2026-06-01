"""
Framework adapters — convert ToolSpec lists into native tool formats.

Supports: OpenAI chat completions, Anthropic messages, LangChain.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bobo_memory.tools.specs import ToolSpec


def to_openai_tools(specs: list["ToolSpec"]) -> list[dict[str, Any]]:
    """Convert ToolSpec list → OpenAI chat completions `tools` format."""
    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            },
        }
        for spec in specs
    ]


def to_anthropic_tools(specs: list["ToolSpec"]) -> list[dict[str, Any]]:
    """Convert ToolSpec list → Anthropic messages API `tools` format."""
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "input_schema": spec.parameters,
        }
        for spec in specs
    ]


def to_langchain_tools(specs: list["ToolSpec"]) -> list[Any]:
    """Convert ToolSpec list → LangChain StructuredTool objects.

    Returns plain dicts if langchain is not installed.
    """
    try:
        from langchain.tools import StructuredTool
        from langchain.pydantic_v1 import BaseModel, Field, create_model
        import json

        tools = []
        for spec in specs:
            # Build a minimal pydantic model from the JSON schema
            props = spec.parameters.get("properties", {})
            required = set(spec.parameters.get("required", []))
            field_defs = {}
            for name, prop in props.items():
                annotation = _json_type_to_python(prop.get("type", "string"))
                default = ... if name in required else None
                field_defs[name] = (annotation, Field(default, description=prop.get("description", "")))

            ArgsModel = create_model(f"{spec.name}_args", **field_defs)

            def _make_runner(s):
                def _run(**kwargs):
                    return s.handler(kwargs)
                return _run

            tools.append(
                StructuredTool(
                    name=spec.name,
                    description=spec.description,
                    args_schema=ArgsModel,
                    func=_make_runner(spec),
                )
            )
        return tools
    except ImportError:
        # Return a simple list of dicts if langchain not installed
        return [{"name": s.name, "description": s.description} for s in specs]


def _json_type_to_python(json_type: str) -> type:
    mapping = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    return mapping.get(json_type, str)


def dispatch_tool_call(
    name: str,
    arguments: dict[str, Any],
    specs: list["ToolSpec"],
) -> dict[str, Any]:
    """Find the matching spec and invoke its handler."""
    for spec in specs:
        if spec.name == name:
            return spec.handler(arguments)
    return {"ok": False, "error": f"Unknown tool: '{name}'"}
