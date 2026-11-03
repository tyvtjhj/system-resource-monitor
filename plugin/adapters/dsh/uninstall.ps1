#Requires -Version 5.1
<#
.SYNOPSIS
    从 DSH 中卸载 dsh-hardware-monitor（bundle 安装方式）。

.DESCRIPTION
    移除 profile node_modules 链接、package.json 里的依赖与 bundles 条目、
    旧版 cordis.patch.yml 标记块、以及 ~/.dsh/skills/hardware-monitor。
    不删除本仓库源码，也不停止 Hardware Monitor 采集服务。
#>
[CmdletBinding()]
param(
    [string]$Profile = "desktop"
)

$ErrorActionPreference = "Continue"
$dshHome = Join-Path $env:USERPROFILE ".dsh"
$profileDir = Join-Path $dshHome "profiles\$Profile"
$pkgName = "dsh-hardware-monitor"
$linkTarget = Join-Path $dshHome "plugins\$pkgName"
$linkPath = Join-Path $profileDir "node_modules\$pkgName"
$pkgPath = Join-Path $profileDir "package.json"
$patchPath = Join-Path $profileDir "cordis.patch.yml"
$skillDir = Join-Path $dshHome "skills\hardware-monitor"

function Step([string]$Text) { Write-Host "`n==> $Text" -ForegroundColor Cyan }
function Ok([string]$Text) { Write-Host "[OK] $Text" -ForegroundColor Green }

Write-Host "卸载 $pkgName（profile: $Profile）"

Step "移除链接"
if (Test-Path $linkPath) { cmd /c rmdir "$linkPath" 2>&1 | Out-Null; Ok $linkPath }
if (Test-Path $linkTarget) { cmd /c rmdir "$linkTarget" 2>&1 | Out-Null; Ok $linkTarget }

Step "从 profile package.json 移除依赖与 bundle"
if (Test-Path $pkgPath) {
    $backup = "$pkgPath.bak-$pkgName-uninstall-$(Get-Date -Format yyyyMMdd-HHmmss)"
    Copy-Item $pkgPath $backup -Force
    $pkg = Get-Content $pkgPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($pkg.dependencies -and $pkg.dependencies.PSObject.Properties[$pkgName]) {
        $pkg.dependencies.PSObject.Properties.Remove($pkgName)
    }
    if ($pkg.dsh -and $pkg.dsh.profile -and $pkg.dsh.profile.bundles) {
        $pkg.dsh.profile.bundles = @($pkg.dsh.profile.bundles | Where-Object { $_ -ne $pkgName })
    }
    [IO.File]::WriteAllText($pkgPath, ($pkg | ConvertTo-Json -Depth 20), (New-Object System.Text.UTF8Encoding($false)))
    Ok "package.json 已更新（备份：$backup）"
}

Step "清理 cordis.patch.yml 残留"
if (Test-Path $patchPath) {
    $backup = "$patchPath.bak-$pkgName-uninstall-$(Get-Date -Format yyyyMMdd-HHmmss)"
    Copy-Item $patchPath $backup -Force
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
    Ok "已清理（备份：$backup）"
}

Step "移除 skill"
if (Test-Path $skillDir) { Remove-Item $skillDir -Recurse -Force; Ok "~/.dsh/skills/hardware-monitor" }

Write-Host "`n卸载完成。刷新页面或重启 DSH 后，右上角挂件与 hardware_monitor_status 工具将消失。" -ForegroundColor Green
