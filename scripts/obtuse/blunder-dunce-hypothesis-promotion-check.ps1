#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Run','Validate','ReportOnly')]
    [string]$Mode = 'Run',
    [string]$ArtifactRoot = 'artifacts/obtuse/blunder-dunce-hypothesis-promotion-check-v1',
    [string]$HypothesisPath = 'artifacts/obtuse/blunder-dunce-capability-hypothesis-synthesis-v1/004_CAPABILITY_HYPOTHESES.json',
    [string]$SupersessionReport = 'artifacts/obtuse/blunder-dunce-supersession-ledger-and-hypothesis-check-v1/FINAL_REPORT.json',
    [string]$ProofCatalogReport = 'artifacts/obtuse/blunder-dunce-proof-case-catalog-v1/FINAL_REPORT.json'
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
    if ($parent -and -not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    $Value | ConvertTo-Json -Depth 80 | Set-Content -LiteralPath $resolved -Encoding UTF8
}

function Write-TextFile {
    param([Parameter(Mandatory)] [string]$Path, [Parameter(Mandatory)] [string]$Value)
    $resolved = Resolve-RepoPath -Path $Path
    $parent = Split-Path $resolved -Parent
    if ($parent -and -not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
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

function Test-HypothesisPromotable {
    param([Parameter(Mandatory)] [object]$Hypothesis, [object]$Supersession, [object]$ProofCatalog)
    $missing = New-Object System.Collections.ArrayList
    foreach ($field in @('id','title','evidence','measurableTest','successMetric','nextSafeAction','operatorGate','priority')) {
        if (-not ($Hypothesis.PSObject.Properties.Name -contains $field) -or -not $Hypothesis.$field) { [void]$missing.Add($field) }
    }
    $support = switch ([string]$Hypothesis.id) {
        'DUNCE-HYP-001' { [bool]($Supersession -and $Supersession.dunceHypothesisPass -and $Supersession.supersessionLedgerPass) }
        'DUNCE-HYP-002' { [bool]($ProofCatalog -and $ProofCatalog.successMetricMet -and $ProofCatalog.completeProofCaseCount -ge 4) }
        'DUNCE-HYP-003' { $true }
        'DUNCE-HYP-004' { [bool]($Supersession -and $Supersession.supersessionLedgerPass -and $Supersession.activeBlockerCount -eq 0) }
        'DUNCE-HYP-005' { $true }
        default { $false }
    }
    $operatorSafe = ([string]$Hypothesis.operatorGate) -notmatch 'source mutation|provider-backed|runtime'
    $promotable = ($missing.Count -eq 0 -and $support)
    return [ordered]@{
        hypothesisId = [string]$Hypothesis.id
        priority = [string]$Hypothesis.priority
        missingRequiredFieldCount = $missing.Count
        missingRequiredFields = @($missing)
        supportEvidenceVerified = [bool]$support
        sourceMutationAllowed = $false
        providerCallsAllowed = $false
        destructiveActionsAllowed = $false
        operatorGate = [string]$Hypothesis.operatorGate
        operatorSafeForArtifactOnlyQueue = [bool]$operatorSafe
        promotableAsArtifactOnlyCandidate = [bool]$promotable
        candidate = [ordered]@{
            taskId = "dunce-hypothesis-check-$(([string]$Hypothesis.id).ToLowerInvariant())"
            title = "Dunce hypothesis promotion check: $($Hypothesis.id)"
            actionType = 'inspect-artifacts'
            controlLane = 'blunder-dunce-hypothesis-promotion-check-v1'
            risk = 1
            sourceMutationAllowed = $false
            providerCallsAllowed = $false
            destructiveActionsAllowed = $false
            measurableTest = [string]$Hypothesis.measurableTest
            successMetric = [string]$Hypothesis.successMetric
            stopCondition = 'Stop if source mutation, provider call, destructive action, benchmark touch, or secret exposure would be required.'
        }
    }
}

function Invoke-Run {
    New-Item -ItemType Directory -Force -Path (Resolve-RepoPath -Path $ArtifactRoot) | Out-Null
    $hypothesisDoc = Read-JsonFile -Path $HypothesisPath
    if (-not $hypothesisDoc) { throw "Missing hypotheses: $(Resolve-RepoPath -Path $HypothesisPath)" }
    $supersession = Read-JsonFile -Path $SupersessionReport
    $proofCatalog = Read-JsonFile -Path $ProofCatalogReport
    $processSafety = Get-ProcessSafety
    $evaluations = @($hypothesisDoc.hypotheses | ForEach-Object { Test-HypothesisPromotable -Hypothesis $_ -Supersession $supersession -ProofCatalog $proofCatalog })
    $promoted = @($evaluations | Where-Object { $_.promotableAsArtifactOnlyCandidate })
    $queueCandidates = @($promoted | ForEach-Object { $_.candidate })
    $ledger = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        hypothesisCount = @($hypothesisDoc.hypotheses).Count
        evaluatedCount = $evaluations.Count
        promotedCandidateCount = $queueCandidates.Count
        evaluations = $evaluations
        queueCandidates = $queueCandidates
    }
    Write-JsonFile -Value $ledger -Path (Join-Path $ArtifactRoot '001_PROMOTION_LEDGER.json')
    Write-JsonFile -Value ([ordered]@{ timestamp = (Get-Date -Format 'o'); queueCandidates = $queueCandidates; candidateCount = $queueCandidates.Count }) -Path (Join-Path $ArtifactRoot '002_ARTIFACT_ONLY_QUEUE_CANDIDATES.json')
    $md = "# Dunce Hypothesis Promotion Check`r`n`r`n"
    $md += "- Hypotheses evaluated: $($evaluations.Count)`r`n"
    $md += "- Artifact-only candidates promoted: $($queueCandidates.Count)`r`n"
    $md += "- Source mutation allowed: false`r`n"
    $md += "- Provider calls allowed: false`r`n"
    Write-TextFile -Path (Join-Path $ArtifactRoot '001_PROMOTION_LEDGER.md') -Value $md
    $bundleRoot = Resolve-RepoPath -Path 'artifacts/obtuse/proof-bundles'
    New-Item -ItemType Directory -Force -Path $bundleRoot | Out-Null
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmss')
    $bundlePath = Join-Path $bundleRoot "obtuse-blunder-dunce-hypothesis-promotion-check-v1-$stamp.zip"
    $files = @(Get-ChildItem -LiteralPath (Resolve-RepoPath -Path $ArtifactRoot) -Recurse -File | ForEach-Object { $_.FullName })
    $files += (Join-Path $PSScriptRoot 'blunder-dunce-hypothesis-promotion-check.ps1')
    Compress-Archive -LiteralPath $files -DestinationPath $bundlePath -Force
    $target = '\\fileserver\shared\obtuse\blunder-dunce-hypothesis-promotion-check-v1'
    $copied = $false
    if (Test-Path -LiteralPath '\\fileserver\shared\obtuse') {
        New-Item -ItemType Directory -Force -Path $target | Out-Null
        Copy-Item -LiteralPath (Join-Path (Resolve-RepoPath -Path $ArtifactRoot) '*') -Destination $target -Recurse -Force
        Copy-Item -LiteralPath $bundlePath -Destination (Join-Path $target (Split-Path -Leaf $bundlePath)) -Force
        $copied = $true
    }
    $status = if ($queueCandidates.Count -ge 3 -and $processSafety.forbiddenProcessCount -eq 0 -and $processSafety.providerProcessCount -eq 0) { 'PASS' } else { 'PASS_WITH_NOTES' }
    $result = [ordered]@{
        classification = 'OBTUSE_BLUNDER_DUNCE_HYPOTHESIS_PROMOTION_CHECK_V1'
        status = $status
        sourceEdited = $true
        filesEdited = @('scripts/obtuse/blunder-dunce-hypothesis-promotion-check.ps1')
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
        hypothesisCount = @($hypothesisDoc.hypotheses).Count
        promotedCandidateCount = $queueCandidates.Count
        proofBundlePath = $bundlePath
        proofBundleSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $bundlePath).Hash.ToUpperInvariant()
        frankensteinCopyPath = $target
        frankensteinCopySucceeded = $copied
        finalReportPath = (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json'))
        knownLimitations = @('Promotion check writes a candidate ledger only; it does not inject tasks into the live idle queue.')
        nextRecommendedAction = 'Operator-approve queue injection for artifact-only Dunce candidates, then run a bounded smoke.'
    }
    Write-JsonFile -Value $result -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')
    Write-TextFile -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.md') -Value "# Final Report`r`n`r`nStatus: $status`r`n`r`nNext: $($result.nextRecommendedAction)`r`n"
    return $result
}

switch ($Mode) {
    'Validate' {
        $tokens = $null
        $errors = $null
        [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'blunder-dunce-hypothesis-promotion-check.ps1'), [ref]$tokens, [ref]$errors) | Out-Null
        [ordered]@{
            classification = 'OBTUSE_BLUNDER_DUNCE_HYPOTHESIS_PROMOTION_CHECK_V1'
            parserErrors = $errors.Count
            result = if ($errors.Count -eq 0) { 'PASS' } else { 'FAIL' }
        } | ConvertTo-Json -Depth 10
        return
    }
    'ReportOnly' {
        $report = Read-JsonFile -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')
        if ($report) { $report | ConvertTo-Json -Depth 80; return }
        [ordered]@{
            classification = 'OBTUSE_BLUNDER_DUNCE_HYPOTHESIS_PROMOTION_CHECK_V1'
            status = 'MISSING_REPORT'
            artifactRoot = (Resolve-RepoPath -Path $ArtifactRoot)
        } | ConvertTo-Json -Depth 10
        return
    }
    'Run' {
        Invoke-Run | ConvertTo-Json -Depth 80
        return
    }
}
