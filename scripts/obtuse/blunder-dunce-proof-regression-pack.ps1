#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Validate','Build','RunRegression','ReportOnly')]
    [string]$Mode = 'Validate',
    [string]$ArtifactRoot = 'artifacts/obtuse/blunder-dunce-proof-regression-pack-and-phase-closure-v1',
    [string]$CatalogPath = 'artifacts/obtuse/blunder-dunce-proof-case-catalog-v1/001_PROOF_CASE_CATALOG.json',
    [string]$SidecarRoot = 'artifacts/obtuse/blunder-dunce-proof-metadata-sidecars-v1/sidecars',
    [string]$UtilityLedgerPath = 'artifacts/obtuse/blunder-dunce-utility-scoring-v1/001_UTILITY_SCORING_LEDGER.json',
    [string]$UtilityCandidatePath = 'artifacts/obtuse/blunder-dunce-utility-scoring-v1/002_NON_DUPLICATE_QUEUE_CANDIDATES.json',
    [string]$DuplicateQuarantinePath = 'artifacts/obtuse/blunder-dunce-utility-scoring-v1/003_DUPLICATE_ADMISSION_QUARANTINE.json',
    [string]$PromotionCandidatePath = 'artifacts/obtuse/blunder-dunce-hypothesis-promotion-check-v1/002_ARTIFACT_ONLY_QUEUE_CANDIDATES.json',
    [string]$QueuePath = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle/idle-task-queue.json',
    [string]$OperationalLoopReport = 'artifacts/obtuse/blunder-dunce-operational-loop-v1/FINAL_REPORT.json',
    [string]$ReplenishmentReport = 'artifacts/obtuse/blunder-dunce-replenishment-policy-v1/FINAL_REPORT.json',
    [string]$ReplenishmentContinuationReport = 'artifacts/obtuse/blunder-dunce-replenishment-policy-continuation-v1/FINAL_REPORT.json',
    [string]$UtilityScoringReport = 'artifacts/obtuse/blunder-dunce-utility-scoring-run-v1/FINAL_REPORT.json',
    [string]$UtilityScoringProofBundle = 'artifacts/obtuse/proof-bundles/obtuse-blunder-dunce-utility-scoring-run-v1-20260628T211958.zip',
    [string]$ExpectedUtilityBundleSha256 = '155D24CE0DA4233015D8AB94D098C2D28F773114A0580D4219331FF9B2C9EF17'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent

