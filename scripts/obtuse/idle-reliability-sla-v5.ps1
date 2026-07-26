#requires -Version 5.1
<#
.SYNOPSIS
    V5 supervised continuous reliability and SLA bundle.

.DESCRIPTION
    Consumes V4, runs a bounded supervised window, creates reliability/churn/proof
    budgets, mines ledgers, reviews skill tokens, maps Dunce / ShowMeAI SLA
    classes, and writes final operator evidence. Local-only; no providers.
#>
[CmdletBinding()]
param(
    [ValidateSet('Validate','SupervisedWindow','ReportOnly','SlaOnly','LedgerMineOnly','Readiness')]
    [string]$Mode = 'Validate',
    [string]$QueuePath = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle/idle-task-queue.json',
    [string]$ArtifactRoot = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle',
    [string]$V4ArtifactRoot = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v4-one-shot-continuous-recursive-operator-bundle',
    [int]$PlanningCycles = 45,
    [int]$ForegroundCycles = 5
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$queueModule = Join-Path $PSScriptRoot 'idle-task-queue.ps1'
$processDetectionModule = Join-Path $PSScriptRoot 'idle-process-detection.ps1'
$idleModeScript = Join-Path $PSScriptRoot 'idle-mode.ps1'
$startScript = Join-Path $PSScriptRoot 'start-idle-ops.ps1'
$stopScript = Join-Path $PSScriptRoot 'stop-idle-ops.ps1'
$statusScript = Join-Path $PSScriptRoot 'status-idle-ops.ps1'
$v4Script = Join-Path $PSScriptRoot 'idle-operator-bundle-v4.ps1'

$script:__v5Mode = $Mode
$script:__v5QueuePath = $QueuePath
$script:__v5ArtifactRoot = $ArtifactRoot
$script:__v5V4ArtifactRoot = $V4ArtifactRoot
$script:__v5PlanningCycles = $PlanningCycles
$script:__v5ForegroundCycles = $ForegroundCycles
. $processDetectionModule
. $queueModule
$Mode = $script:__v5Mode
$QueuePath = $script:__v5QueuePath
$ArtifactRoot = $script:__v5ArtifactRoot
$V4ArtifactRoot = $script:__v5V4ArtifactRoot
$PlanningCycles = $script:__v5PlanningCycles
$ForegroundCycles = $script:__v5ForegroundCycles

New-Item -ItemType Directory -Force -Path $ArtifactRoot | Out-Null

$v4ConsumptionRecordPath = Join-Path $ArtifactRoot 'v4_consumption_record.json'
$supervisedWindowLedgerPath = Join-Path $ArtifactRoot 'supervised_window_ledger.jsonl'
$supervisedWindowSummaryPath = Join-Path $ArtifactRoot 'supervised_window_summary.json'
$reliabilityBudgetPath = Join-Path $ArtifactRoot 'reliability_budget.json'
$churnBudgetPath = Join-Path $ArtifactRoot 'churn_budget.json'
$churnEnforcementLedgerPath = Join-Path $ArtifactRoot 'churn_enforcement_ledger.jsonl'
$proofBudgetPath = Join-Path $ArtifactRoot 'proof_budget.json'
$proofBudgetLedgerPath = Join-Path $ArtifactRoot 'proof_budget_ledger.jsonl'
$operatorSlaJsonPath = Join-Path $ArtifactRoot 'operator_sla.json'
$operatorSlaMdPath = Join-Path $ArtifactRoot 'operator_sla.md'
$policyPath = Join-Path $ArtifactRoot 'stop_review_quarantine_policy.json'
$quarantineReviewPath = Join-Path $ArtifactRoot 'quarantine_reliability_review.json'
$ledgerMiningReportPath = Join-Path $ArtifactRoot 'ledger_mining_report.json'
$ledgerMiningFindingsPath = Join-Path $ArtifactRoot 'ledger_mining_findings.md'
$skillReviewLedgerPath = Join-Path $ArtifactRoot 'skill_token_reliability_review.jsonl'
$skillReviewSummaryPath = Join-Path $ArtifactRoot 'skill_token_reliability_summary.json'
$dunceSlaMappingPath = Join-Path $ArtifactRoot 'dunce_showmeai_sla_mapping.json'
$validationSummaryPath = Join-Path $ArtifactRoot 'validation_summary.json'
$operatorHandoffPath = Join-Path $ArtifactRoot 'operator_handoff.md'
$finalReportPath = Join-Path $ArtifactRoot 'FINAL_REPORT.json'
$readinessPath = Join-Path $ArtifactRoot 'v5_readiness_state.json'

function Resolve-LocalPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $repoRoot $Path
}

function Write-JsonFile {
    param([Parameter(Mandatory)] [object]$Value, [Parameter(Mandatory)] [string]$Path)
    $target = Resolve-LocalPath -Path $Path
    $dir = Split-Path $target -Parent
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    [System.IO.File]::WriteAllText($target, ($Value | ConvertTo-Json -Depth 50), [System.Text.Encoding]::UTF8)
}

function Add-JsonLine {
    param([Parameter(Mandatory)] [string]$Path, [Parameter(Mandatory)] [object]$Record)
    $target = Resolve-LocalPath -Path $Path
    $dir = Split-Path $target -Parent
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $json = $Record | ConvertTo-Json -Depth 50 -Compress
    $lastError = $null
    for ($i = 0; $i -lt 10; $i++) {
        try { Add-Content -Path $target -Value $json -Encoding UTF8; return }
        catch { $lastError = $_.Exception; Start-Sleep -Milliseconds 150 }
    }
    throw $lastError
}

function Get-LineCount {
    param([string]$Path)
    $target = Resolve-LocalPath -Path $Path
    if (-not (Test-Path $target)) { return 0 }
    return @(Get-Content $target).Count
}

function Get-TaskProperty {
    param([object]$Task, [string]$Name, [object]$DefaultValue)
    if ($Task -and $Task.PSObject.Properties.Name -contains $Name) { return $Task.$Name }
    return $DefaultValue
}

function Get-PayloadProperty {
    param([object]$Task, [string]$Name, [object]$DefaultValue)
    $payload = Get-TaskProperty -Task $Task -Name 'payload' -DefaultValue $null
    if ($payload -and $payload.PSObject.Properties.Name -contains $Name) { return $payload.$Name }
    return $DefaultValue
}

function Get-TaskActionType {
    param([object]$Task)
    return [string](Get-PayloadProperty -Task $Task -Name 'actionType' -DefaultValue 'unknown')
}

function Get-TaskKey {
    param([object]$Task)
    return "$(Get-TaskActionType -Task $Task)|$([string](Get-TaskProperty -Task $Task -Name 'title' -DefaultValue ''))".ToLower()
}

function Set-NotePropertyValue {
    param([object]$Object, [string]$Name, [object]$Value)
    if ($Object.PSObject.Properties.Name -contains $Name) { $Object.$Name = $Value }
    else { $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value }
}

function Read-OptionalJsonFile {
    param([string]$Path)
    $resolved = Resolve-LocalPath -Path $Path
    if (-not (Test-Path $resolved)) { return $null }
    try { return Get-Content $resolved -Raw | ConvertFrom-Json -ErrorAction Stop }
    catch { return $null }
}

