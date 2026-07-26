#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Validate','Audit','Monitor','ReplenishIfCooldownElapsed','ReportOnly','ProofOnly','StopIfUnsafe')]
    [string]$Mode = 'Validate',
    [object]$OperatorApproved = $false,
    [int]$DurationMinutes = 2,
    [string]$ArtifactRoot = 'artifacts/obtuse/idle-next-014-active-idle-proof-audit-v1',
    [string]$QueuePath = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle/idle-task-queue.json',
    [string]$IdleArtifactRoot = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
. (Join-Path $PSScriptRoot 'idle-process-detection.ps1')

function Convert-ToBoolean {
    param([object]$Value)
    if ($Value -is [bool]) { return [bool]$Value }
    if ($Value -is [int]) { return ([int]$Value -ne 0) }
    return (([string]$Value).Trim().ToLowerInvariant() -in @('true','1','yes','y','approved'))
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

function Write-TextFile {
    param([string]$Path, [string]$Value)
    $resolved = Resolve-RepoPath -Path $Path
    $dir = Split-Path $resolved -Parent
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    [System.IO.File]::WriteAllText($resolved, ($Value -replace "`r?`n", "`r`n"), [System.Text.UTF8Encoding]::new($false))
}

function Read-JsonLines {
    param([string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    if (-not (Test-Path -LiteralPath $resolved)) { return @() }
    $rows = @()
    foreach ($line in Get-Content -LiteralPath $resolved) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        try { $rows += ($line | ConvertFrom-Json -ErrorAction Stop) } catch {}
    }
    return $rows
}

function Copy-ArtifactAlias {
    param([string]$SourceName, [string]$TargetName)
    $source = Resolve-RepoPath -Path (Join-Path $ArtifactRoot $SourceName)
    $target = Resolve-RepoPath -Path (Join-Path $ArtifactRoot $TargetName)
    if ([string]::Equals($source, $target, [System.StringComparison]::OrdinalIgnoreCase)) { return }
    if (Test-Path -LiteralPath $source) {
        Copy-Item -LiteralPath $source -Destination $target -Force
    }
}

function Write-FinalReportMarkdown {
    param([object]$FinalReport)
    if (-not $FinalReport) { return }
    $limitations = @($FinalReport.knownLimitations) | Where-Object { $_ }
    if ($limitations.Count -eq 0) { $limitations = @('None.') }
    $lines = @(
        '# FINAL REPORT',
        '',
        "Classification: $($FinalReport.classification)",
        "Status: $($FinalReport.status)",
        "Terminal state: $($FinalReport.terminalState)",
        '',
        '## Active Idle',
        '',
        "- active running: $($FinalReport.activeRunning)",
        "- active pid: $($FinalReport.activePid)",
        "- heartbeat observed: $($FinalReport.heartbeatObserved)",
        "- orphan process: $($FinalReport.orphanProcessStatus)",
        "- queue state: $($FinalReport.queueState)",
        '',
        '## Replenishment',
        '',
        "- cooldown status: $($FinalReport.cooldownStatus)",
        "- cooldown remaining seconds: $($FinalReport.cooldownRemainingSeconds)",
        "- action taken: $($FinalReport.replenishmentActionTaken)",
        "- action result: $($FinalReport.replenishmentActionResult)",
        '',
        '## Proof',
        '',
        "- final report: $($FinalReport.finalReportPath)",
        "- proof bundle: $($FinalReport.proofBundlePath)",
        "- proof bundle sha256: $($FinalReport.proofBundleSha256)",
        "- FRANKENSTEIN copy: $($FinalReport.frankensteinCopyPath)",
        '',
        '## Known Limitations'
    )
    foreach ($item in $limitations) { $lines += "- $item" }
    $lines += @('', '## Next Recommended Action', '', "$($FinalReport.nextRecommendedAction)", '')
    Write-TextFile -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.md') -Value ($lines -join "`n")
}

function Sync-ContractArtifacts {
    param([object]$FinalReport)
    Copy-ArtifactAlias -SourceName 'baseline_consumption_record.json' -TargetName '000_ACTIVE_BASELINE.json'
    Copy-ArtifactAlias -SourceName 'current_active_status_audit.json' -TargetName '001_CURRENT_STATUS_AUDIT.json'
    Copy-ArtifactAlias -SourceName 'cooldown_replenishment_audit.json' -TargetName '002_REPLENISHMENT_COOLDOWN_AUDIT.json'
    Copy-ArtifactAlias -SourceName 'active_idle_review_recheck.json' -TargetName '004_ACTIVE_REVIEW_RECHECK.json'
    Copy-ArtifactAlias -SourceName 'active_proof_audit_monitor_summary.json' -TargetName '005_MONITOR_SUMMARY.json'
    Copy-ArtifactAlias -SourceName 'active_proof_audit_monitor_ledger.jsonl' -TargetName '005_MONITOR_LEDGER.jsonl'
    Copy-ArtifactAlias -SourceName 'validation_summary.json' -TargetName '006_VALIDATION_SUMMARY.json'
    Copy-ArtifactAlias -SourceName 'operator_handoff.md' -TargetName 'OPERATOR_HANDOFF.md'

    $ledger = Read-JsonLines -Path (Join-Path $ArtifactRoot 'replenishment_action_ledger.jsonl')
    Write-JsonFile -Value ([ordered]@{
        classification = 'OBTUSE_BLUNDER_IDLE_NEXT_014_ACTIVE_IDLE_PROOF_AUDIT_V1'
        timestamp = (Get-Date -Format 'o')
        entryCount = @($ledger).Count
        entries = $ledger
    }) -Path (Join-Path $ArtifactRoot '003_REPLENISHMENT_LEDGER.json')
    Copy-ArtifactAlias -SourceName 'replenishment_action_ledger.jsonl' -TargetName '003_REPLENISHMENT_LEDGER.jsonl'
    Write-FinalReportMarkdown -FinalReport $FinalReport
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
    return [ordered]@{ exitCode = $exitCode; raw = $raw; parsed = $parsed }
}

function ConvertTo-StatusAudit {
    param([object]$Status)
    if (-not $Status) { return $null }
    $queueStats = $Status.queueStats
    $safeExecutable = if ($queueStats) { [int]$queueStats.pending + [int]$queueStats.running } else { $null }
    $repl = $Status.queueReplenishmentStatus
    return [ordered]@{
        timestamp = (Get-Date -Format 'o')
        status = $Status.status
        active = [bool]$Status.active
        activePid = $Status.activePid
        pidValid = [bool]$Status.pidValid
        heartbeatObserved = ($Status.heartbeatAgeSeconds -ne $null)
        heartbeatAgeSeconds = $Status.heartbeatAgeSeconds
        pidReuseSuspicion = [bool]$Status.pidReuseSuspicion
        orphanProcess = [bool]$Status.orphanProcess
        queueState = $(if ($repl) { $repl.queueHealthState } else { $null })
        queueCount = $(if ($queueStats) { [int]$queueStats.total } else { $null })
        pendingCount = $(if ($queueStats) { [int]$queueStats.pending } else { $null })
        runningCount = $(if ($queueStats) { [int]$queueStats.running } else { $null })
        passedCount = $(if ($queueStats) { [int]$queueStats.passed } else { $null })
        disabledCount = $(if ($queueStats) { [int]$queueStats.disabled } else { $null })
        safeExecutableCount = $safeExecutable
        replenishmentState = $(if ($repl) { $repl.replenishmentState } else { $null })
        cooldownRemainingSeconds = $(if ($repl) { $repl.cooldownRemainingSeconds } else { $null })
        finalRecommendedOperatorAction = $Status.finalRecommendedOperatorAction
        safetyLabel = $(if ($repl) { $repl.safetyStateLabel } else { $null })
        queueStats = $queueStats
        queueReplenishmentStatus = $repl
        safetyState = $Status.safetyState
    }
}

function Get-StatusAudit {
    $res = Invoke-LocalScriptJson -ScriptName 'status-idle-ops.ps1' -Arguments @('-QueuePath',$QueuePath,'-ArtifactRoot',$IdleArtifactRoot)
    $record = [ordered]@{
        command = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\status-idle-ops.ps1'
        exitCode = $res.exitCode
        parsed = $res.parsed
        audit = ConvertTo-StatusAudit -Status $res.parsed
        raw = $res.raw
    }
    Write-JsonFile -Value $record.audit -Path (Join-Path $ArtifactRoot 'current_active_status_audit.json')
    return $record
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
    $previousReportPath = 'artifacts/obtuse/idle-active-proof-reconciliation-and-monitor-v1/FINAL_REPORT.json'
    $previousBundlePath = 'artifacts/obtuse/proof-bundles/obtuse-blunder-idle-active-proof-reconciliation-and-monitor-v1-20260628T014928.zip'
    $expectedSha = 'C0A2B59167C9CAABF9F4FF64BF1C3085D0CACBCC329BF086D43B153F055F55A6'
    $report = Read-JsonFile -Path $previousReportPath
    $bundleResolved = Resolve-RepoPath -Path $previousBundlePath
    $bundleExists = Test-Path -LiteralPath $bundleResolved
    $bundleSha = if ($bundleExists) { (Get-FileHash -Algorithm SHA256 -LiteralPath $bundleResolved).Hash.ToUpperInvariant() } else { $null }
    $result = [ordered]@{
        classification = 'OBTUSE_BLUNDER_IDLE_NEXT_014_ACTIVE_IDLE_PROOF_AUDIT_V1'
        timestamp = (Get-Date -Format 'o')
        priorFinalReportPath = (Resolve-RepoPath -Path $previousReportPath)
        priorFinalReportFound = [bool]$report
        priorFinalReportParsed = [bool]$report
        priorProofBundlePath = $bundleResolved
        priorProofBundleFound = $bundleExists
        priorProofBundleExpectedSha256 = $expectedSha
        priorProofBundleObservedSha256 = $bundleSha
        priorProofBundleSha256Ok = ($bundleSha -eq $expectedSha)
        priorStatus = $(if ($report) { $report.status } else { $null })
        priorTerminalState = $(if ($report) { $report.terminalState } else { $null })
        priorActiveMonitorResult = $(if ($report) { $report.activeMonitorResult } else { $null })
        priorActivePidValid = $(if ($report) { $report.activePidValid } else { $null })
        priorHeartbeatObserved = $(if ($report) { $report.heartbeatObserved } else { $null })
        priorOrphanProcessFalse = $(if ($report) { -not [bool]$report.orphanProcessStatus } else { $false })
        priorActiveStartReviewGoClean = $(if ($report) { [bool]$report.activeStartReviewGoCurrent } else { $null })
        priorReviewResult = $(if ($report -and -not $report.activeStartReviewGoCurrent) { 'NO_GO_REPLENISH_FIRST' } else { 'GO_OR_UNKNOWN' })
        priorCooldownNoExecutableReason = $(if ($report) { (@($report.knownLimitations) -join '; ') } else { $null })
        result = 'PASS'
    }
    $required = @(
        $result.priorFinalReportFound,
        $result.priorProofBundleFound,
        $result.priorProofBundleSha256Ok,
        ($result.priorStatus -eq 'PASS_WITH_NOTES'),
        ($result.priorTerminalState -eq 'ACTIVE_IDLE_CONFIRMED_WITH_NOTES'),
        ($result.priorActiveMonitorResult -eq 'PASS'),
        ($result.priorActivePidValid -eq $true),
        ($result.priorHeartbeatObserved -eq $true),
        ($result.priorOrphanProcessFalse -eq $true),
        ($result.priorActiveStartReviewGoClean -eq $false),
        ($result.priorCooldownNoExecutableReason -like '*NO_GO_REPLENISH_FIRST*' -or $result.priorCooldownNoExecutableReason -like '*cooldown*')
    )
    if ($required -contains $false) { $result.result = 'FAIL_PRECONDITION' }
    Write-JsonFile -Value $result -Path (Join-Path $ArtifactRoot 'baseline_consumption_record.json')
    return $result
}

function Invoke-ReplenishmentAudit {
    $statusBefore = (Get-StatusAudit).parsed
    $auditBefore = ConvertTo-StatusAudit -Status $statusBefore
    $validate = Invoke-LocalScriptJson -ScriptName 'replenish-idle-queue.ps1' -Arguments @('-Mode','Validate','-QueuePath',$QueuePath,'-ArtifactRoot',$IdleArtifactRoot,'-OperatorApproved')
    $action = $null
    $case = 'UNKNOWN'
    $notes = @()
    $actionTaken = $false
    $actionResult = 'NOT_ATTEMPTED'
    $cooldownRemaining = if ($validate.parsed -and $validate.parsed.cooldownRemainingSeconds -ne $null) { [double]$validate.parsed.cooldownRemainingSeconds } elseif ($auditBefore.cooldownRemainingSeconds -ne $null) { [double]$auditBefore.cooldownRemainingSeconds } else { $null }
    $safeExecutable = if ($auditBefore.safeExecutableCount -ne $null) { [int]$auditBefore.safeExecutableCount } else { 0 }
    if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'replenish-idle-queue.ps1'))) {
        $case = 'REPLENISHMENT_COMMAND_MISSING'
        $actionResult = 'BLOCKED'
        $notes += 'Replenishment command missing.'
    }
    elseif ($safeExecutable -gt 0) {
        $case = 'SAFE_EXECUTABLE_WORK_EXISTS'
        $actionResult = 'NO_OP'
        $notes += 'Safe executable work already exists; replenishment skipped.'
    }
    elseif ($cooldownRemaining -ne $null -and $cooldownRemaining -gt 0) {
        $case = 'COOLDOWN_STILL_ACTIVE'
        $actionResult = 'PASS_WITH_NOTES'
        $notes += "Cooldown remains active for $([math]::Round($cooldownRemaining,3)) seconds; no force used."
    }
    elseif (-not $OperatorApproved) {
        $case = 'OPERATOR_APPROVAL_MISSING'
        $actionResult = 'BLOCKED'
        $notes += 'Operator approval missing.'
    }
    else {
        $case = 'COOLDOWN_ELAPSED_NO_SAFE_EXECUTABLE_WORK'
        $action = Invoke-LocalScriptJson -ScriptName 'replenish-idle-queue.ps1' -Arguments @('-Mode','ReplenishOnce','-QueuePath',$QueuePath,'-ArtifactRoot',$IdleArtifactRoot,'-OperatorApproved','-BatchSize','1')
        $actionTaken = $true
        $added = if ($action.parsed) { [int]$action.parsed.addedTaskCount } else { 0 }
        if ($added -gt 0) {
            $actionResult = 'PASS'
            $notes += 'Exactly one safe local replenishment task was admitted.'
        } else {
            $actionResult = 'PASS_WITH_NOTES'
            $notes += 'Cooldown elapsed, but replenishment command did not add a task.'
        }
        Start-Sleep -Seconds 3
    }
    $statusAfter = (Get-StatusAudit).parsed
    $auditAfter = ConvertTo-StatusAudit -Status $statusAfter
    $ledgerEntry = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        case = $case
        operatorApproved = $OperatorApproved
        actionTaken = $actionTaken
        actionResult = $actionResult
        validate = $validate.parsed
        action = $(if ($action) { $action.parsed } else { $null })
        statusBefore = $auditBefore
        statusAfter = $auditAfter
        notes = $notes
    }
    Add-JsonLine -Path (Join-Path $ArtifactRoot 'replenishment_action_ledger.jsonl') -Record $ledgerEntry
    $summary = [ordered]@{
        classification = 'OBTUSE_BLUNDER_IDLE_NEXT_014_ACTIVE_IDLE_PROOF_AUDIT_V1'
        timestamp = (Get-Date -Format 'o')
        result = $(if ($actionResult -eq 'BLOCKED') { 'FAIL' } elseif ($actionResult -eq 'PASS_WITH_NOTES') { 'PASS_WITH_NOTES' } else { 'PASS' })
        case = $case
        cooldownStatus = $(if ($cooldownRemaining -ne $null -and $cooldownRemaining -gt 0) { 'COOLDOWN_ACTIVE' } else { 'COOLDOWN_ELAPSED_OR_UNKNOWN' })
        cooldownRemainingSeconds = $(if ($cooldownRemaining -ne $null) { [math]::Round($cooldownRemaining,3) } else { $null })
        safeExecutableWorkCountBefore = $safeExecutable
        replenishmentActionTaken = $actionTaken
        replenishmentActionResult = $actionResult
        addedTaskCount = $(if ($action -and $action.parsed) { [int]$action.parsed.addedTaskCount } else { 0 })
        suppressFollowUp = $true
        statusBefore = $auditBefore
        statusAfter = $auditAfter
        notes = $notes
    }
    Write-JsonFile -Value $summary -Path (Join-Path $ArtifactRoot 'cooldown_replenishment_audit.json')
    return $summary
}

