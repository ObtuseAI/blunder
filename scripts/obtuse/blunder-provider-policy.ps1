#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Validate','Check','RecordCall','RecordTimeout')]
    [string]$Mode = 'Check',
    [string]$ArtifactRoot = 'artifacts/obtuse/blunder-flash-full-autonomy-compounding-bundle-v1',
    [string]$Provider = 'OpenRouter',
    [string]$Model = 'deepseek-v4-flash',
    [int]$BudgetPerRun = 60,
    [int]$CallCapPerLoop = 60,
    [int]$CallCapPerCycle = 3,
    [int]$TimeoutSeconds = 120,
    [int]$MaxRetries = 1,
    [int]$MaxConsecutiveFailures = 2,
    [int]$MaxTotalFailures = 10,
    [string]$OperatorApproved = 'false',
    [string]$SelfCodingArtifactRoot = 'artifacts/obtuse/blunder-flash-full-autonomy-compounding-bundle-v1',
    [string]$CompoundLoopArtifactRoot = 'artifacts/obtuse/blunder-flash-full-autonomy-compounding-bundle-v1'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent

function Resolve-RepoPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $repoRoot $Path
}

function Add-JsonLine {
    param([string]$Path, [object]$Record)
    $resolved = Resolve-RepoPath -Path $Path
    $dir = Split-Path $resolved -Parent
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $Record | ConvertTo-Json -Depth 20 -Compress | Add-Content -LiteralPath $resolved -Encoding UTF8
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

function Convert-ToBoolean {
    param([object]$Value)
    if ($Value -is [bool]) { return [bool]$Value }
    if ($Value -is [int]) { return ([int]$Value -ne 0) }
    return (([string]$Value).Trim().ToLowerInvariant() -in @('true','1','yes','y','approved'))
}

$OperatorApproved = (Convert-ToBoolean -Value $OperatorApproved) -or (Convert-ToBoolean -Value $env:OBTUSE_OPENROUTER_OPERATOR_APPROVED)

$artRoot = Resolve-RepoPath -Path $ArtifactRoot
New-Item -ItemType Directory -Force -Path $artRoot | Out-Null

$policyPath = Join-Path $ArtifactRoot 'provider_policy.json'
$budgetLedgerPath = Join-Path $ArtifactRoot 'provider_budget_ledger.jsonl'
$timeoutLedgerPath = Join-Path $ArtifactRoot 'provider_timeout_ledger.jsonl'

$policy = [ordered]@{
    classification = 'OBTUSE_BLUNDER_PROVIDER_POLICY_V1'
    timestamp = (Get-Date -Format 'o')
    provider = $Provider
    model = $Model
    budgetPerRun = $BudgetPerRun
    callCapPerLoop = $CallCapPerLoop
    callCapPerCycle = $CallCapPerCycle
    timeoutSeconds = $TimeoutSeconds
    maxRetries = $MaxRetries
    consecutiveFailureHardStop = $MaxConsecutiveFailures
    totalFailureHardStop = $MaxTotalFailures
    modelSwitchPolicy = 'DISABLED_UNLESS_EXPLICITLY_APPROVED'
    providerSpendEscalationPolicy = 'DISABLED_BY_OPERATOR_CHEAP_MODEL_POLICY'
    monetaryBudgetGateEnabled = $false
    budgetHardStopEnabled = $false
    secretRedactionPolicy = 'REQUIRED'
    callLedgerRequired = $true
    budgetLedgerRequired = $true
    timeoutLedgerRequired = $true
}
$policy | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Resolve-RepoPath -Path $policyPath) -Encoding UTF8

