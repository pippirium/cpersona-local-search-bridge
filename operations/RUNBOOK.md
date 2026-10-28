# Bridge operations runbook (B-15)

Status date: 2026-10-26. §2–§7 were executed on the development Mac
against a throwaway provider data dir; the backup round-trip, rebuild drill
(which MEASURED rowid reuse and closed a wrong-file return — D-20, docs/13
Correction), smoke script (9/9) and eval CLI all ran with recorded results.
**[fresh-Mac unverified]** marks the install procedure only — not rehearsed
on a factory-fresh machine.

## 0. Pinned dependencies (D-14 — never "latest")

| component | pin | where it lives |
|---|---|---|
| Python | 3.12 (uv-managed) | `pyproject.toml` `requires-python` |
| MCP Python SDK | `mcp>=2.3,<3` | `pyproject.toml` |
| httpx | `>=0.27` | `pyproject.toml` |
| local-search | commit `31f31048fb8edeedf0f94d295bc0c9471000920a` (v0.1.1) | your checkout; the audited contract is docs/10 for exactly this commit |
| CPersona fork | `7821840f114866673022edfc5fe4b21a9b08a5e5` (2.6.13a2) | ref shapes `mem:<int>`/`ep:<int>` audited docs/10 |

Before upgrading ANY pin: re-run the opt-in live integration tests
(`tests/integration/`, section 6) — they are the contract tripwire.

## 1. Install **[fresh-Mac unverified]**

```sh
git clone https://github.com/pippirium/cpersona-local-search-bridge
cd cpersona-local-search-bridge
uv sync                 # python 3.12 + mcp + httpx (dev: pytest)
```

No package install is needed. From the checkout root run
`PYTHONPATH=src uv run python -m bridge.server`.
Pytest's `pythonpath=["src"]` setting applies only to tests, not server startup.

## 2. Start local-search and register the watched folder (operator, once)

Start local-search as that project's docs describe (loopback only; never
change its guard/listener — AGENTS.md). Register a **dedicated** folder
for Bridge-managed copies and keep `watch: true`:

```sh
curl -s -X POST -H "Host: 127.0.0.1:18765" -H "Content-Type: application/json" \
  -d '{"path":"<DEDICATED-DIR>","kinds":["image"],"exclude":[],"watch":true}' \
  http://127.0.0.1:18765/api/folders
# -> {"id": N, ..., "watch": true}
```

The Bridge NEVER calls folder write endpoints; this step is yours. Keep
the folder dedicated. Pending resolution compares stored basename, shard,
folder, kind and size against listing and fresh detail; a full raw recent
page remains pending (D-23/D-24). Owned managed copies are hashed locally
before binding; the resolver does not return image bytes.

## 3. MCP host registration

stdio only (docs/12 §1). Example for a generic MCP client config:

```json
{
  "mcpServers": {
    "cpersona-bridge": {
      "command": "uv",
      "args": ["run", "--directory", "/ABS/PATH/cpersona-local-search-bridge",
               "-m", "bridge.server"],
      "env": {
        "PYTHONPATH": "/ABS/PATH/cpersona-local-search-bridge/src",
        "BRIDGE_DATA_DIR": "/ABS/PATH/bridge-data",
        "BRIDGE_PROVIDER_URL": "http://127.0.0.1:18765",
        "BRIDGE_MANAGED_ROOT": "/ABS/PATH/bridge-managed",
        "BRIDGE_MANAGED_FOLDER_ID": "N"
      }
    }
  }
}
```

Environment keys (src/bridge/config.py validates all of them at startup):

| key | meaning |
|---|---|
| `BRIDGE_DATA_DIR` | Bridge SQLite (metadata only). Created 0700 |
| `BRIDGE_PROVIDER_URL` | loopback literal only (`http://127.0.0.1:<port>`) |
| `BRIDGE_PROVIDER_INSTANCE_ID` | optional; defaults to the URL authority |
| `BRIDGE_SOURCE_INSTANCE_ID` | validated configuration metadata; MCP scope arguments still default to empty. Pass `source_instance_id` explicitly and identically on each scoped tool call for multiple instances |
| `BRIDGE_MANAGED_ROOT` + `BRIDGE_MANAGED_FOLDER_ID` | managed import opt-in, all-or-nothing; root must be 0700, `managed_folder_id` = the id from step 2 |

`bridge_health` answers `managed_enabled`, `managed_folder_registered`,
`managed_watch` and `managed_root_matches` — the premise is checked live,
never assumed.

