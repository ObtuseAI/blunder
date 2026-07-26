#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$QueuePath = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle/idle-task-queue.json',
    [string]$ArtifactRoot = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$processDetectionModule = Join-Path $PSScriptRoot 'idle-process-detection.ps1'
$queueModule = Join-Path $PSScriptRoot 'idle-task-queue.ps1'
$operatorAuthorizationModule = Join-Path $PSScriptRoot 'blunder-operator-authorization.ps1'
. $processDetectionModule
. $operatorAuthorizationModule
$script:BlunderOperatorAuthorization = Get-BlunderOperatorAuthorization -RepoRoot $repoRoot

function Resolve-LocalPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $repoRoot $Path
}

function Resolve-OpenRouterModelId {
    param([string]$Alias)
    if ([string]::IsNullOrWhiteSpace($Alias)) { return $null }
    switch ($Alias.ToLowerInvariant()) {
        'deepseek-v4-flash' { return 'deepseek/deepseek-v4-flash' }
        'deepseek-v4-pro' { return 'deepseek-v4-pro' }
        default { return $Alias }
    }
}

function Test-QueueTasksMalformed {
    param([object[]]$Tasks)
    if ($null -eq $Tasks) { return $false }
    foreach ($task in $Tasks) {
        $required = @('id','title','status','priority','risk','sourceProject','nextAction','payload','createdAt','updatedAt')
        foreach ($r in $required) {
            if (-not ($task.PSObject.Properties.Name -contains $r)) { return $true }
        }
        if ($task.status -notin @('pending','running','passed','failed','blocked','quarantined')) { return $true }
        if ($null -eq $task.title -or $null -eq $task.status -or $null -eq $task.id) { return $true }
        try { [int]$task.priority | Out-Null } catch { return $true }
        try { [int]$task.risk | Out-Null } catch { return $true }
    }
    return $false
}

function Get-QueueReadabilityResult {
    param([string]$QueuePath)
    $resolved = Resolve-LocalPath -Path $QueuePath
    $result = [ordered]@{
        readable = $false
        malformed = $false
        malformedItemCount = 0
        queueRaw = $null
        queueObject = $null
        parseError = $null
    }
    if (-not (Test-Path $resolved)) {
        $result.parseError = 'Queue file not found'
        return $result
    }
    for ($attempt = 1; $attempt -le 5; $attempt++) {
        try {
            $raw = Get-Content $resolved -Raw -ErrorAction Stop
            $result.queueRaw = $raw
            $obj = $raw | ConvertFrom-Json -ErrorAction Stop
            $result.queueObject = $obj
            if (-not ($obj.PSObject.Properties.Name -contains 'tasks') -or -not ($obj.tasks -is [array])) {
                $result.malformed = $true
                $result.malformedItemCount = 1
                return $result
            }
            $malformed = 0
            foreach ($task in $obj.tasks) {
                $required = @('id','title','status','priority','risk','sourceProject','nextAction','payload','createdAt','updatedAt')
                $bad = $false
                foreach ($r in $required) {
                    if (-not ($task.PSObject.Properties.Name -contains $r)) { $bad = $true; break }
                }
                if ($task.status -notin @('pending','running','passed','failed','blocked','quarantined','disabled')) { $bad = $true }
                if ($null -eq $task.title -or $null -eq $task.status -or $null -eq $task.id) { $bad = $true }
                try { [int]$task.priority | Out-Null } catch { $bad = $true }
                try { [int]$task.risk | Out-Null } catch { $bad = $true }
                if ($bad) { $malformed++ }
            }
            $result.readable = $true
            $result.malformed = ($malformed -gt 0)
            $result.malformedItemCount = $malformed
            $result.parseError = $null
            return $result
        }
        catch {
            $result.parseError = $_.Exception.Message
            Start-Sleep -Milliseconds (75 * $attempt)
        }
    }
    return $result
}

function Test-ReplenishmentStateMalformed {
    param([object]$State)
    if ($null -eq $State) { return $false }
    $required = @('version','cursor','lastReplenishedAt','policy','cooldownSeconds')
    foreach ($r in $required) {
        if (-not ($State.PSObject.Properties.Name -contains $r)) { return $true }
    }
    try { [int]$State.cursor | Out-Null } catch { return $true }
    try { [int]$State.cooldownSeconds | Out-Null } catch { return $true }
    if ($State.lastReplenishedAt -and -not ($State.lastReplenishedAt -as [datetime])) { return $true }
    return $false
}

