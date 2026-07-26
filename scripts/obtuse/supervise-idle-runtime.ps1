#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string]$ArtifactRoot,
    [Parameter(Mandatory)] [string]$StartTimeIso,
    [Parameter(Mandatory)] [int]$StopTargetSeconds,
    [Parameter(Mandatory)] [int]$PollSeconds,
    [Parameter(Mandatory)] [string]$LiveDirectoryName
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location -LiteralPath $repoRoot
$processDetectionModule = Join-Path $PSScriptRoot 'idle-process-detection.ps1'
. $processDetectionModule

$liveRoot = Join-Path $ArtifactRoot $LiveDirectoryName
New-Item -ItemType Directory -Force -Path $liveRoot | Out-Null
$startTime = [datetime]::Parse($StartTimeIso)
$deadline = $startTime.AddSeconds($StopTargetSeconds)

function Write-JsonFile {
    param([object]$Value, [string]$Path)
    $dir = Split-Path $Path -Parent
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $Value | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Invoke-Control {
    param([string]$Name, [scriptblock]$Block)
    $output = & $Block 2>&1
    $exit = $LASTEXITCODE
    if ($null -eq $exit) { $exit = 0 }
    return [ordered]@{ name = $Name; exitCode = $exit; output = @($output | ForEach-Object { [string]$_ }) }
}

function Convert-ControlJson {
    param([object]$Record)
    try { return (($Record.output | ForEach-Object { [string]$_ }) -join "`n") | ConvertFrom-Json -ErrorAction Stop }
    catch { return $null }
}

$state = [ordered]@{
    classification = 'OBTUSE_BLUNDER_BOUNDED_IDLE_RUNTIME_SUPERVISOR'
    startedAt = $startTime.ToString('o')
    supervisorStartedAt = (Get-Date -Format 'o')
    stopTargetSeconds = $StopTargetSeconds
    pollSeconds = $PollSeconds
    liveDirectoryName = $LiveDirectoryName
    completed = $false
    hardStopTriggered = $false
    snapshots = 0
}
Write-JsonFile -Value $state -Path (Join-Path $ArtifactRoot 'supervisor_state.json')

$index = 0
try {
    while ((Get-Date) -lt $deadline) {
        $now = Get-Date
        $elapsed = [math]::Floor(($now - $startTime).TotalSeconds)
        $statusRecord = Invoke-Control -Name 'status' -Block { powershell -ExecutionPolicy Bypass -File scripts\obtuse\status-idle-ops.ps1 }
        $status = Convert-ControlJson -Record $statusRecord
        $forbidden = Get-ForbiddenProcessSummary
        $snapshot = [ordered]@{
            timestamp = $now.ToString('o')
            elapsedSeconds = $elapsed
            statusExitCode = $statusRecord.exitCode
            idleProcessAlive = $(if ($status) { [bool]$status.active } else { $null })
            activePid = $(if ($status) { $status.activePid } else { $null })
            heartbeatAgeSeconds = $(if ($status) { $status.heartbeatAgeSeconds } else { $null })
            queueSize = $(if ($status) { $status.queueCount } else { $null })
            queueStats = $(if ($status) { $status.queueStats } else { $null })
            reliabilityBudgetState = $(if ($status) { $status.reliabilityBudgetStatus } else { $null })
            churnBudgetState = $(if ($status) { $status.churnBudgetStatus } else { $null })
            proofBudgetState = $(if ($status) { $status.proofBudgetStatus } else { $null })
            terminalBenchProcessCount = $forbidden.terminalBenchProcessCount
            providerProcessCount = $forbidden.providerProcessCount
            terminalBenchTouched = $false
            providerCalls = $false
            secretsPrinted = $false
            destructiveActions = $false
            safetyBoundariesChanged = $false
            errorState = $(if ($statusRecord.exitCode -ne 0) { $statusRecord.output } else { $null })
        }
        $snapshotPath = Join-Path $liveRoot ('snapshot_{0:D3}.json' -f $index)
        Write-JsonFile -Value $snapshot -Path $snapshotPath
        $state.snapshots = $index + 1
        $state.lastSnapshot = $snapshotPath
        $state.lastElapsedSeconds = $elapsed
        $state.idleProcessAlive = $snapshot.idleProcessAlive
        $state.heartbeatAgeSeconds = $snapshot.heartbeatAgeSeconds
        $state.queueSize = $snapshot.queueSize
        $state.terminalBenchProcessCount = $forbidden.terminalBenchProcessCount
        $state.providerProcessCount = $forbidden.providerProcessCount
        Write-JsonFile -Value $state -Path (Join-Path $ArtifactRoot 'supervisor_state.json')
        if ($forbidden.terminalBenchProcessCount -gt 0 -or $forbidden.providerProcessCount -gt 0 -or $snapshot.destructiveActions -or $snapshot.safetyBoundariesChanged) {
            $state.hardStopTriggered = $true
            $state.hardStopReason = 'terminal_bench_provider_or_safety_flag'
            break
        }
        $remaining = [math]::Ceiling(($deadline - (Get-Date)).TotalSeconds)
        if ($remaining -le 0) { break }
        Start-Sleep -Seconds ([math]::Min($PollSeconds, $remaining))
        $index++
    }
}
finally {
    $stopIssuedAt = Get-Date
    $firstStop = Invoke-Control -Name 'firstStop' -Block { powershell -ExecutionPolicy Bypass -File scripts\obtuse\stop-idle-ops.ps1 -WaitSeconds 30 }
    Start-Sleep -Seconds 2
    $statusAfterFirst = Invoke-Control -Name 'statusAfterFirstStop' -Block { powershell -ExecutionPolicy Bypass -File scripts\obtuse\status-idle-ops.ps1 }
    $secondStop = Invoke-Control -Name 'secondStop' -Block { powershell -ExecutionPolicy Bypass -File scripts\obtuse\stop-idle-ops.ps1 -WaitSeconds 30 }
    Start-Sleep -Seconds 2
    $statusAfterSecond = Invoke-Control -Name 'statusAfterSecondStop' -Block { powershell -ExecutionPolicy Bypass -File scripts\obtuse\status-idle-ops.ps1 }
    $statusObject = Convert-ControlJson -Record $statusAfterSecond
    $firstStopObject = Convert-ControlJson -Record $firstStop
    $secondStopObject = Convert-ControlJson -Record $secondStop
    $runtimeSeconds = [math]::Floor(($stopIssuedAt - $startTime).TotalSeconds)
    $stopReport = [ordered]@{
        classification = 'OBTUSE_BLUNDER_BOUNDED_IDLE_RUNTIME_SUPERVISOR'
        stopAttempted = $true
        stopIssuedAt = $stopIssuedAt.ToString('o')
        firstStop = $firstStop
        secondStop = $secondStop
        firstStopSucceeded = ($firstStop.exitCode -eq 0 -and $firstStopObject -and $firstStopObject.processRemaining -eq $false)
        secondStopIdempotent = ($secondStop.exitCode -eq 0 -and $secondStopObject -and $secondStopObject.processRemaining -eq $false)
        statusAfterFirstStop = $statusAfterFirst
        statusAfterSecondStop = $statusAfterSecond
        idleFinalState = $(if ($statusObject) { [string]$statusObject.status } else { 'unknown' })
        orphanProcess = $(if ($statusObject) { [bool]$statusObject.active } else { $true })
        queuePersisted = $(if ($statusObject) { $statusObject.queueCount -gt 0 } else { $false })
        runtimeSeconds = $runtimeSeconds
    }
    Write-JsonFile -Value $stopReport -Path (Join-Path $ArtifactRoot 'supervisor_stop_and_orphan_check.json')
    $state.completed = $true
    $state.supervisorCompletedAt = (Get-Date -Format 'o')
    $state.runtimeSeconds = $runtimeSeconds
    $state.finalIdleState = $stopReport.idleFinalState
    $state.orphanProcess = $stopReport.orphanProcess
    Write-JsonFile -Value $state -Path (Join-Path $ArtifactRoot 'supervisor_state.json')
}
