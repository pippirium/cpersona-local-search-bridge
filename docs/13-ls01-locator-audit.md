# 13 — LS-01: stable file locator and search-scope compatibility audit (local-search)

Investigation-only deliverable, as the ticket demands: **"まず既存APIで実現できるか示す。実装が不要なら『変更不要』で完了。"** No change to pippirium/local-search is proposed, requested or made by this document. Findings are against the pinned revision `31f31048fb8edeedf0f94d295bc0c9471000920a` (see docs/10).

## Q1. Can a Bridge asset keep a stable reference to a provider file across index rebuilds, using existing read-only APIs?

**Yes, with one frozen caveat.**

| Scenario | What local-search does (verified in source) | Bridge behavior |
| --- | --- | --- |
| Index rebuild (model/params) | Generation switch **updates** `files` rows in place (`copy_building_to_files`: `b.file_id = files.id`); `file_id`, `(folder_id, rel_path)` identity preserved | locator stays; `provider_generation` recorded; revalidate evidence, then `ready` |
| File rename / move within folder | scan move-detection (same kind+size+quick_hash) keeps the row id; vectors follow | locator stays; rel evidence updated via detail fetch |
| Move between registered folders | supported (`elsewhere` rows participate in moves); `folder_id` rewritten on the same row | locator stays; `provider_folder_id` evidence updated |
| Drive disconnected | row kept `status=missing`; `/content` answers `409 file_unavailable` | locator → `missing`; links untouched |
| Drive reconnected | `Restore` path returns previous status without re-read | evidence revalidation → `ready` |
| File deleted, path gone | ledger row deleted; search no longer returns it | locator → `missing`; never auto-rebound |
| File deleted + same path re-added (or different file) | **new `file_id`**; SQLite rowid reuse can also hand a *stale* id to a different file | numeric-id-only rebind is forbidden (docs/11 §5); evidence mismatch ⇒ `stale` |

**Caveat (frozen limitation, no provider change requested):** the ledger holds `quick_hash` (xxh3-64 of size + first/last 1 MiB) but **no API serves any content hash**, so byte-level confirmation requires fetching content — an operation the policy reserves for explicit `get_asset` (docs/08, D-09). Therefore M1 identity evidence is kind+size+mtime+generation+availability (docs/11 §5). Residual risk: a file replaced **in place** keeping size **and** mtime (e.g. `cp -p`/restored backup) is indistinguishable without a content fetch or a provider hash endpoint. The Bridge marks this accepted residual risk explicitly; the evidence set detects all ordinary cases (edit, truncate, move, delete, rebuild, swap-with-different-file).

## Q2. Are search-time filters sufficient, or is scoped search fundamentally partial?

`POST /api/search` supports exactly one scope-relevant filter: **`folder_id`** (int). There is no tag, path-prefix, or per-asset filter, and `folder_id` narrows to a whole registered folder.

- Bridge scoped search = candidate retrieval (page_size 20/50/100, cursor pages available) ∩ Bridge's own scoped links.
- When unassociated library files crowd the top pages, scoped results beyond the fetched pages are unreachable ⇒ **`search_coverage: "partial"` is a correctness property, not a disclaimer** (docs/02 §Search isolation). `remaining`/`next_cursor` from the provider bound how deep pagination could have gone; the Bridge may page up to a configured candidate ceiling but must still report partial when the ceiling bit.
- **M2 improvement without any provider change:** managed assets live in a Bridge-dedicated registered folder, so their searches can pass `folder_id=<managed folder>` — exact candidate coverage for managed assets. External (user-folder) assets remain partial by nature. **Conclusion: no provider change needed for LS-01; partial coverage for external assets is accepted with truthfully-reported coverage.**

## Q3. What about `GET /api/files/recent`, history, SSE events as locator inputs?

Available but unnecessary: `/api/files/recent` and `/api/history` carry no stable identifiers beyond `file_id`; SSE `/api/events` exists for the UI's ingest progress. M1/M2 use the four audited routes plus folder endpoints for the M2 managed-folder registration check (`GET /api/folders` returns `id/status/watch`, which B-11's readiness verification consumes). **No new dependency.**

## Q4. Is any provider API change required?

**No.** Everything M0/M1/M2 contracts need is served today:

| Need | Served by | Change requested? |
| --- | --- | --- |
| durable id across rebuild/move | `files.id` stability (Q1) | No |
| staleness detection | `/files/{id}` 404/409 + status fields | No |
| index-generation awareness | `/api/status` `index.generation`/`mismatch`; search 409 `index_mismatch` | No |
| folder-scoped candidate search | `search.folder_id` | No |
| bounded original fetch | `/content` with `Range` | No |
| M2 readiness observation | `GET /api/folders` (`status`, `watch`), `/api/status` `ingest` | No |
| byte-level identity probe | — (not exposed) | **No change requested.** Accepted as residual risk (Q1 caveat); a future checksum endpoint could be an M2+ discussion, out of scope now |

Consequently, **LS-01 closes as "変更不要" (no provider modification)**. No issue/PR is opened in pippirium/local-search; its loopback listener, guard and read-only behavior remain untouched. `provider_generation` (TEXT) and evidence columns in the frozen schema are sufficient to implement Q1's table (B-12).

## Verification record

- Method: static source audit at the pinned SHA + fixture modeling in this repo (`fixtures/contracts/local-search/*`). No live calls (provider stopped at audit time; none claimed).
- Executable regression: `tests/contract/test_local_search_contract.py` + `tests/unit/test_schema_contract.py` pin the consumed shapes and the locator lifecycle the table above implies; `docs/11` §4 is the state graph B-12 will implement.
- The reindex scenario itself (real rebuild on a disposable corpus) is exercised in **B-13**'s isolated three-process test, not here; this document does not claim it ran.
