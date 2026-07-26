# Provider Delta Monitor - State Machine
# Dot-source this script to use New-ProviderDeltaMonitor and related functions.
# No Terminal-Bench tasks are executed by this monitor.

$script:ProviderStates = @{
    NOT_STARTED = "NOT_STARTED"
    PROVIDER_CONTACT_PENDING = "PROVIDER_CONTACT_PENDING"
    PROVIDER_REQUEST_STARTED = "PROVIDER_REQUEST_STARTED"
    PROVIDER_IN_FLIGHT = "PROVIDER_IN_FLIGHT"
    PROVIDER_STREAMING_OR_RESPONDING = "PROVIDER_STREAMING_OR_RESPONDING"
    PROVIDER_RESPONSE_RECEIVED = "PROVIDER_RESPONSE_RECEIVED"
    TASK_EXECUTION_ACTIVE = "TASK_EXECUTION_ACTIVE"
    TASK_POSTPROCESSING_ACTIVE = "TASK_POSTPROCESSING_ACTIVE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TRUE_STALL = "TRUE_STALL"
    UNKNOWN = "UNKNOWN"
}

function New-ProviderDeltaMonitor {
    param(
        [string]$TrialId = "unknown",
        [string]$Provider = "openrouter",
        [string]$ModelRoute = "openrouter/glm-5.2",
        [hashtable]$Timeouts = @{}
    )

    $defaultTimeouts = @{
        preProviderContactTimeoutMinutes = 8
        providerInFlightSoftWarningTimeoutMinutes = 20
        providerInFlightHardTimeoutMinutes = 45
        taskExecutionNoOutputSoftWarningTimeoutMinutes = 15
        taskExecutionHardTimeoutMinutes = 45
        totalTrialHardTimeoutMinutes = 75
        totalRunHardTimeoutHours = 24
        heartbeatStaleTimeoutMinutes = 5
    }

    foreach ($key in $defaultTimeouts.Keys) {
        if (-not $Timeouts.ContainsKey($key)) {
            $Timeouts[$key] = $defaultTimeouts[$key]
        }
    }

    return @{
        trialId = $TrialId
        provider = $Provider
        modelRoute = $ModelRoute
        state = $script:ProviderStates.NOT_STARTED
        requestStart = $null
        firstByteOrFirstDelta = $null
        lastDelta = $null
        responseEnd = $null
        tokensPrompt = $null
        tokensCompletion = $null
        costUsd = $null
        error = $null
        timeouts = $Timeouts
        transitions = @()
        events = @()
    }
}

function Add-ProviderMonitorEvent {
    param($Monitor, [string]$EventType, [hashtable]$Data)
    $entry = @{
        timestamp = (Get-Date -Format "o")
        eventType = $EventType
        data = $Data
    }
    $Monitor.events += $entry
}

function Set-ProviderState {
    param($Monitor, [string]$NewState, [hashtable]$Data)
    $oldState = $Monitor.state
    $Monitor.state = $NewState
    $transition = @{
        at = (Get-Date -Format "o")
        from = $oldState
        to = $NewState
        data = $Data
    }
    $Monitor.transitions += $transition
    Add-ProviderMonitorEvent -Monitor $Monitor -EventType "STATE_TRANSITION" -Data $transition
}

function Register-ProviderContact {
    param($Monitor)
    if ($Monitor.state -eq $script:ProviderStates.NOT_STARTED) {
        Set-ProviderState -Monitor $Monitor -NewState $script:ProviderStates.PROVIDER_CONTACT_PENDING -Data @{ reason = "first contact attempt" }
    }
}

function Register-ProviderRequestStart {
    param($Monitor)
    $Monitor.requestStart = (Get-Date -Format "o")
    Set-ProviderState -Monitor $Monitor -NewState $script:ProviderStates.PROVIDER_REQUEST_STARTED -Data @{}
}

function Register-ProviderFirstByte {
    param($Monitor)
    $Monitor.firstByteOrFirstDelta = (Get-Date -Format "o")
    if ($Monitor.state -in @($script:ProviderStates.PROVIDER_REQUEST_STARTED, $script:ProviderStates.PROVIDER_CONTACT_PENDING)) {
        Set-ProviderState -Monitor $Monitor -NewState $script:ProviderStates.PROVIDER_IN_FLIGHT -Data @{}
    }
}

