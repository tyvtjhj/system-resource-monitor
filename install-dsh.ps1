#Requires -Version 5.1
<#
.SYNOPSIS
    一键安装 Hardware Monitor 及其 DSH 原生插件（Windows）。

.DESCRIPTION
    依次完成：
      1. 定位仓库（在仓库内运行则直接用；否则克隆到 %LOCALAPPDATA%\HardwareMonitor，
         git 不可用时自动下载 ZIP，GitHub 失败回退 Gitee）；
      2. 检查 DSH desktop profile；
      3. 创建 .venv 并安装锁定依赖（复用 scripts/bootstrap.ps1）；
      4. 安装 DSH 插件（junction + profile bundle + skill + 热加载）；
      5. 启动 5678 监控服务（非管理员，温度走 ACPI）；
      6. 打印使用说明。

.PARAMETER InstallDir
    自动克隆时的目标目录，默认 %LOCALAPPDATA%\HardwareMonitor。

.PARAMETER RepoUrl
    GitHub 仓库地址。

.PARAMETER GiteeUrl
    Gitee 镜像地址（GitHub 不可用时回退）。

.PARAMETER Profile
    DSH profile 名，默认 desktop。

.PARAMETER SkipBootstrap
    跳过依赖安装（要求 .venv 已存在）。

.PARAMETER NoStart
    不自动启动监控服务（DSH 插件随后也会自动拉起）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install-dsh.ps1

.EXAMPLE
    # 任意目录（无需先克隆）
    powershell -ExecutionPolicy Bypass -Command "$u='https://raw.githubusercontent.com/tyvtjhj/system-resource-monitor/main/install-dsh.ps1'; $p=Join-Path $env:TEMP 'install-dsh.ps1'; irm $u -OutFile $p; & $p"
#>
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'HardwareMonitor'),
    [string]$RepoUrl = 'https://github.com/tyvtjhj/system-resource-monitor',
    [string]$GiteeUrl = 'https://gitee.com/tyvtjhj/system-resource-monitor',
    [string]$Profile = 'desktop',
    [switch]$SkipBootstrap,
    [switch]$NoStart
)

$ErrorActionPreference = 'Stop'
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
} catch {
    # 旧系统没有 Tls12 时忽略
}

function Step([string]$Text) { Write-Host "`n==> $Text" -ForegroundColor Cyan }
function Ok([string]$Text) { Write-Host "[OK] $Text" -ForegroundColor Green }
function Warn([string]$Text) { Write-Host "[WARN] $Text" -ForegroundColor Yellow }
function Fail([string]$Text) { Write-Host "[ERROR] $Text" -ForegroundColor Red; exit 1 }

function Test-Repo([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
    return (Test-Path (Join-Path $Path 'server.py')) -and
           (Test-Path (Join-Path $Path 'plugins\dsh-hardware-monitor\install.ps1'))
}

# ---------------------------------------------------------------- 1. 定位仓库
Step '定位监控程序目录'
$Repo = ''
foreach ($candidate in @($PSScriptRoot, (Get-Location).Path, $InstallDir)) {
    if (Test-Repo $candidate) {
        $Repo = (Resolve-Path $candidate).Path
        break
    }
}

if (-not $Repo) {
    Step "未在本地找到仓库，准备安装到：$InstallDir"
    if (Test-Path $InstallDir) {
        Fail "目录已存在但不是监控仓库：$InstallDir。请删除它，或用 -InstallDir 指定其它目录。"
    }
    New-Item -ItemType Directory -Force -Path (Split-Path $InstallDir -Parent) | Out-Null

    $cloned = $false
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($git) {
        foreach ($url in @($RepoUrl, $GiteeUrl)) {
            Write-Host "  git clone $url" -ForegroundColor DarkGray
            try {
                & git clone --depth 1 $url $InstallDir 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
                if ($LASTEXITCODE -eq 0 -and (Test-Repo $InstallDir)) { $cloned = $true; break }
            } catch {
                # 尝试下一个地址
            }
            if (Test-Path $InstallDir) { Remove-Item $InstallDir -Recurse -Force -ErrorAction SilentlyContinue }
        }
    }

    if (-not $cloned) {
        Step 'git 克隆失败，改为下载 ZIP'
        $zip = Join-Path $env:TEMP ('hardware-monitor-' + [Guid]::NewGuid().ToString('N') + '.zip')
        $extract = Join-Path $env:TEMP ('hardware-monitor-' + [Guid]::NewGuid().ToString('N'))
        $downloaded = $false
        $zipUrls = @(
            "$RepoUrl/archive/refs/heads/main.zip",
            "$GiteeUrl/repository/archive/main.zip"
        )
        foreach ($zipUrl in $zipUrls) {
            try {
                Write-Host "  download $zipUrl" -ForegroundColor DarkGray
                Invoke-WebRequest -Uri $zipUrl -OutFile $zip -UseBasicParsing -TimeoutSec 120
                $downloaded = $true
                break
            } catch {
                # 尝试下一个地址
            }
        }
        if (-not $downloaded) {
            Fail '无法克隆或下载仓库。请检查网络/代理，或手动下载仓库后在本目录内运行 install-dsh.ps1。'
        }
        Expand-Archive -Path $zip -DestinationPath $extract -Force
        $inner = Get-ChildItem $extract -Directory | Select-Object -First 1
        if (-not $inner) { Fail 'ZIP 解压后未找到仓库目录。' }
        Move-Item $inner.FullName $InstallDir
        Remove-Item $zip -Force -ErrorAction SilentlyContinue
        Remove-Item $extract -Recurse -Force -ErrorAction SilentlyContinue
        if (-not (Test-Repo $InstallDir)) { Fail '下载的仓库不完整（缺少 server.py 或 DSH 插件）。' }
    }
    $Repo = (Resolve-Path $InstallDir).Path
}
Ok "仓库目录：$Repo"

# ---------------------------------------------------------------- 2. 检查 DSH
Step '检查 DSH 安装'
$dshHome = Join-Path $env:USERPROFILE '.dsh'
$profileDir = Join-Path $dshHome "profiles\$Profile"
if (-not (Test-Path $profileDir)) {
    Fail "未检测到 DSH profile：$profileDir。请先安装 DeepSeek Harness 并启动一次（会生成 profile），再重试。"
}
Ok "DSH profile：$profileDir"

# ---------------------------------------------------------------- 3. Python 环境
Step '准备 Python 环境（.venv + 依赖）'
$venvPython = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    if ($SkipBootstrap) {
        Fail "未找到 $venvPython，且指定了 -SkipBootstrap。请先运行 scripts\bootstrap.ps1。"
    }
    $bootstrap = Join-Path $Repo 'scripts\bootstrap.ps1'
    if (-not (Test-Path $bootstrap)) { Fail "缺少 bootstrap 脚本：$bootstrap" }
    & powershell -NoProfile -ExecutionPolicy Bypass -File $bootstrap -SkipPipUpgrade
    if ($LASTEXITCODE -ne 0) {
        Fail "依赖安装失败（bootstrap exit $LASTEXITCODE）。可手动运行 scripts\bootstrap.ps1 查看详细原因。"
    }
    if (-not (Test-Path $venvPython)) { Fail "bootstrap 执行后仍未找到 $venvPython。" }
}
$pythonVersion = (& $venvPython --version 2>&1) -join ' '
Ok "$pythonVersion（$venvPython）"

