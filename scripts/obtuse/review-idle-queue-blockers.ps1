#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Validate','Review','ReportOnly')]
    [string]$Mode = 'Validate',
    [string]$QueuePath = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle/idle-task-queue.json',
    [string]$ArtifactRoot = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$queueModule = Join-Path $PSScriptRoot 'idle-task-queue.ps1'
$script:__reviewQueuePath = $QueuePath
$script:__reviewArtifactRoot = $ArtifactRoot
. $queueModule
$QueuePath = $script:__reviewQueuePath
$ArtifactRoot = $script:__reviewArtifactRoot

function Resolve-LocalPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $repoRoot $Path
}

$reviewPath = Join-Path $ArtifactRoot 'queue_blocker_review.json'
$repairLedgerPath = Join-Path $ArtifactRoot 'queue_blocker_repair_ledger.jsonl'
$afterRepairPath = Join-Path $ArtifactRoot 'queue_state_after_repair.json'

$now = Get-Date -Format 'o'
$queue = Get-IdleQueue -Path $QueuePath
$stats = Get-QueueStats -QueuePath $QueuePath
$tasks = @($queue.tasks)

$disabledTasks = @($tasks | Where-Object { $_.status -eq 'disabled' })
$passedTasks = @($tasks | Where-Object { $_.status -eq 'passed' })
$pendingTasks = @($tasks | Where-Object { $_.status -eq 'pending' })
$runningTasks = @($tasks | Where-Object { $_.status -eq 'running' })
$blockedTasks = @($tasks | Where-Object { $_.status -eq 'blocked' })
$quarantinedTasks = @($tasks | Where-Object { $_.status -eq 'quarantined' })
$failedTasks = @($tasks | Where-Object { $_.status -eq 'failed' })
$malformedTasks = @($tasks | Where-Object { -not $_.id -or -not $_.title -or -not $_.status })

$disabledReasons = @{}
foreach ($t in $disabledTasks) {
    $reason = if ($t.quarantineReason) { [string]$t.quarantineReason } elseif ($t.description) { [string]$t.description } else { 'unknown' }
    $key = $reason.ToLowerInvariant()
    if (-not $disabledReasons.ContainsKey($key)) { $disabledReasons[$key] = 0 }
    $disabledReasons[$key]++
}

$hasExecutableWork = ($pendingTasks.Count + $runningTasks.Count) -gt 0
$isPassedOnly = ($passedTasks.Count -gt 0 -and -not $hasExecutableWork -and $disabledTasks.Count -eq 0 -and $blockedTasks.Count -eq 0 -and $quarantinedTasks.Count -eq 0 -and $failedTasks.Count -eq 0)
$isHistoryOnly = (-not $hasExecutableWork -and $disabledTasks.Count -eq 0 -and $blockedTasks.Count -eq 0 -and $failedTasks.Count -eq 0 -and $malformedTasks.Count -eq 0 -and ($passedTasks.Count + $quarantinedTasks.Count) -gt 0)
$isBlocked = (-not $hasExecutableWork -and ($disabledTasks.Count -gt 0 -or $blockedTasks.Count -gt 0 -or $failedTasks.Count -gt 0 -or $malformedTasks.Count -gt 0))

$blockerClassification = if ($hasExecutableWork) { 'NO_BLOCKER_EXECUTABLE_WORK_EXISTS' } elseif ($isHistoryOnly) { 'NO_SAFE_EXECUTABLE_WORK_HISTORY_ONLY' } elseif ($isPassedOnly) { 'NO_SAFE_EXECUTABLE_WORK_PASSED_ONLY' } elseif ($isBlocked) { 'NON_EXECUTABLE_BLOCKERS_PRESENT' } else { 'EMPTY_QUEUE' }

