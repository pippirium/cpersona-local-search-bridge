#!/bin/bash
# CP-02 chunked runner: one harness process per task, smallest-first, so
# cache warm-up and stall detection happen on cheap corpora. One model, one
# embedding cache. Skips tasks whose <Task>.json already exists in OUTPUT_DIR.
#
# Env: same contract as run_gemma_trackb.sh, plus
#   CP02_TASKS   space-separated task list (required)
#   CP02_DEVICE  cpu | mps (default cpu — see README "Machine finding")
#   LOG_DIR      runner/task logs (default $WORKDIR/logs)
set -u
: "${WORKDIR:?}"
LOG_DIR="${LOG_DIR:-$WORKDIR/logs}"
mkdir -p "$LOG_DIR"
export DEVICE="${CP02_DEVICE:-cpu}"
TASKS="${CP02_TASKS:?space-separated task list}"
for t in $TASKS; do
  if [ -f "$OUTPUT_DIR/$t.json" ]; then
    echo "$(date +%T) SKIP $t (exists)" >> "$LOG_DIR/runner.log"
    continue
  fi
  echo "$(date +%T) LAUNCH $t" >> "$LOG_DIR/runner.log"
  bash "$(dirname "$0")/run_gemma_trackb.sh" --tasks "$t" >> "$LOG_DIR/task_${t}.log" 2>&1
  echo "$(date +%T) DONE $t rc=$?" >> "$LOG_DIR/runner.log"
done
echo "$(date +%T) ALL CHUNKS DONE" >> "$LOG_DIR/runner.log"
