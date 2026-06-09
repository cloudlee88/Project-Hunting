$ErrorActionPreference = "SilentlyContinue"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$RuntimeDir = Join-Path $Root ".runtime"

function Stop-ProcessTree {
    param([int]$ProcessId)

    if (-not $ProcessId -or $ProcessId -eq $PID) {
        return
    }
    $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($proc) {
        Write-Host "Stopping recorded process tree (PID $ProcessId, $($proc.ProcessName))"
        & taskkill.exe /PID $ProcessId /T /F | Out-Null
    }
}

if (Test-Path $RuntimeDir) {
    foreach ($file in @("supervisor.pid", "backend.pid", "frontend.pid", "redirect.pid")) {
        $path = Join-Path $RuntimeDir $file
        if (Test-Path $path) {
            $raw = (Get-Content $path -ErrorAction SilentlyContinue | Select-Object -First 1)
            [int]$recordedPid = 0
            if ([int]::TryParse($raw, [ref]$recordedPid)) {
                Stop-ProcessTree -ProcessId $recordedPid
            }
            Remove-Item $path -Force -ErrorAction SilentlyContinue
        }
    }
}

$Ports = @(3001, 8088)
foreach ($port in $Ports) {
    $connections = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    $processIds = @($connections | Select-Object -ExpandProperty OwningProcess -Unique)
    foreach ($processId in $processIds) {
        if (-not $processId -or $processId -eq $PID) {
            continue
        }
        $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host "Stopping port $port (PID $processId, $($proc.ProcessName))"
            Stop-Process -Id $processId -Force
        }
    }
}

Start-Sleep -Seconds 2

Write-Host "Stopped app listeners on ports 3001 and 8088."