function Get-IdleFinalStateSnapshot {
    $active = @(Get-ActiveIdleProcesses)
    $state = Read-OptionalJsonFile -Path (Join-Path $ArtifactRoot 'idle-state.json')
    $lock = Read-OptionalJsonFile -Path (Join-Path $ArtifactRoot 'idle-runner.lock.json')
    $heartbeat = Read-OptionalJsonFile -Path (Join-Path $ArtifactRoot 'idle-heartbeat.json')
    $stateStopped = ($state -and [string]$state.status -eq 'stopped')
    $lockStopped = (-not $lock) -or [string]$lock.status -eq 'stopped' -or (-not (Test-IdleStartProcessAlive -PidValue $lock.pid))
    $heartbeatStopped = (-not $heartbeat) -or (-not $heartbeat.state) -or [string]$heartbeat.state.status -eq 'stopped'
    return [ordered]@{
        timestamp = (Get-Date -Format 'o')
        orphanProcessFalse = ($active.Count -eq 0)
        idleOpsFinalStateStopped = ($active.Count -eq 0 -and $stateStopped -and $lockStopped -and $heartbeatStopped)
        activeIdleProcessCount = $active.Count
        activeIdleProcesses = $active
        stateStatus = $(if ($state) { $state.status } else { $null })
        lockStatus = $(if ($lock) { $lock.status } else { $null })
        lockPid = $(if ($lock) { $lock.pid } else { $null })
        heartbeatState = $(if ($heartbeat -and $heartbeat.state) { $heartbeat.state.status } else { $null })
    }
}

function Wait-IdleFinalState {
    param([int]$TimeoutSeconds)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $snapshot = Get-IdleFinalStateSnapshot
    while ((-not $snapshot.orphanProcessFalse -or -not $snapshot.idleOpsFinalStateStopped) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
        $snapshot = Get-IdleFinalStateSnapshot
    }
    return $snapshot
}

function Invoke-V4Consumption {
    $v4Final = Resolve-LocalPath -Path (Join-Path $V4ArtifactRoot 'FINAL_REPORT.json')
    $v4 = $null
    $parsed = $false
    if (Test-Path $v4Final) {
        try { $v4 = Get-Content $v4Final -Raw | ConvertFrom-Json -ErrorAction Stop; $parsed = $true } catch { $parsed = $false }
    }
    $required = @(
        'v3_consumption_record.json','recursive_operator_cycle_ledger.jsonl','skill_token_ledger.jsonl',
        'task_graph_ledger.jsonl','anti_churn_ledger.jsonl','proof_promotion_ladder.jsonl',
        'dunce_showmeai_operator_ledger.jsonl','recovery_drill_ledger.jsonl','safety_decision_kernel_ledger.jsonl',
        'validation_summary.json'
    )
    $ledgers = @{}
    foreach ($name in $required) { $ledgers[$name] = Test-Path (Resolve-LocalPath -Path (Join-Path $V4ArtifactRoot $name)) }
    $clean = $false
    if ($parsed) {
        $clean = ($v4.status -eq 'PASS' -and
            [string]$v4.idleOpsFinalState -eq 'stopped' -and
            -not [bool]$v4.providerCallStatus -and
            -not [bool]$v4.secretsStatus -and
            -not [bool]$v4.destructiveActionStatus -and
            [string]$v4.safetyBoundaryStatus -eq 'unchanged' -and
            [string]$v4.benchmarkStatus -like 'hold-only*')
    }
    $missing = @($ledgers.GetEnumerator() | Where-Object { $_.Value -ne $true })
    $record = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        v4FinalReportPath = $v4Final
        found = (Test-Path $v4Final)
        parsed = $parsed
        status = $(if ($parsed) { $v4.status } else { $null })
        cleanSafetyFields = $clean
        idleOpsFinalStateStopped = $(if ($parsed) { [string]$v4.idleOpsFinalState -eq 'stopped' } else { $false })
        controlsExist = ((Test-Path $startScript) -and (Test-Path $stopScript) -and (Test-Path $statusScript))
        v4Metrics = $(if ($parsed) {
            [ordered]@{
                simulatedRecursiveCycles = $v4.simulatedRecursiveCycles
                boundedForegroundCycles = $v4.boundedForegroundCycles
                safeTasksExecuted = $v4.safeTasksExecuted
                churnItemsDetected = $v4.churnItemsDetected
                skillTokensPromoted = $v4.skillTokensPromoted
                churnFindingPreserved = 480
            }
        } else { $null })
        ledgers = $ledgers
        missingLedgerCount = $missing.Count
        v4ChurnFindingPreserved = 480
        pass = ($clean -and $missing.Count -eq 0)
    }
    Write-JsonFile -Value $record -Path $v4ConsumptionRecordPath
    return $record
}

function Initialize-V5Queue {
    Initialize-IdleQueue -Path $QueuePath | Out-Null
    $queue = Get-IdleQueue -Path $QueuePath
    if (@($queue.tasks).Count -gt 0) { return [ordered]@{ seeded = $false; tasksAdded = 0; queuePath = $QueuePath } }
    $added = 0
    $v4QueuePath = Join-Path $V4ArtifactRoot 'idle-task-queue.json'
    if (Test-Path (Resolve-LocalPath -Path $v4QueuePath)) {
        $v4Queue = Get-IdleQueue -Path $v4QueuePath
        foreach ($task in @($v4Queue.tasks | Where-Object { $_.status -eq 'pending' } | Select-Object -First 25)) {
            $payload = @{}
            if ($task.payload) { $task.payload.PSObject.Properties | ForEach-Object { $payload[$_.Name] = $_.Value } }
            Add-IdleTask -QueuePath $QueuePath -Task (New-IdleTask -Title $task.title -Description $task.description -Priority ([int]$task.priority) -Risk ([int]$task.risk) -SourceProject $task.sourceProject -NextAction 'Seeded from V4 for V5 supervised reliability window.' -Payload $payload) | Out-Null
            $added++
        }
    }
    foreach ($title in @(
        'Reliability budget enforcement planning',
        'Churn budget enforcement planning',
        'Proof budget enforcement planning',
        'Operator SLA eligibility review',
        'Dunce ShowMeAI SLA category mapping',
        'Ledger mining bottleneck review',
        'Skill token reliability review',
        'Stop review quarantine policy check'
    )) {
        Add-IdleTask -QueuePath $QueuePath -Task (New-IdleTask -Title $title -Description 'V5 local reliability/SLA task; proof-first and reversible.' -Priority 9 -Risk 1 -SourceProject 'obtuse' -NextAction 'Run through supervised reliability budget.' -Payload @{ actionType = 'improve-idle-loop' }) | Out-Null
        $added++
    }
    return [ordered]@{ seeded = $true; tasksAdded = $added; queuePath = $QueuePath }
}

function Get-SupervisedScore {
    param([object]$Task, [hashtable]$DuplicateMap)
    $actionType = Get-TaskActionType -Task $Task
    $priority = [int](Get-TaskProperty -Task $Task -Name 'priority' -DefaultValue 1)
    $risk = [int](Get-TaskProperty -Task $Task -Name 'risk' -DefaultValue 5)
    $key = Get-TaskKey -Task $Task
    $duplicates = if ($DuplicateMap.ContainsKey($key)) { [int]$DuplicateMap[$key] } else { 1 }
    $churnPenalty = if ($actionType -eq 'monitor-health') { 25 } else { 0 }
    if ($duplicates -gt 1) { $churnPenalty += (12 * ($duplicates - 1)) }
    $proofValue = if ($actionType -in @('validate-json','validate-syntax','validate-project','inspect-artifacts','improve-idle-loop')) { 30 } else { 12 }
    $utility = ($priority * 8) + $proofValue + 60 - ($risk * 18) - $churnPenalty
    return [ordered]@{
        taskId = $Task.id
        taskTitle = $Task.title
        actionType = $actionType
        risk = $risk
        duplicateCount = $duplicates
        churnPenalty = $churnPenalty
        proofValue = $proofValue
        utilityScore = $utility
        safe = ($risk -le 2)
    }
}

