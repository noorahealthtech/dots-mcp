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
- [`uv`](https://docs.astral.sh/uv/) (run `which uv` to find its install path — used in the wiring examples below)

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

The `getData` API has **no endpoint to list content/profile/tag types**, so those
live in a local file. Generate it from your live tenant (recommended) — the script
confirms each content type is readable, fetches counts, and harvests the tag
vocabularies from real documents:

```bash
uv run python scripts/build_schema.py            # writes ./kms_schema.json
uv run python scripts/build_schema.py someNewType # also confirm/add UI-found types
```

> ⚠️ Content-type ids are exactly as the API spells them, which is **not always
> the web-UI URL**. e.g. the API type is `organisationalReports` (British "s"),
> while the UI URL shows `organizationalReports` (which 401s). The script warns on
> any id it can't read.

Shape:

```jsonc
{
  "content_types": [{ "id": "reports", "name": "Reports", "description": "...", "count": 300 }],
  "profile_types": [],   // none are accessible in this tenant
  "tag_types": [
    { "id": "country", "name": "Country", "name_path": "tags.country.data.display",
      "filter_field": "tagId",                       // "tagId" slug, or "_id" for slug-less collections
      "values": { "Indonesia": "indonesia" },        // display -> filter id
      "content_types": ["reports", "routineVisits"] } // which types carry this collection
  ]
}
```

- `list_content_types` / `list_profile_types` / `list_tag_types` read from this file.
- **Tag filtering uses Mongo `findQuery` on `tags.<collection>.data.<filter_field>`**
  (the documented `activeFilters`/`tagType` shape is rejected by the API). The tools
  build this for you — just pass display names via the `tags` param (below).

If `KMS_SCHEMA_PATH` is unset, the server uses `./kms_schema.json` when present,
otherwise the packaged default.

### Filtering by tags

Every query tool (`search_knowledge`, `collect`, `count_only`, `facet_counts`) takes
a `tags` map of collection → values (display names or slugs):

```jsonc
search_knowledge(content_types=["reports"],
                 tags={"country": ["Indonesia"], "conditionAreas": ["Antenatal Care (ANC)"]})
```

Values within a list are OR'd; collections are AND'd. `list_tag_types` shows valid
values per collection (and which content types carry each — vocabularies differ by
type). `search_by_tag_name` and `compare_regions` are one-step convenience wrappers.

## Run & inspect

```bash
# Run the stdio server (Ctrl-C to stop)
uv run dots-kms-mcp                      # console script
uv run python -m dots_kms_mcp           # equivalent

# Quick CLI tool tester (no client needed)
uv run python scripts/try_tool.py                       # list tools
uv run python scripts/try_tool.py count_only '{"content_types": ["reports"], "tags": {"country": ["Indonesia"]}}'

# Explore the tools interactively in the MCP Inspector
uv run mcp dev src/dots_kms_mcp/server.py
```

> Launch it as a **package**, not a loose file — `python src/dots_kms_mcp/server.py`
> fails with `attempted relative import with no known parent package`. Use the console
> script or `python -m dots_kms_mcp` above.

In the Inspector (or `scripts/try_tool.py`) you can list the tools, see their schemas,
and call `search_knowledge` / `list_content_types` to view (mock or live) results.
Run `try_tool.py <tool>` with no JSON to print that tool's parameters.

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
        "--directory", "/path/to/dots-mcp",
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
  /opt/homebrew/bin/uv --directory "/path/to/dots-mcp" run dots-kms-mcp
```

(Swap `-e KMS_MOCK=1` for `-e KMS_AUTH_TOKEN=... -e KMS_TENANT=...` to go live.)

## Tools, prompts & resources

Exercises all three MCP primitives: **Tools** (model-called), **Prompts** (user-triggered
slash commands), and **Resources** (host-loaded context).

### Tools

| Name | What it does |
|------|--------------|
| `search_knowledge` | The workhorse: query content **or** profiles with search, filters, sort, projection, pagination. |
| `get_document` | Fetch one document by `_id` within a content/profile type. |
| `get_documents` | Batch-fetch several documents by `_id` (optionally expanding references / joins). |
| `count_only` | Just the total count for a query (cheap; uses the API's optimized count mode). |
| `facet_counts` | Counts grouped by a field/tag (e.g. articles per category) — no documents fetched. |
| `collect` | Auto-paginate up to N results in one call (for "a bunch" / "representative sample"; capped at 200). |
| `search_by_tag_name` | Search filtered by a tag **name** in one step (resolves the id for you). |
| `related_documents` | Find documents sharing a given document's tags. |
| `compare_regions` | Pull symmetric, comparable samples for two regions (the cross-synthesis recipe, as code). |
| `list_content_types` / `list_profile_types` / `list_tag_types` | Discovery from `kms_schema.json`. |
| `resolve_tag` | Turn a tag name ("Karnataka") into its ObjectId (cache first; speculative live fallback). |
| `query_getdata` | Raw escape hatch: run any `configs` object (population/joins, facet, aggregation, …). |

### Prompts (slash commands — `/mcp__dots-kms__<name>` in Claude Code)

| Name | What it does |
|------|--------------|
| `kms_compare(region_a, region_b, period?)` | Recipe: resolve both regions, pull samples, cross-synthesize with citations. |
| `kms_research(topic, content_types?)` | Recipe: search + broaden + paginate → a cited research brief. |
| `kms_brief(content_type)` | Recipe: count + facet + recent items → an overview digest. |

### Resources

| URI | What it is |
|-----|------------|
| `kms://schema` | The full discovery schema (static). |
| `kms://content-type/{content_type}` | A content type's schema entry + a recent sample (templated). |
| `kms://recent/{content_type}` | The latest items of a content type (templated). |

## Testing

```bash
uv run pytest
```

Covers the configs builder, the **double-stringify on the wire** (via `respx`),
error/auth parsing, the mock client (pagination, search, determinism), settings
resolution, the group A/B tools (count/facet, `collect` pagination & capping,
tag-name search, region compare, batch/related fetch), and prompt/resource
registration. 52 tests, no live credentials required.

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
