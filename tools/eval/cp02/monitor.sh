#!/bin/bash
# CP-02 stall monitor: kills the running harness chunk after STALL_SEC without
# log growth, and aborts the whole runner after 3 consecutive stalls. Rationale:
# on a 32 GB-class Mac the Gemma encode hangs (MPS: GPU copy queue wait; CPU:
# throughput collapse) and a hung chunk would otherwise burn hours. Each kill
# saves a `sample` stack dump next to the task log as stall evidence.
#
# Env: LOG_DIR (default $WORKDIR/logs), OUTPUT_DIR, STALL_SEC (default 1200).
set -u
: "${WORKDIR:?}"
: "${OUTPUT_DIR:?}"
LOG_DIR="${LOG_DIR:-$WORKDIR/logs}"
STALL_SEC="${STALL_SEC:-1200}"
stalls=0
while true; do
  sleep 300
  if grep -q "ALL CHUNKS DONE" "$LOG_DIR/runner.log" 2>/dev/null; then echo "$(date +%T) RUNNER DONE"; break; fi
  if grep -q "RUNNER ABORTED" "$LOG_DIR/runner.log" 2>/dev/null; then echo "$(date +%T) RUNNER ABORTED EARLIER"; break; fi
  pid=$(pgrep -f "benchmark_trackb_lmeb.py|harness_gemma.py" | tail -1)
  if [ -z "$pid" ]; then echo "$(date +%T) no harness pid"; continue; fi
  log=$(lsof -p "$pid" 2>/dev/null | awk '/logs\/task_/ {print $NF; exit}')
  [ -z "$log" ] && continue
  m=$(stat -f %m "$log"); age=$(( $(date +%s) - m ))
  tasks=$(ls "$OUTPUT_DIR"/*.json 2>/dev/null | wc -l | tr -d ' ')
  echo "$(date +%T) tasks=$tasks log_age=${age}s $log"
  if [ "$age" -gt "$STALL_SEC" ]; then
    stalls=$((stalls+1))
    task=$(basename "$log" .log); task=${task#task_}
    echo "$(date +%T) STALL ${age}s on $task (stall #$stalls) — sampling and killing $pid"
    sample "$pid" 1 > "$LOG_DIR/stall_${task}.sample.txt" 2>&1
    kill "$pid"
    if [ "$stalls" -ge 3 ]; then
      echo "$(date +%T) RUNNER ABORTED after 3 stalls" >> "$LOG_DIR/runner.log"
      pkill -f "launch_chunked.sh"
      break
    fi
  else
    stalls=0
  fi
done
