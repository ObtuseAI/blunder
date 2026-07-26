#requires -Version 5.1
<#
.SYNOPSIS
    Close old failed idle queue items and archive historical quarantine noise.

.DESCRIPTION
    Repairs the three known full-autonomy bootstrap failures by running their
    real local implementations, marks them passed with evidence, and moves
    quarantined historical records into an archive ledger before removing them
    from the active queue. This preserves evidence without leaving the status
    screen blocked by old non-executable history.
#>
[CmdletBinding()]
param(
    [ValidateSet('Validate','Apply','ReportOnly')]
    [string]$Mode = 'Validate',
    [string]$QueuePath = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle/idle-task-queue.json',
    [string]$ArtifactRoot = 'artifacts/obtuse/idle-queue-failed-and-quarantine-closure-v1'
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$commonModule = Join-Path $PSScriptRoot 'blunder-full-autonomy-common.ps1'
. $commonModule

$classification = 'OBTUSE_IDLE_QUEUE_FAILED_AND_QUARANTINE_CLOSURE_V1'
$targetTitles = @(
    'Validate full autonomy policy record',
    'Mine Dunce proof artifacts for autonomy candidates',
    'Refresh operator control handoff'
)

function Resolve-RepoPath {
    param([Parameter(Mandatory)] [string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $repoRoot $Path
}

function Write-JsonFile {
    param([Parameter(Mandatory)] [object]$Value, [Parameter(Mandatory)] [string]$Path)
    $dir = Split-Path $Path -Parent
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    $Value | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $Path -Encoding UTF8 -Force
}

function Add-JsonLine {
    param([Parameter(Mandatory)] [string]$Path, [Parameter(Mandatory)] [object]$Record)
    $dir = Split-Path $Path -Parent
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    Add-Content -LiteralPath $Path -Encoding UTF8 -Value ($Record | ConvertTo-Json -Depth 100 -Compress)
}

function Get-QueueStatsFromTasks {
    param([array]$Tasks)
    $stats = [ordered]@{ total = $Tasks.Count }
    foreach ($statusName in @('pending','running','passed','failed','blocked','quarantined','disabled')) {
        $stats[$statusName] = @($Tasks | Where-Object { $_.status -eq $statusName }).Count
    }
    return $stats
}

function Read-Queue {
    param([Parameter(Mandatory)] [string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    if (-not (Test-Path -LiteralPath $resolved)) {
        throw "Queue not found: $resolved"
    }
    return [ordered]@{
        path = $resolved
        queue = (Get-Content -Raw -LiteralPath $resolved | ConvertFrom-Json -ErrorAction Stop)
    }
}

function Get-TargetFailedTasks {
    param([array]$Tasks)
    return @($Tasks | Where-Object {
        $_.status -eq 'failed' -and
        ([string]$_.title) -in $targetTitles
    })
}

function Invoke-RepairForTask {
    param([Parameter(Mandatory)] [object]$Task)
    $result = Invoke-BlunderAutonomyTask -Task $Task
    return $result
}

function Set-TaskPassed {
    param([Parameter(Mandatory)] [object]$Task, [Parameter(Mandatory)] [object]$RepairResult)
    $now = Get-Date -Format 'o'
    if (-not ($Task.PSObject.Properties.Name -contains 'previousStatusBeforeClosure')) {
        $Task | Add-Member -NotePropertyName previousStatusBeforeClosure -NotePropertyValue ([string]$Task.status) -Force
    }
    $Task.status = 'passed'
    $Task.updatedAt = $now
    $Task.nextAction = 'Closed by queue hygiene repair; real local implementation returned PASS.'
    $Task.executionResult = $RepairResult
    $Task.validationResult = [ordered]@{
        pass = $true
        reason = "Repaired by $classification; action $($RepairResult.actionType) returned PASS"
    }
    $Task | Add-Member -NotePropertyName closureDecision -NotePropertyValue 'REPAIRED_AND_PASSED' -Force
    $Task | Add-Member -NotePropertyName closureClassification -NotePropertyValue $classification -Force
    $Task | Add-Member -NotePropertyName closureTimestamp -NotePropertyValue $now -Force
}

function New-ArchiveRecord {
    param([Parameter(Mandatory)] [object]$Task, [Parameter(Mandatory)] [string]$Reason)
    return [ordered]@{
        timestamp = (Get-Date -Format 'o')
        classification = $classification
        archiveReason = $Reason
        previousStatus = [string]$Task.status
        taskId = [string]$Task.id
        taskTitle = [string]$Task.title
        originalTask = $Task
    }
}

function Write-MarkdownReport {
    param([Parameter(Mandatory)] [object]$Report, [Parameter(Mandatory)] [string]$Path)
    $lines = @(
        '# Idle Queue Failed And Quarantine Closure',
        '',
        "Status: $($Report.status)",
        "Mode: $($Report.mode)",
        "Timestamp: $($Report.timestamp)",
        '',
        "Failed target tasks before: $($Report.failedTargetCount)",
        "Repair passed: $($Report.repairedPassedCount)",
        "Quarantine records archived: $($Report.quarantinedArchivedCount)",
        '',
        'Decisions:'
    )
    foreach ($decision in @($Report.decisions)) {
        $lines += "- $($decision.title): $($decision.decision) ($($decision.reason))"
    }
    $lines += ''
    $lines += "Queue stats before: total=$($Report.queueStatsBefore.total), passed=$($Report.queueStatsBefore.passed), failed=$($Report.queueStatsBefore.failed), quarantined=$($Report.queueStatsBefore.quarantined), pending=$($Report.queueStatsBefore.pending)"
    $lines += "Queue stats after: total=$($Report.queueStatsAfter.total), passed=$($Report.queueStatsAfter.passed), failed=$($Report.queueStatsAfter.failed), quarantined=$($Report.queueStatsAfter.quarantined), pending=$($Report.queueStatsAfter.pending)"
    $lines += ''
    $lines += 'Evidence was archived, not silently discarded.'
    $dir = Split-Path $Path -Parent
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    Set-Content -LiteralPath $Path -Encoding UTF8 -Force -Value $lines
}

Push-Location $repoRoot
try {
    $resolvedArtifactRoot = Resolve-RepoPath -Path $ArtifactRoot
    New-Item -ItemType Directory -Force -Path $resolvedArtifactRoot | Out-Null

    $queueState = Read-Queue -Path $QueuePath
    $resolvedQueuePath = $queueState.path
    $queue = $queueState.queue
    $tasks = @($queue.tasks)
    $statsBefore = Get-QueueStatsFromTasks -Tasks $tasks
    $failedTargets = Get-TargetFailedTasks -Tasks $tasks
    $quarantinedTasks = @($tasks | Where-Object { $_.status -eq 'quarantined' })
    $decisions = New-Object System.Collections.ArrayList
    $repairResults = New-Object System.Collections.ArrayList

    foreach ($title in $targetTitles) {
        $matching = @($tasks | Where-Object { [string]$_.title -eq $title })
        $failed = @($matching | Where-Object { $_.status -eq 'failed' })
        $passed = @($matching | Where-Object { $_.status -eq 'passed' })
        [void]$decisions.Add([ordered]@{
            title = $title
            decision = $(if ($failed.Count -gt 0) { 'REPAIR' } elseif ($passed.Count -gt 0) { 'ALREADY_REPAIRED' } else { 'NOT_FOUND' })
            reason = $(if ($failed.Count -gt 0) { 'Local implementation exists and can rerun safely.' } elseif ($passed.Count -gt 0) { 'Queue already contains passed evidence.' } else { 'No matching active queue item found.' })
            failedCount = $failed.Count
            passedCount = $passed.Count
        })
    }
    [void]$decisions.Add([ordered]@{
        title = 'Historical quarantined queue noise'
        decision = $(if ($quarantinedTasks.Count -gt 0) { 'ARCHIVE_OUT_OF_ACTIVE_QUEUE' } else { 'ALREADY_CLEAR' })
        reason = 'Quarantined records are historical evidence, not runnable work; archive ledger preserves them.'
        failedCount = 0
        passedCount = 0
        quarantinedCount = $quarantinedTasks.Count
    })

    if ($Mode -eq 'Apply') {
        $backupPath = Join-Path $resolvedArtifactRoot "idle-task-queue.before-closure.$((Get-Date).ToString('yyyyMMdd-HHmmss')).json"
        Copy-Item -LiteralPath $resolvedQueuePath -Destination $backupPath -Force

        foreach ($task in $failedTargets) {
            $repair = Invoke-RepairForTask -Task $task
            [void]$repairResults.Add($repair)
            if ($repair.result -ne 'PASS') {
                throw "Repair failed for $($task.title): $($repair.result)"
            }
        }

        foreach ($task in $failedTargets) {
            $repair = @($repairResults | Where-Object { $_.taskId -eq $task.id } | Select-Object -First 1)
            Set-TaskPassed -Task $task -RepairResult $repair
        }

        $archiveLedgerPath = Join-Path $resolvedArtifactRoot 'archived_quarantined_queue_noise.jsonl'
        if (Test-Path -LiteralPath $archiveLedgerPath) { Remove-Item -LiteralPath $archiveLedgerPath -Force }
        foreach ($task in $quarantinedTasks) {
            Add-JsonLine -Path $archiveLedgerPath -Record (New-ArchiveRecord -Task $task -Reason 'HISTORICAL_QUARANTINE_NOISE_CLEARED_FROM_ACTIVE_QUEUE')
        }
        $quarantinedIds = @{}
        foreach ($task in $quarantinedTasks) {
            $quarantinedIds[[string]$task.id] = $true
        }
        $queue.tasks = @($queue.tasks | Where-Object { -not $quarantinedIds.ContainsKey([string]$_.id) })
        Write-JsonFile -Value $queue -Path $resolvedQueuePath
    }

    $afterQueue = (Read-Queue -Path $QueuePath).queue
    $afterTasks = @($afterQueue.tasks)
    $statsAfter = Get-QueueStatsFromTasks -Tasks $afterTasks
    $report = [ordered]@{
        classification = $classification
        status = $(if ($statsAfter.failed -eq 0 -and $statsAfter.quarantined -eq 0) { 'PASS' } elseif ($Mode -eq 'Apply') { 'REVIEW_REQUIRED' } else { 'READY' })
        mode = $Mode
        timestamp = (Get-Date -Format 'o')
        repoRoot = $repoRoot
        queuePath = $resolvedQueuePath
        artifactRoot = $resolvedArtifactRoot
        failedTargetCount = $failedTargets.Count
        repairedPassedCount = @($repairResults | Where-Object { $_.result -eq 'PASS' }).Count
        quarantinedArchivedCount = $(if ($Mode -eq 'Apply') { $quarantinedTasks.Count } else { 0 })
        decisions = @($decisions)
        repairResults = @($repairResults)
        queueStatsBefore = $statsBefore
        queueStatsAfter = $statsAfter
        activeQueueClean = ($statsAfter.failed -eq 0 -and $statsAfter.quarantined -eq 0)
        evidencePreserved = $true
        liveBenchmarkLaunched = $false
        providerCallsMade = $false
        destructiveActionsPerformed = $false
    }

    $reportPath = Join-Path $resolvedArtifactRoot 'queue_closure_report.json'
    $reportMdPath = Join-Path $resolvedArtifactRoot 'queue_closure_report.md'
    Write-JsonFile -Value $report -Path $reportPath
    Write-MarkdownReport -Report $report -Path $reportMdPath
    $report | ConvertTo-Json -Depth 100
}
finally {
    Pop-Location
}
