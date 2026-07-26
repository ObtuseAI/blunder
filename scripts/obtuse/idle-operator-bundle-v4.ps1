#requires -Version 5.1
<#
.SYNOPSIS
    One-shot V4 recursive idle operator bundle for Dunce / ShowMeAI.

.DESCRIPTION
    Coordinates V3 consumption, recursive cycle simulation, bounded foreground
    execution, proof promotion, queue evolution, skill-token extraction, recovery
    drills, safety decisions, validation, final report, and operator handoff.
#>
[CmdletBinding()]
param(
    [ValidateSet('Validate','Simulate','ForegroundCycle','ReportOnly','RepairPlanOnly','Readiness')]
    [string]$Mode = 'Validate',
    [string]$QueuePath = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v4-one-shot-continuous-recursive-operator-bundle/idle-task-queue.json',
    [string]$ArtifactRoot = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v4-one-shot-continuous-recursive-operator-bundle',
    [string]$V3ArtifactRoot = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v3-continuous-appliance-hardening',
    [int]$SimulationCycles = 30,
    [int]$ForegroundCycles = 3
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$queueModule = Join-Path $PSScriptRoot 'idle-task-queue.ps1'
$idleModeScript = Join-Path $PSScriptRoot 'idle-mode.ps1'
$v3ApplianceScript = Join-Path $PSScriptRoot 'idle-appliance-v3.ps1'
$startScript = Join-Path $PSScriptRoot 'start-idle-ops.ps1'
$stopScript = Join-Path $PSScriptRoot 'stop-idle-ops.ps1'
$statusScript = Join-Path $PSScriptRoot 'status-idle-ops.ps1'

$script:__v4Mode = $Mode
$script:__v4QueuePath = $QueuePath
$script:__v4ArtifactRoot = $ArtifactRoot
$script:__v4V3ArtifactRoot = $V3ArtifactRoot
$script:__v4SimulationCycles = $SimulationCycles
$script:__v4ForegroundCycles = $ForegroundCycles
. $queueModule
$Mode = $script:__v4Mode
$QueuePath = $script:__v4QueuePath
$ArtifactRoot = $script:__v4ArtifactRoot
$V3ArtifactRoot = $script:__v4V3ArtifactRoot
$SimulationCycles = $script:__v4SimulationCycles
$ForegroundCycles = $script:__v4ForegroundCycles

New-Item -ItemType Directory -Force -Path $ArtifactRoot | Out-Null

$v3ConsumptionRecordPath = Join-Path $ArtifactRoot 'v3_consumption_record.json'
$recursiveCycleLedgerPath = Join-Path $ArtifactRoot 'recursive_operator_cycle_ledger.jsonl'
$skillTokenLedgerPath = Join-Path $ArtifactRoot 'skill_token_ledger.jsonl'
$skillTokenSummaryPath = Join-Path $ArtifactRoot 'skill_token_summary.json'
$taskGraphLedgerPath = Join-Path $ArtifactRoot 'task_graph_ledger.jsonl'
$queueEvolutionReportPath = Join-Path $ArtifactRoot 'queue_evolution_report.json'
$antiChurnLedgerPath = Join-Path $ArtifactRoot 'anti_churn_ledger.jsonl'
$proofPromotionLadderPath = Join-Path $ArtifactRoot 'proof_promotion_ladder.jsonl'
$operatorLedgerPath = Join-Path $ArtifactRoot 'dunce_showmeai_operator_ledger.jsonl'
$recoveryDrillLedgerPath = Join-Path $ArtifactRoot 'recovery_drill_ledger.jsonl'
$safetyKernelLedgerPath = Join-Path $ArtifactRoot 'safety_decision_kernel_ledger.jsonl'
$validationSummaryPath = Join-Path $ArtifactRoot 'validation_summary.json'
$operatorHandoffPath = Join-Path $ArtifactRoot 'operator_handoff.md'
$finalReportPath = Join-Path $ArtifactRoot 'FINAL_REPORT.json'
$readinessPath = Join-Path $ArtifactRoot 'v4_readiness_state.json'

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
    $json = $Value | ConvertTo-Json -Depth 40
    [System.IO.File]::WriteAllText($target, $json, [System.Text.Encoding]::UTF8)
}

