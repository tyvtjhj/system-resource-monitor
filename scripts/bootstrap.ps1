<#
.SYNOPSIS
    Create the project .venv and install the locked dependencies (T1.3).

.DESCRIPTION
    Finds a Python >= 3.10 (py -3.10 -> py -3 -> python), creates .venv,
    upgrades pip (best effort), installs requirements.txt, then runs
    scripts/doctor.py as a self check.

.PARAMETER SkipPipUpgrade
    Skip "python -m pip install --upgrade pip". Useful on networks/proxies
    where pip self-upgrade hangs.

.PARAMETER Recreate
    Delete an existing .venv before creating a new one.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\bootstrap.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 -SkipPipUpgrade
#>
[CmdletBinding()]
param(
    [switch]$SkipPipUpgrade,
    [switch]$Recreate
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Write-Step([string]$Text) { Write-Host "[bootstrap] $Text" -ForegroundColor Cyan }
function Fail([string]$Text) { Write-Host "[bootstrap][ERROR] $Text" -ForegroundColor Red; exit 1 }

if (-not (Test-Path (Join-Path $Root 'requirements.txt'))) {
    Fail 'requirements.txt not found. Run scripts\lock-deps.ps1 first (or restore the committed lock file).'
}

# 1. Locate a suitable interpreter.
$candidates = @(
    @('py', '-3.10'),
    @('py', '-3'),
    @('python')
)
$chosenExe = $null
$chosenArgs = @()
foreach ($cand in $candidates) {
    $exe = $cand[0]
    $exeArgs = @()
    if ($cand.Count -gt 1) { $exeArgs = $cand[1..($cand.Count - 1)] }
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    try {
        & $exe @exeArgs -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $chosenExe = $exe
            $chosenArgs = $exeArgs
            break
        }
    } catch {
        continue
    }
}
if (-not $chosenExe) {
    Fail 'No Python >= 3.10 found. Install Python 3.10+ (with the py launcher) and retry.'
}
$display = (@($chosenExe) + $chosenArgs) -join ' '
Write-Step "Interpreter: $display"
& $chosenExe @chosenArgs -c "import sys; print('[bootstrap] Python', sys.version.split()[0])"

# 2. Create (or reuse) the virtual environment.
$venvDir = Join-Path $Root '.venv'
if (Test-Path $venvDir) {
    if ($Recreate) {
        Write-Step 'Removing existing .venv (-Recreate)'
        Remove-Item -Recurse -Force $venvDir
    } else {
        Write-Step '.venv already exists (use -Recreate to rebuild)'
    }
}
if (-not (Test-Path $venvDir)) {
    Write-Step 'Creating .venv'
    & $chosenExe @chosenArgs -m venv $venvDir
    if ($LASTEXITCODE -ne 0) { Fail "python -m venv failed (exit $LASTEXITCODE)" }
}

$venvPython = Join-Path $venvDir 'Scripts\python.exe'
if (-not (Test-Path $venvPython)) { Fail "venv python not found: $venvPython" }

# 3. Upgrade pip (best effort).
if ($SkipPipUpgrade) {
    Write-Step 'Skipping pip upgrade (-SkipPipUpgrade)'
} else {
    Write-Step 'Upgrading pip (-SkipPipUpgrade to skip; some proxies hang on this step)'
    & $venvPython -m pip install --upgrade pip --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[bootstrap][WARN] pip upgrade failed (exit $LASTEXITCODE); continuing" -ForegroundColor Yellow
    }
}

# 4. Install locked dependencies.
Write-Step 'Installing requirements.txt'
& $venvPython -m pip install --disable-pip-version-check -r (Join-Path $Root 'requirements.txt')
if ($LASTEXITCODE -ne 0) { Fail "pip install -r requirements.txt failed (exit $LASTEXITCODE)" }

# 5. Self check + next steps.
Write-Step 'Running scripts/doctor.py'
& $venvPython (Join-Path $Root 'scripts\doctor.py')
$doctorExit = $LASTEXITCODE

Write-Host ''
Write-Host '[bootstrap] Done. Next steps:' -ForegroundColor Cyan
Write-Host '  Activate venv : .\.venv\Scripts\Activate.ps1'
Write-Host '  Start service : python server.py     (or run.bat, UAC prompt included)'
Write-Host '  Self check    : .\.venv\Scripts\python.exe scripts\doctor.py'
if ($doctorExit -ne 0) {
    Write-Host "[bootstrap][WARN] doctor.py reported problems (exit $doctorExit)" -ForegroundColor Yellow
    exit 1
}
exit 0
