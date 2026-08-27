param(
    [Parameter(Mandatory = $true)]
    [string]$Root
)

$ErrorActionPreference = "Continue"

$BackendDir = Join-Path $Root "backend"
$FrontendDir = Join-Path $Root "frontend"
$RuntimeDir = Join-Path $Root ".runtime"
$LogDir = Join-Path $Root "logs"
$BackendPort = 8088
$FrontendPort = 3001
$ShortcutPort = 80

New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $BackendDir "logs") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $FrontendDir "logs") -Force | Out-Null

function Test-HttpReady {
    param([string]$Url)
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
        return ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500)
    } catch {
        return $false
    }
}

function Test-PortListening {
    param([int]$Port)
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Get-RecordedPid {
    param([string]$Name)
    $path = Join-Path $RuntimeDir "$Name.pid"
    if (-not (Test-Path $path)) {
        return 0
    }
    $raw = Get-Content $path -ErrorAction SilentlyContinue | Select-Object -First 1
    [int]$recordedPid = 0
    if ([int]::TryParse($raw, [ref]$recordedPid)) {
        return $recordedPid
    }
    return 0
}

function Stop-ProcessTree {
    param([int]$ProcessId)
    if (-not $ProcessId -or $ProcessId -eq $PID) {
        return
    }
    $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($proc) {
        & taskkill.exe /PID $ProcessId /T /F | Out-Null
    }
}

function Start-ManagedProcess {
    param(
        [string]$Name,
        [string]$Command
    )
    $oldPid = Get-RecordedPid -Name $Name
    Stop-ProcessTree -ProcessId $oldPid
    $proc = Start-Process -FilePath "cmd.exe" -ArgumentList "/c", $Command -WindowStyle Hidden -PassThru
    Set-Content -Path (Join-Path $RuntimeDir "$Name.pid") -Value $proc.Id -Encoding ascii
}

$backendLog = Join-Path $BackendDir "logs\backend.log"
$frontendLog = Join-Path $FrontendDir "logs\frontend.log"
$redirectLog = Join-Path $LogDir "localhost-redirect.log"
$supervisorLog = Join-Path $LogDir "supervisor.log"

$backendCommand = "chcp 65001 >NUL && set `"PYTHONUTF8=1`" && set `"PYTHONIOENCODING=utf-8`" && cd /d `"$BackendDir`" && .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port $BackendPort --loop asyncio >> `"$backendLog`" 2>&1"
$frontendCommand = "chcp 65001 >NUL && cd /d `"$FrontendDir`" && npm.cmd run dev >> `"$frontendLog`" 2>&1"
$redirectCommand = "cd /d `"$Root`" && set `"LISTEN_PORT=$ShortcutPort`" && set `"TARGET_PORT=$FrontendPort`" && node scripts\localhost-redirect.js >> `"$redirectLog`" 2>&1"

"$(Get-Date -Format s) supervisor started" | Add-Content -Path $supervisorLog -Encoding utf8

while ($true) {
    if (-not (Test-HttpReady -Url "http://127.0.0.1:$BackendPort/health")) {
        "$(Get-Date -Format s) backend unhealthy; restarting" | Add-Content -Path $supervisorLog -Encoding utf8
        Start-ManagedProcess -Name "backend" -Command $backendCommand
        Start-Sleep -Seconds 5
    }

    # Next.js dev server may block while compiling large routes such as /programs.
    # Treat a listening port as healthy so the supervisor does not kill the frontend
    # in the middle of route compilation and reset browser connections.
    if (-not (Test-PortListening -Port $FrontendPort)) {
        "$(Get-Date -Format s) frontend port missing; restarting" | Add-Content -Path $supervisorLog -Encoding utf8
        Start-ManagedProcess -Name "frontend" -Command $frontendCommand
        Start-Sleep -Seconds 5
    }

    if (-not (Test-PortListening -Port $ShortcutPort)) {
        "$(Get-Date -Format s) localhost shortcut missing; restarting" | Add-Content -Path $supervisorLog -Encoding utf8
        Start-ManagedProcess -Name "redirect" -Command $redirectCommand
        Start-Sleep -Seconds 2
    }

    Start-Sleep -Seconds 5
}