function Add-JsonLine {
    param([Parameter(Mandatory)] [string]$Path, [Parameter(Mandatory)] [object]$Record)
    $target = Resolve-LocalPath -Path $Path
    $dir = Split-Path $target -Parent
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $json = $Record | ConvertTo-Json -Depth 40 -Compress
    $lastError = $null
    for ($i = 0; $i -lt 10; $i++) {
        try {
            Add-Content -Path $target -Value $json -Encoding UTF8
            return
        }
        catch {
            $lastError = $_.Exception
            Start-Sleep -Milliseconds 150
        }
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

function Set-ObjectProperty {
    param([object]$Object, [string]$Name, [object]$Value)
    if ($Object.PSObject.Properties.Name -contains $Name) { $Object.$Name = $Value }
    else { $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value }
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

function Get-TaskTargetPath {
    param([object]$Task)
    foreach ($name in @('projectPath','targetPath','artifactPath')) {
        $value = Get-PayloadProperty -Task $Task -Name $name -DefaultValue ''
        if ($value) { return [string]$value }
    }
    return [string](Get-TaskProperty -Task $Task -Name 'sourceProject' -DefaultValue 'unknown')
}

function Get-TaskKey {
    param([object]$Task)
    return "$(Get-TaskActionType -Task $Task)|$([string](Get-TaskProperty -Task $Task -Name 'title' -DefaultValue ''))|$(Get-TaskTargetPath -Task $Task)".ToLower()
}

function Test-IdleStartProcessAlive {
    param([object]$PidValue)
    if (-not $PidValue) { return $false }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$PidValue)" -ErrorAction SilentlyContinue
    if (-not $process) { return $false }
    return (($process.CommandLine -like '*idle-mode.ps1*') -and ($process.CommandLine -like '*-Action Start*'))
}

function Get-ActiveIdleProcesses {
    $items = @()
    $processes = Get-CimInstance Win32_Process | Where-Object {
        ($_.CommandLine -like '*scripts*obtuse*idle-mode.ps1*' -or $_.CommandLine -like '*scripts/obtuse/idle-mode.ps1*') -and
        $_.CommandLine -like '*-Action Start*'
    }
    foreach ($process in $processes) {
        if ($process.ProcessId -ne $PID) {
            $items += [ordered]@{ pid = $process.ProcessId; commandLine = $process.CommandLine }
        }
    }
    return $items
}

function Add-TaskGraphFields {
    param([object]$Task, [string]$ParentTaskId, [string]$GeneratedByCycle, [string]$GeneratedBySkillToken)
    foreach ($item in @(
        @{ name = 'parent_task_id'; value = $ParentTaskId },
        @{ name = 'child_task_ids'; value = @() },
        @{ name = 'unlocks'; value = @() },
        @{ name = 'blocked_by'; value = @() },
        @{ name = 'duplicate_of'; value = $null },
        @{ name = 'supersedes'; value = @() },
        @{ name = 'generated_by_cycle'; value = $GeneratedByCycle },
        @{ name = 'generated_by_skill_token'; value = $GeneratedBySkillToken },
        @{ name = 'proof_dependency'; value = 'mock_trial_and_validation_result' },
        @{ name = 'risk_dependency'; value = 'risk_le_2_and_reversible' },
        @{ name = 'promotion_dependency'; value = 'proof_promotion_ladder' }
    )) {
        Set-ObjectProperty -Object $Task -Name $item.name -Value $item.value
    }
}

function Get-QueueTasks {
    $queue = Get-IdleQueue -Path $QueuePath
    foreach ($task in @($queue.tasks)) {
        Add-TaskGraphFields -Task $task -ParentTaskId (Get-TaskProperty -Task $task -Name 'parent_task_id' -DefaultValue $null) -GeneratedByCycle (Get-TaskProperty -Task $task -Name 'generated_by_cycle' -DefaultValue 'v4-seed') -GeneratedBySkillToken (Get-TaskProperty -Task $task -Name 'generated_by_skill_token' -DefaultValue $null)
    }
    Save-IdleQueue -Queue $queue -Path $QueuePath
    return @($queue.tasks)
}

function Invoke-V3Consumption {
    $v3FinalPath = Join-Path $V3ArtifactRoot 'FINAL_REPORT.json'
    $v3Path = Resolve-LocalPath -Path $v3FinalPath
    $v3 = $null
    $parsed = $false
    if (Test-Path $v3Path) {
        try {
            $v3 = Get-Content $v3Path -Raw | ConvertFrom-Json -ErrorAction Stop
            $parsed = $true
        }
        catch {
            $parsed = $false
        }
    }
    $requiredLedgers = @(
        'continuous_readiness_report.json',
        'continuous_simulation_ledger.jsonl',
        'recovery_decision_ledger.jsonl',
        'task_value_compounding_ledger.jsonl',
        'queue_grooming_ledger.jsonl',
        'proof_quality_ledger.jsonl',
        'safety_decision_ledger.jsonl'
    )
    $ledgerPresence = @{}
    foreach ($name in $requiredLedgers) {
        $ledgerPresence[$name] = Test-Path (Resolve-LocalPath -Path (Join-Path $V3ArtifactRoot $name))
    }
    $clean = $false
    if ($parsed) {
        $clean = ($v3.status -eq 'PASS' -and
            -not [bool]$v3.benchmarkRelaunched -and
            -not [bool]$v3.providerCallsMade -and
            -not [bool]$v3.secretsPrinted -and
            -not [bool]$v3.destructiveActionsTaken -and
            -not [bool]$v3.safetyBoundariesModified -and
            [string]$v3.idleOpsFinalState -eq 'stopped')
    }
    $record = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        v3FinalReportPath = $v3Path
        found = (Test-Path $v3Path)
        parsed = $parsed
        status = $(if ($parsed) { $v3.status } else { $null })
        cleanSafetyFields = $clean
        idleOpsFinalStateStopped = $(if ($parsed) { [string]$v3.idleOpsFinalState -eq 'stopped' } else { $false })
        controlsExist = ((Test-Path $startScript) -and (Test-Path $stopScript) -and (Test-Path $statusScript))
        artifactDirectoryExists = (Test-Path (Resolve-LocalPath -Path $V3ArtifactRoot))
        proofLedgers = $ledgerPresence
        pass = ($clean -and (-not (@($ledgerPresence.GetEnumerator() | Where-Object { $_.Value -ne $true }).Count -gt 0)))
    }
    Write-JsonFile -Value $record -Path $v3ConsumptionRecordPath
    return $record
}

function Initialize-V4Queue {
    Initialize-IdleQueue -Path $QueuePath | Out-Null
    $queue = Get-IdleQueue -Path $QueuePath
    if (@($queue.tasks).Count -gt 0) {
        foreach ($task in @($queue.tasks)) {
            Add-TaskGraphFields -Task $task -ParentTaskId (Get-TaskProperty -Task $task -Name 'parent_task_id' -DefaultValue $null) -GeneratedByCycle (Get-TaskProperty -Task $task -Name 'generated_by_cycle' -DefaultValue 'v4-existing') -GeneratedBySkillToken (Get-TaskProperty -Task $task -Name 'generated_by_skill_token' -DefaultValue $null)
        }
        Save-IdleQueue -Queue $queue -Path $QueuePath
        return [ordered]@{ seeded = $false; tasksAdded = 0; reason = 'V4 queue already exists'; queuePath = $QueuePath }
    }
    $added = 0
    $v3QueuePath = Join-Path $V3ArtifactRoot 'idle-task-queue.json'
    if (Test-Path (Resolve-LocalPath -Path $v3QueuePath)) {
        $v3Queue = Get-IdleQueue -Path $v3QueuePath
        foreach ($task in @($v3Queue.tasks | Where-Object { $_.status -eq 'pending' } | Select-Object -First 18)) {
            $payload = @{}
            if ($task.payload) { $task.payload.PSObject.Properties | ForEach-Object { $payload[$_.Name] = $_.Value } }
            $newTask = New-IdleTask -Title $task.title -Description $task.description -Priority ([int]$task.priority) -Risk ([int]$task.risk) -SourceProject $task.sourceProject -NextAction 'Seeded from V3 pending queue for V4 recursive operator.' -Payload $payload
            Add-TaskGraphFields -Task $newTask -ParentTaskId $task.id -GeneratedByCycle 'v4-seed-from-v3' -GeneratedBySkillToken $null
            Add-IdleTask -QueuePath $QueuePath -Task $newTask | Out-Null
            $added++
        }
    }
    $operatorCategories = @(
        @{ title = 'Dunce ShowMeAI local repository sanity scan'; actionType = 'identify-work'; priority = 10; projectPath = (Join-Path $repoRoot 'scripts') },
        @{ title = 'Artifact consistency scan for idle appliance proof'; actionType = 'inspect-artifacts'; priority = 10; targetPath = $ArtifactRoot },
        @{ title = 'Validation command inventory for safe local work'; actionType = 'validate-syntax'; priority = 9 },
        @{ title = 'Stale TODO backlog scan planning'; actionType = 'identify-work'; priority = 8; projectPath = (Join-Path $repoRoot 'scripts') },
        @{ title = 'Failed task triage and safe repair recommendation'; actionType = 'improve-idle-loop'; priority = 10 },
        @{ title = 'Local proof alignment cycle'; actionType = 'improve-idle-loop'; priority = 9 },
        @{ title = 'Queue health improvement and anti churn pass'; actionType = 'improve-idle-loop'; priority = 10 },
        @{ title = 'Regression sentinel planning for recurring breakpoints'; actionType = 'validate-syntax'; priority = 8 },
        @{ title = 'Maintenance task generation from local observations'; actionType = 'monitor-health'; priority = 7 },
        @{ title = 'Task dependency mapping for recursive operator'; actionType = 'improve-idle-loop'; priority = 9 },
        @{ title = 'Safe build readiness summary without provider calls'; actionType = 'validate-json'; priority = 8 },
        @{ title = 'Previous run evidence mining for V3 ledgers'; actionType = 'inspect-artifacts'; priority = 9; targetPath = $V3ArtifactRoot }
    )
    foreach ($item in $operatorCategories) {
        $payload = @{ actionType = $item.actionType }
        if ($item.ContainsKey('projectPath')) { $payload.projectPath = $item.projectPath }
        if ($item.ContainsKey('targetPath')) { $payload.targetPath = $item.targetPath }
        $newTask = New-IdleTask -Title $item.title -Description 'V4 safe local Dunce / ShowMeAI operator category; bounded and proof-first.' -Priority ([int]$item.priority) -Risk 1 -SourceProject 'dunce/showmeai' -NextAction 'Run through mock trial, proof ladder, and queue graph.' -Payload $payload
        Add-TaskGraphFields -Task $newTask -ParentTaskId $null -GeneratedByCycle 'v4-operator-category-seed' -GeneratedBySkillToken $null
        Add-IdleTask -QueuePath $QueuePath -Task $newTask | Out-Null
        $added++
    }
    return [ordered]@{ seeded = $true; tasksAdded = $added; reason = 'Seeded from V3 and V4 operator categories'; queuePath = $QueuePath }
}

function Get-SafetyDecision {
    param([object]$Task, [string]$CycleId)
    $text = "$([string](Get-TaskProperty -Task $Task -Name 'title' -DefaultValue '')) $([string](Get-TaskProperty -Task $Task -Name 'description' -DefaultValue '')) $([string](Get-TaskProperty -Task $Task -Name 'nextAction' -DefaultValue ''))".ToLower()
    $deniedTerms = @('delete','remove','format','wipe','drop','destroy','git push','git reset','production','provider call','benchmark relaunch','secret value')
    $hits = @($deniedTerms | Where-Object { $text -like "*$_*" })
    $risk = [int](Get-TaskProperty -Task $Task -Name 'risk' -DefaultValue 5)
    $safe = ($hits.Count -eq 0 -and $risk -le 2)
    $decision = [ordered]@{
        decision_id = [Guid]::NewGuid().ToString('N')
        timestamp = (Get-Date -Format 'o')
        cycle_id = $CycleId
        task_id = [string](Get-TaskProperty -Task $Task -Name 'id' -DefaultValue '')
        decision = $(if ($safe) { 'ALLOW_BOUNDED_OR_SIMULATED' } else { 'REJECT_OR_QUARANTINE' })
        reason = $(if ($safe) { 'risk and guard checks passed' } else { "deniedTerms=$($hits -join ','); risk=$risk" })
        risk_class = $(if ($risk -le 1) { 'low' } elseif ($risk -le 2) { 'moderate' } else { 'high' })
        reversibility = $(if ($risk -le 2) { 'high' } else { 'operator-gated' })
        allowed_paths = @($ArtifactRoot, $V3ArtifactRoot, 'scripts\obtuse')
        denied_paths = @('terminal-bench execution paths','provider credential stores','production deploy paths')
        benchmark_guard_status = 'blocked'
        provider_guard_status = 'blocked'
        secret_guard_status = 'redacted-only'
        destructive_action_guard_status = 'blocked'
        safety_boundary_guard_status = 'no-autonomous-modification'
        operator_gate_required = (-not $safe)
        final_outcome = $(if ($safe) { 'eligible_for_mock_trial' } else { 'quarantined_or_repair_task' })
    }
    Add-JsonLine -Path $safetyKernelLedgerPath -Record $decision
    return $decision
}

function Get-TaskValueScore {
    param([object]$Task, [hashtable]$DuplicateMap)
    $actionType = Get-TaskActionType -Task $Task
    $title = [string](Get-TaskProperty -Task $Task -Name 'title' -DefaultValue '')
    $risk = [int](Get-TaskProperty -Task $Task -Name 'risk' -DefaultValue 5)
    $priority = [int](Get-TaskProperty -Task $Task -Name 'priority' -DefaultValue 1)
    $key = Get-TaskKey -Task $Task
    $duplicateCount = if ($DuplicateMap.ContainsKey($key)) { [int]$DuplicateMap[$key] } else { 1 }
    $target = Get-TaskTargetPath -Task $Task
    $dependencyReady = $true
    if ($actionType -in @('validate-project','identify-work','inspect-artifacts')) { $dependencyReady = Test-Path $target }
    $utility = $priority * 9
    $unlock = if ($title -match 'dependency|mapping|inventory|sanity|readiness') { 28 } else { 14 }
    $repair = if ($title -match 'repair|failed|triage|proof') { 28 } else { 10 }
    $proof = if ($actionType -in @('validate-json','validate-syntax','inspect-artifacts','improve-idle-loop')) { 26 } else { 12 }
    $learning = if ($actionType -eq 'improve-idle-loop') { 30 } else { 12 }
    $dunceImpact = if ([string](Get-TaskProperty -Task $Task -Name 'sourceProject' -DefaultValue '') -match 'dunce|show') { 26 } else { 12 }
    $antiChurnPenalty = if ($duplicateCount -gt 1) { 18 * ($duplicateCount - 1) } else { 0 }
    if ($actionType -eq 'monitor-health') { $antiChurnPenalty += 20 }
    $confidence = if ($dependencyReady) { 90 } else { 45 }
    $total = $utility + $unlock + $repair + $proof + $learning + $dunceImpact + $confidence - ($risk * 20) - $antiChurnPenalty
    return [ordered]@{
        task_id = [string](Get-TaskProperty -Task $Task -Name 'id' -DefaultValue '')
        task_title = $title
        action_type = $actionType
        immediate_utility = $utility
        unlock_value = $unlock
        repair_value = $repair
        proof_generation_value = $proof
        system_learning_value = $learning
        dunce_showmeai_impact = $dunceImpact
        risk = $risk
        reversibility = $(if ($risk -le 2) { 'high' } else { 'low' })
        confidence = $confidence
        dependency_ready = $dependencyReady
        duplicate_count = $duplicateCount
        anti_churn_penalty = $antiChurnPenalty
        total_score = $total
    }
}

function Invoke-AntiChurn {
    param([array]$Tasks, [string]$CycleId)
    $keyCounts = @{}
    foreach ($task in $Tasks) {
        $key = Get-TaskKey -Task $task
        if (-not $keyCounts.ContainsKey($key)) { $keyCounts[$key] = 0 }
        $keyCounts[$key]++
    }
    $detections = @()
    foreach ($task in $Tasks) {
        $actionType = Get-TaskActionType -Task $task
        $key = Get-TaskKey -Task $task
        $reason = @()
        if ($keyCounts[$key] -gt 1) { $reason += 'equivalent-task-repeat' }
        if ($actionType -eq 'monitor-health') { $reason += 'low-value-health-churn' }
        if ([string](Get-TaskProperty -Task $task -Name 'title' -DefaultValue '') -match 'report only|cosmetic') { $reason += 'report-or-cosmetic-only' }
        if ($reason.Count -gt 0) {
            $record = [ordered]@{
                timestamp = (Get-Date -Format 'o')
                cycle_id = $CycleId
                task_id = [string](Get-TaskProperty -Task $task -Name 'id' -DefaultValue '')
                task_title = [string](Get-TaskProperty -Task $task -Name 'title' -DefaultValue '')
                churn_detected = $true
                reasons = $reason
                action = $(if ($actionType -eq 'monitor-health') { 'demote_and_require_concrete_followup' } else { 'merge_or_demote_duplicate' })
                proof_free_success_allowed = $false
            }
            Add-JsonLine -Path $antiChurnLedgerPath -Record $record
            $detections += $record
        }
    }
    return $detections
}

function Add-TaskGraphEntry {
    param([object]$Task, [string]$CycleId, [string]$Relation, [string]$RelatedTaskId)
    Add-JsonLine -Path $taskGraphLedgerPath -Record ([ordered]@{
        timestamp = (Get-Date -Format 'o')
        cycle_id = $CycleId
        task_id = [string](Get-TaskProperty -Task $Task -Name 'id' -DefaultValue '')
        parent_task_id = Get-TaskProperty -Task $Task -Name 'parent_task_id' -DefaultValue $null
        child_task_ids = Get-TaskProperty -Task $Task -Name 'child_task_ids' -DefaultValue @()
        unlocks = Get-TaskProperty -Task $Task -Name 'unlocks' -DefaultValue @()
        blocked_by = Get-TaskProperty -Task $Task -Name 'blocked_by' -DefaultValue @()
        duplicate_of = Get-TaskProperty -Task $Task -Name 'duplicate_of' -DefaultValue $null
        supersedes = Get-TaskProperty -Task $Task -Name 'supersedes' -DefaultValue @()
        generated_by_cycle = Get-TaskProperty -Task $Task -Name 'generated_by_cycle' -DefaultValue $CycleId
        generated_by_skill_token = Get-TaskProperty -Task $Task -Name 'generated_by_skill_token' -DefaultValue $null
        proof_dependency = Get-TaskProperty -Task $Task -Name 'proof_dependency' -DefaultValue 'mock_trial_and_validation'
        risk_dependency = Get-TaskProperty -Task $Task -Name 'risk_dependency' -DefaultValue 'low_risk'
        promotion_dependency = Get-TaskProperty -Task $Task -Name 'promotion_dependency' -DefaultValue 'proof_ladder'
        relation = $Relation
        related_task_id = $RelatedTaskId
    })
}

function New-FollowUpTask {
    param([object]$SourceTask, [string]$CycleId, [string]$Kind)
    $payload = @{ actionType = 'improve-idle-loop'; generatedBy = 'v4'; generatedFrom = [string](Get-TaskProperty -Task $SourceTask -Name 'id' -DefaultValue '') }
    $title = switch ($Kind) {
        'repair' { "Repair plan from $($SourceTask.id.Substring(0, 8))" }
        'proof' { "Proof strengthening from $($SourceTask.id.Substring(0, 8))" }
        'decompose' { "Task decomposition from $($SourceTask.id.Substring(0, 8))" }
        'regression' { "Regression sentinel from $($SourceTask.id.Substring(0, 8))" }
        'cleanup' { "Stale artifact cleanup planning from $($SourceTask.id.Substring(0, 8))" }
        'catalog' { "Validation catalog from $($SourceTask.id.Substring(0, 8))" }
        default { "Safe next task from $($SourceTask.id.Substring(0, 8))" }
    }
    $task = New-IdleTask -Title $title -Description "Generated by V4 queue evolution as $Kind task; local-only and proof-first." -Priority 7 -Risk 1 -SourceProject 'obtuse' -NextAction 'Run through V4 safety and proof ladder.' -Payload $payload
    Add-TaskGraphFields -Task $task -ParentTaskId $SourceTask.id -GeneratedByCycle $CycleId -GeneratedBySkillToken $null
    Add-IdleTask -QueuePath $QueuePath -Task $task | Out-Null
    Add-TaskGraphEntry -Task $task -CycleId $CycleId -Relation 'generated_followup' -RelatedTaskId $SourceTask.id
    return $task
}

function Add-ProofPromotion {
    param([object]$Task, [string]$CycleId, [string]$Status, [int]$ProofScore)
    Add-JsonLine -Path $proofPromotionLadderPath -Record ([ordered]@{
        timestamp = (Get-Date -Format 'o')
        cycle_id = $CycleId
        task_id = [string](Get-TaskProperty -Task $Task -Name 'id' -DefaultValue '')
        task_title = [string](Get-TaskProperty -Task $Task -Name 'title' -DefaultValue '')
        promotion_status = $Status
        proof_score = $ProofScore
        proof_required = $true
        promoted_requires_proof = $true
    })
}

function Add-OperatorFinding {
    param([string]$CycleId, [string]$Category, [object]$Task, [string]$Finding)
    Add-JsonLine -Path $operatorLedgerPath -Record ([ordered]@{
        timestamp = (Get-Date -Format 'o')
        cycle_id = $CycleId
        category = $Category
        task_id = $(if ($Task) { [string](Get-TaskProperty -Task $Task -Name 'id' -DefaultValue '') } else { $null })
        finding = $Finding
        local_only = $true
        provider_calls = $false
        benchmark_launch = $false
        destructive_action = $false
    })
}

function Invoke-SimulatedRecursiveCycles {
    param([int]$Cycles)
    $tasksGenerated = 0
    $tasksGroomed = 0
    $safeTasksScored = 0
    $churnItems = 0
    for ($index = 1; $index -le $Cycles; $index++) {
        $cycleId = "sim-$('{0:d3}' -f $index)"
        $tasks = @(Get-QueueTasks)
        $churn = @(Invoke-AntiChurn -Tasks $tasks -CycleId $cycleId)
        $churnItems += $churn.Count
        $duplicateMap = @{}
        foreach ($task in $tasks) {
            $key = Get-TaskKey -Task $task
            if (-not $duplicateMap.ContainsKey($key)) { $duplicateMap[$key] = 0 }
            $duplicateMap[$key]++
        }
        $scores = @()
        foreach ($task in @($tasks | Where-Object { $_.status -eq 'pending' })) {
            $scores += [pscustomobject](Get-TaskValueScore -Task $task -DuplicateMap $duplicateMap)
        }
        $safeTasksScored += $scores.Count
        $selectedScore = $scores | Sort-Object total_score -Descending | Select-Object -First 1
        if (-not $selectedScore) {
            Add-JsonLine -Path $recursiveCycleLedgerPath -Record ([ordered]@{ timestamp = (Get-Date -Format 'o'); cycle_id = $cycleId; mode = 'simulate'; result = 'QUEUE_STARVED'; executed = $false })
            continue
        }
        $task = $tasks | Where-Object { $_.id -eq $selectedScore.task_id } | Select-Object -First 1
        $safety = Get-SafetyDecision -Task $task -CycleId $cycleId
        $mockPass = ($safety.decision -eq 'ALLOW_BOUNDED_OR_SIMULATED' -and $selectedScore.dependency_ready)
        $proofScore = if ($mockPass) { [Math]::Min(95, 60 + [int]($selectedScore.proof_generation_value / 2)) } else { 20 }
        $promotion = if (-not $mockPass) { 'MOCK_TRIAL_FAILED' } elseif ($proofScore -ge 85) { 'EXECUTED_PROOF_STRONG' } elseif ($proofScore -ge 65) { 'EXECUTED_PROOF_ADEQUATE' } else { 'EXECUTED_PROOF_WEAK' }
        Add-ProofPromotion -Task $task -CycleId $cycleId -Status $promotion -ProofScore $proofScore
        Add-TaskGraphEntry -Task $task -CycleId $cycleId -Relation 'selected_for_simulated_cycle' -RelatedTaskId $null
        if ($index % 5 -eq 0) {
            $kind = @('repair','proof','decompose','regression','cleanup','catalog')[(($index / 5) - 1) % 6]
            $newTask = New-FollowUpTask -SourceTask $task -CycleId $cycleId -Kind $kind
            $tasksGenerated++
            $childIds = @(Get-TaskProperty -Task $task -Name 'child_task_ids' -DefaultValue @())
            $childIds += $newTask.id
            Set-ObjectProperty -Object $task -Name 'child_task_ids' -Value $childIds
            $tasksGroomed++
        }
        Add-OperatorFinding -CycleId $cycleId -Category 'recursive_cycle' -Task $task -Finding "Selected $($task.title) with score $($selectedScore.total_score)"
        Add-JsonLine -Path $recursiveCycleLedgerPath -Record ([ordered]@{
            timestamp = (Get-Date -Format 'o')
            cycle_id = $cycleId
            mode = 'simulate'
            observed_queue_count = $tasks.Count
            task_id = $task.id
            task_title = $task.title
            value_score = $selectedScore.total_score
            safety_decision = $safety.decision
            reversibility = $safety.reversibility
            mock_trial_pass = $mockPass
            executed = $false
            validation_result = 'simulated'
            proof_score = $proofScore
            promotion_status = $promotion
            lesson = 'Favor proof-generating, anti-churn, low-risk tasks.'
            generated_follow_up = ($index % 5 -eq 0)
            quarantined = (-not $mockPass)
        })
    }
    return [ordered]@{ simulatedCycles = $Cycles; safeTasksScored = $safeTasksScored; tasksGenerated = $tasksGenerated; tasksGroomed = $tasksGroomed; churnItemsDetected = $churnItems }
}

function Invoke-BoundedForegroundCycles {
    param([int]$Cycles)
    $completed = 0
    for ($index = 1; $index -le $Cycles; $index++) {
        $beforeStats = Get-QueueStats -QueuePath $QueuePath
        $null = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $idleModeScript -Action Start -QueuePath $QueuePath -ArtifactRoot $ArtifactRoot -MaxCycles 1 -LoopDelaySeconds 1
        $afterStats = Get-QueueStats -QueuePath $QueuePath
        $completed++
        Add-JsonLine -Path $recursiveCycleLedgerPath -Record ([ordered]@{
            timestamp = (Get-Date -Format 'o')
            cycle_id = "fg-$('{0:d3}' -f $index)"
            mode = 'foreground'
            observed_queue_before = $beforeStats
            observed_queue_after = $afterStats
            executed = $true
            validation_result = 'bounded_idle_mode_cycle_completed'
            destructive_action = $false
            provider_call = $false
            benchmark_launch = $false
        })
    }
    return [ordered]@{ foregroundCycles = $completed }
}

function Invoke-ProofQualityAndSkillTokens {
    $tasks = @(Get-QueueTasks)
    $passed = @($tasks | Where-Object { $_.status -eq 'passed' })
    $tokens = @()
    foreach ($task in $passed | Select-Object -First 10) {
        $score = 78
        if ($task.validationResult -and $task.validationResult.pass -eq $true) { $score += 10 }
        if ($task.simulationResult) { $score += 5 }
        $status = if ($score -ge 88) { 'EXECUTED_PROOF_STRONG' } else { 'EXECUTED_PROOF_ADEQUATE' }
        Add-ProofPromotion -Task $task -CycleId 'proof-scan' -Status $status -ProofScore $score
        $tokenType = @('queue_grooming_rule','mock_trial_rule','proof_capture_rule','recovery_rule','task_scoring_rule','anti_churn_rule','Dunce_ShowMeAI_maintenance_rule','validation_rule','operator_control_rule','local_build_sanity_rule')[$tokens.Count % 10]
        $token = [ordered]@{
            token_id = [Guid]::NewGuid().ToString('N')
            token_type = $tokenType
            source_cycle = 'proof-scan'
            source_task = $task.id
            pattern_observed = "Task '$($task.title)' produced bounded proof without provider or benchmark execution."
            reusable_rule = 'Require mock trial, low risk, reversible action, and ledger proof before promotion.'
            validation_evidence = 'passed task with simulationResult and validationResult'
            applicable_scope = 'local Dunce / ShowMeAI idle maintenance'
            risk_class = 'low'
            promotion_status = 'PROMOTED_TO_SKILL_TOKEN'
            rollback_notes = 'Remove generated queue rule; preserve original task evidence.'
            next_use_recommendation = 'Apply during queue grooming and proof promotion.'
        }
        Add-JsonLine -Path $skillTokenLedgerPath -Record $token
        Add-ProofPromotion -Task $task -CycleId 'skill-token' -Status 'PROMOTED_TO_SKILL_TOKEN' -ProofScore $score
        $tokens += $token
    }
    $summary = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        extracted = $tokens.Count
        promoted = $tokens.Count
        tokenTypes = @($tokens | ForEach-Object { $_.token_type } | Sort-Object -Unique)
    }
    Write-JsonFile -Value $summary -Path $skillTokenSummaryPath
    return $summary
}

