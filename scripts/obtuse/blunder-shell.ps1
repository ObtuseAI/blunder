#requires -Version 5.1
<#
.SYNOPSIS
    Launch the localhost-only Blunder Focus Shell.

.DESCRIPTION
    Starts or reuses the dependency-free Blunder web shell on 127.0.0.1.
    The shell exposes only allowlisted actions; provider-backed tasks require
    an explicit per-task approval inside the interface.
#>
[CmdletBinding()]
param(
    [int]$Port = 8767,
    [switch]$NoBrowser,
    [switch]$Foreground,
    [switch]$Smoke,
    [switch]$Stop
)

$ErrorActionPreference = 'Stop'

function Resolve-RepoRoot {
    return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
}

function Get-ShellPaths {
    param([Parameter(Mandatory)] [string]$RepoRoot)
    $artifactRoot = Join-Path $RepoRoot 'artifacts\obtuse\blunder-shell-v2'
    return [ordered]@{
        entrypoint = Join-Path $RepoRoot 'scripts\obtuse\blunder_shell_v2.py'
        artifactRoot = $artifactRoot
        session = Join-Path $artifactRoot 'server.json'
        stdout = Join-Path $artifactRoot 'server.stdout.log'
        stderr = Join-Path $artifactRoot 'server.stderr.log'
    }
}

function Get-PythonPath {
    $python = Get-Command python -ErrorAction Stop
    return [string]$python.Source
}

function Test-ShellHealth {
    param([Parameter(Mandatory)] [int]$ShellPort)
    try {
        $result = Invoke-RestMethod -Uri "http://127.0.0.1:$ShellPort/api/health" -Method Get -TimeoutSec 2
        return ($result.status -eq 'ok')
    }
    catch {
        return $false
    }
}

function Test-OwnedShell {
    param(
        [Parameter(Mandatory)] [int]$ShellPort,
        [Parameter(Mandatory)] [string]$SessionPath,
        [Parameter(Mandatory)] [string]$Entrypoint
    )
    if (-not (Test-Path -LiteralPath $SessionPath)) { return $false }
    try {
        $session = Get-Content -Raw -LiteralPath $SessionPath | ConvertFrom-Json -ErrorAction Stop
        if ([int]$session.port -ne $ShellPort) { return $false }
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$session.pid)" -ErrorAction SilentlyContinue
        if ($null -eq $process) { return $false }
        if ([string]$process.CommandLine -notlike "*$Entrypoint*") { return $false }
        return (Test-ShellHealth -ShellPort $ShellPort)
    }
    catch {
        return $false
    }
}

function Stop-BlunderShell {
    param([Parameter(Mandatory)] [string]$SessionPath, [Parameter(Mandatory)] [string]$Entrypoint)
    if (-not (Test-Path -LiteralPath $SessionPath)) {
        return [ordered]@{ status = 'BLUNDER_SHELL_NOT_RUNNING'; stopped = $false }
    }
    $session = Get-Content -Raw -LiteralPath $SessionPath | ConvertFrom-Json -ErrorAction Stop
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$session.pid)" -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        return [ordered]@{ status = 'BLUNDER_SHELL_STALE_SESSION'; stopped = $false; pid = $session.pid }
    }
    if ([string]$process.CommandLine -notlike "*$Entrypoint*") {
        throw "Refusing to stop PID $($session.pid): command line does not belong to Blunder Shell."
    }
    Stop-Process -Id ([int]$session.pid) -Force -ErrorAction Stop
    return [ordered]@{ status = 'BLUNDER_SHELL_STOPPED'; stopped = $true; pid = $session.pid }
}

$repoRoot = Resolve-RepoRoot
$paths = Get-ShellPaths -RepoRoot $repoRoot
$pythonPath = Get-PythonPath
if (-not (Test-Path -LiteralPath $paths.entrypoint)) {
    throw "Blunder Focus Shell entrypoint is missing: $($paths.entrypoint)"
}

if ($Smoke.IsPresent) {
    & $pythonPath $paths.entrypoint --root $repoRoot --port $Port --smoke
    exit $LASTEXITCODE
}

if ($Stop.IsPresent) {
    Stop-BlunderShell -SessionPath $paths.session -Entrypoint $paths.entrypoint | ConvertTo-Json -Depth 10
    return
}

if ($Foreground.IsPresent) {
    & $pythonPath $paths.entrypoint --root $repoRoot --port $Port
    exit $LASTEXITCODE
}

New-Item -ItemType Directory -Path $paths.artifactRoot -Force | Out-Null
$healthyPort = Test-ShellHealth -ShellPort $Port
$reused = Test-OwnedShell -ShellPort $Port -SessionPath $paths.session -Entrypoint $paths.entrypoint
if ($healthyPort -and -not $reused) {
    throw "Port 127.0.0.1:$Port is owned by another process or Blunder workspace. Choose an unused -Port explicitly."
}
if (-not $reused) {
    Set-Content -LiteralPath $paths.stdout -Value '' -Encoding UTF8
    Set-Content -LiteralPath $paths.stderr -Value '' -Encoding UTF8
    $process = Start-Process -FilePath $pythonPath -ArgumentList @($paths.entrypoint, '--root', $repoRoot, '--port', "$Port") -WorkingDirectory $repoRoot -RedirectStandardOutput $paths.stdout -RedirectStandardError $paths.stderr -WindowStyle Hidden -PassThru
    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        Start-Sleep -Milliseconds 250
        if (Test-ShellHealth -ShellPort $Port) {
            $ready = $true
            break
        }
        if ($process.HasExited) { break }
    }
    if (-not $ready) {
        $errorText = if (Test-Path -LiteralPath $paths.stderr) { Get-Content -Raw -LiteralPath $paths.stderr } else { '' }
        throw "Blunder Focus Shell failed to start on 127.0.0.1:$Port. $errorText"
    }
}

if (-not (Test-Path -LiteralPath $paths.session)) {
    throw "Blunder Focus Shell is healthy but its session file is missing: $($paths.session)"
}
$session = Get-Content -Raw -LiteralPath $paths.session | ConvertFrom-Json -ErrorAction Stop
if (-not (Test-OwnedShell -ShellPort $Port -SessionPath $paths.session -Entrypoint $paths.entrypoint)) {
    throw "Blunder Focus Shell session identity does not match the owned process on 127.0.0.1:$Port."
}
if (-not $NoBrowser.IsPresent) {
    Start-Process -FilePath ([string]$session.url)
}
[ordered]@{
    status = 'BLUNDER_SHELL_READY'
    version = 4
    url = $session.url
    pid = $session.pid
    reused = $reused
    localhostOnly = $true
    providerCallAttempted = $false
    benchmarkLaunchAttempted = $false
} | ConvertTo-Json -Depth 10
