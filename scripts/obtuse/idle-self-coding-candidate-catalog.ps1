#requires -Version 5.1
param(
    [ValidateSet('Validate','Report')]
    [string]$Mode = 'Validate'
)

$ErrorActionPreference = 'Stop'
$catalog = @(
    [ordered]@{ id = 'ledger-summary-helper'; target = 'scripts/obtuse/idle-self-coding-ledger-summary.ps1'; risk = 'LOW'; purpose = 'Summarize promotions, rollbacks, provider calls, and duplicate pressure.' },
    [ordered]@{ id = 'candidate-catalog-helper'; target = 'scripts/obtuse/idle-self-coding-candidate-catalog.ps1'; risk = 'LOW'; purpose = 'Expose safe candidate families and forbidden scopes for operators.' },
    [ordered]@{ id = 'throttle-report-helper'; target = 'scripts/obtuse/idle-self-coding-throttle-report.ps1'; risk = 'LOW'; purpose = 'Report cooldown, provider failures, and compounding readiness.' },
    [ordered]@{ id = 'health-helper'; target = 'scripts/obtuse/idle-openrouter-self-coding-health.ps1'; risk = 'LOW'; purpose = 'Report OpenRouter self-coding artifact health.' }
)
$result = [ordered]@{
    classification = 'OBTUSE_BLUNDER_SELF_CODING_CANDIDATE_CATALOG_V1'
    timestamp = (Get-Date -Format 'o')
    mode = $Mode
    candidateFamilies = $catalog
    forbiddenScopes = @('benchmarks','WANEFALL','ShowMeAI source','mobile/ADB','production deployment','provider secrets','destructive cleanup','git push/reset/commit')
    modelPolicy = 'deepseek-v4-flash default only; Pro requires explicit manual override.'
    result = 'PASS'
}
$result | ConvertTo-Json -Depth 20
