#!/usr/bin/env bash
# T1.3 bootstrap for Linux/macOS: create .venv and install locked dependencies.
#
# Usage:
#   bash scripts/bootstrap.sh
#   SKIP_PIP_UPGRADE=1 bash scripts/bootstrap.sh   # skip pip self-upgrade
#   RECREATE=1 bash scripts/bootstrap.sh           # rebuild .venv from scratch
#   PYTHON=/usr/bin/python3.10 bash scripts/bootstrap.sh
#
# On Windows use scripts/bootstrap.ps1 instead.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SKIP_PIP_UPGRADE="${SKIP_PIP_UPGRADE:-0}"
RECREATE="${RECREATE:-0}"

log() { printf '[bootstrap] %s\n' "$*"; }
warn() { printf '[bootstrap][WARN] %s\n' "$*" >&2; }
fail() { printf '[bootstrap][ERROR] %s\n' "$*" >&2; exit 1; }

[ -f requirements.txt ] || fail "requirements.txt not found. Run scripts/lock-deps.sh first."

find_python() {
    local candidate
    for candidate in python3.10 python3.11 python3.12 python3; do
        if command -v "$candidate" >/dev/null 2>&1; then
            if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
                printf '%s\n' "$candidate"
                return 0
            fi
        fi
    done
    return 1
}

if [ -n "${PYTHON:-}" ]; then
    command -v "$PYTHON" >/dev/null 2>&1 || fail "PYTHON=$PYTHON not found."
    "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' \
        || fail "PYTHON=$PYTHON is not Python >= 3.10."
    PY="$PYTHON"
else
    PY="$(find_python)" || fail "No Python >= 3.10 found (set PYTHON=/path/to/python to override)."
fi
log "Interpreter: $PY ($("$PY" -c 'import sys; print(sys.version.split()[0])'))"

if [ -d .venv ]; then
    if [ "$RECREATE" = "1" ]; then
        log "Removing existing .venv (RECREATE=1)"
        rm -rf .venv
    else
        log ".venv already exists (set RECREATE=1 to rebuild)"
    fi
fi
if [ ! -d .venv ]; then
    log "Creating .venv"
    "$PY" -m venv .venv
fi

VENV_PY=".venv/bin/python"
[ -x "$VENV_PY" ] || fail "venv python not found: $VENV_PY"

if [ "$SKIP_PIP_UPGRADE" = "1" ]; then
    log "Skipping pip upgrade (SKIP_PIP_UPGRADE=1)"
else
    log "Upgrading pip (SKIP_PIP_UPGRADE=1 to skip; some proxies hang on this step)"
    "$VENV_PY" -m pip install --upgrade pip --disable-pip-version-check || warn "pip upgrade failed; continuing"
fi

log "Installing requirements.txt"
"$VENV_PY" -m pip install --disable-pip-version-check -r requirements.txt

log "Running scripts/doctor.py"
set +e
"$VENV_PY" scripts/doctor.py
doctor_status=$?
set -e

cat <<'EOF'

[bootstrap] Done. Next steps:
  Activate venv : source .venv/bin/activate
  Start service : python server.py   (or ./run.sh)
  Self check    : .venv/bin/python scripts/doctor.py
EOF

exit "$doctor_status"