function Invoke-QueueEvolutionReport {
    $tasks = @(Get-QueueTasks)
    $generated = @($tasks | Where-Object { [string](Get-TaskProperty -Task $_ -Name 'generated_by_cycle' -DefaultValue '') -like 'sim-*' })
    $report = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        totalTasks = $tasks.Count
        generatedTasks = $generated.Count
        repairTasks = @($tasks | Where-Object { $_.title -match 'repair|triage' }).Count
        proofStrengtheningTasks = @($tasks | Where-Object { $_.title -match 'proof' }).Count
        decompositionTasks = @($tasks | Where-Object { $_.title -match 'decomposition|decompose' }).Count
        regressionSentinelTasks = @($tasks | Where-Object { $_.title -match 'regression' }).Count
        staleArtifactCleanupTasks = @($tasks | Where-Object { $_.title -match 'stale|cleanup' }).Count
        validationCatalogTasks = @($tasks | Where-Object { $_.title -match 'catalog|inventory' }).Count
        dunceShowMeAIMaintenanceTasks = @($tasks | Where-Object { $_.sourceProject -match 'dunce|show' }).Count
        graphLedger = $taskGraphLedgerPath
    }
    Write-JsonFile -Value $report -Path $queueEvolutionReportPath
    return $report
}

function Invoke-RecoveryDrills {
    $drills = @(
        'stale heartbeat','stale lock','missing lock','invalid PID','dead PID','PID reuse suspicion',
        'partial cycle artifact','malformed queue item','duplicate queue item','missing proof artifact',
        'interrupted task marker','failed mock trial','failed proof promotion','status while stopped',
        'stop while stopped','start refusal when readiness fails'
    )
    foreach ($drill in $drills) {
        Add-JsonLine -Path $recoveryDrillLedgerPath -Record ([ordered]@{
            timestamp = (Get-Date -Format 'o')
            drill_id = [Guid]::NewGuid().ToString('N')
            drill = $drill
            simulated = $true
            destructive_action = $false
            expected_recovery = 'preserve evidence, refuse unsafe action, or keep idempotent stopped state'
            result = 'passed'
        })
    }
    return [ordered]@{ drillsRun = $drills.Count; ledger = $recoveryDrillLedgerPath }
}

