#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Validate','EvaluateCandidate','PromoteSafe','Rollback','ReportOnly')]
    [string]$Mode = 'Validate',
    [ValidateSet('capability','memory','genome','organ','agent-team','research-concept','strategy','improvement','release-candidate')]
    [string]$Scope = 'improvement',
    [string]$EvidencePath = ''
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'blunder-full-autonomy-common.ps1')

if ($Mode -in @('EvaluateCandidate','PromoteSafe')) {
    if ([string]::IsNullOrWhiteSpace($EvidencePath)) {
        throw 'Autonomous promotion requires -EvidencePath with one strict PromotionEvidenceV1 JSON file.'
    }
    $repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
    & python -m blunder.promotion_cli --root $repoRoot --scope $Scope --evidence $EvidencePath
    exit $LASTEXITCODE
}

$record = Write-BlunderPromotionPolicyRecord -Mode $Mode
$record | ConvertTo-Json -Depth 100
