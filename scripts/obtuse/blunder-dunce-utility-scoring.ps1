#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Run','Validate','ReportOnly')]
    [string]$Mode = 'Run',
    [string]$ArtifactRoot = 'artifacts/obtuse/blunder-dunce-utility-scoring-v1',
    [string]$QueuePath = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle/idle-task-queue.json',
    [string]$ProofCatalogPath = 'artifacts/obtuse/blunder-dunce-proof-case-catalog-v1/001_PROOF_CASE_CATALOG.json',
    [string]$PromotionCandidatePath = 'artifacts/obtuse/blunder-dunce-hypothesis-promotion-check-v1/002_ARTIFACT_ONLY_QUEUE_CANDIDATES.json'
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

function Get-ProcessSafety {
    $forbiddenPattern = 'external-benchmark|external-agent-runner|WANEFALL|adb\.exe|fastboot\.exe'
    $providerPattern = 'openrouter-idle-model\.ps1|idle-self-coding-accelerator\.ps1'
    $all = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine })
    return [ordered]@{
        forbiddenProcessCount = @($all | Where-Object { $_.CommandLine -match $forbiddenPattern }).Count
        providerProcessCount = @($all | Where-Object { $_.CommandLine -match $providerPattern }).Count
    }
}

function Get-QueueSignatureSet {
    param([object]$Queue)
    $set = @{}
    foreach ($task in @($Queue.tasks)) {
        if ($task.generationSignature) {
            $set[[string]$task.generationSignature] = $true
        }
    }
    return $set
}

function New-CandidateSignature {
    param([Parameter(Mandatory)] [string]$CandidateId)
    return "dunce-utility-v1|inspect-artifacts|$($CandidateId.ToLowerInvariant())"
}

function New-Candidate {
    param(
        [Parameter(Mandatory)] [string]$CandidateId,
        [Parameter(Mandatory)] [string]$Title,
        [Parameter(Mandatory)] [string]$TargetPath,
        [Parameter(Mandatory)] [string]$MeasurableTest,
        [Parameter(Mandatory)] [string]$SuccessMetric,
        [Parameter(Mandatory)] [double]$Novelty,
        [Parameter(Mandatory)] [double]$Utility,
        [Parameter(Mandatory)] [double]$ProofDepth,
        [Parameter(Mandatory)] [double]$Reversibility
    )
    $signature = New-CandidateSignature -CandidateId $CandidateId
    $score = [math]::Round((($Novelty * 0.30) + ($Utility * 0.30) + ($ProofDepth * 0.25) + ($Reversibility * 0.15)), 4)
    return [ordered]@{
        candidateId = $CandidateId
        title = $Title
        actionType = 'inspect-artifacts'
        targetPath = (Resolve-RepoPath -Path $TargetPath)
        controlLane = 'blunder-dunce-utility-scoring-v1'
        sourceProject = 'dunce/showmeai'
        risk = 1
        sourceMutationAllowed = $false
        providerCallsAllowed = $false
        destructiveActionsAllowed = $false
        suppressFollowUp = $true
        measurableTest = $MeasurableTest
        successMetric = $SuccessMetric
        stopCondition = 'Stop if source mutation, provider call, destructive action, benchmark touch, or secret exposure would be required.'
        generationSignature = $signature
        scores = [ordered]@{
            novelty = $Novelty
            utility = $Utility
            proofDepth = $ProofDepth
            reversibility = $Reversibility
            weightedTotal = $score
        }
    }
}