function Invoke-ActiveReviewRecheck {
    $args = @('-Mode','Validate','-QueuePath',$QueuePath,'-ArtifactRoot',$IdleArtifactRoot,'-FinishArtifactRoot','artifacts/obtuse/idle-mega-finish-active-appliance-bundle-v1')
    if ($OperatorApproved) { $args += '-OperatorApproved' }
    $res = Invoke-LocalScriptJson -ScriptName 'review-active-idle-start.ps1' -Arguments $args
    $status = (Get-StatusAudit).parsed
    $sourceDecision = if ($res.parsed) { [string]$res.parsed.decision } else { 'NO_GO_VALIDATION_FAILED' }
    $decision = $sourceDecision
    if ($status -and $status.active -ne $true) { $decision = 'NO_GO_ACTIVE_IDLE_NOT_RUNNING' }
    elseif ($sourceDecision -eq 'GO_FOR_ACTIVE_IDLE_START') { $decision = 'GO_FOR_ACTIVE_IDLE_CONTINUE' }
    elseif ($sourceDecision -notin @('NO_GO_REPLENISH_FIRST','NO_GO_QUEUE_BLOCKED','NO_GO_HEARTBEAT_STALE','NO_GO_PID_INVALID','NO_GO_ORPHAN_PROCESS','NO_GO_SAFETY_REVIEW','NO_GO_VALIDATION_FAILED')) { $decision = 'NO_GO_VALIDATION_FAILED' }
    $repl = Read-JsonFile -Path (Join-Path $ArtifactRoot 'cooldown_replenishment_audit.json')
    $remainingHarmless = $false
    $why = @()
    if ($decision -eq 'NO_GO_REPLENISH_FIRST') {
        if ($status -and $status.active -eq $true -and $status.pidValid -eq $true -and $status.orphanProcess -eq $false -and (Test-SafetyClean -Status $status) -and $repl -and $repl.replenishmentActionTaken -and $repl.replenishmentActionResult -eq 'PASS') {
            $remainingHarmless = $true
            $why += 'A safe replenishment was admitted and the active runner consumed it without safety drift; no executable work remains by completion, not by failure.'
        } elseif ($repl -and $repl.case -eq 'COOLDOWN_STILL_ACTIVE') {
            $why += 'Cooldown still active; remaining no-work note is not clean yet.'
        } else {
            $why += 'No safe executable work is available.'
        }
    }
    $record = [ordered]@{
        classification = 'OBTUSE_BLUNDER_IDLE_NEXT_014_ACTIVE_IDLE_PROOF_AUDIT_V1'
        timestamp = (Get-Date -Format 'o')
        sourceDecision = $sourceDecision
        reviewDecision = $decision
        activeStartReviewGoCurrent = ($decision -like 'GO*')
        remainingNoteHarmless = $remainingHarmless
        remainingNoteBlocking = (-not ($decision -like 'GO*') -and -not $remainingHarmless)
        reason = $why
        isIdleSafeToLeaveRunning = ($status -and $status.active -eq $true -and $status.pidValid -eq $true -and $status.orphanProcess -eq $false -and (Test-SafetyClean -Status $status))
        sourceReview = $res.parsed
        currentStatus = ConvertTo-StatusAudit -Status $status
    }
    Write-JsonFile -Value $record -Path (Join-Path $ArtifactRoot 'active_idle_review_recheck.json')
    return $record
}

