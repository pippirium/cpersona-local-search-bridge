# CP-02 — Preregistered dense comparison protocol

Status: Phase 1 (Japanese smoke and SciFact) was measured independently. The full-LMEB Phase 2 conditions below were fixed before its model inference. Jina is complete; BGE-M3 is paused after LongMemEval group 8; Gemma is unrun. See the [2026-10-28 handoff](20-cp02-handoff-status.md) and detailed [CPersona checkpoint report](https://github.com/pippirium/CPersona/tree/feat/cp02-fair-embedding-comparison/docs/research/cp02-full-lmeb-checkpoint-2026-10-28.ja.md). The user permits PR creation in the experimental CPersona fork, but this handoff is branch-push only and creates no PR. This remains a standalone evaluation and does not change the Bridge runtime or provider contracts.

The Gemma-specific run gate and execution steps are in the [EmbeddingGemma 2 experiment plan](21-cp02-gemma2-experiment-plan.md).

## Fixed inputs

| Component | Revision / condition |
| --- | --- |
| CPersona reference PoC | fork `7821840f114866673022edfc5fe4b21a9b08a5e5`, `poc/embedding/sample.json` (10 Japanese synthetic smoke queries) |
| LMEB task | full published `mteb/LMEB_SciFact` test at `432cf48c879c2de792720d5c0af6900312ebe7b6`: 1,748 corpus passages, 188 queries, 366 qrel rows |
| Jina | `jinaai/jina-embeddings-v5-text-nano` @ `8a7f00aac812071b69403df470f1038ec85f8925` |
| BGE-M3 | `BAAI/bge-m3` @ `5617a9f61b028005a4858fdac845db406aefb181` |
| Gemma | `google/embeddinggemma-2` @ `914f7f89142e33e77833254d9c9b90c3cef7303b` |

The LMEB [paper](https://arxiv.org/abs/2603.12572) and [processed dataset](https://huggingface.co/datasets/mteb/LMEB_SciFact) define this as one semantic-memory task. Running it is not a claim to reproduce the entire 22-dataset LMEB aggregate. The Japanese data remains the original PoC smoke set, not a representative Japanese benchmark. Every model receives all identical records, query IDs and qrels of each dataset. No query-selected candidate reduction, reranker or generated answer is used.

## Conditions fixed before measurement

- Isolated standalone evaluation worker per model; no CPersona live database, MCP configuration or existing provider index. Model source is not patched. No generation LLM or external embedding service.
- Primary run: one Mac / MPS / bf16 for all three models. CPU/fp32 is a separate unmeasured condition, never mixed into these results. Fixed batch size 8 for corpus, 1 for timed queries; common max sequence length 512. Report tokenizer-specific truncation and native dimensions rather than assuming identical tokenization.
- Gemma: text-only configuration (vision/audio disabled); Document/SearchQuery prompts. Jina: pinned publisher custom code, retrieval task and document/query prompts. BGE-M3: dense SentenceTransformer encode without query instruction, per [publisher usage](https://huggingface.co/BAAI/bge-m3). No sparse or multi-vector score is added.
- Build fresh normalized document vectors per model and dataset, use cosine/dot-product ranking with stable corpus-order tie breaks. No shared vector cache or mixed-model index. Corpus text is title plus body when title is nonempty, using the same assembly for every model.
- Two untimed document/query priming calls per dataset before corpus encoding and timed query execution. Explicit device synchronization bounds timings. Report model load, corpus encoding, query embedding and ranking separately. No cross-run warm/cold inference from these timings.
- Recall@1/5/10, Success@10, MRR@10 and graded NDCG@10 (`2^grade-1` gain), query counts and missing/error counts. Preserve per-query IDs, ranks and metrics; do not commit corpus/query text or private paths.
- Sample process RSS and MPS driver allocation during inference; record sampling interval and limits. Model download time is excluded. Record package versions, exact model/data revisions, input fingerprints, native dimensions and truncation counts.
- Paired comparison uses identical query IDs, win/tie/loss and seeded bootstrap 95% intervals (seed 1729; 2,000 resamples) for NDCG@10/Recall@10/MRR@10. Report Japanese smoke and LMEB task separately. Do not combine them into a general model-selection verdict.

## Phase 2 — full LMEB

- Source: `KaLM-Embedding/LMEB` at revision `f9d4294be4a24b8c16d6bfb59d56a6fec4bd95c0`; 22 datasets, 193 query tasks and 103 corpus groups. The normalized inputs contain 2,032,230 per-corpus unique records and 159,011 positive-qrel queries. No query is excluded for missing positive qrels.
- Same three fixed model revisions, separate offline worker processes on M1 Pro / 32 GB Mac, MPS/bf16, max sequence length 512, corpus and quality-query batch 8. The pinned LMEB task-instruction file has SHA-256 `f3f83069f3a3818a89c28de320051521bc37c6b8314d110b8911356eb9adfaa8`; use task instructions for Jina only, matching LMEB's published model settings. BGE and Gemma use no task instruction. Keep each model's document/query prompts.
- Normalize title + body and sort IDs. QASPER contains 44,792 exact repeated rows (65,300 source / 20,508 unique); only identical content under the same ID is collapsed. Four repeated positive qrel pairs with identical grade collapse to one judgment. Any content/grade conflict aborts preparation.
- Use published candidate scenes for 28,587 scoped queries and the full task corpus otherwise. Keep all positive qrels—including 109 links to 96 corpus-group-scoped absent IDs and 72 links outside candidate scenes—in Recall/NDCG denominators; the two counts can overlap. Count 4,588 missing candidate IDs; no candidate pool is empty.
- Report Recall@1/5/10, Success@10, MRR@10 and graded NDCG@10. Average queries into task scores, tasks equally into each of the 22 dataset scores, and the 22 datasets equally for Mean (Dataset). Also report Mean (Type), the equally weighted average of four memory-type means. Paired intervals resample queries within tasks, tasks within datasets and datasets for the overall mean (2,000 resamples, seed 1729).
- Quality query embeddings use batch 8. Separately sample up to 20 query IDs per task for synchronized batch-one embedding + ranking latency; report sample count and p50/p95. Sample RSS/MPS driver memory every 100 ms. Keep compressed per-query IDs/top-10/metrics and provenance, but no public query/corpus text or local paths.
- This is dense retrieval, not CPersona hybrid search, generated answer accuracy or a representative Japanese benchmark. Phase 1 SciFact/10-query Japanese smoke, full-LMEB results and downstream Track B remain separately labeled.

## Delivery boundary

This handoff pushes the experimental-fork branch and measurement documentation without a PR; it does not complete Bridge #19. The full BGE-M3 and Gemma runs, paired analysis and report review remain. A PR to the user's `pippirium/CPersona` fork is permitted, but no merge authorization is recorded. Never push or open a PR against `Cloto-dev/CPersona`. Existing published PoC/LMEB scores are reference history and are never copied as new measurements.
