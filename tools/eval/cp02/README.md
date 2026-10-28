# CP-02 measurement harness — reproducible recipe and honest results

CP-02 asks for a fair retrieval-quality comparison of the embedding models
against **CPersona's already-reported Track B numbers**
(`benchmarks/measurements/trackb_results_v2440_bgem3`, mean 57.66, and
`trackb_results_jinanano`, mean 57.08, both `mean_ndcg_at_10` over 22 LMEB
task means). This directory records the procedure that was actually run on
2026-10-26, the values measured, and the tasks that could **not** be measured
on the development machine. No unmeasured cell is filled in.

## Scope decision (user-approved, 2026-10-26)

* Measure **EmbeddingGemma 2 only** (`google/embeddinggemma-2`, pinned revision
  `914f7f89142e33e77833254d9c9b90c3cef7303b`) and compare against the
  **reported** bge-m3 / jina numbers — no new baseline arms.
* Dataset: `KaLM-Embedding/LMEB` at revision
  `f9d4294be4a24b8c16d6bfb59d56a6fec4bd95c0`, 520 files, 1.15 GB, each file
  size-verified against a manifest.
* The CPersona fork checkout must stay byte-unchanged: the benchmark harness
  runs from a detached **worktree** copy and the model code path is selected
  with `CPERSONA_REPO` pointing at a v2.4.40 **worktree** (the reported arms ran
  v2.4.40-era code). The fork itself was never modified.
* The model snapshot goes into a throwaway HF cache. Production caches
  (`~/.local-search/models`, the broken `~/.cache/huggingface/hub` copy) are
  never read or written. Because the snapshot is a plain HF cache layout with
  exactly local-search's `REQUIRED_FILES`, the same directory can later be
  adopted by local-search via `cache_folder` without re-download.

## Scripts (parameterized; personal paths removed)

Run order for a fresh machine. `WORKDIR` is a **durable** directory — the first
full run lost its workdir to macOS `/tmp` cleanup mid-measurement, so do not
use `/tmp` for anything you cannot afford to lose.

| step | script | what it does |
| --- | --- | --- |
| 1 | `download_lmeb.py` | fetches the 23-task eval_data subset at the pinned revision into `$WORKDIR/lmeb/eval_data`, size-verifies every file |
| 2 | `fetch_gemma_snapshot.py` | pinned `snapshot_download` into `$WORKDIR/hf-cache` (offline afterwards) |
| 3 | `run_gemma_trackb.sh` | single-task arm launcher; env contract documented in the file header |
| 4 | `launch_chunked.sh` / `chunk_runner.py` | one process per task; validate pinned files, result JSON, SHA and run fingerprints before resuming |
| 5 | `chunk_runner.py --stall-seconds 1200` | optional per-task stall watchdog (cross-platform POSIX), terminates only its child process group; obsolete macOS-only `monitor.sh` should **not** be launched alongside the new runner |

The launcher copies the harness out of the worktree and applies exactly two
deviations to the **copy** (never the fork checkout), both recorded in D-29:

1. precision — the harness's `--dtype float16` would load fp16, which the
   adopted deployment never uses (NaN risk; policy is MPS→bf16, CPU→fp32), so
   `--dtype` is stripped and the model loads with an explicit per-device dtype
   plus `config_kwargs` dropping the unused vision/audio towers, exactly like
   the deployed embedder and the fork PoC;
2. token window — the checkpoint tokenizer advertises `model_max_length = 1e30`
   (and `config.json` has `max_position_embeddings: null`), which crashes the
   harness's explicit-truncation path with `OverflowError`; the harness's own
   `--max_seq_len` flag clamps it (2048 for the LoCoMo probes, 1024/512 for
   later chunks — the window actually used is in each log line
   `max_seq_length overridden: …@msN`).

## Machine finding: this Mac cannot complete Track B for this model

Established on 2026-10-26 (Apple-silicon Mac, 32 GB-class, MPS):