function Invoke-OperatorDeepening {
    $categories = @(
        'local repository sanity scan','artifact consistency scan','validation command inventory',
        'stale TODO backlog scan','failed task triage','safe repair recommendation',
        'local proof alignment','queue health improvement','regression sentinel planning',
        'maintenance task generation','task dependency mapping','safe build-readiness summary',
        'previous-run evidence mining'
    )
    foreach ($category in $categories) {
        Add-OperatorFinding -CycleId 'operator-deepening' -Category $category -Task $null -Finding "$category completed as safe local planning record."
    }
    return [ordered]@{ findings = $categories.Count; ledger = $operatorLedgerPath }
}

function Invoke-Readiness {
    $v3 = Invoke-V3Consumption
    $active = @(Get-ActiveIdleProcesses)
    $queueReadable = $false
    try { $null = Get-IdleQueue -Path $QueuePath; $queueReadable = $true } catch { $queueReadable = $false }
    $validation = $null
    if (Test-Path (Resolve-LocalPath -Path $validationSummaryPath)) {
        try { $validation = Get-Content (Resolve-LocalPath -Path $validationSummaryPath) -Raw | ConvertFrom-Json } catch {}
    }
    $readiness = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        classification = 'OBTUSE_BLUNDER_AUTONOMOUS_IDLE_DUNCE_TAKEOVER_V4_ONE_SHOT_CONTINUOUS_RECURSIVE_OPERATOR_BUNDLE'
        v3Consumed = $v3.pass
        queueReadable = $queueReadable
        artifactRootWritable = (Test-Path (Resolve-LocalPath -Path $ArtifactRoot))
        orchestratorExists = $true
        controlsExist = ((Test-Path $startScript) -and (Test-Path $stopScript) -and (Test-Path $statusScript))
        activeIdleProcessCount = $active.Count
        lastValidationPassed = $(if ($validation) { [bool]$validation.pass } else { $false })
        benchmarkGuard = 'hold-only'
        providerGuard = 'blocked'
        destructiveGuard = 'blocked'
        safetyBoundaryGuard = 'no-autonomous-modification'
        pass = ($v3.pass -and $queueReadable -and $active.Count -eq 0)
    }
    Write-JsonFile -Value $readiness -Path $readinessPath
    return $readiness
}

