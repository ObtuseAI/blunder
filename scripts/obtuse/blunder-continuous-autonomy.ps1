#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Validate','Start','RunOnce','RunForever','Status','Stop','StopIfUnsafe','ReportOnly')]
    [string]$Mode = 'Status',
    [string]$OperatorApproved = 'false',
    [string]$AllowReliabilityBudgetOverride = 'false',
    [int]$LoopDelaySeconds = 30
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'blunder-full-autonomy-common.ps1')
. (Join-Path $PSScriptRoot 'blunder-operator-authorization.ps1')

$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$operatorAuthorization = Get-BlunderOperatorAuthorization -RepoRoot $repoRoot
$operatorApprovedBool = (ConvertTo-BlunderBoolean -Value $OperatorApproved) -or (Test-BlunderAuthorizationScope -Authorization $operatorAuthorization -Scope 'continuous_idle_self_improvement')
$allowReliabilityBudgetOverrideBool = (ConvertTo-BlunderBoolean -Value $AllowReliabilityBudgetOverride) -or [bool]$operatorAuthorization.allowReliabilityBudgetOverride
$runtimePath = Join-Path $script:BlunderFullAutonomyArtifactRoot 'continuous_autonomy_runtime.json'
$stopPath = Join-Path $script:BlunderFullAutonomyArtifactRoot 'continuous_autonomy_stop.json'

function Get-AutonomyRuntimeProcessState {
    param([object]$Runtime)
    $runtimePid = 0
    if ($Runtime -and $Runtime.pid) { $runtimePid = [int]$Runtime.pid }
    $process = $null
    if ($runtimePid -gt 0) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $runtimePid" -ErrorAction SilentlyContinue
    }
    $commandLine = $(if ($process) { [string]$process.CommandLine } else { '' })
    $runtimeKind = $(if ($Runtime -and $Runtime.runtimeKind) { [string]$Runtime.runtimeKind } else { 'RUN_FOREVER' })
    $identityMatch = if ($runtimeKind -eq 'IDLE_RUNTIME_BOUND') {
        ($process -and $commandLine -match '(?i)idle-mode\.ps1' -and $commandLine -match '(?i)(-Action\s+Start|Start)')
    } else {
        ($process -and $commandLine -match '(?i)blunder-continuous-autonomy\.ps1' -and $commandLine -match '(?i)(-Mode\s+RunForever|RunForever)')
    }
    return [ordered]@{
        pid = $runtimePid
        runtimeKind = $runtimeKind
        processExists = ($null -ne $process)
        identityMatch = [bool]$identityMatch
        active = [bool]($process -and $identityMatch)
    }
}

function Get-RunForeverReadiness {
    param([object]$IdleSnapshot)
    $idle = $IdleSnapshot.parsed
    $eligible = ($idle -and $idle.continuousRunEligibility -eq $true)
    $reliabilityGreen = ($idle -and [string]$idle.reliabilityBudgetStatus -eq 'WITHIN_BUDGET')
    $v7Ready = ($idle -and $idle.v7ChurnRepairStatus -in @('PASS','PASS_WITH_NOTES') -and $idle.v7GenerationBudgetStatus -eq 'WITHIN_BUDGET' -and $idle.v7Readiness120Decision -like 'GO*')
    $v6Ready = ($idle -and $idle.v6ProofAuditStatus -in @('PASS','PASS_WITH_NOTES') -and $idle.v6ProofBudgetStatus -eq 'WITHIN_BUDGET' -and $idle.v6ReliabilityBudgetStatus -eq 'WITHIN_BUDGET')
    $safetyClean = ($idle -and $idle.orphanProcess -ne $true -and $idle.pidReuseSuspicion -ne $true)
    $overrideReady = ($operatorApprovedBool -and $allowReliabilityBudgetOverrideBool -and ($reliabilityGreen -or $v7Ready -or $v6Ready))
    $ready = ($IdleSnapshot.readable -eq $true -and $safetyClean -and ($eligible -or $overrideReady))
    return [ordered]@{
        ready = [bool]$ready
        idleStatusReadable = [bool]$IdleSnapshot.readable
        continuousRunEligibility = $(if ($idle) { $idle.continuousRunEligibility } else { $false })
        reliabilityBudgetStatus = $(if ($idle) { $idle.reliabilityBudgetStatus } else { 'UNKNOWN' })
        v7Ready = [bool]$v7Ready
        v6Ready = [bool]$v6Ready
        overrideAccepted = [bool]$allowReliabilityBudgetOverrideBool
        standingOperatorApproval = [bool]$operatorApprovedBool
        authorizationPath = $operatorAuthorization.authorizationPath
        reason = $(if ($ready) { 'ready' } else { 'long runtime remains gated by idle status or reliability budget' })
    }
}

