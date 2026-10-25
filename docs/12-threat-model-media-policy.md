# 12 — B-03: threat model, trust boundary and media-fetch policy (frozen for M1)

Builds on docs/10 (audited provider behavior) and docs/11 (frozen schema). Extends docs/08; where this document adds a concrete limit or decision, it supersedes prose vagueness. Executable negative tests for the pure rules live in `tests/security/`; tool-level tests arrive with B-04..B-09 and B-13.

## 1. Trust boundary (restated as law)

- **Transport:** MCP stdio only, plus HTTP to `http://127.0.0.1:<port>` only. No listener, no remote transport, no proxy. The Bridge must refuse to start if configured with a non-IPv4-loopback base URL (`localhost` names are not accepted as literal Host either — the guard pins `127.0.0.1:<port>`/`localhost:<port>`; the Bridge uses the literal IPv4 form).
- **Principal:** whoever can spawn the Bridge process (same OS user). `agent_id/project_id/channel/source_instance_id` are **record selectors**, never authentication. The Bridge must never claim isolation from a hostile local process, and must never widen visibility beyond exact scope (D-16).
- **Provider as oracle:** local-search's guard passes requests that carry neither `Origin` nor `Sec-Fetch-Site`. This is deliberate for curl-like clients; the Bridge neither adds such headers nor claims the guard authenticates callers. Any local process can already reach the provider API directly; the Bridge adds no new exposure, so it must strictly **not add** one either (no redirects, no extra hosts, no writes).

## 2. Threat table (B-03 acceptance list, with verification mapping)

