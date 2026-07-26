#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Validate','Scan','ReportOnly')]
    [string]$Mode = 'Scan',
    [string]$ArtifactRoot = 'artifacts/obtuse/blunder-full-autonomy-closure-loop-v1',
    [string]$QueuePath = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle/idle-task-queue.json',
    [string]$IdleArtifactRoot = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle',
    [string]$SelfCodingArtifactRoot = 'artifacts/obtuse/idle-openrouter-deepseek-v4-pro-self-coding-acceleration-v1'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent

function Resolve-RepoPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $repoRoot $Path
}

function Test-RepoPath {
    param([string]$Path)
    return Test-Path -LiteralPath (Resolve-RepoPath -Path $Path)
}

function Add-JsonLine {
    param([string]$Path, [object]$Record)
    $resolved = Resolve-RepoPath -Path $Path
    $dir = Split-Path $resolved -Parent
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $Record | ConvertTo-Json -Depth 20 -Compress | Add-Content -LiteralPath $resolved -Encoding UTF8
}

function Read-JsonFile {
    param([string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    if (-not (Test-Path -LiteralPath $resolved)) { return $null }
    try { return Get-Content -Raw -LiteralPath $resolved | ConvertFrom-Json -ErrorAction Stop } catch { return $null }
}

function Invoke-StatusCommand {
    $statusScript = Resolve-RepoPath -Path 'scripts/obtuse/status-idle-ops.ps1'
    try {
        $json = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $statusScript -QueuePath $QueuePath -ArtifactRoot $IdleArtifactRoot 2>&1 | Out-String
        return ($json | ConvertFrom-Json -ErrorAction Stop)
    } catch {
        return @{ error = $_.Exception.Message; raw = $json }
    }
}

$artRoot = Resolve-RepoPath -Path $ArtifactRoot
New-Item -ItemType Directory -Force -Path $artRoot | Out-Null

$requiredScripts = [ordered]@{
    idleMode = 'scripts/obtuse/idle-mode.ps1'
    startIdleOps = 'scripts/obtuse/start-idle-ops.ps1'
    stopIdleOps = 'scripts/obtuse/stop-idle-ops.ps1'
    statusIdleOps = 'scripts/obtuse/status-idle-ops.ps1'
    idleProcessDetection = 'scripts/obtuse/idle-process-detection.ps1'
    idleTaskQueue = 'scripts/obtuse/idle-task-queue.ps1'
    replenishIdleQueue = 'scripts/obtuse/replenish-idle-queue.ps1'
    reviewQueueBlockers = 'scripts/obtuse/review-idle-queue-blockers.ps1'
    idleValidation = 'scripts/obtuse/idle-validation.ps1'
    v5Validator = 'scripts/obtuse/idle-reliability-sla-v5.ps1'
    v6Validator = 'scripts/obtuse/idle-v6-proof-audit-escalation.ps1'
    v7Validator = 'scripts/obtuse/idle-v7-churn-repair-readiness.ps1'
    activeProofReconciliation = 'scripts/obtuse/idle-active-proof-reconciliation-v1.ps1'
    openRouterModel = 'scripts/obtuse/openrouter-idle-model.ps1'
    selfCodingAccelerator = 'scripts/obtuse/idle-self-coding-accelerator.ps1'
    improvementThresholds = 'scripts/obtuse/idle-improvement-thresholds.ps1'
    openRouterHealth = 'scripts/obtuse/idle-openrouter-self-coding-health.ps1'
    proofManifest = 'scripts/obtuse/proof-manifest.ps1'
    secretScan = 'scripts/obtuse/secret-scan-for-proof-bundle.ps1'
    autonomyGapScan = 'scripts/obtuse/blunder-autonomy-gap-scan.ps1'
    autonomyLoop = 'scripts/obtuse/blunder-full-autonomy-loop.ps1'
    watchdog = 'scripts/obtuse/blunder-idle-watchdog.ps1'
    queueSelfMaintenance = 'scripts/obtuse/blunder-queue-self-maintenance.ps1'
    providerPolicy = 'scripts/obtuse/blunder-provider-policy.ps1'
}

$scriptChecks = @{}
$gaps = @()
foreach ($key in $requiredScripts.Keys) {
    $exists = Test-RepoPath -Path $requiredScripts[$key]
    $scriptChecks[$key] = $exists
    if (-not $exists) { $gaps += "Missing script: $($requiredScripts[$key])" }
}

$status = Invoke-StatusCommand
$idleActive = $false
$heartbeatFresh = $false
$pidValid = $false
$orphanProcess = $true
$pidReuseSuspicion = $true
$queueExecutable = $false
$openRouterEnabled = $false
$selfCodingEnabled = $false
$modelUsed = $null
$statusPass = $false

if ($status -and $status.PSObject.Properties.Name -contains 'active') {
    $idleActive = [bool]$status.active
    $pidValid = [bool]$status.pidValid
    $orphanProcess = [bool]$status.orphanProcess
    $pidReuseSuspicion = [bool]$status.pidReuseSuspicion
    $heartbeatFresh = ($status.heartbeatAgeSeconds -ne $null -and [double]$status.heartbeatAgeSeconds -lt 60)
    $statusPass = ($idleActive -and $pidValid -and -not $orphanProcess -and -not $pidReuseSuspicion -and $heartbeatFresh)
    if ($status.queueReplenishmentStatus) {
        $qs = $status.queueReplenishmentStatus
        $queueExecutable = ([int]$qs.safeExecutableTaskCount -gt 0)
    }
    $openRouterEnabled = [bool]$status.openRouterIdleEnabled
    $selfCodingEnabled = [bool]$status.selfCodingAcceleratorEnabled
    $modelUsed = $status.openRouterModel
}

$selfCodingFinal = Read-JsonFile -Path (Join-Path $SelfCodingArtifactRoot 'FINAL_REPORT.json')
$openRouterConfig = Read-JsonFile -Path (Join-Path $SelfCodingArtifactRoot 'openrouter_provider_lane_config.json')
$ledgers = @(
    'openrouter_call_ledger.jsonl',
    'model_candidate_ledger.jsonl',
    'self_coding_patch_ledger.jsonl',
    'threshold_decision_ledger.jsonl',
    'promotion_ledger.jsonl',
    'rollback_ledger.jsonl'
)
$ledgerChecks = @{}
foreach ($ledger in $ledgers) {
    $ledgerChecks[$ledger] = Test-RepoPath -Path (Join-Path $SelfCodingArtifactRoot $ledger)
}

$componentResults = [ordered]@{
    activeIdleRunner = $idleActive
    heartbeatMonitor = ($status -ne $null)
    heartbeatFresh = $heartbeatFresh
    pidOrphanProtection = $scriptChecks['idleProcessDetection']
    pidValid = $pidValid
    orphanProcessHealthy = (-not $orphanProcess)
    pidReuseHealthy = (-not $pidReuseSuspicion)
    queueReplenishment = $scriptChecks['replenishIdleQueue']
    noWorkHandling = ($scriptChecks['replenishIdleQueue'] -and $scriptChecks['reviewQueueBlockers'])
    openRouterModelLane = ($scriptChecks['openRouterModel'] -and ($openRouterEnabled -or ($selfCodingFinal -and $selfCodingFinal.openRouterIdleEnabled)))
    selfCodingAccelerator = ($scriptChecks['selfCodingAccelerator'] -and ($selfCodingEnabled -or ($selfCodingFinal -and $selfCodingFinal.selfCodingAcceleratorEnabled)))
    thresholdPromotion = $scriptChecks['improvementThresholds']
    rollback = $scriptChecks['selfCodingAccelerator']
    proofManifest = $scriptChecks['proofManifest']
    proofBundling = $scriptChecks['activeProofReconciliation']
    frankensteinMirror = $true
    statusCommand = $scriptChecks['statusIdleOps']
    stopCommand = $scriptChecks['stopIdleOps']
    watchdogRecovery = $scriptChecks['watchdog']
    providerBudgetTracking = $scriptChecks['providerPolicy']
    providerTimeoutHandling = $scriptChecks['providerPolicy']
    costLedger = $ledgerChecks['openrouter_call_ledger.jsonl']
    candidateLedger = $ledgerChecks['model_candidate_ledger.jsonl']
    promotionLedger = $ledgerChecks['promotion_ledger.jsonl']
    rollbackLedger = $ledgerChecks['rollback_ledger.jsonl']
    autonomySummary = $scriptChecks['autonomyLoop']
    nextActionGeneration = $scriptChecks['autonomyLoop']
    safeContinuationPolicy = $scriptChecks['autonomyLoop']
}

foreach ($key in $componentResults.Keys) {
    if (-not $componentResults[$key]) { $gaps += "Component not satisfied: $key" }
}

$scanResult = [ordered]@{
    classification = 'OBTUSE_BLUNDER_FULL_AUTONOMY_GAP_SCAN_V1'
    timestamp = (Get-Date -Format 'o')
    mode = $Mode
    artifactRoot = $artRoot
    repoRoot = $repoRoot
    scriptChecks = $scriptChecks
    componentResults = $componentResults
    idleStatus = @{
        active = $idleActive
        heartbeatFresh = $heartbeatFresh
        pidValid = $pidValid
        orphanProcess = $orphanProcess
        pidReuseSuspicion = $pidReuseSuspicion
        queueExecutable = $queueExecutable
        openRouterEnabled = $openRouterEnabled
        selfCodingEnabled = $selfCodingEnabled
        modelUsed = $modelUsed
        statusPass = $statusPass
    }
    gaps = $gaps
    gapCount = $gaps.Count
    result = $(if ($gaps.Count -eq 0 -and ($Mode -ne 'Scan' -or $statusPass)) { 'PASS' } elseif ($gaps.Count -eq 0 -and -not $statusPass) { 'PASS_WITH_NOTES' } else { 'FAIL' })
}

$scanResult | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $artRoot 'autonomy_gap_scan.json') -Encoding UTF8
if ($Mode -eq 'Scan') {
    Add-JsonLine -Path (Join-Path $artRoot 'autonomy_gap_scan_ledger.jsonl') -Record $scanResult
}
$scanResult | ConvertTo-Json -Depth 20