function Get-QueueReplenishmentStatus {
    param(
        [object]$RunnerStatus,
        [string]$QueuePath,
        [bool]$PidReuseSuspicion,
        [bool]$OrphanProcess
    )
    $queueReadability = Get-QueueReadabilityResult -QueuePath $QueuePath
    $queueStats = $RunnerStatus.queueStats
    $total = if ($queueStats -and ($queueStats.PSObject.Properties.Name -contains 'total')) { [int]$queueStats.total } else { $null }
    $pending = if ($queueStats -and ($queueStats.PSObject.Properties.Name -contains 'pending')) { [int]$queueStats.pending } else { 0 }
    $running = if ($queueStats -and ($queueStats.PSObject.Properties.Name -contains 'running')) { [int]$queueStats.running } else { 0 }
    $passed = if ($queueStats -and ($queueStats.PSObject.Properties.Name -contains 'passed')) { [int]$queueStats.passed } else { 0 }
    $blocked = if ($queueStats -and ($queueStats.PSObject.Properties.Name -contains 'blocked')) { [int]$queueStats.blocked } else { 0 }
    $quarantined = if ($queueStats -and ($queueStats.PSObject.Properties.Name -contains 'quarantined')) { [int]$queueStats.quarantined } else { 0 }
    $failed = if ($queueStats -and ($queueStats.PSObject.Properties.Name -contains 'failed')) { [int]$queueStats.failed } else { 0 }
    $disabled = if ($queueStats -and ($queueStats.PSObject.Properties.Name -contains 'disabled')) { [int]$queueStats.disabled } else { 0 }
    $safeExecutable = ($pending + $running)

    $replState = $RunnerStatus.replenishmentState
    $lastDecision = $RunnerStatus.lastReplenishmentDecision
    $replenishmentDisabled = ($lastDecision -and $lastDecision.decision -eq 'SKIP_DISABLED')

    # Determine replenishment state
    $replenishmentState = 'REPLENISHMENT_UNKNOWN_NEEDS_REVIEW'
    if ($replenishmentDisabled) {
        $replenishmentState = 'REPLENISHMENT_DISABLED'
    }
    elseif ($null -eq $replState -or -not ($replState.PSObject.Properties.Name -contains 'version')) {
        $replenishmentState = 'REPLENISHMENT_STATE_MISSING'
    }
    elseif (Test-ReplenishmentStateMalformed -State $replState) {
        $replenishmentState = 'REPLENISHMENT_STATE_MALFORMED'
    }
    else {
        $queueMalformed = $queueReadability.malformed
        $queueUnreadable = -not $queueReadability.readable
        $guardActive = $OrphanProcess -or $PidReuseSuspicion -or $queueMalformed -or $queueUnreadable
        $queueNonempty = ($null -ne $total -and $total -gt 0)
        $queueHasActiveOrBlockedWork = (($safeExecutable + $blocked + $disabled) -gt 0)

        $ageSeconds = $null
        if ($replState.lastReplenishedAt) {
            try { $ageSeconds = [math]::Round(((Get-Date) - [datetime]$replState.lastReplenishedAt).TotalSeconds, 3) } catch {}
        }
        $cooldownSeconds = [int]$replState.cooldownSeconds

        if ($guardActive) { $replenishmentState = 'REPLENISHMENT_SUPPRESSED_GUARD' }
        elseif ($queueNonempty -and $queueHasActiveOrBlockedWork) { $replenishmentState = 'REPLENISHMENT_SUPPRESSED_QUEUE_NONEMPTY' }
        elseif ($ageSeconds -and $ageSeconds -lt $cooldownSeconds) { $replenishmentState = 'REPLENISHMENT_SUPPRESSED_COOLDOWN' }
        else { $replenishmentState = 'REPLENISHMENT_AVAILABLE' }
    }

    # Determine queue health state
    $queueState = 'QUEUE_UNKNOWN_NEEDS_REVIEW'
    if (-not $queueReadability.readable) { $queueState = 'QUEUE_UNREADABLE' }
    elseif ($queueReadability.malformed) { $queueState = 'QUEUE_MALFORMED' }
    elseif ($null -eq $total) { $queueState = 'QUEUE_UNKNOWN_NEEDS_REVIEW' }
    elseif ($total -eq 0) {
        switch ($replenishmentState) {
            'REPLENISHMENT_STATE_MALFORMED' { $queueState = 'QUEUE_MALFORMED' }
            'REPLENISHMENT_DISABLED' { $queueState = 'QUEUE_HEALTHY_EMPTY' }
            'REPLENISHMENT_STATE_MISSING' { $queueState = 'QUEUE_HEALTHY_EMPTY' }
            'REPLENISHMENT_AVAILABLE' { $queueState = 'QUEUE_EMPTY_REPLENISHMENT_AVAILABLE' }
            'REPLENISHMENT_SUPPRESSED_COOLDOWN' { $queueState = 'QUEUE_EMPTY_REPLENISHMENT_COOLDOWN' }
            'REPLENISHMENT_SUPPRESSED_GUARD' { $queueState = 'QUEUE_HEALTHY_EMPTY' }
            'REPLENISHMENT_SUPPRESSED_QUEUE_NONEMPTY' { $queueState = 'QUEUE_HEALTHY_EMPTY' }
            default { $queueState = 'QUEUE_UNKNOWN_NEEDS_REVIEW' }
        }
    }
    elseif ($safeExecutable -gt 0) { $queueState = 'QUEUE_NONEMPTY_READY' }
    elseif (($blocked + $disabled) -gt 0) { $queueState = 'QUEUE_NONEMPTY_BLOCKED' }
    elseif ($quarantined -gt 0 -or $passed -gt 0) {
        switch ($replenishmentState) {
            'REPLENISHMENT_AVAILABLE' { $queueState = 'QUEUE_HISTORY_ONLY_REPLENISHMENT_AVAILABLE' }
            'REPLENISHMENT_SUPPRESSED_COOLDOWN' { $queueState = 'QUEUE_HISTORY_ONLY_REPLENISHMENT_COOLDOWN' }
            'REPLENISHMENT_SUPPRESSED_GUARD' { $queueState = 'QUEUE_HISTORY_ONLY_REVIEW_GUARD' }
            default { $queueState = 'QUEUE_HISTORY_ONLY_NO_EXECUTABLE_WORK' }
        }
    }
    else { $queueState = 'QUEUE_UNKNOWN_NEEDS_REVIEW' }

    # Replenishment timing
    $lastReplenishedAt = if ($replState) { $replState.lastReplenishedAt } else { $null }
    $ageSeconds = $null
    $cooldownRemainingSeconds = $null
    $nextEligibleReplenishmentTime = $null
    if ($lastReplenishedAt) {
        try {
            $last = [datetime]$lastReplenishedAt
            $ageSeconds = [math]::Round(((Get-Date) - $last).TotalSeconds, 3)
            $cooldownSeconds = if ($replState.cooldownSeconds) { [int]$replState.cooldownSeconds } else { 600 }
            $remaining = $cooldownSeconds - $ageSeconds
            if ($remaining -gt 0) {
                $cooldownRemainingSeconds = [math]::Round($remaining, 3)
                $nextEligibleReplenishmentTime = $last.AddSeconds($cooldownSeconds).ToString('o')
            }
        } catch {}
    }

    # Operator recommendation
    $operatorRecommendation = 'DO_NOT_START_UNTIL_REPAIRED'
    if ($OrphanProcess -or $PidReuseSuspicion) {
        $operatorRecommendation = 'STOP_AND_REVIEW'
    }
    elseif ($replenishmentState -eq 'REPLENISHMENT_STATE_MISSING' -and $queueState -eq 'QUEUE_HEALTHY_EMPTY') {
        $operatorRecommendation = 'RUN_VALIDATE'
    }
    elseif ($queueState -eq 'QUEUE_HEALTHY_EMPTY') {
        $operatorRecommendation = 'NO_ACTION_IDLE_HEALTHY'
    }
    elseif ($queueState -eq 'QUEUE_EMPTY_REPLENISHMENT_AVAILABLE') {
        $operatorRecommendation = 'START_ALLOWED_WHEN_OPERATOR_READY'
    }
    elseif ($queueState -eq 'QUEUE_EMPTY_REPLENISHMENT_COOLDOWN') {
        $operatorRecommendation = 'WAIT_FOR_REPLENISHMENT_COOLDOWN'
    }
    elseif ($queueState -eq 'QUEUE_HISTORY_ONLY_REPLENISHMENT_AVAILABLE') {
        if ($RunnerStatus.active) { $operatorRecommendation = 'NO_ACTION_IDLE_HEALTHY' }
        else { $operatorRecommendation = 'START_ALLOWED_WHEN_OPERATOR_READY' }
    }
    elseif ($queueState -eq 'QUEUE_HISTORY_ONLY_REPLENISHMENT_COOLDOWN') {
        if ($RunnerStatus.active) { $operatorRecommendation = 'NO_ACTION_IDLE_HEALTHY' }
        else { $operatorRecommendation = 'WAIT_FOR_REPLENISHMENT_COOLDOWN' }
    }
    elseif ($queueState -eq 'QUEUE_HISTORY_ONLY_NO_EXECUTABLE_WORK') {
        $operatorRecommendation = 'RUN_VALIDATE'
    }
    elseif ($queueState -eq 'QUEUE_NONEMPTY_READY') {
        if ($RunnerStatus.active) { $operatorRecommendation = 'NO_ACTION_IDLE_HEALTHY' }
        else { $operatorRecommendation = 'START_ALLOWED_WHEN_OPERATOR_READY' }
    }
    elseif ($queueState -eq 'QUEUE_NONEMPTY_BLOCKED') {
        $operatorRecommendation = 'REVIEW_QUEUE_BLOCKERS'
    }
    elseif ($queueState -in @('QUEUE_UNREADABLE','QUEUE_MALFORMED')) {
        $operatorRecommendation = 'STOP_AND_REVIEW'
    }

    # Safety state label
    $safetyStateLabel = 'SAFE_IDLE_STOPPED'
    if ($OrphanProcess) { $safetyStateLabel = 'UNSAFE_ORPHAN_PROCESS_SUSPECTED' }
    elseif ($PidReuseSuspicion) { $safetyStateLabel = 'UNSAFE_PID_REUSE_SUSPECTED' }
    elseif ($queueState -in @('QUEUE_MALFORMED','QUEUE_UNREADABLE')) { $safetyStateLabel = 'UNSAFE_REVIEW_REQUIRED' }
    elseif ($queueState -eq 'QUEUE_NONEMPTY_BLOCKED') { $safetyStateLabel = 'SAFE_IDLE_STOPPED_REVIEW_SUGGESTED' }
    elseif ($RunnerStatus.active) { $safetyStateLabel = 'SAFE_IDLE_RUNNING' }
    elseif ($queueState -like 'QUEUE_HISTORY_ONLY*') { $safetyStateLabel = 'SAFE_IDLE_STOPPED_HISTORY_ONLY' }
    elseif ($null -eq $replState -or -not ($replState.PSObject.Properties.Name -contains 'version')) { $safetyStateLabel = 'SAFE_IDLE_STOPPED_REVIEW_SUGGESTED' }

    # NEXT-006 regression evidence
    $next006ArtifactDir = Join-Path $repoRoot 'artifacts\obtuse\idle-next-006-supervisor-false-positive-regression-v1'
    $next006ProofBundle = Join-Path $repoRoot 'artifacts\obtuse\proof-bundles\obtuse-blunder-idle-next-006-supervisor-false-positive-regression-v1-20260628T001225.zip'
    $expectedSha = 'B2157860E19237D8231CA3C1D2897765F75C623BC4693FF0AE596021E42AE299'
    $fpRegressionEvidencePresent = $false
    if (Test-Path $next006ProofBundle) {
        $observedSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $next006ProofBundle).Hash.ToUpperInvariant()
        if ($observedSha -eq $expectedSha) { $fpRegressionEvidencePresent = $true }
    }

    # Longer runtime gate reminder
    $longIdleAuthorized = Test-BlunderAuthorizationScope -Authorization $script:BlunderOperatorAuthorization -Scope 'long_idle_runtime_120min_plus'
    $longerRuntimeGate = if ($longIdleAuthorized) { 'LONGER_RUNTIME_OPERATOR_AUTHORIZED' } else { 'LONGER_RUNTIME_REMAINS_GATED' }
    $longerRuntimeReminder = if ($longIdleAuthorized) {
        '120-minute and longer continuous idle runs are covered by the local standing operator authorization; proof artifacts remain required.'
    } else {
        '120-minute and longer continuous idle runs remain operator-gated; explicit approval and proof artifacts required before start.'
    }
    if ($operatorRecommendation -eq 'START_ALLOWED_WHEN_OPERATOR_READY') {
        $operatorRecommendation = 'LONGER_RUNTIME_REMAINS_GATED'
    }

    $queueInterpretation = switch ($queueState) {
        'QUEUE_HEALTHY_EMPTY' { 'Queue is empty and healthy.' }
        'QUEUE_EMPTY_REPLENISHMENT_AVAILABLE' { 'Queue is empty and replenishment is available when needed.' }
        'QUEUE_EMPTY_REPLENISHMENT_COOLDOWN' { 'Queue is empty; replenishment is cooling down before the next batch.' }
        'QUEUE_NONEMPTY_READY' { 'Queue contains executable work.' }
        'QUEUE_NONEMPTY_BLOCKED' { 'Queue has tasks but none are currently runnable; review blockers.' }
        'QUEUE_HISTORY_ONLY_REPLENISHMENT_AVAILABLE' { 'Queue contains only completed, failed, or quarantined history; active runner can replenish on the next cycle.' }
        'QUEUE_HISTORY_ONLY_REPLENISHMENT_COOLDOWN' { 'Queue contains only completed, failed, or quarantined history; replenishment is cooling down.' }
        'QUEUE_HISTORY_ONLY_NO_EXECUTABLE_WORK' { 'Queue contains only completed or quarantined history and no executable work.' }
        'QUEUE_HISTORY_ONLY_REVIEW_GUARD' { 'Queue contains only completed or quarantined history, but a guard prevents replenishment.' }
        'QUEUE_UNREADABLE' { 'Queue file cannot be read or parsed.' }
        'QUEUE_MALFORMED' { 'Queue contains malformed task records.' }
        default { 'Queue state is unclear and requires review.' }
    }

    $replenishmentInterpretation = switch ($replenishmentState) {
        'REPLENISHMENT_DISABLED' { 'Automatic replenishment is disabled.' }
        'REPLENISHMENT_STATE_MISSING' { 'Replenishment state has not been initialized.' }
        'REPLENISHMENT_STATE_MALFORMED' { 'Replenishment state file is malformed.' }
        'REPLENISHMENT_AVAILABLE' { 'Replenishment is enabled and ready when the queue is empty.' }
        'REPLENISHMENT_SUPPRESSED_COOLDOWN' { 'Replenishment is suppressed by cooldown.' }
        'REPLENISHMENT_SUPPRESSED_QUEUE_NONEMPTY' { 'Replenishment is suppressed because the queue has work.' }
        'REPLENISHMENT_SUPPRESSED_GUARD' { 'Replenishment is suppressed by a safety guard.' }
        default { 'Replenishment state is unclear and requires review.' }
    }

    return [ordered]@{
        queuePath = $QueuePath
        queueReadable = $queueReadability.readable
        queueEmpty = ($null -ne $total -and $total -eq 0)
        queueHealthState = $queueState
        safeExecutableTaskCount = $safeExecutable
        blockedTaskCount = $blocked
        quarantinedTaskCount = $quarantined
        malformedQueueItemCount = $queueReadability.malformedItemCount
        queueInterpretation = $queueInterpretation
        replenishmentPolicyEnabled = ($replenishmentState -ne 'REPLENISHMENT_DISABLED')
        replenishmentMode = 'queue-empty-only'
        replenishmentCooldownSeconds = if ($replState -and $replState.cooldownSeconds) { [int]$replState.cooldownSeconds } else { 600 }
        replenishmentBatchSize = if ($replState -and $replState.batchSize) { [int]$replState.batchSize } else { 1 }
        lastReplenishedAt = $lastReplenishedAt
        nextEligibleReplenishmentTime = $nextEligibleReplenishmentTime
        cooldownRemainingSeconds = $cooldownRemainingSeconds
        replenishmentSuppressedByCooldown = ($replenishmentState -eq 'REPLENISHMENT_SUPPRESSED_COOLDOWN')
        replenishmentSuppressedBecauseQueueNonempty = ($replenishmentState -eq 'REPLENISHMENT_SUPPRESSED_QUEUE_NONEMPTY')
        replenishmentSuppressedByGuard = ($replenishmentState -eq 'REPLENISHMENT_SUPPRESSED_GUARD')
        replenishmentSuppressFollowUp = $true
        replenishmentState = $replenishmentState
        replenishmentInterpretation = $replenishmentInterpretation
        nextReplenishmentDecision = (Resolve-NextReplenishmentDecision -ReplenishmentState $replenishmentState)
        next006FalsePositiveRegressionEvidencePresent = $fpRegressionEvidencePresent
        next006ProofBundleSha256 = if ($fpRegressionEvidencePresent) { $expectedSha } else { $null }
        operatorRecommendation = $operatorRecommendation
        safetyStateLabel = $safetyStateLabel
        longerRuntimeGate = $longerRuntimeGate
        longerRuntimeReminder = $longerRuntimeReminder
    }
}

