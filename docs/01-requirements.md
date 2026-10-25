# 01 — Requirements (planning baseline)

## Problem and goals
CPersona remembers conversations as original text and reconstructs cited evidence. local-search indexes files (text, images, video) with EmbeddingGemma 2 and can return file references and original bytes. The bridge should let an agent connect a remembered event or decision to an image/file, find it later, and explicitly fetch that media **without** putting image blobs in CPersona or duplicating a search engine.

Target user: a single local developer using MCP agents on the same Mac as local-search. Future portability is a design concern, not an MVP promise.

## Non-negotiable boundaries
- CPersona stays authoritative for memory text and memory IDs. Do not alter its ordinary recall/reconstruct to load images.
- local-search stays authoritative for file indexing, embeddings, paths and original-file access; its own assets remain read-only.
- Bridge owns only association metadata and (from M2 onward, optionally) copies of explicitly submitted assets.
- No external LLM or public API calls. Initial model download is local-search's existing independent operation.
- No crossing agents/projects/channels through a bridge lookup. Isolation is not authentication; one trusted local operator/session is the MVP trust model.
- No upstream PRs. All CPersona changes, if warranted, target pippirium/CPersona only.

## User stories
1. Given an image already indexed in local-search and a CPersona memory, associate them without copying the original (M1).
2. Given a remembered topic, find associated images and receive safe IDs plus provenance, not media bytes (M1).
3. Explicitly request an authorized image via MCP and receive one bounded, validated image payload (M1).
4. Unlink an image without deleting the user's original or changing CPersona memory (M1).
5. Import an explicitly supplied image into a dedicated managed folder and wait for local-search indexing (M2).
6. Detect a missing drive, moved file or changed index and report stale references truthfully (M2).
7. Run repeatable offline retrieval evaluations, including text-to-image and image-to-image (M3).

## Functional requirements
| ID | Requirement | Milestone |
| --- | --- | --- |
| FR-01 | MCP stdio server exposes health and stable typed errors | M1 |
| FR-02 | Scoped link/unlink between an opaque CPersona memory key and a stable bridge asset ID | M1 |
| FR-03 | Text-driven asset search via local-search HTTP API; responses are references | M1 |
| FR-04 | List associated assets for a particular memory and scope | M1 |
| FR-05 | Explicit image fetch with size, MIME and scope checks | M1 |
| FR-06 | Durable bridge SQLite with migration and uniqueness constraints | M1 |
| FR-07 | Stable asset IDs survive local-search index generations; no silent reassociation | M2 |
| FR-08 | Optional managed-file import is atomic, content-addressed and never mutates source | M2 |
| FR-09 | Async index readiness, failure recovery and missing/renamed file handling | M2 |
| FR-10 | CLI/setup documentation, backup/restore and operational diagnostics | M3 |
| FR-11 | Isolated replayable benchmark with per-query output | M3 |

## Non-functional requirements
- Confidentiality: default localhost-only access to local-search, stdio-only MCP, no plaintext sensitive logs.
- Correctness: scoped reads/writes; idempotent links; no accidental asset substitution after reindex.
- Reliability: crash-safe SQLite; managed file writes via temporary file + fsync + atomic rename; restart-safe pending status.
- Performance: record p50/p95 with warm and cold cases; no invented latency or accuracy SLO before baseline measurement.
- Resource efficiency: no second EmbeddingGemma instance in the bridge; stream media and cap sizes.
- Compatibility: pin and test the local-search API revision plus CPersona fork revision; surface incompatibilities.
- Usability: explicit status (ready/pending/unavailable/forbidden), actionable local setup and rollback.

## Non-goals for MVP
RAG answers, OCR, automatic image captioning, audio ingestion, remote hosting, team ACL, arbitrary file browsing, running embedding models in bridge, editing either provider's DB, large-scale global indexing, or modifying CPersona's production storage. BGE-M3/Jina/Gemma comparison is a **separate** CP-side benchmark track, not the bridge's runtime.

## Acceptance gates
M0: contracts and unresolved choices written. M1: fake provider + temporary DB demonstrate association, scoped search, explicit fetch and non-destructive unlink. M2: restart/index-mismatch/missing-file/recovery and optional import tests pass. M3: offline evaluation results, manual smoke test, backup/restore and privacy audit documented. No stage is accepted based on README claims alone.
