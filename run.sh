#!/usr/bin/env bash
# Start the hardware monitor on Linux/macOS.
#
# Prefers the project virtualenv (.venv/bin/python), then python3, then python.
# Extra arguments are passed through to server.py, e.g.:
#   ./run.sh --no-elevate
set -euo pipefail

cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY="python3"
else
    PY="python"
fi

echo "[run] python: $PY"
exec "$PY" server.py "$@"
