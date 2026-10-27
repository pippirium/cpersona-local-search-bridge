# CP-02 evaluation handoff — 2026-10-28

## Status

The full-LMEB standalone dense comparison is partial. Jina completed the registered run. BGE-M3 reached a saved LongMemEval group 8 checkpoint and was stopped after the user's requested remaining-document segment finished. EmbeddingGemma 2 has not been run. These runs do not support a final model comparison.

The detailed Japanese checkpoint report is in the [CPersona fork branch](https://github.com/pippirium/CPersona/tree/feat/cp02-fair-embedding-comparison/docs/research/cp02-full-lmeb-checkpoint-2026-10-28.ja.md); its [English version](https://github.com/pippirium/CPersona/tree/feat/cp02-fair-embedding-comparison/docs/research/cp02-full-lmeb-checkpoint-2026-10-28.md) contains the same measurements and resume notes. The current handoff pushes branches and docs only; it creates no PR.

## Jina complete result

Jina v5-text-nano (`8a7f00aac812071b69403df470f1038ec85f8925`) completed 22 datasets, 103 corpus groups, 193 tasks and 159,011 scored queries with zero errors. Source data was `KaLM-Embedding/LMEB` at `f9d4294be4a24b8c16d6bfb59d56a6fec4bd95c0`; task-instruction SHA-256 was `f3f83069f3a3818a89c28de320051521bc37c6b8314d110b8911356eb9adfaa8`. The worker used Apple M1 Pro, 32 GB, MPS/bf16, max sequence length 512, batch 8 and outer chunks up to 1,024.

| Metric | Mean (Dataset), 22 datasets | Mean (Type), 4 types |
| --- | ---: | ---: |
| Recall@1 | 0.326083 | 0.336396 |
| Recall@5 | 0.563917 | 0.567626 |
| Recall@10 | 0.664546 | 0.661801 |
| Success@10 | 0.810033 | 0.811694 |
| MRR@10 | 0.580861 | 0.592297 |
| nDCG@10 | 0.558271 | 0.563594 |

These are Jina aggregates, not paired comparisons. Batch-one latency was sampled 3,554 times: p50 40.22975 ms and p95 62.038916 ms. Corpus embedding took 41,947.9953 s; quality-query embedding took 1,777.39494 s.

## BGE-M3 stopped checkpoint

BGE-M3 (`5617a9f61b028005a4858fdac845db406aefb181`) has eight saved group checkpoints out of 103. LongMemEval group 8 completed with 237,655 documents and 500 queries, with zero errors:

| Recall@1 | Recall@5 | Recall@10 | Success@10 | MRR@10 | nDCG@10 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0.442900 | 0.818867 | 0.889133 | 0.938000 | 0.792825 | 0.786695 |

This is one group only, not the BGE aggregate. The full BGE result was not produced. Gemma is unrun, so no paired confidence intervals, win/tie/loss counts or model recommendation are available.

## Resume handoff

The fork branch contains the harness and checkpoint documentation. Full-LMEB input text, model weights, checkpoint blobs and per-query outputs are local ignored artifacts and are not on the branch. The other PC can resume at BGE group 9 only if it has matching ignored inputs/checkpoints and verifies the dataset, task-instruction, model, harness and execution fingerprints; otherwise it must prepare isolated inputs and rerun. The current Jina full-result artifact is also local only. Never commit corpus/query text, private paths, credentials or checkpoint data.

Do not resume inference until requested. The next evaluation steps are to finish BGE-M3, run Gemma at the preregistered conditions, and then run the paired comparison over all three complete result files. CP-02 remains incomplete; this handoff does not open a PR or authorize a merge.