if ($Mode -eq 'Validate') {
    $result = [ordered]@{
        classification = 'OBTUSE_BLUNDER_PROVIDER_POLICY_V1'
        mode = 'Validate'
        timestamp = (Get-Date -Format 'o')
        policyPresent = $true
        budgetLedgerPresent = (Test-Path -LiteralPath (Resolve-RepoPath -Path $budgetLedgerPath))
        timeoutLedgerPresent = (Test-Path -LiteralPath (Resolve-RepoPath -Path $timeoutLedgerPath))
        result = 'PASS'
    }
    $result | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $artRoot 'provider_policy_validate_result.json') -Encoding UTF8
    $result | ConvertTo-Json -Depth 10
    return
}

$callLedger1 = Read-JsonLines -Path (Join-Path $SelfCodingArtifactRoot 'openrouter_call_ledger.jsonl')
$callLedger2 = Read-JsonLines -Path (Join-Path $CompoundLoopArtifactRoot 'openrouter_call_ledger.jsonl')
$callLedger = @($callLedger1) + @($callLedger2)
$timeoutLedger = Read-JsonLines -Path $timeoutLedgerPath

$attempted = @($callLedger | Where-Object { $_.callAttempted -eq $true }).Count
$succeeded = @($callLedger | Where-Object { $_.callSucceeded -eq $true -or $_.result -in @('SUCCESS','OK','PASS') }).Count
$failed = @($callLedger | Where-Object { $_.callAttempted -eq $true -and ($_.callSucceeded -ne $true -and $_.result -notin @('SUCCESS','OK','PASS')) }).Count
$timeoutCount = @($timeoutLedger | Where-Object { $_.event -in @('TIMEOUT','PROVIDER_TIMEOUT') }).Count

$consecutiveFailures = 0
for ($i = $callLedger.Count - 1; $i -ge 0; $i--) {
    $row = $callLedger[$i]
    if (-not ($row.callAttempted -eq $true)) { continue }
    if ($row.callSucceeded -eq $true -or $row.result -in @('SUCCESS','OK','PASS')) { break }
    $consecutiveFailures++
}

$hardStopReasons = @()
if ($consecutiveFailures -ge $MaxConsecutiveFailures) { $hardStopReasons += "CONSECUTIVE_FAILURE_HARD_STOP ($consecutiveFailures >= $MaxConsecutiveFailures)" }
if ($failed -ge $MaxTotalFailures) { $hardStopReasons += "TOTAL_FAILURE_HARD_STOP ($failed >= $MaxTotalFailures)" }

$budgetStatus = $(if ($hardStopReasons.Count -gt 0) { 'HARD_STOP' } else { 'DISABLED_BY_OPERATOR_CHEAP_MODEL_POLICY' })

$checkRecord = [ordered]@{
    timestamp = (Get-Date -Format 'o')
    mode = $Mode
    provider = $Provider
    model = $Model
    attempted = $attempted
    succeeded = $succeeded
    failed = $failed
    timeoutCount = $timeoutCount
    consecutiveFailures = $consecutiveFailures
    budgetPerRun = $BudgetPerRun
    callCapPerLoop = $CallCapPerLoop
    callCapPerCycle = $CallCapPerCycle
    monetaryBudgetGateEnabled = $false
    budgetHardStopEnabled = $false
    budgetStatus = $budgetStatus
    hardStopReasons = $hardStopReasons
    operatorApproved = $OperatorApproved
}

if ($Mode -eq 'RecordCall') {
    $checkRecord.event = 'CALL_RECORDED'
    Add-JsonLine -Path $budgetLedgerPath -Record $checkRecord
}
elseif ($Mode -eq 'RecordTimeout') {
    $timeoutRecord = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        event = 'PROVIDER_TIMEOUT'
        provider = $Provider
        model = $Model
        timeoutSeconds = $TimeoutSeconds
        operatorApproved = $OperatorApproved
    }
    Add-JsonLine -Path $timeoutLedgerPath -Record $timeoutRecord
    $timeoutRecord | ConvertTo-Json -Depth 10
    return
}
else {
    Add-JsonLine -Path $budgetLedgerPath -Record $checkRecord
}

$checkRecord | ConvertTo-Json -Depth 10