* Encoding stalls on corpora beyond a few thousand documents **on both
  devices**: MPS hangs in `at::mps::MPSStream::copy_and_sync` /
  `where_kernel_mps` with the GPU queue waiting (several reproductions, 15–25
  min zero progress on 2.4k–65k-doc corpora), and CPU fp32 runs at ~1.7
  docs/s (≈72 hours for LongMemEval's 237,655 docs alone). The user confirmed
  the machine itself freezes under this load and stopped the experiment.
* Tasks that **did** complete sit at or below ~6k docs and ~18k queries
  (LoCoMo, ESGReports, LMEB_SciFact), consistent with the finding.

## Measured results (mean_ndcg_at_10, CPersona Track B, rrf + auto_calibrate)

| task | EmbeddingGemma 2 | reported bge-m3 (v2.4.40) | note |
| --- | --- | --- | --- |
| LoCoMo | **46.10** | 45.91 | MPS bf16, window 2048; subtasks adv 49.67 / multi_hop 32.78 / open_domain 25.58 / single_hop 59.43 / temporal 63.04 vs bge-m3 46.14/33.86/26.96/59.80/62.81 |
| ESGReports | **43.22** | 43.23 | CPU fp32, window 1024 |
| LMEB_SciFact | **74.66** | 74.87 | CPU fp32, window 1024 |
| other 19 tasks | *unmeasured* | 57.66 mean overall | machine limit above |

Calibration thresholds recorded by the runs: LoCoMo 0.8214, ESGReports 0.7422,
LMEB_SciFact 0.7895 (dim 768). Recall latency p50 (full): 11.09 / 7.92 / 6.51
ms respectively.

**Reading limits (mandatory):**

* Three tasks are **not** a model comparison and no overall mean is computed —
  docs/07 forbids mixing regimes, and the three completions above use two
  different device/precision/window combinations. Do not aggregate them.
* The reported bge-m3 arm used the model's native 8192 window, fp16 activation
  flag, and 22 tasks; these Gemma runs used 1024–2048-token windows (a
  documented deviation forced by the tokenizer metadata bug, and closer to
  local-search's deployed 512-token chunking than the reported arm). The
  reported runs' own README notes ±1–2 pt calibration noise per task mean, so
  the per-task deltas above are within or near noise and support no ranking
  claim.
* OpenViking compares with CPersona only at the **answer layer** (OmniMemEval:
  CPersona pro 84.03% vs OV 59.09% LoCoMo etc.); OpenViking's embedding model
  is undocumented, so **no embedding-model-layer comparison with OpenViking is
  possible** and none is made.

## Where the raw evidence lives

The run outputs, logs and patched-harness copy were preserved by backup at
`~/Documents/CPersona-backups/20261026-150347/` (not in this repository:
result JSONs carry absolute local paths, which the D-19 forbidden-markers rule
excludes from tracked files). The numbers in the table above were read from
those JSON files; the process list snapshot `processes.txt` and
`backup-info.json` in that directory record the exact command lines.

## Reproducing

```sh
export WORKDIR=/some/durable/dir            # NOT /tmp
export CP_BENCH_WT=/some/dir/cpersona-bench # git worktree of the fork at the harness-bearing commit
export CP_ARM_WT=/some/dir/cpersona-v2440   # git worktree at tag v2.4.40 (code path only)
export VENV=/path/to/python3.12/venv        # sentence-transformers 6.x + torch (see fork poc/embedding/requirements.lock)
python3 download_lmeb.py && python3 fetch_gemma_snapshot.py
CP02_TASKS="ESGReports" CP02_DEVICE=cpu ./run_gemma_trackb.sh --tasks ESGReports   # smallest task first
```

Before any long run, check free RAM: if the compressor is already multi-GB,
the machine will stall or freeze during encoding (the failure mode documented
above). Only tasks ≤ ~6k docs are realistic on a 32 GB-class Mac; larger
corpora need a machine with headroom, and that run is still open work.

## Separate-machine runner (Issue #75, review branch)

**This runner does not make the 19 unmeasured tasks measured.** The actual
Gemma/Track B tests must be performed on the separate PC. Use the executable
`tools/eval/cp02/chunk_runner.py` provided by this branch and review/merge
PR #53 before expecting these scripts on `main`.

The legacy launcher skipped by file existence and printed "ALL CHUNKS DONE"
after failed tasks. The replacement refuses that false-success behavior:

1. Fingerprints the pinned Gemma snapshot content, *all* LMEB downloaded bytes,
   the dataset manifest, two CPersona worktree commits, runner/launcher SHA,
   dtype, device and token window. Keep `WORKDIR`, `OUTPUT_DIR` and
   `EMB_CACHE_DIR` dedicated, with no production data mounted.
2. Validates the output JSON's task identity, finite score, subtask count and
   time; additionally requires a SHA-bound `<Task>.completed.json` marker
   carrying the matching run-context fingerprint before skipping.
3. Runs tasks sequentially in a child process group, preserving per-task
   stdout/stderr in a non-overwriting log. On nonzero exit or stall, exits
   nonzero rather than publishing an all-done claim. The stall watchdog uses
   log growth as a proxy: long silent but healthy runs can time out, so set
   an appropriate limit and check task logs.
4. An interrupted/invalid task is **not** silently overwritten: move its
   `<Task>.json`, marker (if any) and `task_<Task>.log` aside **manually**
   before retrying. For different model/device/window/code/data, use a
   **new output directory**. `summary.json` from upstream's harness may
   refer only to the most recently requested task; never treat it as a
   cross-task aggregate.

After the existing `WORKDIR`, `CP_BENCH_WT`, `CP_ARM_WT`, `VENV`,
`OUTPUT_DIR` and `EMB_CACHE_DIR` variables and the data/model snapshots
are set up, run:

```sh
# Small smoke first; script supports cpu/mps/cuda as a forwarded argument,
# but CUDA bf16 and all library versions MUST be checked on that machine.
CP02_TASKS='ESGReports' CP02_DEVICE=cpu \
  bash tools/eval/cp02/launch_chunked.sh --window 1024

# Explicit chunk list, one task at a time; use a longer stall window if needed:
python3 tools/eval/cp02/chunk_runner.py \
  --tasks 'LoCoMo,ESGReports' --device cpu \
  --window 1024 --stall-seconds 3600
```

On Linux, do not run the old macOS-specific `monitor.sh`
(`sample`, `stat -f`). On CUDA, the copied harness uses bfloat16,
which requires compatible GPU/torch; confirm that it loads without error.
If a new PC needs adapted model-loading code, update the launcher in a
reviewable commit and let the changed SHA force new output fingerprints.
A stored `run-context.json` is **run metadata, not proof of successful
execution**. Read the per-task markers and report failed/unmeasured tasks.

Before publishing results, strip any local absolute paths or sensitive
text from the upstream benchmark's JSON/logs. This runner only writes
local private outputs, does not load any production DB, and does not
run a generation LLM.