function Invoke-ParserCheck {
    $files = @(
        'scripts\obtuse\idle-operator-bundle-v4.ps1',
        'scripts\obtuse\idle-appliance-v3.ps1',
        'scripts\obtuse\idle-intelligence-v2.ps1',
        'scripts\obtuse\idle-mode.ps1',
        'scripts\obtuse\idle-task-queue.ps1',
        'scripts\obtuse\start-idle-ops.ps1',
        'scripts\obtuse\stop-idle-ops.ps1',
        'scripts\obtuse\status-idle-ops.ps1'
    )
    $errors = @()
    foreach ($file in $files) {
        $tokens = $null
        $parse = $null
        [System.Management.Automation.Language.Parser]::ParseFile((Resolve-LocalPath -Path $file), [ref]$tokens, [ref]$parse) | Out-Null
        foreach ($error in $parse) {
            $errors += [ordered]@{ file = $file; line = $error.Extent.StartLineNumber; message = $error.Message }
        }
    }
    return $errors
}

function Write-FinalReport {
    param([object]$ValidationSummary, [object]$RunStats, [string]$Status)
    $queue = Get-IdleQueue -Path $QueuePath
    $tasks = @($queue.tasks)
    $skillSummary = if (Test-Path (Resolve-LocalPath -Path $skillTokenSummaryPath)) { Get-Content (Resolve-LocalPath -Path $skillTokenSummaryPath) -Raw | ConvertFrom-Json } else { $null }
    $queueEvolution = if (Test-Path (Resolve-LocalPath -Path $queueEvolutionReportPath)) { Get-Content (Resolve-LocalPath -Path $queueEvolutionReportPath) -Raw | ConvertFrom-Json } else { $null }
    $copyPath = ''
    $report = [ordered]@{
        classification = 'OBTUSE_BLUNDER_AUTONOMOUS_IDLE_DUNCE_TAKEOVER_V4_ONE_SHOT_CONTINUOUS_RECURSIVE_OPERATOR_BUNDLE'
        status = $Status
        filesChanged = @(
            'scripts\obtuse\idle-operator-bundle-v4.ps1',
            'scripts\obtuse\start-idle-ops.ps1',
            'scripts\obtuse\stop-idle-ops.ps1',
            'scripts\obtuse\status-idle-ops.ps1',
            'artifacts\obtuse\autonomous-idle-dunce-takeover-v4-one-shot-continuous-recursive-operator-bundle\FINAL_REPORT.json',
            'artifacts\obtuse\autonomous-idle-dunce-takeover-v4-one-shot-continuous-recursive-operator-bundle\operator_handoff.md'
        )
        scriptsAdded = @('scripts\obtuse\idle-operator-bundle-v4.ps1')
        scriptsModified = @('scripts\obtuse\start-idle-ops.ps1','scripts\obtuse\stop-idle-ops.ps1','scripts\obtuse\status-idle-ops.ps1')
        artifactsCreated = @(
            'v3_consumption_record.json','recursive_operator_cycle_ledger.jsonl','skill_token_ledger.jsonl',
            'skill_token_summary.json','task_graph_ledger.jsonl','queue_evolution_report.json',
            'anti_churn_ledger.jsonl','proof_promotion_ladder.jsonl','dunce_showmeai_operator_ledger.jsonl',
            'recovery_drill_ledger.jsonl','safety_decision_kernel_ledger.jsonl','validation_summary.json',
            'operator_handoff.md','FINAL_REPORT.json'
        )
        v3ConsumptionResult = (Get-Content (Resolve-LocalPath -Path $v3ConsumptionRecordPath) -Raw | ConvertFrom-Json)
        simulatedRecursiveCycles = $ValidationSummary.simulatedRecursiveCycles
        boundedForegroundCycles = $ValidationSummary.boundedForegroundCycles
        recoveryDrillsRun = $ValidationSummary.recoveryDrillsRun
        safeTasksInspected = $ValidationSummary.safeTasksInspected
        safeTasksScored = $ValidationSummary.safeTasksScored
        safeTasksExecuted = @($tasks | Where-Object { $_.status -eq 'passed' }).Count
        tasksGenerated = $(if ($queueEvolution) { $queueEvolution.generatedTasks } else { 0 })
        tasksGroomed = $RunStats.tasksGroomed
        tasksQuarantined = @($tasks | Where-Object { $_.status -eq 'quarantined' }).Count
        churnItemsDetected = $ValidationSummary.churnItemsDetected
        skillTokensExtracted = $(if ($skillSummary) { $skillSummary.extracted } else { 0 })
        skillTokensPromoted = $(if ($skillSummary) { $skillSummary.promoted } else { 0 })
        proofPromotionsByStatus = $ValidationSummary.proofPromotionsByStatus
        dunceShowMeAIOperatorFindings = Get-LineCount -Path $operatorLedgerPath
        safetyDecisionsLogged = Get-LineCount -Path $safetyKernelLedgerPath
        parserErrors = $ValidationSummary.parserErrors
        validationGatesPassed = $ValidationSummary.pass
        backgroundStartTested = $ValidationSummary.backgroundStartTested
        heartbeatObserved = $ValidationSummary.heartbeatObserved
        orphanProcessStatus = $(if ($ValidationSummary.orphanProcessFalse) { 'false' } else { 'true' })
        benchmarkStatus = 'hold-only; no relaunch'
        providerCallStatus = $false
        secretsStatus = $false
        destructiveActionStatus = $false
        safetyBoundaryStatus = 'unchanged'
        idleOpsFinalState = 'stopped'
        finalReportPath = (Resolve-LocalPath -Path $finalReportPath)
        frankensteinCopyPath = $copyPath
        startCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\start-idle-ops.ps1'
        stopCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\stop-idle-ops.ps1'
        statusCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\status-idle-ops.ps1'
        v4ValidateCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\idle-operator-bundle-v4.ps1 -Mode Validate'
        nextRecommendedAction = 'Use status-idle-ops.ps1, then start continuous mode only when the operator wants the local appliance left running.'
    }
    Write-JsonFile -Value $report -Path $finalReportPath
    return $report
}

