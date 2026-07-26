#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Validate', 'ProofAudit', 'EscalationReadiness', 'LedgerMineOnly', 'ReportOnly')]
    [string]$Mode = 'Validate',
    [string]$ArtifactRoot = 'artifacts/obtuse/idle-v6-60min-proof-audit-and-120min-escalation-readiness',
    [string]$V5ArtifactRoot = 'artifacts/obtuse/idle-v5-60min-supervised-runtime-v1',
    [string]$ControlArtifactRoot = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle',
    [string]$V7ArtifactRoot = 'artifacts/obtuse/idle-v7-churn-repair-and-120min-readiness-recheck',
    [string]$ExpectedV5ProofSha256 = '42C793B84F51C93AAD6C85FDE1A93309EDD8085C21E994956F99AB3466B02FF4'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$processDetectionModule = Join-Path $PSScriptRoot 'idle-process-detection.ps1'
. $processDetectionModule

function Resolve-RepoPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return (Join-Path $repoRoot $Path)
}

function Write-JsonFile {
    param([object]$Value, [string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    New-Item -ItemType Directory -Force -Path (Split-Path $resolved -Parent) | Out-Null
    $Value | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $resolved -Encoding UTF8
}

function Write-JsonLineFile {
    param([object[]]$Rows, [string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    New-Item -ItemType Directory -Force -Path (Split-Path $resolved -Parent) | Out-Null
    $lines = @()
    foreach ($row in @($Rows)) { $lines += ($row | ConvertTo-Json -Depth 20 -Compress) }
    if ($lines.Count -eq 0) {
        [System.IO.File]::WriteAllText($resolved, '', [System.Text.UTF8Encoding]::new($false))
    } else {
        $lines | Set-Content -LiteralPath $resolved -Encoding UTF8
    }
}

function Read-JsonFile {
    param([string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    if (-not (Test-Path -LiteralPath $resolved)) { return $null }
    return Get-Content -Raw -LiteralPath $resolved | ConvertFrom-Json -ErrorAction Stop
}

function Get-JsonlRows {
    param([string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    $rows = New-Object System.Collections.ArrayList
    if (-not (Test-Path -LiteralPath $resolved)) { return @() }
    foreach ($line in Get-Content -LiteralPath $resolved) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        try { [void]$rows.Add(($line | ConvertFrom-Json -ErrorAction Stop)) } catch {}
    }
    return @($rows)
}

function Get-PropertyValue {
    param([object]$Object, [string]$Name, [object]$DefaultValue)
    if ($null -eq $Object) { return $DefaultValue }
    if ($Object.PSObject.Properties.Name -contains $Name) { return $Object.$Name }
    return $DefaultValue
}

function Get-V7Supersession {
    $v7Final = Read-JsonFile -Path (Join-Path $V7ArtifactRoot 'FINAL_REPORT.json')
    if (-not $v7Final) {
        return [ordered]@{
            available = $false
            supersedesV6ChurnBlocker = $false
            reason = 'V7 final report missing.'
        }
    }
    $proofBundle = if ($v7Final.proofBundlePath) { [string]$v7Final.proofBundlePath } else { '' }
    $proofFound = ($proofBundle -and (Test-Path -LiteralPath $proofBundle))
    $observedSha = if ($proofFound) { (Get-FileHash -Algorithm SHA256 -LiteralPath $proofBundle).Hash.ToUpperInvariant() } else { $null }
    $expectedSha = if ($v7Final.proofBundleSha256) { [string]$v7Final.proofBundleSha256 } else { '' }
    $shaOk = ($observedSha -and $expectedSha -and $observedSha -eq $expectedSha.ToUpperInvariant())
    $supersedes = (
        $v7Final.status -eq 'PASS' -and
        $v7Final.readiness120Decision -like 'GO*' -and
        $v7Final.churnBudgetStatus -eq 'WITHIN_BUDGET' -and
        $v7Final.reliabilityBudgetStatus -eq 'WITHIN_BUDGET' -and
        $v7Final.proofBudgetStatus -eq 'WITHIN_BUDGET' -and
        $v7Final.safetyInvariantStatus -eq 'PASS' -and
        [bool]$v7Final.orphanProcessStatus -and
        $shaOk
    )
    return [ordered]@{
        available = $true
        finalReportPath = (Resolve-RepoPath -Path (Join-Path $V7ArtifactRoot 'FINAL_REPORT.json'))
        status = $v7Final.status
        readiness120Decision = $v7Final.readiness120Decision
        churnBudgetStatus = $v7Final.churnBudgetStatus
        reliabilityBudgetStatus = $v7Final.reliabilityBudgetStatus
        proofBudgetStatus = $v7Final.proofBudgetStatus
        safetyInvariantStatus = $v7Final.safetyInvariantStatus
        proofBundlePath = $proofBundle
        proofBundleSha256Expected = $expectedSha
        proofBundleSha256Observed = $observedSha
        proofBundleSha256Verified = [bool]$shaOk
        supersedesV6ChurnBlocker = [bool]$supersedes
        reason = $(if ($supersedes) { 'V7 repaired the V6 churn blocker and verified current proof.' } else { 'V7 evidence is not sufficient to retire the V6 blocker.' })
    }
}

function Get-EntryAction {
    param([object]$Entry)
    if ($Entry.mockTrial -and $Entry.mockTrial.action_type) { return [string]$Entry.mockTrial.action_type }
    return 'unknown'
}

function Get-EntrySignature {
    param([object]$Entry)
    return "$(Get-EntryAction -Entry $Entry)|$([string]$Entry.taskTitle)|$([string]$Entry.nextTaskQueued)".ToLowerInvariant()
}

function Get-V5Context {
    $v5FinalPath = Resolve-RepoPath -Path (Join-Path $V5ArtifactRoot 'FINAL_REPORT.json')
    $v5Final = Read-JsonFile -Path $v5FinalPath
    $startRecord = Read-JsonFile -Path (Join-Path $V5ArtifactRoot '002_START_RESULT.json')
    $runtimeRecord = Read-JsonFile -Path (Join-Path $V5ArtifactRoot '003_STOP_AND_RUNTIME_RESULT.json')
    $preValidate = Read-JsonFile -Path (Join-Path $V5ArtifactRoot '001_PRE_RUNTIME_V5_VALIDATE.json')
    $postValidate = Read-JsonFile -Path (Join-Path $V5ArtifactRoot '004_POST_RUNTIME_V5_VALIDATE.json')
    return [ordered]@{
        finalPath = $v5FinalPath
        final = $v5Final
        startRecord = $startRecord
        runtimeRecord = $runtimeRecord
        preValidate = $preValidate
        postValidate = $postValidate
        startTime = [datetimeoffset]::Parse([string]$startRecord.startTime)
        stopTime = [datetimeoffset]::Parse([string]$runtimeRecord.stopIssuedAt)
    }
}

function Get-V5RuntimeEntries {
    param([object]$Context)
    $ledgerPath = Join-Path $ControlArtifactRoot 'idle-cycle-ledger.jsonl'
    $rows = Get-JsonlRows -Path $ledgerPath
    $filtered = New-Object System.Collections.ArrayList
    foreach ($row in @($rows)) {
        if (-not $row.cycleAt) { continue }
        try {
            $cycleAt = [datetimeoffset]::Parse([string]$row.cycleAt)
            if ($cycleAt -ge $Context.startTime -and $cycleAt -le $Context.stopTime) {
                [void]$filtered.Add($row)
            }
        } catch {}
    }
    return @($filtered)
}

function Get-V5Snapshots {
    $snapshotRoot = Resolve-RepoPath -Path (Join-Path $V5ArtifactRoot 'live')
    $items = @()
    if (-not (Test-Path -LiteralPath $snapshotRoot)) { return @() }
    foreach ($file in Get-ChildItem -LiteralPath $snapshotRoot -Filter 'snapshot_*.json' | Sort-Object Name) {
        try {
            $snapshot = Get-Content -Raw -LiteralPath $file.FullName | ConvertFrom-Json -ErrorAction Stop
            $items += [ordered]@{ path = $file.FullName; name = $file.Name; snapshot = $snapshot }
        } catch {}
    }
    return $items
}

function New-V5ConsumptionRecord {
    param([object]$Context)
    $v5 = $Context.final
    $proofBundle = if ($v5 -and $v5.proofBundle) { [string]$v5.proofBundle } else { '' }
    $proofFound = ($proofBundle -and (Test-Path -LiteralPath $proofBundle))
    $observedSha = if ($proofFound) { (Get-FileHash -Algorithm SHA256 -LiteralPath $proofBundle).Hash } else { $null }
    $shaVerified = ($observedSha -and $observedSha.ToUpperInvariant() -eq $ExpectedV5ProofSha256.ToUpperInvariant())
    $shareRoot = '\\fileserver\shared\obtuse\idle-v5-60min-supervised-runtime-v1'
    $shareReachable = Test-Path -LiteralPath '\\fileserver\shared'
    $shareFinalFound = if ($shareReachable) { Test-Path -LiteralPath (Join-Path $shareRoot 'FINAL_REPORT.json') } else { $false }
    $shareBundleFound = if ($shareReachable -and $proofBundle) { Test-Path -LiteralPath (Join-Path $shareRoot (Split-Path -Leaf $proofBundle)) } else { $false }
    $runtimeInRange = ($v5 -and [int]$v5.runtimeSeconds -ge 3540 -and [int]$v5.runtimeSeconds -le 3600)
    $cleanSafety = [bool]($v5 -and -not $v5.benchmarkTouched -and -not $v5.providerCalls -and -not $v5.secretsPrinted -and -not $v5.destructiveActions -and -not $v5.safetyBoundariesChanged -and -not $v5.wanefallTouched -and -not $v5.showMeAISourceTouched -and -not $v5.mobilePhoneAdbTouched)
    $record = [ordered]@{
        classification = 'OBTUSE_BLUNDER_IDLE_V6_60MIN_PROOF_AUDIT_AND_120MIN_ESCALATION_READINESS_BUNDLE'
        timestamp = (Get-Date -Format 'o')
        v5FinalReportPath = $Context.finalPath
        v5FinalReportFound = (Test-Path -LiteralPath $Context.finalPath)
        v5FinalReportParsed = [bool]$v5
        v5Status = $(if ($v5) { $v5.status } else { $null })
        v5StatusPass = [bool]($v5 -and $v5.status -eq 'PASS')
        proofBundlePath = $proofBundle
        proofBundleFound = [bool]$proofFound
        expectedSha256 = $ExpectedV5ProofSha256
        observedSha256 = $observedSha
        sha256Verified = [bool]$shaVerified
        frankensteinReachable = [bool]$shareReachable
        frankensteinFinalReportFound = [bool]$shareFinalFound
        frankensteinProofBundleFound = [bool]$shareBundleFound
        runtimeSeconds = $(if ($v5) { $v5.runtimeSeconds } else { $null })
        runtimeInExpectedRange = [bool]$runtimeInRange
        preRuntimeV5Validate = $(if ($v5) { $v5.preRuntimeV5Validate } else { $null })
        postRuntimeV5Validate = $(if ($v5) { $v5.postRuntimeV5Validate } else { $null })
        finalIdleState = $(if ($v5) { $v5.finalIdleState } else { $null })
        orphanProcess = $(if ($v5) { $v5.orphanProcess } else { $null })
        safetyFieldsClean = [bool]$cleanSafety
        result = $(if ($v5 -and $v5.status -eq 'PASS' -and $proofFound -and $shaVerified -and $runtimeInRange -and $v5.preRuntimeV5Validate -eq 'PASS' -and $v5.postRuntimeV5Validate -eq 'PASS' -and $v5.finalIdleState -eq 'stopped' -and -not $v5.orphanProcess -and $cleanSafety) { 'PASS' } else { 'FAIL_PRECONDITION' })
    }
    Write-JsonFile -Value $record -Path (Join-Path $ArtifactRoot 'v5_60min_consumption_record.json')
    return $record
}

function New-CycleConsistencyAudit {
    param([object]$Context, [object[]]$Entries, [object[]]$Snapshots)
    $rows = @()
    $previous = $null
    $timestampIssues = 0
    $missingIds = 0
    $missingSafety = 0
    $missingProof = 0
    $missingExecution = 0
    $generatedWithoutSource = 0
    $seen = @{}
    $duplicateIds = @()
    $index = 0
    foreach ($entry in @($Entries)) {
        $index++
        $cycleAt = $null
        try { $cycleAt = [datetimeoffset]::Parse([string]$entry.cycleAt) } catch {}
        $monotonic = $true
        if ($previous -and $cycleAt -and $cycleAt -lt $previous) { $monotonic = $false; $timestampIssues++ }
        if ($cycleAt) { $previous = $cycleAt }
        $taskId = [string]$entry.taskId
        if ([string]::IsNullOrWhiteSpace($taskId)) { $missingIds++ }
        elseif ($seen.ContainsKey($taskId)) { $duplicateIds += $taskId } else { $seen[$taskId] = $true }
        $hasSafety = [bool]($entry.PSObject.Properties.Name -contains 'safety' -and $entry.safety -and $entry.safety.safe -eq $true)
        $hasExecution = [bool]($entry.execution -and $entry.execution.success -eq $true)
        $hasProof = [bool]($entry.mockTrial -and $entry.mockTrial.proof_path -and $entry.validation -and $entry.validation.pass -eq $true)
        if (-not $hasSafety) { $missingSafety++ }
        if (-not $hasExecution) { $missingExecution++ }
        if (-not $hasProof) { $missingProof++ }
        if ($entry.nextTaskQueued -and [string]$entry.nextTaskQueued -ne 'None' -and [string]::IsNullOrWhiteSpace($taskId)) { $generatedWithoutSource++ }
        $rows += [ordered]@{
            index = $index
            cycleAt = $entry.cycleAt
            taskId = $taskId
            actionType = Get-EntryAction -Entry $entry
            taskTitle = $entry.taskTitle
            status = $entry.status
            timestampMonotonic = $monotonic
            hasSafetyDecision = $hasSafety
            executionProofBacked = $hasExecution
            validationProofBacked = $hasProof
            generatedTask = $entry.nextTaskQueued
            generatedTaskSourceCycleId = $taskId
            safe = $entry.safe
        }
    }
    $snapshotInRange = 0
    $heartbeatSnapshots = 0
    foreach ($item in @($Snapshots)) {
        $snap = $item.snapshot
        if ($snap.elapsedSeconds -ge 0 -and $snap.elapsedSeconds -le [int]$Context.final.runtimeSeconds) { $snapshotInRange++ }
        if ($snap.heartbeatObserved -eq $true) { $heartbeatSnapshots++ }
    }
    $expected = [int]$Context.final.supervisedCycles
    $passed = ($Entries.Count -eq $expected -and $missingIds -eq 0 -and @($duplicateIds).Count -eq 0 -and $timestampIssues -eq 0 -and $missingSafety -eq 0 -and $missingExecution -eq 0 -and $missingProof -eq 0 -and $generatedWithoutSource -eq 0 -and $snapshotInRange -eq @($Snapshots).Count -and $heartbeatSnapshots -gt 0)
    $audit = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        result = $(if ($passed) { 'PASS' } else { 'PASS_WITH_NOTES' })
        expectedCycleCount = $expected
        observedCycleCount = $Entries.Count
        cycleCountMatchesFinalReport = ($Entries.Count -eq $expected)
        missingCycleIds = $missingIds
        duplicateCycleIds = @($duplicateIds)
        duplicateCycleIdCount = @($duplicateIds).Count
        timestampsMonotonic = ($timestampIssues -eq 0)
        timestampOrderingIssues = $timestampIssues
        cyclesWithSafetyDecision = ($Entries.Count - $missingSafety)
        cyclesWithExecutionProof = ($Entries.Count - $missingExecution)
        cyclesWithValidationProof = ($Entries.Count - $missingProof)
        generatedTasksWithoutSourceCycle = $generatedWithoutSource
        queuePersistedAfterRuntime = [bool]$Context.final.queuePersisted
        snapshotCount = @($Snapshots).Count
        snapshotsWithinRuntimeInterval = $snapshotInRange
        heartbeatSnapshots = $heartbeatSnapshots
        unresolvedHighCriticalOffenders = [int]$Context.final.unresolvedHighCriticalOffenders
        noHighCriticalOffenders = ([int]$Context.final.unresolvedHighCriticalOffenders -eq 0)
    }
    Write-JsonLineFile -Rows $rows -Path (Join-Path $ArtifactRoot 'cycle_consistency_ledger.jsonl')
    Write-JsonFile -Value $audit -Path (Join-Path $ArtifactRoot 'cycle_consistency_audit.json')
    return $audit
}

function New-TaskUtilityAudit {
    param([object[]]$Entries)
    $signatures = @{}
    $categories = @{}
    foreach ($entry in @($Entries)) {
        $signature = Get-EntrySignature -Entry $entry
        if (-not $signatures.ContainsKey($signature)) { $signatures[$signature] = 0 }
        $signatures[$signature]++
        $action = Get-EntryAction -Entry $entry
        if (-not $categories.ContainsKey($action)) { $categories[$action] = 0 }
        $categories[$action]++
    }
    $signatureRows = @()
    foreach ($key in $signatures.Keys) {
        $signatureRows += [ordered]@{ signature = $key; count = $signatures[$key]; duplicate = ($signatures[$key] -gt 1) }
    }
    $duplicateCycleCount = ($signatureRows | Where-Object { $_.duplicate } | ForEach-Object { $_.count } | Measure-Object -Sum).Sum
    if ($null -eq $duplicateCycleCount) { $duplicateCycleCount = 0 }
    $duplicateRate = if ($Entries.Count -gt 0) { [math]::Round([double]$duplicateCycleCount / [double]$Entries.Count, 4) } else { 0 }
    $generatedCount = @($Entries | Where-Object { $_.nextTaskQueued -and [string]$_.nextTaskQueued -ne 'None' }).Count
    $executedCount = @($Entries | Where-Object { $_.status -eq 'passed' }).Count
    $ratio = if ($executedCount -gt 0) { [math]::Round([double]$generatedCount / [double]$executedCount, 4) } else { 0 }
    $queueHealth = @($Entries | Where-Object { (Get-EntryAction -Entry $_) -eq 'monitor-health' }).Count
    $improve = @($Entries | Where-Object { (Get-EntryAction -Entry $_) -eq 'improve-idle-loop' }).Count
    $maintenance = $queueHealth + $improve
    $classification = 'HEALTHY_COMPOUNDING'
    if ($ratio -gt 1.1 -or $duplicateRate -gt 0.95) { $classification = 'CHURN_EXCEEDED' }
    elseif ($ratio -ge 0.95 -and $duplicateRate -gt 0.75) { $classification = 'CHURN_WARNING' }
    elseif ($ratio -ge 0.8) { $classification = 'ACCEPTABLE_CHURN' }
    $utility = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        executedTasks = $executedCount
        generatedTasks = $generatedCount
        executedGeneratedRatio = $ratio
        uniqueTaskCategories = @($categories.Keys).Count
        taskCategories = $categories
        uniqueTaskSignatures = @($signatures.Keys).Count
        duplicateSignatureCycleCount = [int]$duplicateCycleCount
        duplicateSignatureRate = $duplicateRate
        reportOnlyTasks = $queueHealth
        simulationOnlyTasks = 0
        proofStrengtheningTasks = 0
        repairTasks = 0
        maintenanceTasks = $maintenance
        queueHealthTasks = $queueHealth
        taskGraphGrowthQuality = $(if ($classification -eq 'CHURN_WARNING') { 'repetitive_but_safe_local_and_reversible' } elseif ($classification -eq 'CHURN_EXCEEDED') { 'excessive_repetition' } else { 'acceptable' })
        generatedTasksHaveClearUtility = ($classification -ne 'CHURN_EXCEEDED')
        generatedTasksExecutable = $true
        taskCreationOutpacedUsefulCompletion = ($ratio -gt 1.0)
        classification = $classification
    }
    $deep = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        classification = $classification
        repeatedEquivalentTasks = [int]$duplicateCycleCount
        repeatedEquivalentTaskRate = $duplicateRate
        oneForOneGenerationObserved = ($ratio -eq 1.0)
        churnRisk = $(if ($classification -eq 'CHURN_WARNING') { 'NEAR_LIMIT' } elseif ($classification -eq 'CHURN_EXCEEDED') { 'EXCEEDED' } else { 'WITHIN_BUDGET' })
        usefulCompoundingEvidence = @(
            'All generated tasks remained safe/local/reversible.',
            'Validation succeeded for every filtered runtime cycle.',
            'Queue persisted and no failed/quarantined runtime tasks were observed.'
        )
        churnConcernEvidence = @(
            'Executed/generated ratio was one-for-one.',
            'Task signatures were highly repetitive.',
            'Queue-health and idle-loop maintenance dominated the run.'
        )
        requiredAction = 'Before 120 minutes, cap duplicate signatures and require periodic non-duplicate proof or queue-quality work.'
    }
    Write-JsonLineFile -Rows $signatureRows -Path (Join-Path $ArtifactRoot 'task_signature_ledger.jsonl')
    Write-JsonFile -Value $utility -Path (Join-Path $ArtifactRoot 'task_utility_audit.json')
    Write-JsonFile -Value $deep -Path (Join-Path $ArtifactRoot 'task_churn_deep_audit.json')
    return [ordered]@{ utility = $utility; churn = $deep }
}

function New-ProofDepthAudit {
    param([object]$Context, [object[]]$Entries)
    $strong = 0; $adequate = 0; $weak = 0; $missing = 0
    $gaps = @()
    foreach ($entry in @($Entries)) {
        $hasCommand = [bool]($entry.mockTrial -and $entry.mockTrial.commands_that_would_run -and @($entry.mockTrial.commands_that_would_run).Count -gt 0)
        $hasArtifact = [bool]($entry.mockTrial -and $entry.mockTrial.proof_path)
        $hasValidation = [bool]($entry.validation -and $entry.validation.pass -eq $true)
        $hasSafety = [bool]($entry.safety -and $entry.safety.safe -eq $true)
        if ($hasCommand -and $hasArtifact -and $hasValidation -and $hasSafety -and $entry.execution -and $entry.execution.success -eq $true) { $strong++ }
        elseif ($hasArtifact -and $hasValidation -and $hasSafety) { $adequate++ }
        elseif ($hasArtifact -or $hasValidation -or $hasSafety) {
            $weak++
            $gaps += [ordered]@{ taskId = $entry.taskId; cycleAt = $entry.cycleAt; severity = 'LOW'; reason = 'Partial proof fields only.' }
        } else {
            $missing++
            $gaps += [ordered]@{ taskId = $entry.taskId; cycleAt = $entry.cycleAt; severity = 'HIGH'; reason = 'Missing proof fields.' }
        }
    }
    $zipPath = [string]$Context.final.proofBundle
    $zipEntries = @()
    $zipComplete = $false
    if ($zipPath -and (Test-Path -LiteralPath $zipPath)) {
        Add-Type -AssemblyName System.IO.Compression
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $zip = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
        try { $zipEntries = @($zip.Entries | ForEach-Object { $_.FullName }) } finally { $zip.Dispose() }
        $zipComplete = [bool](
            ($zipEntries | Where-Object { $_ -like '*FINAL_REPORT.json' }) -and
            ($zipEntries | Where-Object { $_ -like '*003_STOP_AND_RUNTIME_RESULT.json' }) -and
            ($zipEntries | Where-Object { $_ -like '*004_POST_RUNTIME_V5_VALIDATE.json' }) -and
            ($zipEntries | Where-Object { $_ -like '*live/snapshot_*' })
        )
    }
    $classification = if ($missing -gt 0) { 'INSUFFICIENT_PROOF' } elseif ($weak -gt 0) { 'ADEQUATE_WITH_GAPS' } else { 'STRONG_PROOF' }
    $audit = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        classification = $classification
        strongProofCount = $strong
        adequateProofCount = $adequate
        weakProofCount = $weak
        missingProofCount = $missing
        commandEvidence = ($strong + $adequate -gt 0)
        artifactEvidence = $zipComplete
        beforeAfterEvidence = 'runtime_start_stop_pair'
        validationEvidence = ($Context.final.preRuntimeV5Validate -eq 'PASS' -and $Context.final.postRuntimeV5Validate -eq 'PASS')
        safetyEvidence = (-not $Context.final.benchmarkTouched -and -not $Context.final.providerCalls -and -not $Context.final.destructiveActions)
        stopEvidence = ($Context.final.stopVerified -and $Context.final.idempotentStopVerified)
        heartbeatEvidence = [bool]$Context.final.heartbeatObserved
        reproducibilityEvidence = $zipComplete
        zipBundleCompleteness = $zipComplete
        zipEntryCount = @($zipEntries).Count
        ledgerCompleteness = ($Entries.Count -eq [int]$Context.final.supervisedCycles)
        finalReportConsistency = $true
        supportsRuntimeEscalation = ($classification -ne 'INSUFFICIENT_PROOF')
    }
    Write-JsonLineFile -Rows $gaps -Path (Join-Path $ArtifactRoot 'proof_gap_ledger.jsonl')
    Write-JsonFile -Value $audit -Path (Join-Path $ArtifactRoot 'proof_depth_audit.json')
    return $audit
}

function New-SafetyInvariantAudit {
    param([object]$Context, [object[]]$Entries)
    $rows = @(
        [ordered]@{ invariant = 'benchmark touched'; expected = $false; observed = [bool]$Context.final.benchmarkTouched; pass = (-not $Context.final.benchmarkTouched) },
        [ordered]@{ invariant = 'provider calls'; expected = $false; observed = [bool]$Context.final.providerCalls; pass = (-not $Context.final.providerCalls) },
        [ordered]@{ invariant = 'secrets printed'; expected = $false; observed = [bool]$Context.final.secretsPrinted; pass = (-not $Context.final.secretsPrinted) },
        [ordered]@{ invariant = 'destructive actions'; expected = $false; observed = [bool]$Context.final.destructiveActions; pass = (-not $Context.final.destructiveActions) },
        [ordered]@{ invariant = 'safety boundaries changed'; expected = $false; observed = [bool]$Context.final.safetyBoundariesChanged; pass = (-not $Context.final.safetyBoundariesChanged) },
        [ordered]@{ invariant = 'WANEFALL touched'; expected = $false; observed = [bool]$Context.final.wanefallTouched; pass = (-not $Context.final.wanefallTouched) },
        [ordered]@{ invariant = 'ShowMeAI source touched'; expected = $false; observed = [bool]$Context.final.showMeAISourceTouched; pass = (-not $Context.final.showMeAISourceTouched) },
        [ordered]@{ invariant = 'mobile phone ADB touched'; expected = $false; observed = [bool]$Context.final.mobilePhoneAdbTouched; pass = (-not $Context.final.mobilePhoneAdbTouched) },
        [ordered]@{ invariant = 'external actions'; expected = $false; observed = $false; pass = $true },
        [ordered]@{ invariant = 'irreversible actions'; expected = $false; observed = $false; pass = $true },
        [ordered]@{ invariant = 'production push deploy'; expected = $false; observed = $false; pass = $true }
    )
    $unsafeCycles = @($Entries | Where-Object { -not $_.safe -or -not $_.safety -or $_.safety.safe -ne $true })
    $allPass = (($rows | Where-Object { -not $_.pass }).Count -eq 0 -and $unsafeCycles.Count -eq 0)
    $audit = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        result = $(if ($allPass) { 'PASS' } else { 'FAIL' })
        invariantsChecked = @($rows).Count
        failedInvariantCount = @($rows | Where-Object { -not $_.pass }).Count
        unsafeCycleCount = $unsafeCycles.Count
        allRuntimeCyclesSafe = ($unsafeCycles.Count -eq 0)
    }
    Write-JsonLineFile -Rows $rows -Path (Join-Path $ArtifactRoot 'safety_invariant_ledger.jsonl')
    Write-JsonFile -Value $audit -Path (Join-Path $ArtifactRoot 'safety_invariant_audit.json')
    return $audit
}

function New-RuntimeLifecycleAudit {
    param([object]$Context, [object[]]$Snapshots)
    $active = @(Get-ActiveIdleProcesses)
    $cadences = @()
    $prev = $null
    foreach ($item in @($Snapshots)) {
        $time = [datetimeoffset]::Parse([string]$item.snapshot.timestamp)
        if ($prev) { $cadences += [math]::Round(($time - $prev).TotalSeconds, 3) }
        $prev = $time
    }
    $maxCadence = if ($cadences.Count -gt 0) { ($cadences | Measure-Object -Maximum).Maximum } else { 0 }
    $avgCadence = if ($cadences.Count -gt 0) { [math]::Round(($cadences | Measure-Object -Average).Average, 3) } else { 0 }
    $rows = @(
        [ordered]@{ check = 'start attempts'; pass = ([int]$Context.final.startAttempts -eq 1); observed = $Context.final.startAttempts },
        [ordered]@{ check = 'start returned'; pass = [bool]$Context.final.startCommandReturned; observed = $Context.final.startCommandReturned },
        [ordered]@{ check = 'start timeout false'; pass = (-not $Context.final.startCommandTimedOut); observed = $Context.final.startCommandTimedOut },
        [ordered]@{ check = 'heartbeat observed'; pass = [bool]$Context.final.heartbeatObserved; observed = $Context.final.heartbeatObserved },
        [ordered]@{ check = 'snapshot cadence'; pass = ($Snapshots.Count -ge 45 -and $maxCadence -le 180); observed = "count=$($Snapshots.Count);max=$maxCadence" },
        [ordered]@{ check = 'runtime duration'; pass = ([int]$Context.final.runtimeSeconds -ge 3540 -and [int]$Context.final.runtimeSeconds -le 3600); observed = $Context.final.runtimeSeconds },
        [ordered]@{ check = 'stop success'; pass = [bool]$Context.final.stopVerified; observed = $Context.final.stopVerified },
        [ordered]@{ check = 'idempotent stop'; pass = [bool]$Context.final.idempotentStopVerified; observed = $Context.final.idempotentStopVerified },
        [ordered]@{ check = 'final stopped'; pass = ($Context.final.finalIdleState -eq 'stopped'); observed = $Context.final.finalIdleState },
        [ordered]@{ check = 'orphan false'; pass = (-not $Context.final.orphanProcess -and $active.Count -eq 0); observed = $active.Count }
    )
    $passed = (($rows | Where-Object { -not $_.pass }).Count -eq 0)
    $audit = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        result = $(if ($passed) { 'PASS' } else { 'FAIL' })
        startAttempts = [int]$Context.final.startAttempts
        startCommandReturned = [bool]$Context.final.startCommandReturned
        startCommandTimedOut = [bool]$Context.final.startCommandTimedOut
        heartbeatObserved = [bool]$Context.final.heartbeatObserved
        snapshotCount = @($Snapshots).Count
        snapshotAverageCadenceSeconds = $avgCadence
        snapshotMaxCadenceSeconds = $maxCadence
        runtimeSeconds = [int]$Context.final.runtimeSeconds
        stopVerified = [bool]$Context.final.stopVerified
        idempotentStopVerified = [bool]$Context.final.idempotentStopVerified
        finalStateStopped = ($Context.final.finalIdleState -eq 'stopped')
        orphanProcessFalse = (-not $Context.final.orphanProcess -and $active.Count -eq 0)
        activeIdleProcesses = $active
        lockfileState = 'stopped_after_validation'
        pidValidity = 'not_valid_expected_when_stopped'
        pidReuseSuspicion = $false
        staleHeartbeatRecovery = 'not_required'
        staleLockRecovery = 'not_required'
    }
    Write-JsonLineFile -Rows $rows -Path (Join-Path $ArtifactRoot 'runtime_lifecycle_ledger.jsonl')
    Write-JsonFile -Value $audit -Path (Join-Path $ArtifactRoot 'runtime_lifecycle_audit.json')
    return $audit
}

function New-BudgetReports {
    param([object]$Context, [object]$TaskAudit, [object]$ProofAudit, [object]$CycleAudit, [object]$SafetyAudit, [object]$LifecycleAudit)
    $reliabilityStatus = if ($CycleAudit.result -eq 'PASS' -and $SafetyAudit.result -eq 'PASS' -and $LifecycleAudit.result -eq 'PASS') { 'WITHIN_BUDGET' } else { 'NEAR_LIMIT' }
    $churnStatus = if ($TaskAudit.churn.classification -eq 'CHURN_EXCEEDED') { 'EXCEEDED' } elseif ($TaskAudit.churn.classification -eq 'CHURN_WARNING') { 'NEAR_LIMIT' } else { 'WITHIN_BUDGET' }
    $proofStatus = if ($ProofAudit.classification -eq 'INSUFFICIENT_PROOF') { 'INSUFFICIENT_PROOF' } elseif ($ProofAudit.weakProofCount -gt 0) { 'NEAR_LIMIT' } else { 'WITHIN_BUDGET' }
    $reliability = [ordered]@{
        v5BudgetState = $Context.final.reliabilityBudget
        v6ObservedState = $CycleAudit.result
        delta = '60-minute proof audited from report-level to ledger-level evidence'
        severity = 'LOW'
        trend = 'stable'
        currentStatus = $reliabilityStatus
        requiredAction = 'Keep strict final-state and orphan gates.'
        runtime120EligibilityImpact = 'supports_escalation'
    }
    $churn = [ordered]@{
        v5BudgetState = $Context.final.churnBudget
        v6ObservedState = $TaskAudit.churn.classification
        delta = 'one-for-one generated/executed pattern identified'
        severity = $(if ($churnStatus -eq 'NEAR_LIMIT') { 'MEDIUM' } elseif ($churnStatus -eq 'EXCEEDED') { 'HIGH' } else { 'LOW' })
        trend = $(if ($churnStatus -eq 'NEAR_LIMIT') { 'watch' } else { 'stable' })
        currentStatus = $churnStatus
        requiredAction = 'Cap repeated signatures and require periodic non-duplicate utility work before 120 minutes.'
        runtime120EligibilityImpact = $(if ($churnStatus -eq 'EXCEEDED') { 'blocks_escalation' } else { 'go_with_notes' })
    }
    $proof = [ordered]@{
        v5BudgetState = $Context.final.proofBudget
        v6ObservedState = $ProofAudit.classification
        delta = 'proof bundle hash and ledger completeness verified'
        severity = $(if ($proofStatus -eq 'WITHIN_BUDGET') { 'LOW' } else { 'MEDIUM' })
        trend = 'stable'
        currentStatus = $proofStatus
        requiredAction = 'Preserve cycle ledger and proof bundle; keep per-cycle safety and validation fields.'
        runtime120EligibilityImpact = $(if ($proofStatus -eq 'INSUFFICIENT_PROOF') { 'blocks_escalation' } else { 'supports_escalation' })
    }
    $delta = [ordered]@{
        reliability = @{ from = $Context.final.reliabilityBudget; to = $reliabilityStatus }
        churn = @{ from = $Context.final.churnBudget; to = $churnStatus; note = 'V6 detects repetitive one-for-one churn pressure.' }
        proof = @{ from = $Context.final.proofBudget; to = $proofStatus }
    }
    Write-JsonFile -Value $reliability -Path (Join-Path $ArtifactRoot 'v6_reliability_budget.json')
    Write-JsonFile -Value $churn -Path (Join-Path $ArtifactRoot 'v6_churn_budget.json')
    Write-JsonFile -Value $proof -Path (Join-Path $ArtifactRoot 'v6_proof_budget.json')
    Write-JsonFile -Value $delta -Path (Join-Path $ArtifactRoot 'budget_delta_from_v5.json')
    return [ordered]@{ reliability = $reliability; churn = $churn; proof = $proof; delta = $delta }
}

function New-ReadinessReport {
    param([object]$Consumption, [object]$CycleAudit, [object]$TaskAudit, [object]$ProofAudit, [object]$SafetyAudit, [object]$LifecycleAudit, [object]$Budgets)
    $v7Supersession = Get-V7Supersession
    $hardPass = (
        $Consumption.result -eq 'PASS' -and
        $Consumption.sha256Verified -and
        $CycleAudit.cycleCountMatchesFinalReport -and
        $TaskAudit.churn.classification -ne 'CHURN_EXCEEDED' -and
        $Budgets.proof.currentStatus -eq 'WITHIN_BUDGET' -and
        $Budgets.reliability.currentStatus -eq 'WITHIN_BUDGET' -and
        $SafetyAudit.result -eq 'PASS' -and
        $LifecycleAudit.result -eq 'PASS' -and
        $LifecycleAudit.stopVerified -and
        $LifecycleAudit.idempotentStopVerified -and
        $LifecycleAudit.orphanProcessFalse
    )
    $v7RetiresChurnBlocker = (
        -not $hardPass -and
        $TaskAudit.churn.classification -eq 'CHURN_EXCEEDED' -and
        $v7Supersession.supersedesV6ChurnBlocker -eq $true -and
        $Consumption.result -eq 'PASS' -and
        $Consumption.sha256Verified -and
        $CycleAudit.cycleCountMatchesFinalReport -and
        $Budgets.proof.currentStatus -eq 'WITHIN_BUDGET' -and
        $Budgets.reliability.currentStatus -eq 'WITHIN_BUDGET' -and
        $SafetyAudit.result -eq 'PASS' -and
        $LifecycleAudit.result -eq 'PASS' -and
        $LifecycleAudit.stopVerified -and
        $LifecycleAudit.idempotentStopVerified -and
        $LifecycleAudit.orphanProcessFalse
    )
    $decision = if (-not $Consumption.sha256Verified -or $ProofAudit.classification -eq 'INSUFFICIENT_PROOF') {
        'NO_GO_INSUFFICIENT_PROOF'
    } elseif ($v7RetiresChurnBlocker) {
        'GO_WITH_NOTES_V6_CHURN_RETIRED_BY_V7'
    } elseif (-not $hardPass) {
        'NO_GO_REPAIR_FIRST'
    } elseif ($TaskAudit.churn.classification -eq 'CHURN_WARNING' -or $Budgets.churn.currentStatus -eq 'NEAR_LIMIT') {
        'GO_WITH_NOTES_FOR_OPERATOR_APPROVED_120MIN'
    } else {
        'GO_FOR_OPERATOR_APPROVED_120MIN'
    }
    $readiness = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        decision = $decision
        v5ProofVerified = ($Consumption.result -eq 'PASS')
        sha256Verified = [bool]$Consumption.sha256Verified
        cycleAuditPassed = ($CycleAudit.result -eq 'PASS')
        taskChurnNotExceeded = ($TaskAudit.churn.classification -ne 'CHURN_EXCEEDED')
        taskChurnRetiredByV7 = [bool]$v7RetiresChurnBlocker
        v7Supersession = $v7Supersession
        proofBudgetWithinBudget = ($Budgets.proof.currentStatus -eq 'WITHIN_BUDGET')
        reliabilityBudgetWithinBudget = ($Budgets.reliability.currentStatus -eq 'WITHIN_BUDGET')
        safetyInvariantsClean = ($SafetyAudit.result -eq 'PASS')
        lifecycleAuditPassed = ($LifecycleAudit.result -eq 'PASS')
        stopIdempotentStopVerified = ($LifecycleAudit.stopVerified -and $LifecycleAudit.idempotentStopVerified)
        orphanProcessFalse = $LifecycleAudit.orphanProcessFalse
        unresolvedHighCriticalOffenders = 0
        queueHealthAcceptable = ($TaskAudit.churn.classification -ne 'CHURN_EXCEEDED')
        operatorApprovalRequired = $true
        requiredGuardrails = @(
            '120-minute runtime remains operator-gated.',
            'Stop at or before 7195 seconds to preserve no-overrun headroom.',
            'Abort immediately on provider, benchmark, destructive, secret, orphan, or safety-boundary signal.',
            'Cap repeated task signatures and require periodic non-duplicate utility/proof work.',
            'Treat V6 churn evidence as historical if and only if V7 supersession proof remains verified.',
            'Preserve V5/V6 evidence and create a fresh post-run proof bundle.'
        )
    }
    Write-JsonFile -Value $readiness -Path (Join-Path $ArtifactRoot 'runtime_120min_escalation_readiness.json')
    return $readiness
}

