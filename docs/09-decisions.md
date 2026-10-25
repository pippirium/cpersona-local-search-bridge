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

## Verified source observations (as of 2026-10-26)
- pippirium/local-search: Python FastAPI backend with model worker and local-search API; text/media search; file ID metadata and original bytes routes; local-only listener/guard; LanceDB plus SQLite; production index evaluator. The original files are read-only.
- pippirium/CPersona: fork-specific AGENTS.md prohibits upstream PR/push, production memory mutation, and automatic image bytes in normal recall; poc/embedding/README.md already describes an isolated Jina-v5-nano vs EmbeddingGemma 2 smoke comparison (ten synthetic questions and one image inference).
- The current bridge repository started empty. Its planning docs do **not** imply a working integration.
- The two codebases are independently maintained. API contracts and versions must be pinned to actual commits before B-04.

## Open decisions / pre-M1 gates
1. **Memory IDs:** Resolved by B-01 (docs/10-contract-audit.md): refs are `mem:<int>` / `ep:<int>` strings; the Bridge stores them as opaque text and never parses them. CPersona exposes no instance identifier, so source_instance_id is operator configuration (default `""`).
2. **Provider identity:** Can local-search guarantee an indexed file's identity across index rebuilds with its present read-only APIs? Do we need a new file checksum or stable lookup?
3. **Scope enforcement:** M1 local use only is acceptable, but if multiple independent untrusted agents are present, what authenticated identity binds scope?
4. **Search recall:** Post-filtering a bounded candidate list can be incomplete. Is it acceptable to show partial coverage until source-side scoped search exists?
5. **Media limits:** Choose defaults for input bytes, output bytes, pixel count and returned MIME using measured client compatibility; do not hardcode unverified limits in docs.
6. **License:** Public repository does not automatically grant an open-source license. Select a license explicitly before encouraging third-party distribution; do not assume local-search's license.
7. **Owned files:** Define whether M2 managed files support explicit delete/GC and associated retention requirements; M1 must never delete source assets.

## Change control
- Record new D-IDs when contract, security, data retention or scope changes. Update requirements/architecture/API/tickets in the same PR.
- Do not assert completion without tests and a recorded acceptance run.
- The user has authorized this initial plan/README PR and **Squash Merge**. This does not authorize future implementation changes to CPersona or local-search.
