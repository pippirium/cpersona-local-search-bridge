# Bridge evaluation report (B-14)

- bridge `2112321803e5` / provider `0.1.1` / embedder `google/embeddinggemma-2` / generation `1`
- executed 2026-10-26T06:42:59.921739+00:00; warmup 2

| task | status | NDCG@10 | Recall@10 | Success@10 | MRR@10 | p50 | p95 | errors |
|---|---|---|---|---|---|---|---|---|
| text_to_image | measured | 1.0 | 1.0 | 1.0 | 1.0 | 51.3 | 62.4 | 0 |
| memory_to_image | measured | 1.0 | 1.0 | 1.0 | 1.0 | 0.7 | 0.7 | 0 |
| image_to_image | measured | 1.0 | 1.0 | 1.0 | 1.0 | 885.6 | 901.6 | 0 |

## Limitations / unmeasured

- qrels are by construction (generated fixtures + declared links), so scores measure plumbing correctness, NOT semantic embedding quality
- single model/revision; no paired comparison performed
- no CPersona server involved: memory_to_image runs against the Bridge's own link table with synthetic mem: refs
- fixtures were swept (--keep was not passed); rerun regenerates them deterministically

Per-query detail: `result.json` (ranks, grades, errors, misranked_into_top10).