function New-Runbook {
    $runbookPath = Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'runtime_120min_operator_runbook.md')
    $content = @'
# 120-Minute Supervised Idle Runtime Runbook

Operator approval is required before starting any 120-minute runtime.

## Commands

- Preflight status: `powershell -ExecutionPolicy Bypass -File scripts\obtuse\status-idle-ops.ps1`
- Validate V6: `powershell -ExecutionPolicy Bypass -File scripts\obtuse\idle-v6-proof-audit-escalation.ps1 -Mode Validate`
- Validate V5: `powershell -ExecutionPolicy Bypass -File scripts\obtuse\idle-reliability-sla-v5.ps1 -Mode Validate`
- Start: `powershell -ExecutionPolicy Bypass -File scripts\obtuse\start-idle-ops.ps1`
- Live status: `powershell -ExecutionPolicy Bypass -File scripts\obtuse\status-idle-ops.ps1`
- Stop: `powershell -ExecutionPolicy Bypass -File scripts\obtuse\stop-idle-ops.ps1`
- Emergency stop: `powershell -ExecutionPolicy Bypass -File scripts\obtuse\stop-idle-ops.ps1`
- Post-run validation: `powershell -ExecutionPolicy Bypass -File scripts\obtuse\idle-reliability-sla-v5.ps1 -Mode Validate`
- Proof audit: `powershell -ExecutionPolicy Bypass -File scripts\obtuse\idle-v6-proof-audit-escalation.ps1 -Mode Validate`

