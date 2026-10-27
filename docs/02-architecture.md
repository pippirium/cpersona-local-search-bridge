# 02 — Architecture and end-to-end flows

## Topology (M1)

~~~mermaid
flowchart TB
  A[Trusted local MCP agent] -->|CPersona MCP| C[CPersona: memories]
  A -->|stdio MCP explicit tools| B[Bridge MCP server]
  B -->|Bridge-owned SQL transactions| S[(bridge SQLite: metadata)]
  B -->|HTTP 127.0.0.1:18765| L[local-search FastAPI]
  L -->|read-only| F[User files on filesystem]
  L --> V[(local-search SQLite + LanceDB)]
  L --> E[EmbeddingGemma 2 worker]
  B -. M2 optional copies .-> M[Bridge-managed asset directory]
  L -. watch registered M2 folder .-> M
~~~

The agent is the coordinator; CPersona and Bridge are **separate MCP servers** at first. No modification of CPersona's existing tools is required for M1. If a future unified facade is adopted, it must preserve CPersona's normal text-only recall contract.

## Responsibilities
| Owner | May do | Must not do |
| --- | --- | --- |
| CPersona | persist memory text, provide opaque memory references, recall/reconstruct text and citations | embed or automatically return image bytes under normal recall |
| local-search | scan approved folders, maintain file registry and index; serve guarded search and original-file HTTP APIs | become a general file-writing endpoint or remote listener for this project |
| Bridge | associate stable bridge asset IDs with scoped memories, call local-search HTTP, emit references, explicitly serve bounded image bytes | mutate provider DB, duplicate vector index, silently retrieve image bodies, use arbitrary paths |
| MCP client | choose memory IDs/scopes and explicitly invoke Bridge tools | claim remote authenticated user identity merely by choosing agent_id |

## External API baseline
Existing local-search endpoints verified against main (2026-10-26):
- POST /api/search — JSON body with q, tab=image, page_size; produces ResultItem records including file_id, folder_id, kind, score and hits.
- POST /api/search/media — multipart attachment search; out of M1 text-search scope but candidate for M3 evaluation.
- GET /api/files/{file_id} — file metadata and provenance.
- GET /api/files/{file_id}/content — original bytes with range capability.
- GET /api/status — index health. Availability of the exact fields consumed must be checked and version-pinned in B-01/B-06.
- Verified in B-01 (docs/10-contract-audit.md): 409 `index_mismatch` is returned by the search endpoints only; `GET /api/status` answers 200 and reports `index.mismatch` (`model`/`params`/null) with `index.generation`. File-level staleness surfaces as `409 file_unavailable` (missing/disconnected) or `404 not_found` (unknown/unsearchable id). `GET /api/search` also accepts an optional server-side `folder_id` filter (page_size ∈ {20,50,100}).
- Registration uses folder management + watcher; no confirmed direct user-file upload/write API exists. M2 therefore copies opt-in assets into a pre-registered folder.

The server intentionally binds to 127.0.0.1 only and verifies Host/Origin/Sec-Fetch-Site. The Bridge HTTP client uses the existing localhost route; **do not** solve remote access by disabling the guard. Container/macOS host arrangements need a dedicated approved design later.

## Sequence A: attach indexed file (M1)
1. Agent obtains an actual memory identifier from CPersona and a file_id from local-search (do not fabricate or parse IDs from file names).
2. Agent calls link_asset(memory_ref, scope, provider_ref).
3. Bridge reads current file details, confirms image kind, enforces trust policy and creates/finds stable bridge asset_id.
4. Within one bridge transaction, upsert the scoped link and locator. Return asset_id and status; do **not** return file bytes.
5. Store only an asset reference in future conversational memory if desired; do not rewrite CPersona's existing record automatically.

## Sequence B: recall associated image (M1)
1. Agent uses CPersona reconstruct normally and gets text citations.
2. Agent optionally invokes list_memory_assets or search_assets for the same allowed scope.
3. Bridge invokes local-search POST /api/search, matches returned files against owned locators and scoped links, and returns references and provenance.
4. An explicit get_asset(asset_id, scope) revalidates the association and provider identity, then fetches allowed original bytes; failed revalidation returns unavailable rather than a substituted file.
5. MCP client decides how to render image; ordinary recall never invokes get_asset implicitly.

## Search isolation and ranking
local-search indexes the operator's broader library. Bridge must never hand raw unfiltered hits to an untrusted caller. For scoped search: retrieve candidates from local-search, join against scoped associations, return only matching files, keep original scores and note provider cutoff. Client-side filtering after limited top-k **cannot guarantee scoped recall** when many out-of-scope documents rank first. At B-01 and LS-01, evaluate whether local-search can filter by an authorized folder boundary or support a stable-file scoped query. Until then, return an explicit partial-coverage indicator and never claim exhaustive recall.

## Failure semantics
| Failure | Bridge response / behavior |
| --- | --- |
| local-search unreachable | dependency_unavailable; links remain intact |
| local-search 409 index_mismatch | index_incompatible; no stale file returned |
| unknown/stale file_id | asset_unavailable; never fall back to another ID |
| deleted/moved source or unplugged disk | asset_unavailable; preserve link for reconciliation |
| cross-scope asset ID | not_found/denied without leaking other asset details |
| invalid input or unsupported MIME | invalid_request/unsupported_media |
| oversized image | media_too_large; no whole file buffered |
| SQLite write failure | rollback, report storage_error; no orphaned link |
| configuration mismatch | startup error with sanitized diagnostics |

## Platform and startup
- The M1 Bridge runs as a Python 3.12 MCP stdio service on the same Mac as local-search. It does not start or install local-search.
- A separate Bridge data directory is explicit, private (0700 on POSIX) and distinct from both provider DB directories.
- A fake HTTP provider and temporary bridge DB enable tests without EmbeddingGemma model downloads.
- No remote access is supported until user approval and a separate authenticated transport design.

## IMG-01 proposed ownership boundary

The trusted host obtains user approval and CPersona references; Bridge manages bounded input and scope-scoped associations; local-search indexes the managed folder and supplies search. The Bridge does not authenticate caller-provided scope, update provider databases, or implicitly fetch media during recall. See [docs/19](19-image-input-contract.md).
