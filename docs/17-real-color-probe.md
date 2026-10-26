# B-14 — Real EmbeddingGemma 2 color probe (2026-10-26)

Artifacts: [report](../reports/real-colors-20261026/report.md), [per-query JSON](../reports/real-colors-20261026/result.json), [run context](../reports/real-colors-20261026/run-context.json), [predeclared manifest](../eval/manifest.colors.json).

This run used **real** `google/embeddinggemma-2`, revision `914f7f89142e33e77833254d9c9b90c3cef7303b`, text_image configuration, MPS/bf16. local-search source was `31f31048fb8edeedf0f94d295bc0c9471000920a` (0.1.1); Bridge/manifest commit was `2112321803e5`. Existing complete model weights were filesystem-cloned into a new isolated provider directory before startup; no user's existing index was used or modified. Offline/telemetry-disabled flags were set; no generation LLM was called. Source/provider processes and files used by the test were dedicated to this run.

## Reproduction

Start the pinned local-search with real weights and a fresh dedicated data directory, loopback-only. Register a disposable watched image folder through its existing API (operator step). From Bridge root:

```sh
uv run python -m eval.run \
  --manifest eval/manifest.colors.json \
  --provider http://127.0.0.1:<TEST-PORT> \
  --watched-dir <DISPOSABLE-WATCHED-DIR> \
  --managed-folder-id <TEST-FOLDER-ID> \
  --data-dir <DISPOSABLE-BRIDGE-DIR> \
  --out <REPORT-DIR> --warmup 2
```

This command ran with exit 0. The actual port and private paths are intentionally absent from committed artifacts. Fixture generation and sweep were performed by the CLI. The dedicated provider was terminated after the run. JSON-schema validation of result.json, the manifest SHA-256, 12 per-query records and zero query errors/missing qrels were checked with the installed jsonschema validator. The pre-existing schema file's non-JSON leading comment was converted to `$comment` to make standard validation possible; its schema version and field semantics are unchanged.

## Measured results

| Task | Queries | NDCG@10 / Recall@10 / Success@10 / MRR@10 | p50 / p95 (ms) | Errors |
| --- | ---: | --- | --- | ---: |
| Japanese/English text → image | 6 | 1.0 / 1.0 / 1.0 / 1.0 | 51.3 / 62.4 | 0 |
| Memory association → image | 3 | 1.0 / 1.0 / 1.0 / 1.0 | 0.7 / 0.7 | 0 |
| Exact image → image | 3 | 1.0 / 1.0 / 1.0 / 1.0 | 885.6 / 901.6 | 0 |

The model readiness snapshot reported `memory_mb=2497.0`; this is a provider status observation, not a measured peak RSS/VRAM claim. Provider/index/model fingerprints and manifest hash are retained in run-context.json. Model inference/search ran only in the provider; Bridge loaded no second model.

## Limits and unmeasured items

- Three solid RGB images are a controlled synthetic probe with relevance by construction. Perfect results here do **not** establish general semantic-quality accuracy, real-world image recall, OCR ability or model superiority.
- Six text queries and three queries per other task are too few for a stable p95 or confidence interval. No paired comparison or bootstrap interval was measured.
- The CLI made two untimed priming calls per task. Its memory priming uses `mem:0`, which the current contract rejects; those calls do not warm the successful memory-list path. Image priming uses the first fixture; cache-hit/miss timings are not separated. These figures are descriptive observations of this run, not warm/cold performance baselines.
- Cold-start/model-load latency, peak RSS, VRAM, index-size footprint and per-stage embedding/ranking/fetch timings were not measured. No external API spend was incurred by the evaluation; a separate network-capture audit was not performed.
- The eval uses in-process Bridge MCP wiring and synthetic memory references, not a CPersona server. The independent real three-process smoke is documented in docs/16.
- CP-02 (Jina/BGE-M3/EmbeddingGemma 2 comparison) remains **unmeasured** and requires its separately authorized fork work. Fresh-Mac installation and release gates remain as recorded in RUNBOOK/RELEASE-CHECKLIST.
