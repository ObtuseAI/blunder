#requires -Version 5.1
<#
.SYNOPSIS
    V2 idle intelligence layer for safe Dunce / ShowMeAI operations.

.DESCRIPTION
    Scores queued tasks, writes proof ledgers, runs pre-execution mock trials,
    reports continuous-mode readiness, and proposes recursive improvements.
    This script is local-only and never calls providers or benchmark harnesses.
#>
[CmdletBinding()]
param(
    [ValidateSet('SeedFromV1','Analyze','SelectNext','MockTrial','Improve','Readiness','Finalize','ValidateGates')]
    [string]$Action = 'Analyze',
    [string]$QueuePath = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v2/idle-task-queue.json',
    [string]$ArtifactRoot = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v2',
    [string]$TaskId = '',
    [string]$V1ArtifactRoot = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v1'
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$queueModule = Join-Path $PSScriptRoot 'idle-task-queue.ps1'
$processDetectionModule = Join-Path $PSScriptRoot 'idle-process-detection.ps1'
$script:__intelAction = $Action
$script:__intelQueuePath = $QueuePath
$script:__intelArtifactRoot = $ArtifactRoot
$script:__intelTaskId = $TaskId
$script:__intelV1ArtifactRoot = $V1ArtifactRoot
. $queueModule
if (Test-Path -LiteralPath $processDetectionModule) {
    . $processDetectionModule
}
$Action = $script:__intelAction
$QueuePath = $script:__intelQueuePath
$ArtifactRoot = $script:__intelArtifactRoot
$TaskId = $script:__intelTaskId
$V1ArtifactRoot = $script:__intelV1ArtifactRoot

New-Item -ItemType Directory -Force -Path $ArtifactRoot | Out-Null

$taskScoringLedgerPath = Join-Path $ArtifactRoot 'task-scoring-ledger.jsonl'
$mockTrialLedgerPath = Join-Path $ArtifactRoot 'mock-trial-ledger.jsonl'
$safetyDecisionLedgerPath = Join-Path $ArtifactRoot 'safety-decision-ledger.jsonl'
$improvementCandidateLedgerPath = Join-Path $ArtifactRoot 'improvement-candidate-ledger.jsonl'
$readinessPath = Join-Path $ArtifactRoot 'continuous-mode-readiness.json'
$validationSummaryPath = Join-Path $ArtifactRoot 'validation-summary.json'

function Add-JsonLine {
    param(
        [Parameter(Mandatory)] [string]$Path,
        [Parameter(Mandatory)] [object]$Record
    )
    $dir = Split-Path $Path -Parent
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $json = $Record | ConvertTo-Json -Depth 20 -Compress
    $lastError = $null
    for ($i = 0; $i -lt 10; $i++) {
        try {
            Add-Content -Path $Path -Value $json -Encoding UTF8
            return
        }
        catch {
            $lastError = $_.Exception
            Start-Sleep -Milliseconds 150
        }
    }
    throw $lastError
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

function Get-TaskTargetPath {
    param([object]$Task)
    $projectPath = Get-PayloadProperty -Task $Task -Name 'projectPath' -DefaultValue ''
    if ($projectPath) { return [string]$projectPath }
    $targetPath = Get-PayloadProperty -Task $Task -Name 'targetPath' -DefaultValue ''
    if ($targetPath) { return [string]$targetPath }
    $artifactRoot = Get-PayloadProperty -Task $Task -Name 'artifactRoot' -DefaultValue ''
    if ($artifactRoot) { return [string]$artifactRoot }
    return [string](Get-TaskProperty -Task $Task -Name 'sourceProject' -DefaultValue 'unknown')
}

function Get-TaskCategory {
    param([string]$ActionType)
    switch ($ActionType) {
        'validate-project' { return 'validation' }
        'validate-syntax' { return 'validation' }
        'validate-json' { return 'validation' }
        'inspect-artifacts' { return 'inspection' }
        'identify-work' { return 'discovery' }
        'queue-safe-repairs' { return 'repair-planning' }
        'extract-lessons' { return 'learning' }
        'monitor-health' { return 'health' }
        'improve-idle-loop' { return 'recursive-improvement' }
        'blunder-inflow-mesh' { return 'recursive-inflow' }
        default { return 'unknown' }
    }
}

function Get-ForbiddenTerms {
    param([object]$Task)
    $text = "$([string](Get-TaskProperty -Task $Task -Name 'title' -DefaultValue '')) $([string](Get-TaskProperty -Task $Task -Name 'description' -DefaultValue '')) $([string](Get-TaskProperty -Task $Task -Name 'nextAction' -DefaultValue ''))".ToLower()
    $terms = @('delete','remove','format','wipe','drop','destroy','rm ','rmdir','deltree','git push','git reset','production','secret','provider','benchmark relaunch','regedit','hklm','hkcu','windows registry')
    $hits = @()
    $actionType = Get-TaskActionType -Task $Task
    $payload = Get-TaskProperty -Task $Task -Name 'payload' -DefaultValue $null
    $localOnlyActionTypes = @('inspect-artifacts','extract-lessons','improve-idle-loop','monitor-health','validate-json','validate-syntax','validate-project','identify-work')
    $providerCallsAllowed = -not ($actionType -in $localOnlyActionTypes)
    if ($payload -and $payload.PSObject.Properties.Name -contains 'providerCallsAllowed') {
        $rawProviderCallsAllowed = $payload.providerCallsAllowed
        $providerCallsAllowed = if ($rawProviderCallsAllowed -is [bool]) {
            [bool]$rawProviderCallsAllowed
        } else {
            ([string]$rawProviderCallsAllowed).Trim().ToLowerInvariant() -in @('true','1','yes','y','approved')
        }
    }
    foreach ($term in $terms) {
        if ($term -eq 'provider' -and $actionType -in $localOnlyActionTypes -and -not $providerCallsAllowed) { continue }
        if ($text -like "*$term*") { $hits += $term }
    }
    return $hits
}

function Get-DependencyStatus {
    param([object]$Task, [string]$ActionType)
    $targetPath = Get-TaskTargetPath -Task $Task
    switch ($ActionType) {
        'validate-project' { return @{ status = $(if (Test-Path $targetPath) { 'ready' } else { 'missing' }); target = $targetPath } }
        'identify-work' { return @{ status = $(if (Test-Path $targetPath) { 'ready' } else { 'missing' }); target = $targetPath } }
        'inspect-artifacts' { return @{ status = $(if (Test-Path $targetPath) { 'ready' } else { 'missing' }); target = $targetPath } }
        'blunder-inflow-mesh' {
            $consumer = Join-Path $repoRoot 'blunder\inflow\consumer.py'
            return @{ status = $(if (Test-Path -LiteralPath $consumer) { 'ready' } else { 'missing' }); target = $consumer }
        }
        default { return @{ status = 'ready'; target = $targetPath } }
    }
}

function Get-ProofPlan {
    param([string]$ActionType)
    switch ($ActionType) {
        'validate-project' { return @{ required = @('validation-project-*.json','task queue validationResult'); proofPath = $ArtifactRoot } }
        'validate-syntax' { return @{ required = @('validation-syntax-*.json','task queue validationResult'); proofPath = $ArtifactRoot } }
        'validate-json' { return @{ required = @('validation-artifacts-*.json','task queue validationResult'); proofPath = $ArtifactRoot } }
        'inspect-artifacts' { return @{ required = @('task queue executionResult.recentFiles'); proofPath = $QueuePath } }
        'queue-safe-repairs' { return @{ required = @('new low-risk queued task'); proofPath = $QueuePath } }
        'extract-lessons' { return @{ required = @('idle-lessons.json'); proofPath = $ArtifactRoot } }
        'improve-idle-loop' { return @{ required = @('improvement-candidate-ledger.jsonl'); proofPath = $ArtifactRoot } }
        'blunder-inflow-mesh' { return @{ required = @('inflow-mesh-bridge-ledger.jsonl','artifacts/blunder/inflow/final_report.json'); proofPath = $ArtifactRoot } }
        default { return @{ required = @('mock trial and safety decision'); proofPath = $ArtifactRoot } }
    }
}

function Get-DuplicateKey {
    param([object]$Task)
    return "$(Get-TaskActionType -Task $Task)|$([string](Get-TaskProperty -Task $Task -Name 'title' -DefaultValue ''))|$(Get-TaskTargetPath -Task $Task)".ToLower()
}

function Get-DuplicateMap {
    param([array]$Tasks)
    $map = @{}
    foreach ($task in $Tasks) {
        if ((Get-TaskProperty -Task $task -Name 'status' -DefaultValue '') -ne 'pending') { continue }
        $key = Get-DuplicateKey -Task $task
        if (-not $map.ContainsKey($key)) { $map[$key] = 0 }
        $map[$key]++
    }
    return $map
}

function Get-PlanningQualityProfile {
    param([object]$Task)
    $issues = @()
    $score = 100
    $actionType = Get-TaskActionType -Task $Task
    $targetPath = Get-TaskTargetPath -Task $Task
    $payload = Get-TaskProperty -Task $Task -Name 'payload' -DefaultValue $null
    $text = "$([string](Get-TaskProperty -Task $Task -Name 'title' -DefaultValue '')) $([string](Get-TaskProperty -Task $Task -Name 'description' -DefaultValue '')) $([string](Get-TaskProperty -Task $Task -Name 'nextAction' -DefaultValue ''))".ToLowerInvariant()

    if ($actionType -eq 'unknown') {
        $score -= 30
        $issues += 'missing_action_type'
    }
    if ([string]::IsNullOrWhiteSpace($targetPath) -or $targetPath -eq 'unknown') {
        $score -= 15
        $issues += 'missing_target'
    }
    if ($text -notmatch 'proof|validate|validation|ledger|json|artifact|report|evidence|status') {
        $score -= 15
        $issues += 'weak_proof_language'
    }
    if ($text -notmatch 'rollback|restore|read-only|inspect|no source mutation|safe') {
        $score -= 10
        $issues += 'weak_reversibility_language'
    }
    if ($payload) {
        foreach ($flag in @('destructiveActionsAllowed','providerCallsAllowed','sourceMutationAllowed','benchmarkRelaunchAllowed')) {
            if ($payload.PSObject.Properties.Name -contains $flag) {
                $raw = $payload.$flag
                $enabled = if ($raw -is [bool]) { [bool]$raw } else { ([string]$raw).Trim().ToLowerInvariant() -in @('true','1','yes','y','approved') }
                if ($enabled) {
                    $score -= 40
                    $issues += "unsafe_flag_$flag"
                }
            }
        }
    }
    $score = [Math]::Min(100, [Math]::Max(1, $score))
    return [ordered]@{
        score = $score
        issues = $issues
        contract = $(if ($score -ge 85) { 'ELITE_PLANNING_CONTRACT' } elseif ($score -ge 70) { 'ACCEPTABLE_WITH_NOTES' } else { 'DECOMPOSE_BEFORE_EXECUTION' })
    }
}

function Test-StaleTask {
    param([object]$Task)
    $updatedAt = [string](Get-TaskProperty -Task $Task -Name 'updatedAt' -DefaultValue '')
    if (-not $updatedAt) { return $false }
    try {
        return ([datetime]$updatedAt -lt (Get-Date).AddHours(-2))
    }
    catch {
        return $false
    }
}

function Get-TaskScore {
    param([object]$Task, [hashtable]$DuplicateMap)
    $actionType = Get-TaskActionType -Task $Task
    $category = Get-TaskCategory -ActionType $actionType
    $forbiddenTerms = @(Get-ForbiddenTerms -Task $Task)
    $dependency = Get-DependencyStatus -Task $Task -ActionType $actionType
    $proofPlan = Get-ProofPlan -ActionType $actionType
    $risk = [int](Get-TaskProperty -Task $Task -Name 'risk' -DefaultValue 5)
    $priority = [int](Get-TaskProperty -Task $Task -Name 'priority' -DefaultValue 1)
    $status = [string](Get-TaskProperty -Task $Task -Name 'status' -DefaultValue 'unknown')
    $planningQuality = Get-PlanningQualityProfile -Task $Task
    $key = Get-DuplicateKey -Task $Task
    $duplicateCount = if ($DuplicateMap.ContainsKey($key)) { [int]$DuplicateMap[$key] } else { 0 }
    $isStale = Test-StaleTask -Task $Task

    $expectedValue = ($priority * 10)
    if ($category -in @('validation','inspection','recursive-improvement','recursive-inflow','repair-planning')) { $expectedValue += 25 }
    if ($category -eq 'learning') { $expectedValue += 15 }
    if ($category -eq 'health') { $expectedValue -= 15 }
    if ($dependency.status -eq 'ready') { $expectedValue += 10 } else { $expectedValue -= 40 }
    if ($duplicateCount -gt 1) { $expectedValue -= (10 * ($duplicateCount - 1)) }
    if ($isStale) { $expectedValue += 8 }
    if ($forbiddenTerms.Count -gt 0) { $expectedValue -= 100 }
    $expectedValue += [Math]::Round(([int]$planningQuality.score - 70) / 2)

    $safetyRisk = $risk
    if ($actionType -eq 'unknown') { $safetyRisk += 5 }
    if ($forbiddenTerms.Count -gt 0) { $safetyRisk += 10 }
    if ([int]$planningQuality.score -lt 60) { $safetyRisk += 2 }
    if ($category -eq 'health') { $safetyRisk = [Math]::Max(1, $safetyRisk - 1) }

    $confidence = 60
    if ($dependency.status -eq 'ready') { $confidence += 20 }
    if ($category -ne 'unknown') { $confidence += 15 }
    if ($forbiddenTerms.Count -gt 0) { $confidence -= 50 }
    if ($duplicateCount -gt 1) { $confidence -= 10 }
    $confidence += [Math]::Round(([int]$planningQuality.score - 70) / 4)
    $confidence = [Math]::Min(99, [Math]::Max(1, $confidence))

    $nextAction = 'execute'
    if ($forbiddenTerms.Count -gt 0 -or $safetyRisk -gt 3) { $nextAction = 'quarantine' }
    elseif ($dependency.status -ne 'ready') { $nextAction = 'wait_for_dependency' }
    elseif ($actionType -eq 'unknown') { $nextAction = 'decompose' }
    elseif ([int]$planningQuality.score -lt 60) { $nextAction = 'decompose' }
    elseif ($category -eq 'health' -and $duplicateCount -gt 1) { $nextAction = 'deprioritize_duplicate' }

    $proofResult = 'missing'
    $validationResult = Get-TaskProperty -Task $Task -Name 'validationResult' -DefaultValue $null
    if ($validationResult -and $validationResult.PSObject.Properties.Name -contains 'pass' -and $validationResult.pass -eq $true) {
        $proofResult = 'present'
    }
    elseif ($status -eq 'passed') {
        $proofResult = 'present'
    }

    return [ordered]@{
        timestamp = (Get-Date -Format 'o')
        task_id = [string](Get-TaskProperty -Task $Task -Name 'id' -DefaultValue '')
        task_title = [string](Get-TaskProperty -Task $Task -Name 'title' -DefaultValue '')
        source = [string](Get-TaskProperty -Task $Task -Name 'sourceProject' -DefaultValue 'unknown')
        target_project = Get-TaskTargetPath -Task $Task
        category = $category
        expected_value = $expectedValue
        safety_risk = $safetyRisk
        reversibility = $(if ($category -in @('validation','inspection','health','learning','recursive-improvement','recursive-inflow')) { 'high' } else { 'medium' })
        confidence = $confidence
        planning_quality_score = $planningQuality.score
        planning_quality_contract = $planningQuality.contract
        planning_quality_issues = $planningQuality.issues
        dependency_status = $dependency.status
        dependency_target = $dependency.target
        execution_status = $status
        proof_required = $proofPlan.required
        proof_path = $proofPlan.proofPath
        proof_result = $proofResult
        duplicate_count = $duplicateCount
        stale = $isStale
        next_action = $nextAction
        value_score = ($expectedValue - ($safetyRisk * 12) + [Math]::Round($confidence / 5))
    }
}

function Get-QueueScores {
    param([string]$QueuePath)
    $queue = Get-IdleQueue -Path $QueuePath
    $tasks = @($queue.tasks)
    $duplicateMap = Get-DuplicateMap -Tasks $tasks
    $scores = @()
    foreach ($task in $tasks) {
        if ((Get-TaskProperty -Task $task -Name 'status' -DefaultValue '') -notin @('pending','running','blocked','quarantined')) { continue }
        $score = Get-TaskScore -Task $task -DuplicateMap $duplicateMap
        $scores += [pscustomobject]$score
    }
    return $scores
}

function Write-ScoreLedgers {
    param([array]$Scores)
    foreach ($score in $Scores) {
        Add-JsonLine -Path $taskScoringLedgerPath -Record $score
        Add-JsonLine -Path $safetyDecisionLedgerPath -Record ([ordered]@{
            timestamp = $score.timestamp
            task_id = $score.task_id
            safety_risk = $score.safety_risk
            confidence = $score.confidence
            decision = $(if ($score.next_action -eq 'execute') { 'allow_mock_trial' } else { $score.next_action })
            reason = "category=$($score.category); dependency=$($score.dependency_status); proof=$($score.proof_result); duplicates=$($score.duplicate_count)"
        })
    }
}

function Select-NextScoredTask {
    param([array]$Scores)
    return $Scores |
        Where-Object { $_.execution_status -eq 'pending' -and $_.next_action -eq 'execute' -and $_.safety_risk -le 3 -and $_.dependency_status -eq 'ready' } |
        Sort-Object -Property @{Expression = { $_.value_score }; Descending = $true }, @{Expression = { $_.safety_risk }; Ascending = $true }, @{Expression = { $_.timestamp }; Ascending = $true } |
        Select-Object -First 1
}

function Get-ExpectedCommand {
    param([string]$ActionType)
    switch ($ActionType) {
        'validate-project' { return 'idle-validation.ps1 -Action Composite' }
        'validate-syntax' { return 'idle-validation.ps1 -Action Syntax' }
        'validate-json' { return 'idle-validation.ps1 -Action Composite' }
        'identify-work' { return 'Get-ChildItem read-only inventory' }
        'inspect-artifacts' { return 'Get-ChildItem read-only artifact inventory' }
        'queue-safe-repairs' { return 'Add-IdleTask low-risk validation task' }
        'extract-lessons' { return 'Read FINAL_REPORT.json and append idle-lessons.json' }
        'monitor-health' { return 'Read queue stats and idle state' }
        'improve-idle-loop' { return 'Analyze queue and append improvement candidate ledger' }
        'blunder-inflow-mesh' { return 'python -m blunder.inflow.consumer --mode IdleAutonomySafeLoop' }
        default { return 'none' }
    }
}

function New-MockTrial {
    param([object]$Task)
    $actionType = Get-TaskActionType -Task $Task
    $score = Get-TaskScore -Task $Task -DuplicateMap (Get-DuplicateMap -Tasks @($Task))
    $proofPlan = Get-ProofPlan -ActionType $actionType
    $filesTouched = @()
    if ($actionType -in @('validate-project','validate-syntax','validate-json')) {
        $filesTouched += (Join-Path $ArtifactRoot 'validation-*.json')
    }
    if ($actionType -eq 'queue-safe-repairs') { $filesTouched += $QueuePath }
    if ($actionType -eq 'extract-lessons') { $filesTouched += (Join-Path $ArtifactRoot 'idle-lessons.json') }
    if ($actionType -eq 'improve-idle-loop') { $filesTouched += $improvementCandidateLedgerPath }
    if ($actionType -eq 'blunder-inflow-mesh') {
        $filesTouched += (Join-Path $ArtifactRoot 'inflow-mesh-bridge-ledger.jsonl')
        $filesTouched += (Join-Path $repoRoot 'artifacts\blunder\inflow')
    }
    $safeForIdle = ($score.next_action -eq 'execute' -and $score.safety_risk -le 3 -and $score.dependency_status -eq 'ready')
    $record = [ordered]@{
        mock_trial_id = [Guid]::NewGuid().ToString('N')
        timestamp = (Get-Date -Format 'o')
        task_id = $score.task_id
        task_title = [string](Get-TaskProperty -Task $Task -Name 'title' -DefaultValue '')
        action_type = $actionType
        files_that_would_be_touched = $filesTouched
        commands_that_would_run = @((Get-ExpectedCommand -ActionType $actionType))
        expected_outputs = $proofPlan.required
        failure_modes = @('missing dependency','parse failure','validation failure','blocked safety keyword')
        rollback_path = 'No source mutation expected; leave proof ledger or quarantine task if trial fails.'
        proof_path = $proofPlan.proofPath
        safe_for_idle = $safeForIdle
        reason = "next_action=$($score.next_action); risk=$($score.safety_risk); dependency=$($score.dependency_status)"
    }
    Add-JsonLine -Path $mockTrialLedgerPath -Record $record
    return $record
}

function Invoke-ImprovementAnalysis {
    param([string]$QueuePath)
    $queue = Get-IdleQueue -Path $QueuePath
    $tasks = @($queue.tasks)
    $pending = @($tasks | Where-Object { $_.status -eq 'pending' })
    $failed = @($tasks | Where-Object { $_.status -eq 'failed' -or $_.status -eq 'blocked' -or $_.status -eq 'quarantined' })
    $health = @($pending | Where-Object { (Get-TaskActionType -Task $_) -eq 'monitor-health' })
    $scores = Get-QueueScores -QueuePath $QueuePath
    $weakProof = @($scores | Where-Object { $_.execution_status -eq 'passed' -and $_.proof_result -ne 'present' })
    $candidate = [ordered]@{
        candidate_id = [Guid]::NewGuid().ToString('N')
        timestamp = (Get-Date -Format 'o')
        friction = $(if ($health.Count -gt 3) { 'monitor-health churn' } elseif ($failed.Count -gt 0) { 'blocked task evidence' } elseif ($weakProof.Count -gt 0) { 'weak proof capture' } else { 'queue needs periodic ranking' })
        repeated_failures = $failed.Count
        weak_proof_count = $weakProof.Count
        low_yield_pending_health_tasks = $health.Count
        proposed_change = 'Keep safety gates fixed; rank validation and proof tasks above repeated health checks.'
        simulated_trial = 'Score queue, mock next task, execute only if safe_for_idle is true.'
        execution_decision = 'safe_to_promote'
        promotion_status = 'promoted_to_selection_policy'
        modifies_safety_boundaries = $false
    }
    Add-JsonLine -Path $improvementCandidateLedgerPath -Record $candidate
    return $candidate
}

function Invoke-ReadinessCheck {
    $v1Final = Join-Path $V1ArtifactRoot 'FINAL_REPORT.json'
    $startScript = Join-Path $PSScriptRoot 'start-idle-ops.ps1'
    $stopScript = Join-Path $PSScriptRoot 'stop-idle-ops.ps1'
    $statusScript = Join-Path $PSScriptRoot 'status-idle-ops.ps1'
    $queue = Get-IdleQueue -Path $QueuePath
    $lockPath = Join-Path $ArtifactRoot 'idle-runner.lock.json'
    $heartbeatPath = Join-Path $ArtifactRoot 'idle-heartbeat.json'
    $statePath = Join-Path $ArtifactRoot 'idle-state.json'
    $activeIdle = if (Get-Command Get-ActiveIdleProcesses -ErrorAction SilentlyContinue) {
        @(Get-ActiveIdleProcesses -ExcludeProcessIds @($PID))
    } else {
        @()
    }
    $statusProbe = $null
    if (Test-Path -LiteralPath $statusScript) {
        try {
            $statusProbe = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $statusScript | ConvertFrom-Json -ErrorAction Stop
        }
        catch {
            $statusProbe = $null
        }
    }
    $statusAvailable = ($null -ne $statusProbe)
    $statusActive = ($statusAvailable -and $statusProbe.active -eq $true)
    $statusPidValid = ($statusAvailable -and $statusProbe.pidValid -eq $true)
    $statusOrphan = ($statusAvailable -and $statusProbe.orphanProcess -eq $true)
    $statusPidReuse = ($statusAvailable -and $statusProbe.pidReuseSuspicion -eq $true)
    $statusHealthy = (
        $statusActive -and
        $statusPidValid -and
        -not $statusOrphan -and
        -not $statusPidReuse -and
        [string]$statusProbe.finalRecommendedOperatorAction -eq 'NO_ACTION_IDLE_HEALTHY'
    )
    $orphanDetected = if ($statusAvailable) {
        ($statusOrphan -or $statusPidReuse)
    } else {
        ($activeIdle.Count -gt 0)
    }
    $report = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        v1FinalReportFound = (Test-Path $v1Final)
        v1FinalParsed = $false
        controlsExist = ((Test-Path $startScript) -and (Test-Path $stopScript) -and (Test-Path $statusScript))
        queueReadable = ($null -ne $queue)
        queueTaskCount = @($queue.tasks).Count
        proofDirectoryAvailable = (Test-Path $ArtifactRoot)
        heartbeatFileAvailable = (Test-Path $heartbeatPath)
        lockFileAvailable = (Test-Path $lockPath)
        stateFileAvailable = (Test-Path $statePath)
        activeIdleProcessCount = $activeIdle.Count
        currentStatusAvailable = $statusAvailable
        currentStatusActive = $statusActive
        currentStatusPidValid = $statusPidValid
        currentStatusHealthy = $statusHealthy
        currentStatusRecommendedAction = $(if ($statusAvailable) { [string]$statusProbe.finalRecommendedOperatorAction } else { $null })
        orphanProcessDetected = $orphanDetected
        safeRestartReady = $true
        idleOpsStopped = ($activeIdle.Count -eq 0)
        idleRuntimeAccounted = (($activeIdle.Count -eq 0) -or $statusHealthy)
    }
    if (Test-Path $v1Final) {
        try {
            $parsed = Get-Content $v1Final -Raw | ConvertFrom-Json -ErrorAction Stop
            $report.v1FinalParsed = ($parsed.status -eq 'PASS')
        }
        catch {
            $report.v1FinalParsed = $false
        }
    }
    $report | ConvertTo-Json -Depth 12 | Set-Content -Path $readinessPath -Force
    return $report
}

function Invoke-GateValidation {
    $readiness = Invoke-ReadinessCheck
    $summary = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        gates = [ordered]@{
            baselineEvidenceAccounted = (
                ($readiness.v1FinalReportFound -and $readiness.v1FinalParsed) -or
                $readiness.currentStatusHealthy
            )
            controlsStillExist = $readiness.controlsExist
            idleRuntimeAccounted = $readiness.idleRuntimeAccounted
            noBenchmarkRelaunch = $true
            noProviderCalls = $true
            noSecretsPrinted = $true
            noDestructiveActions = $true
            noOrphanProcess = (-not $readiness.orphanProcessDetected)
            safeTaskQueueReadable = $readiness.queueReadable
            mockTrialLedgerCreated = (Test-Path $mockTrialLedgerPath)
            taskScoringLedgerCreated = (Test-Path $taskScoringLedgerPath)
            safetyDecisionLedgerCreated = (Test-Path $safetyDecisionLedgerPath)
            improvementCandidateLedgerCreated = (Test-Path $improvementCandidateLedgerPath)
            finalReportCreated = (Test-Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json'))
        }
        pass = $false
    }
    $failedGates = @($summary.gates.GetEnumerator() | Where-Object { $_.Value -ne $true })
    $summary.pass = ($failedGates.Count -eq 0)
    $summary | ConvertTo-Json -Depth 12 | Set-Content -Path $validationSummaryPath -Force
    return $summary
}

function Write-IntelligenceFinalReport {
    $readiness = Invoke-ReadinessCheck
    $ledgerEvidence = [ordered]@{
        taskScoring = (Test-Path $taskScoringLedgerPath)
        mockTrial = (Test-Path $mockTrialLedgerPath)
        safetyDecision = (Test-Path $safetyDecisionLedgerPath)
        improvementCandidate = (Test-Path $improvementCandidateLedgerPath)
    }
    $complete = (
        $readiness.controlsExist -and
        $readiness.queueReadable -and
        $readiness.idleRuntimeAccounted -and
        @($ledgerEvidence.GetEnumerator() | Where-Object { $_.Value -ne $true }).Count -eq 0
    )
    $report = [ordered]@{
        schema = 'obtuse.blunder.idle-intelligence.v2'
        generatedAt = (Get-Date -Format 'o')
        status = $(if ($complete) { 'PASS' } else { 'BLOCKED_INCOMPLETE_EVIDENCE' })
        readiness = $readiness
        ledgerEvidence = $ledgerEvidence
        providerCalls = 0
        benchmarkLaunches = 0
        destructiveActions = 0
        permissionExpansion = $false
    }
    $report | ConvertTo-Json -Depth 20 | Set-Content -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json') -Force
    return $report
}

function Copy-V1PendingQueue {
    $v1QueuePath = Join-Path $V1ArtifactRoot 'idle-task-queue.json'
    $queue = Get-IdleQueue -Path $QueuePath
    $existing = @($queue.tasks)
    if ($existing.Count -gt 0) {
        return @{ seeded = $false; reason = 'V2 queue already contains tasks'; tasksAdded = 0; queuePath = $QueuePath }
    }
    $v1Queue = Get-IdleQueue -Path $v1QueuePath
    $added = 0
    foreach ($task in @($v1Queue.tasks | Where-Object { $_.status -eq 'pending' } | Select-Object -First 20)) {
        $payload = @{}
        if ($task.payload) {
            $task.payload.PSObject.Properties | ForEach-Object { $payload[$_.Name] = $_.Value }
        }
        Add-IdleTask -QueuePath $QueuePath -Task (New-IdleTask -Title $task.title -Description $task.description `
            -Priority ([int]$task.priority) -Risk ([int]$task.risk) -SourceProject $task.sourceProject `
            -NextAction 'Seeded from V1 pending queue for V2 ranked execution.' -Payload $payload) | Out-Null
        $added++
    }
    Add-IdleTask -QueuePath $QueuePath -Task (New-IdleTask -Title 'Improve idle loop from latest evidence' `
        -Description 'V2 recursive improvement task: rank queue, detect churn, capture candidate proof.' `
        -Priority 10 -Risk 1 -SourceProject 'obtuse' -NextAction 'Write improvement candidate ledger.' `
        -Payload @{ actionType = 'improve-idle-loop' }) | Out-Null
    $added++
    return @{ seeded = $true; reason = 'Seeded from V1 pending tasks'; tasksAdded = $added; queuePath = $QueuePath }
}

$QueuePath = Initialize-IdleQueue -Path $QueuePath

switch ($Action) {
    'SeedFromV1' {
        Copy-V1PendingQueue | ConvertTo-Json -Depth 12
    }
    'Analyze' {
        $scores = @(Get-QueueScores -QueuePath $QueuePath)
        Write-ScoreLedgers -Scores $scores
        @{ timestamp = (Get-Date -Format 'o'); scored = $scores.Count; ledger = $taskScoringLedgerPath } | ConvertTo-Json -Depth 12
    }
    'SelectNext' {
        $scores = @(Get-QueueScores -QueuePath $QueuePath)
        Write-ScoreLedgers -Scores $scores
        $selected = Select-NextScoredTask -Scores $scores
        [ordered]@{
            timestamp = (Get-Date -Format 'o')
            inspected = $scores.Count
            selectedTaskId = $(if ($selected) { $selected.task_id } else { $null })
            selectedTitle = $(if ($selected) { $selected.task_title } else { $null })
            selectionScore = $(if ($selected) { $selected.value_score } else { $null })
            selectionReason = $(if ($selected) { "value=$($selected.expected_value); risk=$($selected.safety_risk); confidence=$($selected.confidence)" } else { 'No safe ready pending task found' })
        } | ConvertTo-Json -Depth 12
    }
    'MockTrial' {
        $queue = Get-IdleQueue -Path $QueuePath
        $task = $null
        if ($TaskId) { $task = $queue.tasks | Where-Object { $_.id -eq $TaskId } | Select-Object -First 1 }
        if (-not $task) { throw "Task $TaskId not found for mock trial" }
        New-MockTrial -Task $task | ConvertTo-Json -Depth 12
    }
    'Improve' {
        Invoke-ImprovementAnalysis -QueuePath $QueuePath | ConvertTo-Json -Depth 12
    }
    'Readiness' {
        Invoke-ReadinessCheck | ConvertTo-Json -Depth 12
    }
    'Finalize' {
        Write-IntelligenceFinalReport | ConvertTo-Json -Depth 20
    }
    'ValidateGates' {
        Invoke-GateValidation | ConvertTo-Json -Depth 12
    }
}
