<#
.SYNOPSIS
    Regenerate the dependency lock files (T1.3).

.DESCRIPTION
    Produces requirements.txt and requirements-dev.txt as cross-platform locks
    (platform markers such as sys_platform == 'win32' are preserved).

    pip-tools' pip-compile does NOT provide a cross-platform "--universal"
    option (verified on pip-tools 7.6.2), so this script uses
    "uv pip compile --universal --python-version 3.10" when uv is available.
    If uv is missing it falls back to .venv pip-compile, which can only lock
    the current platform (a warning is printed).

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\lock-deps.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Log([string]$Text) { Write-Host "[lock-deps] $Text" -ForegroundColor Cyan }
function Warn([string]$Text) { Write-Host "[lock-deps][WARN] $Text" -ForegroundColor Yellow }
function Fail([string]$Text) { Write-Host "[lock-deps][ERROR] $Text" -ForegroundColor Red; exit 1 }

if (-not (Test-Path 'requirements.in')) { Fail 'requirements.in not found (run from the project root).' }
if (-not (Test-Path 'requirements-dev.in')) { Fail 'requirements-dev.in not found (run from the project root).' }

$uv = Get-Command uv -ErrorAction SilentlyContinue
if ($uv) {
    Log "uv: $($uv.Source)"
    Log 'Compiling requirements.in -> requirements.txt (universal, Python 3.10)'
    & $uv.Source pip compile --universal --python-version 3.10 requirements.in -o requirements.txt
    if ($LASTEXITCODE -ne 0) { Fail "uv pip compile failed for requirements.in (exit $LASTEXITCODE)" }

    Log 'Compiling requirements-dev.in -> requirements-dev.txt (universal, Python 3.10)'
    & $uv.Source pip compile --universal --python-version 3.10 requirements-dev.in -o requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { Fail "uv pip compile failed for requirements-dev.in (exit $LASTEXITCODE)" }
} else {
    $pipCompile = Join-Path $Root '.venv\Scripts\pip-compile.exe'
    if (-not (Test-Path $pipCompile)) {
        Fail 'Neither uv nor .venv\Scripts\pip-compile.exe found. Install uv (pip install uv) or pip-tools in .venv.'
    }
    Warn 'uv not found: pip-compile can only lock the CURRENT platform (pip-tools has no --universal option).'
    Log 'Compiling requirements.in -> requirements.txt'
    & $pipCompile requirements.in -o requirements.txt
    if ($LASTEXITCODE -ne 0) { Fail "pip-compile failed for requirements.in (exit $LASTEXITCODE)" }

    Log 'Compiling requirements-dev.in -> requirements-dev.txt'
    & $pipCompile requirements-dev.in -o requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { Fail "pip-compile failed for requirements-dev.in (exit $LASTEXITCODE)" }
}

Log 'Done. Review "git diff" before committing the lock files.'
exit 0
