# CLAUDE.md

Guidance for Claude Code when working in this repo.

## What this is

`dots-kms-mcp` — a Python **MCP server** (FastMCP, official `mcp[cli]` 1.x) that wraps the
Noora KMS `getData` discovery API as chat-callable tools, run locally over **stdio** for
Claude Desktop / Claude Code. The model retrieves on demand ("agentic RAG over an API").

The single endpoint `POST {base}/api/discovery/getData` is documented in the tool
docstrings and the conventions below — treat those as the source of truth. The canonical
API docs live at `https://knowledge.noorahealth.org/platformBuilder/apiDocumentation`
(Noora staff only; login-gated).

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
  Honors `useCountDAL` (count-only), `facet` (buckets), and a mini-Mongo `findQuery` matcher
  (`_doc_matches`) for tag filters (`tags.<coll>.data.<field>`), `_id` (`$in`/string/`$ne`), date
  ranges, `$and`/`$or`. Docs carry realistic `tags` (display+tagId+_id) + `kp_date_created`.
- `configs.py` — `build_configs()` (pure kwarg→API-field mapper, `extra=` passthrough) +
  `validate_configs()` + **`tag_query()`** (display/slug → findQuery on `tags.<coll>.data.<field>`),
  **`date_query()`**, `merge_find_query()`. `tag_filter()`/`date_range_filter()` are DEPRECATED
  (activeFilters shape — rejected by the live API).
- `schema.py` — loads the developer-maintained `kms_schema.json`; `extract_doc_tag_ids()`
  (best-effort tag `_id`s, for `related_documents`); **`extract_doc_attachments()`** (structural
  walk pulling PDF/image/video/link attachments, PDF-first, deduped) + **`document_citation()`**
  (web-app deep link `{KMS_WEB_URL}/published-page/{contentType}?id={_id}`).
- `scripts/build_schema.py` — regenerates `kms_schema.json` from the live tenant (content types +
  counts + harvested tag `values`). Re-run to refresh.
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
- **Tag filtering = `findQuery`, NOT activeFilters.** The documented `activeFilters`/`tagType`
  shape returns HTTP 500 on the live API. Real filtering is Mongo `findQuery` on
  `tags.<collection>.data.<field>` where field is `tagId` (slug) for most collections or `_id`
  for slug-less ones (e.g. `nooraUsers`); `display` also matches. Build it with `tag_query()`;
  the `tags=` param on the query tools is the model-facing entry point. Verified live (e.g.
  `tags.country.data.tagId $in ["indonesia"]` → 42 reports).
- **Content-type spelling:** ids are exactly as the API spells them, not the web-UI URL — e.g.
  `organisationalReports` (API, British "s") vs `organizationalReports` (UI URL, 401s). Confirm
  any new id with a 200 (`scripts/build_schema.py` warns on unreadable ids).
- **No discovery / profiles:** the API can't enumerate types and every `profileType` returns
  empty for this token, so `kms_schema.json` is curated (via `build_schema.py`) and
  `profile_types` is `[]`. Tag vocabularies differ by content type (see each tag type's
  `content_types`).
- **`createdAt` doesn't exist** in this data — date filtering uses `kp_date_created`
  (`compare_regions` defaults to it).
- **Citations are always-on:** every document-returning tool annotates each doc with a
  `source_url` (cite it). `include_attachments=True` (and the `document_attachments` tool)
  add an `attachments` list. The FastMCP `instructions` + prompts steer the model to cite
  sources as clickable links. Attachments come in two shapes — GCS upload objects
  (`kind:"storage#object"`, with a directly-openable `publicUrl`) and external link
  objects/strings — handled structurally (field names vary per content type). Web base is
  `KMS_WEB_URL` (default `https://knowledge.noorahealth.org`).
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
