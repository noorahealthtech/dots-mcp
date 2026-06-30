"""Quick command-line tester: call an MCP tool by name with JSON args.

Goes through FastMCP's own tool dispatch (the same path a real MCP client triggers),
so it exercises argument validation and serialization — not just the Python function.
Runs LIVE when creds are in .env (KMS_MOCK=0), otherwise MOCK.

Usage:
    uv run python scripts/try_tool.py                       # list available tools
    uv run python scripts/try_tool.py <tool_name>           # show that tool's parameters
    uv run python scripts/try_tool.py <tool_name> '<json>'  # call it

Examples:
    uv run python scripts/try_tool.py list_content_types
    uv run python scripts/try_tool.py count_only '{"content_types": ["reports"], "tags": {"country": ["Indonesia", "India"]}}'
    uv run python scripts/try_tool.py compare_regions '{"region_a": "East Java", "region_b": "Central Java", "content_types": ["reports"], "sample_size": 2}'

For the full interactive GUI instead, use the MCP Inspector:
    uv run mcp dev src/dots_kms_mcp/server.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from dots_kms_mcp.server import mcp


def _params(tool: Any) -> tuple[dict[str, Any], list[str]]:
    schema = getattr(tool, "inputSchema", None) or {}
    return schema.get("properties", {}) or {}, list(schema.get("required", []) or [])


def _print_params(tool: Any) -> None:
    props, required = _params(tool)
    if not props:
        print("  (takes no parameters)", file=sys.stderr)
        return
    print("Parameters:", file=sys.stderr)
    for name, info in props.items():
        typ = info.get("type") or (
            " | ".join(o.get("type", "?") for o in info.get("anyOf", [])) or "?"
        )
        tag = "  (required)" if name in required else ""
        print(f"  {name}: {typ}{tag}", file=sys.stderr)


async def _run() -> int:
    by_name = {t.name: t for t in await mcp.list_tools()}

    if len(sys.argv) < 2:
        print("Available tools:\n  " + "\n  ".join(sorted(by_name)))
        print('\nShow a tool\'s params:  uv run python scripts/try_tool.py count_only')
        print("Call one:              uv run python scripts/try_tool.py count_only "
              "'{\"content_types\": [\"reports\"]}'")
        return 0

    name = sys.argv[1]
    if name not in by_name:
        print(f"Unknown tool {name!r}. Available:\n  " + "\n  ".join(sorted(by_name)), file=sys.stderr)
        return 1
    tool = by_name[name]

    # No JSON given: show the tool's parameters instead of calling with {} (which would
    # fail noisily for tools that have required params).
    if len(sys.argv) < 3:
        _, required = _params(tool)
        if required:
            print(f"{name} needs arguments.", file=sys.stderr)
            _print_params(tool)
            example = ", ".join(f'"{r}": ...' for r in required)
            print(f"\nThen call, e.g.:\n  uv run python scripts/try_tool.py {name} "
                  f"'{{{example}}}'", file=sys.stderr)
            return 1
        args: dict[str, Any] = {}
    else:
        try:
            args = json.loads(sys.argv[2])
        except json.JSONDecodeError as exc:
            print(f"Invalid JSON args: {exc}", file=sys.stderr)
            return 1

    try:
        result = await mcp.call_tool(name, args)
    except Exception as exc:  # surface a clean message + param hint, not a traceback
        print(f"Error calling {name}: {exc}", file=sys.stderr)
        print(file=sys.stderr)
        _print_params(tool)
        return 1

    # FastMCP returns (content_blocks, structured_content); print each block's text.
    blocks = result[0] if isinstance(result, tuple) else result
    for block in blocks:
        print(getattr(block, "text", block))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
