#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Validate','Check','RecoverIfSafe','ReportOnly')]
    [string]$Mode = 'Check',
    [string]$ArtifactRoot = 'artifacts/obtuse/blunder-flash-full-autonomy-compounding-bundle-v1',
    [string]$QueuePath = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle/idle-task-queue.json',
    [string]$IdleArtifactRoot = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle',
    [string]$OperatorApproved = 'false',
    [int]$MaxHeartbeatAgeSeconds = 60
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$processDetectionModule = Join-Path $PSScriptRoot 'idle-process-detection.ps1'
. $processDetectionModule

function Convert-ToBoolean {
    param([object]$Value)
    if ($Value -is [bool]) { return [bool]$Value }
    if ($Value -is [int]) { return ([int]$Value -ne 0) }
    return (([string]$Value).Trim().ToLowerInvariant() -in @('true','1','yes','y','approved'))
}
$OperatorApproved = (Convert-ToBoolean -Value $OperatorApproved) -or (Convert-ToBoolean -Value $env:OBTUSE_OPENROUTER_OPERATOR_APPROVED)

function Resolve-RepoPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $repoRoot $Path
}

function Add-JsonLine {
    param([string]$Path, [object]$Record)
    $resolved = Resolve-RepoPath -Path $Path
    $dir = Split-Path $resolved -Parent
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $Record | ConvertTo-Json -Depth 20 -Compress | Add-Content -LiteralPath $resolved -Encoding UTF8
}

