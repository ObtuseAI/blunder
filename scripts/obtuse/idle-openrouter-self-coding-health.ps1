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

function Read-JsonFile {
    param([string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    if (-not (Test-Path -LiteralPath $resolved)) { return $null }
    try { return Get-Content -Raw -LiteralPath $resolved | ConvertFrom-Json -ErrorAction Stop } catch { return $null }
}

function Count-JsonLines {
    param([string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    if (-not (Test-Path -LiteralPath $resolved)) { return 0 }
    return @(Get-Content -LiteralPath $resolved | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }).Count
}

$root = Resolve-RepoPath -Path $ArtifactRoot
$config = Read-JsonFile -Path (Join-Path $ArtifactRoot 'openrouter_provider_lane_config.json')
$final = Read-JsonFile -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')
$result = [ordered]@{
    classification = 'OBTUSE_BLUNDER_IDLE_OPENROUTER_SELF_CODING_HEALTH_V1'
    timestamp = (Get-Date -Format 'o')
    mode = $Mode
    artifactRoot = $root
    artifactRootExists = (Test-Path -LiteralPath $root)
    configPresent = [bool]$config
    finalReportPresent = [bool]$final
    openRouterIdleEnabled = $(if ($final) { $final.openRouterIdleEnabled } elseif ($config) { $config.openRouterIdleEnabled } else { $false })
    model = $(if ($final) { $final.modelUsed } elseif ($config) { $config.modelUsed } else { $null })
    callLedgerEntries = Count-JsonLines -Path (Join-Path $ArtifactRoot 'openrouter_call_ledger.jsonl')
    candidateLedgerEntries = Count-JsonLines -Path (Join-Path $ArtifactRoot 'model_candidate_ledger.jsonl')
    promotionLedgerEntries = Count-JsonLines -Path (Join-Path $ArtifactRoot 'promotion_ledger.jsonl')
    rollbackLedgerEntries = Count-JsonLines -Path (Join-Path $ArtifactRoot 'rollback_ledger.jsonl')
    secretsPrinted = $false
    result = $(if (Test-Path -LiteralPath $root) { 'PASS' } else { 'FAIL' })
}

$result | ConvertTo-Json -Depth 10