## 4. Backup / restore

Bridge state consists of `BRIDGE_DATA_DIR/bridge.db` and, when enabled, the
managed and staging roots. Stop Bridge writers and capture the database and
roots as one consistent logical snapshot. Use the SQLite online backup API;
do not copy only the live main DB and ignore its WAL:

```sh
python - <<'PYCODE'
import contextlib, os, sqlite3
from pathlib import Path
source = (Path(os.environ["BRIDGE_DATA_DIR"]) / "bridge.db").resolve()
if not source.is_file():
    raise SystemExit("Bridge database not found")
backup = Path("bridge-backup.sqlite3")  # choose a new, protected destination
fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
os.close(fd)
with contextlib.closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
    with contextlib.closing(sqlite3.connect(backup)) as dst:
        src.backup(dst)
PYCODE
```

Snapshot the configured managed and staging roots separately while writers are
stopped, keep the matching snapshots together, and include the database backup.

- **Backup**: retain the protected database backup and root snapshots together.
- **Restore**: stop writers, restore the database backup as `bridge.db` in a
  clean Bridge data directory and restore the matching roots; point the env at
  those restored locations, restart, and run `bridge_health` first.
- **What a restore does NOT fix**: provider file ids may have drifted
  while you were down. Run `audit_assets` (dry-run default) → expected
  `stale`/`missing` findings → decide. Re-link is explicit
  (`unlink_asset` + `link_asset`); the Bridge never re-binds silently
  (D-08) and never deletes your files (docs/08).
- Managed copies are content-addressed: a repeat in the same scope and
  configured provider folder may reuse its asset without a second write. A
  different scope gets a separate asset ID even when physical bytes are shared.
- Never restore into or copy from local-search's data dir or CPersona's
  memory DB — those are owned by their apps.

## 5. Provider index rebuild (what it does to you)

