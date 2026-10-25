# 05 — Delivery plan (M0–M3)

This file is the project-level authoritative delivery order. Work items with detailed acceptance tests live in [06-tickets.md](06-tickets.md); runtime contracts live in 01–04. This is a **plan**, not an implementation status report.

## Milestone map
| Phase | Intent | Work items | Evidence and exit gate |
| --- | --- | --- | --- |
| M0 Design | Freeze minimal contracts, failure handling, trust model | B-01 to B-03, LS-01 investigation | Signed-off decisions and contract fixtures, no ambiguity on identity or allowed paths |
| M1 Reference MVP | Link existing indexed images to text memories, find references, explicitly fetch images | B-04 to B-09 | Synthetic integration path and negative tests, no CPersona/local-search DB mutation |
| M2 Operational integration | Optional managed import, indexing readiness, reconciliation, operator-safe CPersona handoff | B-10 to B-12; CP-01 if needed | Restart/rename/unplugged index/GC tests and human-triggered image fetch |
| M3 Evidence and release | Full three-process integration, offline evaluation, documentation and independent embedding comparison | B-13 to B-15, CP-02 | Replayable offline results, runbook exercise and approved release checklist |

## Dependencies (DAG)
~~~mermaid
flowchart LR
  B01[B-01 contracts] --> B04[B-04 MCP host]
  B01 --> B06[B-06 HTTP client]
  B01 --> LS01[LS-01 stable locator investigation]
  B02[B-02 IDs+schema] --> B05[B-05 SQLite]
  B03[B-03 security] --> B04
  B04 --> B07[B-07 link/unlink]
  B05 --> B07
  B06 --> B07
  B07 --> B08[B-08 search/list]
  B08 --> B09[B-09 explicit get]
  B09 --> MVP[M1 gate]
  LS01 --> B12[B-12 reconciliation]
  MVP --> B10[B-10 optional import]
  B10 --> B11[B-11 indexing readiness]
  B11 --> B12
  B12 --> CP01[CP-01 optional CPersona handoff]
  B12 --> B13[B-13 integration tests]
  CP01 --> B13
  B13 --> B14[B-14 crossmodal evaluation]
  B13 --> B15[B-15 runbook/release]
  B01 --> CP02[CP-02 embedding A/B track]
~~~

LS-01 is an **investigation first**. Provider modifications are requested in a separate local-search PR only if missing capabilities are proven. CP-01 may finish as adapter-only documentation without changes to CPersona. CP-02 is parallel and must not block basic asset linking.

## Recommended delivery order
1. Agree on an actual CPersona memory identifier shape and source_instance_id, with sample payloads.
2. Resolve whether a provider file can be conclusively identified across local-search index changes. Mark unknown identifiers stale until resolved; never silently reuse them.
3. Create a fake local-search server and a temporary SQLite before implementing any model integration.
4. Implement scoped links and image references; reject cross-scope and malicious payloads.
5. Explicit get_asset only after link authorization and provider revalidation; no implicit media in recall.
6. Add managed import and readiness only after the indexed-file MVP is accepted.
7. Run privacy, restart, restore and retrieval regression tests; measure real model performance in isolated fixtures.

## Pull request and commit plan
- One ticket per development PR by default; isolate related cross-repository changes to their owning repository.
- Each logically independent implementation step must have a descriptive commit body explaining why, limitations, verification and Co-authored-by: Codex <codex@openai.com>.
- PR templates need Scope, Linked task IDs, Test commands/results, Safety review, Compatibility, Rollback and Remaining risks.
- Only merge after review/authorized request; use Closes #N only when that PR actually completes issue N.
- For this initial documentation PR: create base main with a minimal README, use a separate docs branch, add separate commits per document, then squash merge to main as requested. The PR history preserves individual preparation commits even though main gets one squashed commit.

## Acceptance checklist at each gate
- All planned behaviors have positive, negative and boundary tests; document skipped real-model tests.
- Existing CPersona text recall, local-search file read-only behavior and loopback security remain unchanged.
- No private corpora, absolute machine paths or real credentials exist in Git history or CI artifacts.
- Benchmarks specify source versions, model revision, device, precision, embeddings, dataset, cutoffs and per-query outputs.
- Graceful dependency errors never mutate/misattribute source assets or erase stored memory.
- Only approved, local repositories receive PRs: never upstream Cloto-dev/CPersona.

## Coordination
The Bridge repository is the **cross-project source of truth** for the roadmap and dependency ordering; GitHub Issues here track the entire workload. LS-/CP- prefixed tickets have execution ownership in their respective repos when/if separately approved. GitHub Projects can group issues across repos later, but creating a Project is not required for the MVP.