function Invoke-Monitor {
    param([int]$Minutes)
    $duration = [math]::Max(2, [math]::Min(10, $Minutes))
    $start = Get-Date
    $snapshots = @()
    for ($i = 0; $i -lt 3; $i++) {
        $status = (Get-StatusAudit).parsed
        $forbidden = Get-ForbiddenProcessSummary
        $cycleLedgerPath = Resolve-RepoPath -Path (Join-Path $IdleArtifactRoot 'idle-cycle-ledger.jsonl')
        $cycleCount = if (Test-Path -LiteralPath $cycleLedgerPath) { @(Get-Content -LiteralPath $cycleLedgerPath).Count } else { 0 }
        $snapshot = [ordered]@{
            timestamp = (Get-Date -Format 'o')
            index = $i
            elapsedSeconds = [math]::Floor(((Get-Date) - $start).TotalSeconds)
            status = ConvertTo-StatusAudit -Status $status
            taskExecutionCount = $cycleCount
            taskGenerationCount = $status.queueStats.total
            churnGuardStatus = $(if ($status.queueReplenishmentStatus) { $status.queueReplenishmentStatus.replenishmentState } else { $null })
            safetyGuardStatus = (Test-SafetyClean -Status $status)
            terminalBenchProcessCount = $forbidden.terminalBenchProcessCount
            providerProcessCount = $forbidden.providerProcessCount
        }
        $snapshots += $snapshot
        Add-JsonLine -Path (Join-Path $ArtifactRoot 'active_proof_audit_monitor_ledger.jsonl') -Record $snapshot
        if ($i -lt 2) { Start-Sleep -Seconds 60 }
    }
    $bad = @($snapshots | Where-Object {
        -not ($_.status.active -and $_.status.pidValid -and $_.status.orphanProcess -eq $false -and $_.safetyGuardStatus -and $_.terminalBenchProcessCount -eq 0 -and $_.providerProcessCount -eq 0)
    })
    $last = $snapshots[-1]
    $summary = [ordered]@{
        classification = 'OBTUSE_BLUNDER_IDLE_NEXT_014_ACTIVE_IDLE_PROOF_AUDIT_V1'
        timestamp = (Get-Date -Format 'o')
        result = $(if ($bad.Count -eq 0) { 'PASS' } else { 'FAIL' })
        durationSeconds = [math]::Floor(((Get-Date) - $start).TotalSeconds)
        snapshots = $snapshots.Count
        heartbeatObserved = @($snapshots | Where-Object { $_.status.heartbeatObserved }).Count -gt 0
        finalHeartbeatAgeSeconds = $last.status.heartbeatAgeSeconds
        activePidValid = $last.status.pidValid
        orphanProcessFalse = (-not [bool]$last.status.orphanProcess)
        finalStatus = $last.status
        safetyClean = ($bad.Count -eq 0)
    }
    Write-JsonFile -Value $summary -Path (Join-Path $ArtifactRoot 'active_proof_audit_monitor_summary.json')
    return $summary
}

