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

## D-27 — Real three-process smoke is opt-in and isolated (B-13, #20)

`tests/integration/test_three_process.py` launches a real fork stdio process, a separate Bridge stdio process and a dedicated loopback provider child. Only generated media and temporary databases are used. Explicit executable environment variables are required; default offline tests skip it. CPersona embeddings are disabled and the provider uses its documented fake embedder, so docs/16 establishes integration correctness without claiming semantic quality. No external repository modifications or live service/config changes.


## D-28 — Real-model probe evidence stays dataset-bounded (B-14, #20)

A predeclared 12-query, three-image RGB manifest was run against pinned real EmbeddingGemma 2 in an isolated provider (docs/17). Commit per-query output and model/manifest fingerprints without private paths, rather than promoting fake scores or this trivial probe to general-quality/model-comparison claims. Report priming/cache limitations and missing cold/resource baselines explicitly. The schema's leading comment becomes standard `$comment`, with no semantic/version change. No provider source or live data/config modifications.

## D-30 — Explicit unlinked-image ownership and input contract (IMG-01, #55)

The proposed image APIs use bounded data or an operator-controlled staging handoff and store exact-scope owner evidence independently of memory links. Deduplication must not reveal other-scope ownership; legacy links retain their prior visibility. User approval and CPersona reference provenance are trusted-host responsibilities, not self-asserted authentication. EmbeddingGemma 2, stdio and the existing explicit fetch boundary remain unchanged. [docs/19](19-image-input-contract.md) specifies the design, migration and rollback; this decision does not claim runtime implementation. D-30 is reserved here because the concurrent CP-02 PR uses D-29.

**Attribution correction:** The [original IMG-01 commit in PR #64](https://github.com/pippirium/cpersona-local-search-bridge/commit/cfee1ec88b98d684621f2435202d306449dddb26) records `Co-authored-by: Codex <codex@openai.com>`. The squash merge `d0b3636` omitted that trailer in error. This note preserves the attribution on main without rewriting published history; it does not change the contract or runtime.

## D-31 — Typed result null fields (IMG-02, #56)

MCP SDK typed result serialization includes unused optional fields as JSON null. The ingest success and error schema allows these null fields while forbidding actual partial results on errors. This corrects the proposed response shape to match the executable wire behavior; it does not change authorization or media-return rules.

## D-32 — Scoped, read-only status observation (IMG-04, #58)

Status reads perform bounded provider and managed-copy checks without writing the locator or invoking the global resolver. A freshly observed identity failure, deletion or index conflict is reported as stale, missing or incompatible even if the stored state has not changed. Provider outages preserve the stored status but set indexed false and supply a retryable reason. checked_at remains the last stored verification time, not the time of this read. Resolution of new owned assets captures the provider index generation after confirming it remains unchanged across the identity checks; missing or changing generation leaves the asset pending.

## D-33 — Explicit local staging handoff (IMG-03, #57)

The operator configures separate private staging and permitted source roots,
selects a relative file with explicit confirmation, and hands the host a
short-lived random token. The MCP tool accepts only that token. Staged bytes,
source evidence and owner scope are rechecked; a transaction commits owner,
retry and consume records together. Expiry does not delete files automatically;
operator-triggered bounded GC removes only matching expired ledger entries.
Source reads use no-follow descriptors, an advisory shared lock and unchanged
file identity checks. A non-cooperating process can still modify an original
after selection; the copied image is a validated snapshot, not a claim of
exclusive control over the source.

## D-34 — Allowlisted discovery with bounded hash evidence (IMG-05, #59)

Discovery queries each operator-allowed folder with the provider-side folder
filter, and retains opaque scope-bound handles only in process memory. To detect
same-size/same-time replacement at later selection, this explicitly invoked
operation reads and validates at most 20 local candidate files per call and
retains hashes, never image bytes or original paths in the response or logs.
Known asset membership is accepted only when stored locator evidence still
matches; reused row IDs and mismatched managed hashes stay hidden. A selection
re-reads and compares its hash and live generation. Cursors and
tokens last at most five minutes and can be evicted on capacity or restart.
The MCP SDK emits unused fields as null and empty arrays; the response schema
permits those on failure without permitting a partial candidate result.

## D-35 — Explicit image-query search (IMG-06, #60)

Image search accepts exactly one bounded image input or an owned/linked query
asset, delegates embedding to local-search, and intersects results with exact
linked scope before returning any metadata. An explicit image search may read
a managed query copy and bounded linked candidate bytes to confirm immutable
content hashes; it neither returns media bytes nor creates a stored query.
Provider snapshots are limited to five pages and stale or incompatible identity
fails closed. The SDK emits unused response fields as null or empty arrays; the
contract allows those without allowing partial results on errors.

## D-36 — Explicit image-to-memory association (IMG-07, #61)

An operator-approved host supplies a CPersona-returned reference and an owned
asset ID or selected discovery token. Bridge checks syntax, scope and current
content identity, and commits a link and retry record atomically; it cannot
prove CPersona reference existence or authenticate the host's approval. Pending
links do not promise availability. Candidate-created external assets store the
verified digest, which both explicit fetch and linked search recheck. Historical
external assets without a digest are not rebound through candidate selection.
Safety checks precede retry replay, so a changed or unavailable source fails
closed even when the request key was previously successful.


## D-37 — Scoped user-authored provenance is not source identity (IMG-08, #62)

Provenance is a separate additive v4 Bridge SQLite relation keyed by exact
scope and asset ID; access is granted by ownership **or** an existing link.
A linked-only scope may have metadata independent of the asset owner's fields.
The API allows nullable partial edits; caption and origin are coupled.
Unlink removes visibility where it ends the only link, but does not silently
erase the scoped metadata. Explicit field erasure leaves no history; SQLite
backup/restore is the rollback path. User claims, consent codes and dates
are never authentication, image identity evidence, or a reason to update the
provider locator. Reject free-text URLs, filesystem paths and unsafe controls;
do not add provenance to ordinary search results (backward-compatible).
The v4 repository locks the database transaction before authorizing and merging
patches, preventing concurrent writers from dropping each other's changes.
The exact safe reference alphabet, namespace and timestamp precision are in
docs/19; malformed stored metadata fails closed instead of being echoed.
