# IMG-09 — Synthetic three-process image-to-memory acceptance (#63)

**Status: automation added, execution pending.** This report is not a certification,
and new assertions are not claimed to have passed. See [docs/16](16-three-process-acceptance.md)
for the **historical** B-13 pass from 2026-10-26. This test extends that case;
its current result requires a fresh isolated run. No individual user data is used.

## Scope and prerequisites

Three distinct processes run only on disposable data:

1. Real `pippirium/CPersona` stdio MCP, with its own isolated temporary DB,
   no embedding/generation model and no inherited user credentials.
2. Bridge stdio MCP on a temporary v4 SQLite DB, configured for a dedicated
   managed/watch directory, operator staging directory, and folder allowlist.
3. Real `pippirium/local-search` loopback service, started with its
   **documented fake embedder** and an independent data/index directory.
   No running user's local-search instance is contacted or stopped.

Prerequisite: [PR #71](https://github.com/pippirium/cpersona-local-search-bridge/pull/71)
must be merged before this PR's new v4 provenance assertions can pass.
The test is opt-in, marked skip unless both executable environment variables
are present. The test runner creates its own roots under pytest `tmp_path`.
The operator must ensure the supplied executables really are isolated test
installations, **not production service commands**.

No EmbeddingGemma 2 configuration is changed by either PR. A fake embedder
proves protocol coordination, **not semantic recall accuracy**. No comparison
to any other model is performed.

## Run

On a fresh disposable working copy, review `AGENTS.md` and pin both
external projects as described in [docs/16](16-three-process-acceptance.md).
Use separate virtual environments for CPersona and Bridge; do not replace the
MCP dependency of either with the other project's environment. Install Bridge:

```sh
uv sync
uv run pytest tests/contract tests/unit tests/security -q
uv run pytest tests/integration/test_three_process.py -q
```

The second command must report **skipped** unless specifically opted in.
For a live three-process acceptance, point these variables at verified test
executables installed independently, then run the same test:

```sh
BRIDGE_THREE_PROCESS_CPERSONA_PYTHON=<isolated-cpersona-python> \
BRIDGE_THREE_PROCESS_LOCAL_SEARCH=<isolated-local-search-executable> \
uv run pytest tests/integration/test_three_process.py -q
```

Use a test-only localhost port. The test allocates its own temporary loopback
port; no production listener must be reconfigured. `BRIDGE_THREE_PROCESS_*`
must not be set in normal `uv run pytest` runs. The test executes CPersona
store/recall/reconstruct with a synthetically generated message; no user
memory is ever imported or read. The test creates synthetic red/blue/green
PNG images; it never opens an existing user photograph.

The host must actively pass bytes via Base64 for `ingest_image`. Merely
displaying an attachment does **not** mean the MCP host forwards its data.
For hosts that cannot forward image bytes, the test invokes the confirmed
operator-only `bridge.stage_image` CLI against a dedicated selection folder,
then calls `import_staged_image` with only the returned opaque staging ID.
Both paths return `asset_id` and `pending`; after watcher indexing, an
explicit `resolve_pending_assets` is required to bind and observe `ready`.

## Assertions and negative evidence

| Requirement | Evidence | Execution status in this PR |
| --- | --- | --- |
| Real CPersona store → memory ref → link → recall/reconstruct; no image bytes in normal memory | `tests/integration/test_three_process.py` | Not yet rerun |
| Bridge health, configured watched folder, both direct and staged input | same file | Not yet rerun |
| Unlinked image never appears in `list_memory_assets` | same file | Not yet rerun |
| Scoped `get_ingest_status`, pending → ready, explicit `get_asset` identical bytes | same file | Not yet rerun |
| Source provenance after unlink and other-scope denial | `test_three_process.py` plus PR #71 `test_tools_metadata.py` | Not yet rerun |
| `search_assets_by_image` returns metadata only | `test_three_process.py`; `tests/unit/test_tools_image_search.py` | Not yet rerun |
| Unallowed folder, cross-scope and bad bytes refused | `test_three_process.py`; `tests/unit/test_tools_ingest.py` | Not yet rerun |
| No automatic discovery→link | `tests/unit/test_tools_discover.py::test_discovery_has_no_implicit_links_and_hides_inputs_from_logs` | Existing test, not rerun |
| Expired or replaced staging token, no confirmation, symlink/hardlink, retry/DB failure | `tests/unit/test_staging.py` | Existing tests, not rerun |
| Same-size different content; index churn, source deletion, content drift | `tests/unit/test_reconcile.py`, `tests/unit/test_tools_image_search.py`, `tests/unit/test_tools_get.py` | Existing tests, not rerun |
| Provider outage, timeout, retry, partial pages | `tests/unit/test_http_client.py`, `tests/unit/test_tools_image_search.py` | Existing tests, not rerun |
| Scope contract, unauthorized media access, information leakage | `tests/security/`, `tests/unit/test_tools_get.py` | Existing tests, not rerun |
| Compatibility of previous MCP tools | `tests/unit/test_server_health.py` tool listing + existing contract suite | Existing tests, not rerun |

The real CPersona/real model semantic precision, actual commercial host image
transfer support, long-duration pending under load, fresh-Mac dry run and
full crash/rebuild on a live provider are **not verified** by this new case.
They cannot be marked accepted on the strength of fake embeddings. For any
host lacking native image forwarding, the documented local staging alternative
is required; do not silently read chat attachments or user paths.

## Recovery, backup, and security

See [RUNBOOK](../operations/RUNBOOK.md), in particular the IMG-08 schema v4
backup/rollback section (added by #62), and the IMG-09 companion instructions.
For deployed Bridge schema migrations, stop Bridge writers, take a SQLite
online backup, and snapshot the managed and staging roots. On failure, stop
writers and restore **matching** DB/root snapshots. Downgrading an upgraded
SQLite file in place or dropping the metadata table is prohibited.

The test uses new disposable roots. Child stdout/stderr goes to a
`tmp_path` test-only log; do not copy this log into Git or reports.
The assertion suite and docs should never publish paths, tokens, Base64
payloads, source messages, or personal media. Every failed MCP call must
return a sanitized error code. A cancelled user association is an absent link,
not permission to auto-link after retried discovery.

## Result collection (fill after executing)

- Bridge commit and versions:
- CPersona executable/checkpoint:
- local-search executable/checkpoint:
- Fake vs real embedder:
- `uv run pytest -q`: **not run here**
- `uv run pytest tests/security -q`: **not run here**
- `uv run pytest tests/integration/test_three_process.py -q`: **not run here**
- Distinct scopes/source bytes intact:
- Known unsupported host paths:
- Failure remediation notes:

Do not close #63 on only a unit-test pass; its acceptance requires a real
isolated three-process execution and honest runtime evidence.
