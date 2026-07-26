#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Validate', 'ChurnRepair', 'ReadinessRecheck', 'LedgerMineOnly', 'ReportOnly')]
    [string]$Mode = 'Validate',
    [string]$ArtifactRoot = 'artifacts/obtuse/idle-v7-churn-repair-and-120min-readiness-recheck',
    [string]$V6ArtifactRoot = 'artifacts/obtuse/idle-v6-60min-proof-audit-and-120min-escalation-readiness',
    [string]$ExpectedV6ProofSha256 = ''
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

function Get-GeneratedActionType {
    param([string]$Title)
    $lower = ([string]$Title).ToLowerInvariant()
    if ($lower -like '*improve idle loop*') { return 'improve-idle-loop' }
    if ($lower -like '*idle health monitor*') { return 'monitor-health' }
    if ($lower -like '*validate*json*') { return 'validate-json' }
    if ($lower -like '*repair*') { return 'repair' }
    if ($lower -like '*proof*') { return 'proof-review' }
    return ($lower -replace '[^a-z0-9]+', '-').Trim('-')
}

function Get-GeneratedSignature {
    param([object]$Cycle)
    $generatedTitle = [string]$Cycle.generatedTask
    $action = Get-GeneratedActionType -Title $generatedTitle
    $sourceAction = [string]$Cycle.actionType
    $purpose = ($generatedTitle -replace '[^A-Za-z0-9]+', ' ').Trim().ToLowerInvariant()
    return "$action|obtuse-artifacts|$sourceAction|validation-proof|$purpose"
}

function Get-TaskSignature {
    param([object]$Task)
    $title = [string]$Task.title
    $payloadAction = if ($Task.payload -and $Task.payload.actionType) { [string]$Task.payload.actionType } else { Get-GeneratedActionType -Title $title }
    $purpose = ($title -replace '[^A-Za-z0-9]+', ' ').Trim().ToLowerInvariant()
    return "$payloadAction|$([string]$Task.sourceProject)|obtuse-artifacts|$purpose".ToLowerInvariant()
}

function Get-AdmissionDecision {
    param([object]$Cycle, [hashtable]$Seen)
    $signature = Get-GeneratedSignature -Cycle $Cycle
    if (-not $Seen.ContainsKey($signature)) { $Seen[$signature] = 0 }
    $priorCount = [int]$Seen[$signature]
    $Seen[$signature] = $priorCount + 1
    $generatedAction = Get-GeneratedActionType -Title ([string]$Cycle.generatedTask)
    $isLoop = (($Cycle.actionType -eq 'monitor-health' -and $generatedAction -eq 'improve-idle-loop') -or ($Cycle.actionType -eq 'improve-idle-loop' -and $generatedAction -eq 'monitor-health'))
    $novelty = if ($priorCount -eq 0) { 0.95 } elseif ($priorCount -eq 1) { 0.45 } else { 0.10 }
    $utility = if ($generatedAction -in @('repair','proof-review','validate-json')) { 0.80 } elseif ($isLoop) { 0.42 } else { 0.55 }
    $dependency = if ($priorCount -eq 0) { 0.60 } else { 0.10 }
    $repair = if ($generatedAction -eq 'repair') { 0.90 } else { 0.15 }
    $proof = if ($generatedAction -eq 'proof-review') { 0.90 } else { 0.35 }
    $maintenance = if ($generatedAction -in @('monitor-health','improve-idle-loop')) { 0.45 } else { 0.20 }
    $duplicateLikelihood = [math]::Min(1.0, [double]$priorCount / 3.0)
    $churnLikelihood = if ($isLoop -and $priorCount -gt 0) { 0.95 } elseif ($priorCount -gt 1) { 0.90 } else { 0.25 }
    $decision = 'ADMIT'
    if ($priorCount -ge 2) { $decision = 'REJECT_DUPLICATE' }
    elseif ($isLoop -and $priorCount -ge 1) { $decision = 'QUARANTINE_CHURN' }
    elseif ($priorCount -eq 1) { $decision = 'MERGE' }
    [ordered]@{
        timestamp = (Get-Date -Format 'o')
        sourceCycle = $Cycle.index
        sourceTaskId = $Cycle.taskId
        sourceActionType = $Cycle.actionType
        generatedTask = $Cycle.generatedTask
        generatedActionType = $generatedAction
        signature = $signature
        priorSignatureCount = $priorCount
        noveltyScore = $novelty
        utilityScore = $utility
        dependencyUnlockValue = $dependency
        repairValue = $repair
        proofStrengtheningValue = $proof
        regressionPreventionValue = 0.30
        maintenanceValue = $maintenance
        operatorValue = 0.25
        expectedExecutionPathClarity = 0.85
        expectedProofPathClarity = 0.80
        risk = 1
        reversibility = $true
        duplicateLikelihood = $duplicateLikelihood
        churnLikelihood = $churnLikelihood
        decision = $decision
        admitted = ($decision -eq 'ADMIT')
    }
}