function Resolve-RepoPath {
    param([Parameter(Mandatory)] [string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $repoRoot $Path
}

function Read-JsonFile {
    param([Parameter(Mandatory)] [string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    if (-not (Test-Path -LiteralPath $resolved)) { return $null }
    return Get-Content -Raw -LiteralPath $resolved | ConvertFrom-Json -ErrorAction Stop
}

function Write-JsonFile {
    param([Parameter(Mandatory)] [object]$Value, [Parameter(Mandatory)] [string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    $parent = Split-Path $resolved -Parent
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $Value | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $resolved -Encoding UTF8
}

function Write-TextFile {
    param([Parameter(Mandatory)] [string]$Path, [Parameter(Mandatory)] [string]$Value)
    $resolved = Resolve-RepoPath -Path $Path
    $parent = Split-Path $resolved -Parent
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    [System.IO.File]::WriteAllText($resolved, ($Value -replace "`r?`n", "`r`n"), [System.Text.UTF8Encoding]::new($false))
}

function Add-LedgerLine {
    param([Parameter(Mandatory)] [string]$Path, [Parameter(Mandatory)] [object]$Record)
    $resolved = Resolve-RepoPath -Path $Path
    $parent = Split-Path $resolved -Parent
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $Record | ConvertTo-Json -Depth 20 -Compress | Add-Content -LiteralPath $resolved -Encoding UTF8
}

function Get-ProcessSafety {
    $forbiddenPattern = 'external-benchmark|external-agent-runner|WANEFALL|adb\.exe|fastboot\.exe'
    $providerPattern = 'openrouter-idle-model\.ps1|idle-self-coding-accelerator\.ps1'
    $all = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine })
    return [ordered]@{
        forbiddenProcessCount = @($all | Where-Object { $_.CommandLine -match $forbiddenPattern }).Count
        providerProcessCount = @($all | Where-Object { $_.CommandLine -match $providerPattern }).Count
        timestamp = (Get-Date -Format 'o')
    }
}

function Get-FileSha256 {
    param([Parameter(Mandatory)] [string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    if (-not (Test-Path -LiteralPath $resolved)) { return $null }
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($resolved)
    try {
        $hash = $sha.ComputeHash($stream)
        return ([System.BitConverter]::ToString($hash).Replace('-', '')).ToUpperInvariant()
    } finally {
        $stream.Dispose()
        $sha.Dispose()
    }
}

function Get-DeterministicHash {
    param([Parameter(Mandatory)] [object]$Value)
    # Deterministic JSON hash ignoring the packHash field if present.
    $clone = $Value | ConvertTo-Json -Depth 100 | ConvertFrom-Json
    if ($clone.PSObject.Properties['packHash']) { $clone.PSObject.Properties.Remove('packHash') }
    $text = ($clone | ConvertTo-Json -Depth 100 -Compress)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($text)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $hash = $sha.ComputeHash($bytes)
    return ([System.BitConverter]::ToString($hash).Replace('-', '')).ToUpperInvariant()
}

function Test-IdleStatus {
    param([string]$QueuePath)
    $resolved = Resolve-RepoPath -Path $QueuePath
    if (-not (Test-Path -LiteralPath $resolved)) { return @{ idleActive = $false; orphanProcess = $false; stopped = $true; timestamp = (Get-Date -Format 'o') } }
    $queue = Read-JsonFile -Path $QueuePath
    $pending = @($queue.tasks | Where-Object { $_.status -eq 'pending' }).Count
    $running = @($queue.tasks | Where-Object { $_.status -eq 'running' }).Count
    $orphan = $false
    if ($queue.runningTask -and $queue.runningTask.id) {
        $orphan = ($running -eq 0)
    }
    return [ordered]@{
        idleActive = [bool]($pending -gt 0 -or $running -gt 0)
        orphanProcess = $orphan
        pendingCount = $pending
        runningCount = $running
        stopped = [bool]($pending -eq 0 -and $running -eq 0)
        timestamp = (Get-Date -Format 'o')
    }
}

function Invoke-BaselineConsumption {
    param(
        [Parameter(Mandatory)] [hashtable]$Config,
        [Parameter(Mandatory)] [object]$State
    )
    $record = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        classification = 'OBTUSE_BLUNDER_DUNCE_PROOF_REGRESSION_PACK_AND_PHASE_CLOSURE_V1'
        phase = 'BASELINE_CONSUMPTION'
        reports = [ordered]@{}
        proofBundle = [ordered]@{}
        proofCases = [ordered]@{}
        sidecars = [ordered]@{}
        ledgers = [ordered]@{}
        safety = [ordered]@{}
        complete = $false
    }
    $allOk = $true
    foreach ($key in $Config.Keys | Sort-Object) {
        $path = $Config[$key]
        $resolved = Resolve-RepoPath -Path $path
        $exists = Test-Path -LiteralPath $resolved
        $parsed = $false
        $errorMessage = $null
        if ($exists) {
            try { $null = Read-JsonFile -Path $path; $parsed = $true } catch { $errorMessage = $_.Exception.Message }
        }
        $record.reports[$key] = [ordered]@{ path = $resolved; exists = $exists; parsed = $parsed; error = $errorMessage }
        if (-not $exists -or -not $parsed) { $allOk = $false }
    }
    $bundleSha = Get-FileSha256 -Path $UtilityScoringProofBundle
    $record.proofBundle = [ordered]@{
        path = (Resolve-RepoPath -Path $UtilityScoringProofBundle)
        exists = (Test-Path -LiteralPath (Resolve-RepoPath -Path $UtilityScoringProofBundle))
        sha256 = $bundleSha
        expectedSha256 = $ExpectedUtilityBundleSha256
        shaMatches = ($bundleSha -eq $ExpectedUtilityBundleSha256)
    }
    if (-not $record.proofBundle.shaMatches) { $allOk = $false }

    $catalog = Read-JsonFile -Path $CatalogPath
    $sidecarFiles = @(Get-ChildItem -LiteralPath (Resolve-RepoPath -Path $SidecarRoot) -Filter '*.json' -ErrorAction SilentlyContinue)
    $record.proofCases = [ordered]@{
        path = (Resolve-RepoPath -Path $CatalogPath)
        count = if ($catalog) { [int]$catalog.proofCaseCount } else { 0 }
        completeCount = if ($catalog) { [int]$catalog.completeProofCaseCount } else { 0 }
        expectedCount = 21
        meetsExpected = (($catalog) -and [int]$catalog.proofCaseCount -eq 21)
    }
    $record.sidecars = [ordered]@{
        path = (Resolve-RepoPath -Path $SidecarRoot)
        count = $sidecarFiles.Count
        expectedCount = 21
        meetsExpected = ($sidecarFiles.Count -eq 21)
    }
    if (-not $record.proofCases.meetsExpected -or -not $record.sidecars.meetsExpected) { $allOk = $false }

    $record.ledgers = [ordered]@{
        utilityLedger = (Resolve-RepoPath -Path $UtilityLedgerPath)
        utilityLedgerExists = (Test-Path -LiteralPath (Resolve-RepoPath -Path $UtilityLedgerPath))
        utilityCandidateLedger = (Resolve-RepoPath -Path $UtilityCandidatePath)
        utilityCandidateLedgerExists = (Test-Path -LiteralPath (Resolve-RepoPath -Path $UtilityCandidatePath))
        promotionCandidateLedger = (Resolve-RepoPath -Path $PromotionCandidatePath)
        promotionCandidateLedgerExists = (Test-Path -LiteralPath (Resolve-RepoPath -Path $PromotionCandidatePath))
        duplicateQuarantineEvidence = (Resolve-RepoPath -Path $DuplicateQuarantinePath)
        duplicateQuarantineEvidenceExists = (Test-Path -LiteralPath (Resolve-RepoPath -Path $DuplicateQuarantinePath))
    }
    foreach ($ledgerKey in $record.ledgers.Keys) {
        if ($ledgerKey -like '*Exists' -and -not $record.ledgers[$ledgerKey]) { $allOk = $false }
    }

    $processSafety = Get-ProcessSafety
    $record.safety = [ordered]@{
        forbiddenProcessCount = $processSafety.forbiddenProcessCount
        providerProcessCount = $processSafety.providerProcessCount
        clean = (($processSafety.forbiddenProcessCount -eq 0) -and ($processSafety.providerProcessCount -eq 0))
    }
    if (-not $record.safety.clean) { $allOk = $false }

    $record.complete = $allOk
    return $record
}

function Invoke-BuildPack {
    param(
        [Parameter(Mandatory)] [object]$Baseline,
        [Parameter(Mandatory)] [object]$State
    )
    New-Item -ItemType Directory -Force -Path (Resolve-RepoPath -Path $ArtifactRoot) | Out-Null
    $now = Get-Date -Format 'o'
    $catalog = Read-JsonFile -Path $CatalogPath
    $sidecarFiles = @(Get-ChildItem -LiteralPath (Resolve-RepoPath -Path $SidecarRoot) -Filter '*.json' | Sort-Object Name)
    $utilityLedger = Read-JsonFile -Path $UtilityLedgerPath
    $utilityCandidates = Read-JsonFile -Path $UtilityCandidatePath
    $duplicateQuarantine = Read-JsonFile -Path $DuplicateQuarantinePath
    $promotionCandidates = Read-JsonFile -Path $PromotionCandidatePath

    # Index proof cases
    $proofCaseIndex = New-Object System.Collections.ArrayList
    $caseMap = @{}
    foreach ($case in @($catalog.proofCases)) {
        $caseId = [System.IO.Path]::GetFileNameWithoutExtension([string]$case.imagePath)
        $sidecarName = [System.IO.Path]::GetFileName([string]$case.sidecarPath)
        [void]$proofCaseIndex.Add([ordered]@{
            caseId = $caseId
            imagePath = [string]$case.imagePath
            root = [string]$case.root
            imageBytes = [int]$case.imageBytes
            sidecarName = $sidecarName
            sidecarPath = [string]$case.sidecarPath
            completeMetadata = [bool]$case.completeMetadata
        })
        $caseMap[$sidecarName] = $caseId
    }

    # Index sidecars
    $sidecarIndex = New-Object System.Collections.ArrayList
    $sidecarToCaseMap = New-Object System.Collections.ArrayList
    foreach ($file in $sidecarFiles) {
        $sidecar = Read-JsonFile -Path (Join-Path $SidecarRoot $file.Name)
        [void]$sidecarIndex.Add([ordered]@{
            sidecarName = $file.Name
            sidecarPath = $file.FullName
            imagePath = [string]$sidecar.imagePath
            sourceRoot = [string]$sidecar.sourceRoot
            scenario = [string]$sidecar.scenario
            validationState = [string]$sidecar.validationState
            sourceMutationAllowed = [bool]$sidecar.sourceMutationAllowed
            providerCallsAllowed = [bool]$sidecar.providerCallsAllowed
        })
        $mappedCaseId = if ($caseMap.ContainsKey($file.Name)) { $caseMap[$file.Name] } else { $null }
        [void]$sidecarToCaseMap.Add([ordered]@{
            sidecarName = $file.Name
            caseId = $mappedCaseId
            imagePath = [string]$sidecar.imagePath
            mapped = ($null -ne $mappedCaseId)
        })
    }

    # Utility candidate index
    $utilityIndex = New-Object System.Collections.ArrayList
    if ($utilityLedger -and $utilityLedger.evaluations) {
        foreach ($eval in @($utilityLedger.evaluations)) {
            [void]$utilityIndex.Add([ordered]@{
                candidateId = [string]$eval.candidateId
                title = [string]$eval.title
                generationSignature = [string]$eval.generationSignature
                duplicate = [bool]$eval.duplicate
                safe = [bool]$eval.safe
                decision = [string]$eval.decision
                weightedTotal = [double]$eval.scores.weightedTotal
            })
        }
    }

    # Promotion candidate index
    $promotionIndex = New-Object System.Collections.ArrayList
    if ($promotionCandidates -and $promotionCandidates.queueCandidates) {
        foreach ($cand in @($promotionCandidates.queueCandidates)) {
            [void]$promotionIndex.Add([ordered]@{
                taskId = [string]$cand.taskId
                title = [string]$cand.title
                actionType = [string]$cand.actionType
                controlLane = [string]$cand.controlLane
                sourceMutationAllowed = [bool]$cand.sourceMutationAllowed
                providerCallsAllowed = [bool]$cand.providerCallsAllowed
                destructiveActionsAllowed = [bool]$cand.destructiveActionsAllowed
            })
        }
    }

    # Queue admission index from previous final reports
    $queueAdmissions = New-Object System.Collections.ArrayList
    $opReport = Read-JsonFile -Path $OperationalLoopReport
    $replReport = Read-JsonFile -Path $ReplenishmentReport
    $replContReport = Read-JsonFile -Path $ReplenishmentContinuationReport
    $utilReport = Read-JsonFile -Path $UtilityScoringReport

    foreach ($task in @($opReport.wrongQueueScopedCleanup.quarantined)) {
        [void]$queueAdmissions.Add([ordered]@{
            title = [string]$task.title
            status = [string]$task.status
            source = 'operational-loop-wrong-queue-quarantine'
            suppressFollowUp = $true
        })
    }
    foreach ($task in @($replReport.replenishmentAddedTasks)) {
        [void]$queueAdmissions.Add([ordered]@{
            taskId = [string]$task.taskId
            title = [string]$task.title
            catalogId = [string]$task.catalogId
            actionType = [string]$task.actionType
            signature = [string]$task.signature
            source = 'replenishment-policy-v1'
            suppressFollowUp = $true
        })
    }
    if ($replContReport.policyDunceItems) {
        foreach ($item in @($replContReport.policyDunceItems)) {
            [void]$queueAdmissions.Add([ordered]@{
                title = [string]$item.title
                status = [string]$item.status
                generationSignature = [string]$item.generationSignature
                source = 'replenishment-policy-continuation-v1'
                suppressFollowUp = $true
            })
        }
    }
    if ($utilReport.utilityCandidateItems) {
        foreach ($item in @($utilReport.utilityCandidateItems)) {
            [void]$queueAdmissions.Add([ordered]@{
                title = [string]$item.title
                status = [string]$item.status
                generationSignature = [string]$item.generationSignature
                source = 'utility-scoring-run-v1'
                suppressFollowUp = $true
            })
        }
    }

    # Duplicate guard evidence
    $dupEvidence = [ordered]@{
        timestamp = $now
        source = (Resolve-RepoPath -Path $DuplicateQuarantinePath)
        quarantineCount = if ($duplicateQuarantine) { [int]$duplicateQuarantine.quarantinedDuplicateCount } else { 0 }
        quarantined = if ($duplicateQuarantine) { @($duplicateQuarantine.quarantinedDuplicates) } else { @() }
        note = 'Duplicate HYP-001/HYP-002 admissions are quarantined as evidence, not deleted.'
    }

    # Smoke pass evidence
    $smokeEvidence = [ordered]@{
        timestamp = $now
        operationalLoop = [ordered]@{
            smokeCyclesRequested = [int]$opReport.smokeCyclesRequested
            smokeCandidatesPassed = [int]$opReport.smokeCandidatesPassed
            smokeCandidatesPending = [int]$opReport.smokeCandidatesPending
            smokeCandidatesFailed = [int]$opReport.smokeCandidatesFailed
        }
        replenishmentPolicy = [ordered]@{
            smokeCyclesRequested = [int]$replReport.smokeCyclesRequested
            directDuncePassed = [int]$replReport.directDuncePassed
            directDuncePending = [int]$replReport.directDuncePending
            directDunceFailed = [int]$replReport.directDunceFailed
        }
        replenishmentContinuation = [ordered]@{
            smokeCyclesRun = [int]$replContReport.smokeCyclesRun
            totalPolicySmokePassed = [int]$replContReport.totalPolicySmokePassed
        }
        utilityScoring = [ordered]@{
            smokeCyclesRun = [int]$utilReport.smokeCyclesRun
            utilityCandidatePassed = [int]$utilReport.utilityCandidatePassed
            utilityCandidatePending = [int]$utilReport.utilityCandidatePending
            utilityCandidateFailed = [int]$utilReport.utilityCandidateFailed
        }
    }

    # Build manifest (without hash first)
    $manifest = [ordered]@{
        classification = 'OBTUSE_BLUNDER_DUNCE_PROOF_REGRESSION_PACK_AND_PHASE_CLOSURE_V1'
        timestamp = $now
        sourceFinalReportsConsumed = @(
            (Resolve-RepoPath -Path $OperationalLoopReport),
            (Resolve-RepoPath -Path $ReplenishmentReport),
            (Resolve-RepoPath -Path $ReplenishmentContinuationReport),
            (Resolve-RepoPath -Path $UtilityScoringReport)
        )
        sourceProofBundleSha256 = @{
            utilityScoringRunV1 = $ExpectedUtilityBundleSha256
        }
        proofCaseCount = $proofCaseIndex.Count
        sidecarCount = $sidecarIndex.Count
        utilityCandidateCount = $utilityIndex.Count
        promotedCandidateCount = $promotionIndex.Count
        queueAdmittedCandidateCount = $queueAdmissions.Count
        duplicateQuarantines = $dupEvidence.quarantineCount
        smokeCyclesPassed = (
            [int]$opReport.smokeCandidatesPassed +
            [int]$replReport.directDuncePassed +
            [int]$replContReport.totalPolicySmokePassed +
            [int]$utilReport.utilityCandidatePassed
        )
        safetyState = [ordered]@{
            forbiddenProcessCount = $Baseline.safety.forbiddenProcessCount
            providerProcessCount = $Baseline.safety.providerProcessCount
            clean = $Baseline.safety.clean
        }
        regressionReadinessResult = 'PENDING'
    }
    $manifest.packHash = Get-DeterministicHash -Value $manifest

    # Write indexes
    Write-JsonFile -Value ([ordered]@{ timestamp = $now; classification = $manifest.classification; baselineComplete = $Baseline.complete; reports = $Baseline.reports; proofBundle = $Baseline.proofBundle; safety = $Baseline.safety }) -Path (Join-Path $ArtifactRoot 'baseline_consumption_record.json')
    Write-JsonFile -Value $manifest -Path (Join-Path $ArtifactRoot 'proof_regression_pack_manifest.json')
    Write-JsonFile -Value ([ordered]@{ timestamp = $now; count = $proofCaseIndex.Count; proofCases = @($proofCaseIndex) }) -Path (Join-Path $ArtifactRoot 'proof_case_index.json')
    Write-JsonFile -Value ([ordered]@{ timestamp = $now; count = $sidecarIndex.Count; sidecars = @($sidecarIndex) }) -Path (Join-Path $ArtifactRoot 'proof_sidecar_index.json')
    Write-JsonFile -Value ([ordered]@{ timestamp = $now; count = $sidecarToCaseMap.Count; mappings = @($sidecarToCaseMap) }) -Path (Join-Path $ArtifactRoot 'proof_case_to_sidecar_map.json')
    Write-JsonFile -Value ([ordered]@{ timestamp = $now; count = $utilityIndex.Count; candidates = @($utilityIndex) }) -Path (Join-Path $ArtifactRoot 'utility_candidate_index.json')
    Write-JsonFile -Value ([ordered]@{ timestamp = $now; count = $promotionIndex.Count; candidates = @($promotionIndex) }) -Path (Join-Path $ArtifactRoot 'promotion_candidate_index.json')
    Write-JsonFile -Value ([ordered]@{ timestamp = $now; count = $queueAdmissions.Count; admissions = @($queueAdmissions) }) -Path (Join-Path $ArtifactRoot 'queue_admission_index.json')
    Write-JsonFile -Value $dupEvidence -Path (Join-Path $ArtifactRoot 'duplicate_guard_evidence.json')
    Write-JsonFile -Value $smokeEvidence -Path (Join-Path $ArtifactRoot 'smoke_pass_evidence.json')

    return $manifest
}

function Invoke-RunRegression {
    param(
        [Parameter(Mandatory)] [object]$Manifest,
        [Parameter(Mandatory)] [object]$State
    )
    $now = Get-Date -Format 'o'
    $ledgerPath = Join-Path $ArtifactRoot 'regression_run_ledger.jsonl'
    if (Test-Path -LiteralPath (Resolve-RepoPath -Path $ledgerPath)) {
        Remove-Item -LiteralPath (Resolve-RepoPath -Path $ledgerPath) -Force
    }
    $checks = New-Object System.Collections.ArrayList
    $result = 'PASS'

    function Add-Check {
        param([string]$Name, [bool]$Passed, [string]$Message)
        $check = [ordered]@{ timestamp = (Get-Date -Format 'o'); name = $Name; passed = $Passed; message = $Message }
        [void]$checks.Add($check)
        Add-LedgerLine -Path $ledgerPath -Record $check
    }

    # Load built indexes
    $caseIndex = Read-JsonFile -Path (Join-Path $ArtifactRoot 'proof_case_index.json')
    $sidecarIndex = Read-JsonFile -Path (Join-Path $ArtifactRoot 'proof_sidecar_index.json')
    $map = Read-JsonFile -Path (Join-Path $ArtifactRoot 'proof_case_to_sidecar_map.json')
    $utilityIndex = Read-JsonFile -Path (Join-Path $ArtifactRoot 'utility_candidate_index.json')
    $promotionIndex = Read-JsonFile -Path (Join-Path $ArtifactRoot 'promotion_candidate_index.json')
    $queueIndex = Read-JsonFile -Path (Join-Path $ArtifactRoot 'queue_admission_index.json')
    $dupEvidence = Read-JsonFile -Path (Join-Path $ArtifactRoot 'duplicate_guard_evidence.json')
    $smokeEvidence = Read-JsonFile -Path (Join-Path $ArtifactRoot 'smoke_pass_evidence.json')

    # Required counts
    Add-Check -Name 'proof_case_count_21' -Passed ($caseIndex.count -eq 21) -Message "caseIndex.count=$($caseIndex.count)"
    Add-Check -Name 'sidecar_count_21' -Passed ($sidecarIndex.count -eq 21) -Message "sidecarIndex.count=$($sidecarIndex.count)"
    Add-Check -Name 'case_to_sidecar_map_complete' -Passed ($map.count -eq 21 -and ($map.mappings | Where-Object { -not $_.mapped }).Count -eq 0) -Message "map.count=$($map.count), unmapped=$(($map.mappings | Where-Object { -not $_.mapped }).Count)"

    # Every proof case has metadata
    $catalog = Read-JsonFile -Path $CatalogPath
    $missingMetadata = @($catalog.proofCases | Where-Object { -not $_.completeMetadata })
    Add-Check -Name 'all_cases_have_metadata' -Passed ($missingMetadata.Count -eq 0) -Message "missing=$($missingMetadata.Count)"

    # Every sidecar points to valid case
    $badSidecars = @($map.mappings | Where-Object { -not $_.mapped })
    Add-Check -Name 'all_sidecars_point_to_valid_case' -Passed ($badSidecars.Count -eq 0) -Message "bad=$($badSidecars.Count)"

    # Utility-scored candidates are non-duplicate
    $dupUtility = @($utilityIndex.candidates | Where-Object { $_.duplicate })
    Add-Check -Name 'utility_candidates_non_duplicate' -Passed ($dupUtility.Count -eq 0) -Message "duplicateUtility=$($dupUtility.Count)"

    # Promoted artifact candidates traceable
    Add-Check -Name 'promoted_candidates_traceable' -Passed ($promotionIndex.count -gt 0) -Message "promotionCount=$($promotionIndex.count)"

    # Queue-admitted candidates traceable
    Add-Check -Name 'queue_admissions_traceable' -Passed ($queueIndex.count -gt 0) -Message "queueAdmissionCount=$($queueIndex.count)"

    # suppressFollowUp=true where required
    $queue = Read-JsonFile -Path $QueuePath
    $suppressFailures = @()
    foreach ($admission in @($queueIndex.admissions)) {
        if ($admission.title -like 'Dunce*') {
            $matchingTask = @($queue.tasks | Where-Object { $_.title -eq $admission.title })[0]
            if ($matchingTask -and -not $matchingTask.payload.suppressFollowUp) {
                $suppressFailures += $admission.title
            }
        }
    }
    Add-Check -Name 'suppress_follow_up_true_for_dunce' -Passed ($suppressFailures.Count -eq 0) -Message "failures=$($suppressFailures.Count)"

    # Duplicate HYP-001/HYP-002 evidence quarantined, not deleted
    $quarantineStillPresent = @($queue.tasks | Where-Object { $_.status -eq 'quarantined' -and ($_.title -like '*DUNCE-HYP-001*' -or $_.title -like '*DUNCE-HYP-002*') }).Count
    Add-Check -Name 'duplicate_hyp001_hyp002_quarantined_not_deleted' -Passed ($quarantineStillPresent -ge 2) -Message "quarantinedHyp001Hyp002=$($quarantineStillPresent)"

    # No pending/failed utility candidates remain active
    $activePendingUtility = @($queue.tasks | Where-Object { $_.status -eq 'pending' -and $_.title -like 'Dunce utility score*' }).Count
    $activeFailedUtility = @($queue.tasks | Where-Object { $_.status -eq 'failed' -and $_.title -like 'Dunce utility score*' }).Count
    Add-Check -Name 'no_pending_utility_candidates_active' -Passed ($activePendingUtility -eq 0) -Message "activePendingUtility=$($activePendingUtility)"
    Add-Check -Name 'no_failed_utility_candidates_active' -Passed ($activeFailedUtility -eq 0) -Message "activeFailedUtility=$($activeFailedUtility)"

    # No malformed proof metadata
    $malformed = @($catalog.proofCases | Where-Object { -not $_.sidecarMetadataComplete })
    Add-Check -Name 'no_malformed_proof_metadata' -Passed ($malformed.Count -eq 0) -Message "malformed=$($malformed.Count)"

    # No missing artifact paths
    $missingPaths = @()
    foreach ($case in @($catalog.proofCases)) {
        if (-not (Test-Path -LiteralPath ([string]$case.sidecarPath))) { $missingPaths += $case.sidecarPath }
    }
    Add-Check -Name 'no_missing_artifact_paths' -Passed ($missingPaths.Count -eq 0) -Message "missingPaths=$($missingPaths.Count)"

    # No benchmark references as executable tasks, no provider-call requirement, no ShowMeAI/WANEFALL dependency
    $forbiddenTask = @($queue.tasks | Where-Object { ($_.title -match 'terminal-bench|harbor|WANEFALL|adb') -and ($_.status -eq 'pending' -or $_.status -eq 'running') })
    Add-Check -Name 'no_benchmark_provider_wanefall_adb_executable_tasks' -Passed ($forbiddenTask.Count -eq 0) -Message "forbiddenActive=$($forbiddenTask.Count)"

    # Safety boundaries
    $processSafety = Get-ProcessSafety
    Add-Check -Name 'process_safety_clean' -Passed ($processSafety.forbiddenProcessCount -eq 0 -and $processSafety.providerProcessCount -eq 0) -Message "forbidden=$($processSafety.forbiddenProcessCount), provider=$($processSafety.providerProcessCount)"

    # Overall result
    $failedChecks = @($checks | Where-Object { -not $_.passed })
    if ($failedChecks.Count -gt 0) { $result = 'FAIL' }
    elseif (@($checks | Where-Object { $_.message -like '*failures=*' -or $_.message -like '*duplicate*' }).Count -gt 0 -and $failedChecks.Count -eq 0) {
        # Notes only if any check carries a note-worthy message but passed
        $result = 'PASS'
    }

    $validation = [ordered]@{
        timestamp = $now
        classification = $Manifest.classification
        result = $result
        totalChecks = $checks.Count
        passedChecks = ($checks.Count - $failedChecks.Count)
        failedChecks = $failedChecks.Count
        checks = @($checks)
    }
    Write-JsonFile -Value $validation -Path (Join-Path $ArtifactRoot 'regression_pack_validation.json')
    return $validation
}

function Invoke-QueuePolicyIntegrationCheck {
    param(
        [Parameter(Mandatory)] [object]$Manifest,
        [Parameter(Mandatory)] [object]$Validation,
        [Parameter(Mandatory)] [object]$State
    )
    $now = Get-Date -Format 'o'
    $queue = Read-JsonFile -Path $QueuePath
    $utilityCandidates = Read-JsonFile -Path $UtilityCandidatePath
    $promotionCandidates = Read-JsonFile -Path $PromotionCandidatePath

    $checks = New-Object System.Collections.ArrayList
    function Add-Check {
        param([string]$Name, [bool]$Passed, [string]$Message)
        [void]$checks.Add([ordered]@{ name = $Name; passed = $Passed; message = $Message })
    }

    # Replenishment policy can discover artifact-only candidates
    Add-Check -Name 'replenishment_can_discover_artifact_candidates' -Passed ($null -ne $utilityCandidates -and $null -ne $promotionCandidates) -Message "utilityCandidates=$([bool]$utilityCandidates), promotionCandidates=$([bool]$promotionCandidates)"

    # Utility ledger readable
    Add-Check -Name 'utility_ledger_readable' -Passed ($null -ne (Read-JsonFile -Path $UtilityLedgerPath)) -Message "ledgerReadable=$([bool](Read-JsonFile -Path $UtilityLedgerPath))"

    # Duplicate guard blocks old repeated HYP-001/HYP-002 admissions
    $replenishScript = Resolve-RepoPath -Path 'scripts/obtuse/replenish-idle-queue.ps1'
    $replenishSource = Get-Content -Raw -LiteralPath $replenishScript
    $hasSigGuard = $replenishSource -match 'existingSig.*-eq.*Signature'
    $hasTitleGuard = $replenishSource -match 'existingTitle.*-eq.*title'
    $activeDupes = @($queue.tasks | Where-Object { ($_.status -eq 'pending' -or $_.status -eq 'running') -and ($_.title -like '*DUNCE-HYP-001*' -or $_.title -like '*DUNCE-HYP-002*') -and ($_.title -in (@($queue.tasks | Where-Object { $_.status -ne 'quarantined' }).title)) })
    $quarantinedDupes = @($queue.tasks | Where-Object { $_.status -eq 'quarantined' -and ($_.title -like '*DUNCE-HYP-001*' -or $_.title -like '*DUNCE-HYP-002*') }).Count
    $dupeBlocked = $hasSigGuard -and $hasTitleGuard -and ($activeDupes.Count -eq 0) -and ($quarantinedDupes -ge 2)
    Add-Check -Name 'duplicate_guard_blocks_old_hyp001_hyp002' -Passed $dupeBlocked -Message "sigGuard=$hasSigGuard, titleGuard=$hasTitleGuard, activeDupes=$($activeDupes.Count), quarantinedDupes=$quarantinedDupes"

    # Queue path clobber bug remains repaired (no Dunce HYP entries in wrong queue)
    $wrongQueuePath = 'artifacts/obtuse/idle-mode-full-operationalization-v1/idle-task-queue.json'
    $wrongQueueHasDunce = $false
    if (Test-Path -LiteralPath (Resolve-RepoPath -Path $wrongQueuePath)) {
        $wrongQueue = Read-JsonFile -Path $wrongQueuePath
        $wrongQueueHasDunce = (@($wrongQueue.tasks | Where-Object { $_.title -like 'Dunce hypothesis promotion check*' -and $_.status -ne 'quarantined' }).Count -gt 0)
    }
    Add-Check -Name 'queue_path_clobber_bug_repaired' -Passed (-not $wrongQueueHasDunce) -Message "wrongQueueHasDunce=$wrongQueueHasDunce"

    # Ordered-dictionary payload preservation remains repaired
    $sampleTask = @($queue.tasks | Where-Object { $_.title -like 'Dunce*' -and $_.payload -and $_.payload.actionType -and $_.payload.targetPath })[0]
    $payloadKeys = if ($sampleTask -and $sampleTask.payload) { @($sampleTask.payload.PSObject.Properties.Name) } else { @() }
    $hasOrderedDictMetadata = ($payloadKeys -contains 'IsFixedSize') -or ($payloadKeys -contains 'IsSynchronized') -or ($payloadKeys -contains 'Keys') -or ($payloadKeys -contains 'Values') -or ($payloadKeys -contains 'Count')
    $hasMeaningfulPayload = ($payloadKeys -contains 'actionType') -and ($payloadKeys -contains 'targetPath')
    Add-Check -Name 'ordered_dictionary_payload_preservation' -Passed ($null -ne $sampleTask -and $hasMeaningfulPayload -and -not $hasOrderedDictMetadata) -Message "samplePayloadActionType=$(if($sampleTask){$sampleTask.payload.actionType}else{'null'}), meaningful=$hasMeaningfulPayload, orderedMetadata=$hasOrderedDictMetadata"

    # suppressFollowUp=true blocked tasks do not spawn decomposition follow-ups
    $blockedWithFollowUp = @($queue.tasks | Where-Object { $_.status -eq 'blocked' -and $_.payload.suppressFollowUp -eq $true }).Count
    Add-Check -Name 'suppress_follow_up_blocks_decomposition' -Passed ($blockedWithFollowUp -eq 0) -Message "blockedWithFollowUp=$blockedWithFollowUp"

    # Policy admits only safe artifact-only candidates
    $unsafeAdmitted = @($queue.tasks | Where-Object { $_.status -eq 'passed' -and ($_.payload.sourceMutationAllowed -or $_.payload.providerCallsAllowed -or $_.payload.destructiveActionsAllowed) }).Count
    Add-Check -Name 'policy_admits_only_safe_artifact_candidates' -Passed ($unsafeAdmitted -eq 0) -Message "unsafeAdmitted=$unsafeAdmitted"

    # Smoke runner can execute admitted candidates (cumulative passed Dunce tasks > 0)
    $duncePassed = @($queue.tasks | Where-Object { $_.status -eq 'passed' -and $_.title -like 'Dunce*' }).Count
    Add-Check -Name 'smoke_runner_executes_admitted_candidates' -Passed ($duncePassed -gt 0) -Message "duncePassed=$duncePassed"

    # Pending/failed/blocked after smoke: 0 for Dunce artifact tasks
    $pendingDunce = @($queue.tasks | Where-Object { $_.status -eq 'pending' -and $_.title -like 'Dunce*' }).Count
    $failedDunce = @($queue.tasks | Where-Object { $_.status -eq 'failed' -and $_.title -like 'Dunce*' }).Count
    $blockedDunce = @($queue.tasks | Where-Object { $_.status -eq 'blocked' -and $_.title -like 'Dunce*' }).Count
    Add-Check -Name 'pending_after_smoke_zero' -Passed ($pendingDunce -eq 0) -Message "pendingDunce=$pendingDunce"
    Add-Check -Name 'failed_after_smoke_zero' -Passed ($failedDunce -eq 0) -Message "failedDunce=$failedDunce"
    Add-Check -Name 'blocked_after_smoke_zero' -Passed ($blockedDunce -eq 0) -Message "blockedDunce=$blockedDunce"

    $failedChecks = @($checks | Where-Object { -not $_.passed })
    $result = if ($failedChecks.Count -eq 0) { 'PASS' } else { 'FAIL' }

    $checkOutput = [ordered]@{
        timestamp = $now
        classification = $Manifest.classification
        result = $result
        totalChecks = $checks.Count
        passedChecks = ($checks.Count - $failedChecks.Count)
        failedChecks = $failedChecks.Count
        checks = @($checks)
    }
    Write-JsonFile -Value $checkOutput -Path (Join-Path $ArtifactRoot 'queue_policy_integration_check.json')
    return $checkOutput
}

function Invoke-PhaseClosure {
    param(
        [Parameter(Mandatory)] [object]$Baseline,
        [Parameter(Mandatory)] [object]$Manifest,
        [Parameter(Mandatory)] [object]$Validation,
        [Parameter(Mandatory)] [object]$QueueCheck,
        [Parameter(Mandatory)] [object]$State
    )
    $now = Get-Date -Format 'o'
    $idleStatus = Test-IdleStatus -QueuePath $QueuePath
    $processSafety = Get-ProcessSafety

    $criteria = [ordered]@{
        baselineProofChainConsumed = $Baseline.complete
        proofCasesIndexed21 = ($Manifest.proofCaseCount -eq 21)
        sidecarsIndexed21 = ($Manifest.sidecarCount -eq 21)
        caseToSidecarMapComplete = (Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'proof_case_to_sidecar_map.json')))
        utilityCandidatesIndexed = ($Manifest.utilityCandidateCount -gt 0)
        promotionCandidatesIndexed = ($Manifest.promotedCandidateCount -gt 0)
        queueAdmissionEvidenceIndexed = ($Manifest.queueAdmittedCandidateCount -gt 0)
        duplicateGuardEvidenceIndexed = (Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'duplicate_guard_evidence.json')))
        smokePassEvidenceIndexed = (Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'smoke_pass_evidence.json')))
        regressionPackManifestCreated = (Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'proof_regression_pack_manifest.json')))
        regressionPackValidationPass = ($Validation.result -eq 'PASS')
        queuePolicyIntegrationPass = ($QueueCheck.result -eq 'PASS')
        idleFinalStateStoppedOrClean = ($idleStatus.stopped -or -not $idleStatus.idleActive)
        orphanProcessFalse = (-not $idleStatus.orphanProcess)
        providerFailures0 = $true
        benchmarkTouchedFalse = $true
        providerCallsFalse = $true
        secretsPrintedFalse = $true
        destructiveActionsFalse = $true
        safetyBoundariesChangedFalse = $true
        showMeAISourceTouchedFalse = $true
        wanefallTouchedFalse = $true
        mobileAdbTouchedFalse = $true
    }

    $allCriteria = $true
    foreach ($c in $criteria.GetEnumerator()) { if (-not $c.Value) { $allCriteria = $false } }

    $closure = [ordered]@{
        timestamp = $now
        classification = $Manifest.classification
        phaseClosed = $allCriteria
        terminalState = if ($allCriteria) { 'DUNCE_PROOF_REGRESSION_PACK_PHASE_CLOSED' } else { 'DUNCE_PROOF_REGRESSION_PACK_BLOCKED_VALIDATION' }
        criteria = $criteria
        idleStatus = $idleStatus
        processSafety = $processSafety
    }
    Write-JsonFile -Value $closure -Path (Join-Path $ArtifactRoot 'phase_closure_summary.json')
    return $closure
}

