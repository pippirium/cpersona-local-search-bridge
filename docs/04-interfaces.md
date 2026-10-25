# 04 — Interface contracts (proposed; not yet implemented)

## General rules
All MCP methods require local trusted stdio in M1. Their request schema rejects extra/unrecognized fields, invalid ID lengths and oversized strings. JSON examples below describe **planned tools**, not operational endpoints. Scope fields: source_instance_id (defaults to empty for a single local CPersona installation), agent_id (required), project_id/channel (optional, normalized to empty).

The MCP caller is not authenticated merely by its declared agent_id. This is a **single-operator trusted-local-process** design, not enterprise multi-tenant isolation. Do not enable remote connections without a strong authenticated principal and policy.

Responses must not include absolute paths, private folder names in public logs, original image bytes except explicit get_asset, or any source material from other scopes.

## Proposed MCP tools

### bridge_health
Input: empty object.
Output: bridge schema version, provider connectivity, provider index compatibility, configuration safety indicators, counts only. Never expose tokens, private paths or corpus text.

### link_asset
Input: scope, memory_ref (opaque nonempty string), provider_file_id (positive integer), optional human-supplied note metadata (non-sensitive).
Output: stable asset_id, scope, link_status, provider availability.
Rules: validate media type, provider response and allowed scope before transaction; idempotent under identical link; do not automatically write to CPersona; no image download in the link response.

### unlink_asset
Input: scope, memory_ref, asset_id.
Output: unlinked boolean, remaining_links count within authorized scope.
Rules: idempotent; remove only association row. Never delete local-search file or Bridge-managed copy.

### list_memory_assets
Input: scope, memory_ref, limit (default 20, maximum 100), continuation token when needed.
Output: ordered asset references with kind, display label, status, linked_at and stable asset_id.
Rules: authorized scope only; do not return image bytes; report missing sources truthfully.

### search_assets
Input: scope, query text, kind=image for M1, limit (max 20).
Output: references, scores, provenance, search_coverage (complete/partial), provider health and stable asset IDs. Scope must be enforced **after** candidate retrieval and **before** response.
Rules: unassociated arbitrary local-search library files must not leak. This is **not** a globally exposed personal file search endpoint. Do not equate a filtered top-N sample with exhaustive scoped search.

### get_asset
Input: scope, asset_id, optional memory_ref to require a particular link.
Output: an MCP image content entry (M1 images only) with approved MIME type, bounded image dimensions and encoded bytes, or a sanitized typed error.
Rules: explicit call only; verify the scoped link and source identity immediately before fetch. No raw path parameter, no arbitrary URL, no automatic media read by search or recall. Enforce configured maximum bytes before reading body and during streaming; reject nonimage input. If downscaling/conversion is introduced, do not replace user originals, and mark derived format in result metadata.

### register_asset (M2, future)
Input: scope, memory_ref, a verified local source/upload chosen by the trusted MCP client, permitted MIME, size.
Output: stable asset_id, pending/ready status. Implement only with an explicit managed folder and safe upload/copy contract; **not implemented in M1**.

## Minimal examples (planned protocol)
~~~json
{"tool":"link_asset","arguments":{"agent_id":"coding","project_id":"demo","channel":"notes","memory_ref":"mem-42","provider_file_id":218}}
~~~
~~~json
{"asset_id":"b519a9d4-9f85-469c-b603-c8ce2c6a54c3","link_status":"ready","media_kind":"image"}
~~~
~~~json
{"tool":"search_assets","arguments":{"agent_id":"coding","project_id":"demo","channel":"notes","query":"GPUのエラー画面","kind":"image","limit":10}}
~~~

## Existing local-search HTTP integration
| Purpose | Current endpoint | Plan |
| --- | --- | --- |
| Find image candidates by text | POST /api/search with q and tab=image | Use for search_assets and attach selection |
| Find file details | GET /api/files/{file_id} | Validate kind, status, identity/provenance |
| Fetch original | GET /api/files/{file_id}/content | Explicit get_asset only; streamed + bounded |
| Image query | POST /api/search/media | M3 search quality evaluation; not M1 MCP requirement |
| Check health | GET /api/status | Surface availability and index incompatibility |
| Register watch folder | existing folder management API | Operator/M2 import workflow; do not overwrite source |

The HTTP adapter must accept only configured loopback base URL (http://127.0.0.1:18765 by default). Use a trusted HTTP client with strict redirect prevention, bounded response reads, connect/read timeouts, retry only safe idempotent reads, and parse/version-check returned schemas. A provider 409 index_mismatch is an error, not an empty search.

## Error taxonomy
| Code | Situation |
| --- | --- |
| invalid_request | Invalid IDs/fields/sizes |
| unauthorized_scope | Scope cannot be used under configured trusted-local policy |
| asset_not_found | Unknown asset or asset outside scope (no information leak) |
| asset_unavailable | File missing/offline/stale identity |
| unsupported_media | Returned bytes or source kind unsupported for image tool |
| media_too_large | File or derived payload exceeds bound |
| dependency_unavailable | local-search cannot be contacted |
| index_incompatible | Provider model/index mismatch or API contract conflict |
| storage_error | SQLite transaction or media store failed |

## Extension points and policy
- A future CPersona-native integration may consume stable asset refs; it must preserve existing text-only recall default and be done in the user's fork under a separate approved PR.
- A future provider could replace local-search behind a typed AssetSearchProvider protocol. No generalized provider framework is necessary for M1.
- No arbitrary file upload/write HTTP endpoint should be introduced to local-search simply to save Bridge media; Bridge manages its opt-in folder and local-search only indexes it.