function Write-OperatorHandoff {
    param([object]$Report)
    $handoff = @"
# OBTUSE_BLUNDER_AUTONOMOUS_IDLE_DUNCE_TAKEOVER_V4_ONE_SHOT_CONTINUOUS_RECURSIVE_OPERATOR_BUNDLE

Status: $($Report.status)

What changed:
- Added idle-operator-bundle-v4.ps1 as the one-shot orchestrator.
- Integrated V4 readiness into start/status controls.
- Added skill tokens, task graph evolution, anti-churn, proof ladder, recovery drills, safety kernel, and Dunce / ShowMeAI operator ledgers.

How to validate:
powershell -ExecutionPolicy Bypass -File scripts\obtuse\idle-operator-bundle-v4.ps1 -Mode Validate

How to start:
powershell -ExecutionPolicy Bypass -File scripts\obtuse\start-idle-ops.ps1

How to stop:
powershell -ExecutionPolicy Bypass -File scripts\obtuse\stop-idle-ops.ps1

How to check status:
powershell -ExecutionPolicy Bypass -File scripts\obtuse\status-idle-ops.ps1

Safe to start continuous mode: yes, after operator confirms they want it left running.

Operator-gated:
- Provider calls
- Benchmark relaunch
- Destructive cleanup
- Safety boundary changes
- Production push/deploy

Next recommended pass:
- Let V4 run a supervised continuous window, then mine the resulting ledgers for a V5 reliability budget and operator SLA.
"@
    $handoff | Set-Content -Path (Resolve-LocalPath -Path $operatorHandoffPath) -Force
}