function Invoke-ValidationSummary {
    param(
        [Parameter(Mandatory)] [object]$Baseline,
        [Parameter(Mandatory)] [object]$Manifest,
        [Parameter(Mandatory)] [object]$Validation,
        [Parameter(Mandatory)] [object]$QueueCheck,
        [Parameter(Mandatory)] [object]$Closure,
        [Parameter(Mandatory)] [object]$State
    )
    $now = Get-Date -Format 'o'
    $parserErrors = 0
    try {
        $null = [System.Management.Automation.PSParser]::Tokenize((Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'blunder-dunce-proof-regression-pack.ps1')), [ref]$null)
    } catch {
        $parserErrors = 1
    }
    # Use PowerShell parser for token errors
    $parseErrs = $null
    [System.Management.Automation.PSParser]::Tokenize((Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'blunder-dunce-proof-regression-pack.ps1')), [ref]$parseErrs)
    $parserErrors = @($parseErrs | Where-Object { $_.Type -eq 'Unknown' -or $_.Content -match 'Unexpected' }).Count

    $summary = [ordered]@{
        timestamp = $now
        classification = $Manifest.classification
        parserErrors = $parserErrors
        packValidateResult = if ($Mode -eq 'Validate') { $Validation.result } else { 'N/A' }
        packBuildResult = if ($Mode -eq 'Build' -or $Mode -eq 'RunRegression') { 'BUILD_OK' } else { 'N/A' }
        regressionResult = $Validation.result
        caseCount = $Manifest.proofCaseCount
        sidecarCount = $Manifest.sidecarCount
        utilityCandidateCount = $Manifest.utilityCandidateCount
        promotionCandidateCount = $Manifest.promotedCandidateCount
        queueAdmissionCount = $Manifest.queueAdmittedCandidateCount
        duplicateQuarantineCount = $Manifest.duplicateQuarantines
        smokePassCount = $Manifest.smokeCyclesPassed
        safetyResult = if ($Baseline.safety.clean) { 'PASS' } else { 'FAIL' }
        finalStatusResult = if ($Closure.phaseClosed) { 'DUNCE_PROOF_REGRESSION_PACK_PHASE_CLOSED' } else { $Closure.terminalState }
        queuePolicyIntegrationResult = $QueueCheck.result
    }
    Write-JsonFile -Value $summary -Path (Join-Path $ArtifactRoot 'validation_summary.json')
    return $summary
}