| # | Threat | Bridge policy (frozen) | Verification |
| --- | --- | --- | --- |
| T-01 | Scope spoofing / accidental cross-scope read | exact-equality scope on every read/write; cross-scope asset_id answers `asset_not_found` with **no echo** of other-scope fields | `tests/security/test_scope_isolation_rules.py` (pattern level) + B-07/B-08 tool tests |
| T-02 | ID guessing / enumeration | `asset_not_found` is identical for "unknown" and "other scope"; UUIDv4 space is unguessable; no counts of other scopes in any error | B-07 negative tests |
| T-03 | Arbitrary file read via path | no tool accepts a path/URL; `get_asset` consumes only `asset_id`; provider itself refuses `..`/symlink/absolute rel_paths (`originals.contained_path`, O_NOFOLLOW) | docs/10; B-09 tool test asserts no path field exists in schemas |
| T-04 | Symlink swap on locator | Bridge never opens provider files itself; provider refuses symlinked paths and non-regular files (verified in source) | audit + B-13 |
| T-05 | Huge image / decompression bomb | see §3 caps; fetch is bounded **before** body read and during streaming; pixel cap enforced by provider-reported `meta.width/height` before fetch when present | `tests/security/test_media_policy_rules.py`; B-09 payload tests |
| T-06 | SSRF / redirect to foreign host | configured base URL must parse to literal `127.0.0.1`; redirects disabled; URL is never caller-influenced; only 4 documented endpoints | `test_base_url_policy.py` |
| T-07 | Provider stopped / slow | connect 2.0s / read 10.0s / total-body 30.0s timeouts; `dependency_unavailable`; **retry at most once, only connection-phase failures, only on idempotent reads**; no retry after any byte received | `test_retry_policy.py` |
| T-08 | Index rebuild / file_id reuse | §5 identity evidence set; mismatch ⇒ `stale`/`incompatible`, never auto-swap; provider `generation` recorded per locator | docs/11 §4–5; B-12 integration suite |
| T-09 | Log leakage of secrets/PII | §6 forbidden-string table; structured errors carry codes + ids only; provider error `message` never logged verbatim (interpolates `{name}`) | B-04 logging tests + a CI sentinel grep (T-09 test below) |
| T-10 | Partial write / crash mid-transaction | SQLite transactions (docs/11 §7); managed copies tmp+fsync+rename (B-10); M1 writes only SQLite | B-05/B-10 |
| T-11 | Oversized response body / lying Content-Length | read at most `max+1` bytes via Range; `Content-Length > max` refuses before body; actual streamed bytes re-checked | `test_media_policy_rules.py` bound arithmetic; B-06 mock suite |
| T-12 | MIME spoofing | accepted kinds are extension-derived by the provider (docs/10 table); Bridge allow-lists `image/jpeg|png|webp|gif` for M1 `get_asset`; `image/heic|heif` are **rejected typed** (`unsupported_media` — MCP image spec doesn't carry them; provider HEIC→JPEG conversion is not auto-invoked in M1) | `test_media_policy_rules.py` |
| T-13 | Memory-ref injection | pattern-validated opaque strings only; never interpolated into SQL (parameters) or paths | contract tests + B-05 parameterized-SQL rule |
| T-14 | Auto image fetch by recall | Bridge has no passive hook into CPersona; `get_asset` is the only media path and requires explicit call + fresh revalidation (docs/04) | B-09/B-13 asserts bytes only after get_asset |
| T-15 | Data directory squatting | Bridge data dir created 0700, outside provider data dirs; startup refuses if path is world-writable or inside either provider's configured data dir | B-04 config tests |

## 3. Media limits (frozen defaults, operator-configurable downward-only in M1)

| Limit | Default | Basis |
| --- | --- | --- |
| `max_fetch_bytes` | **8 MiB** (8 × 10⁶) | local-search's own attachment bound for images is 20 MB (`image_too_large`); MCP image blocks are base64-in-JSON at clients — 8 MiB raw ≈ 10.7 MiB encoded stays inside common client message limits while covering the vast majority of screenshots/photos. Measured client compatibility is re-checked in B-15 (open decision 5 remains partly open by design) |
| `max_pixels` | **40 × 10⁶** (40 MP) | above any realistic phone camera; blocks decompression bombs that report insane dimensions |
| `min_bytes` | 1 | an empty file is `asset_unavailable` (never a "successful" empty image) |
| accepted MIME (get_asset, M1) | `image/jpeg`, `image/png`, `image/webp`, `image/gif` | MCP image content spec; HEIC/HEIF/GIF note below |
| GIF handling | allowed as `image/gif` **static-only in intent**; Bridge does not de-animate in M1; animated GIFs reach the client as-is within the byte cap | recorded limitation |
| HEIC/HEIF | typed `unsupported_media` in M1 (no auto-conversion; provider `/preview` route exists for M2 evaluation) | no silent re-encoding of user originals (D-09/D-02) |
| Response read | Range `bytes=0-{max_fetch_bytes}` request + streamed count; if server ignores Range and sends 200 with more than the cap → abort, `media_too_large` | T-11 |
| Pixels check order | if `FileDetail.meta.width/height` present and product > max_pixels → refuse **before** fetching content | T-05 cheap gate |

Downward-only in M1: raising defaults requires a config change plus this table's re-review; the tool schema never exposes byte caps as arguments (no caller-controlled limits).

## 4. HTTP client policy (extends docs/04)
- Literal IPv4 loopback base URL only; path is always one of the 4 audited routes; no user-supplied path segments beyond validated int `file_id`.
- `follow_redirects=False` (any 3xx ⇒ `dependency_unavailable` + local log code only).
- Client sends no `Origin`, no `Sec-Fetch-Site` (guard compatibility, docs/10) and no custom `Host` beyond what httpx derives from the literal URL.
- Timeouts: connect 2.0 s / read 10.0 s; total content fetch wall cap 30 s.
- Retry: **at most once**, only on connection-establishment failure, only for `GET /api/status`, `GET /api/files/{id}`, and header phase of `GET .../content`; never for `POST /api/search`, never after any response byte.
- Response envelope parse is strict (unknown top-level keys tolerated only where B-01 documents pydantic echo behavior; error bodies must match the envelope or ⇒ `index_incompatible` class handling).

## 5. Identity evidence (unchanged from docs/11 §5, restated for B-03 sign-off)
kind + size + (mtime or generation-unchanged & name/rel_dir) + provider availability flags; hash only from explicit fetch. No auto-substitution, ever (D-08).

## 6. Forbidden in logs, tool responses, fixtures and commits (audit list)
Absolute paths (`FileDetail.path`, `Folder.path`, Bridge data dir, managed roots); query text (`SearchResponse.query.text`); `Hit.excerpt`; provider error `message` strings (code only); file names beyond `last_seen_name` in DB (never in logs); image bytes; base64; memory content; tokens/env values; exception tracebacks containing URLs. Structured log format: `event=<code> asset_id=<uuid> scope_hash=<truncated sha256 of scope tuple>` — **scope tuples are hashed in logs** because agent/project/channel names can themselves be sensitive; the raw tuple lives only in the DB.
The literal grep patterns live only in `bridge_policy.py` (FORBIDDEN_LOG_MARKERS; home-directory prefixes, token prefixes, auth headers, inline data-URI markers — the actual strings are kept out of this document on purpose, because a doc that quotes them would itself trip the scan). The CI sentinel (`tests/security/test_policy_rules.py`) inventories every tracked text file; docs and fixtures are held to the same bar as code.

## 7. Ordinary recall must not auto-fetch (T-14) restated
The Bridge never runs inside CPersona's process, subscribes to its DB, or registers as a provider. CPersona's recall/reconstruct remain untouched. Media bytes appear in a Bridge response **only** as the direct consequence of a `get_asset` call within the same request. B-13 asserts this end-to-end with the real three processes.

## 8. Decisions recorded in docs/09 (this PR)
- **D-18 (Adopted)**: M1 media caps and MIME allow-list as in §3; caps are config-only, never tool arguments; HEIC typed-rejected without auto-conversion.
- **D-19 (Adopted)**: scope tuples are hashed in logs; the forbidden-strings list §6 is the audit contract for logs/responses/fixtures; CI sentinel enforced.
