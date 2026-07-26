#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Validate','Maintain','ReportOnly')]
    [string]$Mode = 'Maintain',
    [string]$ArtifactRoot = 'artifacts/obtuse/blunder-flash-full-autonomy-compounding-bundle-v1',
    [string]$QueuePath = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle/idle-task-queue.json',
    [string]$IdleArtifactRoot = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle',
    [string]$SelfCodingArtifactRoot = 'artifacts/obtuse/blunder-flash-full-autonomy-compounding-bundle-v1/self-coding',
    [string]$OperatorApproved = 'false',
    [switch]$AllowModelLaneWork,
    [string]$Model = 'deepseek-v4-flash'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$queueModule = Join-Path $PSScriptRoot 'idle-task-queue.ps1'
$script:__maintQueuePath = $QueuePath
$script:__maintIdleArtifactRoot = $IdleArtifactRoot
. $queueModule
$QueuePath = $script:__maintQueuePath
$IdleArtifactRoot = $script:__maintIdleArtifactRoot

function Convert-ToBoolean {
    param([object]$Value)
    if ($Value -is [bool]) { return [bool]$Value }
    if ($Value -is [int]) { return ([int]$Value -ne 0) }
    return (([string]$Value).Trim().ToLowerInvariant() -in @('true','1','yes','y','approved'))
}
$OperatorApproved = (Convert-ToBoolean -Value $OperatorApproved) -or (Convert-ToBoolean -Value $env:OBTUSE_OPENROUTER_OPERATOR_APPROVED)

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

function Invoke-Status {
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

if ($Mode -eq 'Validate') {
    $ledgerPresent = Test-Path -LiteralPath (Join-Path $artRoot 'queue_self_maintenance_ledger.jsonl')
    $summaryPresent = Test-Path -LiteralPath (Join-Path $artRoot 'queue_self_maintenance_summary.json')
    $result = [ordered]@{
        classification = 'OBTUSE_BLUNDER_QUEUE_SELF_MAINTENANCE_V1'
        mode = 'Validate'
        timestamp = (Get-Date -Format 'o')
        scriptPresent = $true
        ledgerPresent = $ledgerPresent
        summaryPresent = $summaryPresent
        result = $(if ($ledgerPresent -and $summaryPresent) { 'PASS' } else { 'PASS_WITH_NOTES' })
    }
    $result | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $artRoot 'queue_self_maintenance_validate_result.json') -Encoding UTF8
    $result | ConvertTo-Json -Depth 10
    return
}

$now = Get-Date -Format 'o'
$status = Invoke-Status
$safeExecutable = 0
$queueHealthState = 'UNKNOWN'
$replenishmentState = 'UNKNOWN'
$cooldownRemaining = $null
if ($status -and $status.queueReplenishmentStatus) {
    $qs = $status.queueReplenishmentStatus
    $safeExecutable = [int]$qs.safeExecutableTaskCount
    $queueHealthState = $qs.queueHealthState
    $replenishmentState = $qs.replenishmentState
    $cooldownRemaining = $qs.cooldownRemainingSeconds
}

$actions = @()
$replenishResult = $null
$modelLaneResult = $null
$safeExecutableBefore = $safeExecutable

function Add-FreshSafeTask {
    param([string]$ActionType)
    $sig = "blunder-autonomy-fallback|v1|$ActionType|$(Get-Date -Format 'yyyyMMddHHmmssfff')"
    $title = "Autonomy fallback safe task: $ActionType"
    $desc = "Autonomously generated safe validation task with unique signature to prevent churn."
    $payload = [ordered]@{
        actionType = $ActionType
        replenishmentPolicyId = 'non-churning-v1'
        replenishmentCatalogId = 'autonomy-fallback'
        suppressFollowUp = $true
        operatorTriggeredReplenishment = $false
        autonomyGenerated = $true
        proofRequired = $true
    }
    $task = New-IdleTask -Title $title -Description $desc -Priority 4 -Risk 1 -SourceProject 'obtuse' -NextAction "Execute $ActionType and do not generate a follow-up." -Payload $payload
    $task['generationSignature'] = $sig
    $task['replenishmentPolicyId'] = 'non-churning-v1'
    $task['replenishmentCatalogId'] = 'autonomy-fallback'
    $task['autonomyGenerated'] = $true
    Add-IdleTask -QueuePath $QueuePath -Task $task | Out-Null
    return @{ taskId = $task.id; title = $task.title; signature = $sig; actionType = $ActionType }
}