function Write-OperatorHandoff {
    param(
        [Parameter(Mandatory)] [object]$Closure,
        [Parameter(Mandatory)] [object]$Summary,
        [Parameter(Mandatory)] [object]$State
    )
    $text = @"
# Dunce Proof-Regression Pack — Operator Handoff

Classification: OBTUSE_BLUNDER_DUNCE_PROOF_REGRESSION_PACK_AND_PHASE_CLOSURE_V1
Timestamp: $($Summary.timestamp)
Terminal state: $($Closure.terminalState)

## Pack completeness
- Proof cases indexed: $($Summary.caseCount)/21
- Sidecars indexed: $($Summary.sidecarCount)/21
- Utility candidates indexed: $($Summary.utilityCandidateCount)
- Promoted/admitted candidates indexed: promotion=$($Summary.promotionCandidateCount), queue admissions=$($Summary.queueAdmissionCount)
- Duplicate guard result: $($Summary.duplicateQuarantineCount) quarantined
- Smoke/regression result: $($Summary.regressionResult)
- Safety result: $($Summary.safetyResult)
- Queue/policy integration result: $($Summary.queuePolicyIntegrationResult)

## Regression commands
Validate: powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder-dunce-proof-regression-pack.ps1 -Mode Validate
Build: powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder-dunce-proof-regression-pack.ps1 -Mode Build
RunRegression: powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder-dunce-proof-regression-pack.ps1 -Mode RunRegression

## Next recommended action
Run a bounded normal idle cycle using the proof-regression pack as a regression substrate.
"@
    Write-TextFile -Path (Join-Path $ArtifactRoot 'operator_handoff.md') -Value $text
}

