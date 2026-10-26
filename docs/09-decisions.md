# 09 — Decisions, assumptions and unresolved questions

This log separates **approved current facts** from **proposed implementation policy**. Design proposals are not claims that code exists.

| ID | Status | Decision / rationale | Consequence |
| --- | --- | --- | --- |
| D-01 | Adopted for plan | Standalone bridge repository, public source, no copied private local-search implementation | Simple independent reviews; publish only shareable contracts and synthetic fixtures |
| D-02 | Adopted for plan | CPersona text/memory ownership, local-search media/index ownership, Bridge owns associations | No direct writes to either provider DB |
| D-03 | Adopted for plan | MCP stdio and local-search loopback HTTP first | No remote deployment/auth claims |
| D-04 | Adopted for plan | CPersona ordinary recall returns only text + asset references | get_asset is explicit and optional |
| D-05 | Proposed | Separate Bridge SQLite with opaque memory_ref, stable UUID asset_id and ephemeral provider_file_id | Requires identity and reindex regression tests |
| D-06 | Proposed | M1 links files already indexed, M2 copies optional new images | Reduce moving parts and original-file risk |
| D-07 | Proposed | Scope is source_instance_id/agent_id/project_id/channel, but **not authentication** | MVP for one trusted local operator; remote ACL deferred |
| D-08 | Proposed | Unknown provider identity fails closed and keeps link stale | Do not substitute another image on index changes |
| D-09 | Proposed | Image fetch requires explicit MCP action and byte/MIME/pixel limits | No unrequested memory/VRAM or token cost |
| D-10 | Proposed | LLM-free retrieval benchmarks separated from generated answer accuracy | No GPT model needed for Recall/NDCG |
| D-11 | Proposed | Track all 18 tasks in this bridge; only create external repo changes with later authorization | Prevent accidental upstream PRs |
| D-12 | Adopted for plan | No runtime code or release until M0 contract review | Honest status and small blast radius |
| D-13 | Proposed | Detailed commits in PR and squash-merge planning changes to main | Main remains concise; PR history preserves all commits |
| D-14 | Adopted (B-01 audit) | Providers pinned at local-search `31f31048…920a` / CPersona fork `7821840f…a5e5`; contract fixtures under fixtures/contracts with tests/contract as the compatibility gate | Client changes must keep fixtures+audit doc in step; measured revisions are recorded, not assumed |
| D-15 | Adopted (B-01 audit) | memory_ref = opaque `mem:<int>`/`ep:<int>` string (max 128); source_instance_id is configuration, not a provider field | No numeric parsing; instance disambiguation is explicit |
| D-16 | Adopted (B-02) | Bridge scope is exact-equality over four strings normalized `None→""` only; CPersona's γ/channel widening is deliberately not mirrored in link visibility | No implicit cross-scope reads; agents must reuse the identical scope tuple (CP-01 guide) |
| D-17 | Adopted (B-02) | v1 schema frozen in `migrations/0001_v1.sql`; `provider_file_id` NULL only while pending (partial unique index); locator state graph and revalidation evidence frozen in docs/11 §4–§5 | B-05/B-11/B-12 implement against tests that already pin these rules |
| D-18 | Adopted (B-03) | M1 media caps 8×10⁶ B / 40 MP / JPEG+PNG+WebP+GIF, config-only downward; HEIC/HEIF typed-rejected, never auto-converted | No caller-controlled limits; no silent re-encoding of user originals |
| D-20 | Adopted (B-15) | get_asset enforces docs/11 §5 item 3 as a hard fetch gate (gate 3b): mtime mismatch refuses (`asset_unavailable` + truthful `stale`) unless generation unchanged AND audited name matches; live generation read from `/api/status`, unknown never grants the waiver; stored rel_dir does not exist so name equality stands in (docs/13 correction) | closes the measured rowid-reuse wrong-file return; audit/reconcile stay mtime-blind by contract |
| D-19 | Adopted (B-03) | Logs/responses/fixtures obey the docs/12 §6 forbidden-strings list; scope tuples appear only as a truncated sha256 in logs | CI sentinel test enforces on tracked files |

## Verified source observations (as of 2026-10-26)
- pippirium/local-search: Python FastAPI backend with model worker and local-search API; text/media search; file ID metadata and original bytes routes; local-only listener/guard; LanceDB plus SQLite; production index evaluator. The original files are read-only.
- pippirium/CPersona: fork-specific AGENTS.md prohibits upstream PR/push, production memory mutation, and automatic image bytes in normal recall; poc/embedding/README.md already describes an isolated Jina-v5-nano vs EmbeddingGemma 2 smoke comparison (ten synthetic questions and one image inference).
- Historical baseline: this repository started empty. Current implemented status and remaining validation gates are in README; planning-era proposals alone are not completion evidence.
- The two codebases are independently maintained. API contracts and versions must be pinned to actual commits before B-04.

