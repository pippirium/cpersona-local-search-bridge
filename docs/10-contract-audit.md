# 10 — Contract audit (B-01)

Audit of the two providers' **actual** interfaces, done by reading the code at the pinned revisions below and by calling the live loopback API. This document is the evidence base for the contracts in 02/04 and for LS-01. It records **what the providers do today**, not what the Bridge will do. Fixtures under `fixtures/contracts/` are synthetic reproductions of these shapes; `tests/contract/` asserts the Bridge-facing model of them.

## Pinned revisions (measured 2026-10-26)

| Repository | Revision | State |
| --- | --- | --- |
| pippirium/local-search (`origin/main`) | `31f31048fb8edeedf0f94d295bc0c9471000920a` | == local HEAD; version 0.1.1 |
| pippirium/CPersona (fork `master`, local checkout) | `7821840f114866673022edfc5fe4b21a9b08a5e5` | `cpersona.__version__ = 2.6.13a2` |
| bridge | this PR's base `main` (`05ae17d`) | planning docs only |

live-search listener check at audit time: **127.0.0.1:18765 was not listening** (server stopped). No live call was made; everything below is from the pinned source. This is recorded so no claim of a live probe is implied.

## local-search HTTP API (verified against source)

### `POST /api/search` (`backend/src/local_search/api/search.py`, `api/models.py`)

Request (`SearchRequest`, `strict + extra="forbid"` — unknown keys → FastAPI 422 → wrapped as `400 invalid_request`):

| Field | Type | Notes |
| --- | --- | --- |
| `q` | string \| null | query text; engine enforces length (`query_empty`, `query_too_long` at 500 chars) |
| `like` | string \| null | hit id of a "similar to this" search |
| `search_id` | string \| null | paginate/re-filter a previous search; **exclusive** with q/like |
| `tab` | `"all"｜"text"｜"image"｜"video"` | default `"all"` |
| `folder_id` | int \| null | **server-side folder filter — see LS-01 findings** |
| `days` | `7｜30｜365` \| null | |
| `sort` | `"relevance"｜"newest"｜"name"` | default relevance |
| `cursor` | string \| null | pagination |
| `page_size` | `20｜50｜100` \| null | **only these three values** — the plan's "limit ≤20" must round up to 20 |

Response (`SearchResponse`): `search_id`, `generation` (int \| null), `took_ms`, `query` (summary — **echoes the query text**, see privacy note), `fusion` (`rrf｜score`), `counts{all,text,image,video}`, `counts_capped`, `items[]`, `next_cursor`, `remaining`, `empty_reason` (`no_index｜below_threshold｜filtered｜tab_empty` \| null), `unfiltered_all`.

`items[]` (`ResultItem`): `file_id` (int), `kind` (`text｜image｜video`), `name`, `folder_id`, `folder` (display name), `rel_dir`, `mtime`, `size`, `score`, `rank_score`, `hit_count`, `hits[]`, `thumb`, `available` (bool), `duration_sec`, `width`, `height`, `taken_at`, `pages`. **No absolute path, no content hash** in search items.

`hits[]` (`Hit`): `id` (string hit id), `score`, `page`, `heading`, `excerpt` (may contain personal text — do not log), `start_sec`, `end_sec`, `thumb`.

### `GET /api/files/{file_id}` → `FileDetail`

`file_id` validated `ge=1, le=2^63-1` (bad → 400). Fields: `file_id`, `kind` (image/video/text only; ledger kind `other` → 404), `name`, **`path` (absolute path — Bridge must never echo or log it)**, `folder_id`, `folder` (display name), `rel_dir`, `size`, `mtime`, `status` (`pending｜indexed｜failed｜skipped｜missing`), `available` (bool), `thumb`, `meta` (`{width,height,taken_at}` for images), `index` (`{unit,count,...}`), `hits` (empty without `search_id`), `contexts` (empty for images).

### `GET /api/files/{file_id}/content` (and HEAD)

Original bytes. `Content-Type` derived from extension (`originals.content_type`; images are `image/jpeg|png|webp|gif|heic|heif`, not extensionless guesses). `Accept-Ranges: bytes`, `Range` → 206/416, `Cache-Control: no-store`, `Content-Security-Policy: sandbox`. Errors: `404 not_found` (unsearchable/unsupported kind), `409 file_unavailable` (missing/disconnected). **No redirect is ever issued.**

### `GET /api/status` → `Status`

`version`, `model{id,revision,config,device,dtype,state,progress,memory_mb,error}`, `index{generation,int files/chunks/segments/vectors, index_bytes, thumbs_bytes, updated_at, mismatch, rebuilding, deriving_dims}`, `ingest{state,done,total,eta_sec}`, `capabilities{...}`. `index.generation` is the **active index generation number**; `index.mismatch` is `"model"｜"params"｜null`. **No content hash of individual files.**