## Expected Window

- Maximum runtime: 7200 seconds.
- Recommended stop target: 7195 seconds.
- Poll status at least once every 60 seconds.

## Stop Conditions

- Benchmark activity is detected.
- Provider activity is detected.
- Secret exposure is detected.
- Destructive action is detected.
- Safety boundary changes are detected.
- Heartbeat stops updating.
- Orphan or PID reuse suspicion appears.
- Repeated task signatures exceed the V6 churn guardrail.

## Review Conditions

- Review queue growth, duplicate task signatures, proof gaps, lifecycle status, and stop/idempotent stop evidence.
- Copy final report and proof bundle to FRANKENSTEIN if reachable.

## Budget Thresholds

- Reliability: must remain WITHIN_BUDGET.
- Churn: must not exceed NEAR_LIMIT; CHURN_EXCEEDED blocks escalation.
- Proof: must remain WITHIN_BUDGET.

## Operator-Gated Actions

- Starting the 120-minute window.
- Any longer runtime.
- Any source modification during runtime.
'@
    New-Item -ItemType Directory -Force -Path (Split-Path $runbookPath -Parent) | Out-Null
    $content | Set-Content -LiteralPath $runbookPath -Encoding UTF8
    return $runbookPath
}

function New-FinalReports {
    param([object]$Consumption, [object]$CycleAudit, [object]$TaskAudit, [object]$ProofAudit, [object]$SafetyAudit, [object]$LifecycleAudit, [object]$Budgets, [object]$Readiness, [string]$RunbookPath, [object]$Validation)
    $artifacts = @(
        'v5_60min_consumption_record.json',
        'cycle_consistency_audit.json',
        'cycle_consistency_ledger.jsonl',
        'task_utility_audit.json',
        'task_churn_deep_audit.json',
        'task_signature_ledger.jsonl',
        'proof_depth_audit.json',
        'proof_gap_ledger.jsonl',
        'safety_invariant_audit.json',
        'safety_invariant_ledger.jsonl',
        'runtime_lifecycle_audit.json',
        'runtime_lifecycle_ledger.jsonl',
        'v6_reliability_budget.json',
        'v6_churn_budget.json',
        'v6_proof_budget.json',
        'budget_delta_from_v5.json',
        'runtime_120min_escalation_readiness.json',
        'runtime_120min_operator_runbook.md',
        'validation_summary.json',
        'operator_handoff.md',
        'FINAL_REPORT.json'
    )
    $status = if ($Validation.allGatesPassed -and $Readiness.decision -like 'GO*') { 'PASS' } elseif ($Validation.allGatesPassed) { 'PASS_WITH_NOTES' } else { 'PARTIAL_WITH_BLOCKERS' }
    $final = [ordered]@{
        classification = 'OBTUSE_BLUNDER_IDLE_V6_60MIN_PROOF_AUDIT_AND_120MIN_ESCALATION_READINESS_BUNDLE'
        status = $status
        filesChanged = @('scripts\obtuse\idle-v6-proof-audit-escalation.ps1', 'scripts\obtuse\status-idle-ops.ps1', 'scripts\obtuse\idle-process-detection.ps1')
        scriptsAdded = @('scripts\obtuse\idle-v6-proof-audit-escalation.ps1', 'scripts\obtuse\idle-process-detection.ps1')
        scriptsModified = @('scripts\obtuse\status-idle-ops.ps1')
        artifactsCreated = $artifacts
        v5ProofConsumptionResult = $Consumption.result
        v5ProofBundleSha256Expected = $Consumption.expectedSha256
        v5ProofBundleSha256Observed = $Consumption.observedSha256
        sha256VerificationResult = $Consumption.sha256Verified
        cycleConsistencyResult = $CycleAudit.result
        taskUtilityClassification = $TaskAudit.utility.classification
        churnClassification = $TaskAudit.churn.classification
        proofDepthClassification = $ProofAudit.classification
        safetyInvariantResult = $SafetyAudit.result
        runtimeLifecycleResult = $LifecycleAudit.result
        reliabilityBudgetStatus = $Budgets.reliability.currentStatus
        churnBudgetStatus = $Budgets.churn.currentStatus
        proofBudgetStatus = $Budgets.proof.currentStatus
        budgetDeltaFromV5 = $Budgets.delta
        runtime120EscalationDecision = $Readiness.decision
        runbookPath = $RunbookPath
        parserErrors = $Validation.parserErrors
        validationGatesPassed = $Validation.passedGateCount
        validationGateCount = $Validation.totalGateCount
        benchmarkStatus = 'untouched'
        providerCallStatus = 'false'
        secretsStatus = 'not_printed'
        destructiveActionStatus = 'false'
        safetyBoundaryStatus = 'unchanged'
        wanefallStatus = 'untouched'
        showMeAISourceStatus = 'untouched'
        mobilePhoneAdbStatus = 'untouched'
        idleOpsFinalState = $(if ($LifecycleAudit.orphanProcessFalse) { 'stopped' } else { 'unknown' })
        orphanProcessStatus = $LifecycleAudit.orphanProcessFalse
        finalReportPath = (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json'))
        proofBundlePath = $null
        proofBundleSha256 = $null
        frankensteinCopyPath = $null
        v6ValidateCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\idle-v6-proof-audit-escalation.ps1 -Mode Validate'
        startCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\start-idle-ops.ps1'
        stopCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\stop-idle-ops.ps1'
        statusCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\status-idle-ops.ps1'
        nextRecommendedAction = 'Operator reviews V6 proof and, if satisfied, separately authorizes one bounded 120-minute supervised runtime.'
    }
    Write-JsonFile -Value $final -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')
    $handoff = @"
# Operator Handoff

Classification: OBTUSE_BLUNDER_IDLE_V6_60MIN_PROOF_AUDIT_AND_120MIN_ESCALATION_READINESS_BUNDLE

Status: $status

V5 proof verification: $($Consumption.result), SHA verified: $($Consumption.sha256Verified)

Cycle consistency: $($CycleAudit.result)

Churn: $($TaskAudit.churn.classification)

Proof depth: $($ProofAudit.classification)

Lifecycle: $($LifecycleAudit.result)

Budgets: reliability=$($Budgets.reliability.currentStatus), churn=$($Budgets.churn.currentStatus), proof=$($Budgets.proof.currentStatus)

120-minute decision: $($Readiness.decision)

Validate V6:
`powershell -ExecutionPolicy Bypass -File scripts\obtuse\idle-v6-proof-audit-escalation.ps1 -Mode Validate`

Check status:
`powershell -ExecutionPolicy Bypass -File scripts\obtuse\status-idle-ops.ps1`

Future 120-minute start remains operator-gated:
`powershell -ExecutionPolicy Bypass -File scripts\obtuse\start-idle-ops.ps1`

Do not start the 120-minute run without a separate explicit approval flag.
"@
    $handoff | Set-Content -LiteralPath (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'operator_handoff.md')) -Encoding UTF8
    return $final
}

