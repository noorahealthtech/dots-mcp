"""Quick command-line tester: call an MCP tool by name with JSON args.

Goes through FastMCP's own tool dispatch (the same path a real MCP client triggers),
so it exercises argument validation and serialization — not just the Python function.
Runs LIVE when creds are in .env (KMS_MOCK=0), otherwise MOCK.

Usage:
    uv run python scripts/try_tool.py                       # list available tools
    uv run python scripts/try_tool.py <tool_name> ['<json>']

Examples:
    uv run python scripts/try_tool.py list_content_types
    uv run python scripts/try_tool.py list_tag_types
    uv run python scripts/try_tool.py count_only '{"content_types": ["reports"], "tags": {"country": ["Indonesia", "India"]}}'
    uv run python scripts/try_tool.py search_knowledge '{"content_types": ["reports"], "tags": {"conditionAreas": ["Antenatal Care (ANC)"]}, "limit": 2}'

For the full interactive GUI instead, use the MCP Inspector:
    uv run mcp dev src/dots_kms_mcp/server.py
"""

from __future__ import annotations

import asyncio
import json
import sys

from dots_kms_mcp.server import mcp


async def _run() -> int:
    tools = {t.name for t in await mcp.list_tools()}

    if len(sys.argv) < 2:
        print("Available tools:\n  " + "\n  ".join(sorted(tools)))
        print('\nCall one, e.g.:\n  uv run python scripts/try_tool.py count_only '
              '\'{"content_types": ["reports"]}\'')
        return 0

    name = sys.argv[1]
    if name not in tools:
        print(f"Unknown tool {name!r}. Available:\n  " + "\n  ".join(sorted(tools)), file=sys.stderr)
        return 1

    try:
        args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    except json.JSONDecodeError as exc:
        print(f"Invalid JSON args: {exc}", file=sys.stderr)
        return 1

    result = await mcp.call_tool(name, args)
    # FastMCP returns (content_blocks, structured_content); print each block's text.
    blocks = result[0] if isinstance(result, tuple) else result
    for block in blocks:
        print(getattr(block, "text", block))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