### Folder registration (relevant to M2, verified present)

`GET /api/folders` → `Folder[]` (`id, path, name, kinds, exclude, watch, status, progress, counts, error, added_at, scanned_at`); `POST /api/folders` (create+scan), `POST /api/folders/validate`, `POST /api/folders/{id}/rescan`, `PATCH`, `DELETE`. This confirms the M2 plan's premise (copy into a pre-registered watched folder) is implementable **without any local-search change**. `Folder.path` is absolute — never log it.

**Correction (B-13 live audit, pinned `31f3104`):** the earlier note for a folder inventory route `GET /api/files?folder_id=&limit=` was wrong — **that route does not exist**. The audited inventory route is `GET /api/files/recent?limit=N` → `ResultItem[]`, newest-first, **server-capped at limit=100 and NOT paged, with no server-side folder filter** (the Bridge filters by `folder_id` client-side in `folder_entries()`). Consequence recorded in reconcile: ambiguity checks for pending resolution are sound only within the recent page; a full raw page (before folder filtering) is treated as ambiguous (`page_full`) and never binds. `POST/PATCH/DELETE /api/folders*` stay OUT of the Bridge's call set (read-only from the provider; folder registration is operator-side).

### Error envelope (all endpoints)

`{"error": {"code": str, "message": str, "field": str|null}}`. Codes relevant to the Bridge: `invalid_request`(400), `query_empty/query_too_long`(400), `forbidden_host`(403), `not_found`(404), `file_unavailable`(409), `index_mismatch`(409), `rebuild_running`(409), `search_expired`(410), `model_loading`(503), `internal_error`(500), plus media-template codes (`image_too_large` 413 etc.). Messages are fixed Japanese strings; templated codes interpolate `{name}`, `{size}`, `{duration}` — `{name}` can carry a personal file name: **do not log provider error messages verbatim**.

### Guard (`api/guard.py`)

`Host` must equal `127.0.0.1:<port>` or `localhost:<port>`; `Origin`, if present, must be in the allowed set; repeated Host/Origin/Sec-Fetch-Site headers are refused. **A request with neither `Origin` nor `Sec-Fetch-Site` passes** — a plain stdio HTTP client sends neither, so the Bridge's client works unmodified and must keep sending neither (no browser-like headers). The Bridge must set `Host: 127.0.0.1:18765` explicitly (configure the base URL as a literal IPv4 loopback).

## CPersona memory identifiers (verified against source 2.6.13a2)

- `store` returns `{"ok": true, "result": "stored", "id": <int>, "embedded": bool}` — `mem_id` is a **SQLite INTEGER rowid**. `result` ∈ `stored｜skipped｜rejected`; pause mode adds `persisted:false, id:"no-persist"`.
- `recall` messages and `reconstruct` items carry **`ref` / `head_ref` strings** spelled `mem:<id>`, `ep:<id>` (`cpersona/recall_trace.py:ref_of`, `reconstruct`), plus `profile` for the synthetic id `-1`.
- Episodes are a **separate table** (`episodes`) whose ids can collide numerically with memories — `mem:`/`ep:` is not merely cosmetic.
- Scope is `(agent_id, project_id, channel)` (CPersona `isolation.py`): `agent_id` is exact-match hard isolation; `project_id` uses γ semantics (bucket ∪ global); `channel` `X` matches `X ∪ ''`. These are **record selectors, not authentication** (CPersona `acl.py` resolves principals only when configured; agent_id is self-declared).
- **No server-instance identifier exists in CPersona responses.** `_meta.server_version` reports the library version, not an instance. Therefore the Bridge's `source_instance_id` must be **operator-configured** (default `""` for a single local installation), never derived from a provider response.

### memory_ref decision (for B-02)