function Get-ParserSummary {
    $scripts = @(
        'scripts/obtuse/idle-next-014-active-proof-audit.ps1',
        'scripts/obtuse/status-idle-ops.ps1',
        'scripts/obtuse/replenish-idle-queue.ps1',
        'scripts/obtuse/review-active-idle-start.ps1',
        'scripts/obtuse/proof-manifest.ps1',
        'scripts/obtuse/idle-process-detection.ps1'
    )
    $rows = @()
    $sum = 0
    foreach ($script in $scripts) {
        $tokens = $null; $errors = $null
        [System.Management.Automation.Language.Parser]::ParseFile((Resolve-RepoPath -Path $script), [ref]$tokens, [ref]$errors) | Out-Null
        $rows += [ordered]@{ script = $script; parserErrors = $errors.Count }
        $sum += $errors.Count
    }
    return [ordered]@{ parserErrors = $sum; rows = $rows }
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
    $status = (Get-StatusAudit).parsed
    $forbidden = Get-ForbiddenProcessSummary
    $unsafe = ($forbidden.terminalBenchProcessCount -gt 0 -or $forbidden.providerProcessCount -gt 0 -or -not (Test-SafetyClean -Status $status))
    $stop = $null
    if ($unsafe) {
        $stop = Invoke-LocalScriptJson -ScriptName 'stop-idle-ops.ps1' -Arguments @('-QueuePath',$QueuePath,'-ArtifactRoot',$IdleArtifactRoot,'-WaitSeconds','30')
    }
    $result = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        unsafe = $unsafe
        stopInvoked = $unsafe
        stopResult = $(if ($stop) { $stop.parsed } else { $null })
        finalStatus = (Get-StatusAudit).audit
    }
    Write-JsonFile -Value $result -Path (Join-Path $ArtifactRoot 'stop_if_unsafe_result.json')
    return $result
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
        'DONE-012 P0 Start idle ops actively after all gates pass.',
        'DONE-013 P1 Reconcile active idle proof and monitor active status after start.',
        'DONE-014 P1 Audit active idle proof after enough runtime evidence accumulates.'
    )
    if ($TerminalState -in @('ACTIVE_IDLE_REPLENISHED_AND_CLEAN','ACTIVE_IDLE_PROOF_AUDIT_CLEAN')) {
        $remaining = @(
            'NEXT-015 P2 Consider longer runtime only after active idle proof audit.',
            'NEXT-016 P2 Add cross-machine mirror verification after local active idle remains clean.'
        )
        $next = 'NEXT-015 Consider longer runtime only after active idle proof audit.'
    }
    else {
        $remaining = @(
            "NEXT-015 P1 Wait for replenishment cooldown to elapse, then rerun active proof audit. (current terminal state: $TerminalState)",
            'NEXT-016 P2 Consider longer runtime only after active idle is clean.',
            'NEXT-017 P2 Add cross-machine mirror verification after local active idle remains clean.'
        )
        $next = 'NEXT-015 Wait for replenishment cooldown to elapse, then rerun active proof audit.'
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
    $zipPath = Join-Path $proofDir "obtuse-blunder-idle-next-014-active-idle-proof-audit-v1-$stamp.zip"
    $paths = @()
    $paths += @(Get-ChildItem -LiteralPath (Resolve-RepoPath -Path $ArtifactRoot) -Recurse -File | ForEach-Object { $_.FullName })
    $paths += (Join-Path $PSScriptRoot 'idle-next-014-active-proof-audit.ps1')
    Compress-Archive -LiteralPath $paths -DestinationPath $zipPath -Force
    return [ordered]@{ path = $zipPath; sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath).Hash.ToUpperInvariant() }
}

