#!/bin/bash
# CP-02 portable, fail-closed task launcher.
# Requires CP02_TASKS and environment variables documented in README.md.
# Use python3 directly for --stall-seconds, --window and --device overrides.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/chunk_runner.py" "$@"