function Resolve-NextReplenishmentDecision {
    param([string]$ReplenishmentState)
    switch ($ReplenishmentState) {
        'REPLENISHMENT_DISABLED' { return 'SKIP_DISABLED' }
        'REPLENISHMENT_STATE_MISSING' { return 'STATE_MISSING_DEFAULT_TO_AVAILABLE' }
        'REPLENISHMENT_STATE_MALFORMED' { return 'REJECT_MALFORMED_STATE' }
        'REPLENISHMENT_SUPPRESSED_COOLDOWN' { return 'SKIP_COOLDOWN' }
        'REPLENISHMENT_SUPPRESSED_QUEUE_NONEMPTY' { return 'SKIP_QUEUE_HAS_WORK' }
        'REPLENISHMENT_SUPPRESSED_GUARD' { return 'REJECT_GUARD' }
        'REPLENISHMENT_AVAILABLE' { return 'ADMIT_REPLENISHMENT_BATCH' }
        default { return 'REJECT_UNKNOWN' }
    }
}

function Read-JsonFileSafe {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try { return Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json -ErrorAction Stop } catch { return $null }
}

function Read-JsonLinesSafe {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return @() }
    $rows = @()
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        try { $rows += ($line | ConvertFrom-Json -ErrorAction Stop) } catch {}
    }
    return $rows
}

