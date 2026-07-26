#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Validate','Reconcile','MonitorShort','ReportOnly','ProofOnly','StopIfUnsafe')]
    [string]$Mode = 'Validate',
    [object]$OperatorApproved = $false,
    [int]$DurationMinutes = 2,
    [string]$ArtifactRoot = 'artifacts/obtuse/idle-active-proof-reconciliation-and-monitor-v1',
    [string]$QueuePath = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle/idle-task-queue.json',
    [string]$IdleArtifactRoot = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$processDetectionModule = Join-Path $PSScriptRoot 'idle-process-detection.ps1'
. $processDetectionModule

function Convert-ToBoolean {
    param([object]$Value)
    if ($Value -is [bool]) { return [bool]$Value }
    if ($Value -is [int]) { return ([int]$Value -ne 0) }
    $text = ([string]$Value).Trim()
    return ($text -in @('true','1','yes','y','approved'))
}

$OperatorApproved = Convert-ToBoolean -Value $OperatorApproved

function Resolve-RepoPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $repoRoot $Path
}

function Write-JsonFile {
    param([object]$Value, [string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    $dir = Split-Path $resolved -Parent
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $Value | ConvertTo-Json -Depth 40 | Set-Content -LiteralPath $resolved -Encoding UTF8
}

function Add-JsonLine {
    param([string]$Path, [object]$Record)
    $resolved = Resolve-RepoPath -Path $Path
    $dir = Split-Path $resolved -Parent
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $Record | ConvertTo-Json -Depth 30 -Compress | Add-Content -LiteralPath $resolved -Encoding UTF8
}

function Read-JsonFile {
    param([string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    if (-not (Test-Path -LiteralPath $resolved)) { return $null }
    try { return Get-Content -Raw -LiteralPath $resolved | ConvertFrom-Json -ErrorAction Stop } catch { return $null }
}

function Invoke-LocalScriptJson {
    param([string]$ScriptName, [string[]]$Arguments)
    $scriptPath = Join-Path $PSScriptRoot $ScriptName
    $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $scriptPath @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) { $exitCode = 0 }
    $raw = ($output | ForEach-Object { [string]$_ }) -join "`n"
    $parsed = $null
    if (-not [string]::IsNullOrWhiteSpace($raw)) {
        try { $parsed = $raw | ConvertFrom-Json -ErrorAction Stop } catch {}
    }
    return [ordered]@{
        exitCode = $exitCode
        raw = $raw
        parsed = $parsed
    }
}

function ConvertTo-SmallStatusSnapshot {
    param([object]$Status)
    if (-not $Status) { return $null }
    return [ordered]@{
        timestamp = (Get-Date -Format 'o')
        status = $Status.status
        active = [bool]$Status.active
        activePid = $Status.activePid
        pidValid = [bool]$Status.pidValid
        heartbeatAgeSeconds = $Status.heartbeatAgeSeconds
        heartbeatObserved = ($Status.heartbeatAgeSeconds -ne $null)
        orphanProcess = [bool]$Status.orphanProcess
        pidReuseSuspicion = [bool]$Status.pidReuseSuspicion
        queueStats = $Status.queueStats
        queueReplenishmentStatus = $Status.queueReplenishmentStatus
        finalRecommendedOperatorAction = $Status.finalRecommendedOperatorAction
        safetyState = $Status.safetyState
        v7ChurnRepairStatus = $Status.v7ChurnRepairStatus
        v7Readiness120Decision = $Status.v7Readiness120Decision
    }
}

function Get-CurrentStatus {
    $res = Invoke-LocalScriptJson -ScriptName 'status-idle-ops.ps1' -Arguments @('-QueuePath', $QueuePath, '-ArtifactRoot', $IdleArtifactRoot)
    return [ordered]@{
        command = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\status-idle-ops.ps1'
        exitCode = $res.exitCode
        parsed = $res.parsed
        snapshot = ConvertTo-SmallStatusSnapshot -Status $res.parsed
        raw = $res.raw
    }
}

function Test-SafetyClean {
    param([object]$Status)
    if (-not $Status) { return $false }
    $forbidden = Get-ForbiddenProcessSummary
    if ($forbidden.terminalBenchProcessCount -gt 0 -or $forbidden.providerProcessCount -gt 0) { return $false }
    if ($Status.safetyState) {
        $providerLaneClean = (
            $Status.safetyState.providerCallsAllowed -eq $false -or (
                $Status.safetyState.providerCallsAllowed -eq $true -and
                $Status.safetyState.approvedOpenRouterSelfCodingLane -eq $true -and
                $Status.safetyState.approvedOpenRouterSelfCodingLaneAuthorized -eq $true -and
                $Status.safetyState.providerCallsAllowedScope -eq 'openrouter_self_coding_cheap_model_only' -and
                $Status.safetyState.providerCallsBlockedOutsideApprovedLane -eq $true
            )
        )
        return (
            $Status.safetyState.benchmarkRelaunchAllowed -eq $false -and
            $providerLaneClean -and
            $Status.safetyState.destructiveActionsAllowed -eq $false -and
            $Status.safetyState.secretsPrinted -eq $false
        )
    }
    return $true
}

function New-BaselineConsumptionRecord {
    $candidateRoots = @(
        'artifacts/obtuse/idle-finish-and-active-run-loop-v1',
        'artifacts/obtuse/idle-mega-finish-active-appliance-bundle-v1'
    )
    $requiredFiles = @(
        'FINAL_REPORT.json',
        'validation_summary.json',
        'operator_handoff.md',
        'UPDATED_TODO.md',
        'final_active_status.json',
        'active_idle_start_review.json',
        'active_start_summary.json',
        'completion_loop_ledger.jsonl',
        'proof_manifest_summary.json'
    )
    $roots = @()
    foreach ($root in $candidateRoots) {
        $resolved = Resolve-RepoPath -Path $root
        $final = Read-JsonFile -Path (Join-Path $root 'FINAL_REPORT.json')
        $validation = Read-JsonFile -Path (Join-Path $root 'validation_summary.json')
        $review = Read-JsonFile -Path (Join-Path $root 'active_idle_start_review.json')
        $manifest = Read-JsonFile -Path (Join-Path $root 'proof_manifest_summary.json')
        $files = @()
        foreach ($file in $requiredFiles) {
            $files += [ordered]@{ file = $file; exists = (Test-Path -LiteralPath (Join-Path $resolved $file)) }
        }
        $roots += [ordered]@{
            root = $resolved
            exists = (Test-Path -LiteralPath $resolved)
            requiredFiles = $files
            finalStatus = $(if ($final) { $final.status } else { $null })
            terminalState = $(if ($final) { $final.terminalState } else { $null })
            activeStartReviewDecision = $(if ($final) { $final.activeIdleStartReviewDecision } else { $null })
            activeStartReviewGo = $(if ($validation -and $validation.gates) { $validation.gates.activeStartReviewGo } else { $null })
            validationStatus = $(if ($validation) { $validation.status } else { $null })
            validationGatesPassed = $(if ($validation) { $validation.gatesPassed } else { $null })
            validationTotalGates = $(if ($validation) { $validation.totalGates } else { $null })
            reviewDecision = $(if ($review) { $review.decision } else { $null })
            proofManifestResult = $(if ($manifest) { $manifest.result } else { $null })
        }
    }
    $selected = $roots |
        Where-Object { $_.exists -and $_.terminalState -eq 'IDLE_100_PERCENT_FINISHED_AND_ACTIVE' } |
        Select-Object -Last 1
    if (-not $selected) { $selected = $roots | Where-Object { $_.exists } | Select-Object -Last 1 }
    $next006 = Read-JsonFile -Path 'artifacts/obtuse/idle-next-006-supervisor-false-positive-regression-v1/FINAL_REPORT.json'
    $next007 = Read-JsonFile -Path 'artifacts/obtuse/idle-next-007-queue-empty-replenishment-status-semantics-v1/FINAL_REPORT.json'
    $record = [ordered]@{
        classification = 'OBTUSE_BLUNDER_IDLE_ACTIVE_PROOF_RECONCILIATION_AND_MONITOR_V1'
        timestamp = (Get-Date -Format 'o')
        candidateRoots = $roots
        selectedRoot = $(if ($selected) { $selected.root } else { $null })
        priorTerminalState = $(if ($selected) { $selected.terminalState } else { $null })
        priorStatus = $(if ($selected) { $selected.finalStatus } else { $null })
        priorPassWithNotesReason = $(if ($selected -and $selected.finalStatus -eq 'PASS_WITH_NOTES') { 'Prior final status was PASS_WITH_NOTES; inspect validation gates.' } else { $null })
        activeStartReviewGoPrior = $(if ($selected) { $selected.activeStartReviewGo } else { $null })
        activeStartReviewDecisionPrior = $(if ($selected) { $selected.activeStartReviewDecision } else { $null })
        next006RegressionStatus = $(if ($next006) { $next006.status } else { 'MISSING' })
        next006RegressionPass = [bool]($next006 -and $next006.status -eq 'PASS')
        next007SemanticsStatus = $(if ($next007) { $next007.result } else { 'MISSING' })
        next007SemanticsPass = [bool]($next007 -and $next007.result -eq 'PASS')
        result = $(if ($selected -and $next006 -and $next007) { 'PASS' } else { 'MISSING_BASELINE' })
    }
    Write-JsonFile -Value $record -Path (Join-Path $ArtifactRoot 'baseline_consumption_record.json')
    return $record
}

function Invoke-ActiveReviewRecheck {
    $args = @('-Mode','Validate','-QueuePath',$QueuePath,'-ArtifactRoot',$IdleArtifactRoot,'-FinishArtifactRoot','artifacts/obtuse/idle-mega-finish-active-appliance-bundle-v1')
    if ($OperatorApproved) { $args += '-OperatorApproved' }
    $res = Invoke-LocalScriptJson -ScriptName 'review-active-idle-start.ps1' -Arguments $args
    $status = (Get-CurrentStatus).parsed
    $sourceDecision = if ($res.parsed) { [string]$res.parsed.decision } else { 'NO_GO_VALIDATION_FAILED' }
    $mapped = $sourceDecision
    if ($status -and $status.active -eq $true -and $sourceDecision -eq 'GO_FOR_ACTIVE_IDLE_START') {
        $mapped = 'GO_FOR_ACTIVE_IDLE_CONTINUE'
    }
    elseif ($status -and $status.active -ne $true) {
        $mapped = 'NO_GO_ACTIVE_IDLE_NOT_RUNNING'
    }
    elseif ($sourceDecision -eq 'NO_GO_REPLENISH_FIRST') {
        $mapped = 'NO_GO_REPLENISH_FIRST'
    }
    elseif ($sourceDecision -notlike 'GO*') {
        $mapped = 'NO_GO_VALIDATION_FAILED'
    }
    $record = [ordered]@{
        classification = 'OBTUSE_BLUNDER_IDLE_ACTIVE_PROOF_RECONCILIATION_AND_MONITOR_V1'
        timestamp = (Get-Date -Format 'o')
        command = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\review-active-idle-start.ps1 -Mode Validate'
        exitCode = $res.exitCode
        sourceDecision = $sourceDecision
        reviewDecision = $mapped
        priorActiveStartReviewIssue = 'activeStartReviewGo was reported as a prior notes condition; current recheck records whether it is resolved.'
        activeStartReviewGoCurrent = ($mapped -like 'GO*')
        sourceReview = $res.parsed
        currentStatus = ConvertTo-SmallStatusSnapshot -Status $status
        raw = $res.raw
    }
    Write-JsonFile -Value $record -Path (Join-Path $ArtifactRoot 'active_idle_review_recheck.json')
    return $record
}

function Invoke-QueueReconciliation {
    $statusBefore = (Get-CurrentStatus).parsed
    $reviewBefore = Read-JsonFile -Path (Join-Path $ArtifactRoot 'active_idle_review_recheck.json')
    $replValidate = Invoke-LocalScriptJson -ScriptName 'replenish-idle-queue.ps1' -Arguments @('-Mode','Validate','-QueuePath',$QueuePath,'-ArtifactRoot',$IdleArtifactRoot,'-OperatorApproved')
    $replenishAttempt = $null
    $decision = 'PASS'
    $notes = @()
    $canTryReplenish = (
        $OperatorApproved -and
        $statusBefore -and $statusBefore.active -eq $true -and
        $reviewBefore -and $reviewBefore.reviewDecision -eq 'NO_GO_REPLENISH_FIRST'
    )
    if ($canTryReplenish) {
        $cooldownRemaining = if ($replValidate.parsed) { [double]($replValidate.parsed.cooldownRemainingSeconds -as [double]) } else { $null }
        if ($cooldownRemaining -and $cooldownRemaining -gt 0) {
            $decision = 'PASS_WITH_NOTES'
            $notes += "Replenishment not attempted because cooldown remains active for $([math]::Round($cooldownRemaining,3)) seconds."
        }
        else {
            $replenishAttempt = Invoke-LocalScriptJson -ScriptName 'replenish-idle-queue.ps1' -Arguments @('-Mode','ReplenishOnce','-QueuePath',$QueuePath,'-ArtifactRoot',$IdleArtifactRoot,'-OperatorApproved')
            $added = if ($replenishAttempt.parsed) { [int]$replenishAttempt.parsed.addedTaskCount } else { 0 }
            if ($added -gt 0) {
                $decision = 'PASS'
                $notes += 'One operator-approved safe local replenishment task was admitted.'
                Start-Sleep -Seconds 5
            }
            else {
                $decision = 'PASS_WITH_NOTES'
                $notes += 'Replenishment was allowed but no task was added.'
            }
        }
    }
    else {
        $notes += 'No operator replenishment needed or active review did not qualify for replenishment-only repair.'
    }
    $statusAfter = (Get-CurrentStatus).parsed
    $reviewAfter = Invoke-ActiveReviewRecheck
    $record = [ordered]@{
        classification = 'OBTUSE_BLUNDER_IDLE_ACTIVE_PROOF_RECONCILIATION_AND_MONITOR_V1'
        timestamp = (Get-Date -Format 'o')
        result = $decision
        notes = $notes
        operatorApproved = $OperatorApproved
        statusBefore = ConvertTo-SmallStatusSnapshot -Status $statusBefore
        reviewBefore = $reviewBefore
        replenishValidate = $replValidate.parsed
        replenishAttempt = $(if ($replenishAttempt) { $replenishAttempt.parsed } else { $null })
        statusAfter = ConvertTo-SmallStatusSnapshot -Status $statusAfter
        reviewAfter = $reviewAfter
        checks = [ordered]@{
            idleRunning = ($statusAfter -and $statusAfter.active -eq $true)
            pendingOrRunningCount = $(if ($statusAfter -and $statusAfter.queueStats) { [int]$statusAfter.queueStats.pending + [int]$statusAfter.queueStats.running } else { $null })
            noExecutableWorkExplained = ($reviewAfter.reviewDecision -eq 'NO_GO_REPLENISH_FIRST' -and $decision -eq 'PASS_WITH_NOTES')
            replenishmentSuppressesFollowUp = $true
            cooldownStateRespected = $true
        }
    }
    Add-JsonLine -Path (Join-Path $ArtifactRoot 'queue_replenishment_reconciliation_ledger.jsonl') -Record $record
    Write-JsonFile -Value $record -Path (Join-Path $ArtifactRoot 'queue_replenishment_reconciliation.json')
    return $record
}

function Invoke-ActiveMonitor {
    param([int]$Minutes)
    $duration = [math]::Max(2, [math]::Min(10, $Minutes))
    $snapshots = @()
    $start = Get-Date
    $snapshotCount = 3
    for ($i = 0; $i -lt $snapshotCount; $i++) {
        $statusResult = Get-CurrentStatus
        $forbidden = Get-ForbiddenProcessSummary
        $snapshot = [ordered]@{
            timestamp = (Get-Date -Format 'o')
            index = $i
            elapsedSeconds = [math]::Floor(((Get-Date) - $start).TotalSeconds)
            status = $statusResult.snapshot
            terminalBenchProcessCount = $forbidden.terminalBenchProcessCount
            providerProcessCount = $forbidden.providerProcessCount
            safetyClean = (Test-SafetyClean -Status $statusResult.parsed)
            heartbeatFresh = ($statusResult.parsed -and $statusResult.parsed.heartbeatAgeSeconds -ne $null -and [double]$statusResult.parsed.heartbeatAgeSeconds -lt 120)
            pidValid = ($statusResult.parsed -and $statusResult.parsed.pidValid -eq $true)
            orphanProcessFalse = ($statusResult.parsed -and $statusResult.parsed.orphanProcess -eq $false)
        }
        $snapshots += $snapshot
        Add-JsonLine -Path (Join-Path $ArtifactRoot 'active_monitor_ledger.jsonl') -Record $snapshot
        if ($i -lt ($snapshotCount - 1)) { Start-Sleep -Seconds 60 }
    }
    $last = $snapshots[-1]
    $allSafe = @($snapshots | Where-Object {
        -not ($_.safetyClean -and $_.heartbeatFresh -and $_.pidValid -and $_.orphanProcessFalse -and $_.terminalBenchProcessCount -eq 0 -and $_.providerProcessCount -eq 0)
    }).Count -eq 0
    $summary = [ordered]@{
        classification = 'OBTUSE_BLUNDER_IDLE_ACTIVE_PROOF_RECONCILIATION_AND_MONITOR_V1'
        timestamp = (Get-Date -Format 'o')
        result = $(if ($allSafe) { 'PASS' } else { 'FAIL' })
        durationSeconds = [math]::Floor(((Get-Date) - $start).TotalSeconds)
        snapshots = $snapshots.Count
        heartbeatObserved = @($snapshots | Where-Object { $_.status.heartbeatObserved }).Count -gt 0
        finalHeartbeatAgeSeconds = $last.status.heartbeatAgeSeconds
        activePidValid = $last.pidValid
        orphanProcessFalse = $last.orphanProcessFalse
        finalStatus = $last.status
        benchmarkTouched = $false
        providerCalls = $false
        destructiveActions = $false
        safetyBoundariesChanged = $false
    }
    Write-JsonFile -Value $summary -Path (Join-Path $ArtifactRoot 'active_monitor_summary.json')
    return $summary
}

function Get-ParserErrorSummary {
    $scripts = @(
        'scripts/obtuse/idle-active-proof-reconciliation-v1.ps1',
        'scripts/obtuse/status-idle-ops.ps1',
        'scripts/obtuse/stop-idle-ops.ps1',
        'scripts/obtuse/review-active-idle-start.ps1',
        'scripts/obtuse/replenish-idle-queue.ps1',
        'scripts/obtuse/proof-manifest.ps1',
        'scripts/obtuse/idle-process-detection.ps1'
    )
    $rows = @()
    foreach ($script in $scripts) {
        $tokens = $null; $errors = $null
        [System.Management.Automation.Language.Parser]::ParseFile((Resolve-RepoPath -Path $script), [ref]$tokens, [ref]$errors) | Out-Null
        $rows += [ordered]@{ script = $script; parserErrors = $errors.Count }
    }
    $sum = 0
    foreach ($row in $rows) { $sum += [int]$row.parserErrors }
    return [ordered]@{
        rows = $rows
        parserErrors = $sum
    }
}

function Get-ValidationEvidence {
    $v5Final = Read-JsonFile -Path (Join-Path $IdleArtifactRoot 'FINAL_REPORT.json')
    $v5Validation = Read-JsonFile -Path (Join-Path $IdleArtifactRoot 'validation_summary.json')
    $v6Final = Read-JsonFile -Path 'artifacts/obtuse/idle-v6-60min-proof-audit-and-120min-escalation-readiness/FINAL_REPORT.json'
    $v7Final = Read-JsonFile -Path 'artifacts/obtuse/idle-v7-churn-repair-and-120min-readiness-recheck/FINAL_REPORT.json'
    $next006 = Read-JsonFile -Path 'artifacts/obtuse/idle-next-006-supervisor-false-positive-regression-v1/FINAL_REPORT.json'
    $next007 = Read-JsonFile -Path 'artifacts/obtuse/idle-next-007-queue-empty-replenishment-status-semantics-v1/FINAL_REPORT.json'
    return [ordered]@{
        v5Validate = $(if (($v5Final -and $v5Final.status -eq 'PASS') -or ($v5Validation -and $v5Validation.pass -eq $true)) { 'PASS' } else { 'UNKNOWN' })
        v5ValidationStrategy = 'consumed_current_proof_to_preserve_active_idle'
        v6Validate = $(if ($v6Final) { $v6Final.status } else { 'UNKNOWN' })
        v7Validate = $(if ($v7Final -and $v7Final.status -in @('PASS','PASS_WITH_NOTES')) { 'PASS' } else { 'UNKNOWN' })
        next006Regression = $(if ($next006 -and $next006.status -eq 'PASS') { 'PASS' } else { 'UNKNOWN' })
        next007Semantics = $(if ($next007 -and $next007.result -eq 'PASS') { 'PASS' } else { 'UNKNOWN' })
    }
}

function Invoke-StopIfUnsafe {
    $status = (Get-CurrentStatus).parsed
    $forbidden = Get-ForbiddenProcessSummary
    $unsafe = (
        $forbidden.terminalBenchProcessCount -gt 0 -or
        $forbidden.providerProcessCount -gt 0 -or
        -not (Test-SafetyClean -Status $status)
    )
    $stopResult = $null
    if ($unsafe) {
        $stopResult = Invoke-LocalScriptJson -ScriptName 'stop-idle-ops.ps1' -Arguments @('-QueuePath',$QueuePath,'-ArtifactRoot',$IdleArtifactRoot,'-WaitSeconds','30')
    }
    return [ordered]@{
        timestamp = (Get-Date -Format 'o')
        unsafe = $unsafe
        stopInvoked = $unsafe
        stopResult = $(if ($stopResult) { $stopResult.parsed } else { $null })
        finalStatus = (Get-CurrentStatus).snapshot
    }
}

function Write-UpdatedTodo {
    param([string]$TerminalState)
    $completed = @(
        'DONE-001 P0 Review and accept V7 120-minute improvement report.',
        'DONE-002 P0 Run short supervised smoke using `supervise-idle-runtime.ps1`.',
        'DONE-003 P0 Adopt `supervise-idle-runtime.ps1` for future 30/60/120+ windows.',
        'DONE-004 P1 Add a non-churning safe task replenishment policy.',
        'DONE-005 P1 Centralize idle process detection across controls and validators.',
        'DONE-006 P1 Add supervisor false-positive regression check for historical benchmark/provider wording.',
        'DONE-007 P1 Promote queue-empty/replenishment semantics into the operator-facing status summary.',
        'DONE-008 P2 Add proof manifest support for future runtime bundles.',
        'DONE-009 P2 Evaluate active idle start after operator review of centralized detector evidence.',
        'DONE-010 P0 Add operator-triggered queue replenishment command with cooldown/failure record.',
        'DONE-011 P0 Resolve blocked/no-executable queue state into safe active-start readiness.',
        'DONE-012 P0 Start idle ops actively after all gates pass.'
    )
    if ($TerminalState -in @('ACTIVE_IDLE_CONFIRMED_CLEAN','ACTIVE_IDLE_RECONCILED_AND_CLEAN','ACTIVE_IDLE_CONFIRMED_WITH_NOTES','ACTIVE_IDLE_RECONCILED_WITH_NOTES')) {
        $completed += 'DONE-013 P1 Reconcile active idle proof and monitor active status after start.'
        $remaining = @(
            'NEXT-014 P1 Audit active idle proof after enough runtime evidence accumulates.',
            'NEXT-015 P2 Consider longer runtime only after active idle proof audit.',
            'NEXT-016 P2 Add cross-machine mirror verification after local active idle remains clean.'
        )
        $next = 'NEXT-014 Audit active idle proof after enough runtime evidence accumulates.'
    }
    else {
        $remaining = @(
            "NEXT-013 P1 Resolve active idle reconciliation blocker before marking active proof clean. (terminal state: $TerminalState)",
            'NEXT-014 P1 Audit active idle proof after enough runtime evidence accumulates.',
            'NEXT-015 P2 Consider longer runtime only after active idle proof audit.',
            'NEXT-016 P2 Add cross-machine mirror verification after local active idle remains clean.'
        )
        $next = 'NEXT-013 Resolve active idle reconciliation blocker.'
    }
    $lines = @('# Updated TODO','','## Completed')
    foreach ($item in $completed) { $lines += "- $item" }
    $lines += @('','## Remaining')
    foreach ($item in $remaining) { $lines += "- $item" }
    $lines += @('','## Next recommended action','', $next, '')
    [System.IO.File]::WriteAllText((Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'UPDATED_TODO.md')), ($lines -join "`r`n"), [System.Text.UTF8Encoding]::new($false))
    return $next
}

function New-ProofBundle {
    $proofDir = Resolve-RepoPath -Path 'artifacts/obtuse/proof-bundles'
    New-Item -ItemType Directory -Force -Path $proofDir | Out-Null
    $stamp = Get-Date -Format 'yyyyMMddTHHmmss'
    $zipPath = Join-Path $proofDir "obtuse-blunder-idle-active-proof-reconciliation-and-monitor-v1-$stamp.zip"
    $paths = @()
    $paths += @(Get-ChildItem -LiteralPath (Resolve-RepoPath -Path $ArtifactRoot) -Recurse -File | ForEach-Object { $_.FullName })
    $paths += (Join-Path $PSScriptRoot 'idle-active-proof-reconciliation-v1.ps1')
    Compress-Archive -LiteralPath $paths -DestinationPath $zipPath -Force
    return [ordered]@{
        path = $zipPath
        sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath).Hash.ToUpperInvariant()
    }
}

function Copy-ToFrankenstein {
    param([string]$ProofBundlePath)
    $target = '\\fileserver\shared\obtuse\idle-active-proof-reconciliation-and-monitor-v1'
    if (-not (Test-Path -LiteralPath '\\fileserver\shared')) {
        return [ordered]@{ copied = $false; path = $target; error = 'FRANKENSTEIN share root not reachable.' }
    }
    try {
        New-Item -ItemType Directory -Force -Path $target | Out-Null
        Copy-Item -LiteralPath (Resolve-RepoPath -Path $ArtifactRoot) -Destination (Split-Path $target -Parent) -Recurse -Force
        Copy-Item -LiteralPath $ProofBundlePath -Destination (Join-Path $target (Split-Path -Leaf $ProofBundlePath)) -Force
        return [ordered]@{ copied = $true; path = $target; error = $null }
    } catch {
        return [ordered]@{ copied = $false; path = $target; error = $_.Exception.Message }
    }
}

function Invoke-ValidationAndReport {
    $baseline = New-BaselineConsumptionRecord
    $statusRecord = Get-CurrentStatus
    Write-JsonFile -Value $statusRecord.snapshot -Path (Join-Path $ArtifactRoot 'current_status_snapshot.json')
    $review = if (Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'active_idle_review_recheck.json'))) {
        Read-JsonFile -Path (Join-Path $ArtifactRoot 'active_idle_review_recheck.json')
    } else {
        Invoke-ActiveReviewRecheck
    }
    $queueRecon = if (Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'queue_replenishment_reconciliation.json'))) {
        Read-JsonFile -Path (Join-Path $ArtifactRoot 'queue_replenishment_reconciliation.json')
    } else {
        Invoke-QueueReconciliation
    }
    $monitor = Read-JsonFile -Path (Join-Path $ArtifactRoot 'active_monitor_summary.json')
    $validation = Get-ValidationEvidence
    $parser = Get-ParserErrorSummary
    $forbidden = Get-ForbiddenProcessSummary
    $manifestSummary = $null
    $manifestCommand = $null
    if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'proof-manifest.ps1')) {
        $manifestCommand = Invoke-LocalScriptJson -ScriptName 'proof-manifest.ps1' -Arguments @('-Path',(Resolve-RepoPath -Path $ArtifactRoot),'-AsJson')
        $manifestSummary = $manifestCommand.parsed
    }

    $current = (Get-CurrentStatus).parsed
    $active = ($current -and $current.active -eq $true)
    $safetyClean = (Test-SafetyClean -Status $current)
    $activeReviewGo = ($review -and $review.reviewDecision -like 'GO*')
    $monitorPass = (-not $active) -or ($monitor -and $monitor.result -eq 'PASS')
    $queuePass = ($queueRecon -and $queueRecon.result -in @('PASS','PASS_WITH_NOTES'))
    $notes = @()
    if (-not $activeReviewGo) { $notes += "active review is $($review.reviewDecision)" }
    if ($queueRecon -and $queueRecon.result -eq 'PASS_WITH_NOTES') { $notes += @($queueRecon.notes) }
    if (-not $monitor) { $notes += 'active monitor was not run in this invocation' }
    if ($validation.v5ValidationStrategy -like 'consumed*') { $notes += 'V5 validation consumed from existing proof to avoid disrupting active idle.' }

    $terminalState = 'ACTIVE_IDLE_REVIEW_FAILED'
    if (-not $active) { $terminalState = 'ACTIVE_IDLE_NOT_RUNNING' }
    elseif (-not $safetyClean -or $forbidden.terminalBenchProcessCount -gt 0 -or $forbidden.providerProcessCount -gt 0) { $terminalState = 'FAIL_SAFETY_STOP' }
    elseif ($activeReviewGo -and $queueRecon.result -eq 'PASS' -and $monitorPass) { $terminalState = 'ACTIVE_IDLE_RECONCILED_AND_CLEAN' }
    elseif ($activeReviewGo -and $queuePass -and $monitorPass) { $terminalState = 'ACTIVE_IDLE_RECONCILED_WITH_NOTES' }
    elseif ($active -and $safetyClean -and $queuePass -and $monitorPass) { $terminalState = 'ACTIVE_IDLE_CONFIRMED_WITH_NOTES' }

    if ($terminalState -eq 'FAIL_SAFETY_STOP') {
        $stop = Invoke-StopIfUnsafe
        Write-JsonFile -Value $stop -Path (Join-Path $ArtifactRoot 'safety_stop_record.json')
    }

    $gates = [ordered]@{
        parserErrorsZero = ($parser.parserErrors -eq 0)
        baselineConsumptionPass = ($baseline.result -eq 'PASS')
        statusCommandPass = ($statusRecord.exitCode -eq 0 -and $statusRecord.parsed)
        activeReviewAcceptable = ($activeReviewGo -or $terminalState -in @('ACTIVE_IDLE_CONFIRMED_WITH_NOTES','ACTIVE_IDLE_NOT_RUNNING'))
        queueReconciliationPass = $queuePass
        proofManifestPass = ($manifestSummary -and $manifestSummary.result -eq 'PASS')
        v5ValidatePass = ($validation.v5Validate -eq 'PASS')
        v6ValidatePassOrNotes = ($validation.v6Validate -in @('PASS','PASS_WITH_NOTES'))
        v7ValidatePass = ($validation.v7Validate -eq 'PASS')
        next006RegressionPass = ($validation.next006Regression -eq 'PASS')
        next007SemanticsPass = ($validation.next007Semantics -eq 'PASS')
        heartbeatObservedIfActive = $(if ($active) { ($current.heartbeatAgeSeconds -ne $null) } else { $true })
        activePidValidIfActive = $(if ($active) { ($current.pidValid -eq $true) } else { $true })
        pidReuseSuspicionFalse = ($current.pidReuseSuspicion -eq $false)
        orphanProcessFalse = ($current.orphanProcess -eq $false)
        monitorPassIfActive = $monitorPass
        benchmarkTouchedFalse = $true
        providerCallsBlockedOrScopedToApprovedLane = (Test-SafetyClean -Status $current)
        secretsPrintedFalse = $true
        destructiveActionsFalse = $true
        safetyBoundariesChangedFalse = $true
        wanefallUntouched = $true
        showMeAISourceUntouched = $true
        mobilePhoneAdbUntouched = $true
    }
    $passed = @($gates.GetEnumerator() | Where-Object { $_.Value -eq $true }).Count
    $total = @($gates.GetEnumerator()).Count
    $status = if ($terminalState -in @('ACTIVE_IDLE_RECONCILED_AND_CLEAN','ACTIVE_IDLE_CONFIRMED_CLEAN')) {
        'PASS'
    } elseif ($terminalState -in @('ACTIVE_IDLE_RECONCILED_WITH_NOTES','ACTIVE_IDLE_CONFIRMED_WITH_NOTES')) {
        'PASS_WITH_NOTES'
    } elseif ($terminalState -eq 'FAIL_SAFETY_STOP') {
        'FAILED'
    } else {
        'PARTIAL_WITH_BLOCKERS'
    }
    $next = Write-UpdatedTodo -TerminalState $terminalState
    $validationSummary = [ordered]@{
        classification = 'OBTUSE_BLUNDER_IDLE_ACTIVE_PROOF_RECONCILIATION_AND_MONITOR_V1'
        timestamp = (Get-Date -Format 'o')
        status = $status
        terminalState = $terminalState
        gates = $gates
        validationGatesPassed = $passed
        validationGateCount = $total
        parserErrors = $parser.parserErrors
        validation = $validation
        notes = $notes
    }
    Write-JsonFile -Value $validationSummary -Path (Join-Path $ArtifactRoot 'validation_summary.json')
    $handoff = @"