# ---------------------------------------------------------------- 4. DSH 插件
Step '安装 DSH 原生插件'
$installer = Join-Path $Repo 'plugins\dsh-hardware-monitor\install.ps1'
if (-not (Test-Path $installer)) { Fail "缺少 DSH 安装脚本：$installer" }
& powershell -NoProfile -ExecutionPolicy Bypass -File $installer -Profile $Profile -Repo $Repo
if ($LASTEXITCODE -ne 0) { Fail "DSH 插件安装失败（exit $LASTEXITCODE）。" }
Ok 'DSH 插件安装完成（已触发宿主热加载）'

# ---------------------------------------------------------------- 5. 启动服务
Step '检查监控服务'
$serviceUrl = 'http://127.0.0.1:5678'
$online = $false
try {
    $resp = Invoke-RestMethod "$serviceUrl/api/start" -TimeoutSec 3
    $online = ($resp.status -eq 'ok')
} catch {
    $online = $false
}

if (-not $online -and -not $NoStart) {
    Step '启动监控服务（非管理员；需要 CPU Package 温度时请改用 run.bat 提权）'
    Start-Process -FilePath $venvPython -ArgumentList @('-m', 'hardware_monitor', '--no-elevate') -WorkingDirectory $Repo -WindowStyle Hidden
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 500
        try {
            $resp = Invoke-RestMethod "$serviceUrl/api/start" -TimeoutSec 2
            if ($resp.status -eq 'ok') { $online = $true; break }
        } catch {
            # 继续等待
        }
    }
}

if ($online) {
    Ok "监控服务在线：$serviceUrl"
} elseif ($NoStart) {
    Warn '已按 -NoStart 跳过服务启动（DSH 插件会在 DSH 启动时自动拉起）。'
} else {
    Warn "服务未在预期时间内启动，请手动运行：$Repo\run.bat"
}

# ---------------------------------------------------------------- 6. 完成
Write-Host ''
Write-Host '安装完成。' -ForegroundColor Green
Write-Host '  • 打开 DSH 并按 Ctrl+R 刷新：侧边栏「硬件监控」+ 输入框下方性能挂件'
Write-Host "  • 完整仪表盘：$serviceUrl/"
Write-Host "  • 需要内核级 CPU 温度：关闭当前服务后运行 $Repo\run.bat（管理员）"
Write-Host '  • 可选环境变量：HARDWARE_MONITOR_URL / HARDWARE_MONITOR_TOKEN（启动 DSH 前设置）'
Write-Host "  • 卸载插件：powershell -ExecutionPolicy Bypass -File `"$Repo\plugins\dsh-hardware-monitor\uninstall.ps1`""