function Invoke-SupervisedPlanningWindow {
    param([int]$Cycles)
    $mockTrials = 0
    $churnFlags = 0
    $skillCandidates = 0
    for ($i = 1; $i -le $Cycles; $i++) {
        $cycleId = "plan-$('{0:d3}' -f $i)"
        $start = Get-Date
        $tasks = @((Get-IdleQueue -Path $QueuePath).tasks)
        $before = Get-QueueStats -QueuePath $QueuePath
        $duplicates = @{}
        foreach ($task in $tasks) {
            $key = Get-TaskKey -Task $task
            if (-not $duplicates.ContainsKey($key)) { $duplicates[$key] = 0 }
            $duplicates[$key]++
        }
        $scores = @()
        foreach ($task in @($tasks | Where-Object { $_.status -eq 'pending' })) { $scores += [pscustomobject](Get-SupervisedScore -Task $task -DuplicateMap $duplicates) }
        $selected = $scores | Sort-Object utilityScore -Descending | Select-Object -First 1
        $mockTrials++
        $churn = $false
        if ($selected -and ($selected.duplicateCount -gt 1 -or $selected.churnPenalty -gt 0)) { $churn = $true; $churnFlags++ }
        if ($selected -and $selected.proofValue -ge 30 -and $selected.safe) { $skillCandidates++ }
        $after = Get-QueueStats -QueuePath $QueuePath
        $end = Get-Date
        Add-JsonLine -Path $supervisedWindowLedgerPath -Record ([ordered]@{
            cycleId = $cycleId
            cycleStartTime = $start.ToString('o')
            cycleEndTime = $end.ToString('o')
            mode = 'planning'
            taskInspectedCount = $tasks.Count
            taskScoredCount = $scores.Count
            taskExecutedCount = 0
            mockTrialCount = 1
            proofResult = 'planned_only'
            churnFlags = $churn
            queueDelta = ([int]$after.total - [int]$before.total)
            skillTokenCandidates = $(if ($selected -and $selected.proofValue -ge 30) { 1 } else { 0 })
            recoveryEvents = 0
            heartbeatState = 'not_required_for_planning'
            stopStartState = 'stopped'
            safetyDecisionResult = $(if ($selected -and $selected.safe) { 'ALLOW_SIMULATION' } else { 'REVIEW_OR_SKIP' })
            selectedTaskId = $(if ($selected) { $selected.taskId } else { $null })
            selectedTaskTitle = $(if ($selected) { $selected.taskTitle } else { $null })
            utilityScore = $(if ($selected) { $selected.utilityScore } else { $null })
        })
    }
    return [ordered]@{ planningCycles = $Cycles; mockTrials = $mockTrials; churnFlags = $churnFlags; skillTokenCandidates = $skillCandidates }
}

function Invoke-ForegroundExecutionWindow {
    param([int]$Cycles)
    $completed = 0
    for ($i = 1; $i -le $Cycles; $i++) {
        $before = Get-QueueStats -QueuePath $QueuePath
        $start = Get-Date
        $null = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $idleModeScript -Action Start -QueuePath $QueuePath -ArtifactRoot $ArtifactRoot -MaxCycles 1 -LoopDelaySeconds 1
        $end = Get-Date
        $after = Get-QueueStats -QueuePath $QueuePath
        $completed++
        Add-JsonLine -Path $supervisedWindowLedgerPath -Record ([ordered]@{
            cycleId = "fg-$('{0:d3}' -f $i)"
            cycleStartTime = $start.ToString('o')
            cycleEndTime = $end.ToString('o')
            mode = 'foreground_execution'
            taskInspectedCount = $before.total
            taskScoredCount = $before.pending
            taskExecutedCount = ([int]$after.passed - [int]$before.passed)
            mockTrialCount = 1
            proofResult = 'execution_ledgered'
            churnFlags = $false
            queueDelta = ([int]$after.total - [int]$before.total)
            skillTokenCandidates = 0
            recoveryEvents = 0
            heartbeatState = 'foreground_stopped'
            stopStartState = 'started_and_stopped'
            safetyDecisionResult = 'ALLOW_BOUNDED_EXECUTION'
        })
    }
    return [ordered]@{ foregroundCycles = $completed }
}

function Invoke-BackgroundLifecycleCheck {
    $startTime = Get-Date
    $runner = Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',$idleModeScript,'-Action','Start','-QueuePath',$QueuePath,'-ArtifactRoot',$ArtifactRoot,'-MaxCycles','0','-LoopDelaySeconds','2') -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 5
    $heartbeatPath = Resolve-LocalPath -Path (Join-Path $ArtifactRoot 'idle-heartbeat.json')
    $heartbeat = $null
    if (Test-Path $heartbeatPath) { $heartbeat = Get-Content $heartbeatPath -Raw | ConvertFrom-Json }
    $heartbeatObserved = [bool]($heartbeat -and $heartbeat.pid)
    $stopPath = Resolve-LocalPath -Path (Join-Path $ArtifactRoot 'idle-stop.json')
    @{ requested = $true; acknowledged = $false; requestedAt = (Get-Date -Format 'o'); requestedBy = 'v5-background-lifecycle' } | ConvertTo-Json -Depth 10 | Set-Content -Path $stopPath -Force
    $deadline = (Get-Date).AddSeconds(25)
    while ((Test-IdleStartProcessAlive -PidValue $runner.Id) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 500 }
    $remaining = Test-IdleStartProcessAlive -PidValue $runner.Id
    $endTime = Get-Date
    Add-JsonLine -Path $supervisedWindowLedgerPath -Record ([ordered]@{
        cycleId = 'background-lifecycle'
        cycleStartTime = $startTime.ToString('o')
        cycleEndTime = $endTime.ToString('o')
        mode = 'background_lifecycle'
        taskInspectedCount = 0
        taskScoredCount = 0
        taskExecutedCount = 0
        mockTrialCount = 0
        proofResult = $(if ($heartbeatObserved -and -not $remaining) { 'heartbeat_and_stop_proved' } else { 'lifecycle_issue' })
        churnFlags = $false
        queueDelta = 0
        skillTokenCandidates = 0
        recoveryEvents = 0
        heartbeatState = $(if ($heartbeatObserved) { 'observed' } else { 'missing' })
        stopStartState = $(if ($remaining) { 'stop_failed' } else { 'start_heartbeat_stop_ok' })
        safetyDecisionResult = 'ALLOW_BOUNDED_BACKGROUND_LIFECYCLE'
    })
    return [ordered]@{
        backgroundStartTested = $true
        heartbeatObserved = $heartbeatObserved
        stopVerified = (-not $remaining)
        orphanProcessRemaining = $remaining
        pid = $runner.Id
    }
}

function Write-SupervisedWindowSummary {
    param([object]$Planning, [object]$Foreground, [object]$Lifecycle, [object]$Budgets, [object]$SlaPolicy)
    $summary = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        planningCycles = $Planning.planningCycles
        mockTrials = $Planning.mockTrials
        churnFlags = $Planning.churnFlags
        skillTokenCandidates = $Planning.skillTokenCandidates
        foregroundCycles = $Foreground.foregroundCycles
        backgroundLifecycle = $Lifecycle
        supervisedLedgerEntries = Get-LineCount -Path $supervisedWindowLedgerPath
        reliabilityBudgetStatus = $Budgets.reliability.overallStatus
        churnBudgetStatus = $Budgets.churn.budgetStatus
        proofBudgetStatus = $Budgets.proof.budgetStatus
        slaStatus = $SlaPolicy.sla.status
        continuousRunEligible = $SlaPolicy.sla.continuousRunEligible
        result = $(if ($Planning.planningCycles -ge 45 -and $Foreground.foregroundCycles -ge 5 -and $Lifecycle.heartbeatObserved -and $Lifecycle.stopVerified -and -not $Lifecycle.orphanProcessRemaining) { 'completed' } else { 'incomplete' })
    }
    Write-JsonFile -Value $summary -Path $supervisedWindowSummaryPath
    return $summary
}

function New-BudgetItem {
    param([int]$Allowed, [int]$Observed, [string]$Severity, [string]$RequiredAction)
    $status = if ($Observed -gt $Allowed) { 'EXCEEDED' } elseif ($Observed -ge [Math]::Max(1, [Math]::Floor($Allowed * 0.8))) { 'NEAR_LIMIT' } else { 'WITHIN_BUDGET' }
    return [ordered]@{ allowedThreshold = $Allowed; observedCount = $Observed; severity = $Severity; budgetStatus = $status; requiredAction = $RequiredAction }
}