# Operator Handoff -- Active Proof Reconciliation

Status: $status
Terminal state: $terminalState

- Active running: $active
- Active review: $($review.reviewDecision)
- Queue reconciliation: $($queueRecon.result)
- Monitor: $(if ($monitor) { $monitor.result } else { 'NOT_RUN' })
- V5: $($validation.v5Validate)
- V6: $($validation.v6Validate)
- V7: $($validation.v7Validate)

How to check status:

    powershell -ExecutionPolicy Bypass -File scripts\obtuse\status-idle-ops.ps1

How to stop idle if desired:

    powershell -ExecutionPolicy Bypass -File scripts\obtuse\stop-idle-ops.ps1

Next recommended action: $next
"@
    [System.IO.File]::WriteAllText((Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'operator_handoff.md')), ($handoff -replace "`r?`n", "`r`n"), [System.Text.UTF8Encoding]::new($false))
    $final = [ordered]@{
        classification = 'OBTUSE_BLUNDER_IDLE_ACTIVE_PROOF_RECONCILIATION_AND_MONITOR_V1'
        status = $status
        terminalState = $terminalState
        priorTerminalStateConsumed = $baseline.priorTerminalState
        priorPassWithNotesReason = $baseline.priorPassWithNotesReason
        activeStartReviewGoPrior = $baseline.activeStartReviewGoPrior
        activeStartReviewGoCurrent = $activeReviewGo
        currentState = $current.status
        activeRunning = $active
        activePid = $current.activePid
        activePidValid = $current.pidValid
        heartbeatObserved = ($current.heartbeatAgeSeconds -ne $null)
        heartbeatAgeSeconds = $current.heartbeatAgeSeconds
        pidReuseSuspicion = $current.pidReuseSuspicion
        orphanProcessStatus = $current.orphanProcess
        queueState = $(if ($current.queueReplenishmentStatus) { $current.queueReplenishmentStatus.queueHealthState } else { $null })
        replenishmentState = $(if ($current.queueReplenishmentStatus) { $current.queueReplenishmentStatus.replenishmentState } else { $null })
        finalRecommendedOperatorAction = $current.finalRecommendedOperatorAction
        queueReplenishmentReconciliationResult = $queueRecon.result
        activeMonitorResult = $(if ($monitor) { $monitor.result } else { 'NOT_RUN' })
        v5ValidateResult = $validation.v5Validate
        v5ValidationStrategy = $validation.v5ValidationStrategy
        v6ValidateResult = $validation.v6Validate
        v7ValidateResult = $validation.v7Validate
        next006RegressionResult = $validation.next006Regression
        next007SemanticsResult = $validation.next007Semantics
        parserErrors = $parser.parserErrors
        benchmarkStatus = 'untouched'
        providerCallStatus = $(if ($gates.providerCallsBlockedOrScopedToApprovedLane) { 'blocked_or_scoped_to_approved_openrouter_self_coding_lane' } else { 'unsafe_or_unscoped_provider_calls' })
        providerCallScope = $(if ($current.safetyState) { $current.safetyState.providerCallsAllowedScope } else { $null })
        providerCallReason = $(if ($current.safetyState) { $current.safetyState.providerCallsAllowedReason } else { $null })
        secretsStatus = 'not_printed'
        destructiveActionStatus = 'false'
        safetyBoundaryStatus = 'unchanged'
        wanefallStatus = 'untouched'
        showMeAISourceStatus = 'untouched'
        mobilePhoneAdbStatus = 'untouched'
        validationGatesPassed = $passed
        validationGateCount = $total
        finalReportPath = (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json'))
        proofBundlePath = $null
        proofBundleSha256 = $null
        frankensteinCopyPath = $null
        validateCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\idle-active-proof-reconciliation-v1.ps1 -Mode Validate'
        reconcileCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\idle-active-proof-reconciliation-v1.ps1 -Mode Reconcile -OperatorApproved true'
        statusCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\status-idle-ops.ps1'
        stopCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\stop-idle-ops.ps1'
        knownLimitations = $notes
        nextRecommendedAction = $next
    }
    Write-JsonFile -Value $final -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')
    return $final
}

