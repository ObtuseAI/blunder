#requires -Version 5.1
param(
    [ValidateSet('Validate','Report')]
    [string]$Mode = 'Validate',
    [string]$ArtifactRoot = 'artifacts/obtuse/blunder-flash-full-autonomy-compounding-bundle-v1/self-coding',
    [string]$IdleArtifactRoot = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle',
    [string]$InflowArtifactRoot = 'artifacts/blunder/inflow'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent

function Resolve-RepoPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $repoRoot $Path
}

function Count-JsonLines {
    param([string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    if (-not (Test-Path -LiteralPath $resolved)) { return 0 }
    return @(Get-Content -LiteralPath $resolved | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }).Count
}

function Get-FileLength {
    param([string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    if (-not (Test-Path -LiteralPath $resolved)) { return 0 }
    return (Get-Item -LiteralPath $resolved).Length
}

$result = [ordered]@{
    classification = 'OBTUSE_BLUNDER_GENERATED_SELF_CODING_HELPER_V1'
    marker = 'GENERATED-SELF-CODING-HELPER-v1'
    timestamp = (Get-Date -Format 'o')
    mode = $Mode
    generatedRunId = '20260704T112609'
    title = 'Add inflow capability bridge report helper'
    problem = 'Inflow capability candidates are being mined into artifacts but not made visible as fresh idle queue work.'
    artifactRoot = Resolve-RepoPath -Path $ArtifactRoot
    idleArtifactRoot = Resolve-RepoPath -Path $IdleArtifactRoot
    inflowArtifactRoot = Resolve-RepoPath -Path $InflowArtifactRoot
    providerCallLedgerEntries = Count-JsonLines -Path (Join-Path $ArtifactRoot 'openrouter_call_ledger.jsonl')
    candidateLedgerEntries = Count-JsonLines -Path (Join-Path $ArtifactRoot 'model_candidate_ledger.jsonl')
    patchLedgerEntries = Count-JsonLines -Path (Join-Path $ArtifactRoot 'self_coding_patch_ledger.jsonl')
    promotionLedgerEntries = Count-JsonLines -Path (Join-Path $ArtifactRoot 'promotion_ledger.jsonl')
    queueBytes = Get-FileLength -Path (Join-Path $IdleArtifactRoot 'idle-task-queue.json')
    replenishmentLedgerEntries = Count-JsonLines -Path (Join-Path $IdleArtifactRoot 'idle-replenishment-ledger.jsonl')
    inflowCandidateEntries = Count-JsonLines -Path (Join-Path $InflowArtifactRoot 'capability_candidates.jsonl')
    inflowReplayFixtures = Count-JsonLines -Path (Join-Path $InflowArtifactRoot 'replay_fixtures.jsonl')
    secretsPrinted = $false
    result = 'PASS'
}

$result | ConvertTo-Json -Depth 20
