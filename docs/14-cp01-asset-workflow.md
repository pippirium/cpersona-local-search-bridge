# 14 — CP-01: Memory ↔ asset user workflow

Status: **CP-01 accepted with zero CPersona fork changes** (docs/06 CP-01:
"Bridge+Agentの運用で満たせばCPersona本体変更なしとして完了"). This document
is the deliverable: how an Agent drives the two MCP servers today, what a
memory body should record, and the exact evidence trail. It proposes no code
in either upstream; nothing here relaxes an invariant.

## Trust note that shapes everything below

Bridge tool arguments (`agent_id` / `project_id` / `channel` /
`memory_ref`) are **scope, not authentication** (docs/03 §6, AGENTS.md). The
trust boundary is the local operator + the stdio transport, identical to
CPersona's own model. The workflow below does not create, and must not be
described as creating, a per-agent security boundary.

## Workflow A — attach an asset to a memory (the common path)

1. **Get the memory ref from CPersona itself.** Call CPersona
   `recall`/`reconstruct`; each row carries a `ref` — exactly `mem:<int>` or
   `ep:<int>` (docs/10 B-01 audit). The Agent never invents or renumbers
   refs; they are stored verbatim by the Bridge and are matched as strings.
2. **Identify the file.** Note the deliberate ordering: `search_assets`
   answers with *already-linked assets in your scope* (T-02 — an unlinked
   provider hit is invisible to it). So a never-linked file is first named
   by other means — the user points at it, or the Agent passes a
   `provider_file_id` the user supplied — and search_assets then serves as
   the find-by-query path once linking exists.
3. **Link:**
   `link_asset(agent_id, memory_ref, provider_file_id, project_id?, channel?)`
   → stable `asset_id`, `link_status`. Repeating the same file is
   idempotent; the Bridge re-verifies with the provider before writing
   (docs/04 §link_asset). After this, `search_assets` finds it (scoped).
4. **Record the ref in the memory body (recommended, optional):** the
   memory text gets a one-line trailer — the *only* in-body format this
   project blesses:

   ```
   [asset:<uuid4>]
   ```

   Rules: exactly the `asset_id` the Bridge returned, one token per line,
   at the end of the body. Rationale: refs live canonically in the Bridge
   DB (`list_memory_assets` is the query path); the in-body token exists
   only so plain-text CPersona recall can hint "this memory has an asset"
   without the Bridge DB being consulted. `list_memory_assets` remains the
   source of truth; a body token is a hint, never a lookup key, and the
   Bridge never parses memory bodies.

## Workflow B — view an image

Only in response to an explicit request to see the file:
`get_asset(agent_id, asset_id, project_id?, channel?, memory_ref?)`.

- Ordinary CPersona `recall`/`reconstruct` never return image bytes — the
  memory store holds text plus refs only, and the Bridge never feeds
  itself into CPersona (docs/03 §7). `get_asset` is the sole byte path and
  requires an explicit call.
- Scope must repeat exactly; foreign-scope ids answer as unknown (D-16).
- A stale/missing locator answers `asset_unavailable` — the Bridge never
  substitutes another file (docs/11 §4, AGENTS.md invariant).

## Workflow C — durability against provider reindex (when to use register_asset)

`link_asset` binds to the provider's file id, which local-search may
reassign across rebuilds (rowid reuse, docs/13). The Bridge detects reuse
by evidence and goes `stale`/`missing` — it never serves a wrong image, but
the link then needs explicit repair (re-link to the new id). When a memory
must survive reindex without repair, use the managed path instead:

1. Operator pre-registers the Bridge-managed folder in local-search **with
   watch on** (out-of-band; the Bridge never mutates folder state —
   `bridge_health` verifies the premise and warns otherwise).
2. `register_asset(agent_id, memory_ref, provider_file_id, …)` — copies the
   bytes once into the content-addressed managed store (originals
   untouched), verifies MIME/dimensions from real bytes, hashes, links.
   The copy is re-indexed by local-search itself; the Bridge resolves the
   copy's new file id through `resolve_pending_assets` when the watcher
   has indexed it.
3. Body token format is identical (`[asset:<uuid4>]`).

`resolve_pending_assets` and `audit_assets` are operator-maintenance
tools: call `bridge_health` first; run resolve when health reports pendings,
run `audit_assets` (dry-run default) before any repair decision.

## Worked Agent transcript (synthetic; no personal data)

```
user: 記憶 mem:41 に昨日のスシの写真を貼り付けて
agent -> cpersona.recall(query="sushi", …)
          rows: [{ref: "mem:41", …}]              # ref verbatim from CPersona
agent:  (ユーザーが写真 IMG_0042.jpg を指す; local-search file_id=218)
agent -> bridge.link_asset(agent_id="coding", memory_ref="mem:41",
                           provider_file_id=218)
          {ok: true, asset_id: "3f8a…", link_status: "ready"}
agent -> bridge.search_assets(agent_id="coding", query="sushi")
          [{asset_id: "3f8a…", memory_refs: ["mem:41"]}]   # 紐付け後は検索できる
agent -> cpersona.update_memory(memory_id=41, content=…existing text… +
          "\n[asset:3f8a…]")                       # the blessed trailer
agent:  メモリに画像を1件関連付けました（asset_id 3f8a…）。

# later, in another session
user: あの寿司の写真見せて
agent -> cpersona.recall(query="sushi") → body hints [asset:3f8a…]
agent -> bridge.list_memory_assets(agent_id="coding", memory_ref="mem:41")
          → confirms asset 3f8a… status ready (source of truth)
agent -> bridge.get_asset(agent_id="coding", asset_id="3f8a…")
          → ImageContent (explicit request only)
```

## Contract-test anchors (Bridge side, already green)

- refs stored verbatim / rejected patterns: `tests/unit/test_tools_link.py`
- exact-scope invisibility (D-16): `tests/unit/test_tools_get.py`
- stale never substitutes: `tests/unit/test_reconcile.py`, `test_audit.py`
- no bytes without explicit get: `tests/unit/test_tools_get.py` (call-count
  assertions on `file_content`)
- 251-passed suite is the operational sample's regression baseline; CP-01
  changes no code, so it must stay 251.

## What CP-01 explicitly does NOT do

- No CPersona fork code change (none is necessary; the refs, update_memory,
  and recall paths above already exist). If a future need emerges, it needs
  a fresh proposal + user approval; upstream Cloto-dev/CPersona remains off
  limits.
- No automatic body annotation, no background indexer, no auto-display.
- No cross-instance scope magic: multi-instance installs separate via
  `source_instance_id` operator config (docs/03 §6), not detection.