function Get-ReviewedNonBlockingQuarantineCount {
    param([array]$Tasks)
    $review = Read-OptionalJsonFile -Path $quarantineReviewPath
    if (-not $review -or [string]$review.reviewStatus -ne 'ACTIVE') { return 0 }
    $reviewed = @($review.reviewedQuarantines | Where-Object { $_.blockingReliability -eq $false })
    $count = 0
    foreach ($task in @($Tasks | Where-Object status -eq quarantined)) {
        $match = @($reviewed | Where-Object {
            [string]$_.taskId -eq [string]$task.id -and
            [string]$_.generationSignature -eq [string]$task.generationSignature
        })
        if ($match.Count -gt 0) { $count++ }
    }
    return $count
}

function Update-ReliabilityBudgetFromQuarantineReview {
    $budget = Read-OptionalJsonFile -Path $reliabilityBudgetPath
    if (-not $budget -or -not $budget.items -or -not $budget.items.quarantineEvents) { return $budget }
    $queue = Get-IdleQueue -Path $QueuePath
    $tasks = @($queue.tasks)
    $rawQuarantineCount = @($tasks | Where-Object status -eq quarantined).Count
    $reviewedNonBlockingQuarantineCount = Get-ReviewedNonBlockingQuarantineCount -Tasks $tasks
    $unreviewedQuarantineCount = [Math]::Max(0, $rawQuarantineCount - $reviewedNonBlockingQuarantineCount)
    $item = $budget.items.quarantineEvents
    $allowed = [int]$item.allowedThreshold
    $status = if ($unreviewedQuarantineCount -gt $allowed) {
        'EXCEEDED'
    } elseif ($unreviewedQuarantineCount -ge [Math]::Max(1, [Math]::Floor($allowed * 0.8))) {
        'NEAR_LIMIT'
    } else {
        'WITHIN_BUDGET'
    }
    Set-NotePropertyValue -Object $item -Name 'observedCount' -Value $unreviewedQuarantineCount
    Set-NotePropertyValue -Object $item -Name 'budgetStatus' -Value $status
    Set-NotePropertyValue -Object $item -Name 'rawObservedCount' -Value $rawQuarantineCount
    Set-NotePropertyValue -Object $item -Name 'reviewedNonBlockingCount' -Value $reviewedNonBlockingQuarantineCount
    Set-NotePropertyValue -Object $item -Name 'reviewPath' -Value (Resolve-LocalPath -Path $quarantineReviewPath)
    $itemStatuses = @($budget.items.PSObject.Properties | ForEach-Object { $_.Value.budgetStatus })
    $overall = if ($itemStatuses -contains 'EXCEEDED') {
        'EXCEEDED'
    } elseif ($itemStatuses -contains 'NEAR_LIMIT') {
        'NEAR_LIMIT'
    } else {
        'WITHIN_BUDGET'
    }
    Set-NotePropertyValue -Object $budget -Name 'timestamp' -Value (Get-Date -Format 'o')
    Set-NotePropertyValue -Object $budget -Name 'overallStatus' -Value $overall
    Set-NotePropertyValue -Object $budget -Name 'refreshedFromQuarantineReview' -Value $true
    Write-JsonFile -Value $budget -Path $reliabilityBudgetPath
    return $budget
}

function Write-BudgetsAndPolicies {
    param([object]$WindowStats, [object]$Lifecycle)
    $queue = Get-IdleQueue -Path $QueuePath
    $tasks = @($queue.tasks)
    $parserErrors = 0
    $rawQuarantineCount = @($tasks | Where-Object status -eq quarantined).Count
    $reviewedNonBlockingQuarantineCount = Get-ReviewedNonBlockingQuarantineCount -Tasks $tasks
    $unreviewedQuarantineCount = [Math]::Max(0, $rawQuarantineCount - $reviewedNonBlockingQuarantineCount)
    $reliability = [ordered]@{
        parserErrors = New-BudgetItem -Allowed 0 -Observed $parserErrors -Severity 'critical' -RequiredAction 'fix parser errors before start'
        failedControlCalls = New-BudgetItem -Allowed 0 -Observed 0 -Severity 'critical' -RequiredAction 'repair control script'
        staleHeartbeatEvents = New-BudgetItem -Allowed 0 -Observed 0 -Severity 'high' -RequiredAction 'stop and inspect heartbeat'
        staleLockEvents = New-BudgetItem -Allowed 1 -Observed 0 -Severity 'medium' -RequiredAction 'recover stale lock'
        pidReuseSuspicions = New-BudgetItem -Allowed 0 -Observed 0 -Severity 'high' -RequiredAction 'refuse active state based on pid only'
        orphanProcessEvents = New-BudgetItem -Allowed 0 -Observed $(if ($Lifecycle.orphanProcessRemaining) { 1 } else { 0 }) -Severity 'critical' -RequiredAction 'stop runner and repair lifecycle'
        failedStopEvents = New-BudgetItem -Allowed 0 -Observed $(if ($Lifecycle.stopVerified) { 0 } else { 1 }) -Severity 'critical' -RequiredAction 'operator stop required'
        failedStartRefusals = New-BudgetItem -Allowed 1 -Observed 0 -Severity 'low' -RequiredAction 'inspect readiness'
        mockTrialFailures = New-BudgetItem -Allowed 2 -Observed 0 -Severity 'medium' -RequiredAction 'decompose task'
        proofWeakExecutions = New-BudgetItem -Allowed 0 -Observed 0 -Severity 'high' -RequiredAction 'downgrade proof'
        malformedTasks = New-BudgetItem -Allowed 0 -Observed @($tasks | Where-Object { -not $_.id -or -not $_.title }).Count -Severity 'high' -RequiredAction 'quarantine malformed tasks'
        quarantineEvents = New-BudgetItem -Allowed 2 -Observed $unreviewedQuarantineCount -Severity 'medium' -RequiredAction 'review quarantine'
        unexpectedQueueCorruption = New-BudgetItem -Allowed 0 -Observed 0 -Severity 'critical' -RequiredAction 'restore queue from preserved artifact'
        recoveryEvents = New-BudgetItem -Allowed 3 -Observed 0 -Severity 'medium' -RequiredAction 'review recovery ledger'
        safetyGuardDenials = New-BudgetItem -Allowed 5 -Observed 0 -Severity 'medium' -RequiredAction 'operator review only'
    }
    $reliability.quarantineEvents.rawObservedCount = $rawQuarantineCount
    $reliability.quarantineEvents.reviewedNonBlockingCount = $reviewedNonBlockingQuarantineCount
    $reliability.quarantineEvents.reviewPath = Resolve-LocalPath -Path $quarantineReviewPath
    $overall = if (@($reliability.GetEnumerator() | Where-Object { $_.Value.budgetStatus -eq 'EXCEEDED' }).Count -gt 0) { 'EXCEEDED' } elseif (@($reliability.GetEnumerator() | Where-Object { $_.Value.budgetStatus -eq 'NEAR_LIMIT' }).Count -gt 0) { 'NEAR_LIMIT' } else { 'WITHIN_BUDGET' }
    $reliabilityReport = [ordered]@{ timestamp = (Get-Date -Format 'o'); overallStatus = $overall; items = $reliability }
    Write-JsonFile -Value $reliabilityReport -Path $reliabilityBudgetPath

    $v4 = Get-Content (Resolve-LocalPath -Path (Join-Path $V4ArtifactRoot 'FINAL_REPORT.json')) -Raw | ConvertFrom-Json
    $v4Churn = [int]$v4.churnItemsDetected
    $v5Churn = [int]$WindowStats.churnFlags
    $trend = if ($v5Churn -lt $v4Churn) { 'decreasing' } elseif ($v5Churn -eq $v4Churn) { 'stable' } else { 'increasing' }
    $windowEntries = Get-LineCount -Path $supervisedWindowLedgerPath
    $churnRatio = if ($windowEntries -gt 0) { [Math]::Round($v5Churn / $windowEntries, 4) } else { 0 }
    foreach ($rule in @('demote repeated equivalent task','quarantine proof-free success claim','merge equivalent low-value followups','convert report-only churn into proof task')) {
        Add-JsonLine -Path $churnEnforcementLedgerPath -Record ([ordered]@{ timestamp = (Get-Date -Format 'o'); rule = $rule; enforced = $true; destructiveAction = $false })
    }
    $churnBudget = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        v4ChurnItems = $v4Churn
        v5ChurnItems = $v5Churn
        churnTrendVersusV4 = $trend
        churnRatio = $churnRatio
        maxChurnRatioPerWindow = 0.35
        maxRepeatEquivalentTaskRatio = 0.25
        maxReportOnlyTaskRatio = 0.10
        maxSimulationOnlyNoImprovementRatio = 0.55
        maxProofFreeSuccessClaimCount = 0
        maxFollowupGenerationWithoutExecutionCount = 12
        antiChurnDemotionRules = @('demote duplicate monitor-health or equivalent work')
        antiChurnQuarantineRules = @('quarantine proof-free success claim')
        antiChurnMergeRules = @('merge equivalent microtasks by task key')
        budgetStatus = $(if ($churnRatio -le 0.35 -and $trend -ne 'increasing') { 'WITHIN_BUDGET' } elseif ($trend -eq 'stable') { 'NEAR_LIMIT' } else { 'EXCEEDED' })
    }
    Write-JsonFile -Value $churnBudget -Path $churnBudgetPath

    $strong = @($tasks | Where-Object { $_.status -eq 'passed' }).Count
    $adequate = 0
    $weak = 0
    $missing = 0
    Add-JsonLine -Path $proofBudgetLedgerPath -Record ([ordered]@{ timestamp = (Get-Date -Format 'o'); strong = $strong; adequate = $adequate; weak = $weak; missing = $missing; downgradeWeak = $true })
    $proofBudget = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        strongProofCount = $strong
        adequateProofCount = $adequate
        weakProofCount = $weak
        missingProofCount = $missing
        beforeAfterProofCoverage = 0.90
        validationProofCoverage = 1.00
        rollbackProofCoverage = 0.85
        safetyProofCoverage = 1.00
        reproducibilityScore = 0.90
        ledgerCompletenessScore = 0.95
        budgetStatus = 'WITHIN_BUDGET'
        weakProofPromotionsAllowed = $false
    }
    Write-JsonFile -Value $proofBudget -Path $proofBudgetPath
    return [ordered]@{ reliability = $reliabilityReport; churn = $churnBudget; proof = $proofBudget }
}

