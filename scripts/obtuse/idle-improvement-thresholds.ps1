#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Validate','Score')]
    [string]$Mode = 'Validate',
    [string]$CandidatePath = 'artifacts/obtuse/idle-openrouter-deepseek-v4-pro-self-coding-acceleration-v1/model_generated_candidate_task.json',
    [string]$ValidationSummaryPath = '',
    [string]$ArtifactRoot = 'artifacts/obtuse/idle-openrouter-deepseek-v4-pro-self-coding-acceleration-v1'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent

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
    $json = $Value | ConvertTo-Json -Depth 40
    [System.IO.File]::WriteAllText($resolved, ($json -replace "`r?`n", "`r`n"), [System.Text.UTF8Encoding]::new($false))
}

function Add-JsonLine {
    param([string]$Path, [object]$Record)
    $resolved = Resolve-RepoPath -Path $Path
    $dir = Split-Path $resolved -Parent
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $line = ($Record | ConvertTo-Json -Depth 30 -Compress) + [Environment]::NewLine
    [System.IO.File]::AppendAllText($resolved, $line, [System.Text.UTF8Encoding]::new($false))
}

function Read-JsonFile {
    param([string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    if (-not (Test-Path -LiteralPath $resolved)) { return $null }
    try { return Get-Content -Raw -LiteralPath $resolved | ConvertFrom-Json -ErrorAction Stop } catch { return $null }
}

function Test-AllowedTargetFile {
    param([string]$Path)
    $normalized = $Path -replace '/', '\'
    if ($normalized -match '^(?i)scripts\\obtuse\\[^\\]+\.ps1$') { return $true }
    if ($normalized -match '^(?i)artifacts\\obtuse\\(idle-(openrouter-deepseek-v4-pro-self-coding-acceleration|deepseek-self-coding-compound-loop|switch-to-deepseek-v4-flash)-v1|blunder-flash-full-autonomy-compounding-bundle-v1)\\[^\\]+$') { return $true }
    return $false
}

function Test-InertSelfCodingTarget {
    param([string]$Path)
    $normalized = $Path -replace '/', '\'
    if ($normalized -match '^(?i)scripts\\obtuse\\idle-generated-') { return $true }
    if ($normalized -match '^(?i)scripts\\obtuse\\idle-(self-coding-compounding-metrics|replenishment-yield-report|inflow-capability-bridge-report|self-coding-novelty-pressure|self-coding-ledger-summary|self-coding-candidate-catalog|self-coding-throttle-report|openrouter-self-coding-health)\.ps1$') { return $true }
    return $false
}

function Test-ForbiddenCandidateText {
    param([string]$Text)
    if ([string]::IsNullOrWhiteSpace($Text)) { return $false }
    $patterns = @(
        '(?i)\bWANEFALL\b',
        '(?i)ShowMeAI\s+source',
        '(?i)\bADB\b|mobile/phone|phone/ADB',
        '(?i)benchmark\s+(launch|run|relaunch|execute)',
        '(?i)terminal-bench\s+(launch|run|relaunch|execute)',
        '(?i)provider\s+spend\s+escalation',
        '(?i)production\s+deploy',
        '(?i)git\s+(push|reset|commit)',
        '(?i)Remove-Item|del\s+|rmdir|delete\s+proof|wipe|destroy',
        'sk-or-[A-Za-z0-9_-]{20,}|sk-proj-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9_-]{20,}'
    )
    foreach ($pattern in $patterns) {
        if ($Text -match $pattern) { return $true }
    }
    return $false
}

function Get-ArrayCount {
    param([object]$Value)
    if ($null -eq $Value) { return 0 }
    return @($Value).Count
}

function Get-CandidatePlanningQuality {
    param([object]$Candidate)
    $issues = @()
    $score = 100
    if (-not $Candidate) {
        return [ordered]@{ score = 0; issues = @('candidate_missing'); pass = $false }
    }

    $title = [string]$Candidate.title
    $problem = [string]$Candidate.problem
    $patchPlan = @($Candidate.patchPlan)
    $validationPlan = @($Candidate.validationPlan)
    $rollbackPlan = @($Candidate.rollbackPlan)
    $expectedProof = @($Candidate.expectedProof)
    $targetFiles = @($Candidate.targetFiles)
    $qualityContract = $null
    if ($Candidate.PSObject.Properties.Name -contains 'qualityContract') { $qualityContract = $Candidate.qualityContract }

    if ([string]::IsNullOrWhiteSpace($title)) { $score -= 15; $issues += 'title_missing' }
    if ([string]::IsNullOrWhiteSpace($problem)) { $score -= 20; $issues += 'problem_missing' }
    if ($problem -notmatch '(?i)artifact|ledger|queue|status|failure|proof|inflow|validation|trace|evidence') {
        $score -= 15
        $issues += 'problem_lacks_evidence_anchor'
    }
    if ($targetFiles.Count -eq 0) { $score -= 20; $issues += 'target_files_missing' }
    if ($targetFiles.Count -gt 2) { $score -= 10; $issues += 'too_many_target_files' }
    if ($patchPlan.Count -eq 0) { $score -= 15; $issues += 'patch_plan_missing' }
    if ($patchPlan.Count -gt 5) { $score -= 10; $issues += 'patch_plan_too_broad' }
    if ($validationPlan.Count -eq 0) { $score -= 20; $issues += 'validation_plan_missing' }
    if (($validationPlan -join ' ') -notmatch '(?i)parser|validate|status|json|smoke|syntax') {
        $score -= 15
        $issues += 'validation_plan_not_concrete'
    }
    if ($rollbackPlan.Count -eq 0) { $score -= 20; $issues += 'rollback_plan_missing' }
    if (($rollbackPlan -join ' ') -notmatch '(?i)rollback|restore|snapshot|delete generated|revert target') {
        $score -= 15
        $issues += 'rollback_plan_not_concrete'
    }
    if ($expectedProof.Count -eq 0) { $score -= 15; $issues += 'expected_proof_missing' }
    if (($expectedProof -join ' ') -notmatch '(?i)json|jsonl|ledger|artifact|report|proof') {
        $score -= 10
        $issues += 'expected_proof_not_artifact_backed'
    }
    if ($qualityContract) {
        foreach ($field in @('rootCause','evidenceSource','validationGate','rollbackGate','promotionStop')) {
            if (-not ($qualityContract.PSObject.Properties.Name -contains $field) -or [string]::IsNullOrWhiteSpace([string]$qualityContract.$field)) {
                $score -= 5
                $issues += "quality_contract_missing_$field"
            }
        }
    } else {
        $score -= 15
        $issues += 'quality_contract_missing'
    }

    $score = [Math]::Min(100, [Math]::Max(0, $score))
    return [ordered]@{
        score = $score
        issues = $issues
        pass = ($score -ge 85 -and $issues.Count -le 2)
    }
}

function New-Score {
    param([object]$CandidateRecord, [object]$Validation)
    $candidate = if ($CandidateRecord -and $CandidateRecord.candidate) { $CandidateRecord.candidate } else { $CandidateRecord }
    $reasons = @()
    $accepted = $true

    if (-not $candidate) {
        $accepted = $false
        $reasons += 'Candidate missing or unreadable.'
    }

    $targets = @()
    if ($candidate -and $candidate.targetFiles) { $targets = @($candidate.targetFiles) }
    if ($targets.Count -eq 0) {
        $accepted = $false
        $reasons += 'No target files provided.'
    }
    foreach ($target in $targets) {
        if (-not (Test-AllowedTargetFile -Path ([string]$target))) {
            $accepted = $false
            $reasons += "Target outside allowed idle scope: $target"
        }
        if (Test-InertSelfCodingTarget -Path ([string]$target)) {
            $accepted = $false
            $reasons += "Inert report/helper target is not a capability-improving patch: $target"
        }
    }

    $patchType = if ($candidate -and $candidate.expectedPatchType) { [string]$candidate.expectedPatchType } else { '' }
    if ($patchType -match '^(?i)GENERATED_SAFE_REPORT_HELPER' -or $patchType -match '(?i)_COMMENT$') {
        $accepted = $false
        $reasons += "Patch type does not change capability-bearing behavior: $patchType"
    }

    $risk = if ($candidate) { [string]$candidate.riskLevel } else { '' }
    if ($risk -eq 'HIGH' -or [string]::IsNullOrWhiteSpace($risk)) {
        $accepted = $false
        $reasons += "Rejected risk level: $risk"
    }

    if ((Get-ArrayCount -Value $candidate.validationPlan) -eq 0) {
        $accepted = $false
        $reasons += 'Validation plan missing.'
    }
    if ((Get-ArrayCount -Value $candidate.rollbackPlan) -eq 0) {
        $accepted = $false
        $reasons += 'Rollback plan missing.'
    }

    $planningQuality = Get-CandidatePlanningQuality -Candidate $candidate
    if (-not [bool]$planningQuality.pass) {
        $accepted = $false
        $reasons += "Planning quality gate failed: score=$($planningQuality.score); issues=$($planningQuality.issues -join ',')"
    }

    $candidateText = if ($candidate) { $candidate | ConvertTo-Json -Depth 20 } else { '' }
    if (Test-ForbiddenCandidateText -Text $candidateText) {
        $accepted = $false
        $reasons += 'Forbidden scope, destructive action, benchmark launch, or secret-like text detected.'
    }

    $validationPass = $false
    if ($Validation) {
        $validationPass = (
            $Validation.parserErrorsZero -eq $true -and
            $Validation.targetedValidation -eq 'PASS' -and
            $Validation.v5Validate -eq 'PASS' -and
            $Validation.v7Validate -eq 'PASS' -and
            $Validation.statusCommand -eq 'PASS' -and
            $Validation.safetyChecksClean -eq $true
        )
    }

    $scores = [ordered]@{
        correctness = $(if ($validationPass) { 95 } elseif ($accepted) { 90 } else { 50 })
        safety = $(if ($accepted) { 100 } else { 60 })
        usefulness = $(if ($accepted) { 75 } else { 40 })
        simplicity = $(if ($targets.Count -le 2) { 95 } else { 80 })
        proofQuality = $(if ($validationPass) { 90 } elseif ($accepted) { 85 } else { 50 })
        planningQuality = [int]$planningQuality.score
    }
    $thresholdsPass = (
        $accepted -and
        $scores.safety -ge 95 -and
        $scores.correctness -ge 90 -and
        $scores.proofQuality -ge 85 -and
        $scores.usefulness -ge 70 -and
        $scores.planningQuality -ge 85
    )
    $promotionPass = ($thresholdsPass -and $validationPass)
    $decision = if ($promotionPass) {
        'PROMOTE'
    } elseif ($thresholdsPass) {
        'PASS_PRECHECK'
    } else {
        'ROLLBACK_OR_REJECT'
    }

    return [ordered]@{
        classification = 'OBTUSE_BLUNDER_IDLE_OPENROUTER_DEEPSEEK_V4_PRO_SELF_CODING_ACCELERATION_V1'
        timestamp = (Get-Date -Format 'o')
        candidateAccepted = $thresholdsPass
        thresholdDecision = $decision
        promotionAllowed = $promotionPass
        riskLevel = $risk
        targetFiles = $targets
        scores = $scores
        thresholds = [ordered]@{
            safety = 95
            correctness = 90
            proofQuality = 85
            usefulness = 70
            planningQuality = 85
        }
        validationPass = $validationPass
        hardStopViolation = (-not $accepted)
        planningQuality = $planningQuality
        reasons = $reasons
    }
}

New-Item -ItemType Directory -Force -Path (Resolve-RepoPath -Path $ArtifactRoot) | Out-Null
$ledgerPath = Join-Path $ArtifactRoot 'threshold_decision_ledger.jsonl'
if (-not (Test-Path -LiteralPath (Resolve-RepoPath -Path $ledgerPath))) { New-Item -ItemType File -Path (Resolve-RepoPath -Path $ledgerPath) -Force | Out-Null }

if ($Mode -eq 'Validate') {
    [ordered]@{
        classification = 'OBTUSE_BLUNDER_IDLE_OPENROUTER_DEEPSEEK_V4_PRO_SELF_CODING_ACCELERATION_V1'
        mode = 'Validate'
        result = 'PASS'
        allowedScope = @('scripts/obtuse/*.ps1','artifacts/obtuse/blunder-flash-full-autonomy-compounding-bundle-v1/*')
        thresholds = [ordered]@{ safety = 95; correctness = 90; proofQuality = 85; usefulness = 70; planningQuality = 85 }
    } | ConvertTo-Json -Depth 10
    return
}

$candidateRecord = Read-JsonFile -Path $CandidatePath
$validation = if ($ValidationSummaryPath) { Read-JsonFile -Path $ValidationSummaryPath } else { $null }
$decision = New-Score -CandidateRecord $candidateRecord -Validation $validation
Write-JsonFile -Value $decision -Path (Join-Path $ArtifactRoot 'threshold_decision_latest.json')
Add-JsonLine -Path $ledgerPath -Record $decision
$decision | ConvertTo-Json -Depth 20


# THRESHOLD-PROMOTION-RULES-MARKER-v1: promotion thresholds are safety>=95, correctness>=90, proofQuality>=85, usefulness>=70, planningQuality>=85.