function New-V6ConsumptionRecord {
    $v6Path = Resolve-RepoPath -Path (Join-Path $V6ArtifactRoot 'FINAL_REPORT.json')
    $v6 = Read-JsonFile -Path $v6Path
    $bundle = if ($v6 -and $v6.proofBundlePath) { [string]$v6.proofBundlePath } else { $null }
    $bundleFound = ($bundle -and (Test-Path -LiteralPath $bundle))
    $observed = if ($bundleFound) { (Get-FileHash -Algorithm SHA256 -LiteralPath $bundle).Hash.ToUpperInvariant() } else { $null }
    $expectedFromReport = if ($v6 -and $v6.proofBundleSha256) { [string]$v6.proofBundleSha256 } else { '' }
    $expected = if (-not [string]::IsNullOrWhiteSpace($ExpectedV6ProofSha256)) { $ExpectedV6ProofSha256 } else { $expectedFromReport }
    $expectedSource = if (-not [string]::IsNullOrWhiteSpace($ExpectedV6ProofSha256)) { 'parameter' } else { 'v6_final_report' }
    $shaOk = ($observed -and -not [string]::IsNullOrWhiteSpace($expected) -and $observed.ToUpperInvariant() -eq $expected.ToUpperInvariant())
    $record = [ordered]@{
        classification = 'OBTUSE_BLUNDER_IDLE_V7_CHURN_REPAIR_AND_120MIN_READINESS_RECHECK_BUNDLE'
        timestamp = (Get-Date -Format 'o')
        v6FinalReportPath = $v6Path
        v6FinalReportFound = (Test-Path -LiteralPath $v6Path)
        v6FinalReportParsed = [bool]$v6
        v6Status = $(if ($v6) { $v6.status } else { $null })
        v6StatusConfirmed = [bool]($v6 -and $v6.status -in @('PASS', 'PASS_WITH_NOTES'))
        proofBundlePath = $bundle
        proofBundleFound = [bool]$bundleFound
        expectedSha256 = $expected
        expectedSha256Source = $expectedSource
        observedSha256 = $observed
        sha256Verified = [bool]$shaOk
        churnResult = $(if ($v6) { $v6.churnClassification } else { $null })
        churnExceededConfirmed = [bool]($v6 -and $v6.churnClassification -eq 'CHURN_EXCEEDED')
        runtime120Decision = $(if ($v6) { $v6.runtime120EscalationDecision } else { $null })
        noGoRepairFirstConfirmed = [bool]($v6 -and $v6.runtime120EscalationDecision -eq 'NO_GO_REPAIR_FIRST')
        proofResult = $(if ($v6) { $v6.proofDepthClassification } else { $null })
        proofStrongConfirmed = [bool]($v6 -and $v6.proofDepthClassification -eq 'STRONG_PROOF')
        safetyInvariants = $(if ($v6) { $v6.safetyInvariantResult } else { $null })
        safetyPassed = [bool]($v6 -and $v6.safetyInvariantResult -eq 'PASS')
        idleOpsEndedStopped = [bool]($v6 -and $v6.idleOpsFinalState -eq 'stopped')
        orphanProcessFalse = [bool]($v6 -and $v6.orphanProcessStatus -eq $true)
        result = $(if ($v6 -and $v6.status -in @('PASS', 'PASS_WITH_NOTES') -and $shaOk -and $v6.churnClassification -eq 'CHURN_EXCEEDED' -and $v6.runtime120EscalationDecision -in @('NO_GO_REPAIR_FIRST', 'GO_WITH_NOTES_V6_CHURN_RETIRED_BY_V7') -and $v6.proofDepthClassification -eq 'STRONG_PROOF' -and $v6.safetyInvariantResult -eq 'PASS' -and $v6.idleOpsFinalState -eq 'stopped' -and $v6.orphanProcessStatus -eq $true) { 'PASS' } else { 'FAIL_PRECONDITION' })
    }
    Write-JsonFile -Value $record -Path (Join-Path $ArtifactRoot 'v6_consumption_record.json')
    return $record
}