## Open decisions / pre-M1 gates
1. **Memory IDs:** Resolved by B-01 (docs/10-contract-audit.md): refs are `mem:<int>` / `ep:<int>` strings; the Bridge stores them as opaque text and never parses them. CPersona exposes no instance identifier, so source_instance_id is operator configuration (default `""`).
2. **Provider identity:** Resolved by LS-01 (docs/13): `file_id` is durable across rebuilds/moves with today's read-only APIs; delete+re-add or rowid reuse is fail-closed via evidence (never numeric-only rebind). No checksum endpoint exists; in-place replacement keeping size+mtime is an accepted residual risk (byte confirmation only via explicit fetch). **No local-search change requested.**
3. **Scope enforcement:** M1 local use only is acceptable, but if multiple independent untrusted agents are present, what authenticated identity binds scope?
4. **Search recall:** Resolved by LS-01 (docs/13 Q2): `folder_id` is the only provider filter; external-asset scoped search reports `search_coverage=partial` truthfully (M2's managed folder gives exact coverage for managed assets via `folder_id`). Accepted; no provider change.
5. **Media limits:** Interim defaults adopted by B-03 (docs/12 §3, D-18): 8×10⁶ bytes, 40 MP, JPEG/PNG/WebP/GIF; HEIC/HEIF typed-rejected without auto-conversion. Still open: measured client compatibility must be re-verified in B-15 before calling the defaults final — the values are explicit starting points, not measured guarantees.
6. **License:** Public repository does not automatically grant an open-source license. Select a license explicitly before encouraging third-party distribution; do not assume local-search's license.
7. **Owned files:** Define whether M2 managed files support explicit delete/GC and associated retention requirements; M1 must never delete source assets.

## Change control
- Record new D-IDs when contract, security, data retention or scope changes. Update requirements/architecture/API/tickets in the same PR.
- Do not assert completion without tests and a recorded acceptance run.
- The user has authorized this initial plan/README PR and **Squash Merge**. This does not authorize future implementation changes to CPersona or local-search.

## D-21 — Trusted evidence survives refusals (B-09/B-12, #39)

Demotion to stale/missing/incompatible never refreshes trusted identity columns. Metadata-only revalidation may change status but cannot replace the fetch evidence; a later fetch must still pass its original identity gate. Repeated registration of a resolved locator preserves its evidence. Incompatible remains terminal for get/import failure transitions. No schema migration is needed; rollback restores code only, but cannot repair evidence already overwritten by older versions: operators must explicitly re-establish affected associations.

## D-22 — Independent fetch gates (B-09, #40; supersedes D-20 gate details)

Every fetch checks status, availability, kind, name, generation, size and mtime independently using the decision table in docs/11 §5. Known generation mismatch or name mismatch always refuses; size/mtime edits require known unchanged generation and matching name. Missing generations do not become the string `None` and never grant a waiver. This strengthens fetch only; import/reconcile retain their distinct copy/status purposes and cannot refresh fetch evidence. No API/schema change.

## D-23 — Managed copy identity before binding (B-11, #41)

Size alone never identifies a managed copy. Listing and fresh detail must agree with stored content-addressed basename AND shard `rel_dir`, folder, kind and size (docs/11). Foreign same-size entries are ignored; duplicate identity entries remain ambiguous. The audited provider exposes `rel_dir` in both responses, so missing or contradictory directory evidence leaves pending. No schema change or implicit content read.

## D-24 — Inventory completeness belongs to the raw page (B-06/B-11, #42)

Client-side folder filtering must retain raw_count and truncated in FolderInventory. Resolver never infers completeness from filtered item count; a full effective recent page always waits/page_full. Provider's 100-item cap cannot be bypassed with a larger limit. Internal adapter contract changes only; no HTTP API or database migration.

## D-25 — Deduplicated import preserves locator state (B-10/B-11, #43)

Reusing a managed digest adds only an idempotent scoped link. Existing locator identity, status and verification timestamp survive unchanged; response reports the actual status, including stale/missing/incompatible. Only a new provider locator is initialized pending. Repository initialization conflicts preserve locators, and pending with a resolved file ID is rejected. No schema migration: existing inconsistent rows from older versions need explicit operator review, not automatic rebinding or evidence reset. Code rollback is a revert; metadata backup/restore follows RUNBOOK.

## D-26 — Current documentation and historical evidence (B-15, #44)

README and docs/04 describe current development runtime, docs/06 preserves original acceptance scope, and GitHub issue states/merged PRs track delivery. Historical D-IDs/audit runs remain labeled historical and cannot prove later fixes or unexecuted full integration. Corrected startup requires explicit src on PYTHONPATH; pytest configuration does not configure server startup. No release, real-model quality, fresh-Mac install or real CPersona three-process success is claimed. External MCP link generation capture remains absent, conservatively disabling its edit waiver.