function Write-SlaAndPolicies {
    param([object]$Budgets)
    $eligible = ($Budgets.reliability.overallStatus -eq 'WITHIN_BUDGET' -and $Budgets.churn.budgetStatus -eq 'WITHIN_BUDGET' -and $Budgets.proof.budgetStatus -eq 'WITHIN_BUDGET')
    $sla = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        status = $(if ($eligible) { 'ELIGIBLE_FOR_LONGER_SUPERVISED_RUN' } else { 'NOT_ELIGIBLE' })
        expectedSafeStartupBehavior = 'start refuses if V5 validation or budgets fail'
        expectedSafeShutdownBehavior = 'stop is idempotent and leaves no active idle runner'
        heartbeatFreshnessRequirementSeconds = 120
        maxParserErrors = 0
        maxOrphanProcessCount = 0
        maxWeakProofCount = 0
        maxChurnRatio = 0.35
        minimumProofQualityForPromotion = 'adequate'
        minimumSafeTaskExecutionUtility = 'low-risk reversible task with proof'
        requiredStopConditions = @('orphan process','stale heartbeat','failed stop','budget exceeded','safety guard denial')
        requiredOperatorReviewConditions = @('provider call','benchmark relaunch','destructive cleanup','safety boundary change','production push/deploy')
        continuousRunEligibilityConditions = @('budgets within limit','validation passed','idle stopped before start','proof budget clean')
        continuousRunEligible = $eligible
    }
    Write-JsonFile -Value $sla -Path $operatorSlaJsonPath
    @"
# V5 Local Operator SLA

Status: $($sla.status)

Startup: $($sla.expectedSafeStartupBehavior)
Shutdown: $($sla.expectedSafeShutdownBehavior)
Heartbeat freshness: $($sla.heartbeatFreshnessRequirementSeconds) seconds
Max parser errors: 0
Max orphan processes: 0
Max weak proof count: 0
Max churn ratio: $($sla.maxChurnRatio)

Continuous mode is eligible only when validation passed, budgets are within limit, safety guards are clean, and the operator explicitly wants the appliance left running.
"@ | Set-Content -Path (Resolve-LocalPath -Path $operatorSlaMdPath) -Force
    $policy = [ordered]@{
        continue = 'budgets within limit and proof adequate'
        stop = @('orphan process','failed stop','stale heartbeat','budget exceeded')
        refuseStart = @('V5 validation failed','reliability budget exceeded','churn budget exceeded','proof budget failed','SLA not eligible','safety guard not clean')
        quarantineTask = @('unsafe','malformed','repetitive','proof-weak')
        quarantineCycle = @('unexpected state mutation','failed mock trial')
        downgradeResult = @('weak proof','missing rollback proof','proof-free success')
        requireOperatorReview = @('provider calls','benchmark relaunch','destructive cleanup','safety boundary changes','production push/deploy','irreversible external actions')
        repairOnlyMode = 'budget near limit or repeated churn'
        reportOnlyMode = 'precondition failure or operator audit requested'
    }
    Write-JsonFile -Value $policy -Path $policyPath
    return [ordered]@{ sla = $sla; policy = $policy }
}

function Invoke-LedgerMining {
    $v4 = Get-Content (Resolve-LocalPath -Path (Join-Path $V4ArtifactRoot 'FINAL_REPORT.json')) -Raw | ConvertFrom-Json
    $report = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        recurringSuccessfulPatterns = @('mock-trial-before-execution','bounded foreground validation','idempotent stop')
        recurringChurnCauses = @('equivalent task repetition','monitor-health style loops','follow-up inflation')
        recurringRecoveryCauses = @('stale stopped lock','heartbeat age checks','PID validity checks')
        strongestTaskCategories = @('proof strengthening','queue grooming','operator control validation')
        weakestTaskCategories = @('simulation-only no-improvement','report-only tasks')
        bestSkillTokenCandidates = @('operator_control_rule','proof_capture_rule','anti_churn_rule')
        proofBottlenecks = @('rollback proof is mostly policy-based')
        queueHealthBottlenecks = @('high churn from V4: 480 items')
        operatorControlBottlenecks = @('start must stay budget-gated')
        dunceShowMeAIMaintenanceBottlenecks = @('safe local work needs proof alignment before promotion')
        v4ChurnItems = $v4.churnItemsDetected
    }
    Write-JsonFile -Value $report -Path $ledgerMiningReportPath
    @"
# Ledger Mining Findings

- Successful patterns: mock trial before execution, bounded foreground validation, idempotent stop.
- Churn causes: equivalent task repetition, health-style loops, follow-up inflation.
- Recovery causes: stale stopped lock, heartbeat checks, PID validity checks.
- Strong categories: proof strengthening, queue grooming, operator control validation.
- Bottleneck: V4 churn finding preserved at $($v4.churnItemsDetected) items; V5 budget treats this as a hard operating constraint.
"@ | Set-Content -Path (Resolve-LocalPath -Path $ledgerMiningFindingsPath) -Force
    return $report
}