function Get-OpenRouterSelfCodingStatus {
    $candidateRoots = @(
        (Join-Path $repoRoot 'artifacts\obtuse\blunder-flash-full-autonomy-compounding-bundle-v1\self-coding'),
        (Join-Path $repoRoot 'artifacts\obtuse\blunder-flash-full-autonomy-compounding-bundle-v1'),
        (Join-Path $repoRoot 'artifacts\obtuse\idle-switch-to-deepseek-v4-flash-v1'),
        (Join-Path $repoRoot 'artifacts\obtuse\idle-openrouter-deepseek-v4-pro-self-coding-acceleration-v1')
    )
    $root = $candidateRoots | Where-Object { Test-Path -LiteralPath (Join-Path $_ 'openrouter_provider_lane_config.json') } | Select-Object -First 1
    if (-not $root) { $root = $candidateRoots[0] }
    $config = Read-JsonFileSafe -Path (Join-Path $root 'openrouter_provider_lane_config.json')
    $final = Read-JsonFileSafe -Path (Join-Path $root 'FINAL_REPORT.json')
    $run = Read-JsonFileSafe -Path (Join-Path $root 'self_coding_run_summary.json')
    $threshold = Read-JsonFileSafe -Path (Join-Path $root 'threshold_decision_latest.json')
    $compoundPlan = Read-JsonFileSafe -Path (Join-Path $root 'compound_evolution_plan.json')
    $calls = @(Read-JsonLinesSafe -Path (Join-Path $root 'openrouter_call_ledger.jsonl'))
    $lastCall = if ($calls.Count -gt 0) { $calls[-1] } else { $null }
    $modelAlias = $(if ($config -and $config.openRouterModelAlias) { $config.openRouterModelAlias } elseif ($final -and $final.openRouterModelAlias) { $final.openRouterModelAlias } elseif ($config -and $config.modelConfigured) { $config.modelConfigured } elseif ($final -and $final.modelConfigured) { $final.modelConfigured } else { $null })
    if ($modelAlias -eq 'deepseek/deepseek-v4-flash') { $modelAlias = 'deepseek-v4-flash' }
    $modelResolved = $(if ($config -and $config.openRouterModelResolved) { $config.openRouterModelResolved } elseif ($final -and $final.openRouterModelResolved) { $final.openRouterModelResolved } elseif ($modelAlias) { Resolve-OpenRouterModelId -Alias $modelAlias } else { $null })
    $finalHasOpenRouterIdleEnabled = ($final -and ($final.PSObject.Properties.Name -contains 'openRouterIdleEnabled'))
    $finalHasOpenRouterEnabled = ($final -and ($final.PSObject.Properties.Name -contains 'openRouterEnabled'))
    $finalHasSelfCodingAcceleratorEnabled = ($final -and ($final.PSObject.Properties.Name -contains 'selfCodingAcceleratorEnabled'))
    $finalHasBlunderFullyAutonomous = ($final -and ($final.PSObject.Properties.Name -contains 'blunderFullyAutonomous'))
    $configHasOpenRouterIdleEnabled = ($config -and ($config.PSObject.Properties.Name -contains 'openRouterIdleEnabled'))
    $configHasModelNeedsOperatorReview = ($config -and ($config.PSObject.Properties.Name -contains 'modelNeedsOperatorReview'))
    $laneEnabled = if ($configHasOpenRouterIdleEnabled) {
        [bool]$config.openRouterIdleEnabled
    } elseif ($finalHasOpenRouterIdleEnabled) {
        [bool]$final.openRouterIdleEnabled
    } elseif ($finalHasOpenRouterEnabled) {
        [bool]$final.openRouterEnabled
    } else {
        $false
    }
    $acceleratorEnabled = if ($configHasOpenRouterIdleEnabled) {
        [bool]$config.openRouterIdleEnabled
    } elseif ($finalHasSelfCodingAcceleratorEnabled) {
        [bool]$final.selfCodingAcceleratorEnabled
    } elseif ($finalHasBlunderFullyAutonomous) {
        [bool]$final.blunderFullyAutonomous
    } else {
        $false
    }
    $needsReview = if ($configHasModelNeedsOperatorReview) {
        [bool]$config.modelNeedsOperatorReview
    } elseif ($final) {
        $final.terminalState -eq 'OPENROUTER_LANE_READY_MODEL_ID_NEEDS_OPERATOR_REVIEW'
    } else {
        $false
    }
    return [ordered]@{
        openRouterIdleEnabled = $laneEnabled
        openRouterModel = $(if ($final) { $final.modelUsed } elseif ($config) { $config.modelUsed } else { $null })
        openRouterModelAlias = $modelAlias
        openRouterModelResolved = $modelResolved
        openRouterCallsUsed = @($calls | Where-Object { $_.callAttempted -eq $true }).Count
        openRouterCallBudget = $(if ($config) { $config.providerCallBudget } else { 6 })
        providerBudgetGateEnabled = $false
        providerBudgetGateStatus = 'DISABLED_BY_OPERATOR_CHEAP_MODEL_POLICY'
        providerCallsAttempted = @($calls | Where-Object { $_.callAttempted -eq $true }).Count
        providerCallsSucceeded = @($calls | Where-Object { $_.success -eq $true }).Count
        providerCallsFailed = @($calls | Where-Object { $_.callAttempted -eq $true -and $_.success -ne $true }).Count
        openRouterLastCallUtc = $(if ($lastCall) { $lastCall.timestamp } else { $null })
        openRouterLastResult = $(if ($lastCall) { $lastCall.result } elseif ($final) { $final.smokeCallResult } else { $null })
        selfCodingAcceleratorEnabled = $acceleratorEnabled
        selfCodingLastCycleUtc = $(if ($run) { $run.timestamp } else { $null })
        selfCodingLastPromotion = $(if ($final -and ($final.PSObject.Properties.Name -contains 'promoted')) { $final.promoted } elseif ($final -and ($final.PSObject.Properties.Name -contains 'promotions')) { ([int]$final.promotions -gt 0) } elseif ($run) { $run.promoted } else { $null })
        selfCodingLastRollback = $(if ($final -and ($final.PSObject.Properties.Name -contains 'rollbackUsed')) { $final.rollbackUsed } elseif ($final -and ($final.PSObject.Properties.Name -contains 'rollbacks')) { ([int]$final.rollbacks -gt 0) } elseif ($run) { $run.rollbackUsed } else { $null })
        selfCodingThresholdStatus = $(if ($final -and ($final.PSObject.Properties.Name -contains 'thresholdDecision')) { $final.thresholdDecision } elseif ($final -and ($final.PSObject.Properties.Name -contains 'terminalState')) { $final.terminalState } elseif ($threshold) { $threshold.thresholdDecision } else { $null })
        compoundEvolutionPlanStatus = $(if ($compoundPlan) { $compoundPlan.status } else { $null })
        compoundEvolutionSelectedFamily = $(if ($compoundPlan) { $compoundPlan.selectedFamily } else { $null })
        compoundEvolutionPressureLevel = $(if ($compoundPlan) { $compoundPlan.pressure.level } else { $null })
        compoundEvolutionCandidateBudgetNext = $(if ($compoundPlan) { $compoundPlan.pressure.candidateBudgetNext } else { $null })
        compoundEvolutionCooledDownFamilies = $(if ($compoundPlan) { @($compoundPlan.cooledDownFamilies) } else { @() })
        compoundEvolutionChampionFitness = $(if ($compoundPlan) { $compoundPlan.championFloor.fitness } else { $null })
        compoundEvolutionAdvisoryOnly = $(if ($compoundPlan) { $compoundPlan.safety.advisoryOnly } else { $true })
        openRouterModelNeedsOperatorReview = $needsReview
        openRouterArtifactRoot = $root
    }
}

