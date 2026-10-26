# 11 — B-02: persistent IDs, SQLite schema and scope determination

Builds on the B-01 audit (docs/10). This document **freezes** the identifier rules and the SQLite schema contract that B-05 will implement. M0 delivers decisions + machine-readable schema + executable rule tests — **not** the production repository layer (that is B-05's runtime code).

## 1. Identifier rules (frozen)

### asset_id
- Bridge-generated UUID v4 string (`str(uuid.uuid4())`), lowercase canonical form.
- Pattern: `[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}`.
- Stable across provider index rebuilds, folder moves and file renames. Never derived from provider data, paths or content.
- Accepted as tool input only after pattern validation; unknown pattern → `invalid_request`.

### memory_ref
- Opaque string, `mem:<int>` or `ep:<int>` exactly as CPersona spells refs (B-01). Pattern `(mem|ep):[1-9][0-9]{0,17}`; total length ≤128; never parsed as a number; `profile`/id −1 rejected.

### provider_file_id
- local-search `files.id` (INTEGER rowid, 1..2^63−1). **Locator/cache only.** May be NULL while pending resolution.
- Never used as identity alone (rowid reuse after delete is possible); see §5.

### scope
`(source_instance_id, agent_id, project_id, channel)` — four **strings**, exact-equality compound key.
- `source_instance_id`: operator-configured CPersona installation label, default `""` (single local installation). NOT derived from provider responses (CPersona exposes none).
- `agent_id`: required, 1..200 chars, no control characters. **Record selector, not authentication** (docs/08 boundary remains).
- `project_id`, `channel`: optional, default `""` — normalized empty.
- **Normalization is exactly one rule: `None → ""`. No lowercasing, no trimming, no Unicode folding.** CPersona's own γ/channel semantics deliberately differ from simple equality; the Bridge must not re-implement them implicitly — a different scope string is a different scope, full stop.
- Scope equality is the **only** cross-query isolation mechanism in M1; it is applied to every read and write.

### content_sha256
- Lowercase hex SHA-256 of managed-copy bytes (M2), or of fetched originals **only when the operator explicitly requested a fetch**. Optional (`NULL` allowed) for M1 external references.
- **Not an authorization token** and never a substitute for provider revalidation.

## 2. Scope vs. CPersona read semantics (why exact equality)

CPersona reads apply asymmetric defaults: `project_id=X` reads *X ∪ global('')*; `channel=X` reads *X ∪ ''*. The Bridge deliberately does **not** mirror this in link storage:
- A link written under `(s, a, X, c)` is visible **only** to queries with the identical four-tuple.
- Rationale: silently widening to global would let one project's query see another project's asset links whenever CPersona rows were filed with empty scope — the exact cross-scope leak the MVP forbids.
- Consequence: agents must pass the same scope tuple they used when linking. This is documented for CP-01's operator guide.

## 3. Frozen schema v1

`migrations/0001_v1.sql` holds the exact DDL. It is byte-identical in intent to docs/03's draft with two frozen additions: `PRAGMA user_version = 1` at the end of migration 1, and `link_status` is not stored (see §4). Summary:

- `assets(asset_id PK, origin ∈ {external, managed}, media_kind ∈ {image, video, text}, content_sha256 NULL, relative_managed_path NULL, media_type NULL, size_bytes NULL≥0, created_at, updated_at)` with CHECK tying `managed`↔path and `external`↔no-path.
- `asset_links(source_instance_id, agent_id, project_id, channel, memory_ref, asset_id→assets, created_at)` PK = all five scope+ref+asset columns → duplicate links are impossible at the storage layer.
- `provider_locators(asset_id→assets ON DELETE CASCADE, provider_instance_id, provider_file_id NULL, provider_generation NULL, provider_folder_id NULL, last_seen_kind NULL, last_seen_name NULL, last_seen_mtime NULL, last_seen_size NULL≥0, status ∈ {pending,ready,stale,missing,incompatible}, verified_at NULL)` PK `(asset_id, provider_instance_id)`, UNIQUE `(provider_instance_id, provider_file_id)` plus the partial index in §3. `provider_generation` is stored as TEXT even though local-search spells it int, so a future provider that renames generations cannot be lossily coerced.

### Nullable-UNIQUE decision (explicit)
SQLite UNIQUE treats NULLs as distinct, so multiple `(p, NULL)` rows are allowed. **Frozen: `provider_file_id` must be NULL only while `status='pending'`** (locator created before resolution). B-05 must enforce with a partial unique index:

```sql
CREATE UNIQUE INDEX uq_locator_provider_file
  ON provider_locators (provider_instance_id, provider_file_id)
  WHERE provider_file_id IS NOT NULL;
```

plus an application-level invariant "at most one pending locator per (provider_instance_id)" only if a future feature creates multiple — M1 creates exactly one locator per (asset, provider_instance), so PK alone suffices and no pending-multiplicity index is added now.

### `provider_instance_id`
local-search installation label (operator-configured base identity, e.g. URL origin string `127.0.0.1:18765` by default). Recorded per locator so a second provider instance can never inherit another's file_ids. One provider instance in M1; the column exists because omitting it would force a migration to add it later.

## 4. State model and link/locator interaction (frozen)

- Link existence = user intent. It never carries availability; **deleting or staling a locator never deletes a link** (docs/03 §5/§7).
- Locator status transitions (B-11/B-12 runtime; B-02 freezes the admissible graph):

```
pending → ready        provider resolved & identity verified
pending → stale        resolution ambiguous (e.g. generation changed mid-resolve)
ready   → stale        revalidation cannot confirm identity (size/mtime/kind mismatch)
ready   → missing      provider 404/409 file_unavailable (moved away/disconnected)
*       → incompatible index generation/model mismatch makes resolution meaningless
stale   → ready        successful revalidation with matching evidence
missing → ready        file seen again unchanged (same folder, size+mtime evidence)
```

- No transition may **swap the provider_file_id** of an existing locator to a different file; a new binding means a new asset_id after explicit re-link (D-08 fail-closed).
- `verified_at` updates only on a successful identity revalidation.

## 5. Identity revalidation evidence (frozen minimum set)

Fetch identity validation uses fresh provider metadata before any content request:

| Evidence | Required behavior |
| --- | --- |
| kind / availability / status | image, available, indexed or pending; otherwise refuse |
| known stored and live generation differ | refuse even if mtime is unchanged |
| stored name missing or live name differs | refuse |
| size and mtime match stored evidence | allow, absent a conflict above |
| size or mtime differs/is missing | allow only with known unchanged generation AND identical stored/live name (indexed-edit waiver) |
| generation absent or status probe fails | no edit waiver; matching size/mtime/name still permits fetch |

Name is the audited basename only; no stored relative-directory evidence exists for external locators (docs/13). Unknown evidence cannot override an explicit contradiction. Metadata-only audit/revalidation retains its kind/size/availability verdict and never refreshes fetch evidence (D-21); status ready alone is insufficient for fetch. Refusals preserve the original evidence, IDs and links. An original file whose evidence returns may recover; incompatible remains terminal. This policy does not detect replacement preserving every observed identity field. No content is read implicitly to strengthen identity.

## 6. Timestamps and formats (frozen)
- All stored times: UTC RFC 3339 with `Z` (`2026-10-26T12:00:00Z`), second precision, produced by one helper.
- Provider `mtime` is **evidence data, not Bridge time**: migration 1 adds a `last_seen_mtime` column to `provider_locators` storing the exact string the provider returned (`FileDetail.mtime` is ISO-8601 with offset). Revalidation compares strings/known fields without re-parsing, so a provider format change cannot silently fake a match. Bridge's own `verified_at` uses the helper above.
- UUIDs lowercase; hex digests lowercase; no enum typos accepted (CHECK constraints).

## 7. Migration contract (specification for B-05)
- `PRAGMA user_version` is the single schema version. Fresh DB = apply migrations in numeric filename order; v1 sets user_version=1 inside the same transaction as its DDL.
- **Fail closed**: opening a DB whose user_version > latest known migration aborts startup with a sanitized error (no paths in the message); no reads, no writes, no silent down-migration.
- `PRAGMA foreign_keys=ON` is set on every connection (SQLite default is off) before any statement; B-05 tests assert enforcement bites.
- Migrations are forward-only, one logical step each, no data-dependent branching inside a migration.
- WAL mode is chosen by the **deployment configuration**, not the migration; B-05 documents backup implications (docs/08).

## 8. Repository operations B-05 must expose (derived from this contract)
- `link(scope, memory_ref, asset_id)` — idempotent insert-or-noop; never touches locators or provider.
- `unlink(scope, memory_ref, asset_id)` — deletes the link row only; returns remaining count **within the same exact scope**.
- `upsert_locator(asset_id, provider_instance_id, ...)` — single transaction with asset upsert; CHECK-enforced state values.
- `assets_for_memory(scope, memory_ref, limit)` / `search_join(scope, candidate_file_ids)` — always exact-scope filtered, status returned verbatim.
- Every write runs inside one transaction; a failed provider call during `link_asset` rolls back the whole operation (B-07 acceptance: no half-written links).

## 9. Test policy (executable subset lands in this PR; full repository suite lands in B-05)
Executed here (`tests/unit/test_schema_contract.py`, stdlib sqlite3, temp files):
- migration 1 applies to a fresh temp DB and sets user_version=1;
- duplicate identical link → 1 row (PK);
- same asset linked from two memory_refs and two scopes (4 rows, no conflation);
- empty-scope tuple `("","", "", "")` is distinct from agent-only scope;
- FK enforcement: locator/link insert without asset fails;
- UNIQUE NULL behavior: two pending locators (NULL file_id) coexist; two with same non-NULL file_id collide;
- CHECK: `managed` without path / `external` with path fails; bad status/enum rejected; negative size rejected;
- newer user_version refuses to open (fail-closed simulation);
- identifier pattern tests (asset_id/memory_ref) shared with B-04 input validation.

Deferred to B-05 (documented, not executed now): connection reopen/restart durability, rollback-of-migration failure tests, backup API round-trip, WAL configuration.

## 10. Decisions to record in docs/09 (this PR)
- **D-16 (Adopted)**: scope is exact-equality over four normalized-to-empty strings; CPersona's γ/channel widening is deliberately NOT mirrored in Bridge link visibility.
- **D-17 (Adopted)**: v1 schema frozen as `migrations/0001_v1.sql` + partial unique index; `provider_file_id` NULL only while pending; locator state graph frozen (§4).