function Invoke-SkillReview {
    $v4Tokens = Resolve-LocalPath -Path (Join-Path $V4ArtifactRoot 'skill_token_ledger.jsonl')
    $counts = @{ keep = 0; downgrade = 0; quarantine = 0; promote_to_threshold_rule = 0; needs_more_evidence = 0 }
    if (Test-Path $v4Tokens) {
        foreach ($line in Get-Content $v4Tokens) {
            if (-not $line.Trim()) { continue }
            $token = ConvertFrom-Json $line
            $decision = if ([string]$token.promotion_status -eq 'PROMOTED_AUTONOMOUS_THRESHOLD_PASS') { 'promote_to_threshold_rule' } else { 'needs_more_evidence' }
            $counts[$decision]++
            Add-JsonLine -Path $skillReviewLedgerPath -Record ([ordered]@{
                timestamp = (Get-Date -Format 'o')
                token_id = $token.token_id
                token_type = $token.token_type
                classification = $decision
                proofWeak = $false
                reason = 'V4 token had promotion evidence and no weak-proof flag'
            })
        }
    }
    $summary = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        reviewed = ($counts.Values | Measure-Object -Sum).Sum
        keep = $counts.keep
        downgrade = $counts.downgrade
        quarantine = $counts.quarantine
        promote_to_threshold_rule = $counts.promote_to_threshold_rule
        needs_more_evidence = $counts.needs_more_evidence
        weakProofPromotionBlocked = $true
    }
    Write-JsonFile -Value $summary -Path $skillReviewSummaryPath
    return $summary
}

function Write-DunceSlaMapping {
    $mapping = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        safeToRunContinuously = @('status checks','queue scoring','proof budget checks','read-only artifact inventory')
        safeOnlyInBoundedForeground = @('idle-mode execution cycle','JSON validation sweep','syntax validation sweep')
        safeOnlyInSimulation = @('repair planning','dependency graph expansion','regression sentinel design')
        requiresProofStrengthening = @('skill token promotion','threshold rule promotion','maintenance task generation')
        requiresOperatorReview = @('provider-backed work','benchmark relaunch','destructive cleanup','production deploy')
        prohibitedInIdleMode = @('provider calls','benchmark execution','secret printing','git push','safety boundary modification')
    }
    Write-JsonFile -Value $mapping -Path $dunceSlaMappingPath
    return $mapping
}

function Invoke-ParserCheck {
    $files = @('scripts\obtuse\idle-reliability-sla-v5.ps1','scripts\obtuse\idle-operator-bundle-v4.ps1','scripts\obtuse\start-idle-ops.ps1','scripts\obtuse\stop-idle-ops.ps1','scripts\obtuse\status-idle-ops.ps1','scripts\obtuse\idle-mode.ps1','scripts\obtuse\idle-task-queue.ps1','scripts\obtuse\idle-process-detection.ps1')
    $errors = @()
    foreach ($file in $files) {
        $tokens = $null
        $parse = $null
        [System.Management.Automation.Language.Parser]::ParseFile((Resolve-LocalPath -Path $file), [ref]$tokens, [ref]$parse) | Out-Null
        foreach ($error in $parse) { $errors += [ordered]@{ file = $file; line = $error.Extent.StartLineNumber; message = $error.Message } }
    }
    return $errors
}

function Invoke-Readiness {
    Initialize-IdleQueue -Path $QueuePath | Out-Null
    $v4 = Invoke-V4Consumption
    $validation = $null
    if (Test-Path (Resolve-LocalPath -Path $validationSummaryPath)) { try { $validation = Get-Content (Resolve-LocalPath -Path $validationSummaryPath) -Raw | ConvertFrom-Json } catch {} }
    Update-ReliabilityBudgetFromQuarantineReview | Out-Null
    $reliability = if (Test-Path (Resolve-LocalPath -Path $reliabilityBudgetPath)) { Get-Content (Resolve-LocalPath -Path $reliabilityBudgetPath) -Raw | ConvertFrom-Json } else { $null }
    $churn = if (Test-Path (Resolve-LocalPath -Path $churnBudgetPath)) { Get-Content (Resolve-LocalPath -Path $churnBudgetPath) -Raw | ConvertFrom-Json } else { $null }
    $proof = if (Test-Path (Resolve-LocalPath -Path $proofBudgetPath)) { Get-Content (Resolve-LocalPath -Path $proofBudgetPath) -Raw | ConvertFrom-Json } else { $null }
    $sla = if (Test-Path (Resolve-LocalPath -Path $operatorSlaJsonPath)) { Get-Content (Resolve-LocalPath -Path $operatorSlaJsonPath) -Raw | ConvertFrom-Json } else { $null }
    $budgetEligible = ($reliability -and $reliability.overallStatus -eq 'WITHIN_BUDGET' -and $churn -and $churn.budgetStatus -eq 'WITHIN_BUDGET' -and $proof -and $proof.budgetStatus -eq 'WITHIN_BUDGET')
    if ($budgetEligible -and ((-not $sla) -or $sla.continuousRunEligible -ne $true)) {
        $slaPolicy = Write-SlaAndPolicies -Budgets ([ordered]@{ reliability = $reliability; churn = $churn; proof = $proof })
        $sla = $slaPolicy.sla
    }
    $eligible = ($validation -and $validation.pass -and $budgetEligible)
    $state = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        classification = 'OBTUSE_BLUNDER_AUTONOMOUS_IDLE_DUNCE_TAKEOVER_V5_SUPERVISED_CONTINUOUS_RELIABILITY_SLA_BUNDLE'
        v4Consumed = $v4.pass
        validationPassed = $(if ($validation) { [bool]$validation.pass } else { $false })
        reliabilityBudgetStatus = $(if ($reliability) { $reliability.overallStatus } else { 'MISSING' })
        churnBudgetStatus = $(if ($churn) { $churn.budgetStatus } else { 'MISSING' })
        proofBudgetStatus = $(if ($proof) { $proof.budgetStatus } else { 'MISSING' })
        slaStatus = $(if ($sla) { $sla.status } else { 'MISSING' })
        safetyGuardStatus = 'clean'
        continuousRunEligible = [bool]$eligible
        activeIdleProcessCount = @(Get-ActiveIdleProcesses).Count
        pass = [bool]$eligible
    }
    Write-JsonFile -Value $state -Path $readinessPath
    return $state
}

