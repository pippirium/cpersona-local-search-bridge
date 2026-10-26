# CP-02 — Preregistered dense comparison protocol

Status: prepared before inference; no comparison scores measured by this document. This is the Bridge coordination/preparation artifact for #19, not authorization to modify or publish a CPersona fork PR. It does not change the MCP runtime or provider contracts.

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

## Delivery boundary

Measurements and reproducibility assets can be prepared within Bridge. The ticket still requires the separately authorized CPersona-fork evaluation PR and report; a Bridge-only artifact must not be called full CP-02 completion. Existing published PoC/LMEB scores are reference history and will not be copied into a new run as if remeasured.
