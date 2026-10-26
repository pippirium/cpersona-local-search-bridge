# 15 — B-13 historical live provider audit log (real HTTP, fake embedder)

Date: 2026-10-26. Provider: local-search `31f31048fb8edeedf0f94d295bc0c9471000920a`
(v0.1.1, the D-14 pin) started **explicitly for this audit** with:

- `--data-dir` = a throwaway directory (never the operator's `~/.local-search`);
- `LOCAL_SEARCH_EMBEDDER=fake` (deterministic embeddings, no model load; the
  index ledger/paths are the production code paths regardless — the embedder
  is the pluggable component);
- loopback only, same Host/Origin guard as production (the Bridge client
  sends no `Origin` — non-browser clients pass the guard as designed).

Bridge side ran fully in-process over MCP stdio-equivalent wiring
(`create_server` + `connected_client`) against the real HTTP provider —
Bridge, CPersona-free of this scope; CPersona involvement in B-13 is the
manual transcript of docs/14 §Worked transcript (recorded as executed by
tool shapes, DB-touching steps left to operator environments).

## Measured behaviors (docs/13 claims tested against reality)

| # | Scenario | Result | Bridge consequence |
|---|---|---|---|
| 1 | Server restart, same data dir | ids 1–4 **unchanged** | restart alone is identity-safe |
| 2 | Delete file → id absent from `/api/files/recent`; detail of old id → **404 `not_found`** | client maps `not_found` → `asset_unavailable` → audit verdict `missing`. A deleted id is **not** silently reassigned at read time | `missing`, never substitution |
| 3 | New file lands in watched folder after the delete | got id **5** — the deleted id 1 was **not** reused (monotonic rowid here) | rowid reuse is possible but NOT observed on plain delete+add; evidence-based stale detection stays as the net (docs/13 wording "may" is right) |
| 4 | Rename in place (`int_1.png` → `RENAMED_int_1.png`) | id **2 kept**, name updated | supports the gen-unchanged name-drift waiver in tools_import (within a generation, scan move-detection keeps ids) |
| 5 | `POST /api/index/rebuild` → generation 1 → **2**; ids after switch: `2,3,5` kept, `copy_a.png` got **6** (was 4) — **generation switch CAN swap ids** | the `page_full` and generation evidence matter exactly here: same-id-different-evidence → `stale` via detail mismatch, never a silent wrong image |
| 6 | `GET /api/folders` | JSON array, `watch` boolean present (premise check works against reality) | resolve_pending premise gate verified live |
| 7 | `GET /api/files/recent` | JSON array of ResultItems; **no folder filter parameter; capped at 100** | client `folder_entries()` filters client-side; reconcile fail-closes `page_full` on a full page (docs/10 correction recorded) |
| 8 | Semantic search for a FILENAME (`q="int_0"`) | **0 items, `empty_reason=below_threshold`** | pending resolution never used search by design — verified correct: filename queries are NOT a discovery path. Inventory route is the join source |
| 9 | Managed full loop (live watcher): register → managed copy → copy bytes appear in watched folder → provider indexes → `resolve_pending_assets` | **bound** with the copy's NEW file id, DB-driven (restart-independent), 30 s deadline met in ~2 s poll steps | B-11 managed loop is real end-to-end |

## Consequences committed alongside this log

- `src/bridge/client.py`: `list_folder` (nonexistent route) →
  `folder_entries()` over `/api/files/recent` with client-side folder filter.
  A unit test kept the wrong route green only because the fake served the
  invented route — that class of bug is exactly why B-13 exists (docs/12
  T-13).
- `src/bridge/reconcile.py`: RECENT_PAGE_MAX=100; a full recent page cannot
  be fully surveyed for same-size siblings → `page_full`, fail closed.
- Historical claim corrected (#44): current `link_asset` accepts generation
  only as an internal argument; the MCP wrapper does not capture it. External
  links created through MCP therefore cannot use the generation edit waiver.
  They still require matching stored name/size/mtime (docs/11). Managed imports
  capture generation separately. This is a conservative runtime limitation,
  not evidence that the originally claimed generation capture ran.
- `tests/integration/test_live_provider_contract.py`: all of the above as
  opt-in tests (skip without `BRIDGE_LIVE_BASE_URL`; never start the
  provider; only synthetic files in an operator-registered test folder).

## What was NOT done live

- Real EmbeddingGemma weights (fake embedder was used; embedding quality is
  out of Bridge scope — docs/07 evaluates the provider, not the adapter).
- CPersona production DB: untouched, as required. CPersona-side steps are
  contract-tested shapes (docs/14), live transcript is an operator run.
- DNS-rebinding/Host guard tests: provider-side property, owned by
  local-search's own test suite; only "Bridge requests pass the guard" was
  exercised (all live calls succeeded with `Host: 127.0.0.1:<port>`).

Current contracts supersede this historical audit: D-21–D-25 preserve trusted evidence, enforce independent fetch gates, bind managed copies by name/shard and retain raw inventory completeness. The recorded provider run predates those patches; their validation is offline synthetic/MockTransport, not a newly executed live audit. A real CPersona three-process run has not been performed.