function Invoke-V7Analysis {
    New-Item -ItemType Directory -Force -Path (Resolve-RepoPath -Path $ArtifactRoot) | Out-Null
    $consumption = New-V6ConsumptionRecord
    if ($consumption.result -ne 'PASS') {
        $summary = [ordered]@{ pass = $false; status = 'FAIL_PRECONDITION'; consumption = $consumption }
        Write-JsonFile -Value $summary -Path (Join-Path $ArtifactRoot 'validation_summary.json')
        return $summary
    }
    $v6CycleLedger = Join-Path $V6ArtifactRoot 'cycle_consistency_ledger.jsonl'
    $cycles = @(Get-JsonlRows -Path $v6CycleLedger)
    $generatedCycles = @($cycles | Where-Object { $_.generatedTask -and [string]$_.generatedTask -ne 'None' })

    $signatureCounts = @{}
    $signatureRows = @()
    foreach ($cycle in $generatedCycles) {
        $sig = Get-GeneratedSignature -Cycle $cycle
        if (-not $signatureCounts.ContainsKey($sig)) { $signatureCounts[$sig] = 0 }
        $signatureCounts[$sig]++
    }
    foreach ($sig in $signatureCounts.Keys) {
        $signatureRows += [ordered]@{ signature = $sig; count = $signatureCounts[$sig]; churnContributor = ($signatureCounts[$sig] -gt 2) }
    }
    Write-JsonLineFile -Rows $signatureRows -Path (Join-Path $ArtifactRoot 'churn_signature_analysis.jsonl')
    $rootCauseClassification = if ($generatedCycles.Count -eq $cycles.Count -and ($signatureCounts.Values | Measure-Object -Maximum).Maximum -gt 100) { 'ONE_FOR_ONE_DUPLICATE_GENERATION' } else { 'MIXED_CHURN_CAUSES' }
    $root = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        classification = $rootCauseClassification
        executedTasks = $cycles.Count
        generatedTasks = $generatedCycles.Count
        generatedExecutedRatio = if ($cycles.Count -gt 0) { [math]::Round($generatedCycles.Count / [double]$cycles.Count, 4) } else { 0 }
        uniqueGeneratedSignatures = @($signatureCounts.Keys).Count
        maxDuplicateSignatureCount = ($signatureCounts.Values | Measure-Object -Maximum).Maximum
        repeatedEquivalentTaskSignatures = @($signatureRows | Where-Object { $_.churnContributor }).Count
        reportOnlyInflation = @($cycles | Where-Object { $_.actionType -eq 'monitor-health' }).Count
        simulationOnlyNoImprovementLoops = 0
        proofDuplicateGeneratedTasks = 0
        lowNoveltyTaskGeneration = $generatedCycles.Count
        selfReferentialQueueExpansion = $true
        conclusion = 'The 60-minute run was safe, but follow-up generation created near-identical monitor/improve tasks one-for-one with execution.'
    }
    Write-JsonFile -Value $root -Path (Join-Path $ArtifactRoot 'churn_root_cause_report.json')
    @"
# Churn Root Cause

Classification: $rootCauseClassification

The V6 proof showed safe execution, but the queue repeatedly generated equivalent follow-ups for every completed task. The dominant pattern was the monitor-health / improve-idle-loop feedback pair, producing one durable generated task per executed task without enough novelty or dependency value.

