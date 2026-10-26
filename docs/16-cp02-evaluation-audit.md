# 16 — CP-02 evaluation audit: what CPersona is measured with, and what Gemma can be compared to

Issue: #19 (CP-02). This memo is the Phase-1 audit that must precede any new
measurement: it records **what the reported numbers actually measure**, which
of them a new embedding run may be compared against, and the honest exclusions.
Measured values and the repro recipe live in `tools/eval/cp02/README.md`; the
regime decision is D-27.

## 1. CPersona is evaluated on two distinct layers — never mix them

| Layer | Harness | Data | Metric | Reported |
| --- | --- | --- | --- | --- |
| **Answer layer** | OmniMemEval harness via `reconstruct` | LoCoMo 1,540 Q, LongMemEval-S 500 Q | answer accuracy %, context tokens, latency, write tokens | `benchmarks/measurements/results-omnimemeval-2_6_11.md` |
| **Retrieval layer (Track B)** | `benchmarks/benchmark_trackb_lmeb.py` (CPersona store→recall pipeline with substituted embeddings) | LMEB 23-task eval_data | `mean_ndcg_at_10` per task, calibration record, recall latency | `benchmarks/measurements/trackb_results_v2440_bgem3` (mean **57.66**), `trackb_results_jinanano` (mean **57.08**), `trackb_results_v2440_minilm` |

Answer-layer numbers (e.g. LoCoMo pro **84.03%** at 4,441 tokens; LongMemEval
lite **82.20%** at 993.3 tokens) involve a generation LLM answering from
retrieved context and **must never be equated** with NDCG (docs/07 principle).

## 2. Where OpenViking sits

OpenViking 0.5.0 was compared by the fork **only at the answer layer**, with
the same local `bge-m3` server given to it for embeddings (registered
comparison, `results-omnimemeval-2_6_11.md` §"Against OpenViking 0.5.0"):
LoCoMo CPersona pro 84.03% vs OV 59.09% (+24.9 pts, 95% one-sided LB +22.6),
context 4,031.2 vs 6,937.2 cl100k, write tokens 2,314,992 vs 0, and the 60-Q
LongMemEval screen 52 vs 51 correct. **OpenViking's own embedding model is not
documented** (it was handed ours in the registered run), therefore a
fair embedding-model-layer comparison "Gemma vs OpenViking's embedder" does not
exist and cannot be constructed from published data. Any CP-02 claim must say
so. (README also notes a 37-million write-token figure for OV in another run;
quote it with its source if used.)

## 3. What Gemma can legitimately be compared against

Gemma had **no reported Track B run** — CP-02's purpose was to fill exactly
that gap. The only fair baseline is the reported bge-m3/jina Track B table
(model, revision, dataset, pipeline, calibration method and regime recorded in
each run log). Scope approved by the user on 2026-10-26: measure **Gemma 2
only** against these reported numbers, full 23-task `trackb` regime
(`--recall_mode rrf --auto_calibrate`, `--fast --accel_backend numpy` to match
the reported log's accel line, fork checkout unchanged via worktrees).

## 4. Result status on this machine (2026-10-26)

Three task means completed before the machine hit its limit: LoCoMo **46.10**
(vs bge-m3 45.91), ESGReports **43.22** (vs 43.23), LMEB_SciFact **74.66**
(vs 74.87) — all within the reported runs' documented ±1–2 pt calibration
noise; **no ranking claim is supportable from 3/22 tasks**, and no overall mean
is computed. The remaining 19 tasks are **unmeasured**: encoding corpora beyond
~6k documents stalls on this 32 GB-class Mac (MPS hangs in the GPU copy queue,
CPU fp32 collapses to ~1.7 docs/s) and the machine freezes under load — the
user halted the experiment on this basis. The repro under `tools/eval/cp02/`
documents every deviation (D-27) so the remaining 19 tasks can be completed on
a machine with headroom, ideally re-running bge-m3 under the same pinned
conditions for a same-regime comparison at that time.

## 5. Honest-exclusion checklist for any CP-02 write-up

- [x] No aggregation of LoCoMo answer accuracy with retrieval metrics.
- [x] No aggregation of MPS-bf16 and CPU-fp32 cells into one timing/quality claim.
- [x] Window deviation (1e30 tokenizer bug; 1024–2048 vs native 8192) stated per task.
- [x] ±1–2 pt calibration noise stated when quoting per-task deltas.
- [x] No OpenViking embedding-layer comparison (undocumented model).
- [x] Unmeasured tasks left literally unmeasured; no imputed cells, no overall mean.
- [x] Fork checkout untouched; production memory DB and production model caches untouched.
