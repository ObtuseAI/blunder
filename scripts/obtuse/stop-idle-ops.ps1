#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$QueuePath = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle/idle-task-queue.json',
    [string]$ArtifactRoot = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle',
    [int]$WaitSeconds = 20
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$queueModule = Join-Path $PSScriptRoot 'idle-task-queue.ps1'
$processDetectionModule = Join-Path $PSScriptRoot 'idle-process-detection.ps1'
. $processDetectionModule

function Set-ObjectPropertyValue {
    param([object]$Object, [string]$Name, [object]$Value)
    if ($Object.PSObject.Properties.Name -contains $Name) {
        $Object.$Name = $Value
    } else {
        $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
    }
}

Push-Location $repoRoot
try {
    New-Item -ItemType Directory -Force -Path $ArtifactRoot | Out-Null
    $lockPath = Join-Path $ArtifactRoot 'idle-runner.lock.json'
    $stopPath = Join-Path $ArtifactRoot 'idle-stop.json'
    $statePath = Join-Path $ArtifactRoot 'idle-state.json'
    $heartbeatPath = Join-Path $ArtifactRoot 'idle-heartbeat.json'
    $pidValue = $null
    if (Test-Path $lockPath) {
        try { $pidValue = (Get-Content $lockPath -Raw | ConvertFrom-Json -ErrorAction Stop).pid } catch {}
    }
    $pidActive = $false
    if ($pidValue -and (Test-IdleStartProcessAlive -PidValue $pidValue)) { $pidActive = $true }

    @{
        requested = $true
        acknowledged = (-not $pidActive)
        requestedAt = (Get-Date -Format 'o')
        acknowledgedAt = $(if (-not $pidActive) { Get-Date -Format 'o' } else { $null })
        requestedByPid = $PID
        queuePath = $QueuePath
        artifactRoot = $ArtifactRoot
    } | ConvertTo-Json -Depth 10 | Set-Content -Path $stopPath -Force

    if (Test-Path $statePath) {
        try {
            $state = Get-Content $statePath -Raw | ConvertFrom-Json -ErrorAction Stop
            $state.status = $(if ($pidActive) { 'stopping' } else { 'stopped' })
            $state.stopRequestedAt = (Get-Date -Format 'o')
            $state | ConvertTo-Json -Depth 10 | Set-Content -Path $statePath -Force
        } catch {}
    }

    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    while ($pidValue -and (Test-IdleStartProcessAlive -PidValue $pidValue) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
    }
    $processRemaining = $false
    if ($pidValue -and (Test-IdleStartProcessAlive -PidValue $pidValue)) { $processRemaining = $true }
    if (-not $processRemaining) {
        $now = Get-Date -Format 'o'
        try {
            $stopState = Get-Content $stopPath -Raw | ConvertFrom-Json -ErrorAction Stop
            Set-ObjectPropertyValue -Object $stopState -Name 'acknowledged' -Value $true
            Set-ObjectPropertyValue -Object $stopState -Name 'acknowledgedAt' -Value $now
            $stopState | ConvertTo-Json -Depth 10 | Set-Content -Path $stopPath -Force
        } catch {}
        if (Test-Path $statePath) {
            try {
                $state = Get-Content $statePath -Raw | ConvertFrom-Json -ErrorAction Stop
                Set-ObjectPropertyValue -Object $state -Name 'status' -Value 'stopped'
                Set-ObjectPropertyValue -Object $state -Name 'stoppedAt' -Value $now
                Set-ObjectPropertyValue -Object $state -Name 'stopAcknowledgedAt' -Value $now
                $state | ConvertTo-Json -Depth 10 | Set-Content -Path $statePath -Force
            } catch {}
        }
        if (Test-Path $lockPath) {
            try {
                $lock = Get-Content $lockPath -Raw | ConvertFrom-Json -ErrorAction Stop
                Set-ObjectPropertyValue -Object $lock -Name 'status' -Value 'stopped'
                Set-ObjectPropertyValue -Object $lock -Name 'stoppedAt' -Value $now
                $lock | ConvertTo-Json -Depth 10 | Set-Content -Path $lockPath -Force
            } catch {}
        }
        if (Test-Path $heartbeatPath) {
            try {
                $heartbeat = Get-Content $heartbeatPath -Raw | ConvertFrom-Json -ErrorAction Stop
                if ($heartbeat.state) {
                    Set-ObjectPropertyValue -Object $heartbeat.state -Name 'status' -Value 'stopped'
                }
                Set-ObjectPropertyValue -Object $heartbeat -Name 'timestamp' -Value $now
                Set-ObjectPropertyValue -Object $heartbeat -Name 'stoppedAt' -Value $now
                $heartbeat | ConvertTo-Json -Depth 10 | Set-Content -Path $heartbeatPath -Force
            } catch {}
        }
        if (Test-Path $queueModule) {
            $script:__stopQueuePath = $QueuePath
            $script:__stopArtifactRoot = $ArtifactRoot
            $script:__stopWaitSeconds = $WaitSeconds
            . $queueModule
            $QueuePath = $script:__stopQueuePath
            $ArtifactRoot = $script:__stopArtifactRoot
            $WaitSeconds = $script:__stopWaitSeconds
            $recovery = Repair-RunningIdleTasks -QueuePath $QueuePath
        } else {
            $recovery = @{ recoveredRunningTasks = 0 }
        }
    }
    @{
        stopRequested = $true
        pid = $pidValue
        alreadyStopped = (-not $pidActive)
        processRemaining = $processRemaining
        recoveredRunningTasks = $(if ($recovery) { $recovery.recoveredRunningTasks } else { 0 })
        timestamp = (Get-Date -Format 'o')
    } | Tee-Object -Variable stopDecision | ConvertTo-Json -Depth 10
    $stopLog = Join-Path $ArtifactRoot 'stop_decision_ledger.jsonl'
    $stopDecision | ConvertTo-Json -Compress -Depth 10 | Add-Content -Path $stopLog -Encoding UTF8
}
finally {
    Pop-Location
}