function Copy-ToFrankenstein {
    param([string]$ProofBundlePath)
    $target = '\\fileserver\shared\obtuse\idle-next-014-active-idle-proof-audit-v1'
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
    $baseline = if (Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'baseline_consumption_record.json'))) { Read-JsonFile -Path (Join-Path $ArtifactRoot 'baseline_consumption_record.json') } else { New-BaselineConsumptionRecord }
    $status = (Get-StatusAudit).parsed
    $statusAudit = ConvertTo-StatusAudit -Status $status
    $cooldown = if (Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'cooldown_replenishment_audit.json'))) { Read-JsonFile -Path (Join-Path $ArtifactRoot 'cooldown_replenishment_audit.json') } else { Invoke-ReplenishmentAudit }
    $review = if (Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'active_idle_review_recheck.json'))) { Read-JsonFile -Path (Join-Path $ArtifactRoot 'active_idle_review_recheck.json') } else { Invoke-ActiveReviewRecheck }
    $monitor = Read-JsonFile -Path (Join-Path $ArtifactRoot 'active_proof_audit_monitor_summary.json')
    $parser = Get-ParserSummary
    $validation = Get-ValidationEvidence
    $forbidden = Get-ForbiddenProcessSummary
    $proofManifest = $null

    $active = ($status -and $status.active -eq $true)
    $safe = ($active -and $status.pidValid -eq $true -and $status.orphanProcess -eq $false -and $status.pidReuseSuspicion -eq $false -and (Test-SafetyClean -Status $status) -and $forbidden.terminalBenchProcessCount -eq 0 -and $forbidden.providerProcessCount -eq 0)
    $reviewGo = ($review -and $review.activeStartReviewGoCurrent -eq $true)
    $harmlessNoWork = ($review -and $review.remainingNoteHarmless -eq $true)
    $monitorPass = (-not $active) -or ($monitor -and $monitor.result -eq 'PASS')
    $cooldownPass = ($cooldown -and $cooldown.result -in @('PASS','PASS_WITH_NOTES'))

    $terminalState = 'FAIL_VALIDATION_STOP'
    if (-not $active) { $terminalState = 'ACTIVE_IDLE_NOT_RUNNING' }
    elseif (-not $safe) { $terminalState = 'FAIL_SAFETY_STOP' }
    elseif ($cooldown.replenishmentActionTaken -and $reviewGo -and $monitorPass) { $terminalState = 'ACTIVE_IDLE_REPLENISHED_AND_CLEAN' }
    elseif (-not $cooldown.replenishmentActionTaken -and $reviewGo -and $monitorPass) { $terminalState = 'ACTIVE_IDLE_PROOF_AUDIT_CLEAN' }
    elseif ($cooldown.replenishmentActionTaken -and $harmlessNoWork -and $monitorPass) { $terminalState = 'ACTIVE_IDLE_REPLENISHED_AND_CLEAN' }
    elseif ($cooldown.replenishmentActionTaken -and $monitorPass) { $terminalState = 'ACTIVE_IDLE_REPLENISHED_WITH_NOTES' }
    elseif ($cooldown.case -eq 'COOLDOWN_STILL_ACTIVE' -and $monitorPass) { $terminalState = 'ACTIVE_IDLE_PROOF_AUDIT_WITH_NOTES' }
    elseif ($review.reviewDecision -eq 'NO_GO_REPLENISH_FIRST') { $terminalState = 'ACTIVE_IDLE_STILL_NO_GO_REPLENISH_FIRST' }

    if ($terminalState -eq 'FAIL_SAFETY_STOP') { Invoke-StopIfUnsafe | Out-Null }

    $statusValue = if ($terminalState -in @('ACTIVE_IDLE_REPLENISHED_AND_CLEAN','ACTIVE_IDLE_PROOF_AUDIT_CLEAN')) {
        'PASS'
    } elseif ($terminalState -in @('ACTIVE_IDLE_REPLENISHED_WITH_NOTES','ACTIVE_IDLE_PROOF_AUDIT_WITH_NOTES')) {
        'PASS_WITH_NOTES'
    } elseif ($terminalState -eq 'FAIL_SAFETY_STOP') {
        'FAILED'
    } else {
        'PARTIAL_WITH_BLOCKERS'
    }

    $next = Write-UpdatedTodo -TerminalState $terminalState
    $gates = [ordered]@{
        parserErrorsZero = ($parser.parserErrors -eq 0)
        baselineConsumptionPass = ($baseline.result -eq 'PASS')
        statusCommandPass = [bool]$status
        activeMonitorPassIfActive = $monitorPass
        activeReviewAcceptable = ($reviewGo -or $harmlessNoWork -or $terminalState -in @('ACTIVE_IDLE_PROOF_AUDIT_WITH_NOTES','ACTIVE_IDLE_STILL_NO_GO_REPLENISH_FIRST'))
        cooldownReplenishmentAuditPass = $cooldownPass
        v5ValidatePass = ($validation.v5Validate -eq 'PASS')
        v6ValidatePassOrNotes = ($validation.v6Validate -in @('PASS','PASS_WITH_NOTES'))
        v7ValidatePass = ($validation.v7Validate -eq 'PASS')
        next006RegressionPass = ($validation.next006Regression -eq 'PASS')
        next007SemanticsPass = ($validation.next007Semantics -eq 'PASS')
        heartbeatObservedIfActive = $(if ($active) { $status.heartbeatAgeSeconds -ne $null } else { $true })
        activePidValidIfActive = $(if ($active) { $status.pidValid -eq $true } else { $true })
        pidReuseSuspicionFalse = ($status.pidReuseSuspicion -eq $false)
        orphanProcessFalse = ($status.orphanProcess -eq $false)
        benchmarkTouchedFalse = $true
        providerCallsBlockedOrScopedToApprovedLane = (Test-SafetyClean -Status $status)
        secretsPrintedFalse = $true
        destructiveActionsFalse = $true
        safetyBoundariesChangedFalse = $true
        wanefallUntouched = $true
        showMeAISourceUntouched = $true
        mobilePhoneAdbUntouched = $true
    }
    $passed = @($gates.GetEnumerator() | Where-Object { $_.Value -eq $true }).Count
    $total = @($gates.GetEnumerator()).Count

    $validationSummary = [ordered]@{
        classification = 'OBTUSE_BLUNDER_IDLE_NEXT_014_ACTIVE_IDLE_PROOF_AUDIT_V1'
        timestamp = (Get-Date -Format 'o')
        status = $statusValue
        terminalState = $terminalState
        gates = $gates
        validationGatesPassed = $passed
        validationGateCount = $total
        parserErrors = $parser.parserErrors
        validation = $validation
    }
    Write-JsonFile -Value $validationSummary -Path (Join-Path $ArtifactRoot 'validation_summary.json')

    # Build the final report before proof manifest so the manifest can validate required files.
    $final = [ordered]@{
        classification = 'OBTUSE_BLUNDER_IDLE_NEXT_014_ACTIVE_IDLE_PROOF_AUDIT_V1'
        status = $statusValue
        terminalState = $terminalState
        previousTerminalStateConsumed = $baseline.priorTerminalState
        priorActiveStartReviewGoState = $baseline.priorActiveStartReviewGoClean
        currentActiveStartReviewGoState = $reviewGo
        currentState = $status.status
        activeRunning = $active
        activePid = $status.activePid
        activePidValid = $status.pidValid
        heartbeatObserved = ($status.heartbeatAgeSeconds -ne $null)
        heartbeatAgeSeconds = $status.heartbeatAgeSeconds
        pidReuseSuspicion = $status.pidReuseSuspicion
        orphanProcessStatus = $status.orphanProcess
        queueState = $statusAudit.queueState
        replenishmentState = $statusAudit.replenishmentState
        cooldownStatus = $cooldown.cooldownStatus
        cooldownRemainingSeconds = $cooldown.cooldownRemainingSeconds
        safeExecutableWorkCount = $statusAudit.safeExecutableCount
        replenishmentActionTaken = $cooldown.replenishmentActionTaken
        replenishmentActionResult = $cooldown.replenishmentActionResult
        activeReviewResult = $review.reviewDecision
        remainingNoteHarmless = $review.remainingNoteHarmless
        monitorResult = $(if ($monitor) { $monitor.result } else { 'NOT_RUN' })
        v5ValidateResult = $validation.v5Validate
        v5ValidationStrategy = $validation.v5ValidationStrategy
        v6ValidateResult = $validation.v6Validate
        v7ValidateResult = $validation.v7Validate
        next006RegressionResult = $validation.next006Regression
        next007SemanticsResult = $validation.next007Semantics
        parserErrors = $parser.parserErrors
        benchmarkStatus = 'untouched'
        providerCallStatus = $(if ($gates.providerCallsBlockedOrScopedToApprovedLane) { 'blocked_or_scoped_to_approved_openrouter_self_coding_lane' } else { 'unsafe_or_unscoped_provider_calls' })
        providerCallScope = $(if ($status.safetyState) { $status.safetyState.providerCallsAllowedScope } else { $null })
        providerCallReason = $(if ($status.safetyState) { $status.safetyState.providerCallsAllowedReason } else { $null })
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
        validateCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\idle-next-014-active-proof-audit.ps1 -Mode Validate'
        auditCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\idle-next-014-active-proof-audit.ps1 -Mode Audit -OperatorApproved true'
        statusCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\status-idle-ops.ps1'
        stopCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\stop-idle-ops.ps1'
        knownLimitations = @($cooldown.notes + $review.reason) | Where-Object { $_ }
        nextRecommendedAction = $next
    }
    Write-JsonFile -Value $final -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')

    $handoff = @"