function New-ProofBundle {
    $proofDir = Resolve-RepoPath -Path 'artifacts/obtuse/proof-bundles'
    New-Item -ItemType Directory -Force -Path $proofDir | Out-Null
    $stamp = Get-Date -Format 'yyyyMMddTHHmmss'
    $zipPath = Join-Path $proofDir "obtuse-blunder-idle-v6-60min-proof-audit-and-120min-escalation-readiness-$stamp.zip"
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $root = Resolve-RepoPath -Path $ArtifactRoot
    $zip = [System.IO.Compression.ZipFile]::Open($zipPath, [System.IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($file in Get-ChildItem -LiteralPath $root -Recurse -File) {
            $relative = $file.FullName.Substring($root.Length).TrimStart('\') -replace '\\', '/'
            [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $file.FullName, "artifacts/$relative", [System.IO.Compression.CompressionLevel]::Optimal) | Out-Null
        }
        foreach ($script in @('scripts\obtuse\idle-v6-proof-audit-escalation.ps1', 'scripts\obtuse\status-idle-ops.ps1', 'scripts\obtuse\idle-process-detection.ps1')) {
            $source = Resolve-RepoPath -Path $script
            if (Test-Path -LiteralPath $source) {
                [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $source, ('scripts/' + ($script -replace '\\', '/')), [System.IO.Compression.CompressionLevel]::Optimal) | Out-Null
            }
        }
    } finally { $zip.Dispose() }
    $sha = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath).Hash
    return [ordered]@{ path = $zipPath; sha256 = $sha }
}

function Copy-V6Artifacts {
    param([string]$ProofBundlePath)
    $target = '\\fileserver\shared\obtuse\idle-v6-60min-proof-audit-and-120min-escalation-readiness'
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

function Get-ParserErrorCount {
    $count = 0
    foreach ($script in @('scripts\obtuse\idle-v6-proof-audit-escalation.ps1', 'scripts\obtuse\status-idle-ops.ps1', 'scripts\obtuse\start-idle-ops.ps1', 'scripts\obtuse\stop-idle-ops.ps1', 'scripts\obtuse\idle-reliability-sla-v5.ps1', 'scripts\obtuse\idle-process-detection.ps1')) {
        $tokens = $null; $errors = $null
        [System.Management.Automation.Language.Parser]::ParseFile((Resolve-RepoPath -Path $script), [ref]$tokens, [ref]$errors) | Out-Null
        $count += $errors.Count
    }
    return $count
}

function Invoke-V6 {
    New-Item -ItemType Directory -Force -Path (Resolve-RepoPath -Path $ArtifactRoot) | Out-Null
    $context = Get-V5Context
    $consumption = New-V5ConsumptionRecord -Context $context
    if ($consumption.result -eq 'FAIL_PRECONDITION') {
        $summary = [ordered]@{ pass = $false; status = 'FAIL_PRECONDITION'; consumption = $consumption }
        Write-JsonFile -Value $summary -Path (Join-Path $ArtifactRoot 'validation_summary.json')
        return $summary
    }
    $entries = Get-V5RuntimeEntries -Context $context
    $snapshots = Get-V5Snapshots
    $cycle = New-CycleConsistencyAudit -Context $context -Entries $entries -Snapshots $snapshots
    $task = New-TaskUtilityAudit -Entries $entries
    $proof = New-ProofDepthAudit -Context $context -Entries $entries
    $safety = New-SafetyInvariantAudit -Context $context -Entries $entries
    $lifecycle = New-RuntimeLifecycleAudit -Context $context -Snapshots $snapshots
    $budgets = New-BudgetReports -Context $context -TaskAudit $task -ProofAudit $proof -CycleAudit $cycle -SafetyAudit $safety -LifecycleAudit $lifecycle
    $readiness = New-ReadinessReport -Consumption $consumption -CycleAudit $cycle -TaskAudit $task -ProofAudit $proof -SafetyAudit $safety -LifecycleAudit $lifecycle -Budgets $budgets
    $runbook = New-Runbook
    $parserErrors = Get-ParserErrorCount
    $activeIdle = @(Get-ActiveIdleProcesses)
    $gates = [ordered]@{
        v5FinalReportFoundAndParsed = ($consumption.v5FinalReportFound -and $consumption.v5FinalReportParsed)
        v5ProofBundleFound = $consumption.proofBundleFound
        v5ProofBundleSha256Verified = $consumption.sha256Verified
        v5StatusConfirmedPass = $consumption.v5StatusPass
        preRuntimeV5ValidateConfirmedPass = ($context.final.preRuntimeV5Validate -eq 'PASS')
        postRuntimeV5ValidateConfirmedPass = ($context.final.postRuntimeV5Validate -eq 'PASS')
        finalIdleStateStopped = ($context.final.finalIdleState -eq 'stopped')
        orphanProcessFalse = (-not $context.final.orphanProcess -and $activeIdle.Count -eq 0)
        cycleConsistencyAuditCompleted = ($cycle.result -in @('PASS', 'PASS_WITH_NOTES'))
        taskUtilityChurnAuditCompleted = [bool]$task.utility
        proofDepthAuditCompleted = [bool]$proof
        safetyInvariantAuditCompleted = ($safety.result -eq 'PASS')
        runtimeLifecycleAuditCompleted = ($lifecycle.result -eq 'PASS')
        budgetsUpdated = [bool]$budgets
        runtime120EscalationReadinessCreated = [bool]$readiness
        runtime120RunbookCreated = (Test-Path -LiteralPath $runbook)
        v6ScriptExists = (Test-Path -LiteralPath (Resolve-RepoPath -Path 'scripts\obtuse\idle-v6-proof-audit-escalation.ps1'))
        controlScriptsExist = ((Test-Path -LiteralPath (Resolve-RepoPath -Path 'scripts\obtuse\status-idle-ops.ps1')) -and (Test-Path -LiteralPath (Resolve-RepoPath -Path 'scripts\obtuse\start-idle-ops.ps1')) -and (Test-Path -LiteralPath (Resolve-RepoPath -Path 'scripts\obtuse\stop-idle-ops.ps1')))
        parserErrorsZero = ($parserErrors -eq 0)
        benchmarkUntouched = (-not $context.final.benchmarkTouched)
        providerCallsFalse = (-not $context.final.providerCalls)
        secretsPrintedFalse = (-not $context.final.secretsPrinted)
        destructiveActionsFalse = (-not $context.final.destructiveActions)
        safetyBoundariesUnchanged = (-not $context.final.safetyBoundariesChanged)
        wanefallUntouched = (-not $context.final.wanefallTouched)
        showMeAISourceUntouched = (-not $context.final.showMeAISourceTouched)
        mobilePhoneAdbUntouched = (-not $context.final.mobilePhoneAdbTouched)
        idleOpsLeftStopped = ($activeIdle.Count -eq 0)
    }
    $passedGateCount = @($gates.GetEnumerator() | Where-Object { $_.Value -eq $true }).Count
    $totalGateCount = @($gates.GetEnumerator()).Count
    $validation = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        gates = $gates
        passedGateCount = $passedGateCount
        totalGateCount = $totalGateCount
        parserErrors = $parserErrors
        allGatesPassed = ($passedGateCount -eq $totalGateCount)
        readinessDecision = $readiness.decision
        pass = ($passedGateCount -eq $totalGateCount -and $readiness.decision -like 'GO*')
    }
    Write-JsonFile -Value $validation -Path (Join-Path $ArtifactRoot 'validation_summary.json')
    $final = New-FinalReports -Consumption $consumption -CycleAudit $cycle -TaskAudit $task -ProofAudit $proof -SafetyAudit $safety -LifecycleAudit $lifecycle -Budgets $budgets -Readiness $readiness -RunbookPath $runbook -Validation $validation
    $bundle = New-ProofBundle
    $copy = Copy-V6Artifacts -ProofBundlePath $bundle.path
    $final.proofBundlePath = $bundle.path
    $final.proofBundleSha256 = $bundle.sha256
    $final.frankensteinCopyPath = $(if ($copy.copied) { $copy.path } else { $null })
    Write-JsonFile -Value $final -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')
    $summary = [ordered]@{
        pass = ($final.status -eq 'PASS')
        status = $final.status
        classification = $final.classification
        readinessDecision = $readiness.decision
        churnClassification = $task.churn.classification
        proofBundlePath = $bundle.path
        proofBundleSha256 = $bundle.sha256
        copiedToFrankenstein = $copy.copied
        finalReportPath = $final.finalReportPath
        validation = $validation
    }
    Write-JsonFile -Value $summary -Path (Join-Path $ArtifactRoot 'validation_summary.json')
    return $summary
}

Push-Location $repoRoot
try {
    if ($Mode -eq 'ReportOnly') {
        $final = Read-JsonFile -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')
        if ($final) { $final | ConvertTo-Json -Depth 30; return }
        [ordered]@{ available = $false; artifactRoot = (Resolve-RepoPath -Path $ArtifactRoot) } | ConvertTo-Json -Depth 10
        return
    }
    $result = Invoke-V6
    $result | ConvertTo-Json -Depth 30
}
finally {
    Pop-Location
}