if ($Mode -eq 'Validate') {
    $policy = Write-BlunderAutonomyPolicyRecord -Mode 'Validate'
    $queue = Write-BlunderQueueReplenishmentRecord -Mode 'Validate' -AdmitCandidates $false
    $idle = Get-BlunderStatusSnapshot
    $readiness = Get-RunForeverReadiness -IdleSnapshot $idle
    $record = [ordered]@{
        classification = $script:BlunderFullAutonomyClassification
        mode = $Mode
        timestamp = (Get-Date -Format 'o')
        policyResult = $policy.result
        queueResult = $queue.result
        runForeverReady = $readiness.ready
        runForeverReadiness = $readiness
        duplicateStartGuard = $true
        benchmarkLaunchesForbidden = $true
        budgetTokenRequestCostCapsUsedAsBlockers = $false
        result = $(if ($policy.result -eq 'PASS' -and $queue.result -eq 'PASS') { 'PASS' } else { 'FAIL' })
    }
    $record | ConvertTo-Json -Depth 100
    return
}

if ($Mode -eq 'StopIfUnsafe') {
    $safety = Get-BlunderForbiddenProcessState
    if ($safety.result -ne 'PASS') {
        Write-BlunderRuntimeStatus -Status 'STOPPED_UNSAFE' -Reason 'Forbidden process state detected.' | Out-Null
        throw 'Continuous autonomy stopped by hard safety state.'
    }
    $safety | ConvertTo-Json -Depth 100
    return
}

if ($Mode -eq 'Status' -or $Mode -eq 'ReportOnly') {
    $runtime = Read-BlunderJsonFile -Path $runtimePath
    $runtimeProcess = Get-AutonomyRuntimeProcessState -Runtime $runtime
    $idle = Get-BlunderStatusSnapshot
    $readiness = Get-RunForeverReadiness -IdleSnapshot $idle
    $record = [ordered]@{
        classification = $script:BlunderFullAutonomyClassification
        mode = $Mode
        timestamp = (Get-Date -Format 'o')
        runtime = $runtime
        runtimeProcess = $runtimeProcess
        idleStatusReadable = $idle.readable
        idleStatus = $idle.parsed
        runForeverReady = $readiness.ready
        runForeverReadiness = $readiness
        benchmarkLaunched = $false
        result = 'PASS'
    }
    $record | ConvertTo-Json -Depth 100
    return
}

if ($Mode -eq 'Stop') {
    Write-BlunderJsonFile -Value ([ordered]@{ requested = $true; requestedAt = (Get-Date -Format 'o'); requestedByPid = $PID }) -Path $stopPath | Out-Null
    $record = Write-BlunderRuntimeStatus -Status 'STOPPED' -Reason 'Operator stop requested.'
    $record | ConvertTo-Json -Depth 100
    return
}

if ($Mode -in @('Start','RunOnce','RunForever') -and -not $operatorApprovedBool) {
    throw "OperatorApproved true is required for $Mode."
}

if ($Mode -in @('Start','RunForever')) {
    $idle = Get-BlunderStatusSnapshot
    $readiness = Get-RunForeverReadiness -IdleSnapshot $idle
    if (-not $readiness.ready) {
        throw "$Mode refused: $($readiness.reason). continuousRunEligibility=$($readiness.continuousRunEligibility); reliabilityBudgetStatus=$($readiness.reliabilityBudgetStatus); overrideAccepted=$($readiness.overrideAccepted)."
    }
}