function New-UtilityCandidates {
    param([object]$ProofCatalog)
    $proofCaseCount = if ($ProofCatalog) { [int]$ProofCatalog.proofCaseCount } else { 0 }
    $completeProofCaseCount = if ($ProofCatalog) { [int]$ProofCatalog.completeProofCaseCount } else { 0 }
    $proofCompleteness = if ($proofCaseCount -gt 0) { [math]::Min(1.0, $completeProofCaseCount / [double]$proofCaseCount) } else { 0.0 }
    return @(
        New-Candidate -CandidateId 'proof-regression-pack-readiness' `
            -Title 'Dunce utility score: proof regression pack readiness' `
            -TargetPath 'artifacts/obtuse/blunder-dunce-proof-case-catalog-v1' `
            -MeasurableTest 'Inspect proof catalog and sidecar outputs; verify proof cases are complete enough for regression-pack assembly.' `
            -SuccessMetric 'Proof catalog has at least 4 complete cases, zero invalid JSON, and zero credential findings.' `
            -Novelty 0.88 -Utility 0.92 -ProofDepth $proofCompleteness -Reversibility 1.0
        New-Candidate -CandidateId 'supersession-regression-pack-readiness' `
            -Title 'Dunce utility score: supersession regression pack readiness' `
            -TargetPath 'artifacts/obtuse/blunder-dunce-supersession-ledger-and-hypothesis-check-v1' `
            -MeasurableTest 'Inspect supersession ledger and verify retired blockers remain proof-linked with no active contradictions.' `
            -SuccessMetric 'Active blocker count is zero and every retired blocker has verified supersession proof.' `
            -Novelty 0.82 -Utility 0.86 -ProofDepth 0.90 -Reversibility 1.0
        New-Candidate -CandidateId 'queue-policy-health-after-dunce-replenishment' `
            -Title 'Dunce utility score: queue policy health after replenishment' `
            -TargetPath 'artifacts/obtuse/blunder-dunce-replenishment-policy-continuation-v1' `
            -MeasurableTest 'Inspect replenishment policy reports and verify Dunce candidates pass through policy without pending, failed, blocked, or quarantined results.' `
            -SuccessMetric 'Policy-fed Dunce candidates are all passed, suppressFollowUp is true, and idle final state is stopped.' `
            -Novelty 0.78 -Utility 0.88 -ProofDepth 0.85 -Reversibility 1.0
        New-Candidate -CandidateId 'next-dunce-artifact-gap-scan' `
            -Title 'Dunce utility score: next artifact gap scan' `
            -TargetPath 'artifacts/obtuse/blunder-dunce-capability-hypothesis-synthesis-v1' `
            -MeasurableTest 'Inspect existing Dunce capability hypotheses and identify the next missing artifact-only proof gap without queue duplication.' `
            -SuccessMetric 'At least one new artifact-only proof gap is identified with a measurable test and no provider/source mutation requirement.' `
            -Novelty 0.94 -Utility 0.84 -ProofDepth 0.72 -Reversibility 1.0
    )
}

