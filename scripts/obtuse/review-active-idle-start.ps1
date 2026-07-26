#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Validate','ReportOnly')]
    [string]$Mode = 'Validate',
    [string]$QueuePath = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle/idle-task-queue.json',
    [string]$ArtifactRoot = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle',
    [string]$FinishArtifactRoot = 'artifacts/obtuse/idle-finish-and-active-run-loop-v1',
    [switch]$OperatorApproved
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent

function Resolve-LocalPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $repoRoot $Path
}

function Read-Json {
    param([string]$Path)
    $resolved = Resolve-LocalPath -Path $Path
    if (-not (Test-Path $resolved)) { return $null }
    try { return Get-Content $resolved -Raw | ConvertFrom-Json -ErrorAction Stop } catch { return $null }
}

$now = Get-Date -Format 'o'

$v5Root = $ArtifactRoot
$v6Root = 'artifacts/obtuse/idle-v6-60min-proof-audit-and-120min-escalation-readiness'
$v7Root = 'artifacts/obtuse/idle-v7-churn-repair-and-120min-readiness-recheck'

$v5Final = Read-Json (Join-Path $v5Root 'FINAL_REPORT.json')
$v5Readiness = Read-Json (Join-Path $v5Root 'v5_readiness_state.json')
$v5Validation = Read-Json (Join-Path $v5Root 'validation_summary.json')
$v6Final = Read-Json (Join-Path $v6Root 'FINAL_REPORT.json')
$v7Final = Read-Json (Join-Path $v7Root 'FINAL_REPORT.json')
$v7Validate = Read-Json (Join-Path $v7Root 'validation_summary.json')

$next007Final = Read-Json (Join-Path (Join-Path (Join-Path $FinishArtifactRoot '..') 'idle-next-007-queue-empty-replenishment-status-semantics-v1') 'FINAL_REPORT.json')
$proofManifest = Read-Json (Join-Path $FinishArtifactRoot 'proof_manifest_summary.json')
$blockerReview = Read-Json (Join-Path $ArtifactRoot 'queue_blocker_review.json')
$replenishmentSummary = Read-Json (Join-Path $ArtifactRoot 'queue_replenishment_command_summary.json')

$status = $null
try {
    $status = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'status-idle-ops.ps1') -QueuePath $QueuePath -ArtifactRoot $ArtifactRoot | ConvertFrom-Json
} catch {}

$processDetectionModule = Join-Path $PSScriptRoot 'idle-process-detection.ps1'
. $processDetectionModule
$forbidden = Get-ForbiddenProcessSummary

$v5Pass = ($v5Final -and $v5Final.status -eq 'PASS') -or ($v5Validation -and $v5Validation.pass -eq $true)
$v6PassOrNotes = ($v6Final -and $v6Final.status -in @('PASS','PASS_WITH_NOTES'))
$v7Pass = ($v7Final -and $v7Final.status -in @('PASS','PASS_WITH_NOTES')) -or ($v7Validate -and $v7Validate.pass -eq $true)
$next007Pass = ($next007Final -and $next007Final.result -eq 'PASS')
$proofManifestPass = ($proofManifest -and $proofManifest.result -eq 'PASS')
$blockerReviewPass = ($blockerReview -and $blockerReview.result -eq 'PASS')
$replenishmentPass = ($replenishmentSummary -and $replenishmentSummary.result -in @('PASS','NO_OP'))

$queueReady = $false
$hasExecutableWork = $false
if ($status -and $status.queueStats) {
    $pending = [int]$status.queueStats.pending
    $running = [int]$status.queueStats.running
    $hasExecutableWork = (($pending + $running) -gt 0)
    $queueState = if ($status.queueReplenishmentStatus) { [string]$status.queueReplenishmentStatus.queueHealthState } else { '' }
    $queueReady = ($hasExecutableWork -and ($queueState -in @('QUEUE_NONEMPTY_READY','QUEUE_NONEMPTY_RUNNABLE','NO_SAFE_EXECUTABLE_WORK')))
}

$orphanFalse = ($status -and $status.orphanProcess -eq $false)
$pidReuseFalse = ($status -and $status.pidReuseSuspicion -eq $false)
$detectorClean = ($forbidden.terminalBenchProcessCount -eq 0 -and $forbidden.providerProcessCount -eq 0)
$providerLaneClean = (
    $status -and $status.safetyState -and (
        $status.safetyState.providerCallsAllowed -eq $false -or (
            $status.safetyState.providerCallsAllowed -eq $true -and
            $status.safetyState.approvedOpenRouterSelfCodingLane -eq $true -and
            $status.safetyState.approvedOpenRouterSelfCodingLaneAuthorized -eq $true -and
            $status.safetyState.providerCallsAllowedScope -eq 'openrouter_self_coding_cheap_model_only' -and
            $status.safetyState.providerCallsBlockedOutsideApprovedLane -eq $true
        )
    )
)
$safetyClean = ($status -and $status.safetyState -and $status.safetyState.benchmarkRelaunchAllowed -eq $false -and $providerLaneClean -and $status.safetyState.destructiveActionsAllowed -eq $false -and $status.safetyState.secretsPrinted -eq $false)

