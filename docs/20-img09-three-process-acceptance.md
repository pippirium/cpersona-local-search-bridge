# IMG-09 — Synthetic three-process image-to-memory acceptance (#63)

**Status: isolated synthetic acceptance passed on 2026-10-28.** This is not a
release certification or a semantic-quality measurement. See [docs/16](16-three-process-acceptance.md)
for the separate **historical** B-13 pass from 2026-10-26. No individual user
data was used in the new run.

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
was merged before this run, providing schema v4 provenance support.
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
| Real CPersona store → returned memory ref → link → recall/reconstruct; no image bytes in normal memory | `tests/integration/test_three_process.py` | Passed in isolated run |
| Bridge health, configured watched folder, both direct and staged input | same file | Passed in isolated run |
| Allowed unlinked candidate is discoverable, absent from memory search until explicitly linked, then unlink removes search results | same file | Passed in isolated run |
| Scoped `get_ingest_status`, pending → ready, explicit `get_asset` identical bytes | same file | Passed in isolated run |
| Source provenance after unlink and other-scope denial | `test_three_process.py` plus PR #71 `test_tools_metadata.py` | Passed in isolated run |
| `search_assets_by_image` returns metadata only | `test_three_process.py`; `tests/unit/test_tools_image_search.py` | Passed in isolated run |
| Unallowed folder, cross-scope and bad bytes refused | `test_three_process.py`; `tests/unit/test_tools_ingest.py` | Passed in isolated run |
| No automatic discovery→link | `tests/unit/test_tools_discover.py::test_discovery_has_no_implicit_links_and_hides_inputs_from_logs` | Passed offline |
| Expired or replaced staging token, no confirmation, symlink/hardlink, retry/DB failure | `tests/unit/test_staging.py` | Passed offline |
| Same-size different content; index churn, source deletion, content drift | `tests/unit/test_reconcile.py`, `tests/unit/test_tools_image_search.py`, `tests/unit/test_tools_get.py` | Passed offline |
| Provider outage, timeout, retry, partial pages | `tests/unit/test_http_client.py`, `tests/unit/test_tools_image_search.py`; provider outage in three-process test | Passed offline and isolated |
| Long pending, cancellation, publication interruption/restart, same-size row-ID reuse and deletion with metadata retained | `tests/integration/test_acceptance_failures.py` | Passed offline with synthetic fake provider |
| Existing B-14 CLI, generated media and image-only manifest | `tests/integration/test_eval_live.py` | Passed against isolated fake-embedder provider |
| Runbook online SQLite backup, no overwrite and matching v4 restore | `tests/integration/test_acceptance_failures.py` | Passed with temporary DB and root snapshots |
| No query, attachment bytes, staging token, caption or source paths in Bridge child log | `tests/integration/test_three_process.py` | Passed in isolated run |
| Scope contract, unauthorized media access, information leakage | `tests/security/`, `tests/unit/test_tools_get.py` | Passed offline |
| Compatibility of previous MCP tools | `tests/unit/test_server_health.py` tool listing + existing contract suite | Passed offline |

Real-model semantic precision, image transfer in any specific commercial host,
a real long-duration pending period under load, fresh-Mac dry run and live
crash/rebuild are **not verified** by this new case. Old pending, publication
interruption and index rebuild/row-ID reuse were simulated in isolated tests;
the historical live rebuild observation remains in docs/15.
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

## Recorded result

- Bridge: the IMG-09 branch based on main after PR #71 (`a2f8a21`); schema v4.
- CPersona fork archive `7821840f114866673022edfc5fe4b21a9b08a5e5`, version 2.6.13a2, installed in an isolated environment. Embeddings disabled, so its recall emitted a keyword-only advisory; no generation model was run.
- local-search checkout `31f31048fb8edeedf0f94d295bc0c9471000920a`, version 0.1.1, dedicated temp data and the fake embedder.
- `uv run pytest -o addopts='' -q`: **465 passed, 7 skipped** (optional live tests).
- `uv run pytest tests/security -o addopts='' -q`: **23 passed**.
- With both explicit isolated executable variables set, `uv run pytest tests/integration/test_three_process.py tests/integration/test_ingest_live.py tests/integration/test_eval_live.py -o addopts='' -q`: **3 passed**.
- The unchanged B-14 CLI ran with a temporary image-only derivative of the example manifest. An initial attempt with the example ZIP fixture timed out because the image-only watched folder did not index that fixture; no real-model quality or model comparison is claimed.
- Both scopes were exercised; all source images were synthetic and remained unchanged. The Bridge child log was checked for the generated query, image input, token, caption and source-path sentinels.
- The MCP Python client transferred Base64 in this test; support in a particular end-user host remains unverified. The documented staging handoff is the alternative.

The fake-embedder outputs establish protocol and isolation behavior only. They do
not measure real EmbeddingGemma 2 retrieval quality or validate a fresh Mac install.
