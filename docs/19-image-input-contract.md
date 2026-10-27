# IMG-01 — Image input and association contract

Status: design contract for #55; these tools are not implemented by this document.
EmbeddingGemma 2 remains the provider model. The Bridge uses stdio, and neither
provider database is written by the Bridge. CPersona owns memory text;
local-search owns its index and embeddings; the Bridge owns managed copies,
metadata and associations. The trusted local operator controls process access,
the managed root, staging root and discovery folder allowlist. Scope is an
exact record selector, **not authentication**. Approval is a trusted-host
workflow, not a security assertion accepted from a tool argument.

## Host capabilities and explicit actions

| Host capability | Operator/user action | Bridge operation |
| --- | --- | --- |
| Can send bounded image data as a tool string | User selects the image and confirms registration | `ingest_image` |
| Cannot send image data | Operator uses the local staging helper on an explicitly selected image under an operator-configured input root; the helper returns an opaque staging ID | `import_staged_image` |
| Neither path is available | No import occurs; host must provide a supported local handoff | None |

Neither capability is presumed merely because the host displays an attachment.
No MCP tool accepts a filesystem path, URI or URL. The staging helper is a
local operator action, not an MCP arbitrary-file-reading service. A user must
explicitly approve an association before the host calls the link operation;
a high similarity score is not approval. The Bridge cannot independently verify
approval or memory existence: the host must obtain `memory_ref` from the real
CPersona response, and the Bridge validates only the established syntax.

## Common wire rules

All scope-aware tools take required nonempty string `agent_id` and optional
`source_instance_id`, `project_id`, `channel` strings defaulting to `""`;
normalization is exactly D-16. IDs are lowercase UUIDv4 strings except for
opaque staging/candidate/cursor tokens. JSON fields are case sensitive. The
success envelope is `{"ok":true,...}`; errors are
`{"ok":false,"error":{"code":...}}` with no paths, payloads or foreign
scope information. Typed MCP results may include unused success fields as `null`
on errors and `error: null` on success; no actual partial result is returned.
Unknown and inaccessible IDs have the same
`asset_not_found` result. Unsupported image type uses `unsupported_media`,
oversize uses `media_too_large`, malformed arguments use `invalid_request`,
dependency and index faults use the existing codes in docs/04. Storage full
uses `storage_full`; other durable write faults use `storage_error`. Token
expiry or invalidation uses `invalid_request` without disclosing existence.
Existing error fields and semantics are unchanged for the nine existing tools.

A `client_request_id` is an optional nonempty UTF-8 string of at most 128
bytes, with control characters prohibited. It is an idempotency key bound to
operation, exact scope and a fingerprint of all effectful arguments. Reuse with
identical arguments returns the prior outcome; reuse with different arguments
returns `invalid_request`. The durable result is committed atomically with the
effect, so an interrupted reply can be retried. Request keys and hashes are
never logged. Omitting a key does not disable content deduplication or link
uniqueness. No failure is remembered as a successful effect.

## Proposed MCP tools

All request fields below are in addition to scope. Optional fields may be
omitted. Responses below describe success; on failure return the common error
envelope and no partial result. JSON `null` is accepted only where stated.

| Tool | Request | Success response |
| --- | --- | --- |
| `ingest_image` | `image_base64: string`, optional `client_request_id` | `asset_id`, `ingest_status`, `deduplicated: bool`, `media_type`, `size_bytes` |
| `import_staged_image` | `staging_id: string`, optional `client_request_id` | same as `ingest_image` |
| `get_ingest_status` | `asset_id` | `asset_id`, `status`, `indexed: bool`, `retryable: bool`, optional `reason_code`, optional `checked_at` |
| `discover_assets` | `query: string`, optional `folder_id: integer`, `limit: integer` default 10, optional `cursor: string` | `candidates`, `search_coverage: exact/partial`, optional `next_cursor`, `warnings: string[]` |
| `search_assets_by_image` | exactly one of `query_asset_id` and `image_base64`; optional `folder_id`, `limit` default 10 | `results`, `search_coverage`, optional `provider_warning` |
| `link_registered_asset` | `memory_ref`, exactly one of `asset_id` and `candidate_token`, optional `client_request_id` | `asset_id`, `memory_ref`, `link_status`, `already_linked: bool` |
| `set_asset_metadata` | `asset_id`, `metadata_patch: object` | `asset_id`, `metadata` |
| `get_asset_metadata` | `asset_id` | `asset_id`, `metadata` |

