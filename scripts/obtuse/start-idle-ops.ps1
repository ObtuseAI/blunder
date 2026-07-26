#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$QueuePath = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle/idle-task-queue.json',
    [string]$ArtifactRoot = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle',
    [switch]$SafeRestartAfterReboot,
    [switch]$AutonomyRecovery,
    [string]$OperatorApproved = 'false'
)

$ErrorActionPreference = 'Stop'
function Convert-ToBoolean {
    param([object]$Value)
    if ($Value -is [bool]) { return [bool]$Value }
    if ($Value -is [int]) { return ([int]$Value -ne 0) }
    return (([string]$Value).Trim().ToLowerInvariant() -in @('true','1','yes','y','approved'))
}
$OperatorApproved = (Convert-ToBoolean -Value $OperatorApproved) -or (Convert-ToBoolean -Value $env:OBTUSE_OPENROUTER_OPERATOR_APPROVED)
$AutonomyRecovery = $AutonomyRecovery -or (Convert-ToBoolean -Value $env:OBTUSE_AUTONOMY_RECOVERY)

$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$processDetectionModule = Join-Path $PSScriptRoot 'idle-process-detection.ps1'
$queueModule = Join-Path $PSScriptRoot 'idle-task-queue.ps1'
$operatorAuthorizationModule = Join-Path $PSScriptRoot 'blunder-operator-authorization.ps1'
. $processDetectionModule
. $operatorAuthorizationModule
$operatorAuthorization = Get-BlunderOperatorAuthorization -RepoRoot $repoRoot
$OperatorApproved = $OperatorApproved -or (Test-BlunderAuthorizationScope -Authorization $operatorAuthorization -Scope 'continuous_idle_self_improvement')
$AutonomyRecovery = $AutonomyRecovery -or (Test-BlunderAuthorizationScope -Authorization $operatorAuthorization -Scope 'long_idle_runtime_120min_plus')
$script:__startIdleOpsQueuePath = $QueuePath
$script:__startIdleOpsArtifactRoot = $ArtifactRoot
. $queueModule
$QueuePath = $script:__startIdleOpsQueuePath
$ArtifactRoot = $script:__startIdleOpsArtifactRoot

function Ensure-BlunderInflowMeshIdleTask {
    param([Parameter(Mandatory)] [string]$QueuePath)
    $resolvedQueuePath = Initialize-IdleQueue -Path $QueuePath
    $queue = Get-IdleQueue -Path $resolvedQueuePath
    $existing = @($queue.tasks | Where-Object {
        $_.payload -and
        $_.payload.actionType -eq 'blunder-inflow-mesh' -and
        $_.status -in @('pending','running')
    })
    if ($existing.Count -gt 0) {
        return [ordered]@{
            added = $false
            reason = 'existing_pending_or_running'
            taskId = $existing[0].id
            queuePath = $resolvedQueuePath
        }
    }
    $task = New-IdleTask -Title 'Run Blunder inflow mesh proof pass' `
        -Description 'Idle-owned backend inflow task; local proof pass only.' `
        -Priority 9 -Risk 1 -SourceProject 'obtuse/blunder' `
        -NextAction 'Run local inflow mesh and write idle bridge proof.' `
        -Payload @{
            actionType = 'blunder-inflow-mesh'
            mode = 'IdleAutonomySafeLoop'
            artifactRoot = 'artifacts/blunder/inflow'
            suppressFollowUp = $true
            replenishmentPolicyId = 'idle-embedded-v1'
            replenishmentCatalogId = 'blunder-inflow-mesh-v2'
            sourceMutationAllowed = $false
            providerCallsAllowed = $false
            destructiveActionsAllowed = $false
        }
    $task['generationSignature'] = 'idle-embedded|blunder-inflow-mesh-v2'
    $task['replenishmentPolicyId'] = 'idle-embedded-v1'
    $task['replenishmentCatalogId'] = 'blunder-inflow-mesh-v2'
    Add-IdleTask -QueuePath $resolvedQueuePath -Task $task | Out-Null
    return [ordered]@{
        added = $true
        reason = 'seeded_for_idle_start'
        taskId = $task.id
        queuePath = $resolvedQueuePath
    }
}