Measured (docs/15 + B-15 drill): rebuild bumps `generation`, and a deleted source + rebuild + new file **reuses the old `file_id`** (rowid reuse, measured twice).
Bridge behavior after a rebuild: reused ids are refused AT FETCH TIME
by the D-20 evidence gate (`get_asset` -> `asset_unavailable`, never the new
file's bytes); `audit_assets` classifies evidence-drifted locators `stale`
(links preserved); managed copies
re-index themselves through the watcher and
`resolve_pending_assets` binds fresh ids only for `pending` rows.
External links that go stale need an explicit relink by you. After any
rebuild: `bridge_health` → `audit_assets` (dry) → decide → optionally
`audit_assets(apply=true)` (state transitions only, never rebinds).

## 6. Diagnostics

- `bridge_health` — provider reachable? generation? index mismatch?
  managed premise? (codes only, never paths)
- `resolve_pending_assets` — why each managed copy is still pending
  (`not_indexed_yet` / `ambiguous_size` / `page_full` /
  `detail_mismatch` / `premise_folder_unwatched` / provider fault codes).
  Safe to poll; never uses search (docs/15: filename queries are
  below-threshold anyway).
- `audit_assets` — dry-run classification + managed-dir orphan/scratch/
  foreign scan (reports asset ids and codes only). `apply=true` repairs
  states ONLY through the frozen transition graph.
- `uv run pytest` — the whole offline suite (fakes + temp DBs; no sockets).
- live contract tripwire (provider must be up; NEVER starts/stops it):
  ```sh
  BRIDGE_LIVE_BASE_URL=http://127.0.0.1:18765 \
  BRIDGE_LIVE_FOLDER_ID=N BRIDGE_LIVE_WATCHED_DIR=<DEDICATED-DIR> \
  uv run pytest tests/integration -q
  ```
- manual smoke (its own synthetic fixture + link, cleans up after
  itself):
  ```sh
  uv run python operations/smoke.py --provider http://127.0.0.1:18765 \
      --data-dir /tmp/bridge-smoke --folder-id N
  ```
- eval benchmark (explicit, measured vs unmeasured honest): `eval/README.md`

## 7. Privacy check (run after upgrades, and before sharing reports)

1. `uv run pytest tests/security -q` — the committed-text sentinel (no
   personal paths/tokens in tracked files) and policy-rule tests.
2. Log discipline is by construction (D-19): the Bridge writes no log
   files; tool results carry codes and ids only; provider message text is
   discarded at the client boundary (T-09). If you wrap it in a supervisor,
   keep ITS logs code-level too.
3. Eval reports contain fixture ids and ranks, never paths or bytes —
   spot-check `result.json` before sharing.
4. Bridge SQLite holds metadata only; managed copies live under
   `BRIDGE_MANAGED_ROOT` (0700) and nowhere else — `audit_assets` reports
   foreign files appearing there instead of deleting them (docs/08).

## 8. Known limits (do not paper over)

- `/api/files/recent` caps at 100 newest entries with no paging (docs/10
  correction): keep the managed folder dedicated and modestly sized, or
  resolution fail-closes `page_full` (by design).
- `missing` + managed-copy-on-disk is reported (`missing_copy_on_disk`),
  never auto-served — serving from copies is a separate authorized
  product decision (docs/08).
- No automatic scheduler: resolve/audit are explicit tool calls on
  purpose (docs/03 §7 — no implicit actions).
- Managed deletions are not implemented at all; orphan findings are the
  input to a future operator-authorized cleanup, not a trigger.


## IMG-02 schema upgrade and rollback

Before starting code with schema v2, stop writers and make a consistent SQLite
online backup together with a matching snapshot of the configured managed
root. Startup migrates v1 to v2 and preserves existing links and locators.
After upgrading, verify the reported schema version and audit managed assets.
An older binary rejects a v2 database; rollback requires restoring the matching
pre-upgrade database and managed-root snapshot, not dropping owner records.
Retain the v2 backup separately if any images were registered after upgrade.

`ingest_image` requires an explicitly configured managed root and folder ID
that local-search has registered and watches. The Bridge checks that the
provider folder and configured root refer to the same directory. The trusted
host must explicitly send supported image bytes as bounded standard Base64;
no memory is linked until a separate explicit association. Run
`resolve_pending_assets` after the provider indexes the managed copy. A
failed import may leave a content-addressed orphan; audit reports managed
files and removal requires a separate operator action.


`get_ingest_status` checks one owner- or link-visible asset without changing
its locator. It reports waiting, unavailable provider/folder, index conflict,
missing file or identity mismatch as codes. It makes at most three logical provider reads (each may retry once on a
connection failure) and reads at most the configured image byte cap for a managed digest.
A reported `ready` does not replace the explicit `get_asset` identity checks.
Run `resolve_pending_assets` separately after indexing; a status read never
binds pending IDs.


## Local staging handoff for hosts without image-byte arguments

Set `BRIDGE_STAGING_ROOT` to a private, dedicated temporary directory and
`BRIDGE_STAGING_INPUT_ROOT` to an existing operator-allowed selection directory;
keep both separate from the Bridge data and managed roots. Use the same Bridge
environment and exact scope as the MCP server. In the trusted local terminal:

```sh
PYTHONPATH=src uv run python -m bridge.stage_image \
  --agent-id example --relative-path selected-image.png --confirm
```

The selected relative path is only a local CLI argument, never an MCP argument.
The command outputs an opaque `staging_id` and no source path. The user must
explicitly choose the image; the command reads a stable, fully validated
snapshot and never removes the original. Advisory locks cannot prevent a
non-cooperating writer from changing the source after selection. Pass the ID to
`import_staged_image` with the exact scope; use `get_ingest_status` and
`resolve_pending_assets` separately. IDs expire after 15 minutes. Failed
imports remain retryable until expiry; successful retries return the original
result. Expiry alone does not delete the temporary file.

To explicitly remove at most 100 expired ledger-owned staging files, run
`PYTHONPATH=src uv run python -m bridge.stage_image --gc --confirm`. GC does
not delete the original, foreign entries, managed copies or live tokens. A
replaced staging file is reported as blocked for operator review. Crashes can
leave an untracked temporary orphan; inspect the dedicated staging root before
manual removal. Never enable staging by pointing it at a source directory.

Schema v3 adds staging metadata and is migrated at startup. Before upgrading,
make a consistent SQLite online backup and managed-root snapshot; retain a
staging-root snapshot if pending handoffs need recovery. Older binaries reject
a v3 DB; rollback requires restoring matching backups and abandoning tokens
created after that backup.


## Allowlisted discovery

Set `BRIDGE_DISCOVERY_FOLDER_IDS` to a comma-separated set of up to 16 positive
local-search folder IDs. The explicitly configured managed folder is also
allowed and counts toward the 16. Unset means no general folders are exposed.
`discover_assets` searches only those folders, using a separate provider-side
filter per folder. It returns labels and short-lived selection tokens, never
image bytes or other-scope asset IDs. An unapproved folder returns the same
not-found result without a provider search.

Discovery is an explicit potentially expensive operation: at most five search
pages and 20 identity-verification reads per call, each up to the configured
image byte cap. An empty partial page can mean more folders need scanning;
repeat the identical query and folder with `next_cursor`. Cursors are
single-use and, like selection tokens, live in the Bridge process for at most
five minutes and can expire earlier on restart or capacity pressure. `partial`
also reports provider result caps, without exposing hit counts. Selection must
still be explicitly approved and revalidated; discovery never links a memory.


## Explicit image-to-image search

Call `search_assets_by_image` with exact scope and exactly one of a bounded
`image_base64` argument or an owned/linked `query_asset_id`. The host must
actually support forwarding the bytes; otherwise use an already registered
asset ID. The Bridge validates image bytes, delegates embedding and search to
local-search, and returns only scope-linked IDs, memory references and scores.
No image bytes are returned and search input is not stored. A search can read
the query and linked managed candidates internally to verify their hashes.
At most five provider pages of 20 are scanned; `partial` reports a limit or an
unavailable scoped result, not a recall measurement. Call `get_asset`
separately when the user explicitly requests image display.


## IMG-09 — Safe image → index → memory association → later recall

Full isolated E2E acceptance and matrix: [docs/20](../docs/20-img09-three-process-acceptance.md).
The IMG-09 synthetic run passed with a fake embedder; the historical B-13
success in docs/16 is a separate earlier run.

**Fresh test installation (not production):** use a new checkout with
`uv sync`, review each pinned CPersona/local-search revision in docs/16,
and install CPersona in a separate virtual environment from Bridge.
Never reuse CPersona's live DB, an existing local-search index, a client's
existing MCP registration, or a source folder of user images. The test itself
creates independent temp Bridge DB, CPersona DB, provider DB/index,
watched-folder, managed-root, staging-root and allowed-selection folder. It
starts/stops only children it created. Its local-search process uses the
explicit fake embedder; real search accuracy remains unmeasured here.

Before a real-world workflow, the trusted operator registers and enables
watching for an empty managed folder, configures the corresponding
`BRIDGE_MANAGED_FOLDER_ID` and `BRIDGE_MANAGED_ROOT`, and calls
`bridge_health`. A host that can transmit bounded Base64 invokes
`ingest_image` with a deliberate user-selected image. A host that cannot
transmit image bytes must instead use the local `bridge.stage_image` helper
under an explicitly configured input root, then invoke `import_staged_image`
with the short-lived opaque staging ID. Do not assume a displayed attachment
has been forwarded, and never send arbitrary paths to the MCP service.

After the ingest tool returns `pending`, wait for the provider's watched
folder to index the managed copy; call `resolve_pending_assets` explicitly
and then `get_ingest_status` to confirm `ready`. The link itself may
already be created in `pending` once the trusted host has obtained the real
`mem:N` or `ep:N` from CPersona and the user explicitly approves linking.
Use `link_registered_asset` and then `list_memory_assets`. CPersona's
normal `recall` and `reconstruct` do **not** load image bytes. Only the
explicit `get_asset` tool may return a media block. A candidate discovered
through `discover_assets` is never auto-linked.

Example (schematic; use a real CPersona reference and actual returned ID):

```json
{"tool":"link_registered_asset","arguments":{"agent_id":"example","memory_ref":"mem:42","asset_id":"b519a9d4-9f85-469c-b603-c8ce2c6a54c3"}}
```
```json
{"tool":"list_memory_assets","arguments":{"agent_id":"example","memory_ref":"mem:42"}}
```
```json
{"tool":"get_ingest_status","arguments":{"agent_id":"example","asset_id":"b519a9d4-9f85-469c-b603-c8ce2c6a54c3"}}
```

On cancellation, never call `link_registered_asset`; unlinked candidates
stay outside memory results. If indexing is slow, preserve `pending` and
retry the explicit resolver; do not fabricate ready or change provider IDs.
If the provider is unavailable, check `bridge_health`, retry safe HTTP reads
only under their documented bounds, and do not convert provider errors into
empty search success. On changed content, reindex, rowid reuse or missing
bytes, fail closed and investigate the specific asset via status/audit rather
than substituting another image. If a token expires, require a new deliberate
operator selection; do not bypass expiration. The owner remains allowed to
review provenance after unlink, while another link-only scope loses access
when its last link is removed.

For schema upgrade/restore use the **IMG-08 v4** section:
stop writers, take a consistent online SQLite backup and managed/staging root
snapshots, and restore the matching set on failure. After rollback, explicitly
recreate the associations made after the snapshot with user approval rather
than inventing missing CPersona memory references. No automatic destructive
cleanup is performed. Keep temp child process logs private; do not commit
stderr, memory content, Base64, filename, private path or tokens.

Verification commands (the live test requires dedicated executables):
```sh
uv run pytest -q
uv run pytest tests/security -q
BRIDGE_THREE_PROCESS_CPERSONA_PYTHON=<test-python> BRIDGE_THREE_PROCESS_LOCAL_SEARCH=<test-provider> uv run pytest tests/integration/test_three_process.py -q
```

## Explicitly associate an image with a memory

After the user approves an association, the trusted host obtains the actual
`memory_ref` from CPersona recall/reconstruct and calls `link_registered_asset`
with either the owner-scoped `asset_id` or an explicitly selected discovery
`candidate_token`. A pending asset can be linked before indexing, but cannot be
fetched until it resolves. The Bridge validates reference syntax only; a
well-formed fabricated reference cannot be distinguished from a real one
without consulting CPersona, so the host must supply actual returned refs.
Replays are idempotent only while the identity remains safe; changed content
or index evidence is refused. Historical external assets without a content
digest are not rebound through candidate selection; retain the old association
or investigate and explicitly re-establish it rather than guessing.
Use `unlink_asset` to remove an association without deleting the image.


## IMG-08: Scoped image provenance and schema v4 (Issue #62)

Back up **the Bridge SQLite through SQLite online backup** while writers are stopped;
also snapshot the managed root and staging root (if enabled). Schema v4 adds
`asset_metadata` with a composite (scope, asset_id) key; the startup migration
never alters image bytes, local-search index, CPersona memory text, provider locator
or existing memory links. Verify `bridge_health.storage.schema_version == 4`.
Older Bridge versions reject v4. Roll back only by restoring a matching pre-upgrade
SQLite backup **and** matching managed/staging snapshots; do not downgrade in
place or drop the metadata table to force the old binary to start. Post-upgrade
provenance edits will be lost if you restore the older backup.

The trusted host may set provenance after an explicit registration, or for a
previously linked legacy asset. The same four-part scope MUST be repeated.
Both read and update return `asset_not_found` for unknown/other-scope assets.
They work without a running local-search process and never return image bytes.

```json
{"tool":"set_asset_metadata","arguments":{"agent_id":"example","asset_id":"b519a9d4-9f85-469c-b603-c8ce2c6a54c3","metadata_patch":{"user_caption":"synthetic example","caption_origin":"user","source_kind":"chat_attachment","recorded_at":"2026-10-28T00:00:00+09:00","consent_record":"explicit_user"}}}
```
```json
{"tool":"get_asset_metadata","arguments":{"agent_id":"example","asset_id":"b519a9d4-9f85-469c-b603-c8ce2c6a54c3"}}
```
```json
{"tool":"set_asset_metadata","arguments":{"agent_id":"example","asset_id":"b519a9d4-9f85-469c-b603-c8ce2c6a54c3","metadata_patch":{"user_caption":null}}}
```

An omitted key is unchanged; `null` deletes that value. Clearing
`user_caption` also clears `caption_origin`; a non-null caption requires an
origin. Metadata for an owner remains after unlink. A link-only scope loses
access after its last link is removed, though the isolated metadata row persists
until explicit cleanup of the asset. Field deletion has **no audit/history**
record; make backups before bulk edits. `source_message_ref` must be an opaque,
path-free identifier, not an original message body, URL, or user directory.
Allowed characters are ASCII letters, digits, `.`, `_`, `-`; one of `opaque:`,
`msg:`, `message:`, `mem:`, or `ep:` may prefix it. URL-like domains, path
components and leading dots are rejected. Date strings require an explicit
timezone and at most six fractional digits; their original offset is retained.
`consent_record` merely records the trusted host's claim; it is **not**
independent proof of consent. Captions are unverified user/external-source
labels; automatic OCR/VLM/model captions are not implemented and metadata
never substitutes for asset digest/index checks.

Never duplicate the CPersona memory body into `user_caption`, store a
user's local filename in the Bridge, or use the metadata endpoint as a file
import. Import belongs to `ingest_image`/`import_staged_image`, memory body to
CPersona, and image/index to local-search. The Bridge stores minimal provenance.
