# dots-kms-mcp

An **MCP (Model Context Protocol) server** that wraps the Noora KMS `getData`
discovery API so you can ask natural-language questions about your knowledge base
from a chat interface like **Claude Desktop** or **Claude Code**.

The model is given a set of well-described tools and retrieves on demand — it
decides what to search, applies filters, paginates, and synthesizes an answer.
Your whole knowledge base never has to fit in the context window. (This is
"agentic RAG over an API.")

> **Runs without credentials.** If no API token is set, the server starts in
> **mock mode** and returns realistic sample data, so you can wire it into Claude
> and watch the tool-calling work today. Drop in your real token later — no code
> changes.

---

## What it wraps

One endpoint:

```
POST https://okf-be-prod-dot-ok-framework.el.r.appspot.com/api/discovery/getData
Headers: x-auth-token: <token>,  tenant: <tenant-id>
Body:    { "configs": "<JSON.stringify(configObject)>" }   # configs is a JSON *string*
```

A flexible, filterable read API over a MongoDB-backed knowledge base (content
types like articles/stories, profile types like volunteers, tag-based filtering,
full-text search, pagination, joins). This server hides the awkward bits (the
double-encoded `configs`, header auth, error parsing) behind clean tools.

## Requirements

- Python **3.10+**
- [`uv`](https://docs.astral.sh/uv/) (installed at `/opt/homebrew/bin/uv` on this machine)

## Install

```bash
uv sync
```

## Configuration

All config is via environment variables (load them from a local `.env`, which is
gitignored). Copy the template:

```bash
cp .env.example .env
```

| Variable          | Default                                | Purpose |
|-------------------|----------------------------------------|---------|
| `KMS_AUTH_TOKEN`  | *(none)*                               | API token. Secret. |
| `KMS_TENANT`      | *(none)*                               | Tenant identifier string. |
| `KMS_MOCK`        | auto: **on** when creds are missing    | `1`/`true` forces mock; `0`/`false` forces live (needs creds). |
| `KMS_BASE_URL`    | `https://okf-be-prod-dot-ok-framework.el.r.appspot.com` | API host; the `/api/discovery/getData` path is appended. |
| `KMS_SCHEMA_PATH` | bundled default                        | Path to your `kms_schema.json` (see below). |
| `KMS_TIMEOUT`     | `30`                                   | Request timeout (seconds). |

**Auto-mock:** with no `KMS_AUTH_TOKEN`/`KMS_TENANT`, the server logs (to stderr)
that it's in mock mode and serves sample data. Set both vars (and optionally
`KMS_MOCK=0`) to go live.

## The discovery schema (`kms_schema.json`)

The `getData` API has **no endpoint to list content/profile/tag types** or to
resolve a tag name (e.g. "Karnataka") to its MongoDB ObjectId. So those live in a
local, developer-maintained file. Copy the template and edit it:

```bash
cp kms_schema.example.json kms_schema.json
```

Shape:

```jsonc
{
  "content_types": [{ "id": "articles", "name": "Articles", "description": "..." }],
  "profile_types": [{ "id": "volunteers", "name": "Volunteers" }],
  "tag_types": [
    { "id": "states", "name": "States", "name_path": "meta.title",
      "values": { "Karnataka": "673d8531d6ef55f9b7958e6d" } }   // cached name -> ObjectId
  ]
}
```

- `list_content_types` / `list_profile_types` / `list_tag_types` read from this file.
- `resolve_tag` checks the `values` cache first; add confirmed mappings here so the
  model never has to guess tag IDs.

If `KMS_SCHEMA_PATH` is unset, the server uses `./kms_schema.json` when present,
otherwise the packaged default.

## Run & inspect

```bash
# Run the stdio server directly (Ctrl-C to stop)
uv run dots-kms-mcp

# Explore the tools interactively in the MCP Inspector
uv run mcp dev src/dots_kms_mcp/server.py
```

In the Inspector you can list the tools, see their schemas, and call
`search_knowledge` / `list_content_types` to view (mock or live) results.

## Wire into Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) and
add the server under `mcpServers`, then **restart Claude Desktop**. Tools appear
under the MCP (plug) icon.

```jsonc
{
  "mcpServers": {
    "dots-kms": {
      "command": "/opt/homebrew/bin/uv",
      "args": [
        "--directory", "/Users/sreeramramasubramanian/Noora Health/dots-mcp",
        "run", "dots-kms-mcp"
      ],
      "env": {
        "KMS_MOCK": "1"
        // To go live, replace the line above with:
        // "KMS_AUTH_TOKEN": "your-token",
        // "KMS_TENANT": "your-tenant"
      }
    }
  }
}
```

Use the absolute path to `uv` (Claude Desktop doesn't inherit your shell `PATH`).

## Wire into Claude Code

```bash
claude mcp add dots-kms -e KMS_MOCK=1 -- \
  /opt/homebrew/bin/uv --directory "/Users/sreeramramasubramanian/Noora Health/dots-mcp" run dots-kms-mcp
```

(Swap `-e KMS_MOCK=1` for `-e KMS_AUTH_TOKEN=... -e KMS_TENANT=...` to go live.)

## Tools & resources

| Name | What it does |
|------|--------------|
| `search_knowledge` | The workhorse: query content **or** profiles with search, filters, sort, projection, pagination. |
| `get_document` | Fetch one document by `_id` within a content/profile type. |
| `list_content_types` | List queryable content types (from `kms_schema.json`). |
| `list_profile_types` | List queryable profile types. |
| `list_tag_types` | List tag types + any cached name→ObjectId mappings. |
| `resolve_tag` | Turn a tag name ("Karnataka") into its ObjectId (cache first; speculative live fallback). |
| `query_getdata` | Raw escape hatch: run any `configs` object (population/joins, facet, aggregation, …). |
| `kms://schema` *(resource)* | The full discovery schema as a resource. |

## Testing

```bash
uv run pytest
```

Covers the configs builder, the **double-stringify on the wire** (via `respx`),
error/auth parsing, the mock client (pagination, search, determinism), settings
resolution, and tool registration. No live credentials required.

## Security

- Never commit secrets. `.env` and `kms_schema.json` are gitignored.
- Prefer the `env` block / `.env` over inlining tokens anywhere shared.
- Under stdio, **stdout is the protocol channel** — all logging goes to stderr.

## Roadmap

- **Remote HTTP transport** for multi-user / ChatGPT connectors: change only
  `main()` to `mcp.run(transport="streamable-http")`; the tools are unchanged.
- **Confirm `resolve_tag`'s live fallback.** The documented API exposes only
  `getData` (no tags endpoint), so the fallback is speculative — validate it
  against a real tenant, and treat the schema cache as the source of truth.
