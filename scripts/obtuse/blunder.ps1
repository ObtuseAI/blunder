#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Status','Start','Stop','Validate','RunOnce','RunForever','DunceRegression','Replenish','Promote','MineArtifacts','PlanningCodingGate','CapabilityAudit','Handoff')]
    [string]$Command = 'Status',
    [string]$OperatorApproved = 'false',
    [ValidateSet('capability','memory','genome','organ','agent-team','research-concept','strategy','improvement','release-candidate')]
    [string]$PromotionScope = 'improvement',
    [string]$PromotionEvidencePath = ''
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'blunder-full-autonomy-common.ps1')
. (Join-Path $PSScriptRoot 'blunder-operator-authorization.ps1')

$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$operatorAuthorization = Get-BlunderOperatorAuthorization -RepoRoot $repoRoot
$operatorApprovedBool = (ConvertTo-BlunderBoolean -Value $OperatorApproved) -or (Test-BlunderAuthorizationScope -Authorization $operatorAuthorization -Scope 'continuous_idle_self_improvement')
$effectiveOperatorApproved = if ($operatorApprovedBool) { 'true' } else { 'false' }

switch ($Command) {
    'Status' {
        & (Join-Path $PSScriptRoot 'blunder-continuous-autonomy.ps1') -Mode Status
        return
    }
    'Start' {
        & (Join-Path $PSScriptRoot 'blunder-continuous-autonomy.ps1') -Mode Start -OperatorApproved $effectiveOperatorApproved
        return
    }
    'Stop' {
        & (Join-Path $PSScriptRoot 'blunder-continuous-autonomy.ps1') -Mode Stop
        return
    }
    'Validate' {
        Write-BlunderAutonomyPolicyRecord -Mode 'Validate' | Out-Null
        Write-BlunderBudgetGateRecord | Out-Null
        Write-BlunderQueueReplenishmentRecord -Mode 'Validate' -AdmitCandidates $false | Out-Null
        Write-BlunderPromotionPolicyRecord -Mode 'Validate' | Out-Null
        Write-BlunderArtifactMiningRecord -Mode 'Validate' | Out-Null
        Write-BlunderSkillTokenRecord -Mode 'Validate' | Out-Null
        Write-BlunderControlSurfaceRecord | Out-Null
        Write-BlunderOperatorTodo | Out-Null
        Write-BlunderOperatorHandoff | Out-Null
        $summary = Write-BlunderFinalValidationSummary
        $summary | ConvertTo-Json -Depth 100
        return
    }
    'RunOnce' {
        & (Join-Path $PSScriptRoot 'blunder-continuous-autonomy.ps1') -Mode RunOnce -OperatorApproved $effectiveOperatorApproved
        return
    }
    'RunForever' {
        & (Join-Path $PSScriptRoot 'blunder-continuous-autonomy.ps1') -Mode RunForever -OperatorApproved $effectiveOperatorApproved
        return
    }
    'DunceRegression' {
        & (Join-Path $PSScriptRoot 'blunder-dunce-proof-regression-pack.ps1') -Mode Validate
        return
    }
    'Replenish' {
        if (-not $operatorApprovedBool) { throw 'OperatorApproved true or standing local autonomy authorization is required for Replenish.' }
        & (Join-Path $PSScriptRoot 'blunder-autonomous-queue-replenishment.ps1') -Mode ReplenishOnce
        return
    }
    'Promote' {
        & (Join-Path $PSScriptRoot 'blunder-autonomous-promotion.ps1') -Mode PromoteSafe -Scope $PromotionScope -EvidencePath $PromotionEvidencePath
        return
    }
    'MineArtifacts' {
        & (Join-Path $PSScriptRoot 'blunder-artifact-miner.ps1') -Mode MineOnce
        return
    }
    'PlanningCodingGate' {
        & (Join-Path $PSScriptRoot 'blunder-planning-coding-quality-gate.ps1') -Mode Validate
        return
    }
    'CapabilityAudit' {
        & (Join-Path $PSScriptRoot 'blunder-s-tier-capability-audit.ps1') -Mode Validate
        return
    }
    'Handoff' {
        Write-BlunderOperatorTodo | Out-Null
        $handoffPath = Write-BlunderOperatorHandoff
        $report = Write-BlunderFinalReport
        [ordered]@{
            classification = $script:BlunderFullAutonomyClassification
            handoffPath = $handoffPath
            finalReportPath = $report.finalReportPath
            terminalState = $report.terminalState
            result = 'PASS'
        } | ConvertTo-Json -Depth 100
        return
    }
}
