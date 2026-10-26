# 07 — Offline evaluation and test strategy

## Principles
The bridge does not need GPT-4.1-mini, GPT-4o-mini or any other generation LLM to measure **retrieval**. Answer accuracy in LoCoMo (e.g., percentage of questions answered by an LLM) is a different metric and **must never be equated** with Recall@k or NDCG@k. All numeric scores in this repo must be measured, not copied into a new run as if remeasured.

## Three independent evaluation tracks
| Track | What it measures | Data and outputs | Generation LLM? |
| --- | --- | --- | --- |
| A: Embedding model | Jina v5-text-nano, BGE-M3 and EmbeddingGemma 2 at fixed revisions | Japanese smoke plus pinned full LMEB tasks; Recall@1/5/10, Success@10, MRR@10, NDCG@10, per-query latency/resource metrics, model-specific prompts/instructions | No |
| B: CPersona end-to-end retrieval | CPersona's text indexing, hybrid recall and reconstruct inputs with substituted embeddings | Same memory corpus, query set, gate settings and answer-bearing IDs | No, unless separately testing final generated answers |
| C: Bridge asset retrieval | Text→image, image→image, remembered memory→linked asset, explicit media resolution | Labeled image paths and memory↔asset pairs, hit@1, Recall@10, NDCG@10, p50/p95, bytes returned | No |

The already existing fork PoC is https://github.com/pippirium/CPersona/blob/master/poc/embedding/README.md. Its reported 10 synthetic queries and one image inference are smoke tests only; they do not establish superiority. Current local-search already has a production index evaluator: local-search eval --data-dir ... --set ... --out ... . Reuse compatible fixture conventions; record any metric differences.

## Retrieval cases
- Positive: correct image present and linked under exact scope.
- Negative: no relevant image; no linked results; forbidden scope.
- Multimodal: image image; text image; screenshot OCR-heavy query (record limitations).
- Identity: filename collision, duplicated bytes, renamed file, moved file, hash mismatch, index rebuilding.
- Failure: stopped local-search, stale provider ID, media omitted, unusually large image, malformed MIME and corrupt file.
- Ranking: relevance grades 1–3 and ties; missing captions; Japanese/English mixed language.
- Environment: Mac MPS bf16 and CPU fp32 measured separately, never aggregated into a single timing claim.

## Evaluation fixture shape (planning)
~~~json
{"id":"case-001","type":"text_to_image","query":{"text":"GPUのエラー画面"},"scope":{"agent_id":"test-agent","project_id":"test","channel":"demo"},"relevant":[{"asset_ref":"fixture-img-001","grade":3}]}
~~~
The adapter needs a deterministic fixture manifest from asset_ref to controlled temporary file and linked memory ID; do not put personal image paths into committed test files. Use generated test images or documented openly licensed samples only.

## Required measurements
- Hit@1: any relevant result first.
- Recall@10 must be defined unambiguously: fraction of relevant results recovered at 10, with a separate success@10 for any relevant result; **do not conflate** them.
- NDCG@10: graded relevance using DCG gains 2^grade - 1; use the same formula for every model.
- MRR@10: reciprocal rank of first relevant item.
- Search latency: p50/p95 query execution, separately embedding latency, DB ranking latency and optional asset fetch, with warmup count stated.
- Resource/cost: resident RAM, VRAM when measurable, index size, file-copy size and any external API spend (expected zero for retrieval).

## Comparability controls
Pin: dataset revision, query IDs, model name + exact revision, processor config, prompts per role, input length/chunking, image-token budgets, cosine normalization, vector dims, candidate count, threshold, device/precision, caching/warmup, CPersona version/mode and local-search generation. Isolate model-specific vector indexes and data dirs; do not silently mix embeddings. For repeated paired comparisons, report per-query win/tie/loss plus bootstrap 95% confidence interval or another declared paired method. If not supported, label confidence unmeasured.

## Automated test pyramid
1. Unit: pure input validation, normalization, scope equality, ID identity, schema migration, safe media limits, hash collisions and ranking.
2. Contract: fake HTTP local-search and MCP client exercising valid/invalid responses, timeouts and index_mismatch.
3. Integration: real local-search on disposable dataset + separate CPersona test process + Bridge; no production memory DB or mounted personal file roots.
4. Opt-in model evaluation: actual EmbeddingGemma 2; explicit environment flag; never part of default offline unit tests.
5. Restore test: snapshot Bridge DB and managed root (when present); deliberately break provider mapping; verify status and reindex recovery.

## Results format
Write versioned JSON with overall and per-query results, host/device/precision (without identifying private user data), timestamps, config fingerprint, git commits, seeds, query_count, missing_qrels, errors and coverage. Summarize Markdown with limitations and reproduce instructions. Do not claim an unexecuted test passed.
