# CLAUDE.md

Guidance for Claude Code when working in this repo.

## What this is

`dots-kms-mcp` — a Python **MCP server** (FastMCP, official `mcp[cli]` 1.x) that wraps the
Noora KMS `getData` discovery API as chat-callable tools, run locally over **stdio** for
Claude Desktop / Claude Code. The model retrieves on demand ("agentic RAG over an API").

The complete API spec is the saved page **`Noora KMS.html`** in the repo root — it documents
the single endpoint `POST {base}/api/discovery/getData`. Treat it as the source of truth.

## Commands

```bash
uv sync                                      # install deps + create venv
uv run pytest                                # run the test suite (34 tests, no creds needed)
uv run pytest tests/test_double_stringify.py # run one test file
uv run dots-kms-mcp                          # run the stdio server (console script)
uv run python -m dots_kms_mcp                # same, via module
uv run mcp dev src/dots_kms_mcp/server.py    # open the MCP Inspector
```

## Architecture (src/dots_kms_mcp/)

- `server.py` — the FastMCP instance, **14 `@mcp.tool`s**, **3 `@mcp.prompt`s**
  (`kms_compare`/`kms_research`/`kms_brief`), and resources (`kms://schema` static +
  `kms://content-type/{id}` & `kms://recent/{content_type}` templated), plus `main()` (stdio).
  Internal helpers `_resolve_tag()` and `_collect()` are shared by several tools. Tools resolve
  settings/client/schema once at import.
- `getdata_client.py` — `KmsClient` (real async httpx) + `build_client(settings)` factory +
  `KmsClientProtocol`. Owns the double-stringify and error parsing.
- `mock_client.py` — `MockKmsClient`: deterministic sample data so the server works without creds.
  Honors `useCountDAL` (count-only), `facet` (buckets), and `find_query._id` (`$in`/string/`$ne`);
  docs carry a synthetic `tags` field so related/population demos work.
- `configs.py` — `build_configs()` (pure kwarg→API-field mapper, with `extra=` passthrough for
  facet/useCountDAL/population) + `validate_configs()` + `tag_filter()` / `date_range_filter()` helpers.
- `schema.py` — loads the developer-maintained `kms_schema.json` (discovery + tag-ID cache);
  `extract_doc_tag_ids()` (best-effort, for `related_documents`).
- `settings.py` — `Settings.from_env()`; auto-mock when creds absent.
- `errors.py` — `KmsError` / `KmsConfigError` / `KmsApiError` / `KmsAuthError`.

## Conventions & gotchas (read before editing)

- **Double-stringify:** the request body is `{"configs": json.dumps(configObj)}` — `configs`
  is a JSON *string*, not an object. This lives ONLY in `KmsClient.get_data`; `build_configs`
  returns a plain dict. `tests/test_double_stringify.py` guards it.
- **stdout is the protocol channel** under stdio. NEVER `print()` to stdout — all diagnostics
  go to `sys.stderr`.
- **Tool docstrings are the model's interface.** They carry the full contract (params,
  invariants, examples). Keep them rich and accurate when you change a tool signature.
- **content vs profile:** every query needs exactly one of `contentTypes` / `profileTypes`
  (mutually exclusive). `validate_configs` enforces it in both clients.
- **Tag-ID problem:** filters need Mongo ObjectIds, not names. The local `kms_schema.json`
  (name→id cache) is authoritative; `resolve_tag`'s live fallback is **speculative** (the API
  has no documented tags endpoint) — don't rely on it without verifying against a real tenant.
- **Tools catch `KmsError` and re-raise as `ValueError`** with a readable message so the model
  sees a recoverable tool error.
- Mock mode is automatic when `KMS_AUTH_TOKEN`/`KMS_TENANT` are unset (override with `KMS_MOCK`).

## Secrets

`.env` and `kms_schema.json` are gitignored — never commit tokens. Config is env-driven; see
`.env.example` and the README.

## Testing

`pytest` + `pytest-asyncio` (`asyncio_mode=auto`) + `respx` (httpx mock). All tests run without
live credentials. When changing the client/configs, keep the double-stringify and error-parsing
tests passing; when adding a tool, add it to `EXPECTED_TOOLS` in `tests/test_server_tools.py`.

## Git / commits

- **Do NOT add co-author credits** (no `Co-Authored-By` trailer) to commits in this repo.
- `.env`, `kms_schema.json`, and `tmp/` are gitignored — never commit secrets or local scratch
  (e.g. `tmp/mcp-explainer.html`).