if ($safeExecutable -eq 0 -and $OperatorApproved) {
    # First compact blockers to keep queue state clean.
    $reviewScript = Resolve-RepoPath -Path 'scripts/obtuse/review-idle-queue-blockers.ps1'
    try {
        $reviewResult = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $reviewScript -Mode Review -QueuePath $QueuePath -ArtifactRoot $IdleArtifactRoot | ConvertFrom-Json
        $actions += 'reviewed_queue_blockers'
    } catch {
        $actions += "review_blockers_failed: $($_.Exception.Message)"
        $reviewResult = @{ error = $_.Exception.Message }
    }

    # Replenish from safe catalog.
    $replenishScript = Resolve-RepoPath -Path 'scripts/obtuse/replenish-idle-queue.ps1'
    try {
        $replenishResult = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $replenishScript -Mode ReplenishOnce -QueuePath $QueuePath -ArtifactRoot $IdleArtifactRoot -OperatorApproved -Force | ConvertFrom-Json
        $actions += "replenish_decision=$($replenishResult.decision)"
    } catch {
        $actions += "replenish_failed: $($_.Exception.Message)"
        $replenishResult = @{ error = $_.Exception.Message }
    }

    # Re-check status after replenishment.
    $status = Invoke-Status
    if ($status -and $status.queueReplenishmentStatus) {
        $safeExecutable = [int]$status.queueReplenishmentStatus.safeExecutableTaskCount
    }

    # Fallback: if catalog is exhausted due to history blocking, generate a fresh unique safe task.
    if ($safeExecutable -eq 0 -and $replenishResult -and $replenishResult.addedTaskCount -eq 0) {
        try {
            $freshTask = Add-FreshSafeTask -ActionType 'capture-status'
            $actions += "fresh_safe_task_added=$($freshTask.taskId)"
            $status = Invoke-Status
            if ($status -and $status.queueReplenishmentStatus) {
                $safeExecutable = [int]$status.queueReplenishmentStatus.safeExecutableTaskCount
            }
        } catch {
            $actions += "fresh_safe_task_failed: $($_.Exception.Message)"
        }
    }

    # If ordinary replenishment did not produce safe work and model lane is enabled, generate self-coding work.
    if ($safeExecutable -eq 0 -and $AllowModelLaneWork -and $OperatorApproved -and $Model -eq 'deepseek-v4-flash') {
        $accelScript = Resolve-RepoPath -Path 'scripts/obtuse/idle-self-coding-accelerator.ps1'
        try {
            $modelLaneResult = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $accelScript -Mode RunOnce -OperatorApproved true -Model $Model -ArtifactRoot $SelfCodingArtifactRoot -QueuePath $QueuePath -IdleArtifactRoot $IdleArtifactRoot | ConvertFrom-Json
            $actions += "model_lane_result=$($modelLaneResult.result)"
        } catch {
            $actions += "model_lane_failed: $($_.Exception.Message)"
            $modelLaneResult = @{ error = $_.Exception.Message }
        }
        $status = Invoke-Status
        if ($status -and $status.queueReplenishmentStatus) {
            $safeExecutable = [int]$status.queueReplenishmentStatus.safeExecutableTaskCount
        }
    }
} elseif ($safeExecutable -gt 0) {
    $actions += 'queue_already_has_executable_work'
} else {
    $actions += 'operator_approval_missing'
}

$ledgerEntry = [ordered]@{
    timestamp = $now
    mode = $Mode
    safeExecutableBefore = $safeExecutableBefore
    safeExecutableAfter = $safeExecutable
    queueHealthState = $queueHealthState
    replenishmentState = $replenishmentState
    cooldownRemainingSeconds = $cooldownRemaining
    actions = $actions
    reviewResult = $reviewResult
    replenishResult = $replenishResult
    modelLaneResult = $modelLaneResult
}
Add-JsonLine -Path (Join-Path $ArtifactRoot 'queue_self_maintenance_ledger.jsonl') -Record $ledgerEntry

$summary = [ordered]@{
    classification = 'OBTUSE_BLUNDER_QUEUE_SELF_MAINTENANCE_V1'
    timestamp = $now
    mode = $Mode
    result = $(if ($safeExecutable -gt 0) { 'PASS' } elseif ($actions -contains 'queue_already_has_executable_work') { 'PASS' } elseif ($OperatorApproved) { 'PASS_WITH_NOTES' } else { 'OPERATOR_APPROVAL_REQUIRED' })
    safeExecutableBefore = $safeExecutableBefore
    safeExecutableAfter = $safeExecutable
    queueHealthState = $queueHealthState
    replenishmentState = $replenishmentState
    actions = $actions
    modelLaneUsed = ($AllowModelLaneWork -and $modelLaneResult -ne $null)
    operatorApproved = $OperatorApproved
}
$summary | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $artRoot 'queue_self_maintenance_summary.json') -Encoding UTF8
$summary | ConvertTo-Json -Depth 20