function Register-ProviderDelta {
    param($Monitor, [hashtable]$Metadata = @{})
    $Monitor.lastDelta = (Get-Date -Format "o")
    if ($Monitor.firstByteOrFirstDelta -eq $null) {
        Register-ProviderFirstByte -Monitor $Monitor
    }
    if ($Monitor.state -ne $script:ProviderStates.PROVIDER_STREAMING_OR_RESPONDING) {
        Set-ProviderState -Monitor $Monitor -NewState $script:ProviderStates.PROVIDER_STREAMING_OR_RESPONDING -Data $Metadata
    }
    if ($Metadata.tokensPrompt) { $Monitor.tokensPrompt = $Metadata.tokensPrompt }
    if ($Metadata.tokensCompletion) { $Monitor.tokensCompletion = $Metadata.tokensCompletion }
    if ($Metadata.costUsd) { $Monitor.costUsd = $Metadata.costUsd }
}

function Register-ProviderResponseEnd {
    param($Monitor, [hashtable]$Metadata = @{})
    $Monitor.responseEnd = (Get-Date -Format "o")
    if ($Metadata.tokensPrompt) { $Monitor.tokensPrompt = $Metadata.tokensPrompt }
    if ($Metadata.tokensCompletion) { $Monitor.tokensCompletion = $Metadata.tokensCompletion }
    if ($Metadata.costUsd) { $Monitor.costUsd = $Metadata.costUsd }
    Set-ProviderState -Monitor $Monitor -NewState $script:ProviderStates.PROVIDER_RESPONSE_RECEIVED -Data $Metadata
}

function Register-TaskExecutionStart {
    param($Monitor)
    Set-ProviderState -Monitor $Monitor -NewState $script:ProviderStates.TASK_EXECUTION_ACTIVE -Data @{}
}

function Register-TaskPostprocessingStart {
    param($Monitor)
    Set-ProviderState -Monitor $Monitor -NewState $script:ProviderStates.TASK_POSTPROCESSING_ACTIVE -Data @{}
}

function Register-ProviderError {
    param($Monitor, [string]$ErrorMessage)
    $Monitor.error = $ErrorMessage
    Set-ProviderState -Monitor $Monitor -NewState $script:ProviderStates.FAILED -Data @{ error = $ErrorMessage }
}

function Register-ProviderTrueStall {
    param($Monitor, [string]$Reason)
    Set-ProviderState -Monitor $Monitor -NewState $script:ProviderStates.TRUE_STALL -Data @{ reason = $Reason }
}

function Get-ElapsedSeconds {
    param([string]$IsoTimestamp)
    if ([string]::IsNullOrEmpty($IsoTimestamp)) { return $null }
    $then = [datetime]::Parse($IsoTimestamp)
    return [math]::Round(((Get-Date) - $then).TotalSeconds, 3)
}