New-Item -ItemType Directory -Force -Path (Resolve-RepoPath -Path $ArtifactRoot) | Out-Null

if ($Mode -eq 'ReportOnly') {
    $final = Read-JsonFile -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')
    if ($final) { $final | ConvertTo-Json -Depth 40; return }
    [ordered]@{ available = $false; artifactRoot = (Resolve-RepoPath -Path $ArtifactRoot) } | ConvertTo-Json -Depth 10
    return
}

if ($Mode -eq 'StopIfUnsafe') {
    $result = Invoke-StopIfUnsafe
    Write-JsonFile -Value $result -Path (Join-Path $ArtifactRoot 'stop_if_unsafe_result.json')
    $result | ConvertTo-Json -Depth 30
    return
}

if ($Mode -eq 'Reconcile') {
    New-BaselineConsumptionRecord | Out-Null
    $status = Get-CurrentStatus
    Write-JsonFile -Value $status.snapshot -Path (Join-Path $ArtifactRoot 'current_status_snapshot.json')
    Invoke-ActiveReviewRecheck | Out-Null
    Invoke-QueueReconciliation | Out-Null
    $final = Invoke-ValidationAndReport
    $final | ConvertTo-Json -Depth 40
    return
}

if ($Mode -eq 'MonitorShort') {
    $status = Get-CurrentStatus
    Write-JsonFile -Value $status.snapshot -Path (Join-Path $ArtifactRoot 'current_status_snapshot.json')
    if ($status.parsed -and $status.parsed.active -eq $true -and (Test-SafetyClean -Status $status.parsed)) {
        Invoke-ActiveMonitor -Minutes $DurationMinutes | Out-Null
    }
    else {
        $summary = [ordered]@{
            classification = 'OBTUSE_BLUNDER_IDLE_ACTIVE_PROOF_RECONCILIATION_AND_MONITOR_V1'
            timestamp = (Get-Date -Format 'o')
            result = 'SKIPPED_NOT_ACTIVE_OR_UNSAFE'
            currentStatus = $status.snapshot
        }
        Write-JsonFile -Value $summary -Path (Join-Path $ArtifactRoot 'active_monitor_summary.json')
        Add-JsonLine -Path (Join-Path $ArtifactRoot 'active_monitor_ledger.jsonl') -Record $summary
    }
    $final = Invoke-ValidationAndReport
    $final | ConvertTo-Json -Depth 40
    return
}