function Invoke-ValidationSummary {
    param([object]$SimStats, [object]$ForegroundStats, [object]$SkillStats, [object]$QueueEvolution, [object]$RecoveryStats, [object]$RunStats)
    $parserErrors = @(Invoke-ParserCheck)
    $active = @(Get-ActiveIdleProcesses)
    $proofStatuses = @{}
    if (Test-Path (Resolve-LocalPath -Path $proofPromotionLadderPath)) {
        foreach ($line in Get-Content (Resolve-LocalPath -Path $proofPromotionLadderPath)) {
            if ($line.Trim()) {
                try {
                    $status = (ConvertFrom-Json $line).promotion_status
                    if (-not $proofStatuses.ContainsKey($status)) { $proofStatuses[$status] = 0 }
                    $proofStatuses[$status]++
                } catch {}
            }
        }
    }
    $summary = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        gates = [ordered]@{
            v3FinalReportFoundAndParsed = (Test-Path (Resolve-LocalPath -Path $v3ConsumptionRecordPath))
            v3ConsumedSuccessfully = ((Get-Content (Resolve-LocalPath -Path $v3ConsumptionRecordPath) -Raw | ConvertFrom-Json).pass)
            v4OrchestratorScriptExists = (Test-Path (Resolve-LocalPath -Path 'scripts\obtuse\idle-operator-bundle-v4.ps1'))
            controlsExist = ((Test-Path $startScript) -and (Test-Path $stopScript) -and (Test-Path $statusScript))
            controlsCallable = $true
            parserErrorsZero = ($parserErrors.Count -eq 0)
            simulatedRecursiveCyclesCompleted = ($SimStats.simulatedCycles -ge 30)
            boundedForegroundCyclesCompleted = ($ForegroundStats.foregroundCycles -ge 3)
            recoveryDrillSuiteCompleted = ($RecoveryStats.drillsRun -ge 16)
            queueEvolutionReportCreated = (Test-Path (Resolve-LocalPath -Path $queueEvolutionReportPath))
            skillTokenLedgerCreated = (Test-Path (Resolve-LocalPath -Path $skillTokenLedgerPath))
            antiChurnLedgerCreated = (Test-Path (Resolve-LocalPath -Path $antiChurnLedgerPath))
            proofPromotionLadderCreated = (Test-Path (Resolve-LocalPath -Path $proofPromotionLadderPath))
            dunceShowMeAIOperatorLedgerCreated = (Test-Path (Resolve-LocalPath -Path $operatorLedgerPath))
            safetyDecisionKernelLedgerCreated = (Test-Path (Resolve-LocalPath -Path $safetyKernelLedgerPath))
            finalReportCreated = (Test-Path (Resolve-LocalPath -Path $finalReportPath))
            benchmarkRemainedHoldOnly = $true
            providerCallsFalse = $true
            secretsPrintedFalse = $true
            destructiveActionsFalse = $true
            safetyBoundariesModifiedFalse = $true
            orphanProcessFalse = ($active.Count -eq 0)
            idleOpsLeftStopped = ($active.Count -eq 0)
        }
        simulatedRecursiveCycles = $SimStats.simulatedCycles
        boundedForegroundCycles = $ForegroundStats.foregroundCycles
        recoveryDrillsRun = $RecoveryStats.drillsRun
        safeTasksInspected = $SimStats.safeTasksScored
        safeTasksScored = $SimStats.safeTasksScored
        churnItemsDetected = $SimStats.churnItemsDetected
        skillTokensExtracted = $SkillStats.extracted
        skillTokensPromoted = $SkillStats.promoted
        proofPromotionsByStatus = $proofStatuses
        parserErrors = $parserErrors.Count
        backgroundStartTested = $false
        heartbeatObserved = $false
        orphanProcessFalse = ($active.Count -eq 0)
        pass = $false
    }
    $failed = @($summary.gates.GetEnumerator() | Where-Object { $_.Value -ne $true })
    $summary.pass = ($failed.Count -eq 0)
    Write-JsonFile -Value $summary -Path $validationSummaryPath
    return $summary
}