function Get-ProviderCallSafetyState {
    param(
        [object]$OpenRouterSelfCodingStatus,
        [object]$Authorization
    )

    $laneAuthorized = Test-BlunderAuthorizationScope -Authorization $Authorization -Scope 'openrouter_self_coding_cheap_model'
    $laneEnabled = [bool]$OpenRouterSelfCodingStatus.openRouterIdleEnabled
    $needsReview = [bool]$OpenRouterSelfCodingStatus.openRouterModelNeedsOperatorReview
    $cheapModelPolicy = ([string]$OpenRouterSelfCodingStatus.providerBudgetGateStatus -eq 'DISABLED_BY_OPERATOR_CHEAP_MODEL_POLICY')
    $providerFailures = if ($OpenRouterSelfCodingStatus.providerCallsFailed -ne $null) { [int]$OpenRouterSelfCodingStatus.providerCallsFailed } else { 0 }
    $allowed = ($laneAuthorized -and $laneEnabled -and -not $needsReview -and $cheapModelPolicy -and $providerFailures -eq 0)

    $reason = if (-not $laneAuthorized) {
        'OPENROUTER_SELF_CODING_SCOPE_NOT_AUTHORIZED'
    } elseif (-not $laneEnabled) {
        'OPENROUTER_SELF_CODING_NOT_ENABLED'
    } elseif ($needsReview) {
        'OPENROUTER_MODEL_NEEDS_OPERATOR_REVIEW'
    } elseif (-not $cheapModelPolicy) {
        'PROVIDER_BUDGET_POLICY_NOT_CHEAP_MODEL_DISABLED'
    } elseif ($providerFailures -gt 0) {
        'PROVIDER_FAILURES_PRESENT'
    } else {
        'APPROVED_OPENROUTER_SELF_CODING_CHEAP_MODEL_ONLY'
    }

    return [ordered]@{
        providerCallsAllowed = $allowed
        providerCallsAllowedScope = $(if ($allowed) { 'openrouter_self_coding_cheap_model_only' } else { 'blocked' })
        providerCallsAllowedReason = $reason
        providerCallsBlockedOutsideApprovedLane = $true
        approvedOpenRouterSelfCodingLane = $laneEnabled
        approvedOpenRouterSelfCodingLaneAuthorized = $laneAuthorized
        openRouterModelNeedsOperatorReview = $needsReview
        providerBudgetGateStatus = $OpenRouterSelfCodingStatus.providerBudgetGateStatus
        providerCallsFailed = $providerFailures
    }
}