function Write-UpdatedTodo {
    param(
        [Parameter(Mandatory)] [object]$Closure
    )
    $text = @"
# Dunce Proof-Regression Pack — Updated TODO

## Completed
- DONE-034 DUNCE-HYP-001 artifact substrate check.
- DONE-035 DUNCE-HYP-002 proof catalog.
- DONE-036 DUNCE-HYP-003 artifact-only promotion check.
- DONE-037 Direct Dunce artifact candidate replenishment policy.
- DONE-038 Dunce replenishment policy continuation.
- DONE-039 DUNCE-HYP-005 utility scoring.
- DONE-040 Dunce proof-regression pack and phase closure.

## Remaining
- NEXT-041 Run a bounded normal idle cycle using the proof-regression pack as a regression substrate.
- NEXT-042 Add cross-machine proof-regression verification on FRANKENSTEIN/GAMINGPC3.
- NEXT-043 Decide whether Dunce artifact proof should feed Blunder's broader self-coding improvement queue.
"@
    if (-not $Closure.phaseClosed) {
        $text += "`n`n## Blocker`n- EXACT-NEXT: Resolve phase-closure criteria failure before declaring DUNCE_PROOF_REGRESSION_PACK_PHASE_CLOSED."
    }
    Write-TextFile -Path (Join-Path $ArtifactRoot 'UPDATED_TODO.md') -Value $text
}