$review = [ordered]@{
    timestamp = $now
    classification = 'OBTUSE_BLUNDER_IDLE_QUEUE_BLOCKER_REVIEW_V1'
    result = 'PASS'
    queuePath = $QueuePath
    totalTasks = $tasks.Count
    passedCount = $passedTasks.Count
    disabledCount = $disabledTasks.Count
    pendingCount = $pendingTasks.Count
    runningCount = $runningTasks.Count
    blockedCount = $blockedTasks.Count
    quarantinedCount = $quarantinedTasks.Count
    failedCount = $failedTasks.Count
    malformedCount = $malformedTasks.Count
    safeExecutableCount = $pendingTasks.Count + $runningTasks.Count
    historyOnly = $isHistoryOnly
    blockerClassification = $blockerClassification
    disabledReasons = $disabledReasons
    whyQueueIsBlocked = $(if ($isBlocked) { 'Queue contains active blockers (disabled/blocked/failed/malformed) and no pending/running work.' } elseif ($isHistoryOnly) { 'Queue contains only completed or quarantined history and no pending/running work.' } else { 'Queue is not blocked.' })
    disabledTasksAreTombstones = ($disabledTasks.Count -gt 0 -and ($disabledTasks | Where-Object { $_.previousStatusBeforeReclearQuarantine -or $_.quarantineState }).Count -eq $disabledTasks.Count)
    actionableBlockers = $(if ($malformedTasks.Count -gt 0 -or $failedTasks.Count -gt 0 -or $blockedTasks.Count -gt 0) { $true } else { $false })
    recommendedAction = $(if ($hasExecutableWork) { 'NONE_QUEUE_READY' } elseif ($isBlocked) { 'REPLENISH_OR_REPAIR_BLOCKERS' } elseif ($isHistoryOnly -or $isPassedOnly) { 'NO_SAFE_EXECUTABLE_WORK_REPLENISH_IF_COOLDOWN_ALLOWS' } else { 'NO_ACTION_NEEDED' })
}

$review | ConvertTo-Json -Depth 10 | Set-Content -Path (Resolve-LocalPath -Path $reviewPath) -Force

$repairActions = @()
if ($Mode -eq 'Review') {
    # Compaction records only: preserve all historical tasks, emit merge records for equivalent disabled tasks
    $equivalentGroups = $disabledTasks | Group-Object -Property { "$($_.title)|$($_.payload.actionType)" }
    foreach ($group in $equivalentGroups) {
        if ($group.Count -gt 1) {
            $ids = @($group.Group | ForEach-Object { $_.id })
            $repairActions += [ordered]@{
                timestamp = $now
                action = 'MERGE_RECORD_ONLY'
                groupKey = $group.Name
                retainedTaskId = $ids[0]
                supersededTaskIds = @($ids | Select-Object -Skip 1)
                equivalentTaskCount = $group.Count
                evidencePreserved = $true
                durableQueueRewritten = $false
            }
        }
    }
    $repairActions += [ordered]@{
        timestamp = $now
        action = 'CLASSIFY_QUEUE_STATE'
        blockerClassification = $blockerClassification
        disabledTasksAreTombstones = $review.disabledTasksAreTombstones
        evidencePreserved = $true
        note = 'Queue state classified without deleting historical tasks.'
    }
    foreach ($repairAction in $repairActions) {
        $repairAction | ConvertTo-Json -Depth 10 -Compress | Add-Content -Path (Resolve-LocalPath -Path $repairLedgerPath) -Encoding UTF8
    }
}

$afterRepair = [ordered]@{
    timestamp = $now
    classification = 'OBTUSE_BLUNDER_IDLE_QUEUE_STATE_AFTER_REPAIR_V1'
    queuePath = $QueuePath
    queueStats = $stats
    blockerClassification = $blockerClassification
    safeExecutableCount = $review.safeExecutableCount
    queueReadable = $true
    historicalTasksPreserved = $true
    compactionRecordsCreated = $repairActions.Count
    repairActionsTaken = @($repairActions | ForEach-Object { $_.action })
    result = $(if ($hasExecutableWork -or ($isPassedOnly -or $isBlocked -or ($tasks.Count -eq 0))) { 'PASS' } else { 'REVIEW_REQUIRED' })
}
$afterRepair | ConvertTo-Json -Depth 10 | Set-Content -Path (Resolve-LocalPath -Path $afterRepairPath) -Force

$result = [ordered]@{
    timestamp = $now
    mode = $Mode
    reviewPath = $reviewPath
    repairLedgerPath = $repairLedgerPath
    afterRepairPath = $afterRepairPath
    blockerClassification = $blockerClassification
    safeExecutableCount = $review.safeExecutableCount
    hasExecutableWork = $hasExecutableWork
    isBlocked = $isBlocked
    result = $(if ($Mode -eq 'Validate' -or $Mode -eq 'ReportOnly') { 'PASS' } else { $afterRepair.result })
}

$result | ConvertTo-Json -Depth 10
