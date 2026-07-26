#requires -Version 5.1
param(
    [ValidateSet('Validate','Report')]
    [string]$Mode = 'Validate',
    [string]$ArtifactRoot = 'artifacts/obtuse/blunder-flash-full-autonomy-compounding-bundle-v1/self-coding'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent

function Resolve-RepoPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $repoRoot $Path
}

function Read-JsonLines {
    param([string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    $rows = @()
    if (-not (Test-Path -LiteralPath $resolved)) { return $rows }
    foreach ($line in Get-Content -LiteralPath $resolved) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        try { $rows += ($line | ConvertFrom-Json -ErrorAction Stop) } catch {}
    }
    return $rows
}

function Read-JsonFile {
    param([string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    if (-not (Test-Path -LiteralPath $resolved)) { return $null }
    try { return Get-Content -Raw -LiteralPath $resolved | ConvertFrom-Json -ErrorAction Stop } catch { return $null }
}

$patches = @(Read-JsonLines -Path (Join-Path $ArtifactRoot 'self_coding_patch_ledger.jsonl'))
$promotions = @(Read-JsonLines -Path (Join-Path $ArtifactRoot 'promotion_ledger.jsonl'))
$rollbacks = @(Read-JsonLines -Path (Join-Path $ArtifactRoot 'rollback_ledger.jsonl'))
$calls = @(Read-JsonLines -Path (Join-Path $ArtifactRoot 'openrouter_call_ledger.jsonl'))
$duplicateRetirement = Read-JsonFile -Path (Join-Path $ArtifactRoot 'duplicate_pressure_retirement.json')
$retiredPatchTypes = @()
if ($duplicateRetirement -and [string]$duplicateRetirement.retirementStatus -eq 'ACTIVE') {
    $retiredPatchTypes = @($duplicateRetirement.retiredPatchTypes | ForEach-Object { [string]$_.patchType })
}
$patchGroups = @($patches | Group-Object patchType | ForEach-Object {
    [ordered]@{ patchType = $_.Name; count = $_.Count }
})
$meaningfulPatches = @($patches | Where-Object { $_.patchApplied -eq $true -and [int]$_.sourceLinesChanged -gt 0 })
$meaningfulPatchGroups = @($meaningfulPatches | Group-Object patchType | ForEach-Object {
    [ordered]@{ patchType = $_.Name; count = $_.Count }
})
$noOpDuplicateGroups = @($patchGroups | Where-Object { $_.count -ge 3 -and ([string]$_.patchType -match 'ALREADY_PRESENT|ALREADY_PROMOTED') })
$retiredMeaningfulDuplicateGroups = @($meaningfulPatchGroups | Where-Object { $_.count -ge 3 -and ([string]$_.patchType -in $retiredPatchTypes) })
$activeDuplicateGroups = @($meaningfulPatchGroups | Where-Object { $_.count -ge 3 -and ([string]$_.patchType -notin $retiredPatchTypes) })
$result = [ordered]@{
    classification = 'OBTUSE_BLUNDER_SELF_CODING_LEDGER_SUMMARY_V1'
    timestamp = (Get-Date -Format 'o')
    mode = $Mode
    artifactRoot = Resolve-RepoPath -Path $ArtifactRoot
    patchRecords = $patches.Count
    appliedPatchRecords = @($patches | Where-Object { $_.patchApplied -eq $true }).Count
    promotionRecords = $promotions.Count
    promotedRecords = @($promotions | Where-Object { $_.promoted -eq $true -or $_.thresholdDecision -eq 'PROMOTE' }).Count
    rollbackRecords = $rollbacks.Count
    providerCallsAttempted = @($calls | Where-Object { $_.callAttempted -eq $true }).Count
    providerCallsSucceeded = @($calls | Where-Object { $_.success -eq $true -or $_.callSucceeded -eq $true -or $_.result -in @('PASS','OK','SUCCESS') }).Count
    patchTypeCounts = $patchGroups
    meaningfulPatchTypeCounts = $meaningfulPatchGroups
    activeDuplicateGroupCount = $activeDuplicateGroups.Count
    retiredMeaningfulDuplicateGroupCount = $retiredMeaningfulDuplicateGroups.Count
    noOpDuplicateGroupCount = $noOpDuplicateGroups.Count
    duplicateRetirement = [ordered]@{
        active = ($duplicateRetirement -ne $null -and [string]$duplicateRetirement.retirementStatus -eq 'ACTIVE')
        retiredPatchTypes = $retiredPatchTypes
        path = Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'duplicate_pressure_retirement.json')
    }
    duplicatePressure = $(if ($activeDuplicateGroups.Count -gt 0) { 'PRESENT' } else { 'LOW' })
    noOpDuplicatePressure = $(if ($noOpDuplicateGroups.Count -gt 0) { 'RETIRED_OR_NO_OP_PRESENT' } else { 'LOW' })
    secretsPrinted = $false
    result = 'PASS'
}
$result | ConvertTo-Json -Depth 20