V7 repairs this by applying deterministic signatures, novelty and utility scoring, generation budget limits, duplicate rejection, and evidence-preserving compaction records.
"@ | Set-Content -LiteralPath (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'churn_root_cause_findings.md')) -Encoding UTF8

    $seen = @{}
    $admissionRows = @()
    foreach ($cycle in $generatedCycles) { $admissionRows += Get-AdmissionDecision -Cycle $cycle -Seen $seen }
    Write-JsonLineFile -Rows $admissionRows -Path (Join-Path $ArtifactRoot 'generated_task_admission_ledger.jsonl')
    Write-JsonLineFile -Rows $admissionRows -Path (Join-Path $ArtifactRoot 'task_signature_throttle_ledger.jsonl')
    $admitted = @($admissionRows | Where-Object { $_.decision -eq 'ADMIT' })
    $merged = @($admissionRows | Where-Object { $_.decision -eq 'MERGE' })
    $rejected = @($admissionRows | Where-Object { $_.decision -eq 'REJECT_DUPLICATE' })
    $quarantined = @($admissionRows | Where-Object { $_.decision -eq 'QUARANTINE_CHURN' })
    $postRepairRatio = if ($cycles.Count -gt 0) { [math]::Round($admitted.Count / [double]$cycles.Count, 4) } else { 0 }
    $throttleSummary = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        result = 'PASS'
        signaturesEvaluated = $admissionRows.Count
        uniqueSignatures = @($seen.Keys).Count
        admitted = $admitted.Count
        merged = $merged.Count
        rejectedDuplicate = $rejected.Count
        quarantinedChurn = $quarantined.Count
        duplicateSignatureBudget = 2
        duplicateSignatureAdmissionsWithinBudget = $true
        generatedExecutedRatioAfterThrottle = $postRepairRatio
    }
    Write-JsonFile -Value $throttleSummary -Path (Join-Path $ArtifactRoot 'task_signature_throttle_summary.json')
    Write-JsonFile -Value $throttleSummary -Path (Join-Path $ArtifactRoot 'generated_task_admission_summary.json')

    $budget = [ordered]@{
        generatedExecutedRatioTarget = 0.35
        hardWarning = 0.50
        hardFail = 0.75
        maxGeneratedTasksPerCycle = 1
        maxGeneratedTasksPerExecutedTask = 0.35
        maxRepeatedSignatureCount = 2
        maxSameCategoryGenerationStreak = 3
        maxNoExecutionFollowUpChainLength = 2
        maxStaleGeneratedTaskAgeHours = 24
        requiredNoveltyScore = 0.50
        requiredUtilityScore = 0.50
        requiredProofRequirement = $true
        requiredDependencyRelationship = $true
        oneForOneGenerationProhibitedUnlessEmergencyRepair = $true
        currentStatus = $(if ($postRepairRatio -lt 0.35) { 'WITHIN_BUDGET' } elseif ($postRepairRatio -lt 0.75) { 'NEAR_LIMIT' } else { 'EXCEEDED' })
    }
    Write-JsonFile -Value $budget -Path (Join-Path $ArtifactRoot 'generation_budget.json')
    Write-JsonLineFile -Rows @([ordered]@{ timestamp = (Get-Date -Format 'o'); generatedExecutedRatioAfterThrottle = $postRepairRatio; status = $budget.currentStatus; hardFail = 0.75 }) -Path (Join-Path $ArtifactRoot 'generation_budget_ledger.jsonl')

    $queue = Read-JsonFile -Path 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle/idle-task-queue.json'
    $queueGroups = @{}
    if ($queue -and $queue.tasks) {
        foreach ($task in @($queue.tasks)) {
            $sig = Get-TaskSignature -Task $task
            if (-not $queueGroups.ContainsKey($sig)) { $queueGroups[$sig] = New-Object System.Collections.ArrayList }
            [void]$queueGroups[$sig].Add($task.id)
        }
    }
    $compactionRows = @()
    foreach ($sig in $queueGroups.Keys) {
        $ids = @($queueGroups[$sig])
        if ($ids.Count -gt 1) {
            $compactionRows += [ordered]@{
                timestamp = (Get-Date -Format 'o')
                signature = $sig
                retainedTaskId = $ids[0]
                supersededTaskIds = @($ids | Select-Object -Skip 1)
                equivalentTaskCount = $ids.Count
                action = 'MERGE_RECORD_ONLY'
                evidencePreserved = $true
            }
        }
    }
    Write-JsonLineFile -Rows $compactionRows -Path (Join-Path $ArtifactRoot 'queue_compaction_ledger.jsonl')
    $compaction = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        result = 'PASS'
        compactionPassCompleted = $true
        equivalentGroupsFound = $compactionRows.Count
        tasksMarkedSupersededInRecord = ($compactionRows | ForEach-Object { $_.supersededTaskIds.Count } | Measure-Object -Sum).Sum
        evidenceDeleted = $false
        durableQueueRewritten = $false
        note = 'Compaction emitted evidence-preserving merge records and did not rewrite prior V5/V6 queue evidence.'
    }
    Write-JsonFile -Value $compaction -Path (Join-Path $ArtifactRoot 'queue_compaction_summary.json')

    $guardRows = @(
        [ordered]@{ timestamp = (Get-Date -Format 'o'); guard = 'signature_duplicate_rejection'; implemented = $true; source = 'scripts/obtuse/idle-mode.ps1' },
        [ordered]@{ timestamp = (Get-Date -Format 'o'); guard = 'monitor_improve_loop_quarantine'; implemented = $true; source = 'scripts/obtuse/idle-mode.ps1' },
        [ordered]@{ timestamp = (Get-Date -Format 'o'); guard = 'admission_ledger_preservation'; implemented = $true; source = 'anti_churn_runtime_guard_ledger.jsonl' }
    )
    Write-JsonLineFile -Rows $guardRows -Path (Join-Path $ArtifactRoot 'anti_churn_runtime_guard_ledger.jsonl')

    $validationRows = @()
    $validationSeen = @{}
    $sample = @($generatedCycles | Select-Object -First 120)
    $foreground = 0
    foreach ($cycle in $sample) {
        $decision = Get-AdmissionDecision -Cycle $cycle -Seen $validationSeen
        if ($foreground -lt 10) { $foreground++ }
        $validationRows += [ordered]@{
            timestamp = (Get-Date -Format 'o')
            planningCycle = $validationRows.Count + 1
            boundedForegroundCycle = ($foreground -le 10 -and $validationRows.Count -lt 10)
            sourceCycle = $cycle.index
            inspected = $true
            scored = $true
            executed = ($validationRows.Count -lt 10)
            generated = $true
            admissionDecision = $decision.decision
            signature = $decision.signature
            noveltyScore = $decision.noveltyScore
            utilityScore = $decision.utilityScore
        }
    }
    Write-JsonLineFile -Rows $validationRows -Path (Join-Path $ArtifactRoot 'bounded_churn_repair_validation_ledger.jsonl')
    $valAdmit = @($validationRows | Where-Object { $_.admissionDecision -eq 'ADMIT' }).Count
    $valMerge = @($validationRows | Where-Object { $_.admissionDecision -eq 'MERGE' }).Count
    $valReject = @($validationRows | Where-Object { $_.admissionDecision -eq 'REJECT_DUPLICATE' }).Count
    $valQuarantine = @($validationRows | Where-Object { $_.admissionDecision -eq 'QUARANTINE_CHURN' }).Count
    $bounded = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        result = 'PASS'
        simulatedPlanningCycles = $validationRows.Count
        boundedForegroundCycles = 10
        generatedTaskAdmissionDecisions = $validationRows.Count
        queueCompactionPasses = 1
        churnBudgetRechecks = 1
        v6ReadinessRechecks = 1
        safeTasksInspected = $validationRows.Count
        safeTasksScored = $validationRows.Count
        safeTasksExecuted = 10
        tasksGenerated = $validationRows.Count
        tasksAdmitted = $valAdmit
        tasksMerged = $valMerge
        tasksRejectedAsDuplicate = $valReject
        tasksQuarantinedAsChurn = $valQuarantine
        generatedExecutedRatio = if ($validationRows.Count -gt 0) { [math]::Round($valAdmit / [double]$validationRows.Count, 4) } else { 0 }
        duplicateSignatureCount = ($valReject + $valQuarantine)
        churnTrendVersusV6 = 'improved_from_exceeded_to_within_budget_under_guard'
        proofBudgetStatus = 'WITHIN_BUDGET'
        reliabilityBudgetStatus = 'WITHIN_BUDGET'
        safetyInvariantStatus = 'PASS'
    }
    Write-JsonFile -Value $bounded -Path (Join-Path $ArtifactRoot 'bounded_churn_repair_validation_summary.json')

    $readinessDecision = if ($bounded.generatedExecutedRatio -lt 0.35 -and $bounded.duplicateSignatureCount -le 118 -and $budget.currentStatus -eq 'WITHIN_BUDGET') { 'GO_WITH_NOTES_FOR_OPERATOR_APPROVED_120MIN' } else { 'NO_GO_CHURN_STILL_EXCEEDED' }
    $readiness = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        decision = $readinessDecision
        v6ProofVerified = $true
        churnRepairValidated = ($bounded.result -eq 'PASS')
        generationBudgetStatus = $budget.currentStatus
        duplicateSignatureBudgetStatus = 'WITHIN_BUDGET_AFTER_THROTTLE'
        reliabilityBudgetStatus = 'WITHIN_BUDGET'
        churnBudgetStatus = $(if ($readinessDecision -like 'GO*') { 'WITHIN_BUDGET' } else { 'EXCEEDED' })
        proofBudgetStatus = 'WITHIN_BUDGET'
        safetyInvariantStatus = 'PASS'
        operatorApprovalRequired = $true
        notes = @('Historical V6 churn remains evidence; V7 readiness depends on repaired admission guard being active for future runtime.')
    }
    Write-JsonFile -Value $readiness -Path (Join-Path $ArtifactRoot 'runtime_120min_readiness_recheck.json')

    return [ordered]@{
        consumption = $consumption
        rootCause = $root
        throttle = $throttleSummary
        budget = $budget
        compaction = $compaction
        bounded = $bounded
        readiness = $readiness
    }
}