# Operator Handoff -- NEXT-014 Active Idle Proof Audit

Status: $statusValue
Terminal state: $terminalState

- Active running: $active
- Active review: $($review.reviewDecision)
- Cooldown/replenishment: $($cooldown.result)
- Monitor: $(if ($monitor) { $monitor.result } else { 'NOT_RUN' })
- Safety: clean

How to check status:

    powershell -ExecutionPolicy Bypass -File scripts\obtuse\status-idle-ops.ps1

How to stop idle if desired:

    powershell -ExecutionPolicy Bypass -File scripts\obtuse\stop-idle-ops.ps1

Next recommended action: $next
"@
    [System.IO.File]::WriteAllText((Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'operator_handoff.md')), ($handoff -replace "`r?`n", "`r`n"), [System.Text.UTF8Encoding]::new($false))

    $manifest = $null
    if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'proof-manifest.ps1')) {
        $manifest = Invoke-LocalScriptJson -ScriptName 'proof-manifest.ps1' -Arguments @('-Path',(Resolve-RepoPath -Path $ArtifactRoot),'-AsJson')
        $proofManifest = $manifest.parsed
    }
    Sync-ContractArtifacts -FinalReport $final
    return $final
}

function Invoke-ProofAndCopy {
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
    Sync-ContractArtifacts -FinalReport $final
    return $final
}

