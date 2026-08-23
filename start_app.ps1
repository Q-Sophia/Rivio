[CmdletBinding()]
param(
    [int]$Port = 8001,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$repoRoot = $PSScriptRoot
$backendDir = Join-Path $repoRoot "backend"
$appUrl = "http://127.0.0.1:$Port/"
$healthUrl = "http://127.0.0.1:$Port/api/health"
$logDir = Join-Path $backendDir "app\data\logs"
$logPath = Join-Path $logDir "api-$Port.startup.log"
$pidPath = Join-Path $logDir "api-$Port.pid"

function Test-AppHealth {
    try {
        $response = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
        return $response.status -eq "ok"
    }
    catch {
        return $false
    }
}

function Get-PortOwner {
    $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -ne $connection) {
        return $connection.OwningProcess
    }

    foreach ($line in (& netstat.exe -ano -p tcp)) {
        if ($line -match "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$") {
            return [int]$Matches[1]
        }
    }
    return $null
}

function Open-AppPage {
    if ($NoBrowser) {
        return
    }

    $openInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $openInfo.FileName = $appUrl
    $openInfo.UseShellExecute = $true
    [void][System.Diagnostics.Process]::Start($openInfo)
}

if (-not (Test-Path -LiteralPath $backendDir -PathType Container)) {
    throw "Backend directory was not found: $backendDir"
}

New-Item -ItemType Directory -Path $logDir -Force | Out-Null

if (Test-AppHealth) {
    $runningPid = Get-PortOwner
    if ($null -ne $runningPid) {
        Set-Content -LiteralPath $pidPath -Value $runningPid -Encoding ASCII
    }
    Write-Host "Competitive Intelligence Assistant is already running." -ForegroundColor Green
    Write-Host "URL: $appUrl"
    if ($null -ne $runningPid) {
        Write-Host "Backend PID: $runningPid"
    }
    Open-AppPage
    exit 0
}

$occupiedPid = Get-PortOwner
if ($null -ne $occupiedPid) {
    Write-Host "Port $Port is occupied by process $occupiedPid, but the health check failed." -ForegroundColor Red
    Write-Host "The launcher will not stop an unrelated process automatically."
    exit 1
}

$pythonCandidates = @(
    (Join-Path $repoRoot "..\competitive-analysis-agent-main\competitive-analysis-agent-main\backend\.venv\Scripts\python.exe"),
    (Join-Path $backendDir ".venv\Scripts\python.exe"),
    (Join-Path $repoRoot ".venv\Scripts\python.exe")
)

$pythonPath = $pythonCandidates |
    Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
    Select-Object -First 1

if ($null -eq $pythonPath) {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -ne $pythonCommand) {
        $pythonPath = $pythonCommand.Source
    }
}

if ($null -eq $pythonPath) {
    Write-Host "No usable Python runtime was found." -ForegroundColor Red
    Write-Host "Create a virtual environment or restore the neighboring competitive-analysis-agent-main project."
    exit 1
}

$pythonPath = [System.IO.Path]::GetFullPath($pythonPath)
$backendEscaped = $backendDir.Replace("'", "''")
$pythonEscaped = $pythonPath.Replace("'", "''")
$logEscaped = $logPath.Replace("'", "''")

$startedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -LiteralPath $logPath -Value "`r`n[$startedAt] Starting FastAPI backend on $appUrl" -Encoding UTF8

$runnerCommand = @"
`$ErrorActionPreference = 'Continue'
Set-Location -LiteralPath '$backendEscaped'
& '$pythonEscaped' -m uvicorn app.api.main:app --host 127.0.0.1 --port $Port *>> '$logEscaped'
exit `$LASTEXITCODE
"@

$encodedCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($runnerCommand))
$powershellCommand = Get-Command powershell.exe -ErrorAction SilentlyContinue
if ($null -eq $powershellCommand) {
    $powershellCommand = Get-Command pwsh.exe -ErrorAction SilentlyContinue
}
if ($null -eq $powershellCommand) {
    Write-Host "No usable PowerShell runtime was found for the background service." -ForegroundColor Red
    exit 1
}
$powershellPath = $powershellCommand.Source
$startInfo = [System.Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $powershellPath
$startInfo.Arguments = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand $encodedCommand"
$startInfo.WorkingDirectory = $backendDir
$startInfo.UseShellExecute = $true
$startInfo.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden

try {
    [void][System.Diagnostics.Process]::Start($startInfo)
}
catch {
    Write-Host "Unable to start the background process: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

$healthy = $false
for ($attempt = 1; $attempt -le 30; $attempt++) {
    Start-Sleep -Milliseconds 500
    if (Test-AppHealth) {
        $healthy = $true
        break
    }
}

if (-not $healthy) {
    Write-Host "The backend did not pass its health check within 15 seconds." -ForegroundColor Red
    Write-Host "Log: $logPath"
    if (Test-Path -LiteralPath $logPath) {
        Write-Host "Recent log output:"
        Get-Content -LiteralPath $logPath -Tail 20
    }
    exit 1
}

$backendPid = Get-PortOwner
if ($null -ne $backendPid) {
    Set-Content -LiteralPath $pidPath -Value $backendPid -Encoding ASCII
}

Write-Host "Competitive Intelligence Assistant started successfully." -ForegroundColor Green
Write-Host "URL: $appUrl"
Write-Host "Health check: passed"
if ($null -ne $backendPid) {
    Write-Host "Backend PID: $backendPid"
}
Write-Host "Log: $logPath"

Open-AppPage