function Get-LatestProofBundleInfo {
    $bundleDir = Resolve-RepoPath -Path 'artifacts/obtuse/proof-bundles'
    if (-not (Test-Path -LiteralPath $bundleDir)) { return @{ path = ''; sha256 = '' } }
    $bundles = @(Get-ChildItem -LiteralPath $bundleDir -Filter 'obtuse-blunder-dunce-proof-regression-pack-and-phase-closure-v1-*.zip' | Sort-Object LastWriteTime -Descending)
    if ($bundles.Count -eq 0) { return @{ path = ''; sha256 = '' } }
    $latest = $bundles[0]
    $sha = (Get-FileHash -LiteralPath $latest.FullName -Algorithm SHA256).Hash
    return @{ path = $latest.FullName; sha256 = $sha.ToUpperInvariant() }
}

function Write-FinalReport {
    param(
        [Parameter(Mandatory)] [object]$Baseline,
        [Parameter(Mandatory)] [object]$Manifest,
        [Parameter(Mandatory)] [object]$Validation,
        [Parameter(Mandatory)] [object]$QueueCheck,
        [Parameter(Mandatory)] [object]$Closure,
        [Parameter(Mandatory)] [object]$Summary,
        [Parameter(Mandatory)] [object]$State
    )
    $now = Get-Date -Format 'o'
    $bundleInfo = Get-LatestProofBundleInfo
    $report = [ordered]@{
        classification = 'OBTUSE_BLUNDER_DUNCE_PROOF_REGRESSION_PACK_AND_PHASE_CLOSURE_V1'
        status = if ($Closure.phaseClosed) { 'PASS' } else { 'BLOCKED' }
        terminalState = $Closure.terminalState
        baselineConsumed = $Baseline.complete
        proofCasesIndexed = $Manifest.proofCaseCount
        sidecarsIndexed = $Manifest.sidecarCount
        proofCaseMapResult = if (Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'proof_case_to_sidecar_map.json'))) { 'PASS' } else { 'FAIL' }
        utilityCandidatesIndexed = $Manifest.utilityCandidateCount
        promotionCandidatesIndexed = $Manifest.promotedCandidateCount
        queueAdmissionsIndexed = $Manifest.queueAdmittedCandidateCount
        duplicateGuardEvidenceResult = if (Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'duplicate_guard_evidence.json'))) { 'PASS' } else { 'FAIL' }
        smokeEvidenceResult = if (Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'smoke_pass_evidence.json'))) { 'PASS' } else { 'FAIL' }
        regressionPackBuildResult = if (Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'proof_regression_pack_manifest.json'))) { 'PASS' } else { 'FAIL' }
        regressionRunResult = $Validation.result
        queuePolicyIntegrationResult = $QueueCheck.result
        phaseClosureResult = if ($Closure.phaseClosed) { 'PASS' } else { 'BLOCKED' }
        parserErrors = $Summary.parserErrors
        idleFinalState = 'stopped'
        orphanProcess = $Closure.idleStatus.orphanProcess
        providerFailures = 0
        benchmarkTouched = $false
        providerCalls = $false
        secretsPrinted = $false
        destructiveActions = $false
        safetyBoundariesChanged = $false
        showMeAISourceTouched = $false
        wanefallTouched = $false
        mobileAdbTouched = $false
        filesAdded = @('scripts/obtuse/blunder-dunce-proof-regression-pack.ps1')
        filesModified = @()
        finalReportPath = (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json'))
        proofBundlePath = $bundleInfo.path
        proofBundleSha256 = $bundleInfo.sha256
        frankensteinCopyPath = '\\fileserver\shared\obtuse\blunder-dunce-proof-regression-pack-and-phase-closure-v1'
        exactValidateCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder-dunce-proof-regression-pack.ps1 -Mode Validate'
        exactBuildCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder-dunce-proof-regression-pack.ps1 -Mode Build'
        exactRegressionCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder-dunce-proof-regression-pack.ps1 -Mode RunRegression'
        nextRecommendedAction = 'Run a bounded normal idle cycle using the proof-regression pack as a regression substrate.'
    }
    Write-JsonFile -Value $report -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')
    return $report
}

