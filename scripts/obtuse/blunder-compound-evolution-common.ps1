#requires -Version 5.1

function Invoke-BlunderCompoundEvolutionPlan {
    param(
        [Parameter(Mandatory)] [string]$RepoRoot,
        [Parameter(Mandatory)] [string]$ArtifactRoot,
        [Parameter(Mandatory)] [string]$PlannerPath
    )
    if (-not (Test-Path -LiteralPath $PlannerPath)) {
        throw "Compound evolution planner is missing: $PlannerPath"
    }
    $python = Get-Command python -ErrorAction Stop
    $ledgerRoot = if ([System.IO.Path]::IsPathRooted($ArtifactRoot)) { $ArtifactRoot } else { Join-Path $RepoRoot $ArtifactRoot }
    $planPath = Join-Path $ledgerRoot 'compound_evolution_plan.json'
    $raw = & $python.Source $PlannerPath --ledger-root $ledgerRoot --plan-path $planPath
    if ($LASTEXITCODE -ne 0) {
        throw "Compound evolution planner failed with exit code $LASTEXITCODE."
    }
    try {
        $plan = ($raw -join [Environment]::NewLine) | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        throw "Compound evolution planner returned invalid JSON: $($_.Exception.Message)"
    }
    if ($plan.status -ne 'PASS' -or $plan.safety.advisoryOnly -ne $true) {
        throw 'Compound evolution planner failed its advisory-only safety contract.'
    }
    if ([int]$plan.pressure.candidateBudgetNext -lt 1) {
        throw 'Compound evolution planner returned an invalid candidate budget.'
    }
    return $plan
}