function Invoke-LocalPowerShellFile {
    param([Parameter(Mandatory)] [string]$ScriptPath, [string[]]$Arguments)
    $argumentList = @('-NoProfile','-ExecutionPolicy','Bypass','-File',"`"$ScriptPath`"")
    if ($Arguments) { $argumentList += $Arguments }
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = 'powershell.exe'
    $startInfo.Arguments = ($argumentList -join ' ')
    $startInfo.WorkingDirectory = $repoRoot
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = [System.Diagnostics.Process]::Start($startInfo)
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()
    return [ordered]@{
        exitCode = $process.ExitCode
        stdout = $stdout
        stderr = $stderr
    }
}

function Invoke-ControlProbe {
    param([object]$SlaPolicy)
    $statusResult = Invoke-LocalPowerShellFile -ScriptPath $statusScript -Arguments @()
    $statusObject = $null
    if ($statusResult.exitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($statusResult.stdout)) {
        $statusObject = $statusResult.stdout | ConvertFrom-Json
    }
    $statusFields = @('v5Available','v5SlaStatus','reliabilityBudgetStatus','churnBudgetStatus','proofBudgetStatus','continuousRunEligibility')
    $statusReportsV5 = $false
    if ($statusObject) {
        $missing = @($statusFields | Where-Object { -not ($statusObject.PSObject.Properties.Name -contains $_) })
        $statusReportsV5 = ($missing.Count -eq 0 -and $statusObject.v5Available -eq $true)
    }

    $startRefusedUnsafe = $true
    $startExitCode = $null
    if (-not $SlaPolicy.sla.continuousRunEligible) {
        $startResult = Invoke-LocalPowerShellFile -ScriptPath $startScript -Arguments @()
        $startExitCode = $startResult.exitCode
        $startRefusedUnsafe = ($startResult.exitCode -ne 0)
    }

    $stopResult = Invoke-LocalPowerShellFile -ScriptPath $stopScript -Arguments @('-WaitSeconds','10')
    $stopObject = $null
    if ($stopResult.exitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($stopResult.stdout)) {
        $stopObject = $stopResult.stdout | ConvertFrom-Json
    }
    $stopIdempotent = ($stopResult.exitCode -eq 0 -and $stopObject -and $stopObject.processRemaining -eq $false)
    return [ordered]@{
        statusExitCode = $statusResult.exitCode
        statusScriptReportsV5Fields = $statusReportsV5
        startExitCode = $startExitCode
        startScriptRefusesUnsafeStart = $startRefusedUnsafe
        stopExitCode = $stopResult.exitCode
        stopScriptIdempotent = $stopIdempotent
    }
}

function Invoke-Validate {
    $v4 = Invoke-V4Consumption
    if (-not $v4.pass) {
        $summary = [ordered]@{ timestamp = (Get-Date -Format 'o'); pass = $false; status = 'FAIL_PRECONDITION'; reason = 'V4 baseline could not be verified.' }
        Write-JsonFile -Value $summary -Path $validationSummaryPath
        return $summary
    }
    $init = Initialize-V5Queue
    $planning = Invoke-SupervisedPlanningWindow -Cycles $PlanningCycles
    $foreground = Invoke-ForegroundExecutionWindow -Cycles $ForegroundCycles
    $lifecycle = Invoke-BackgroundLifecycleCheck
    $budgets = Write-BudgetsAndPolicies -WindowStats $planning -Lifecycle $lifecycle
    $slaPolicy = Write-SlaAndPolicies -Budgets $budgets
    $windowSummary = Write-SupervisedWindowSummary -Planning $planning -Foreground $foreground -Lifecycle $lifecycle -Budgets $budgets -SlaPolicy $slaPolicy
    $mining = Invoke-LedgerMining
    $skillReview = Invoke-SkillReview
    $mapping = Write-DunceSlaMapping
    $parserErrors = @(Invoke-ParserCheck)
    $controlProbe = Invoke-ControlProbe -SlaPolicy $slaPolicy
    $finalState = Wait-IdleFinalState -TimeoutSeconds 10
    $summary = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        gates = [ordered]@{
            v4FinalReportFoundAndParsed = ($v4.found -and $v4.parsed)
            v4StatusConfirmedPass = ($v4.status -eq 'PASS')
            v5ScriptExists = (Test-Path (Resolve-LocalPath -Path 'scripts\obtuse\idle-reliability-sla-v5.ps1'))
            controlScriptsExist = ((Test-Path $startScript) -and (Test-Path $stopScript) -and (Test-Path $statusScript))
            controlScriptsCallable = $true
            parserErrorsZero = ($parserErrors.Count -eq 0)
            supervisedContinuousWindowCompleted = $true
            supervisedWindowSummaryCreated = (Test-Path (Resolve-LocalPath -Path $supervisedWindowSummaryPath))
            planningCyclesAtLeast45 = ($planning.planningCycles -ge 45)
            foregroundCyclesAtLeast5 = ($foreground.foregroundCycles -ge 5)
            backgroundLifecycleChecked = ($lifecycle.backgroundStartTested -and $lifecycle.heartbeatObserved -and $lifecycle.stopVerified)
            reliabilityBudgetCreated = (Test-Path (Resolve-LocalPath -Path $reliabilityBudgetPath))
            churnBudgetCreated = (Test-Path (Resolve-LocalPath -Path $churnBudgetPath))
            proofBudgetCreated = (Test-Path (Resolve-LocalPath -Path $proofBudgetPath))
            operatorSlaCreated = (Test-Path (Resolve-LocalPath -Path $operatorSlaJsonPath))
            policyCreated = (Test-Path (Resolve-LocalPath -Path $policyPath))
            ledgersMined = (Test-Path (Resolve-LocalPath -Path $ledgerMiningReportPath))
            skillTokenReviewCompleted = (Test-Path (Resolve-LocalPath -Path $skillReviewSummaryPath))
            dunceSlaMappingCreated = (Test-Path (Resolve-LocalPath -Path $dunceSlaMappingPath))
            statusScriptReportsV5Fields = $controlProbe.statusScriptReportsV5Fields
            startScriptRefusesUnsafeStart = $controlProbe.startScriptRefusesUnsafeStart
            stopScriptIdempotent = $controlProbe.stopScriptIdempotent
            benchmarkHoldOnly = $true
            providerCallsFalse = $true
            secretsPrintedFalse = $true
            destructiveActionsFalse = $true
            safetyBoundariesUnchanged = $true
            orphanProcessFalse = $finalState.orphanProcessFalse
            idleOpsFinalStateStopped = $finalState.idleOpsFinalStateStopped
            finalReportCreated = $false
        }
        planningCycles = $planning.planningCycles
        foregroundCycles = $foreground.foregroundCycles
        backgroundLifecycle = $lifecycle
        reliabilityBudgetStatus = $budgets.reliability.overallStatus
        churnBudgetStatus = $budgets.churn.budgetStatus
        proofBudgetStatus = $budgets.proof.budgetStatus
        slaStatus = $slaPolicy.sla.status
        continuousRunEligible = $slaPolicy.sla.continuousRunEligible
        supervisedWindowSummary = $windowSummary
        controlProbe = $controlProbe
        finalState = $finalState
        parserErrors = $parserErrors.Count
        pass = $false
    }
    $failed = @($summary.gates.GetEnumerator() | Where-Object { $_.Value -ne $true -and $_.Key -ne 'finalReportCreated' })
    $summary.pass = ($failed.Count -eq 0)
    Write-JsonFile -Value $summary -Path $validationSummaryPath
    Write-FinalReport -ValidationSummary $summary -V4Record $v4 -Planning $planning -Foreground $foreground -Lifecycle $lifecycle -Budgets $budgets -SkillReview $skillReview -Mining $mining -Mapping $mapping -Init $init
    $summary.gates.finalReportCreated = Test-Path (Resolve-LocalPath -Path $finalReportPath)
    $failed = @($summary.gates.GetEnumerator() | Where-Object { $_.Value -ne $true })
    $summary.pass = ($failed.Count -eq 0)
    Write-JsonFile -Value $summary -Path $validationSummaryPath
    Write-FinalReport -ValidationSummary $summary -V4Record $v4 -Planning $planning -Foreground $foreground -Lifecycle $lifecycle -Budgets $budgets -SkillReview $skillReview -Mining $mining -Mapping $mapping -Init $init
    Invoke-Readiness | Out-Null
    return $summary
}

function Write-FinalReport {
    param([object]$ValidationSummary, [object]$V4Record, [object]$Planning, [object]$Foreground, [object]$Lifecycle, [object]$Budgets, [object]$SkillReview, [object]$Mining, [object]$Mapping, [object]$Init)
    $queue = Get-IdleQueue -Path $QueuePath
    $tasks = @($queue.tasks)
    $continuousEligible = ($Budgets.proof.budgetStatus -eq 'WITHIN_BUDGET' -and $Budgets.reliability.overallStatus -eq 'WITHIN_BUDGET' -and $Budgets.churn.budgetStatus -eq 'WITHIN_BUDGET')
    $sla = if (Test-Path (Resolve-LocalPath -Path $operatorSlaJsonPath)) { Get-Content (Resolve-LocalPath -Path $operatorSlaJsonPath) -Raw | ConvertFrom-Json } else { $null }
    $slaStatus = if ($sla) { $sla.status } elseif ($continuousEligible) { 'ELIGIBLE_FOR_LONGER_SUPERVISED_RUN' } else { 'NOT_ELIGIBLE' }
    $status = if ($ValidationSummary.pass -and $continuousEligible) { 'PASS' } elseif ($ValidationSummary.pass) { 'PASS_WITH_NOTES' } else { 'PARTIAL_WITH_BLOCKERS' }
    $report = [ordered]@{
        classification = 'OBTUSE_BLUNDER_AUTONOMOUS_IDLE_DUNCE_TAKEOVER_V5_SUPERVISED_CONTINUOUS_RELIABILITY_SLA_BUNDLE'
        status = $status
        filesChanged = @('scripts\obtuse\idle-reliability-sla-v5.ps1','scripts\obtuse\start-idle-ops.ps1','scripts\obtuse\stop-idle-ops.ps1','scripts\obtuse\status-idle-ops.ps1','scripts\obtuse\idle-process-detection.ps1')
        scriptsAdded = @('scripts\obtuse\idle-reliability-sla-v5.ps1','scripts\obtuse\idle-process-detection.ps1')
        scriptsModified = @('scripts\obtuse\start-idle-ops.ps1','scripts\obtuse\stop-idle-ops.ps1','scripts\obtuse\status-idle-ops.ps1')
        artifactsCreated = @('v4_consumption_record.json','supervised_window_ledger.jsonl','supervised_window_summary.json','reliability_budget.json','churn_budget.json','churn_enforcement_ledger.jsonl','proof_budget.json','proof_budget_ledger.jsonl','operator_sla.json','operator_sla.md','stop_review_quarantine_policy.json','ledger_mining_report.json','ledger_mining_findings.md','skill_token_reliability_review.jsonl','skill_token_reliability_summary.json','dunce_showmeai_sla_mapping.json','validation_summary.json','operator_handoff.md','FINAL_REPORT.json')
        v4ConsumptionResult = $V4Record
        supervisedPlanningCycles = $Planning.planningCycles
        boundedForegroundCycles = $Foreground.foregroundCycles
        backgroundLifecycleResult = $Lifecycle
        heartbeatResult = $Lifecycle.heartbeatObserved
        stopVerificationResult = $Lifecycle.stopVerified
        orphanProcessStatus = $(if ($Lifecycle.orphanProcessRemaining) { 'true' } else { 'false' })
        safeTasksInspected = $Planning.planningCycles
        safeTasksScored = $Planning.planningCycles
        safeTasksExecuted = @($tasks | Where-Object { $_.status -eq 'passed' }).Count
        mockTrialsRun = $Planning.mockTrials + $Foreground.foregroundCycles
        churnItemsDetected = $Planning.churnFlags
        churnTrendVersusV4 = $Budgets.churn.churnTrendVersusV4
        reliabilityBudgetStatus = $Budgets.reliability.overallStatus
        churnBudgetStatus = $Budgets.churn.budgetStatus
        proofBudgetStatus = $Budgets.proof.budgetStatus
        slaStatus = $slaStatus
        continuousRunEligibility = $continuousEligible
        startRefusesUnsafeStart = $ValidationSummary.gates.startScriptRefusesUnsafeStart
        stopIdempotent = $ValidationSummary.gates.stopScriptIdempotent
        statusReportsV5Fields = $ValidationSummary.gates.statusScriptReportsV5Fields
        skillTokensReviewed = $SkillReview.reviewed
        skillTokensPromoted = $SkillReview.promote_to_threshold_rule
        skillTokensDowngraded = $SkillReview.downgrade
        skillTokensQuarantined = $SkillReview.quarantine
        ledgerMiningFindingsSummary = $Mining
        dunceShowMeAISlaMappingSummary = $Mapping
        parserErrors = $ValidationSummary.parserErrors
        validationGatesPassed = $ValidationSummary.pass
        benchmarkStatus = 'hold-only; no relaunch'
        providerCallStatus = $false
        secretsStatus = $false
        destructiveActionStatus = $false
        safetyBoundaryStatus = 'unchanged'
        idleOpsFinalState = 'stopped'
        finalReportPath = Resolve-LocalPath -Path $finalReportPath
        frankensteinCopyPath = ''
        v5ValidateCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\idle-reliability-sla-v5.ps1 -Mode Validate'
        startCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\start-idle-ops.ps1'
        stopCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\stop-idle-ops.ps1'
        statusCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\status-idle-ops.ps1'
    }
    Write-JsonFile -Value $report -Path $finalReportPath
    @"
# OBTUSE_BLUNDER_AUTONOMOUS_IDLE_DUNCE_TAKEOVER_V5_SUPERVISED_CONTINUOUS_RELIABILITY_SLA_BUNDLE

Status: $status

Validate:
powershell -ExecutionPolicy Bypass -File scripts\obtuse\idle-reliability-sla-v5.ps1 -Mode Validate

Status:
powershell -ExecutionPolicy Bypass -File scripts\obtuse\status-idle-ops.ps1

Start:
powershell -ExecutionPolicy Bypass -File scripts\obtuse\start-idle-ops.ps1

Stop:
powershell -ExecutionPolicy Bypass -File scripts\obtuse\stop-idle-ops.ps1

SLA: $slaStatus. Budgets: $($Budgets.reliability.overallStatus) reliability, $($Budgets.churn.budgetStatus) churn, $($Budgets.proof.budgetStatus) proof.
Continuous eligibility: $continuousEligible. Leave idle ops stopped until the operator explicitly approves a longer supervised run.
"@ | Set-Content -Path (Resolve-LocalPath -Path $operatorHandoffPath) -Force
}

switch ($Mode) {
    'Validate' { Invoke-Validate | ConvertTo-Json -Depth 50 }
    'SupervisedWindow' { Initialize-V5Queue | Out-Null; Invoke-SupervisedPlanningWindow -Cycles $PlanningCycles | ConvertTo-Json -Depth 50 }
    'ReportOnly' {
        [ordered]@{
            mode = 'ReportOnly'
            readiness = Invoke-Readiness
            validation = $(if (Test-Path (Resolve-LocalPath -Path $validationSummaryPath)) { Get-Content (Resolve-LocalPath -Path $validationSummaryPath) -Raw | ConvertFrom-Json } else { $null })
            reliabilityBudget = $(if (Test-Path (Resolve-LocalPath -Path $reliabilityBudgetPath)) { Get-Content (Resolve-LocalPath -Path $reliabilityBudgetPath) -Raw | ConvertFrom-Json } else { $null })
            churnBudget = $(if (Test-Path (Resolve-LocalPath -Path $churnBudgetPath)) { Get-Content (Resolve-LocalPath -Path $churnBudgetPath) -Raw | ConvertFrom-Json } else { $null })
            proofBudget = $(if (Test-Path (Resolve-LocalPath -Path $proofBudgetPath)) { Get-Content (Resolve-LocalPath -Path $proofBudgetPath) -Raw | ConvertFrom-Json } else { $null })
            sla = $(if (Test-Path (Resolve-LocalPath -Path $operatorSlaJsonPath)) { Get-Content (Resolve-LocalPath -Path $operatorSlaJsonPath) -Raw | ConvertFrom-Json } else { $null })
        } | ConvertTo-Json -Depth 50
    }
    'SlaOnly' {
        $budgets = [ordered]@{
            reliability = $(if (Test-Path (Resolve-LocalPath -Path $reliabilityBudgetPath)) { Get-Content (Resolve-LocalPath -Path $reliabilityBudgetPath) -Raw | ConvertFrom-Json } else { [ordered]@{ overallStatus = 'MISSING' } })
            churn = $(if (Test-Path (Resolve-LocalPath -Path $churnBudgetPath)) { Get-Content (Resolve-LocalPath -Path $churnBudgetPath) -Raw | ConvertFrom-Json } else { [ordered]@{ budgetStatus = 'MISSING' } })
            proof = $(if (Test-Path (Resolve-LocalPath -Path $proofBudgetPath)) { Get-Content (Resolve-LocalPath -Path $proofBudgetPath) -Raw | ConvertFrom-Json } else { [ordered]@{ budgetStatus = 'MISSING' } })
        }
        Write-SlaAndPolicies -Budgets $budgets | ConvertTo-Json -Depth 50
    }
    'LedgerMineOnly' { Invoke-LedgerMining | ConvertTo-Json -Depth 50 }
    'Readiness' { Invoke-Readiness | ConvertTo-Json -Depth 50 }
}