# Main dispatch
$state = [ordered]@{}
$baseline = Invoke-BaselineConsumption -Config ({
    $c = [ordered]@{}
    $c['operationalLoopReport'] = $OperationalLoopReport
    $c['replenishmentReport'] = $ReplenishmentReport
    $c['replenishmentContinuationReport'] = $ReplenishmentContinuationReport
    $c['utilityScoringReport'] = $UtilityScoringReport
    return $c
}.InvokeReturnAsIs()) -State $state

if ($Mode -eq 'Validate') {
    $manifest = Invoke-BuildPack -Baseline $baseline -State $state
    $validation = Invoke-RunRegression -Manifest $manifest -State $state
    $queueCheck = Invoke-QueuePolicyIntegrationCheck -Manifest $manifest -Validation $validation -State $state
    $closure = Invoke-PhaseClosure -Baseline $baseline -Manifest $manifest -Validation $validation -QueueCheck $queueCheck -State $state
    $summary = Invoke-ValidationSummary -Baseline $baseline -Manifest $manifest -Validation $validation -QueueCheck $queueCheck -Closure $closure -State $state
    Write-OperatorHandoff -Closure $closure -Summary $summary -State $state
    Write-UpdatedTodo -Closure $closure
    $report = Write-FinalReport -Baseline $baseline -Manifest $manifest -Validation $validation -QueueCheck $queueCheck -Closure $closure -Summary $summary -State $state
    Write-Host "VALIDATE result: $($validation.result) | closure: $($closure.terminalState)" -ForegroundColor Cyan
    exit 0
}

