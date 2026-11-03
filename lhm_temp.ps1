# 通过 LibreHardwareMonitorLib 读取 CPU 内核级温度（CPU Package / Core Max）。
# 需要管理员权限：该库依赖内核驱动读 MSR，普通权限下传感器能枚举但读数恒为空。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File lhm_temp.ps1 -DllPath <dll> -IntervalMs 2000
# 输出：每行一条 JSON，持续输出到 stdout（供 server.py 逐行读取）。
param(
    [Parameter(Mandatory = $true)][string]$DllPath,
    [int]$IntervalMs = 2000
)

function Send-Json($payload) {
    $payload | ConvertTo-Json -Compress
    [Console]::Out.Flush()
}

$ErrorActionPreference = 'Stop'

# 固定输出为 UTF-8：Windows PowerShell 默认按控制台代码页写出，
# 父进程按 UTF-8 解码会拿到替换字符（\ufffd）
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# LHM 通过 P/Invoke 加载 PawnIO 的用户态库 PawnIOLib.dll，它既不在 FanControl 目录
# 也不在 PATH 上；找不到就会表现为"传感器读数全空"。这里把 PawnIO 安装目录补进 PATH。
foreach ($pawnDir in @('C:\Program Files\PawnIO', 'C:\Program Files (x86)\PawnIO')) {
    if (Test-Path (Join-Path $pawnDir 'PawnIOLib.dll')) {
        $env:PATH = "$pawnDir;$env:PATH"
        break
    }
}

$isElevated = $false
try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $isElevated = ([Security.Principal.WindowsPrincipal]$identity).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
} catch { }

$dllDir = Split-Path -Parent $DllPath
Push-Location $dllDir

# 依赖解析：加载方式改成「按字节加载」而不是 LoadFrom。
# 从可写目录/受策略限制的路径用 LoadFrom 会报 0x80131515（Operation is not supported），
# 而按字节加载在同一条路径上是正常的。
$resolveHandler = [System.ResolveEventHandler]{
    param($sender, $eventArgs)
    $simpleName = (New-Object System.Reflection.AssemblyName($eventArgs.Name)).Name
    $candidate = Join-Path $dllDir ($simpleName + '.dll')
    if (Test-Path $candidate) {
        return [System.Reflection.Assembly]::Load([System.IO.File]::ReadAllBytes($candidate))
    }
    return $null
}
[System.AppDomain]::CurrentDomain.add_AssemblyResolve($resolveHandler)

try {
    $assembly = [System.Reflection.Assembly]::Load([System.IO.File]::ReadAllBytes($DllPath))
} catch {
    Send-Json @{ ok = $false; error = "无法加载 LibreHardwareMonitorLib: $($_.Exception.Message)" }
    Pop-Location
    exit 1
}

# 预加载同目录下的依赖（HidSharp / System.Management 等），失败不影响主流程
Get-ChildItem $dllDir -Filter *.dll -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -ne (Split-Path -Leaf $DllPath) } |
    ForEach-Object {
        try { [System.Reflection.Assembly]::Load([System.IO.File]::ReadAllBytes($_.FullName)) | Out-Null } catch { }
    }

$computerType = $assembly.GetType('LibreHardwareMonitor.Hardware.Computer')
if (-not $computerType) {
    Send-Json @{ ok = $false; error = '程序集里找不到 Computer 类型，可能不是 LibreHardwareMonitorLib' }
    Pop-Location
    exit 1
}

$computer = $null
try {
    $computer = [Activator]::CreateInstance($computerType)
    $computer.IsCpuEnabled = $true
    $computer.Open()
} catch {
    Send-Json @{ ok = $false; error = "打开硬件监控失败: $($_.Exception.Message)" }
    Pop-Location
    exit 2
}

$cpu = $computer.Hardware | Where-Object { "$($_.HardwareType)" -eq 'Cpu' } | Select-Object -First 1
if (-not $cpu) {
    Send-Json @{ ok = $false; error = '未找到 CPU 硬件对象' }
    $computer.Close()
    Pop-Location
    exit 3
}

$nullRounds = 0
try {
    while ($true) {
        $cpu.Update()
        $temps = @($cpu.Sensors | Where-Object { "$($_.SensorType)" -eq 'Temperature' })

        # 优先 CPU Package，其次 Core Max / Core Average
        $picked = $temps | Where-Object { $_.Name -eq 'CPU Package' } | Select-Object -First 1
        if (-not $picked -or $null -eq $picked.Value) {
            $picked = $temps | Where-Object { $_.Name -eq 'Core Max' } | Select-Object -First 1
        }
        if (-not $picked -or $null -eq $picked.Value) {
            $picked = $temps | Where-Object { $_.Name -eq 'Core Average' } | Select-Object -First 1
        }

        if (-not $picked -or $null -eq $picked.Value) {
            $nullRounds++
            # 连续 3 轮都读不到，基本可以判定是权限/驱动问题，直接把结论报给上层
            if ($nullRounds -ge 3) {
                $reason = if (-not $isElevated) {
                    '传感器读数全空：PawnIO 驱动需要管理员权限（当前进程未提权）'
                } else {
                    '传感器读数全空：已以管理员运行，但 PawnIO 仍未返回读数（驱动被拦截或 PawnIOLib 未找到）'
                }
                Send-Json @{ ok = $false; error = $reason; elevated = $isElevated }
                break
            }
        } else {
            $nullRounds = 0
            $cores = @()
            foreach ($t in $temps) {
                if ($t.Name -like 'CPU Core #*' -and $null -ne $t.Value) {
                    $cores += [pscustomobject]@{ name = $t.Name; value = [math]::Round([double]$t.Value, 1) }
                }
            }
            Send-Json @{
                ok       = $true
                name     = "$($picked.Name)"
                value    = [math]::Round([double]$picked.Value, 1)
                cores    = $cores
                elevated = $isElevated
            }
        }

        Start-Sleep -Milliseconds $IntervalMs
    }
} finally {
    if ($computer) { $computer.Close() }
    Pop-Location
}