Push-Location $repoRoot
try {
    New-Item -ItemType Directory -Force -Path $ArtifactRoot | Out-Null
    $v5Sla = Join-Path $PSScriptRoot 'idle-reliability-sla-v5.ps1'
    $v7Repair = Join-Path $PSScriptRoot 'idle-v7-churn-repair-readiness.ps1'
    $v7Final = Join-Path $repoRoot 'artifacts\obtuse\idle-v7-churn-repair-and-120min-readiness-recheck\FINAL_REPORT.json'
    $v4Bundle = Join-Path $PSScriptRoot 'idle-operator-bundle-v4.ps1'
    $appliance = Join-Path $PSScriptRoot 'idle-appliance-v3.ps1'
    if (Test-Path $v7Repair) {
        $v7State = $null
        try {
            $v7State = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $v7Repair -Mode ReportOnly | ConvertFrom-Json
        }
        catch {
            if ($_.Exception.Message -like 'V7 churn repair readiness*') { throw }
            if (Test-Path $v7Final) {
                throw "V7 churn repair readiness could not be read; start refused. Error: $($_.Exception.Message)"
            }
        }
        if ($v7State -and ($v7State.PSObject.Properties.Name -contains 'available') -and $v7State.available -eq $false) {
            $v7State = $null
        }
        if ($v7State) {
            if ($v7State.status -notin @('PASS','PASS_WITH_NOTES')) {
                throw "V7 churn repair status is $($v7State.status); start refused until V7 validation passes."
            }
            if ($v7State.generationBudgetResult -ne 'WITHIN_BUDGET') {
                throw "V7 generation budget is $($v7State.generationBudgetResult); start refused."
            }
            if ($v7State.churnBudgetStatus -ne 'WITHIN_BUDGET') {
                throw "V7 churn budget is $($v7State.churnBudgetStatus); start refused."
            }
            if ($v7State.readiness120Decision -like 'NO_GO*') {
                throw "V7 churn repair readiness is $($v7State.readiness120Decision); start refused until churn repair validation is GO."
            }
        }
        elseif (Test-Path $v7Final) {
            throw "V7 churn repair readiness is unavailable; start refused."
        }
    }
    if ((Test-Path $v5Sla) -and $ArtifactRoot -like '*v5-supervised*') {
        $readiness = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $v5Sla -Mode Readiness -QueuePath $QueuePath -ArtifactRoot $ArtifactRoot | ConvertFrom-Json
        if (-not $readiness.pass) {
            if ($AutonomyRecovery -and $OperatorApproved) {
                $recoveryNote = "V5 readiness failed but autonomy recovery is operator-approved; continuing with V7/safety checks only."
            } else {
                throw "V5 continuous idle readiness failed; start refused. See $ArtifactRoot\\v5_readiness_state.json"
            }
        }
    }
    elseif ((Test-Path $v4Bundle) -and $ArtifactRoot -like '*v4-one-shot*') {
        $readiness = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $v4Bundle -Mode Readiness -QueuePath $QueuePath -ArtifactRoot $ArtifactRoot | ConvertFrom-Json
        if (-not $readiness.pass) {
            throw "V4 continuous idle readiness failed; start refused. See $ArtifactRoot\\v4_readiness_state.json"
        }
    }
    elseif (Test-Path $appliance) {
        $readiness = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $appliance -Action Readiness -QueuePath $QueuePath -ArtifactRoot $ArtifactRoot | ConvertFrom-Json
        if (-not $readiness.pass) {
            throw "Continuous idle readiness failed; start refused. See $ArtifactRoot\\continuous_readiness_report.json"
        }
    }
    $lockPath = Join-Path $ArtifactRoot 'idle-runner.lock.json'
    if (Test-Path $lockPath) {
        try {
            $lock = Get-Content $lockPath -Raw | ConvertFrom-Json -ErrorAction Stop
            if ($lock.status -eq 'active' -and $lock.pid -and (Test-IdleStartProcessAlive -PidValue $lock.pid)) {
                throw "Idle ops already running with PID $($lock.pid)"
            }
        }
        catch {
            if ($_.Exception.Message -like 'Idle ops already running*') { throw }
        }
    }

    $runner = Join-Path $PSScriptRoot 'idle-mode.ps1'
    $meshTask = Ensure-BlunderInflowMeshIdleTask -QueuePath $QueuePath
    $stdout = Join-Path $ArtifactRoot 'idle-background-stdout.log'
    $stderr = Join-Path $ArtifactRoot 'idle-background-stderr.log'
    "Background runner stdout is intentionally detached; use idle-events.log and idle-cycle-ledger.jsonl for runtime proof." |
        Set-Content -Path $stdout -Force
    "Background runner stderr is intentionally detached; start failures are reported by start-idle-ops.ps1 before launch." |
        Set-Content -Path $stderr -Force
    $args = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', $runner,
        '-Action', 'Start',
        '-QueuePath', $QueuePath,
        '-ArtifactRoot', $ArtifactRoot,
        '-MaxCycles', '0',
        '-LoopDelaySeconds', '2',
        '-ReplenishmentCooldownSeconds', '30',
        '-ReplenishmentBatchSize', '6',
        '-MaxActiveQueueHistory', '100',
        '-RetainedCompletedQueueHistory', '60',
        '-MaxConsecutiveCycleFailures', '3',
        '-TaskTimeoutSeconds', '120'
    )
    $proc = Start-Process -FilePath 'powershell.exe' -ArgumentList $args -WorkingDirectory $repoRoot `
        -WindowStyle Hidden -PassThru
    @{
        started = $true
        pid = $proc.Id
        queuePath = $QueuePath
        artifactRoot = $ArtifactRoot
        inflowMeshTask = $meshTask
        command = "powershell -ExecutionPolicy Bypass -File scripts/obtuse/start-idle-ops.ps1"
        timestamp = (Get-Date -Format 'o')
    } | ConvertTo-Json -Depth 10
}
finally {
    Pop-Location
}
