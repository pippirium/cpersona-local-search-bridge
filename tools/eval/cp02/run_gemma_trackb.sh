#!/bin/bash
# CP-02 — EmbeddingGemma 2 (pinned 914f7f89...) measured on CPersona Track B.
# Parameterized repro of the arm launcher used on 2026-10-26.
#
# Preconditions (see ./README.md):
#   WORKDIR           durable dir with lmeb/ + hf-cache/ (NOT /tmp)
#   CP_BENCH_WT       worktree of the fork at a commit bearing benchmarks/
#   CP_ARM_WT         worktree at tag v2.4.40 — the CODE PATH only
#   VENV              python3.12 venv with sentence-transformers 6.x + torch
#   EMB_CACHE_DIR     dedicated embedding disk cache (never shared across models)
#   OUTPUT_DIR        where <Task>.json results are written
#   DEVICE            cpu | mps  (default cpu; MPS stalls on >~2k-doc corpora
#                     on a 32 GB-class Mac — see README "Machine finding")
#
# The fork checkouts are NEVER modified: the harness and launcher are copied
# to $WORKDIR and patched there. Two deviations from the reported bge-m3 arm,
# both pinned by D-27 in docs/09-decisions.md:
#   1. precision — the harness's --dtype float16 (fp16, which the adopted
#      deployment never uses: NaN risk; policy MPS->bf16, CPU->fp32) is
#      stripped; the model loads with an explicit per-device dtype and
#      config_kwargs dropping the unused vision/audio towers, matching the
#      deployed embedder and the fork PoC.
#   2. window — the checkpoint tokenizer advertises model_max_length=1e30
#      (config max_position_embeddings: null), which crashes the harness's
#      explicit-truncation path (OverflowError); --max_seq_len clamps it.
#      CP02_MAX_SEQ_LEN (default 1024) records the window actually used.
set -e
: "${WORKDIR:?}"
: "${CP_BENCH_WT:?}"
: "${CP_ARM_WT:?}"
: "${VENV:?}"
: "${OUTPUT_DIR:?}"
: "${EMB_CACHE_DIR:?set — never share between models}"
export CPERSONA_REPO="$CP_ARM_WT"
export HF_HOME="$WORKDIR/hf-cache"
export HF_HUB_OFFLINE=1
export PYTHON_BIN="$VENV/bin/python"
export MODEL_PATH="$WORKDIR/hf-cache/models--google--embeddinggemma-2/snapshots/914f7f89142e33e77833254d9c9b90c3cef7303b"
export LMEB_DIR="$WORKDIR/lmeb"
export DEVICE="${DEVICE:-cpu}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-6}"
mkdir -p "$EMB_CACHE_DIR" "$OUTPUT_DIR"

BENCH_B="$CP_BENCH_WT/benchmarks"
python3 - <<PY
bench = "$BENCH_B"
launcher = open(f"{bench}/run_trackb.sh").read()
launcher = launcher.replace("--dtype float16 ", "")
launcher = launcher.replace('SCRIPT_DIR="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"',
                            f"SCRIPT_DIR={bench}")
launcher = launcher.replace('"\$SCRIPT_DIR/benchmark_trackb_lmeb.py"',
                            '"$WORKDIR/harness_gemma.py"')
open("$WORKDIR/run_trackb_gemma.sh", "w").write(launcher)

harness = open(f"{bench}/benchmark_trackb_lmeb.py").read()
old = "    model_kwargs = {}\n"
new = (
    "    import torch as _t\n"
    "    model_kwargs = {\n"
    '        "model_kwargs": {"dtype": _t.float32 if args.device == "cpu" else _t.bfloat16},\n'
    '        "config_kwargs": {"vision_config": None, "audio_config": None},\n'
    "    }\n"
)
assert old in harness, "model_kwargs anchor not found — harness drifted; update this script"
harness = harness.replace(old, new, 1)
harness = harness.replace("sys.path.insert(0, _CPERSONA_REPO)",
                          "sys.path.insert(0, _CPERSONA_REPO)\n"
                          f"sys.path.insert(0, {bench!r})")
open("$WORKDIR/harness_gemma.py", "w").write(harness)
PY

WIN="${CP02_MAX_SEQ_LEN:-1024}"
WINARG=(); [ -n "$WIN" ] && WINARG=(--max_seq_len "$WIN")
exec bash "$WORKDIR/run_trackb_gemma.sh" --fast --accel_backend numpy "${WINARG[@]}" "$@"