`ingest_status` returned by ingestion is the persisted locator state. Status
lookup reports an effective read-only observation: it can report stale, missing
or incompatible for newly observed conflicts without changing the stored locator.
Neither result is a claim that a file is retrievable. The `indexed` field is true only for `ready`; an explicit fetch
still revalidates identity. `checked_at` is the last actual verification time,
not the time of a read. `reason_code` is a non-sensitive enum:
`waiting_for_index`, `page_full`, `folder_not_watched`, `provider_unavailable`,
`index_incompatible`, `identity_mismatch`, `file_missing`, or `unknown`.
A status lookup is read-only and never runs an operator-wide resolver. It makes
at most one status, one folder, and one detail or bounded recent-page logical
request (each GET may retry once on connection failure);
managed copy hash verification reads at most the configured 8 MB limit locally. An
operator explicitly invokes `resolve_pending_assets`; transient provider
errors must not create a false ready state. `pending` is retryable; `missing`
or `stale` are retryable only through explicit revalidation; `incompatible`
requires operator intervention. A failed initial ingest returns an error and
has no asset row; `received` is an in-flight state, not a committed locator.

`discover_assets` uses only operator-configured folder IDs and a server-side
folder filter for each provider request. An omitted folder searches the
configured set (maximum 16 folders); a requested ID outside it yields `asset_not_found` before a
provider call. Return at most 20 candidates per call; a candidate is
`{candidate_token, kind, label, score, status}` where kind is `image`, label
is the provider name permitted by the allowlist, and score/status may be null.
No other scope's asset ID, memory ref, count or thumbnail is disclosed.
Exclude assets owned exclusively by another scope even if their provider file
is in an allowed folder. A token is an opaque cryptographically random,
handle (256 bits of randomness, expires after five minutes) bound to the exact scope, folder, provider instance,
generation and file identity evidence. It grants no authentication. Selection
rechecks live evidence and fails closed on changed/missing/ambiguous identity;
a token is not a permanent locator. Cursors are opaque, short-lived and bound
to exact scope, query, folder allowlist and provider generation and expire after five minutes; invalid or
expired cursors are rejected. Fetch at most five provider pages per request;
`partial` means a bound or result limit prevented exhaustion, never a promise
about semantic recall. Merge per-folder results by score with stable tie
breaking, and do not expose provider-wide counts. No automatic link is made.

`search_assets_by_image` applies the same limits and scope intersection as
`search_assets`, and returns only linked results. For an asset query, owner or
link membership is checked before a bounded identity-verified internal byte
read. This read is permitted only as part of the explicitly requested image
search; no image content is returned in the response or silently persisted.
The provider performs embedding. Empty/mixed inputs are invalid. A provider
conflict or timeout is a typed failure, never an empty successful result.

`link_registered_asset` accepts a pending owned asset or a selected candidate;
its successful link does not guarantee availability. The candidate route must
revalidate evidence before using the existing safe registration/link pathway.
A unique link is idempotent; asset and link changes are transactional. Existing
`unlink_asset` removes a link without deleting source or managed files.
`get_asset` retains its existing linked-scope, explicit-byte-fetch gates;
ownership alone does not grant it. Existing `search_assets` and
`list_memory_assets` remain link-only, and `register_asset` retains its indexed
source and memory-ref requirements. Normal CPersona recall/reconstruct does
not call `get_asset` or include image bytes.

## Image validation and managed copies

The operator-configured maximum is at most 8,000,000 raw bytes and 40,000,000
pixels, and may only be lowered. Standard padded RFC 4648 Base64 with no
whitespace is required. Before decoding, reject more than
`4 * ceil(max_fetch_bytes / 3)` ASCII characters, then strictly decode and
check actual nonzero byte length. Sniff magic and fully validate a single image
with bounded decoding and decompression-bomb checks. Animation is limited to
256 frames and the total decoded canvas pixels across frames to the configured
pixel cap. Accept JPEG, PNG, WebP
and GIF; HEIC/HEIF are explicitly `unsupported_media`. Ignore declared MIME
as an authority. Preserve exact bytes, including EXIF and GIF animation; do not
rotate, strip, log or interpret metadata. SHA-256 is computed on exact bytes.

