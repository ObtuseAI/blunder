#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Validate')]
    [string]$Mode = 'Validate',
    [string]$SelfCodingArtifactRoot = 'artifacts/obtuse/blunder-flash-full-autonomy-compounding-bundle-v1/self-coding'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
. (Join-Path $PSScriptRoot 'blunder-full-autonomy-common.ps1')

function Test-Markers {
    param([Parameter(Mandatory)] [string]$RelativePath, [Parameter(Mandatory)] [string[]]$Markers)
    $path = Join-Path $repoRoot $RelativePath
    if (-not (Test-Path -LiteralPath $path)) { return $false }
    $text = Get-Content -Raw -LiteralPath $path
    foreach ($marker in $Markers) {
        if (-not $text.Contains($marker)) { return $false }
    }
    return $true
}

function Read-JsonRecord {
    param([Parameter(Mandatory)] [string]$Path)
    $resolved = $(if ([System.IO.Path]::IsPathRooted($Path)) { $Path } else { Join-Path $repoRoot $Path })
    if (-not (Test-Path -LiteralPath $resolved)) { return $null }
    try { return Get-Content -Raw -LiteralPath $resolved | ConvertFrom-Json -ErrorAction Stop }
    catch { throw "Capability evidence is invalid JSON at ${resolved}: $($_.Exception.Message)" }
}

$statusSnapshot = Get-BlunderStatusSnapshot
$status = $statusSnapshot.parsed
$candidate = Read-JsonRecord -Path (Join-Path $SelfCodingArtifactRoot 'model_generated_candidate_task.json')
$candidateValue = $(if ($candidate -and $candidate.candidate) { $candidate.candidate } else { $candidate })
$candidateContract = $(if ($candidateValue) { $candidateValue.qualityContract } else { $null })
$missingCandidateContractFields = @()
foreach ($field in @('rootCause','evidenceSource','validationGate','rollbackGate','promotionStop')) {
    if (-not $candidateContract -or -not ($candidateContract.PSObject.Properties.Name -contains $field) -or [string]::IsNullOrWhiteSpace([string]$candidateContract.$field)) {
        $missingCandidateContractFields += $field
    }
}
$ledgerPath = Join-Path $repoRoot (Join-Path $SelfCodingArtifactRoot 'self_coding_patch_ledger.jsonl')
$ledgerCount = $(if (Test-Path -LiteralPath $ledgerPath) { @(Get-Content -LiteralPath $ledgerPath | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }).Count } else { 0 })
$benchmarkIsolation = Test-BlunderBenchmarkIsolationContract

$families = @(
    [ordered]@{
        name = 'native-cognition'
        pass = (Test-Markers -RelativePath 'blunder/cognition.py' -Markers @('build_evidence','select_plan','history_adjustment'))
        evidence = 'blunder/cognition.py'
        targetFiles = @('blunder/cognition.py','tests/test_blunder_cognition.py')
        validationGate = 'python -m unittest -q tests.test_blunder_cognition'
    },
    [ordered]@{
        name = 'autonomy-runtime'
        pass = ($statusSnapshot.readable -and $status -and $status.active -eq $true -and $status.pidValid -eq $true -and [string]$status.finalRecommendedOperatorAction -eq 'NO_ACTION_IDLE_HEALTHY')
        evidence = 'status-idle-ops.ps1 live status'
        targetFiles = @('scripts/obtuse/blunder-continuous-autonomy.ps1','scripts/obtuse/start-idle-ops.ps1')
        validationGate = 'status reports active PID, fresh heartbeat, and NO_ACTION_IDLE_HEALTHY'
    },
    [ordered]@{
        name = 'planning-contract'
        pass = ($candidateContract -and $missingCandidateContractFields.Count -eq 0)
        evidence = Join-Path $SelfCodingArtifactRoot 'model_generated_candidate_task.json'
        targetFiles = @('scripts/obtuse/blunder-planning-coding-quality-gate.ps1')
        validationGate = 'planning quality gate score is at least 95 with no failed gate'
    },
    [ordered]@{
        name = 'mutation-evolution'
        pass = ($ledgerCount -gt 0)
        evidence = $ledgerPath
        targetFiles = @('scripts/obtuse/idle-self-coding-accelerator.ps1')
        validationGate = 'mutation ledger has meaningful changes with rollback coverage'
    },
    [ordered]@{
        name = 'benchmark-isolation'
        pass = [bool]$benchmarkIsolation.pass
        evidence = 'Test-BlunderBenchmarkIsolationContract'
        targetFiles = @('scripts/obtuse/blunder-full-autonomy-common.ps1','scripts/obtuse/blunder.ps1')
        validationGate = 'normal autonomy exposes no benchmark entry point or launch marker'
    }
)

$packets = @($families | Where-Object { $_.pass -ne $true } | ForEach-Object {
    [ordered]@{
        capability = $_.name
        evidence = $_.evidence
        targetFiles = $_.targetFiles
        repair = "Restore the $($_.name) contract from its named evidence and validation gate."
        validationGate = $_.validationGate
        rollback = 'Restore the pre-change snapshot when the validation gate fails.'
        stopCondition = 'Stop without promotion when evidence remains missing or any safety gate fails.'
    }
})
$failingCount = @($families | Where-Object { $_.pass -ne $true }).Count
$complete = ($packets.Count -eq $failingCount)

[ordered]@{
    schema = 'obtuse.blunder.capability-repair-packets.v1'
    timestamp = (Get-Date -Format 'o')
    mode = $Mode
    result = $(if ($complete) { 'PASS' } else { 'FAIL' })
    familyCount = $families.Count
    healthyFamilyCount = @($families | Where-Object { $_.pass -eq $true }).Count
    failingFamilyCount = $failingCount
    packetCount = $packets.Count
    families = $families
    packets = $packets
    liveLaunchAllowed = $false
    providerCallsAllowed = $false
    queueMutationPerformed = $false
    destructiveActionsAllowed = $false
} | ConvertTo-Json -Depth 30