function Read-JsonFile {
    param([string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    if (-not (Test-Path -LiteralPath $resolved)) { return $null }
    try { return Get-Content -Raw -LiteralPath $resolved | ConvertFrom-Json -ErrorAction Stop } catch { return $null }
}

function Read-JsonLines {
    param([string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    $rows = @()
    if (-not (Test-Path -LiteralPath $resolved)) { return $rows }
    foreach ($line in Get-Content -LiteralPath $resolved) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        try { $rows += ($line | ConvertFrom-Json -ErrorAction Stop) } catch {}
    }
    return $rows
}

function Invoke-Status {
    $statusScript = Resolve-RepoPath -Path 'scripts/obtuse/status-idle-ops.ps1'
    try {
        $json = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $statusScript -QueuePath $QueuePath -ArtifactRoot $IdleArtifactRoot 2>&1 | Out-String
        return ($json | ConvertFrom-Json -ErrorAction Stop)
    } catch {
        return @{ error = $_.Exception.Message; raw = $json }
    }
}

function Get-ProviderTimeoutStreak {
    $timeoutLedger = Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'provider_timeout_ledger.jsonl')
    $rows = Read-JsonLines -Path $timeoutLedger
    $streak = 0
    for ($i = $rows.Count - 1; $i -ge 0; $i--) {
        if ($rows[$i].event -in @('TIMEOUT','PROVIDER_TIMEOUT')) { $streak++ } else { break }
    }
    return $streak
}

$artRoot = Resolve-RepoPath -Path $ArtifactRoot
New-Item -ItemType Directory -Force -Path $artRoot | Out-Null

$now = Get-Date -Format 'o'
$status = Invoke-Status

$active = [bool]$status.active
$activePid = $status.activePid
$pidValid = [bool]$status.pidValid
$orphanProcess = [bool]$status.orphanProcess
$pidReuseSuspicion = [bool]$status.pidReuseSuspicion
$heartbeatAge = if ($status.heartbeatAgeSeconds -ne $null) { [double]$status.heartbeatAgeSeconds } else { $null }
$heartbeatStale = ($heartbeatAge -eq $null -or $heartbeatAge -gt $MaxHeartbeatAgeSeconds)
$idleStopped = (-not $active)
$lockStopped = ($status.lockfileState -and $status.lockfileState.status -eq 'stopped')
$invalidPid = ($active -and $activePid -and -not $pidValid)
$pidReuseSuspicion = ($pidReuseSuspicion -and -not $lockStopped)
$orphanProcess = ($orphanProcess -and $active)

$forbidden = Get-ForbiddenProcessSummary
$providerLeftovers = [int]$forbidden.providerProcessCount
$benchmarkLeftovers = [int]$forbidden.terminalBenchProcessCount

$safeExecutable = 0
$queueReadable = $false
if ($status.queueReplenishmentStatus) {
    $safeExecutable = [int]$status.queueReplenishmentStatus.safeExecutableTaskCount
    $queueReadable = [bool]$status.queueReplenishmentStatus.queueReadable
}
$noSafeWork = ($queueReadable -and $safeExecutable -eq 0)

$timeoutStreak = Get-ProviderTimeoutStreak
$timeoutStreakHardStop = ($timeoutStreak -ge 2)

$issues = @()
$notes = @()
if ($idleStopped) { $issues += 'IDLE_STOPPED' }
if ($heartbeatStale) { $issues += 'STALE_HEARTBEAT' }
if ($invalidPid) { $issues += 'INVALID_PID' }
if ($orphanProcess) { $issues += 'ORPHAN_PROCESS' }
if ($pidReuseSuspicion) { $issues += 'PID_REUSE_SUSPECT' }
if ($providerLeftovers -gt 0) { $issues += 'PROVIDER_SUBPROCESS_LEFTOVERS' }
if ($benchmarkLeftovers -gt 0) { $issues += 'BENCHMARK_SUBPROCESS_LEFTOVERS' }
if ($noSafeWork) { $notes += 'NO_SAFE_EXECUTABLE_WORK' }
if ($timeoutStreakHardStop) { $issues += 'PROVIDER_TIMEOUT_STREAK' }

$recoverableIssues = @('IDLE_STOPPED','STALE_HEARTBEAT','INVALID_PID','NO_SAFE_EXECUTABLE_WORK')
$unsafeIssues = @('ORPHAN_PROCESS','PID_REUSE_SUSPECT','PROVIDER_SUBPROCESS_LEFTOVERS','BENCHMARK_SUBPROCESS_LEFTOVERS','PROVIDER_TIMEOUT_STREAK')

$recoveryActions = @()
$recoveryResult = 'NOT_NEEDED'
$recoverySafe = $true
$recoveryError = $null

if ($Mode -eq 'RecoverIfSafe' -and ($issues.Count -gt 0 -or $noSafeWork)) {
    if ($unsafeIssues | Where-Object { $issues -contains $_ }) {
        $recoverySafe = $false
        $recoveryResult = 'RECOVERY_REFUSED_UNSAFE_STATE'
        $recoveryActions += 'No autonomous recovery for unsafe issue; operator review required.'
    } elseif ($OperatorApproved) {
        if ($issues -contains 'IDLE_STOPPED' -or $issues -contains 'STALE_HEARTBEAT' -or $issues -contains 'INVALID_PID') {
            $stopScript = Resolve-RepoPath -Path 'scripts/obtuse/stop-idle-ops.ps1'
            $startScript = Resolve-RepoPath -Path 'scripts/obtuse/start-idle-ops.ps1'
            try {
                & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $stopScript -QueuePath $QueuePath -ArtifactRoot $IdleArtifactRoot | Out-Null
                $recoveryActions += 'Issued stop-idle-ops to clean stale state.'
                Start-Sleep -Seconds 2
                $startResult = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $startScript -QueuePath $QueuePath -ArtifactRoot $IdleArtifactRoot -AutonomyRecovery -OperatorApproved true | ConvertFrom-Json
                $recoveryActions += "Started idle via approved start script; new PID $($startResult.pid)."
                $recoveryResult = 'RECOVERY_SUCCEEDED'
            } catch {
                $recoveryResult = 'RECOVERY_FAILED'
                $recoveryError = $_.Exception.Message
                $recoveryActions += "Recovery error: $recoveryError"
            }
        }
        if ($noSafeWork) {
            $recoveryActions += 'No safe executable work detected; delegating to queue self-maintenance.'
            if ($recoveryResult -eq 'NOT_NEEDED') { $recoveryResult = 'DELEGATED_TO_QUEUE_MAINTENANCE' }
        }
    } else {
        $recoveryResult = 'OPERATOR_APPROVAL_REQUIRED'
        $recoveryActions += 'Recovery requires operator approval.'
    }
}

$checkRecord = [ordered]@{
    timestamp = $now
    mode = $Mode
    idleActive = $active
    activePid = $activePid
    pidValid = $pidValid
    orphanProcess = $orphanProcess
    pidReuseSuspicion = $pidReuseSuspicion
    heartbeatAgeSeconds = $heartbeatAge
    heartbeatStale = $heartbeatStale
    providerLeftovers = $providerLeftovers
    benchmarkLeftovers = $benchmarkLeftovers
    safeExecutableTaskCount = $safeExecutable
    noSafeWork = $noSafeWork
    providerTimeoutStreak = $timeoutStreak
    notes = $notes
    issues = $issues
    recoveryResult = $recoveryResult
    recoveryActions = $recoveryActions
    recoveryError = $recoveryError
    recoverySafe = $recoverySafe
}

Add-JsonLine -Path (Join-Path $ArtifactRoot 'watchdog_check_ledger.jsonl') -Record $checkRecord

$recoverySummary = [ordered]@{
    classification = 'OBTUSE_BLUNDER_IDLE_WATCHDOG_V1'
    timestamp = $now
    mode = $Mode
    checkResult = $(if ($issues.Count -eq 0 -and $recoveryResult -in @('NOT_NEEDED','DELEGATED_TO_QUEUE_MAINTENANCE')) { 'HEALTHY_OR_DELEGATED' } elseif ($issues.Count -eq 0) { 'HEALTHY' } elseif ($recoveryResult -eq 'RECOVERY_SUCCEEDED') { 'RECOVERED' } elseif (-not $recoverySafe) { 'UNSAFE_REVIEW_REQUIRED' } else { 'REVIEW_REQUIRED' })
    notes = $notes
    issues = $issues
    recoveryResult = $recoveryResult
    recoveryActions = $recoveryActions
    recoveryError = $recoveryError
    recoverySafe = $recoverySafe
}
$recoverySummary | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $artRoot 'watchdog_recovery_summary.json') -Encoding UTF8

if ($Mode -eq 'Validate') {
    $validateResult = [ordered]@{
        classification = 'OBTUSE_BLUNDER_IDLE_WATCHDOG_V1'
        timestamp = $now
        mode = 'Validate'
        scriptPresent = $true
        checkOutputPresent = (Test-Path -LiteralPath (Join-Path $artRoot 'watchdog_check_ledger.jsonl'))
        summaryOutputPresent = (Test-Path -LiteralPath (Join-Path $artRoot 'watchdog_recovery_summary.json'))
        result = $(if ((Test-Path -LiteralPath (Join-Path $artRoot 'watchdog_check_ledger.jsonl')) -and (Test-Path -LiteralPath (Join-Path $artRoot 'watchdog_recovery_summary.json'))) { 'PASS' } else { 'FAIL' })
    }
    $validateResult | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $artRoot 'watchdog_validate_result.json') -Encoding UTF8
    $validateResult | ConvertTo-Json -Depth 10
    return
}

$checkRecord | ConvertTo-Json -Depth 20