if ($Mode -eq 'Start') {
    $existing = Read-BlunderJsonFile -Path $runtimePath
    $existingProcess = Get-AutonomyRuntimeProcessState -Runtime $existing
    if ($existing -and $existing.status -eq 'RUNNING' -and $existingProcess.active) {
        [ordered]@{
            classification = $script:BlunderFullAutonomyClassification
            timestamp = (Get-Date -Format 'o')
            status = 'ALREADY_RUNNING'
            pid = $existingProcess.pid
            processActive = $true
            runtime = $existing
            result = 'PASS'
        } | ConvertTo-Json -Depth 100
        return
    }
    if (Test-Path -LiteralPath (Resolve-BlunderPath -Path $stopPath)) {
        Remove-Item -LiteralPath (Resolve-BlunderPath -Path $stopPath) -Force
    }
    $idle = Get-BlunderStatusSnapshot
    $idleStatus = $idle.parsed
    if (-not $idle.readable -or -not $idleStatus -or $idleStatus.active -ne $true -or $idleStatus.pidValid -ne $true) {
        throw 'Autonomy Start requires a healthy active idle runtime. Start idle operations first.'
    }
    $runtime = [ordered]@{
        classification = $script:BlunderFullAutonomyClassification
        timestamp = (Get-Date -Format 'o')
        status = 'RUNNING'
        pid = [int]$idleStatus.activePid
        runtimeKind = 'IDLE_RUNTIME_BOUND'
        reason = 'Autonomy control plane bound to the healthy idle runtime.'
        artifactRoot = Resolve-BlunderPath -Path $script:BlunderFullAutonomyArtifactRoot
        queuePath = $script:BlunderFullAutonomyQueuePath
        runForeverReady = $readiness.ready
    }
    Write-BlunderJsonFile -Value $runtime -Path $runtimePath | Out-Null
    $runtimeProcess = Get-AutonomyRuntimeProcessState -Runtime $runtime
    if (-not $runtimeProcess.active) {
        throw "Autonomy could not verify the bound idle runtime PID $($runtime.pid)."
    }
    [ordered]@{
        classification = $script:BlunderFullAutonomyClassification
        timestamp = (Get-Date -Format 'o')
        status = 'STARTED'
        pid = $runtime.pid
        processActive = $runtimeProcess.active
        runtime = $runtime
        result = 'PASS'
    } | ConvertTo-Json -Depth 100
    return
}

if ($Mode -eq 'RunOnce') {
    $runtime = Read-BlunderJsonFile -Path $runtimePath
    if (-not $runtime -or $runtime.status -ne 'RUNNING') {
        Write-BlunderRuntimeStatus -Status 'RUNNING' -Reason 'RunOnce started runtime record.' | Out-Null
    }
    $summary = Write-BlunderBootstrapRun -TargetPasses 3
    Write-BlunderRuntimeStatus -Status 'STOPPED' -Reason 'RunOnce completed clean bootstrap passes.' | Out-Null
    $summary | ConvertTo-Json -Depth 100
    return
}

if ($Mode -eq 'RunForever') {
    Write-BlunderRuntimeStatus -Status 'RUNNING' -Reason 'RunForever foreground loop started.' | Out-Null
    try {
        while ($true) {
            if (Test-Path -LiteralPath (Resolve-BlunderPath -Path $stopPath)) {
                Write-BlunderRuntimeStatus -Status 'STOPPED' -Reason 'Stop file observed.' | Out-Null
                break
            }
            Write-BlunderBootstrapRun -TargetPasses 3 | Out-Null
            Write-BlunderRuntimeStatus -Status 'RUNNING' -Reason 'RunForever cycle completed.' | Out-Null
            Start-Sleep -Seconds $LoopDelaySeconds
        }
    }
    catch {
        Write-BlunderRuntimeStatus -Status 'FAILED' -Reason $_.Exception.Message | Out-Null
        throw
    }
    (Read-BlunderJsonFile -Path $runtimePath) | ConvertTo-Json -Depth 100
}