$noHighOffenders = $true

$decision = 'NO_GO_INSUFFICIENT_PROOF'
$notes = @()
if (-not $v5Pass) { $decision = 'NO_GO_VALIDATION_FAILED'; $notes += 'V5 validation not PASS' }
elseif (-not $v6PassOrNotes) { $decision = 'NO_GO_VALIDATION_FAILED'; $notes += 'V6 validation not PASS/PASS_WITH_NOTES' }
elseif (-not $v7Pass) { $decision = 'NO_GO_VALIDATION_FAILED'; $notes += 'V7 validation not PASS' }
elseif (-not $next007Pass) { $decision = 'NO_GO_VALIDATION_FAILED'; $notes += 'NEXT-007 baseline not PASS' }
elseif (-not $proofManifestPass) { $decision = 'NO_GO_VALIDATION_FAILED'; $notes += 'Proof manifest not PASS' }
elseif (-not $blockerReviewPass) { $decision = 'NO_GO_QUEUE_BLOCKED'; $notes += 'Queue blocker review not PASS' }
elseif (-not $replenishmentPass) { $decision = 'NO_GO_QUEUE_BLOCKED'; $notes += 'Replenishment command not PASS' }
elseif (-not $hasExecutableWork) { $decision = 'NO_GO_REPLENISH_FIRST'; $notes += 'Queue has no safe executable work' }
elseif (-not $orphanFalse -or -not $pidReuseFalse) { $decision = 'NO_GO_SAFETY_REVIEW'; $notes += 'Orphan process or PID reuse suspected' }
elseif (-not $detectorClean) { $decision = 'NO_GO_SAFETY_REVIEW'; $notes += 'Forbidden benchmark/provider process detected' }
elseif (-not $safetyClean) { $decision = 'NO_GO_SAFETY_REVIEW'; $notes += 'Safety state not clean' }
elseif (-not $OperatorApproved) { $decision = 'NO_GO_INSUFFICIENT_PROOF'; $notes += 'Operator approval switch not set' }
elseif (-not $noHighOffenders) { $decision = 'NO_GO_SAFETY_REVIEW'; $notes += 'Unresolved high/critical offenders' }
else {
    $decision = 'GO_FOR_ACTIVE_IDLE_START'
    $notes += 'All active-idle-start gates pass; operator approval present.'
}

$review = [ordered]@{
    classification = 'OBTUSE_BLUNDER_IDLE_ACTIVE_START_REVIEW_V1'
    timestamp = $now
    mode = $Mode
    operatorApproved = [bool]$OperatorApproved
    decision = $decision
    notes = $notes
    gates = [ordered]@{
        v5ValidatePass = $v5Pass
        v6ValidatePassOrNotes = $v6PassOrNotes
        v7ValidatePass = $v7Pass
        next007BaselinePass = $next007Pass
        proofManifestPass = $proofManifestPass
        queueBlockerReviewPass = $blockerReviewPass
        replenishmentCommandPass = $replenishmentPass
        queueHasExecutableWork = $hasExecutableWork
        queueReadyState = $queueReady
        orphanProcessFalse = $orphanFalse
        pidReuseSuspicionFalse = $pidReuseFalse
        processDetectorClean = $detectorClean
        safetyStateClean = $safetyClean
        providerCallsBlockedOrScopedToApprovedLane = $providerLaneClean
        operatorApproved = [bool]$OperatorApproved
        noUnresolvedHighOffenders = $noHighOffenders
    }
    evidence = [ordered]@{
        v5Status = $(if ($v5Final) { $v5Final.status } else { $null })
        v6Status = $(if ($v6Final) { $v6Final.status } else { $null })
        v7Status = $(if ($v7Final) { $v7Final.status } else { $null })
        next007Status = $(if ($next007Final) { $next007Final.result } else { $null })
        proofManifestResult = $(if ($proofManifest) { $proofManifest.result } else { $null })
        blockerClassification = $(if ($blockerReview) { $blockerReview.blockerClassification } else { $null })
        replenishmentDecision = $(if ($replenishmentSummary) { $replenishmentSummary.decision } else { $null })
        queueHealthState = $(if ($status -and $status.queueReplenishmentStatus) { $status.queueReplenishmentStatus.queueHealthState } else { $null })
        queuePending = $(if ($status -and $status.queueStats) { [int]$status.queueStats.pending } else { $null })
        queueRunning = $(if ($status -and $status.queueStats) { [int]$status.queueStats.running } else { $null })
    }
}

$outPath = Join-Path (Resolve-LocalPath -Path $FinishArtifactRoot) 'active_idle_start_review.json'
$review | ConvertTo-Json -Depth 10 | Set-Content -Path $outPath -Force
$review | ConvertTo-Json -Depth 10