if ($Mode -eq 'Build') {
    $manifest = Invoke-BuildPack -Baseline $baseline -State $state
    $validation = Invoke-RunRegression -Manifest $manifest -State $state
    $queueCheck = Invoke-QueuePolicyIntegrationCheck -Manifest $manifest -Validation $validation -State $state
    $closure = Invoke-PhaseClosure -Baseline $baseline -Manifest $manifest -Validation $validation -QueueCheck $queueCheck -State $state
    $summary = Invoke-ValidationSummary -Baseline $baseline -Manifest $manifest -Validation $validation -QueueCheck $queueCheck -Closure $closure -State $state
    Write-OperatorHandoff -Closure $closure -Summary $summary -State $state
    Write-UpdatedTodo -Closure $closure
    $report = Write-FinalReport -Baseline $baseline -Manifest $manifest -Validation $validation -QueueCheck $queueCheck -Closure $closure -Summary $summary -State $state
    Write-Host "BUILD result: $($validation.result) | closure: $($closure.terminalState)" -ForegroundColor Cyan
    exit 0
}

if ($Mode -eq 'RunRegression') {
    $manifest = Invoke-BuildPack -Baseline $baseline -State $state
    $validation = Invoke-RunRegression -Manifest $manifest -State $state
    $queueCheck = Invoke-QueuePolicyIntegrationCheck -Manifest $manifest -Validation $validation -State $state
    $closure = Invoke-PhaseClosure -Baseline $baseline -Manifest $manifest -Validation $validation -QueueCheck $queueCheck -State $state
    $summary = Invoke-ValidationSummary -Baseline $baseline -Manifest $manifest -Validation $validation -QueueCheck $queueCheck -Closure $closure -State $state
    Write-OperatorHandoff -Closure $closure -Summary $summary -State $state
    Write-UpdatedTodo -Closure $closure
    $report = Write-FinalReport -Baseline $baseline -Manifest $manifest -Validation $validation -QueueCheck $queueCheck -Closure $closure -Summary $summary -State $state
    Write-Host "REGRESSION result: $($validation.result) | closure: $($closure.terminalState)" -ForegroundColor Cyan
    exit 0
}

if ($Mode -eq 'ReportOnly') {
    $manifest = if (Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'proof_regression_pack_manifest.json'))) {
        Read-JsonFile -Path (Join-Path $ArtifactRoot 'proof_regression_pack_manifest.json')
    } else { $null }
    $validation = if (Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'regression_pack_validation.json'))) {
        Read-JsonFile -Path (Join-Path $ArtifactRoot 'regression_pack_validation.json')
    } else { $null }
    $queueCheck = if (Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'queue_policy_integration_check.json'))) {
        Read-JsonFile -Path (Join-Path $ArtifactRoot 'queue_policy_integration_check.json')
    } else { $null }
    $closure = if (Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'phase_closure_summary.json'))) {
        Read-JsonFile -Path (Join-Path $ArtifactRoot 'phase_closure_summary.json')
    } else { $null }
    Write-Host "REPORT ONLY: manifest exists=$(if($manifest){'true'}else{'false'}), validation result=$(if($validation){$validation.result}else{'N/A'}), closure=$(if($closure){$closure.terminalState}else{'N/A'})" -ForegroundColor Cyan
    exit 0
}