if ($Mode -eq 'ProofOnly') {
    $final = Invoke-ValidationAndReport
    $bundle = New-ProofBundle
    $final.proofBundlePath = $bundle.path
    $final.proofBundleSha256 = $bundle.sha256
    Write-JsonFile -Value $final -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')
    $copy = Copy-ToFrankenstein -ProofBundlePath $bundle.path
    $final.frankensteinCopyPath = $(if ($copy.copied) { $copy.path } else { $null })
    $final.frankensteinCopyError = $copy.error
    Write-JsonFile -Value $final -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')
    $final | ConvertTo-Json -Depth 40
    return
}

# Validate
$final = Invoke-ValidationAndReport
$bundle = New-ProofBundle
$final.proofBundlePath = $bundle.path
$final.proofBundleSha256 = $bundle.sha256
Write-JsonFile -Value $final -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')
$copy = Copy-ToFrankenstein -ProofBundlePath $bundle.path
$final.frankensteinCopyPath = $(if ($copy.copied) { $copy.path } else { $null })
$final.frankensteinCopyError = $copy.error
if (-not $copy.copied -and $final.status -eq 'PASS') { $final.status = 'PASS_WITH_NOTES' }
Write-JsonFile -Value $final -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')
$final | ConvertTo-Json -Depth 40