Write under configured managed root as `<sha256-prefix>/<sha256>.<extension>`;
use exclusive temporary creation, bounded writes, fsync and atomic no-clobber
publication, with directory-descriptor based no-follow checks for root and
components. Reject symlinks, unexpected hard links and directory replacement;
verify an existing final file by bytes/hash before reuse. Never modify the
source. A crash before DB commit can leave a managed orphan; audit reports it,
and cleanup is a separate operator-authorized operation. Disk-full or failed
commit returns an error and no link. Existing files are never overwritten.
Deduplication response is scoped: same-scope same-content can reuse an asset;
a different scope must not learn that another scope registered the content.
Global physical copy sharing must not imply shared ownership or reveal a
cross-scope dedup flag. Before ready/fetch/link, revalidate content/identity;
size, filename or provider rowid alone are insufficient. Provider rebuild,
rowid reuse, deletion or changed bytes never silently select another image.

## Ownership, migration, staging and retention

Versioned migration v2 adds an `asset_owners` relation keyed by the four scope
columns and asset ID, plus immutable registration time and evidence fields;
owner assignment, pending locator creation and idempotency result are one
transaction. A shared physical content copy may have separate scoped logical
asset IDs and owners; provider locator uniqueness must be redesigned carefully
so a file ID can resolve to separate scoped assets without cross-scope reads.
Do not infer ownership for historical linked assets: existing link-based access
continues unchanged, and ownership is granted only by an explicit new ingest.
A later feature migration adds staging/request tables and metadata as needed.
Use additive migrations with schema-version checks, SQLite online backup before
upgrade, and documented restore of both DB and managed root. Rolling back to an
older binary cannot interpret v2 and must fail closed; restore a matching
pre-upgrade backup instead of dropping owner data. Test upgrades from v1 with
links and pending locators, foreign keys, concurrent retries, and restore.

A staged entry has a 256-bit random opaque token, exact owner scope, content hash,
size and filesystem identity captured after an atomic completed write, an
expiry (15 minutes from creation), and an atomic consume/result record. The local helper accepts a relative selection only under the configured input
root; it requires explicit `--confirm`. The helper may read only an
operator-configured input root, rejects symlinks, hardlinks, traversal and
replacement using descriptor-based no-follow checks, and copies rather than
moves the original. Staging and managed roots are private and separate. Import
checks expiry, scope, staging file device/inode/mtime, hardlink count and hash again,
reuses the same byte gates,
and commits consume together with its durable result. Retrying a successful
consume returns the same result only to the same scope; parallel consumers
cannot create inconsistent outcomes. Failure before commit leaves the entry
retryable until expiry. Expired temporary entries remain on disk until the operator explicitly runs
the bounded staging GC. It removes only ledger-tracked expired files whose
identity still matches, reports replaced files as blocked, and leaves foreign
files untouched. A successful token replay is allowed until expiry. No original file
is deleted. Cancellation before commit creates no association.

## Metadata patch contract

Metadata is scoped to an owner or existing linked scope and never exposes
another scope. Allowed nullable fields: `user_caption` (max 1000 UTF-8 bytes),
`caption_origin` (`user` or `external_source`), `source_kind`
(`chat_attachment`, `local_selection`, `managed_import`, `external_source`),
`captured_at`, `recorded_at` (RFC 3339 timestamps with explicit timezone),
`source_message_ref` (opaque safe identifier, max 128 bytes), and
`consent_record` (one of `explicit_user`, `operator_confirmed`). Omitted means
unchanged; null removes a field; unknown fields fail the entire patch. Reject
control characters, URLs and filesystem paths in free-text fields. Caption
requires its origin; clearing the caption also clears the origin. These are
user-supplied labels, not identity, proof of consent or authoritative EXIF.
No model-generated caption, OCR or search indexing of captions is introduced.
Deleting a metadata field deletes the current value; no revision history is
promised. Unlink does not erase owner metadata. File updates never rewrite it.

## Examples

```json
{"tool":"ingest_image","arguments":{"agent_id":"example","image_base64":"<bounded-base64>"}}
```
```json
{"tool":"link_registered_asset","arguments":{"agent_id":"example","asset_id":"b519a9d4-9f85-469c-b603-c8ce2c6a54c3","memory_ref":"mem:42"}}
```

The placeholders are schematic, not fixture data. The host supplies a real
CPersona-returned ref and explicit approval before invoking the second action.
Test each new API with isolated temp roots/SQLite, fake provider failures,
separate scopes, size/MIME errors, same-size replacement, concurrent retries,
rebuild/rowid reuse and sanitized logging; #63 owns isolated real three-process
acceptance. Neither synthetic scores nor a fake embedder establish retrieval
quality.