`memory_ref` is an **opaque string** exactly as the provider spells it: `mem:<int>` or `ep:<int>` — pattern `(mem|ep):[1-9][0-9]{0,17}` (rowids fit SQLite's 2^63−1), total length ≤128, empty rejected; no leading zeros; `profile` (id −1) is synthetic and not attachable. The Bridge stores it as text, never parses the numeric part as an identity, and never assumes INTEGER. Both prefixes are accepted because both are real memory records; the prefix keeps numeric collisions between the two CPersona tables from conflating different memories. (Pattern is enforced by `tests/contract/test_cpersona_memory_ref.py` and becomes the tool-level validation in B-04/B-07.)

## Index generation & file identity (feeds LS-01 and B-12)

Verified at `31f3104`:

1. **`file_id` is durable across index rebuilds.** `db/building_files.py:copy_building_to_files` **updates** the existing `files` rows (`b.file_id = files.id`) at the generation switch; rebuild never deletes/reinserts ledger rows. Row identity is `id`; uniqueness is `(folder_id, rel_path)`.
2. **File moves preserve `file_id`** (`ingest/scan.py` move detection: same kind+size+`quick_hash` at a new path rewrites `rel_path` on the same row).
3. **Deletion removes the row** (`ingest/changes.py` folder removal deletes rows) → `file_id` gone → later re-adding the same path yields a **new id**; and SQLite rowid reuse after deletions makes a stale numeric id resolve to **a different file**. ⇒ stale locators must be revalidated by **identity evidence, never by numeric id alone**.
4. **Identity evidence available today:** `GET /api/files/{id}` gives `kind`, `size`, `mtime`, `rel_dir`, `folder_id`, `status`, `available`; `GET /api/status` gives `generation` and `mismatch`. **No endpoint exposes a content hash** (`quick_hash` = xxh3-64 of size + first/last 1 MiB exists in the ledger but is not served). The Bridge can hash bytes itself **only through an explicit content fetch** (which M1 reserves for `get_asset`).
5. **Search-time filtering:** `folder_id` narrows candidates server-side. There is **no tag/asset-id filter**. ⇒ scoped search by post-filter remains partial-coverage by design (B-08's coverage flag stands); M2's managed folder enables exact `folder_id` coverage for managed assets.

## Compatibility table (Bridge-facing)

| Bridge need | Provider support today | Gap / handling |
| --- | --- | --- |
| stable asset identity across rebuild | `file_id` durable on rebuild & move; new id after delete | mark `stale`/`missing`, never silent remap (D-08) |
| identity revalidation without fetching bytes | `files/{id}` kind+size+mtime, `status.generation` | full-byte SHA only at explicit fetch; M2 may add opt-in probe |
| text→image candidates | `POST /api/search tab=image` | page_size ∈ {20,50,100}; scores are provider-internal |
| scope enforcement | Bridge SQLite only; provider has no per-link filter | post-filter + `search_coverage=partial` |
| original bytes with caps | `content` supports `Range` | fetch `bytes=0..max+1` to cap + detect oversize |
| health / index compat | `GET /api/status` (`generation`, `mismatch`, `model.state`) | 409 `index_mismatch` on search is authoritative |
| image fetch limits | `image_too_large` is **attachment-only** (413 on search/media); `/content` serves any size | Bridge enforces its own byte cap |
| memory identity | `mem:<int>` / `ep:<int>` refs | opaque string; no numeric parsing |
| instance identity | none in CPersona responses | operator-configured `source_instance_id` |

## Privacy notes binding on the implementation

- Never log/echo: `FileDetail.path`, `Folder.path`, `SearchResponse.query.text`, `Hit.excerpt`, provider error `message` interpolation (`{name}`), image bytes, MCP payload bodies.
- `Cache-Control: no-store` / CSP sandbox headers come from the provider; the Bridge must not persist fetched bytes to disk in M1.

## Corrections to earlier planning docs (this PR)

1. 02 §"External API baseline": 409 `index_mismatch` is emitted by **search** endpoints only, not by `GET /api/status` (status answers 200 and reports `index.mismatch`). File-level staleness surfaces as `409 file_unavailable`/`404 not_found`.
2. 04 §examples: `page_size` is restricted to `{20,50,100}`; `search_assets` limit ≤20 maps to `page_size=20` exactly.
3. 04 HTTP adapter rules: a loopback request with no `Origin`/`Sec-Fetch-Site` is required by the guard; the client must keep that property.
4. 03: `memory_ref` shape is now known (`mem:<int>`/`ep:<int>`); `source_instance_id` is configuration, not a provider field.

## Not verified (honest limits of this audit)

- No live HTTP probe (server stopped at audit time). Shapes come from pinned source + its pydantic models; contract fixtures mirror them and CI tests the Bridge model against the fixtures, not against the provider.
- `thumb` URL prefix (`api/search.py:THUMB_URL`) not consumed by M1; excluded from fixtures.
- CPersona MCP wire-level sampling was done from source, not from a live MCP session against the production DB (forbidden).

### Inventory adapter completeness (B-06/B-11, #42)

`ProviderPort.folder_entries` returns `FolderInventory(items, raw_count, truncated)`. `items` are folder-filtered but raw_count/truncated describe the unfiltered HTTP page. Requested limit is clamped to 1..100; a response filling that effective limit is conservatively truncated. Resolver checks truncated before candidate matching, including when no managed items survive filtering, and reports page_full without binding. No pagination or server folder filter is assumed. FakeProvider models the same global-page boundary. Evaluation/smoke fixture discovery consumes items without claiming an exhaustive inventory.
