#!/usr/bin/env bash
# T1.3 Regenerate dependency lock files (Linux/macOS).
#
# Produces requirements.txt and requirements-dev.txt as cross-platform locks
# (platform markers such as sys_platform == "win32" are preserved).
#
# pip-tools' pip-compile has no cross-platform "--universal" option (verified on
# pip-tools 7.6.2), so this script uses "uv pip compile --universal
# --python-version 3.10" when uv is available. Without uv it falls back to
# .venv pip-compile, which can only lock the current platform (warning printed).
#
# Usage: bash scripts/lock-deps.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

log() { printf '[lock-deps] %s\n' "$*"; }
warn() { printf '[lock-deps][WARN] %s\n' "$*" >&2; }
fail() { printf '[lock-deps][ERROR] %s\n' "$*" >&2; exit 1; }

[ -f requirements.in ] || fail "requirements.in not found (run from the project root)."
[ -f requirements-dev.in ] || fail "requirements-dev.in not found (run from the project root)."

if command -v uv >/dev/null 2>&1; then
    log "uv: $(command -v uv)"
    log "Compiling requirements.in -> requirements.txt (universal, Python 3.10)"
    uv pip compile --universal --python-version 3.10 requirements.in -o requirements.txt
    log "Compiling requirements-dev.in -> requirements-dev.txt (universal, Python 3.10)"
    uv pip compile --universal --python-version 3.10 requirements-dev.in -o requirements-dev.txt
else
    PIP_COMPILE=".venv/bin/pip-compile"
    [ -x "$PIP_COMPILE" ] || fail "Neither uv nor .venv/bin/pip-compile found. Install uv (pip install uv) or pip-tools in .venv."
    warn "uv not found: pip-compile can only lock the CURRENT platform (pip-tools has no --universal option)."
    log "Compiling requirements.in -> requirements.txt"
    "$PIP_COMPILE" requirements.in -o requirements.txt
    log "Compiling requirements-dev.in -> requirements-dev.txt"
    "$PIP_COMPILE" requirements-dev.in -o requirements-dev.txt
fi

log 'Done. Review "git diff" before committing the lock files.'