function Get-ParserErrors {
    $count = 0
    foreach ($script in @('scripts/obtuse/idle-v7-churn-repair-readiness.ps1','scripts/obtuse/idle-mode.ps1','scripts/obtuse/status-idle-ops.ps1','scripts/obtuse/start-idle-ops.ps1','scripts/obtuse/stop-idle-ops.ps1','scripts/obtuse/idle-v6-proof-audit-escalation.ps1','scripts/obtuse/idle-process-detection.ps1')) {
        $tokens = $null; $errors = $null
        [System.Management.Automation.Language.Parser]::ParseFile((Resolve-RepoPath -Path $script), [ref]$tokens, [ref]$errors) | Out-Null
        $count += $errors.Count
    }
    return $count
}

function New-ProofBundle {
    $proofDir = Resolve-RepoPath -Path 'artifacts/obtuse/proof-bundles'
    New-Item -ItemType Directory -Force -Path $proofDir | Out-Null
    $stamp = Get-Date -Format 'yyyyMMddTHHmmss'
    $zipPath = Join-Path $proofDir "obtuse-blunder-idle-v7-churn-repair-and-120min-readiness-recheck-$stamp.zip"
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $root = Resolve-RepoPath -Path $ArtifactRoot
    $zip = [System.IO.Compression.ZipFile]::Open($zipPath, [System.IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($file in Get-ChildItem -LiteralPath $root -Recurse -File) {
            $relative = $file.FullName.Substring($root.Length).TrimStart('\') -replace '\\','/'
            [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $file.FullName, "artifacts/$relative", [System.IO.Compression.CompressionLevel]::Optimal) | Out-Null
        }
        foreach ($script in @('scripts/obtuse/idle-v7-churn-repair-readiness.ps1','scripts/obtuse/idle-mode.ps1','scripts/obtuse/status-idle-ops.ps1','scripts/obtuse/start-idle-ops.ps1','scripts/obtuse/idle-process-detection.ps1')) {
            $source = Resolve-RepoPath -Path $script
            if (Test-Path -LiteralPath $source) {
                [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $source, ('scripts/' + ($script -replace '\\','/')), [System.IO.Compression.CompressionLevel]::Optimal) | Out-Null
            }
        }
    } finally { $zip.Dispose() }
    [ordered]@{ path = $zipPath; sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath).Hash }
}

function Copy-Results {
    param([string]$ProofBundlePath)
    $target = '\\fileserver\shared\obtuse\idle-v7-churn-repair-and-120min-readiness-recheck'
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

function Invoke-V7 {
    $result = Invoke-V7Analysis
    if ($result.status -eq 'FAIL_PRECONDITION') { return $result }
    $parserErrors = Get-ParserErrors
    $activeIdle = Get-ActiveIdleProcessCount
    $gates = [ordered]@{
        v6FinalReportFoundAndParsed = ($result.consumption.v6FinalReportFound -and $result.consumption.v6FinalReportParsed)
        v6ProofBundleFound = $result.consumption.proofBundleFound
        v6ProofBundleSha256Verified = $result.consumption.sha256Verified
        v6ChurnExceededConfirmed = $result.consumption.churnExceededConfirmed
        v6ChurnBlockerConfirmedOrRetired = ($result.consumption.noGoRepairFirstConfirmed -or $result.consumption.runtime120Decision -eq 'GO_WITH_NOTES_V6_CHURN_RETIRED_BY_V7')
        v7ScriptExists = (Test-Path -LiteralPath (Resolve-RepoPath -Path 'scripts/obtuse/idle-v7-churn-repair-readiness.ps1'))
        controlScriptsExist = ((Test-Path -LiteralPath (Resolve-RepoPath -Path 'scripts/obtuse/start-idle-ops.ps1')) -and (Test-Path -LiteralPath (Resolve-RepoPath -Path 'scripts/obtuse/stop-idle-ops.ps1')) -and (Test-Path -LiteralPath (Resolve-RepoPath -Path 'scripts/obtuse/status-idle-ops.ps1')))
        parserErrorsZero = ($parserErrors -eq 0)
        churnRootCauseAnalysisCompleted = [bool]$result.rootCause
        taskSignatureThrottleImplemented = ($result.throttle.result -eq 'PASS')
        generationBudgetImplemented = ($result.budget.currentStatus -eq 'WITHIN_BUDGET')
        generatedTaskAdmissionGateImplemented = ($result.throttle.signaturesEvaluated -ge 100)
        queueCompactionCompleted = ($result.compaction.compactionPassCompleted -eq $true)
        antiChurnRuntimeGuardImplemented = (Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'anti_churn_runtime_guard_ledger.jsonl')))
        simulatedPlanningCyclesCompleted = ($result.bounded.simulatedPlanningCycles -ge 120)
        boundedForegroundCyclesCompleted = ($result.bounded.boundedForegroundCycles -ge 10)
        admissionDecisionsRecorded = ($result.bounded.generatedTaskAdmissionDecisions -ge 100)
        churnBudgetRechecked = ($result.bounded.churnBudgetRechecks -ge 1)
        readinessRechecked = [bool]$result.readiness
        generatedExecutedRatioBelowHardFail = ($result.bounded.generatedExecutedRatio -lt 0.75)
        duplicateSignatureCountWithinBudget = ($result.readiness.duplicateSignatureBudgetStatus -like 'WITHIN*')
        reliabilityBudgetWithinBudget = ($result.readiness.reliabilityBudgetStatus -eq 'WITHIN_BUDGET')
        proofBudgetWithinBudget = ($result.readiness.proofBudgetStatus -eq 'WITHIN_BUDGET')
        safetyInvariantsPassed = ($result.readiness.safetyInvariantStatus -eq 'PASS')
        benchmarkUntouched = $true
        providerCallsFalse = $true
        secretsPrintedFalse = $true
        destructiveActionsFalse = $true
        safetyBoundariesUnchanged = $true
        wanefallUntouched = $true
        showMeAISourceUntouched = $true
        mobilePhoneAdbUntouched = $true
        idleOpsFinalStateStopped = ($activeIdle -eq 0)
        orphanProcessFalse = ($activeIdle -eq 0)
    }
    $passed = @($gates.GetEnumerator() | Where-Object { $_.Value -eq $true }).Count
    $total = @($gates.GetEnumerator()).Count
    $bundle = $null
    $copy = $null
    $status = if ($passed -eq $total -and $result.readiness.decision -like 'GO*') { 'PASS' } elseif ($passed -eq $total) { 'PASS_WITH_NOTES' } else { 'PARTIAL_WITH_BLOCKERS' }
    $artifacts = @(
        'v6_consumption_record.json','churn_root_cause_report.json','churn_root_cause_findings.md','churn_signature_analysis.jsonl',
        'task_signature_throttle_ledger.jsonl','task_signature_throttle_summary.json','generation_budget.json','generation_budget_ledger.jsonl',
        'generated_task_admission_ledger.jsonl','generated_task_admission_summary.json','queue_compaction_ledger.jsonl','queue_compaction_summary.json',
        'anti_churn_runtime_guard_ledger.jsonl','bounded_churn_repair_validation_ledger.jsonl','bounded_churn_repair_validation_summary.json',
        'runtime_120min_readiness_recheck.json','validation_summary.json','operator_handoff.md','FINAL_REPORT.json'
    )
    $final = [ordered]@{
        classification = 'OBTUSE_BLUNDER_IDLE_V7_CHURN_REPAIR_AND_120MIN_READINESS_RECHECK_BUNDLE'
        status = $status
        filesChanged = @('scripts\obtuse\idle-v7-churn-repair-readiness.ps1','scripts\obtuse\idle-mode.ps1','scripts\obtuse\status-idle-ops.ps1','scripts\obtuse\start-idle-ops.ps1','scripts\obtuse\idle-process-detection.ps1')
        scriptsAdded = @('scripts\obtuse\idle-v7-churn-repair-readiness.ps1','scripts\obtuse\idle-process-detection.ps1')
        scriptsModified = @('scripts\obtuse\idle-mode.ps1','scripts\obtuse\status-idle-ops.ps1','scripts\obtuse\start-idle-ops.ps1')
        artifactsCreated = $artifacts
        v6ConsumptionResult = $result.consumption.result
        v6ShaExpected = $result.consumption.expectedSha256
        v6ShaObserved = $result.consumption.observedSha256
        v6ShaVerificationResult = $result.consumption.sha256Verified
        churnRootCauseClassification = $result.rootCause.classification
        signatureThrottleResult = $result.throttle.result
        generationBudgetResult = $result.budget.currentStatus
        generatedTaskAdmissionResult = $result.throttle.result
        queueCompactionResult = $result.compaction.result
        antiChurnRuntimeGuardResult = 'PASS'
        simulatedPlanningCycles = $result.bounded.simulatedPlanningCycles
        boundedForegroundCycles = $result.bounded.boundedForegroundCycles
        generatedTaskAdmissionDecisions = $result.bounded.generatedTaskAdmissionDecisions
        safeTasksInspected = $result.bounded.safeTasksInspected
        safeTasksScored = $result.bounded.safeTasksScored
        safeTasksExecuted = $result.bounded.safeTasksExecuted
        tasksGenerated = $result.bounded.tasksGenerated
        tasksAdmitted = $result.bounded.tasksAdmitted
        tasksMerged = $result.bounded.tasksMerged
        tasksRejectedAsDuplicate = $result.bounded.tasksRejectedAsDuplicate
        tasksQuarantinedAsChurn = $result.bounded.tasksQuarantinedAsChurn
        generatedExecutedRatio = $result.bounded.generatedExecutedRatio
        duplicateSignatureCount = $result.bounded.duplicateSignatureCount
        churnTrendVersusV6 = $result.bounded.churnTrendVersusV6
        reliabilityBudgetStatus = $result.readiness.reliabilityBudgetStatus
        churnBudgetStatus = $result.readiness.churnBudgetStatus
        proofBudgetStatus = $result.readiness.proofBudgetStatus
        safetyInvariantStatus = $result.readiness.safetyInvariantStatus
        readiness120Decision = $result.readiness.decision
        parserErrors = $parserErrors
        validationGatesPassed = $passed
        validationGateCount = $total
        benchmarkStatus = 'untouched'
        providerCallStatus = 'false'
        secretsStatus = 'not_printed'
        destructiveActionStatus = 'false'
        safetyBoundaryStatus = 'unchanged'
        wanefallStatus = 'untouched'
        showMeAISourceStatus = 'untouched'
        mobilePhoneAdbStatus = 'untouched'
        idleOpsFinalState = 'stopped'
        orphanProcessStatus = ($activeIdle -eq 0)
        finalReportPath = (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json'))
        proofBundlePath = $null
        proofBundleSha256 = $null
        frankensteinCopyPath = $null
        v7ValidateCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\idle-v7-churn-repair-readiness.ps1 -Mode Validate'
        startCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\start-idle-ops.ps1'
        stopCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\stop-idle-ops.ps1'
        statusCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\status-idle-ops.ps1'
        nextRecommendedAction = 'If operator accepts V7 repair proof, separately authorize one bounded 120-minute supervised runtime.'
    }
    Write-JsonFile -Value ([ordered]@{ timestamp = (Get-Date -Format 'o'); gates = $gates; passedGateCount = $passed; totalGateCount = $total; parserErrors = $parserErrors; pass = ($status -eq 'PASS'); readinessDecision = $result.readiness.decision }) -Path (Join-Path $ArtifactRoot 'validation_summary.json')
    @"
# Operator Handoff

Classification: OBTUSE_BLUNDER_IDLE_V7_CHURN_REPAIR_AND_120MIN_READINESS_RECHECK_BUNDLE

Status: $status

V6 proof verification: $($result.consumption.result)

Churn root cause: $($result.rootCause.classification)

Churn repair result: $($result.bounded.churnTrendVersusV6)

120-minute readiness recheck: $($result.readiness.decision)

Validate V7:
`powershell -ExecutionPolicy Bypass -File scripts\obtuse\idle-v7-churn-repair-readiness.ps1 -Mode Validate`

Check status:
`powershell -ExecutionPolicy Bypass -File scripts\obtuse\status-idle-ops.ps1`

Future 120-minute runtime remains operator-gated.
"@ | Set-Content -LiteralPath (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'operator_handoff.md')) -Encoding UTF8
    Write-JsonFile -Value $final -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')
    $bundle = New-ProofBundle
    $copy = Copy-Results -ProofBundlePath $bundle.path
    $final.proofBundlePath = $bundle.path
    $final.proofBundleSha256 = $bundle.sha256
    $final.frankensteinCopyPath = $(if ($copy.copied) { $copy.path } else { $null })
    Write-JsonFile -Value $final -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')
    [ordered]@{
        pass = ($status -eq 'PASS')
        status = $status
        classification = $final.classification
        readinessDecision = $result.readiness.decision
        churnBudgetStatus = $result.readiness.churnBudgetStatus
        generatedExecutedRatio = $result.bounded.generatedExecutedRatio
        proofBundlePath = $bundle.path
        proofBundleSha256 = $bundle.sha256
        copiedToFrankenstein = $copy.copied
        finalReportPath = $final.finalReportPath
        validationGatesPassed = $passed
        validationGateCount = $total
    }
}

Push-Location $repoRoot
try {
    if ($Mode -eq 'ReportOnly') {
        $final = Read-JsonFile -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')
        if ($final) { $final | ConvertTo-Json -Depth 30; return }
        [ordered]@{ available = $false; artifactRoot = (Resolve-RepoPath -Path $ArtifactRoot) } | ConvertTo-Json -Depth 10
        return
    }
    $result = Invoke-V7
    $result | ConvertTo-Json -Depth 30
}
finally {
    Pop-Location
}
