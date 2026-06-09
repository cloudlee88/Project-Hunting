param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir = Join-Path $Root "backend"
$FrontendDir = Join-Path $Root "frontend"
$RuntimeDir = Join-Path $Root ".runtime"
$BackendPort = 8088
$FrontendPort = 3001
$ShortcutPort = 80

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

function Stop-RecordedProcesses {
    if (-not (Test-Path $RuntimeDir)) {
        return
    }
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

function Stop-PortListener {
    param([int]$Port)

    $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    $processIds = @($connections | Select-Object -ExpandProperty OwningProcess -Unique)
    foreach ($processId in $processIds) {
        if (-not $processId -or $processId -eq $PID) {
            continue
        }
        $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host "Stopping existing listener on port $Port (PID $processId, $($proc.ProcessName))"
            Stop-Process -Id $processId -Force
        }
    }
}

function Wait-HttpReady {
    param(
        [string]$Url,
        [string]$Name,
        [int]$TimeoutSeconds = 90
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                Write-Host "$Name is ready: $Url"
                return $true
            }
        } catch {
            Start-Sleep -Seconds 2
        }
    } while ((Get-Date) -lt $deadline)

    Write-Warning "$Name did not become ready within $TimeoutSeconds seconds. Check logs."
    return $false
}

function Test-HttpReady {
    param([string]$Url)

    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
        return ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500)
    } catch {
        return $false
    }
}

if (-not (Test-Path (Join-Path $BackendDir ".venv\Scripts\python.exe"))) {
    throw "Backend virtualenv is missing. Run setup first: cd backend; python -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt"
}

if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
    throw "Frontend dependencies are missing. Run setup first: cd frontend; npm install"
}

New-Item -ItemType Directory -Path (Join-Path $BackendDir "logs") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $FrontendDir "logs") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $Root "logs") -Force | Out-Null
New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null

Stop-RecordedProcesses
Stop-PortListener -Port $FrontendPort
Stop-PortListener -Port $BackendPort

Write-Host "Starting app supervisor"
$supervisorScript = Join-Path $Root "scripts\AppSupervisor.ps1"
$supervisorArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$supervisorScript`" -Root `"$Root`""
$supervisorProcess = Start-Process -FilePath "powershell.exe" -ArgumentList $supervisorArgs -WindowStyle Hidden -PassThru
Set-Content -Path (Join-Path $RuntimeDir "supervisor.pid") -Value $supervisorProcess.Id -Encoding ascii

$backendReady = Wait-HttpReady -Url "http://127.0.0.1:$BackendPort/health" -Name "Backend"
$frontendReady = Wait-HttpReady -Url "http://127.0.0.1:$FrontendPort" -Name "Frontend"

if ($backendReady -and $frontendReady -and -not $NoBrowser) {
    Start-Process "http://localhost:$FrontendPort"
}

$shortcutReady = [bool](Get-NetTCPConnection -LocalPort $ShortcutPort -State Listen -ErrorAction SilentlyContinue)
if ($shortcutReady) {
    Write-Host "Shortcut URL is ready: http://localhost"
}

Write-Host ""
Write-Host "App URL: http://localhost:$FrontendPort"
Write-Host "Shortcut URL: http://localhost"
Write-Host "Backend health: http://localhost:$BackendPort/health"
Write-Host "Logs:"
Write-Host "  $(Join-Path $BackendDir 'logs\backend.log')"
Write-Host "  $(Join-Path $FrontendDir 'logs\frontend.log')"
Write-Host "  $(Join-Path $Root 'logs\supervisor.log')"
Write-Host ""
Write-Host "To stop everything, run: .\Stop-App.ps1"