function Test-ProviderMonitorTimeout {
    param($Monitor)
    $now = Get-Date
    $result = @{
        abort = $false
        warning = $null
        reason = $null
        state = $Monitor.state
    }

    $state = $Monitor.state
    $timeouts = $Monitor.timeouts

    switch ($state) {
        $script:ProviderStates.NOT_STARTED {
            # No timeout; monitor not yet active for this trial.
        }
        $script:ProviderStates.PROVIDER_CONTACT_PENDING {
            $elapsed = Get-ElapsedSeconds -IsoTimestamp $Monitor.requestStart
            if ($elapsed -and $elapsed -gt ($timeouts.preProviderContactTimeoutMinutes * 60)) {
                $result.abort = $true
                $result.reason = "pre-provider-contact timeout exceeded ($($timeouts.preProviderContactTimeoutMinutes) min)"
            }
        }
        $script:ProviderStates.PROVIDER_REQUEST_STARTED {
            $elapsed = Get-ElapsedSeconds -IsoTimestamp $Monitor.requestStart
            if ($elapsed -and $elapsed -gt ($timeouts.preProviderContactTimeoutMinutes * 60)) {
                $result.abort = $true
                $result.reason = "pre-provider-contact timeout exceeded ($($timeouts.preProviderContactTimeoutMinutes) min) - first byte not received"
            }
        }
        $script:ProviderStates.PROVIDER_IN_FLIGHT {
            $elapsed = Get-ElapsedSeconds -IsoTimestamp $Monitor.requestStart
            $hard = $timeouts.providerInFlightHardTimeoutMinutes * 60
            $soft = $timeouts.providerInFlightSoftWarningTimeoutMinutes * 60
            if ($elapsed -and $elapsed -gt $hard) {
                $result.abort = $true
                $result.reason = "provider in-flight hard timeout exceeded ($($timeouts.providerInFlightHardTimeoutMinutes) min)"
            }
            elseif ($elapsed -and $elapsed -gt $soft -and -not $Monitor.softWarningFired) {
                $Monitor.softWarningFired = $true
                $result.warning = "provider in-flight soft warning ($($timeouts.providerInFlightSoftWarningTimeoutMinutes) min)"
            }
        }
        $script:ProviderStates.PROVIDER_STREAMING_OR_RESPONDING {
            $elapsed = Get-ElapsedSeconds -IsoTimestamp $Monitor.lastDelta
            $hard = $timeouts.providerInFlightHardTimeoutMinutes * 60
            $soft = $timeouts.providerInFlightSoftWarningTimeoutMinutes * 60
            if ($elapsed -and $elapsed -gt $hard) {
                $result.abort = $true
                $result.reason = "no provider delta within in-flight hard timeout ($($timeouts.providerInFlightHardTimeoutMinutes) min)"
            }
            elseif ($elapsed -and $elapsed -gt $soft -and -not $Monitor.softWarningFired) {
                $Monitor.softWarningFired = $true
                $result.warning = "provider streaming soft warning ($($timeouts.providerInFlightSoftWarningTimeoutMinutes) min)"
            }
        }
        $script:ProviderStates.PROVIDER_RESPONSE_RECEIVED {
            $elapsed = Get-ElapsedSeconds -IsoTimestamp $Monitor.responseEnd
            $hard = $timeouts.taskExecutionHardTimeoutMinutes * 60
            $soft = $timeouts.taskExecutionNoOutputSoftWarningTimeoutMinutes * 60
            if ($elapsed -and $elapsed -gt $hard) {
                $result.abort = $true
                $result.reason = "task execution hard timeout exceeded ($($timeouts.taskExecutionHardTimeoutMinutes) min)"
            }
            elseif ($elapsed -and $elapsed -gt $soft -and -not $Monitor.taskSoftWarningFired) {
                $Monitor.taskSoftWarningFired = $true
                $result.warning = "task execution soft warning ($($timeouts.taskExecutionNoOutputSoftWarningTimeoutMinutes) min)"
            }
        }
        $script:ProviderStates.TASK_EXECUTION_ACTIVE {
            $elapsed = Get-ElapsedSeconds -IsoTimestamp $Monitor.responseEnd
            $hard = $timeouts.taskExecutionHardTimeoutMinutes * 60
            $soft = $timeouts.taskExecutionNoOutputSoftWarningTimeoutMinutes * 60
            if ($elapsed -and $elapsed -gt $hard) {
                $result.abort = $true
                $result.reason = "task execution hard timeout exceeded ($($timeouts.taskExecutionHardTimeoutMinutes) min)"
            }
            elseif ($elapsed -and $elapsed -gt $soft -and -not $Monitor.taskSoftWarningFired) {
                $Monitor.taskSoftWarningFired = $true
                $result.warning = "task execution soft warning ($($timeouts.taskExecutionNoOutputSoftWarningTimeoutMinutes) min)"
            }
        }
        $script:ProviderStates.TASK_POSTPROCESSING_ACTIVE {
            $elapsed = Get-ElapsedSeconds -IsoTimestamp $Monitor.responseEnd
            $hard = $timeouts.taskExecutionHardTimeoutMinutes * 60
            if ($elapsed -and $elapsed -gt $hard) {
                $result.abort = $true
                $result.reason = "task post-processing hard timeout exceeded ($($timeouts.taskExecutionHardTimeoutMinutes) min)"
            }
        }
    }

    # Total trial hard timeout is independent of state.
    $trialElapsed = Get-ElapsedSeconds -IsoTimestamp $Monitor.requestStart
    if ($trialElapsed -and $trialElapsed -gt ($timeouts.totalTrialHardTimeoutMinutes * 60)) {
        $result.abort = $true
        $result.reason = "total trial hard timeout exceeded ($($timeouts.totalTrialHardTimeoutMinutes) min)"
    }

    return $result
}

function Export-ProviderMonitorTelemetry {
    param($Monitor, [string]$Path)
    $telemetry = @{
        trialId = $Monitor.trialId
        provider = $Monitor.provider
        modelRoute = $Monitor.modelRoute
        requestStart = $Monitor.requestStart
        firstByteOrFirstDelta = $Monitor.firstByteOrFirstDelta
        lastDelta = $Monitor.lastDelta
        responseEnd = $Monitor.responseEnd
        state = $Monitor.state
        elapsedSeconds = if ($Monitor.requestStart) { Get-ElapsedSeconds -IsoTimestamp $Monitor.requestStart } else { $null }
        tokensPrompt = $Monitor.tokensPrompt
        tokensCompletion = $Monitor.tokensCompletion
        costUsd = $Monitor.costUsd
        error = $Monitor.error
        transitions = $Monitor.transitions
    }
    $telemetry | ConvertTo-Json -Depth 10 | Set-Content -Path $Path -Force
}
