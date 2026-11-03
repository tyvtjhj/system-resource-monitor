#Requires -Version 5.1
<#
.SYNOPSIS
    把 dsh-hardware-monitor 安装进 DSH（默认 desktop profile）。

.DESCRIPTION
    与 dsh-netwatch 相同的 bundle 安装方式：
      1. 在 ~/.dsh/plugins 下建立指向本仓库 plugin/adapters/dsh 的 junction；
      2. 在 profile 的 node_modules 下建立同名 junction；
      3. profile package.json 增加依赖（link:）并加入 dsh.profile.bundles；
      4. 清理旧版手工插入的 cordis.patch.yml 标记块（bundle patch 负责插入）；
      5. 安装 skill 到 ~/.dsh/skills/hardware-monitor；
      6. 触发 DSH 热加载并提示刷新页面。

.PARAMETER Profile
    DSH profile 名，默认 desktop。

.PARAMETER Repo
    本仓库根目录，默认从本脚本位置向上推三层。
#>
[CmdletBinding()]
param(
    [string]$Profile = "desktop",
    [string]$Repo = ""
)

$ErrorActionPreference = "Stop"
if (-not $Repo) { $Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path }
$dshHome = Join-Path $env:USERPROFILE ".dsh"
$profileDir = Join-Path $dshHome "profiles\$Profile"
$pluginSrc = Join-Path $Repo "plugin\adapters\dsh"
$pkgName = "dsh-hardware-monitor"
$linkTarget = Join-Path $dshHome "plugins\$pkgName"
$linkPath = Join-Path $profileDir "node_modules\$pkgName"
$patchPath = Join-Path $profileDir "cordis.patch.yml"
$pkgPath = Join-Path $profileDir "package.json"
$skillDir = Join-Path $dshHome "skills\hardware-monitor"

function Step([string]$Text) { Write-Host "`n==> $Text" -ForegroundColor Cyan }
function Ok([string]$Text) { Write-Host "[OK] $Text" -ForegroundColor Green }
function Fail([string]$Text) { Write-Host "[FAIL] $Text" -ForegroundColor Red; exit 1 }

if (-not (Test-Path (Join-Path $pluginSrc "package.json"))) { Fail "找不到插件目录：$pluginSrc" }
if (-not (Test-Path $profileDir)) { Fail "找不到 DSH profile：$profileDir（先启动一次 DSH）" }
if (-not (Test-Path (Join-Path $profileDir "package.json"))) { Fail "profile package.json 不存在：$profileDir" }

# ---------------------------------------------------------------- 1. junction
Step "把插件挂到 ~/.dsh/plugins/$pkgName"
if (Test-Path $linkTarget) { cmd /c rmdir "$linkTarget" 2>&1 | Out-Null }
New-Item -ItemType Junction -Path $linkTarget -Target $pluginSrc | Out-Null
Ok "$linkTarget -> $pluginSrc"

Step "链接到 profile node_modules"
if (Test-Path $linkPath) { cmd /c rmdir "$linkPath" 2>&1 | Out-Null }
New-Item -ItemType Junction -Path $linkPath -Target $linkTarget | Out-Null
Ok "node_modules\$pkgName"

# ---------------------------------------------------------------- 2. package.json
Step "注册为 profile bundle"
$backupPkg = "$pkgPath.bak-$pkgName-$(Get-Date -Format yyyyMMdd-HHmmss)"
Copy-Item $pkgPath $backupPkg -Force
$pkg = Get-Content $pkgPath -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $pkg.dependencies) { $pkg | Add-Member -NotePropertyName dependencies -NotePropertyValue (New-Object PSObject) }
$pkg.dependencies | Add-Member -NotePropertyName $pkgName -NotePropertyValue "link:$linkTarget" -Force
if (-not $pkg.dsh) { $pkg | Add-Member -NotePropertyName dsh -NotePropertyValue (New-Object PSObject) }
if (-not $pkg.dsh.profile) { $pkg.dsh | Add-Member -NotePropertyName profile -NotePropertyValue (New-Object PSObject) }
$bundles = @($pkg.dsh.profile.bundles | Where-Object { $_ -and $_ -ne $pkgName })
$bundles += $pkgName
$pkg.dsh.profile | Add-Member -NotePropertyName bundles -NotePropertyValue $bundles -Force
[IO.File]::WriteAllText($pkgPath, ($pkg | ConvertTo-Json -Depth 20), (New-Object System.Text.UTF8Encoding($false)))
Ok "package.json 已更新（备份：$backupPkg）"

# ---------------------------------------------------------------- 3. 清理旧标记块
Step "清理 cordis.patch.yml 里的旧手工插入块"
if (Test-Path $patchPath) {
    $backupPatch = "$patchPath.bak-$pkgName-$(Get-Date -Format yyyyMMdd-HHmmss)"
    Copy-Item $patchPath $backupPatch -Force
    $lines = @(Get-Content $patchPath -Encoding UTF8)
    $out = New-Object System.Collections.Generic.List[string]
    $skipNext = $false
    foreach ($line in $lines) {
        if ($skipNext -and $line.Trim() -eq "- insert:") { $skipNext = $false; continue }
        if ($line -match $pkgName) {
            if ($out.Count -gt 0 -and $out[$out.Count - 1].Trim() -eq "- insert:") { $out.RemoveAt($out.Count - 1) }
            $skipNext = $true
            continue
        }
        $out.Add($line)
    }
    [IO.File]::WriteAllLines($patchPath, $out, (New-Object System.Text.UTF8Encoding($false)))
    Ok "已清理（备份：$backupPatch）"
} else {
    New-Item -ItemType File -Path $patchPath -Force | Out-Null
    Ok "已创建 cordis.patch.yml"
}

# ---------------------------------------------------------------- 4. skill
Step "安装 skill"
New-Item -ItemType Directory -Force -Path $skillDir | Out-Null
Copy-Item (Join-Path $pluginSrc "SKILL.md") (Join-Path $skillDir "SKILL.md") -Force
Ok "~/.dsh/skills/hardware-monitor/SKILL.md"

# ---------------------------------------------------------------- 5. 热加载
Step "触发 DSH 热加载（宿主半）"
Add-Content -Path $patchPath -Value "# $pkgName reload $(Get-Date -Format o)" -Encoding utf8
Start-Sleep -Seconds 6
$applyLog = Join-Path $pluginSrc "apply.log"
if (Test-Path $applyLog) {
    Write-Host "最近插件加载记录：" -ForegroundColor DarkGray
    Get-Content $applyLog -Tail 8 -Encoding UTF8 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
}

# ---------------------------------------------------------------- 6. 提示
Write-Host ""
Write-Host "安装完成。" -ForegroundColor Green
Write-Host "  • 输入框下方：刷新 DSH 页面（Ctrl+R）后出现 Hardware Monitor 小挂件"
Write-Host "  • 工具：hardware_monitor_status（模型可直接查询）"
Write-Host "  • 服务地址：默认 http://127.0.0.1:5678；启动 DSH 前可设置："
Write-Host "      `$env:HARDWARE_MONITOR_URL='http://127.0.0.1:5678'"
Write-Host "      `$env:HARDWARE_MONITOR_TOKEN='<MONITOR_TOKEN>'"
Write-Host "  • 卸载：powershell -ExecutionPolicy Bypass -File .\plugin\adapters\dsh\uninstall.ps1"