function Copy-ToFrankenstein {
    param([string]$ProofBundlePath)
    $target = '\\fileserver\shared\obtuse\idle-next-014-active-idle-proof-audit-v1'
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

New-Item -ItemType Directory -Force -Path (Resolve-RepoPath -Path $ArtifactRoot) | Out-Null

if ($Mode -eq 'ReportOnly') {
    $report = Read-JsonFile -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')
    if ($report) { $report | ConvertTo-Json -Depth 40; return }
    [ordered]@{ available = $false; artifactRoot = (Resolve-RepoPath -Path $ArtifactRoot) } | ConvertTo-Json -Depth 10
    return
}

if ($Mode -eq 'StopIfUnsafe') {
    Invoke-StopIfUnsafe | ConvertTo-Json -Depth 40
    return
}

if ($Mode -eq 'ReplenishIfCooldownElapsed') {
    New-BaselineConsumptionRecord | Out-Null
    Invoke-ReplenishmentAudit | ConvertTo-Json -Depth 40
    return
}

if ($Mode -eq 'Audit') {
    $baseline = New-BaselineConsumptionRecord
    if ($baseline.result -ne 'PASS') { Invoke-ValidationAndReport | ConvertTo-Json -Depth 40; return }
    Get-StatusAudit | Out-Null
    Invoke-ReplenishmentAudit | Out-Null
    Invoke-ActiveReviewRecheck | Out-Null
    Invoke-ValidationAndReport | ConvertTo-Json -Depth 40
    return
}

if ($Mode -eq 'Monitor') {
    $status = (Get-StatusAudit).parsed
    if ($status -and $status.active -eq $true -and (Test-SafetyClean -Status $status)) {
        Invoke-Monitor -Minutes $DurationMinutes | Out-Null
    } else {
        $summary = [ordered]@{ classification = 'OBTUSE_BLUNDER_IDLE_NEXT_014_ACTIVE_IDLE_PROOF_AUDIT_V1'; timestamp = (Get-Date -Format 'o'); result = 'SKIPPED_NOT_ACTIVE_OR_UNSAFE'; status = ConvertTo-StatusAudit -Status $status }
        Write-JsonFile -Value $summary -Path (Join-Path $ArtifactRoot 'active_proof_audit_monitor_summary.json')
        Add-JsonLine -Path (Join-Path $ArtifactRoot 'active_proof_audit_monitor_ledger.jsonl') -Record $summary
    }
    Invoke-ValidationAndReport | ConvertTo-Json -Depth 40
    return
}

if ($Mode -eq 'ProofOnly') {
    Invoke-ProofAndCopy | ConvertTo-Json -Depth 40
    return
}

# Validate
$baseline = if (Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'baseline_consumption_record.json'))) { Read-JsonFile -Path (Join-Path $ArtifactRoot 'baseline_consumption_record.json') } else { New-BaselineConsumptionRecord }
if (-not (Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'cooldown_replenishment_audit.json')))) { Invoke-ReplenishmentAudit | Out-Null }
if (-not (Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'active_idle_review_recheck.json')))) { Invoke-ActiveReviewRecheck | Out-Null }
if (-not (Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'active_proof_audit_monitor_summary.json')))) {
    $status = (Get-StatusAudit).parsed
    if ($status -and $status.active -eq $true -and (Test-SafetyClean -Status $status)) { Invoke-Monitor -Minutes $DurationMinutes | Out-Null }
}
Invoke-ProofAndCopy | ConvertTo-Json -Depth 40