Push-Location $repoRoot
try {
    $runner = Join-Path $PSScriptRoot 'idle-mode.ps1'
    $runnerStatus = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $runner -Action Status -QueuePath $QueuePath -ArtifactRoot $ArtifactRoot | ConvertFrom-Json
    $v7Repair = Join-Path $PSScriptRoot 'idle-v7-churn-repair-readiness.ps1'
    $v6Audit = Join-Path $PSScriptRoot 'idle-v6-proof-audit-escalation.ps1'
    $v5Sla = Join-Path $PSScriptRoot 'idle-reliability-sla-v5.ps1'
    $v4Bundle = Join-Path $PSScriptRoot 'idle-operator-bundle-v4.ps1'
    $appliance = Join-Path $PSScriptRoot 'idle-appliance-v3.ps1'
    $readiness = $null
    $v7State = $null
    $v6State = $null
    $v5State = $null
    $v4State = $null
    if (Test-Path $v7Repair) {
        try {
            $v7State = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $v7Repair -Mode ReportOnly | ConvertFrom-Json
        } catch {}
    }
    if (Test-Path $v6Audit) {
        try {
            $v6State = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $v6Audit -Mode ReportOnly | ConvertFrom-Json
        } catch {}
    }
    if ((Test-Path $v5Sla) -and $ArtifactRoot -like '*v5-supervised*') {
        try {
            $v5State = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $v5Sla -Mode ReportOnly -QueuePath $QueuePath -ArtifactRoot $ArtifactRoot | ConvertFrom-Json
            $readiness = $v5State.readiness
        } catch {}
    }
    elseif ((Test-Path $v4Bundle) -and $ArtifactRoot -like '*v4-one-shot*') {
        try {
            $v4State = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $v4Bundle -Mode ReportOnly -QueuePath $QueuePath -ArtifactRoot $ArtifactRoot | ConvertFrom-Json
            $readiness = $v4State.readiness
        } catch {}
    }
    elseif (Test-Path $appliance) {
        try { $readiness = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $appliance -Action Readiness -QueuePath $QueuePath -ArtifactRoot $ArtifactRoot | ConvertFrom-Json } catch {}
    }
    $lock = $runnerStatus.lock
    $lockPid = if ($lock) { $lock.pid } else { $null }
    $process = if ($lockPid) { Get-IdleProcessById -PidValue $lockPid } else { $null }
    $pidValid = if ($lockPid) { Test-IdleStartProcessAlive -PidValue $lockPid } else { $false }
    $pidReuseSuspicion = [bool]($lockPid -and $process -and -not $pidValid)
    $heartbeatAgeSeconds = $null
    if ($runnerStatus.heartbeat -and $runnerStatus.heartbeat.timestamp) {
        $heartbeatAgeSeconds = [Math]::Round(((Get-Date) - [datetime]$runnerStatus.heartbeat.timestamp).TotalSeconds, 3)
    }
    $orphanProcess = [bool]($runnerStatus.active -and -not $pidValid)
    $lastCycle = $null
    $cycleLedger = Join-Path $ArtifactRoot 'idle-cycle-ledger.jsonl'
    if (Test-Path $cycleLedger) {
        $line = Get-Content $cycleLedger -Tail 1
        if ($line) { try { $lastCycle = $line | ConvertFrom-Json } catch {} }
    }
    $recoveryState = $null
    $recoveryLedger = Join-Path $ArtifactRoot 'recovery_decision_ledger.jsonl'
    if (Test-Path $recoveryLedger) {
        $line = Get-Content $recoveryLedger -Tail 1
        if ($line) { try { $recoveryState = $line | ConvertFrom-Json } catch {} }
    }
    $validationState = $null
    $validationPath = Join-Path $ArtifactRoot 'validation_summary.json'
    if (Test-Path $validationPath) {
        try { $validationState = Get-Content $validationPath -Raw | ConvertFrom-Json } catch {}
    }
    $skillTokenCount = 0
    $skillLedger = Join-Path $ArtifactRoot 'skill_token_ledger.jsonl'
    if (Test-Path $skillLedger) { $skillTokenCount = @(Get-Content $skillLedger).Count }
    $churnWarnings = 0
    $churnLedger = Join-Path $ArtifactRoot 'anti_churn_ledger.jsonl'
    if (Test-Path $churnLedger) { $churnWarnings = @(Get-Content $churnLedger).Count }
    $proofSummary = $null
    $proofLedger = Join-Path $ArtifactRoot 'proof_promotion_ladder.jsonl'
    if (Test-Path $proofLedger) {
        $proofCounts = @{}
        foreach ($line in Get-Content $proofLedger) {
            if (-not $line.Trim()) { continue }
            try {
                $status = (ConvertFrom-Json $line).promotion_status
                if (-not $proofCounts.ContainsKey($status)) { $proofCounts[$status] = 0 }
                $proofCounts[$status]++
            } catch {}
        }
        $proofSummary = $proofCounts
    }
    $lastCycleSummary = $null
    if ($lastCycle) {
        $lastCycleSummary = [ordered]@{
            cycleAt = $lastCycle.cycleAt
            taskId = $lastCycle.taskId
            taskTitle = $lastCycle.taskTitle
            status = $lastCycle.status
            safe = $lastCycle.safe
            validationPass = $(if ($lastCycle.validation) { $lastCycle.validation.pass } else { $null })
            validationReason = $(if ($lastCycle.validation) { $lastCycle.validation.reason } else { $null })
            mockTrialSafe = $(if ($lastCycle.mockTrial) { $lastCycle.mockTrial.safe_for_idle } else { $null })
            nextTaskQueued = $lastCycle.nextTaskQueued
        }
    }

    $queueReplenishmentStatus = Get-QueueReplenishmentStatus -RunnerStatus $runnerStatus -QueuePath $QueuePath -PidReuseSuspicion $pidReuseSuspicion -OrphanProcess $orphanProcess
    $openRouterSelfCodingStatus = Get-OpenRouterSelfCodingStatus
    $providerCallSafetyState = Get-ProviderCallSafetyState -OpenRouterSelfCodingStatus $openRouterSelfCodingStatus -Authorization $script:BlunderOperatorAuthorization

    $recommended = $queueReplenishmentStatus.operatorRecommendation
    $operatorAutonomyApproved = Test-BlunderAuthorizationScope -Authorization $script:BlunderOperatorAuthorization -Scope 'continuous_idle_self_improvement'
    $longRuntimeAuthorized = Test-BlunderAuthorizationScope -Authorization $script:BlunderOperatorAuthorization -Scope 'long_idle_runtime_120min_plus'
    $blunderCodeSkipPermissionsAuthorized = Test-BlunderAuthorizationScope -Authorization $script:BlunderOperatorAuthorization -Scope 'blundercode_skip_permissions'
    $operatorAuthorizationSummary = [ordered]@{
        enabled = [bool]$script:BlunderOperatorAuthorization.enabled
        authorizationId = $script:BlunderOperatorAuthorization.authorizationId
        authorizationPath = $script:BlunderOperatorAuthorization.authorizationPath
        approvedScopes = @($script:BlunderOperatorAuthorization.approvedScopes)
        allowReliabilityBudgetOverride = [bool]$script:BlunderOperatorAuthorization.allowReliabilityBudgetOverride
        allowHistoricalV5QuarantineOnlyRetirement = [bool]$script:BlunderOperatorAuthorization.allowHistoricalV5QuarantineOnlyRetirement
        preserveHardDenials = @($script:BlunderOperatorAuthorization.preserveHardDenials)
        requiresSeparateApproval = @($script:BlunderOperatorAuthorization.requiresSeparateApproval)
    }

    [ordered]@{
        status = $(if ($runnerStatus.active) { 'running' } else { 'stopped' })
        active = $runnerStatus.active
        activePid = $lockPid
        pidValid = $pidValid
        pidReuseSuspicion = $pidReuseSuspicion
        heartbeatAgeSeconds = $heartbeatAgeSeconds
        orphanProcess = $orphanProcess
        lockfileState = $lock
        lastCycleResult = $lastCycleSummary
        lastTaskResult = $(if ($lastCycleSummary) { $lastCycleSummary.status } else { $null })
        queueCount = $runnerStatus.queueStats.total
        quarantineCount = $runnerStatus.queueStats.quarantined
        queueStats = $runnerStatus.queueStats
        queueCompactionStatus = $runnerStatus.queueCompaction
        queueReplenishmentStatus = $queueReplenishmentStatus
        operatorAuthorization = $operatorAuthorizationSummary
        operatorPermanentAutonomyApproved = $operatorAutonomyApproved
        longRuntimeOperatorAuthorized = $longRuntimeAuthorized
        blunderCodeSkipPermissionsAuthorized = $blunderCodeSkipPermissionsAuthorized
        v7Available = (Test-Path $v7Repair)
        v7ChurnRepairStatus = $(if ($v7State) { $v7State.status } else { $null })
        v7GenerationBudgetStatus = $(if ($v7State) { $v7State.generationBudgetResult } else { $null })
        v7DuplicateSignatureStatus = $(if ($v7State) { $v7State.duplicateSignatureCount } else { $null })
        v7QueueCompactionStatus = $(if ($v7State) { $v7State.queueCompactionResult } else { $null })
        v7ChurnTrendVersusV6 = $(if ($v7State) { $v7State.churnTrendVersusV6 } else { $null })
        v7Readiness120Decision = $(if ($v7State) { $v7State.readiness120Decision } else { $null })
        v5Available = (Test-Path $v5Sla)
        v6Available = (Test-Path $v6Audit)
        v6ProofAuditStatus = $(if ($v6State) { $v6State.status } else { $null })
        v6ProofVerificationStatus = $(if ($v6State) { $v6State.sha256VerificationResult } else { $null })
        v6CycleConsistencyStatus = $(if ($v6State) { $v6State.cycleConsistencyResult } else { $null })
        v6ChurnStatus = $(if ($v6State) { $v6State.churnClassification } else { $null })
        v6ProofBudgetStatus = $(if ($v6State) { $v6State.proofBudgetStatus } else { $null })
        v6ReliabilityBudgetStatus = $(if ($v6State) { $v6State.reliabilityBudgetStatus } else { $null })
        v6Escalation120Decision = $(if ($v6State) { $v6State.runtime120EscalationDecision } else { $null })
        v5LastValidationStatus = $(if ($v5State -and $v5State.validation) { $v5State.validation.pass } else { $null })
        v5SlaStatus = $(if ($v5State -and $v5State.sla) { $v5State.sla.status } else { $null })
        reliabilityBudgetStatus = $(if ($v5State -and $v5State.reliabilityBudget) { $v5State.reliabilityBudget.overallStatus } else { $null })
        churnBudgetStatus = $(if ($v5State -and $v5State.churnBudget) { $v5State.churnBudget.budgetStatus } else { $null })
        proofBudgetStatus = $(if ($v5State -and $v5State.proofBudget) { $v5State.proofBudget.budgetStatus } else { $null })
        continuousRunEligibility = $(if ($readiness) { $readiness.continuousRunEligible } else { $false })
        lastSupervisedWindowResult = $(if ($v5State -and $v5State.validation) { @{ planningCycles = $v5State.validation.planningCycles; foregroundCycles = $v5State.validation.foregroundCycles; backgroundLifecycle = $v5State.validation.backgroundLifecycle } } else { $null })
        stopReviewQuarantineRecommendation = $(if ($v5State -and $v5State.reliabilityBudget -and $v5State.reliabilityBudget.overallStatus -eq 'WITHIN_BUDGET') { 'continue_or_start_only_by_operator_command' } else { 'report_only_or_repair_only_until_budget_green' })
        v4BundleAvailable = (Test-Path $v4Bundle)
        v4ReadinessState = $readiness
        v4LastValidationState = $validationState
        skillTokenCount = $skillTokenCount
        churnWarnings = $churnWarnings
        lastProofQualitySummary = $proofSummary
        recoveryState = $recoveryState
        openRouterIdleEnabled = $openRouterSelfCodingStatus.openRouterIdleEnabled
        openRouterModel = $openRouterSelfCodingStatus.openRouterModel
        openRouterModelAlias = $openRouterSelfCodingStatus.openRouterModelAlias
        openRouterModelResolved = $openRouterSelfCodingStatus.openRouterModelResolved
        openRouterCallsUsed = $openRouterSelfCodingStatus.openRouterCallsUsed
        openRouterCallBudget = $openRouterSelfCodingStatus.openRouterCallBudget
        providerBudgetGateEnabled = $openRouterSelfCodingStatus.providerBudgetGateEnabled
        providerBudgetGateStatus = $openRouterSelfCodingStatus.providerBudgetGateStatus
        providerCallsAttempted = $openRouterSelfCodingStatus.providerCallsAttempted
        providerCallsSucceeded = $openRouterSelfCodingStatus.providerCallsSucceeded
        providerCallsFailed = $openRouterSelfCodingStatus.providerCallsFailed
        openRouterLastCallUtc = $openRouterSelfCodingStatus.openRouterLastCallUtc
        openRouterLastResult = $openRouterSelfCodingStatus.openRouterLastResult
        selfCodingAcceleratorEnabled = $openRouterSelfCodingStatus.selfCodingAcceleratorEnabled
        selfCodingLastCycleUtc = $openRouterSelfCodingStatus.selfCodingLastCycleUtc
        selfCodingLastPromotion = $openRouterSelfCodingStatus.selfCodingLastPromotion
        selfCodingLastRollback = $openRouterSelfCodingStatus.selfCodingLastRollback
        selfCodingThresholdStatus = $openRouterSelfCodingStatus.selfCodingThresholdStatus
        compoundEvolutionPlanStatus = $openRouterSelfCodingStatus.compoundEvolutionPlanStatus
        compoundEvolutionSelectedFamily = $openRouterSelfCodingStatus.compoundEvolutionSelectedFamily
        compoundEvolutionPressureLevel = $openRouterSelfCodingStatus.compoundEvolutionPressureLevel
        compoundEvolutionCandidateBudgetNext = $openRouterSelfCodingStatus.compoundEvolutionCandidateBudgetNext
        compoundEvolutionCooledDownFamilies = $openRouterSelfCodingStatus.compoundEvolutionCooledDownFamilies
        compoundEvolutionChampionFitness = $openRouterSelfCodingStatus.compoundEvolutionChampionFitness
        compoundEvolutionAdvisoryOnly = $openRouterSelfCodingStatus.compoundEvolutionAdvisoryOnly
        openRouterModelNeedsOperatorReview = $openRouterSelfCodingStatus.openRouterModelNeedsOperatorReview
        safetyState = @{
            benchmarkRelaunchAllowed = $false
            providerCallsAllowed = $providerCallSafetyState.providerCallsAllowed
            providerCallsAllowedScope = $providerCallSafetyState.providerCallsAllowedScope
            providerCallsAllowedReason = $providerCallSafetyState.providerCallsAllowedReason
            providerCallsBlockedOutsideApprovedLane = $providerCallSafetyState.providerCallsBlockedOutsideApprovedLane
            destructiveActionsAllowed = $false
            secretsPrinted = $false
            safetyBoundaryModificationAllowed = $false
            approvedOpenRouterSelfCodingLane = $providerCallSafetyState.approvedOpenRouterSelfCodingLane
            approvedOpenRouterSelfCodingLaneAuthorized = $providerCallSafetyState.approvedOpenRouterSelfCodingLaneAuthorized
            openRouterModelNeedsOperatorReview = $providerCallSafetyState.openRouterModelNeedsOperatorReview
            providerBudgetGateStatus = $providerCallSafetyState.providerBudgetGateStatus
            providerCallsFailed = $providerCallSafetyState.providerCallsFailed
        }
        continuousReadinessState = $readiness
        v7RecommendedOperatorAction = $(if ($v7State -and $v7State.readiness120Decision -like 'GO*' -and $longRuntimeAuthorized) { '120-minute runtime is operator-authorized by standing local approval; keep proof artifacts fresh.' } elseif ($v7State -and $v7State.readiness120Decision -like 'GO*') { '120-minute runtime remains operator-gated; explicit approval required before start.' } elseif ($v7State) { 'V7 readiness is not GO; repair or review before any longer runtime.' } else { 'Run idle-v7-churn-repair-readiness.ps1 -Mode Validate to create V7 churn repair proof.' })
        v6RecommendedOperatorAction = $(if ($v6State -and $v6State.runtime120EscalationDecision -like 'GO*' -and $longRuntimeAuthorized) { '120-minute runtime is operator-authorized by standing local approval; review V6 proof if stale.' } elseif ($v6State -and $v6State.runtime120EscalationDecision -like 'GO*') { '120-minute runtime remains operator-gated; review V6 proof before explicit approval.' } elseif ($v6State) { 'Inspect V6 final report before any longer runtime.' } else { 'Run idle-v6-proof-audit-escalation.ps1 -Mode Validate to create V6 proof audit.' })
        finalRecommendedOperatorAction = $recommended
        rawRunnerStatus = $runnerStatus
    } | ConvertTo-Json -Depth 20
}
finally {
    Pop-Location
}


# OpenRouter self-coding status fields are populated by the dedicated accelerator artifact reader in newer builds.


# STATUS-SELF-CODING-OBSERVABILITY-MARKER-v1: OpenRouter self-coding status fields are populated by the accelerator artifact reader.