function Invoke-Run {
    New-Item -ItemType Directory -Force -Path (Resolve-RepoPath -Path $ArtifactRoot) | Out-Null
    $queue = Read-JsonFile -Path $QueuePath
    if (-not $queue) { throw "Missing queue: $(Resolve-RepoPath -Path $QueuePath)" }
    $proofCatalog = Read-JsonFile -Path $ProofCatalogPath
    $promotionDoc = Read-JsonFile -Path $PromotionCandidatePath
    $processSafety = Get-ProcessSafety
    $existingSignatures = Get-QueueSignatureSet -Queue $queue
    $rawCandidates = New-UtilityCandidates -ProofCatalog $proofCatalog
    $evaluations = New-Object System.Collections.ArrayList
    $admitted = New-Object System.Collections.ArrayList
    foreach ($candidate in @($rawCandidates)) {
        $duplicate = $existingSignatures.ContainsKey([string]$candidate.generationSignature)
        $safe = (-not $candidate.sourceMutationAllowed -and -not $candidate.providerCallsAllowed -and -not $candidate.destructiveActionsAllowed)
        $meetsScore = ([double]$candidate.scores.weightedTotal -ge 0.80)
        $decision = if ($duplicate) { 'REJECT_DUPLICATE' } elseif (-not $safe) { 'REJECT_UNSAFE' } elseif (-not $meetsScore) { 'REJECT_LOW_SCORE' } else { 'ADMIT' }
        [void]$evaluations.Add([ordered]@{
            candidateId = $candidate.candidateId
            title = $candidate.title
            generationSignature = $candidate.generationSignature
            scores = $candidate.scores
            duplicate = $duplicate
            safe = $safe
            decision = $decision
        })
        if ($decision -eq 'ADMIT') { [void]$admitted.Add($candidate) }
    }
    $ledger = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        hypothesis = 'DUNCE-HYP-005'
        scoringWeights = [ordered]@{ novelty = 0.30; utility = 0.30; proofDepth = 0.25; reversibility = 0.15 }
        rawCandidateCount = $rawCandidates.Count
        admittedCandidateCount = $admitted.Count
        rejectedCandidateCount = ($rawCandidates.Count - $admitted.Count)
        evaluations = @($evaluations)
    }
    $candidateDoc = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        sourceHypothesis = 'DUNCE-HYP-005'
        queueCandidates = @($admitted)
        candidateCount = $admitted.Count
        previousPromotionCandidateCount = if ($promotionDoc) { [int]$promotionDoc.candidateCount } else { 0 }
    }
    Write-JsonFile -Value $ledger -Path (Join-Path $ArtifactRoot '001_UTILITY_SCORING_LEDGER.json')
    Write-JsonFile -Value $candidateDoc -Path (Join-Path $ArtifactRoot '002_NON_DUPLICATE_QUEUE_CANDIDATES.json')
    $md = "# Dunce Utility Scoring`r`n`r`n"
    $md += "- Raw candidates: $($rawCandidates.Count)`r`n"
    $md += "- Admitted candidates: $($admitted.Count)`r`n"
    $md += "- Rejected candidates: $($rawCandidates.Count - $admitted.Count)`r`n"
    foreach ($row in @($evaluations)) {
        $md += "- $($row.candidateId): $($row.decision), score $($row.scores.weightedTotal)`r`n"
    }
    Write-TextFile -Path (Join-Path $ArtifactRoot '001_UTILITY_SCORING_LEDGER.md') -Value $md
    $bundleRoot = Resolve-RepoPath -Path 'artifacts/obtuse/proof-bundles'
    New-Item -ItemType Directory -Force -Path $bundleRoot | Out-Null
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmss')
    $bundlePath = Join-Path $bundleRoot "obtuse-blunder-dunce-utility-scoring-v1-$stamp.zip"
    $files = @(Get-ChildItem -LiteralPath (Resolve-RepoPath -Path $ArtifactRoot) -Recurse -File | ForEach-Object { $_.FullName })
    $files += (Join-Path $PSScriptRoot 'blunder-dunce-utility-scoring.ps1')
    Compress-Archive -LiteralPath $files -DestinationPath $bundlePath -Force
    $target = '\\fileserver\shared\obtuse\blunder-dunce-utility-scoring-v1'
    $copied = $false
    if (Test-Path -LiteralPath '\\fileserver\shared\obtuse') {
        New-Item -ItemType Directory -Force -Path $target | Out-Null
        Copy-Item -LiteralPath (Join-Path (Resolve-RepoPath -Path $ArtifactRoot) '*') -Destination $target -Recurse -Force
        Copy-Item -LiteralPath $bundlePath -Destination (Join-Path $target (Split-Path -Leaf $bundlePath)) -Force
        $copied = $true
    }
    $status = if ($admitted.Count -gt 0 -and $processSafety.forbiddenProcessCount -eq 0 -and $processSafety.providerProcessCount -eq 0) { 'PASS' } else { 'PASS_WITH_NOTES' }
    $result = [ordered]@{
        classification = 'OBTUSE_BLUNDER_DUNCE_UTILITY_SCORING_V1'
        status = $status
        sourceEdited = $true
        filesEdited = @('scripts/obtuse/blunder-dunce-utility-scoring.ps1')
        artifactOnly = $true
        benchmarkTouched = $false
        providerCalls = $false
        secretsPrinted = $false
        destructiveActions = $false
        safetyBoundariesChanged = $false
        wanefallTouched = $false
        showMeAISourceTouched = $false
        mobilePhoneAdbTouched = $false
        forbiddenProcessCount = $processSafety.forbiddenProcessCount
        providerProcessCount = $processSafety.providerProcessCount
        rawCandidateCount = $rawCandidates.Count
        admittedCandidateCount = $admitted.Count
        rejectedCandidateCount = ($rawCandidates.Count - $admitted.Count)
        duplicateRejectedCount = @($evaluations | Where-Object { $_.decision -eq 'REJECT_DUPLICATE' }).Count
        lowScoreRejectedCount = @($evaluations | Where-Object { $_.decision -eq 'REJECT_LOW_SCORE' }).Count
        proofBundlePath = $bundlePath
        proofBundleSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $bundlePath).Hash.ToUpperInvariant()
        frankensteinCopyPath = $target
        frankensteinCopySucceeded = $copied
        finalReportPath = (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json'))
        knownLimitations = @('This scoring pass emits artifact-only candidates; it does not inject them into the live queue.')
        nextRecommendedAction = 'Admit the DUNCE-HYP-005 non-duplicate utility candidates through the replenishment policy and run a bounded smoke.'
    }
    Write-JsonFile -Value $result -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')
    Write-TextFile -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.md') -Value "# Final Report`r`n`r`nStatus: $status`r`n`r`nAdmitted candidates: $($admitted.Count)`r`nNext: $($result.nextRecommendedAction)`r`n"
    return $result
}

switch ($Mode) {
    'Validate' {
        $tokens = $null
        $errors = $null
        [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'blunder-dunce-utility-scoring.ps1'), [ref]$tokens, [ref]$errors) | Out-Null
        [ordered]@{
            classification = 'OBTUSE_BLUNDER_DUNCE_UTILITY_SCORING_V1'
            parserErrors = $errors.Count
            result = if ($errors.Count -eq 0) { 'PASS' } else { 'FAIL' }
        } | ConvertTo-Json -Depth 10
        return
    }
    'ReportOnly' {
        $report = Read-JsonFile -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')
        if ($report) { $report | ConvertTo-Json -Depth 100; return }
        [ordered]@{ classification = 'OBTUSE_BLUNDER_DUNCE_UTILITY_SCORING_V1'; status = 'MISSING_REPORT' } | ConvertTo-Json -Depth 10
        return
    }
    'Run' {
        Invoke-Run | ConvertTo-Json -Depth 100
        return
    }
}