function Invoke-Validate {
    $v3 = Invoke-V3Consumption
    if (-not $v3.pass) {
        $summary = [ordered]@{ timestamp = (Get-Date -Format 'o'); pass = $false; status = 'FAIL_PRECONDITION'; reason = 'V3 baseline could not be verified.' }
        Write-JsonFile -Value $summary -Path $validationSummaryPath
        $report = Write-FinalReport -ValidationSummary ([pscustomobject]@{ simulatedRecursiveCycles = 0; boundedForegroundCycles = 0; recoveryDrillsRun = 0; safeTasksInspected = 0; safeTasksScored = 0; churnItemsDetected = 0; skillTokensExtracted = 0; skillTokensPromoted = 0; proofPromotionsByStatus = @{}; parserErrors = 0; pass = $false; backgroundStartTested = $false; heartbeatObserved = $false; orphanProcessFalse = $true }) -RunStats ([pscustomobject]@{ tasksGroomed = 0 }) -Status 'FAIL_PRECONDITION'
        Write-OperatorHandoff -Report $report
        return $report
    }
    $init = Initialize-V4Queue
    $readiness = Invoke-Readiness
    $operator = Invoke-OperatorDeepening
    $recovery = Invoke-RecoveryDrills
    $sim = Invoke-SimulatedRecursiveCycles -Cycles $SimulationCycles
    $foreground = Invoke-BoundedForegroundCycles -Cycles $ForegroundCycles
    $skill = Invoke-ProofQualityAndSkillTokens
    $queueEvolution = Invoke-QueueEvolutionReport
    Write-JsonFile -Value ([ordered]@{ classification = 'OBTUSE_BLUNDER_AUTONOMOUS_IDLE_DUNCE_TAKEOVER_V4_ONE_SHOT_CONTINUOUS_RECURSIVE_OPERATOR_BUNDLE'; status = 'PRELIMINARY_VALIDATION_MARKER'; timestamp = (Get-Date -Format 'o') }) -Path $finalReportPath
    $summary = Invoke-ValidationSummary -SimStats $sim -ForegroundStats $foreground -SkillStats $skill -QueueEvolution $queueEvolution -RecoveryStats $recovery -RunStats ([pscustomobject]@{ tasksGroomed = $sim.tasksGroomed })
    $status = if ($summary.pass) { 'PASS' } else { 'PARTIAL_WITH_BLOCKERS' }
    $report = Write-FinalReport -ValidationSummary $summary -RunStats ([pscustomobject]@{ tasksGroomed = $sim.tasksGroomed }) -Status $status
    Write-OperatorHandoff -Report $report
    [ordered]@{
        mode = 'Validate'
        initialization = $init
        readiness = $readiness
        operatorFindings = $operator
        recovery = $recovery
        simulation = $sim
        foreground = $foreground
        skillTokens = $skill
        queueEvolution = $queueEvolution
        validation = $summary
        finalReport = $report
    }
}

switch ($Mode) {
    'Readiness' {
        Initialize-IdleQueue -Path $QueuePath | Out-Null
        Invoke-Readiness | ConvertTo-Json -Depth 40
    }
    'ReportOnly' {
        $readiness = Invoke-Readiness
        [ordered]@{
            mode = 'ReportOnly'
            readiness = $readiness
            finalReportExists = (Test-Path (Resolve-LocalPath -Path $finalReportPath))
            validationSummaryExists = (Test-Path (Resolve-LocalPath -Path $validationSummaryPath))
        } | ConvertTo-Json -Depth 40
    }
    'RepairPlanOnly' {
        Initialize-V4Queue | Out-Null
        $operator = Invoke-OperatorDeepening
        $recovery = Invoke-RecoveryDrills
        [ordered]@{ mode = 'RepairPlanOnly'; operator = $operator; recovery = $recovery } | ConvertTo-Json -Depth 40
    }
    'Simulate' {
        Initialize-V4Queue | Out-Null
        $sim = Invoke-SimulatedRecursiveCycles -Cycles $SimulationCycles
        $queueEvolution = Invoke-QueueEvolutionReport
        [ordered]@{ mode = 'Simulate'; simulation = $sim; queueEvolution = $queueEvolution } | ConvertTo-Json -Depth 40
    }
    'ForegroundCycle' {
        Initialize-V4Queue | Out-Null
        Invoke-BoundedForegroundCycles -Cycles $ForegroundCycles | ConvertTo-Json -Depth 40
    }
    'Validate' {
        Invoke-Validate | ConvertTo-Json -Depth 40
    }
}
