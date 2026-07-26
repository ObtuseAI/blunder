#requires -Version 5.1
<#
.SYNOPSIS
    Blunder idle-mode runner: persistent, crash-safe, autonomous operation loop.

.DESCRIPTION
    Starts a loop that consumes the idle task queue, simulates each task, executes
    only low-risk operations, validates results, records outcomes, and queues the
    next action. Supports stop command and state persistence. Provider calls are
    denied except for the separately budgeted OpenRouter self-coding lane.
#>
[CmdletBinding()]
param(
    [ValidateSet('Start','Stop','Status','RunOnce')]
    [string]$Action = 'Status',
    [string]$QueuePath = 'artifacts/obtuse/idle-mode-full-operationalization-v1/idle-task-queue.json',
    [string]$ArtifactRoot = 'artifacts/obtuse/idle-mode-full-operationalization-v1',
    [int]$MaxCycles = 0,
    [int]$RiskThreshold = 3,
    [int]$LoopDelaySeconds = 2,
    [int]$ReplenishmentCooldownSeconds = 600,
    [int]$ReplenishmentBatchSize = 1,
    [int]$MaxActiveQueueHistory = 100,
    [int]$RetainedCompletedQueueHistory = 60,
    [int]$MaxConsecutiveCycleFailures = 3,
    [int]$TaskTimeoutSeconds = 120,
    [switch]$DisableReplenishment
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$queueModule = Join-Path $PSScriptRoot 'idle-task-queue.ps1'
$processDetectionModule = Join-Path $PSScriptRoot 'idle-process-detection.ps1'
$validationScript = Join-Path $PSScriptRoot 'idle-validation.ps1'
$adapterScript = Join-Path $PSScriptRoot 'dunce-operations-adapter.ps1'
$intelligenceScript = Join-Path $PSScriptRoot 'idle-intelligence-v2.ps1'

$script:__runnerAction = $Action
$script:__runnerQueuePath = $QueuePath
$script:__runnerArtifactRoot = $ArtifactRoot
. $processDetectionModule
. $queueModule
$Action = $script:__runnerAction
$QueuePath = $script:__runnerQueuePath
$ArtifactRoot = $script:__runnerArtifactRoot

New-Item -ItemType Directory -Force -Path $ArtifactRoot | Out-Null
$statePath = Join-Path $ArtifactRoot 'idle-state.json'
$cycleLogPath = Join-Path $ArtifactRoot 'idle-cycle-log.json'
$lessonsPath = Join-Path $ArtifactRoot 'idle-lessons.json'
$heartbeatPath = Join-Path $ArtifactRoot 'idle-heartbeat.json'
$lockPath = Join-Path $ArtifactRoot 'idle-runner.lock.json'
$stopFilePath = Join-Path $ArtifactRoot 'idle-stop.json'
$idleCycleLedgerPath = Join-Path $ArtifactRoot 'idle-cycle-ledger.jsonl'
$safetyDecisionLedgerPath = Join-Path $ArtifactRoot 'safety-decision-ledger.jsonl'
$replenishmentStatePath = Join-Path $ArtifactRoot 'idle-replenishment-state.json'
$replenishmentLedgerPath = Join-Path $ArtifactRoot 'idle-replenishment-ledger.jsonl'
$queueCompactionArchivePath = Join-Path $ArtifactRoot 'idle-queue-compaction-archive.jsonl'
$queueCompactionSummaryPath = Join-Path $ArtifactRoot 'idle-queue-compaction-summary.json'
$queueCompactedSignaturePath = Join-Path $ArtifactRoot 'idle-queue-compacted-signatures.json'
$openRouterSelfCodingArtifactRoot = 'artifacts/obtuse/blunder-flash-full-autonomy-compounding-bundle-v1/self-coding'
$openRouterSelfCodingCooldownSeconds = 60
$blunderInflowMeshArtifactRoot = 'artifacts/blunder/inflow'

function Get-IdleState {
    if (Test-Path $statePath) {
        try {
            $obj = Get-Content $statePath -Raw | ConvertFrom-Json -ErrorAction Stop
            $ht = @{}
            $obj.PSObject.Properties | ForEach-Object { $ht[$_.Name] = $_.Value }
            return $ht
        }
        catch {
            return @{ status = 'stopped'; pid = $null; startedAt = $null; cyclesRun = 0; lastCycleAt = $null; recoveredBadState = $true }
        }
    }
    return @{ status = 'stopped'; pid = $null; startedAt = $null; cyclesRun = 0; lastCycleAt = $null }
}

function Save-IdleState {
    param([object]$State)
    $State | ConvertTo-Json -Depth 10 | Set-Content -Path $statePath -Force
}

function Write-CycleEvent {
    param([string]$Level, [string]$Message)
    $ts = (Get-Date -Format 'o')
    $line = "$ts [$Level] $Message"
    Write-Host $line
    Add-Content -Path (Join-Path $ArtifactRoot 'idle-events.log') -Value $line -ErrorAction SilentlyContinue
}

function Write-IdleHeartbeat {
    param([hashtable]$State)
    @{
        timestamp = (Get-Date -Format 'o')
        pid = $PID
        action = $Action
        queuePath = $QueuePath
        artifactRoot = $ArtifactRoot
        state = $State
    } | ConvertTo-Json -Depth 10 | Set-Content -Path $heartbeatPath -Force
}

function Add-JsonLine {
    param(
        [Parameter(Mandatory)] [string]$Path,
        [Parameter(Mandatory)] [object]$Record
    )
    $dir = Split-Path $Path -Parent
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $json = $Record | ConvertTo-Json -Depth 20 -Compress
    $lastError = $null
    for ($i = 0; $i -lt 10; $i++) {
        try {
            Add-Content -Path $Path -Value $json -Encoding UTF8
            return
        }
        catch {
            $lastError = $_.Exception
            Start-Sleep -Milliseconds 150
        }
    }
    throw $lastError
}

function Get-TaskTimestamp {
    param([object]$Task)
    foreach ($name in @('updatedAt','createdAt')) {
        if ($Task -and ($Task.PSObject.Properties.Name -contains $name) -and $Task.$name) {
            try { return [datetime]$Task.$name } catch {}
        }
    }
    return [datetime]'1970-01-01T00:00:00Z'
}

function Read-CompactedSignatureState {
    if (-not (Test-Path -LiteralPath $queueCompactedSignaturePath)) {
        return [ordered]@{ version = '1'; signatures = @{}; titles = @{}; updatedAt = $null }
    }
    try {
        $state = Get-Content -Raw -LiteralPath $queueCompactedSignaturePath | ConvertFrom-Json -ErrorAction Stop
        $signatures = @{}
        $titles = @{}
        if ($state.signatures) {
            foreach ($property in @($state.signatures.PSObject.Properties)) { $signatures[$property.Name] = $property.Value }
        }
        if ($state.titles) {
            foreach ($property in @($state.titles.PSObject.Properties)) { $titles[$property.Name] = $property.Value }
        }
        return [ordered]@{ version = '1'; signatures = $signatures; titles = $titles; updatedAt = $state.updatedAt }
    }
    catch {
        return [ordered]@{ version = '1'; signatures = @{}; titles = @{}; updatedAt = $null; recoveredBadState = $true }
    }
}

function Save-CompactedSignatureState {
    param([object]$State)
    $State | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $queueCompactedSignaturePath -Force
}

function Add-CompactedTaskTombstones {
    param([object[]]$Tasks)
    if (@($Tasks).Count -eq 0) { return }
    $state = Read-CompactedSignatureState
    foreach ($task in @($Tasks)) {
        $signature = if ($task.generationSignature) { [string]$task.generationSignature } else { '' }
        $title = if ($task.title) { [string]$task.title } else { '' }
        $status = if ($task.status) { [string]$task.status } else { 'unknown' }
        $record = [ordered]@{
            taskId = [string]$task.id
            title = $title
            status = $status
            archivedAt = (Get-Date -Format 'o')
        }
        if (-not [string]::IsNullOrWhiteSpace($signature)) {
            $state.signatures[$signature] = $record
        }
        if (-not [string]::IsNullOrWhiteSpace($title)) {
            $state.titles[$title] = $record
        }
    }
    $state.updatedAt = Get-Date -Format 'o'
    Save-CompactedSignatureState -State $state
}

function Test-CompactedHistoryBlocksEntry {
    param([object]$Entry, [string]$Signature)
    $state = Read-CompactedSignatureState
    $allowReentry = ($Entry.payload -and ($Entry.payload.allowReentry -eq $true -or [string]$Entry.payload.allowReentry -eq 'true'))
    if (-not $allowReentry -and -not [string]::IsNullOrWhiteSpace($Signature) -and $state.signatures.ContainsKey($Signature)) {
        return $true
    }
    $title = if ($Entry.title) { [string]$Entry.title } else { '' }
    if (-not $allowReentry -and -not [string]::IsNullOrWhiteSpace($title) -and $state.titles.ContainsKey($title)) {
        return $true
    }
    return $false
}

function Get-QueueCompactionSummary {
    if (Test-Path -LiteralPath $queueCompactionSummaryPath) {
        try { return Get-Content -Raw -LiteralPath $queueCompactionSummaryPath | ConvertFrom-Json -ErrorAction Stop } catch {}
    }
    return $null
}

function Invoke-IdleQueueCompaction {
    param([string]$Reason)
    if ($MaxActiveQueueHistory -lt 1 -or $RetainedCompletedQueueHistory -lt 1) {
        return [ordered]@{ compacted = $false; reason = 'disabled_by_limit'; archivedCount = 0 }
    }
    $queue = Get-IdleQueue -Path $QueuePath
    $tasks = @($queue.tasks)
    $terminalEvidence = @($tasks | Where-Object { $_.status -in @('failed','quarantined') })
    $historyOverLimit = ($tasks.Count -gt $MaxActiveQueueHistory)
    if (-not $historyOverLimit -and $terminalEvidence.Count -eq 0) {
        return [ordered]@{ compacted = $false; reason = 'below_limit'; totalBefore = $tasks.Count; archivedCount = 0 }
    }
    $active = @($tasks | Where-Object { $_.status -in @('pending','running','blocked') })
    $completed = @($tasks | Where-Object { $_.status -notin @('pending','running','blocked','failed','quarantined') })
    if ($historyOverLimit -and $completed.Count -le $RetainedCompletedQueueHistory -and $terminalEvidence.Count -eq 0) {
        return [ordered]@{ compacted = $false; reason = 'completed_below_retention'; totalBefore = $tasks.Count; archivedCount = 0 }
    }
    $completedSorted = @($completed | Sort-Object @{ Expression = { Get-TaskTimestamp -Task $_ }; Descending = $true })
    $keepCompleted = $(if ($historyOverLimit) { @($completedSorted | Select-Object -First $RetainedCompletedQueueHistory) } else { $completedSorted })
    $archiveCompleted = @($terminalEvidence)
    if ($historyOverLimit) { $archiveCompleted += @($completedSorted | Select-Object -Skip $RetainedCompletedQueueHistory) }
    if ($archiveCompleted.Count -eq 0) {
        return [ordered]@{ compacted = $false; reason = 'nothing_to_archive'; totalBefore = $tasks.Count; archivedCount = 0 }
    }

    $runId = Get-Date -Format 'yyyyMMddTHHmmss'
    foreach ($task in $archiveCompleted) {
        Add-JsonLine -Path $queueCompactionArchivePath -Record ([ordered]@{
            archivedAt = (Get-Date -Format 'o')
            runId = $runId
            reason = $Reason
            preserved = $true
            task = $task
        })
    }
    Add-CompactedTaskTombstones -Tasks $archiveCompleted
    $queue.tasks = @($active + $keepCompleted)
    Save-IdleQueue -Queue $queue -Path $QueuePath
    $summary = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        runId = $runId
        reason = $Reason
        compacted = $true
        totalBefore = $tasks.Count
        totalAfter = @($queue.tasks).Count
        activeKept = $active.Count
        completedKept = $keepCompleted.Count
        terminalEvidenceArchived = $terminalEvidence.Count
        archivedCount = $archiveCompleted.Count
        archivePath = $queueCompactionArchivePath
        compactedSignaturePath = $queueCompactedSignaturePath
        maxActiveQueueHistory = $MaxActiveQueueHistory
        retainedCompletedQueueHistory = $RetainedCompletedQueueHistory
    }
    $summary | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $queueCompactionSummaryPath -Force
    Add-JsonLine -Path $replenishmentLedgerPath -Record ([ordered]@{
        timestamp = $summary.timestamp
        decision = 'ACTIVE_QUEUE_COMPACTED'
        addedTaskCount = 0
        totalBefore = $summary.totalBefore
        totalAfter = $summary.totalAfter
        archivedCount = $summary.archivedCount
        archivePath = $summary.archivePath
    })
    return $summary
}

function Get-SelectedIdleTask {
    if (Test-Path $intelligenceScript) {
        $selection = & $intelligenceScript -Action SelectNext -QueuePath $QueuePath -ArtifactRoot $ArtifactRoot | ConvertFrom-Json
        if ($selection.selectedTaskId) {
            $queue = Get-IdleQueue -Path $QueuePath
            $selected = $queue.tasks | Where-Object { $_.id -eq $selection.selectedTaskId } | Select-Object -First 1
            if ($selected) { return $selected }
        }
    }
    return Get-NextIdleTask -QueuePath $QueuePath
}

function Invoke-PreExecutionMockTrial {
    param([object]$Task)
    if (-not (Test-Path $intelligenceScript)) {
        throw "Idle intelligence script not found: $intelligenceScript"
    }
    return (& $intelligenceScript -Action MockTrial -QueuePath $QueuePath -ArtifactRoot $ArtifactRoot -TaskId $Task.id | ConvertFrom-Json)
}

function Add-RunnerSafetyDecision {
    param([object]$Task, [object]$Simulation, [object]$Safety, [object]$MockTrial)
    Add-JsonLine -Path $safetyDecisionLedgerPath -Record ([ordered]@{
        timestamp = (Get-Date -Format 'o')
        task_id = $Task.id
        task_title = $Task.title
        simulation_can_proceed = $Simulation.canProceed
        static_safety = $Safety.safe
        mock_trial_safe = $MockTrial.safe_for_idle
        decision = $(if ($Simulation.canProceed -and $Safety.safe -and $MockTrial.safe_for_idle) { 'execute' } else { 'quarantine_or_block' })
        reason = "simulation=$($Simulation.precondition); safety=$($Safety.reason); mock=$($MockTrial.reason)"
    })
}

function Add-DecompositionTask {
    param([object]$Task, [string]$Reason)
    $title = "Decompose or repair blocked idle task $($Task.id.Substring(0, 8))"
    Add-IdleTask -QueuePath $QueuePath -Task (New-IdleTask -Title $title `
        -Description "Mock trial blocked '$($Task.title)'. Reason: $Reason" `
        -Priority 7 -Risk 1 -SourceProject 'obtuse' -NextAction 'Analyze blocked task and propose safe smaller unit.' `
        -Payload @{ actionType = 'improve-idle-loop'; blockedTaskId = $Task.id; blockedReason = $Reason }) | Out-Null
    return $title
}

function Get-LockInfo {
    if (-not (Test-Path $lockPath)) { return $null }
    try { return Get-Content $lockPath -Raw | ConvertFrom-Json -ErrorAction Stop }
    catch { return $null }
}

function Test-ProcessAlive {
    param([object]$PidValue)
    return Test-IdleStartProcessAlive -PidValue $PidValue
}

function Assert-OneIdleRunner {
    $lock = Get-LockInfo
    if ($lock -and $lock.status -eq 'active' -and $lock.pid -and (Test-ProcessAlive -PidValue $lock.pid)) {
        throw "Idle runner already active with PID $($lock.pid)"
    }
    if ($lock) {
        Write-CycleEvent 'WARN' "Recovering stale idle lock from PID $($lock.pid)"
    }
    @{
        pid = $PID
        startedAt = (Get-Date -Format 'o')
        queuePath = $QueuePath
        artifactRoot = $ArtifactRoot
        status = 'active'
    } | ConvertTo-Json -Depth 10 | Set-Content -Path $lockPath -Force
}

function Set-IdleLockStatus {
    param([string]$Status)
    @{
        pid = $PID
        updatedAt = (Get-Date -Format 'o')
        queuePath = $QueuePath
        artifactRoot = $ArtifactRoot
        status = $Status
    } | ConvertTo-Json -Depth 10 | Set-Content -Path $lockPath -Force
}

function Test-StopRequested {
    if (-not (Test-Path $stopFilePath)) { return $false }
    try {
        $stop = Get-Content $stopFilePath -Raw | ConvertFrom-Json -ErrorAction Stop
        return ($stop.requested -eq $true -and $stop.acknowledged -ne $true)
    }
    catch {
        return $true
    }
}

function Clear-StopRequestForStart {
    @{
        requested = $false
        acknowledged = $true
        clearedAt = (Get-Date -Format 'o')
        pid = $PID
    } | ConvertTo-Json -Depth 10 | Set-Content -Path $stopFilePath -Force
}

function Request-IdleStop {
    @{
        requested = $true
        acknowledged = $false
        requestedAt = (Get-Date -Format 'o')
        requestedByPid = $PID
    } | ConvertTo-Json -Depth 10 | Set-Content -Path $stopFilePath -Force
}

function Acknowledge-IdleStop {
    @{
        requested = $true
        acknowledged = $true
        acknowledgedAt = (Get-Date -Format 'o')
        pid = $PID
    } | ConvertTo-Json -Depth 10 | Set-Content -Path $stopFilePath -Force
}

function Add-CycleLog {
    param([object]$Entry)
    $log = [System.Collections.ArrayList]@()
    if (Test-Path $cycleLogPath) {
        try {
            $existing = Get-Content $cycleLogPath -Raw | ConvertFrom-Json
            $existing = @($existing) | Where-Object { $_ }
            $log.AddRange($existing) | Out-Null
        } catch {}
    }
    $log.Add($Entry) | Out-Null
    $log | ConvertTo-Json -Depth 10 | Set-Content -Path $cycleLogPath -Force
}

function Test-SafeToExecute {
    param([object]$Task, [int]$RiskThreshold)
    $forbidden = @('delete','remove','format','wipe','drop','destroy','rm ','rmdir','deltree','git push','git reset','force','-Force','production','secrets','regedit','hklm','hkcu','windows registry')
    $lower = "$($Task.title) $($Task.description) $($Task.nextAction)".ToLower()
    foreach ($f in $forbidden) {
        if ($lower -like "*$f*") { return @{ safe = $false; reason = "Forbidden keyword: $f" } }
    }
    if ($Task.risk -gt $RiskThreshold) { return @{ safe = $false; reason = "Risk $($Task.risk) exceeds threshold $RiskThreshold" } }
    return @{ safe = $true; reason = 'OK' }
}

function Resolve-RepoRelativePath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $repoRoot $Path
}

function Get-PayloadValue {
    param([object]$Payload, [string]$Name, [object]$DefaultValue)
    if ($Payload -is [hashtable] -and $Payload.ContainsKey($Name)) { return $Payload[$Name] }
    if ($Payload -and $Payload.PSObject.Properties.Name -contains $Name) { return $Payload.$Name }
    return $DefaultValue
}

function Invoke-BlunderInflowMesh {
    param([object]$Task)
    $mode = [string](Get-PayloadValue -Payload $Task.payload -Name 'mode' -DefaultValue 'IdleAutonomySafeLoop')
    $artifactRootValue = [string](Get-PayloadValue -Payload $Task.payload -Name 'artifactRoot' -DefaultValue $blunderInflowMeshArtifactRoot)
    $resolvedArtifactRoot = Resolve-RepoRelativePath -Path $artifactRootValue
    $consumerPath = Join-Path $repoRoot 'blunder\inflow\consumer.py'
    if (-not (Test-Path -LiteralPath $consumerPath)) {
        throw "Blunder inflow mesh consumer not found: $consumerPath"
    }
    $raw = & python -m blunder.inflow.consumer --repo-root $repoRoot --artifact-root $resolvedArtifactRoot --mode $mode 2>&1
    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) { $exitCode = 0 }
    $outputText = ($raw | ForEach-Object { [string]$_ }) -join "`n"
    $parsed = $null
    if (-not [string]::IsNullOrWhiteSpace($outputText)) {
        try { $parsed = $outputText | ConvertFrom-Json -ErrorAction Stop } catch {}
    }
    $record = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        taskId = $Task.id
        taskTitle = $Task.title
        actionType = 'blunder-inflow-mesh'
        mode = $mode
        artifactRoot = $resolvedArtifactRoot
        exitCode = $exitCode
        validationPass = $(if ($parsed -and $parsed.validation_summary) { [bool]$parsed.validation_summary.pass } else { $false })
        terminalTarget = $(if ($parsed -and $parsed.validation_summary) { $parsed.validation_summary.terminal_target } else { $null })
        output = $outputText
    }
    Add-JsonLine -Path (Join-Path $ArtifactRoot 'inflow-mesh-bridge-ledger.jsonl') -Record $record
    return [ordered]@{
        success = ($exitCode -eq 0 -and $record.validationPass)
        output = $record
    }
}

function Invoke-TaskSimulation {
    param([object]$Task)
    $at = [string](Get-PayloadValue -Payload $Task.payload -Name 'actionType' -DefaultValue 'unknown')
    switch ($at) {
        'validate-project' {
            $path = [string](Get-PayloadValue -Payload $Task.payload -Name 'projectPath' -DefaultValue '')
            return @{ canProceed = (Test-Path $path); precondition = "Project path exists: $path" }
        }
        'identify-work' {
            $path = [string](Get-PayloadValue -Payload $Task.payload -Name 'projectPath' -DefaultValue '')
            return @{ canProceed = (Test-Path $path); precondition = "Project path exists: $path" }
        }
        'validate-syntax' {
            $path = Join-Path $repoRoot 'scripts'
            return @{ canProceed = (Test-Path $path); precondition = "Scripts path exists: $path" }
        }
        'validate-json' {
            $targetPath = [string](Get-PayloadValue -Payload $Task.payload -Name 'targetPath' -DefaultValue 'artifacts')
            $path = Resolve-RepoRelativePath -Path $targetPath
            return @{ canProceed = (Test-Path $path); precondition = "Artifacts path exists: $path" }
        }
        'extract-lessons' {
            $report = Join-Path $repoRoot 'artifacts/obtuse/provider-stability-switch-v1/FINAL_REPORT.json'
            return @{ canProceed = (Test-Path $report); precondition = "Prior report exists: $report" }
        }
        'monitor-health' {
            return @{ canProceed = $true; precondition = 'Health check is read-only status query' }
        }
        'inspect-artifacts' {
            $path = [string](Get-PayloadValue -Payload $Task.payload -Name 'targetPath' -DefaultValue '')
            return @{ canProceed = (Test-Path $path); precondition = "Artifact/status path exists: $path" }
        }
        'queue-safe-repairs' {
            return @{ canProceed = $true; precondition = 'Queues only low-risk validation tasks' }
        }
        'improve-idle-loop' {
            return @{ canProceed = $true; precondition = 'Recursive improvement reads queue evidence and writes proof ledgers only' }
        }
        'blunder-inflow-mesh' {
            $consumer = Join-Path $repoRoot 'blunder\inflow\consumer.py'
            return @{ canProceed = (Test-Path -LiteralPath $consumer); precondition = "Blunder inflow mesh consumer exists: $consumer" }
        }
        default {
            return @{ canProceed = $false; precondition = 'Unknown task type; blocked by default' }
        }
    }
}

function Invoke-BoundedJsonPowerShell {
    param(
        [Parameter(Mandatory)] [string]$ScriptPath,
        [Parameter(Mandatory)] [string[]]$Arguments,
        [Parameter(Mandatory)] [int]$TimeoutSeconds
    )
    $allArguments = @('-NoProfile','-ExecutionPolicy','Bypass','-File',$ScriptPath) + $Arguments
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = 'powershell.exe'
    $info.WorkingDirectory = $repoRoot
    $info.Arguments = (($allArguments | ForEach-Object { '"' + ([string]$_).Replace('"','\"') + '"' }) -join ' ')
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = [System.Diagnostics.Process]::Start($info)
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    $started = Get-Date
    $timedOut = $false
    while (-not $process.WaitForExit(2000)) {
        $elapsed = [Math]::Round(((Get-Date) - $started).TotalSeconds, 3)
        Write-IdleHeartbeat -State @{ status = 'running'; phase = 'bounded-task'; childPid = $process.Id; elapsedSeconds = $elapsed; timeoutSeconds = $TimeoutSeconds }
        if ($elapsed -ge $TimeoutSeconds) {
            Stop-Process -Id $process.Id -Force
            $process.WaitForExit()
            $timedOut = $true
            break
        }
    }
    $output = $stdout.Result
    $errorOutput = $stderr.Result
    $parsed = $null
    $parseError = $null
    if (-not $timedOut -and $process.ExitCode -eq 0) {
        try { $parsed = $output | ConvertFrom-Json -ErrorAction Stop }
        catch { $parseError = $_.Exception.Message }
    }
    return [ordered]@{
        success = (-not $timedOut -and $process.ExitCode -eq 0 -and $null -ne $parsed)
        exitCode = $(if ($timedOut) { 124 } else { $process.ExitCode })
        timedOut = $timedOut
        parsed = $parsed
        parseError = $parseError
        error = $(if ($timedOut) { "Task exceeded $TimeoutSeconds seconds." } elseif ($process.ExitCode -ne 0) { $errorOutput.Trim() } else { $parseError })
    }
}

function Invoke-TaskExecution {
    param([object]$Task)
    $at = [string](Get-PayloadValue -Payload $Task.payload -Name 'actionType' -DefaultValue 'unknown')
    switch ($at) {
        'validate-project' {
            $outFile = Join-Path $ArtifactRoot "validation-project-$(Get-Date -Format 'yyyyMMddTHHmmss').json"
            $projectPath = [string](Get-PayloadValue -Payload $Task.payload -Name 'projectPath' -DefaultValue '')
            $run = Invoke-BoundedJsonPowerShell -ScriptPath $validationScript -Arguments @('-Action','Composite','-TargetPath',$projectPath,'-OutputPath',$outFile) -TimeoutSeconds $TaskTimeoutSeconds
            return @{ success = ($run.success -and $run.parsed.pass -eq $true); output = $(if ($run.parsed) { $run.parsed } else { $run }) }
        }
        'identify-work' {
            $projectPath = [string](Get-PayloadValue -Payload $Task.payload -Name 'projectPath' -DefaultValue '')
            $items = Get-ChildItem -Path $projectPath -Recurse -File -ErrorAction SilentlyContinue |
                Group-Object -Property Extension -NoElement |
                Select-Object Name, Count |
                ForEach-Object { @{ extension = $_.Name; count = $_.Count } }
            return @{ success = $true; output = @{ projectPath = $projectPath; fileTypes = $items } }
        }
        'validate-syntax' {
            $outFile = Join-Path $ArtifactRoot "validation-syntax-$(Get-Date -Format 'yyyyMMddTHHmmss').json"
            $run = Invoke-BoundedJsonPowerShell -ScriptPath $validationScript -Arguments @('-Action','Syntax','-TargetPath',(Join-Path $repoRoot 'scripts'),'-OutputPath',$outFile) -TimeoutSeconds $TaskTimeoutSeconds
            return @{ success = ($run.success -and $run.parsed.pass -eq $true); output = $(if ($run.parsed) { $run.parsed } else { $run }) }
        }
        'validate-json' {
            $outFile = Join-Path $ArtifactRoot "validation-artifacts-$(Get-Date -Format 'yyyyMMddTHHmmss').json"
            $targetPath = [string](Get-PayloadValue -Payload $Task.payload -Name 'targetPath' -DefaultValue 'artifacts')
            $resolvedTargetPath = Resolve-RepoRelativePath -Path $targetPath
            $run = Invoke-BoundedJsonPowerShell -ScriptPath $validationScript -Arguments @('-Action','Composite','-TargetPath',$resolvedTargetPath,'-OutputPath',$outFile) -TimeoutSeconds $TaskTimeoutSeconds
            return @{ success = ($run.success -and $run.parsed.pass -eq $true); output = $(if ($run.parsed) { $run.parsed } else { $run }) }
        }
        'extract-lessons' {
            $reportPath = Join-Path $repoRoot 'artifacts/obtuse/provider-stability-switch-v1/FINAL_REPORT.json'
            $lesson = if (Test-Path $reportPath) {
                $report = Get-Content $reportPath -Raw | ConvertFrom-Json
                "Lesson from $($report.classification): route $($report.selectedRoute) stalled after $($report.completedTrials) trials. Action: avoid relaunching until a stable route is found."
            } else {
                'No prior report available.'
            }
            $entry = @{ timestamp = (Get-Date -Format 'o'); lesson = $lesson; sourceReport = $reportPath }
            $lessons = [System.Collections.ArrayList]@()
            if (Test-Path $lessonsPath) {
                try {
                    $existing = Get-Content $lessonsPath -Raw | ConvertFrom-Json
                    $existing = @($existing) | Where-Object { $_ }
                    $lessons.AddRange($existing) | Out-Null
                } catch {}
            }
            $lessons.Add($entry) | Out-Null
            $lessons | ConvertTo-Json -Depth 10 | Set-Content -Path $lessonsPath -Force
            return @{ success = $true; output = $entry }
        }
        'monitor-health' {
            $stats = Get-QueueStats -QueuePath $QueuePath
            $state = Get-IdleState
            return @{ success = $true; output = @{ queueStats = $stats; state = $state } }
        }
        'inspect-artifacts' {
            $target = [string](Get-PayloadValue -Payload $Task.payload -Name 'targetPath' -DefaultValue '')
            $files = Get-ChildItem -Path $target -File -Recurse -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime -Descending |
                Select-Object -First 25 FullName, Length, LastWriteTime
            return @{ success = $true; output = @{ targetPath = $target; recentFiles = $files } }
        }
        'queue-safe-repairs' {
            Add-IdleTask -QueuePath $QueuePath -Task (New-IdleTask -Title 'Run Dunce / ShowMeAI safe validation sweep' `
                -Description 'Queued by recursive improvement engine after safe repair classification.' `
                -Priority 8 -Risk 1 -SourceProject 'dunce/showmeai' -NextAction 'Validate discovered project and artifact JSON.' `
                -Payload @{ actionType = 'validate-json' }) | Out-Null
            return @{ success = $true; output = @{ queued = 'Run Dunce / ShowMeAI safe validation sweep' } }
        }
        'improve-idle-loop' {
            $out = & $intelligenceScript -Action Improve -QueuePath $QueuePath -ArtifactRoot $ArtifactRoot | ConvertFrom-Json
            return @{ success = $true; output = $out }
        }
        'blunder-inflow-mesh' {
            return Invoke-BlunderInflowMesh -Task $Task
        }
        default {
            return @{ success = $false; output = 'Unknown task type' }
        }
    }
}

function Invoke-TaskValidation {
    param([object]$ExecutionResult)
    if (-not $ExecutionResult.success) { return @{ pass = $false; reason = 'Execution reported failure' } }
    return @{ pass = $true; reason = 'Execution succeeded and produced output' }
}

function Get-NormalizedGeneratedTaskSignature {
    param([string]$Title, [object]$Payload, [string]$SourceProject)
    $actionType = [string](Get-PayloadValue -Payload $Payload -Name 'actionType' -DefaultValue 'unknown')
    $target = [string](Get-PayloadValue -Payload $Payload -Name 'projectPath' -DefaultValue 'obtuse-artifacts')
    $purpose = ($Title -replace '[^A-Za-z0-9]+', ' ').Trim().ToLowerInvariant()
    return "$($actionType.ToLowerInvariant())|$($SourceProject.ToLowerInvariant())|$($target.ToLowerInvariant())|$purpose"
}

function Get-ExistingGeneratedSignatureCount {
    param([string]$Signature)
    $queue = Get-IdleQueue -Path $QueuePath
    $count = 0
    foreach ($existing in @($queue.tasks)) {
        $payload = if ($existing.payload) { $existing.payload } else { @{} }
        $source = if ($existing.sourceProject) { [string]$existing.sourceProject } else { 'obtuse' }
        $existingSignature = $null
        if ($existing.PSObject.Properties.Name -contains 'generationSignature') {
            $existingSignature = [string]$existing.generationSignature
        } else {
            $existingSignature = Get-NormalizedGeneratedTaskSignature -Title ([string]$existing.title) -Payload $payload -SourceProject $source
        }
        if ($existingSignature -eq $Signature) { $count++ }
    }
    return $count
}

function Write-AntiChurnGuardDecision {
    param([object]$Decision)
    $path = Join-Path $ArtifactRoot 'anti_churn_runtime_guard_ledger.jsonl'
    $Decision | ConvertTo-Json -Depth 10 -Compress | Add-Content -Path $path -Encoding UTF8
}

function Get-ReplenishmentState {
    if (Test-Path $replenishmentStatePath) {
        try {
            return Get-Content $replenishmentStatePath -Raw | ConvertFrom-Json -ErrorAction Stop
        }
        catch {
            return @{
                version = '1'
                cursor = 0
                lastReplenishedAt = $null
                policy = 'non-churning-v1'
                cooldownSeconds = $ReplenishmentCooldownSeconds
                recoveredBadState = $true
            }
        }
    }
    return @{
        version = '1'
        cursor = 0
        lastReplenishedAt = $null
        policy = 'non-churning-v1'
        cooldownSeconds = $ReplenishmentCooldownSeconds
    }
}

function Save-ReplenishmentState {
    param([object]$State)
    $State | ConvertTo-Json -Depth 10 | Set-Content -Path $replenishmentStatePath -Force
}

function Set-DynamicProperty {
    param([object]$Object, [string]$Name, [object]$Value)
    if ($Object -is [hashtable]) {
        $Object[$Name] = $Value
        return
    }
    if ($Object.PSObject.Properties.Name -contains $Name) {
        $Object.$Name = $Value
        return
    }
    $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
}

function Get-ReplenishmentCatalog {
    $catalog = New-Object System.Collections.ArrayList
    $inflowCandidatePaths = @(
        (Join-Path $repoRoot 'artifacts/blunder/inflow/external_capability_candidates.jsonl'),
        (Join-Path $repoRoot 'artifacts/blunder/inflow/capability_candidates.jsonl')
    )
    $seenInflowCandidateIds = @{}
    foreach ($inflowCandidatePath in $inflowCandidatePaths) {
    if (Test-Path -LiteralPath $inflowCandidatePath) {
        foreach ($line in Get-Content -LiteralPath $inflowCandidatePath) {
            if ([string]::IsNullOrWhiteSpace($line)) { continue }
            try {
                $candidate = $line | ConvertFrom-Json -ErrorAction Stop
                $candidateId = if ($candidate.candidate_id) { [string]$candidate.candidate_id } elseif ($candidate.evidence_record_id) { [string]$candidate.evidence_record_id } else { ([string]$candidate.title -replace '[^A-Za-z0-9]+','-').Trim('-').ToLowerInvariant() }
                if ($seenInflowCandidateIds.ContainsKey($candidateId)) { continue }
                $seenInflowCandidateIds[$candidateId] = $true
                $targetType = if ($candidate.target_type) { [string]$candidate.target_type } else { 'InflowCapability' }
                $title = if ($candidate.title) { [string]$candidate.title } else { "Replay inflow capability $candidateId" }
                $sourceProject = if ($candidate.source_project) { [string]$candidate.source_project } else { 'obtuse/blunder-inflow' }
                $priority = 8
                $candidateEntry = @{
                    id = "inflow-capability-$candidateId"
                    title = "Replay inflow capability: $title"
                    description = "Cooldown-gated inflow capability replay candidate. Target type: $targetType"
                    priority = $priority
                    risk = 1
                    sourceProject = $sourceProject
                    nextAction = 'Inspect inflow proof artifacts and record replayable capability evidence.'
                    payload = @{
                        actionType = 'inspect-artifacts'
                        targetPath = 'artifacts/blunder/inflow'
                        controlLane = 'inflow-capability-replay'
                        candidateId = $candidateId
                        targetType = $targetType
                        sourceProject = $sourceProject
                        sourcePath = $(if ($candidate.source_path) { [string]$candidate.source_path } else { '' })
                        capabilityType = $(if ($candidate.capability_type) { [string]$candidate.capability_type } else { '' })
                        whyItMatters = $(if ($candidate.why_it_matters) { [string]$candidate.why_it_matters } else { '' })
                        sourceBundleId = $(if ($candidate.source_bundle_id) { [string]$candidate.source_bundle_id } else { '' })
                        replenishmentPolicyId = 'inflow-capability-v1'
                        replenishmentCatalogId = "inflow-capability-$candidateId"
                        allowReentry = $true
                        suppressFollowUp = $true
                        sourceMutationAllowed = $false
                        providerCallsAllowed = $false
                        destructiveActionsAllowed = $false
                    }
                }
                [void]$catalog.Add($candidateEntry)
            } catch {
                Add-JsonLine -Path $replenishmentLedgerPath -Record ([ordered]@{
                    timestamp = (Get-Date -Format 'o')
                    decision = 'INFLOW_CANDIDATE_READ_FAILED'
                    addedTaskCount = 0
                    reason = $_.Exception.Message
                })
            }
        }
    }
    }
    $dunceCandidatePaths = @(
        (Join-Path $repoRoot 'artifacts/obtuse/blunder-dunce-hypothesis-promotion-check-v1/002_ARTIFACT_ONLY_QUEUE_CANDIDATES.json'),
        (Join-Path $repoRoot 'artifacts/obtuse/blunder-dunce-utility-scoring-v1/002_NON_DUPLICATE_QUEUE_CANDIDATES.json'),
        (Join-Path $repoRoot 'artifacts/obtuse/blunder-dunce-regression-substrate-idle-cycle-v1/001_SUBSTRATE_QUEUE_CANDIDATES.json')
    )
    foreach ($dunceCandidatePath in $dunceCandidatePaths) {
    if (Test-Path -LiteralPath $dunceCandidatePath) {
        try {
            $candidateDoc = Get-Content -Raw -LiteralPath $dunceCandidatePath | ConvertFrom-Json -ErrorAction Stop
            foreach ($candidate in @($candidateDoc.queueCandidates)) {
                if ($candidate.sourceMutationAllowed -or $candidate.providerCallsAllowed -or $candidate.destructiveActionsAllowed) { continue }
                $candidateKey = if ($candidate.taskId) { [string]$candidate.taskId } elseif ($candidate.candidateId) { [string]$candidate.candidateId } else { ([string]$candidate.title -replace '[^A-Za-z0-9]+','-').Trim('-').ToLowerInvariant() }
                $policyId = if ($candidateDoc.sourceHypothesis -eq 'DUNCE-HYP-005' -or $candidate.generationSignature -like 'dunce-utility-v1*') { 'dunce-utility-v1' } else { 'dunce-artifact-v1' }
                $targetPath = if ($candidate.targetPath) { [string]$candidate.targetPath } else { Split-Path $dunceCandidatePath -Parent }
                [void]$catalog.Add(@{
                    id = "$policyId-$candidateKey"
                    title = [string]$candidate.title
                    description = "Cooldown-gated Dunce artifact-only candidate. Test: $($candidate.measurableTest)"
                    priority = 8
                    risk = 1
                    sourceProject = 'dunce/showmeai'
                    nextAction = [string]$candidate.successMetric
                    payload = @{
                        actionType = 'inspect-artifacts'
                        targetPath = $targetPath
                        controlLane = [string]$candidate.controlLane
                        replenishmentPolicyId = $policyId
                        replenishmentCatalogId = "$policyId-$candidateKey"
                        suppressFollowUp = $true
                        sourceMutationAllowed = $false
                        providerCallsAllowed = $false
                        destructiveActionsAllowed = $false
                        stopCondition = [string]$candidate.stopCondition
                    }
                })
            }
        } catch {
            Add-JsonLine -Path $replenishmentLedgerPath -Record ([ordered]@{
                timestamp = (Get-Date -Format 'o')
                decision = 'DUNCE_CANDIDATE_CATALOG_READ_FAILED'
                addedTaskCount = 0
                reason = $_.Exception.Message
            })
        }
    }
    }
    foreach ($entry in @(
        @{
            id = 'blunder-inflow-mesh-v2'
            title = 'Run Blunder inflow mesh proof pass'
            description = 'Cooldown-gated safe task: run backend inflow mesh through idle proof gates.'
            priority = 9
            risk = 1
            sourceProject = 'obtuse/blunder'
            nextAction = 'Run local inflow mesh and write idle bridge proof.'
            payload = @{
                actionType = 'blunder-inflow-mesh'
                mode = 'IdleAutonomySafeLoop'
                artifactRoot = $blunderInflowMeshArtifactRoot
                replenishmentPolicyId = 'non-churning-v1'
                replenishmentCatalogId = 'blunder-inflow-mesh-v2'
                allowReentry = $true
                suppressFollowUp = $true
                sourceMutationAllowed = $false
                providerCallsAllowed = $false
                destructiveActionsAllowed = $false
            }
        },
        @{
            id = 'idle-control-syntax'
            title = 'Replenishment validate idle control syntax'
            description = 'Cooldown-gated safe task: validate local idle-control PowerShell syntax.'
            priority = 6
            risk = 1
            sourceProject = 'obtuse'
            nextAction = 'Validate idle-control syntax and do not generate a follow-up.'
            payload = @{
                actionType = 'validate-syntax'
                replenishmentPolicyId = 'non-churning-v1'
                replenishmentCatalogId = 'idle-control-syntax'
                suppressFollowUp = $true
            }
        },
        @{
            id = 'idle-artifact-json'
            title = 'Replenishment validate idle artifact JSON health'
            description = 'Cooldown-gated safe task: validate local Obtuse artifact JSON parseability.'
            priority = 5
            risk = 1
            sourceProject = 'obtuse'
            nextAction = 'Validate artifact JSON and do not generate a follow-up.'
            payload = @{
                actionType = 'validate-json'
                replenishmentPolicyId = 'non-churning-v1'
                replenishmentCatalogId = 'idle-artifact-json'
                suppressFollowUp = $true
            }
        },
        @{
            id = 'idle-queue-health'
            title = 'Replenishment idle queue health snapshot'
            description = 'Cooldown-gated safe task: capture queue and idle-state health without spawning recursive work.'
            priority = 4
            risk = 1
            sourceProject = 'obtuse'
            nextAction = 'Capture queue health and do not generate a follow-up.'
            payload = @{
                actionType = 'monitor-health'
                replenishmentPolicyId = 'non-churning-v1'
                replenishmentCatalogId = 'idle-queue-health'
                suppressFollowUp = $true
            }
        }
    )) {
        [void]$catalog.Add($entry)
    }
    return @($catalog)
}

function Get-ReplenishmentEpoch {
    return [int64]([math]::Floor([DateTimeOffset]::Now.ToUnixTimeSeconds() / [math]::Max(60, $ReplenishmentCooldownSeconds)))
}

function Get-ReplenishmentSignature {
    param([object]$Entry)
    $policy = if ($Entry.payload -and $Entry.payload.replenishmentPolicyId) { [string]$Entry.payload.replenishmentPolicyId } else { 'non-churning-v1' }
    $base = "replenishment|$policy|$($Entry.id)"
    if ($Entry.payload -and ($Entry.payload.allowReentry -eq $true -or [string]$Entry.payload.allowReentry -eq 'true')) {
        return "$base|epoch=$(Get-ReplenishmentEpoch)"
    }
    return $base
}

function Test-ReplenishmentSignatureBlockedByHistory {
    param([string]$Signature)
    $queue = Get-IdleQueue -Path $QueuePath
    foreach ($task in @($queue.tasks)) {
        $existingSig = if ($task.generationSignature) { [string]$task.generationSignature } else { '' }
        if ($existingSig -eq $Signature -and $task.status -in @('disabled','passed','failed','quarantined')) {
            return $true
        }
        if ($existingSig -eq $Signature -and $task.status -in @('pending','running')) {
            return $true
        }
    }
    return $false
}

function Test-ReplenishmentEntryBlockedByHistory {
    param([object]$Entry, [string]$Signature)
    if (Test-CompactedHistoryBlocksEntry -Entry $Entry -Signature $Signature) {
        return $true
    }
    $queue = Get-IdleQueue -Path $QueuePath
    $allowReentry = ($Entry.payload -and ($Entry.payload.allowReentry -eq $true -or [string]$Entry.payload.allowReentry -eq 'true'))
    $entryTitle = [string]$Entry.title
    $matchingQuarantineCount = 0
    foreach ($task in @($queue.tasks)) {
        $existingTitle = if ($task.title) { [string]$task.title } else { '' }
        if ($existingTitle -eq $entryTitle -and $task.status -eq 'quarantined') {
            $matchingQuarantineCount++
        }
    }
    if ($allowReentry -and $matchingQuarantineCount -ge 2) {
        return $true
    }

    foreach ($task in @($queue.tasks)) {
        $existingSig = if ($task.generationSignature) { [string]$task.generationSignature } else { '' }
        $existingTitle = if ($task.title) { [string]$task.title } else { '' }
        if ($existingSig -eq $Signature -and $task.status -in @('disabled','passed','failed','quarantined','pending','running')) {
            return $true
        }
        if ($allowReentry -and $existingTitle -eq [string]$Entry.title -and $task.status -in @('pending','running')) {
            return $true
        }
        if ($allowReentry) { continue }
        if ($existingTitle -eq [string]$Entry.title -and $task.status -in @('passed','failed','quarantined','pending','running')) {
            return $true
        }
    }
    return $false
}

function Invoke-QueueReplenishmentIfDue {
    if ($DisableReplenishment) {
        Add-JsonLine -Path $replenishmentLedgerPath -Record ([ordered]@{
            timestamp = (Get-Date -Format 'o')
            decision = 'SKIP_DISABLED'
            addedTaskCount = 0
            reason = 'Replenishment disabled by runner parameter.'
        })
        return @{ addedTaskCount = 0; reason = 'disabled' }
    }

    $stats = Get-QueueStats -QueuePath $QueuePath
    $pending = [int]$stats.pending
    $running = [int]$stats.running
    if (($pending + $running) -gt 0) {
        Add-JsonLine -Path $replenishmentLedgerPath -Record ([ordered]@{
            timestamp = (Get-Date -Format 'o')
            decision = 'SKIP_QUEUE_HAS_WORK'
            addedTaskCount = 0
            pending = $pending
            running = $running
        })
        return @{ addedTaskCount = 0; reason = 'queue_has_work' }
    }

    $state = Get-ReplenishmentState
    $last = $null
    if ($state.lastReplenishedAt) {
        try { $last = [datetime]$state.lastReplenishedAt } catch { $last = $null }
    }
    $ageSeconds = if ($last) { [math]::Round(((Get-Date) - $last).TotalSeconds, 3) } else { $null }
    if ($last -and $ageSeconds -lt $ReplenishmentCooldownSeconds) {
        Add-JsonLine -Path $replenishmentLedgerPath -Record ([ordered]@{
            timestamp = (Get-Date -Format 'o')
            decision = 'SKIP_COOLDOWN'
            addedTaskCount = 0
            lastReplenishedAt = $state.lastReplenishedAt
            ageSeconds = $ageSeconds
            cooldownSeconds = $ReplenishmentCooldownSeconds
        })
        return @{ addedTaskCount = 0; reason = 'cooldown'; ageSeconds = $ageSeconds }
    }

    $catalog = @(Get-ReplenishmentCatalog)
    $batchSize = [math]::Max(1, [math]::Min($ReplenishmentBatchSize, $catalog.Count))
    $cursor = if ($state.cursor -ne $null) { [int]$state.cursor } else { 0 }
    for ($candidateIndex = 0; $candidateIndex -lt $catalog.Count; $candidateIndex++) {
        $candidateEntry = $catalog[$candidateIndex]
        if ($candidateEntry.id -notlike 'dunce-*') { continue }
        $candidateSignature = Get-ReplenishmentSignature -Entry $candidateEntry
        if (-not (Test-ReplenishmentEntryBlockedByHistory -Entry $candidateEntry -Signature $candidateSignature)) {
            $cursor = $candidateIndex
            break
        }
    }
    $added = @()
    $attempts = 0
    while ($added.Count -lt $batchSize -and $attempts -lt $catalog.Count) {
        $entry = $catalog[$cursor % $catalog.Count]
        $cursor = ($cursor + 1) % $catalog.Count
        $attempts++
        $signature = Get-ReplenishmentSignature -Entry $entry
        if (Test-ReplenishmentEntryBlockedByHistory -Entry $entry -Signature $signature) {
            Add-JsonLine -Path $replenishmentLedgerPath -Record ([ordered]@{
                timestamp = (Get-Date -Format 'o')
                decision = 'SKIP_REPLENISHMENT_HISTORY_DUPLICATE'
                addedTaskCount = 0
                catalogId = $entry.id
                signature = $signature
            })
            continue
        }
        $task = New-IdleTask -Title $entry.title -Description $entry.description `
            -Priority ([int]$entry.priority) -Risk ([int]$entry.risk) -SourceProject $entry.sourceProject `
            -NextAction $entry.nextAction -Payload $entry.payload
        $task['generationSignature'] = $signature
        $task['replenishmentPolicyId'] = if ($entry.payload.replenishmentPolicyId) { [string]$entry.payload.replenishmentPolicyId } else { 'non-churning-v1' }
        $task['replenishmentCatalogId'] = $entry.id
        Add-IdleTask -QueuePath $QueuePath -Task $task | Out-Null
        $added += @{
            taskId = $task.id
            title = $task.title
            catalogId = $entry.id
            actionType = $entry.payload.actionType
            signature = $task.generationSignature
        }
    }

    $now = Get-Date -Format 'o'
    Set-DynamicProperty -Object $state -Name 'cursor' -Value $cursor
    if ($added.Count -eq 0) {
        Set-DynamicProperty -Object $state -Name 'lastReplenishmentNoopAt' -Value $now
        Set-DynamicProperty -Object $state -Name 'lastNoopAttemptCount' -Value $attempts
        Set-DynamicProperty -Object $state -Name 'cooldownSeconds' -Value $ReplenishmentCooldownSeconds
        Set-DynamicProperty -Object $state -Name 'batchSize' -Value $batchSize
        Set-DynamicProperty -Object $state -Name 'policy' -Value 'non-churning-v1'
        Save-ReplenishmentState -State $state
        Add-JsonLine -Path $replenishmentLedgerPath -Record ([ordered]@{
            timestamp = $now
            decision = 'NO_ELIGIBLE_REPLENISHMENT'
            policy = 'non-churning-v1'
            addedTaskCount = 0
            cooldownSeconds = $ReplenishmentCooldownSeconds
            batchSize = $batchSize
            attempts = $attempts
            cursor = $cursor
        })
        return @{ addedTaskCount = 0; reason = 'no_eligible_replenishment'; attempts = $attempts }
    }

    Set-DynamicProperty -Object $state -Name 'lastReplenishedAt' -Value $now
    Set-DynamicProperty -Object $state -Name 'lastAddedTaskIds' -Value @($added | ForEach-Object { $_.taskId })
    Set-DynamicProperty -Object $state -Name 'cooldownSeconds' -Value $ReplenishmentCooldownSeconds
    Set-DynamicProperty -Object $state -Name 'batchSize' -Value $batchSize
    Set-DynamicProperty -Object $state -Name 'policy' -Value 'non-churning-v1'
    Save-ReplenishmentState -State $state

    Add-JsonLine -Path $replenishmentLedgerPath -Record ([ordered]@{
        timestamp = $now
        decision = 'ADMIT_REPLENISHMENT_BATCH'
        policy = 'non-churning-v1'
        addedTaskCount = $added.Count
        cooldownSeconds = $ReplenishmentCooldownSeconds
        batchSize = $batchSize
        suppressFollowUp = $true
        addedTasks = $added
    })
    return @{ addedTaskCount = $added.Count; reason = 'replenished'; addedTasks = $added }
}

function Test-GeneratedTaskAdmission {
    param([object]$SourceTask, [string]$Title, [object]$Payload)
    $signature = Get-NormalizedGeneratedTaskSignature -Title $Title -Payload $Payload -SourceProject 'obtuse'
    $duplicateCount = Get-ExistingGeneratedSignatureCount -Signature $signature
    $actionType = [string](Get-PayloadValue -Payload $Payload -Name 'actionType' -DefaultValue 'unknown')
    $sourceAction = [string](Get-PayloadValue -Payload $SourceTask.payload -Name 'actionType' -DefaultValue 'unknown')
    $isLoopPair = (($sourceAction -eq 'monitor-health' -and $actionType -eq 'improve-idle-loop') -or ($sourceAction -eq 'improve-idle-loop' -and $actionType -eq 'monitor-health'))
    $noveltyScore = if ($duplicateCount -eq 0) { 0.95 } elseif ($duplicateCount -eq 1) { 0.45 } else { 0.10 }
    $utilityScore = switch ($actionType) {
        'validate-json' { 0.82 }
        'blunder-inflow-mesh' { 0.88 }
        'queue-safe-repairs' { 0.78 }
        'extract-lessons' { 0.72 }
        'identify-work' { 0.70 }
        'monitor-health' { 0.42 }
        'improve-idle-loop' { 0.46 }
        default { 0.55 }
    }
    $proofScore = 0.70
    $duplicateLikelihood = [math]::Min(1.0, [double]$duplicateCount / 3.0)
    $churnLikelihood = if ($isLoopPair -and $duplicateCount -gt 0) { 0.95 } elseif ($duplicateCount -gt 1) { 0.90 } else { 0.25 }
    $decision = 'ADMIT'
    $reason = 'Novel generated task admitted.'
    if ($duplicateCount -ge 2) {
        $decision = 'REJECT_DUPLICATE'
        $reason = 'Signature already appears at least twice in durable queue history.'
    } elseif ($isLoopPair -and $duplicateCount -ge 1) {
        $decision = 'QUARANTINE_CHURN'
        $reason = 'Repeated monitor/improve loop would recreate one-for-one churn.'
    } elseif ($noveltyScore -lt 0.50 -and $utilityScore -lt 0.70) {
        $decision = 'MERGE'
        $reason = 'Low-novelty generated task should merge with existing queue evidence.'
    }
    $record = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        sourceTaskId = $SourceTask.id
        sourceTaskTitle = $SourceTask.title
        sourceActionType = $sourceAction
        generatedTitle = $Title
        generatedActionType = $actionType
        signature = $signature
        duplicateSignatureCount = $duplicateCount
        noveltyScore = $noveltyScore
        utilityScore = $utilityScore
        proofRequirementScore = $proofScore
        duplicateLikelihood = $duplicateLikelihood
        churnLikelihood = $churnLikelihood
        decision = $decision
        admitted = ($decision -eq 'ADMIT')
        reason = $reason
    }
    Write-AntiChurnGuardDecision -Decision $record
    return $record
}

function Add-FollowUpTask {
    param([object]$Task)
    $suppressFollowUp = Get-PayloadValue -Payload $Task.payload -Name 'suppressFollowUp' -DefaultValue $false
    if ($suppressFollowUp -eq $true -or ([string]$suppressFollowUp).ToLowerInvariant() -eq 'true') {
        Write-AntiChurnGuardDecision -Decision ([ordered]@{
            timestamp = (Get-Date -Format 'o')
            sourceTaskId = $Task.id
            sourceTaskTitle = $Task.title
            sourceActionType = [string](Get-PayloadValue -Payload $Task.payload -Name 'actionType' -DefaultValue 'unknown')
            decision = 'SUPPRESS_FOLLOW_UP'
            admitted = $false
            policy = [string](Get-PayloadValue -Payload $Task.payload -Name 'replenishmentPolicyId' -DefaultValue 'task-local')
            reason = 'Task payload suppressFollowUp=true; no recursive follow-up generated.'
        })
        return $null
    }
    $title = ''
    $payload = @{}
    $actionType = [string](Get-PayloadValue -Payload $Task.payload -Name 'actionType' -DefaultValue 'unknown')
    switch ($actionType) {
        'validate-project' { $title = 'Identify safe work for validated project'; $payload = @{ actionType = 'identify-work'; projectPath = (Get-PayloadValue -Payload $Task.payload -Name 'projectPath' -DefaultValue '') } }
        'identify-work' { $title = 'Idle health monitor and queue status'; $payload = @{ actionType = 'monitor-health' } }
        'validate-syntax' { $title = 'Validate obtuse artifact JSON files'; $payload = @{ actionType = 'validate-json' } }
        'validate-json' { $title = 'Extract lessons from provider-stall artifact'; $payload = @{ actionType = 'extract-lessons' } }
        'extract-lessons' { $title = 'Improve idle loop from latest evidence'; $payload = @{ actionType = 'improve-idle-loop' } }
        'monitor-health' { $title = 'Improve idle loop from latest evidence'; $payload = @{ actionType = 'improve-idle-loop' } }
        'improve-idle-loop' { $title = 'Idle health monitor and queue status'; $payload = @{ actionType = 'monitor-health' } }
        'inspect-artifacts' { $title = 'Queue safe low-risk repair candidates'; $payload = @{ actionType = 'queue-safe-repairs' } }
        'queue-safe-repairs' { $title = 'Idle health monitor and queue status'; $payload = @{ actionType = 'monitor-health' } }
        'blunder-inflow-mesh' { $title = ''; $payload = @{} }
    }
    if ($title) {
        $admission = Test-GeneratedTaskAdmission -SourceTask $Task -Title $title -Payload $payload
        if (-not $admission.admitted) {
            return $null
        }
        $newTask = New-IdleTask -Title $title -Description "Follow-up generated by runner after '$($Task.title)'" `
            -Priority 5 -Risk 1 -SourceProject 'obtuse' -NextAction 'Continue recursive loop' -Payload $payload
        $newTask['generationSignature'] = $admission.signature
        $newTask['generatedByTaskId'] = $Task.id
        $newTask['generatedByActionType'] = $admission.sourceActionType
        $newTask['admissionDecision'] = $admission.decision
        Add-IdleTask -QueuePath $QueuePath -Task $newTask | Out-Null
        return $title
    }
    return $null
}

function Read-IdleJsonFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try { return Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json -ErrorAction Stop } catch { return $null }
}

function Get-OpenRouterSelfCodingConfig {
    $root = if ([System.IO.Path]::IsPathRooted($openRouterSelfCodingArtifactRoot)) {
        $openRouterSelfCodingArtifactRoot
    } else {
        Join-Path $repoRoot $openRouterSelfCodingArtifactRoot
    }
    $config = Read-IdleJsonFile -Path (Join-Path $root 'openrouter_provider_lane_config.json')
    if (-not $config) {
        return [ordered]@{
            enabled = $false
            reason = 'OPENROUTER_SELF_CODING_CONFIG_MISSING'
            artifactRoot = $openRouterSelfCodingArtifactRoot
        }
    }
    return [ordered]@{
        enabled = ([bool]$config.openRouterIdleEnabled -and -not [bool]$config.modelNeedsOperatorReview -and [bool]$config.operatorApproved)
        reason = $(if ([bool]$config.modelNeedsOperatorReview) { 'MODEL_ID_NEEDS_OPERATOR_REVIEW' } elseif (-not [bool]$config.operatorApproved) { 'OPERATOR_APPROVAL_NOT_RECORDED' } elseif (-not [bool]$config.openRouterIdleEnabled) { 'OPENROUTER_SELF_CODING_NOT_ENABLED' } else { 'READY' })
        artifactRoot = $openRouterSelfCodingArtifactRoot
        model = $(if ($config.openRouterModelAlias -eq 'deepseek/deepseek-v4-flash') { 'deepseek-v4-flash' } elseif ($config.openRouterModelAlias) { $config.openRouterModelAlias } elseif ($config.modelUsed -eq 'deepseek/deepseek-v4-flash') { 'deepseek-v4-flash' } else { $config.modelUsed })
        modelAlias = $(if ($config.openRouterModelAlias -eq 'deepseek/deepseek-v4-flash') { 'deepseek-v4-flash' } elseif ($config.openRouterModelAlias) { $config.openRouterModelAlias } elseif ($config.modelUsed -eq 'deepseek/deepseek-v4-flash') { 'deepseek-v4-flash' } else { $config.modelUsed })
        modelResolved = $(if ($config.openRouterModelResolved) { $config.openRouterModelResolved } else { $config.modelUsed })
        providerBudgetGateEnabled = $false
        providerBudgetGateStatus = 'DISABLED_BY_OPERATOR_CHEAP_MODEL_POLICY'
        callsAttempted = $config.providerCallsAttempted
        callsSucceeded = $config.providerCallsSucceeded
        callsFailed = $config.providerCallsFailed
    }
}

function Invoke-SelfCodingAcceleratorIfEligible {
    $config = Get-OpenRouterSelfCodingConfig
    if (-not $config.enabled) {
        return [ordered]@{ invoked = $false; reason = $config.reason; config = $config }
    }
    $selfCodingRoot = if ([System.IO.Path]::IsPathRooted([string]$config.artifactRoot)) {
        [string]$config.artifactRoot
    } else {
        Join-Path $repoRoot ([string]$config.artifactRoot)
    }
    $lastRun = Read-IdleJsonFile -Path (Join-Path $selfCodingRoot 'self_coding_run_summary.json')
    if ($lastRun -and $lastRun.timestamp) {
        try {
            $ageSeconds = [math]::Round(((Get-Date) - [datetime]$lastRun.timestamp).TotalSeconds, 3)
            if ($ageSeconds -lt $openRouterSelfCodingCooldownSeconds) {
                return [ordered]@{
                    invoked = $false
                    reason = 'SELF_CODING_COOLDOWN'
                    cooldownSeconds = $openRouterSelfCodingCooldownSeconds
                    cooldownRemainingSeconds = [math]::Max(0, [math]::Round($openRouterSelfCodingCooldownSeconds - $ageSeconds, 3))
                    lastRunAt = $lastRun.timestamp
                    config = $config
                }
            }
        } catch {}
    }
    # Provider call-budget gate removed per operator cheap-model policy.
    $forbidden = Get-ForbiddenProcessSummary
    if ($forbidden.terminalBenchProcessCount -gt 0 -or $forbidden.providerProcessCount -gt 0) {
        return [ordered]@{ invoked = $false; reason = 'SAFETY_PROCESS_GUARD_BLOCKED'; config = $config; forbidden = $forbidden }
    }
    $accelerator = Join-Path $PSScriptRoot 'idle-self-coding-accelerator.ps1'
    if (-not (Test-Path -LiteralPath $accelerator)) {
        return [ordered]@{ invoked = $false; reason = 'SELF_CODING_ACCELERATOR_MISSING'; config = $config }
    }
    $args = @(
        '-Mode','RunOnce',
        '-OperatorApproved','true',
        '-Model',[string]$config.model,
        '-ArtifactRoot',[string]$config.artifactRoot,
        '-QueuePath',$QueuePath,
        '-IdleArtifactRoot',$ArtifactRoot
    )
    $raw = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $accelerator @args 2>&1
    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) { $exitCode = 0 }
    $text = ($raw | ForEach-Object { [string]$_ }) -join "`n"
    $parsed = $null
    if ($text) { try { $parsed = $text | ConvertFrom-Json -ErrorAction Stop } catch {} }
    return [ordered]@{
        invoked = $true
        reason = $(if ($exitCode -eq 0) { 'RUNONCE_COMPLETED' } else { 'RUNONCE_FAILED' })
        exitCode = $exitCode
        result = $(if ($parsed) { $parsed.result } else { $null })
        parsed = $parsed
    }
}

function Invoke-IdleCycle {
    $task = Get-SelectedIdleTask
    if (-not $task) {
        $replenishment = Invoke-QueueReplenishmentIfDue
        if ($replenishment.addedTaskCount -gt 0) {
            Write-CycleEvent 'INFO' "Queue replenishment added $($replenishment.addedTaskCount) safe task(s)"
            $task = Get-SelectedIdleTask
        }
    }
    if (-not $task) {
        $selfCoding = Invoke-SelfCodingAcceleratorIfEligible
        if ($selfCoding.invoked) {
            $entry = @{
                cycleAt = (Get-Date -Format 'o')
                taskId = 'openrouter-self-coding'
                taskTitle = 'OpenRouter self-coding accelerator'
                status = $(if ($selfCoding.exitCode -eq 0) { 'passed' } else { 'failed' })
                safe = ($selfCoding.exitCode -eq 0)
                execution = $selfCoding
                validation = @{ pass = ($selfCoding.exitCode -eq 0); reason = $selfCoding.reason }
                nextTaskQueued = $null
            }
            Add-CycleLog -Entry $entry
            Add-JsonLine -Path $idleCycleLedgerPath -Record $entry
            Write-CycleEvent 'INFO' "OpenRouter self-coding accelerator -> $($selfCoding.reason)"
            return @{ cycled = $true; entry = $entry }
        }
        Write-CycleEvent 'INFO' 'No pending task; idle cycle skipped'
        return @{ cycled = $false; reason = 'NO_PENDING_TASK'; selfCoding = $selfCoding }
    }

    Write-CycleEvent 'INFO' "Cycle start: task $($task.id) '$($task.title)'"
    Update-IdleTask -QueuePath $QueuePath -TaskId $task.id -Status 'running' | Out-Null

    $sim = Invoke-TaskSimulation -Task $task
    $safety = Test-SafeToExecute -Task $task -RiskThreshold $RiskThreshold
    $mockTrial = Invoke-PreExecutionMockTrial -Task $task
    Add-RunnerSafetyDecision -Task $task -Simulation $sim -Safety $safety -MockTrial $mockTrial

    $exec = $null
    $validation = @{ pass = $false; reason = 'Not executed' }
    $status = 'blocked'
    $nextTaskTitle = $null

    if ($sim.canProceed -and $safety.safe -and $mockTrial.safe_for_idle) {
        $exec = Invoke-TaskExecution -Task $task
        $validation = Invoke-TaskValidation -ExecutionResult $exec
        $status = if ($validation.pass) { 'passed' } else { 'failed' }
        $nextTaskTitle = Add-FollowUpTask -Task $task
    } else {
        $reason = if (-not $sim.canProceed) { $sim.precondition } elseif (-not $safety.safe) { $safety.reason } else { $mockTrial.reason }
        $exec = @{ success = $false; output = $reason }
        $validation = @{ pass = $false; reason = $reason }
        $status = if (-not $mockTrial.safe_for_idle -or -not $safety.safe) { 'quarantined' } else { 'blocked' }
        $suppressFollowUp = Get-PayloadValue -Payload $task.payload -Name 'suppressFollowUp' -DefaultValue $false
        if ($suppressFollowUp -eq $true -or ([string]$suppressFollowUp).ToLowerInvariant() -eq 'true') {
            Write-AntiChurnGuardDecision -Decision ([ordered]@{
                timestamp = (Get-Date -Format 'o')
                sourceTaskId = $task.id
                sourceTaskTitle = $task.title
                sourceActionType = [string](Get-PayloadValue -Payload $task.payload -Name 'actionType' -DefaultValue 'unknown')
                decision = 'SUPPRESS_BLOCKED_DECOMPOSITION'
                admitted = $false
                policy = [string](Get-PayloadValue -Payload $task.payload -Name 'replenishmentPolicyId' -DefaultValue 'task-local')
                reason = 'Task payload suppressFollowUp=true; blocked task did not generate decomposition follow-up.'
            })
            $nextTaskTitle = $null
        } else {
            $nextTaskTitle = Add-DecompositionTask -Task $task -Reason $reason
        }
    }

    Update-IdleTask -QueuePath $QueuePath -TaskId $task.id -Status $status `
        -SimulationResult $sim -ExecutionResult $exec -ValidationResult $validation `
        -NextAction $(if ($nextTaskTitle) { "Queued: $nextTaskTitle" } else { 'None' }) | Out-Null

    $entry = @{
        cycleAt = (Get-Date -Format 'o')
        taskId = $task.id
        taskTitle = $task.title
        status = $status
        safe = ($sim.canProceed -and $safety.safe)
        simulation = $sim
        safety = $safety
        mockTrial = $mockTrial
        execution = $exec
        validation = $validation
        nextTaskQueued = $nextTaskTitle
    }
    Add-CycleLog -Entry $entry
    Add-JsonLine -Path $idleCycleLedgerPath -Record $entry
    Write-CycleEvent 'INFO' "Cycle end: task '$($task.title)' -> $status"
    return @{ cycled = $true; entry = $entry }
}

# Main dispatch
$state = Get-IdleState

switch ($Action) {
    'Status' {
        $lock = Get-LockInfo
        $lastReplenishmentDecision = $null
        if (Test-Path $replenishmentLedgerPath) {
            $lastLine = Get-Content $replenishmentLedgerPath -Tail 1
            if ($lastLine) { try { $lastReplenishmentDecision = $lastLine | ConvertFrom-Json } catch {} }
        }
        @{
            state = $state
            lock = $lock
            active = [bool]($lock -and $lock.status -eq 'active' -and $lock.pid -and (Test-ProcessAlive -PidValue $lock.pid))
            heartbeat = if (Test-Path $heartbeatPath) { Get-Content $heartbeatPath -Raw | ConvertFrom-Json } else { $null }
            queueStats = Get-QueueStats -QueuePath $QueuePath
            replenishmentState = Get-ReplenishmentState
            lastReplenishmentDecision = $lastReplenishmentDecision
            queueCompaction = Get-QueueCompactionSummary
        } | ConvertTo-Json -Depth 10
    }
    'Stop' {
        Request-IdleStop
        $state.status = 'stopped'
        $state.stoppedAt = (Get-Date -Format 'o')
        Save-IdleState -State $state
        Write-CycleEvent 'INFO' 'Idle runner stopped by operator command'
        $state | ConvertTo-Json -Depth 10
    }
    'RunOnce' {
        $state.status = 'running'
        $state.lastCycleAt = (Get-Date -Format 'o')
        Save-IdleState -State $state
        $result = Invoke-IdleCycle
        $state = Get-IdleState
        $state.cyclesRun = [int]$state.cyclesRun + 1
        $state.lastCycleAt = (Get-Date -Format 'o')
        Save-IdleState -State $state
        Invoke-IdleQueueCompaction -Reason 'run_once_cycle_completed' | Out-Null
        $result | ConvertTo-Json -Depth 10
    }
    'Start' {
        Assert-OneIdleRunner
        Clear-StopRequestForStart
        $recovered = Repair-RunningIdleTasks -QueuePath $QueuePath
        Invoke-IdleQueueCompaction -Reason 'runner_start_preflight' | Out-Null
        $state.status = 'running'
        $state.pid = $PID
        $state.startedAt = (Get-Date -Format 'o')
        $state.recoveredRunningTasks = $recovered.recoveredRunningTasks
        Save-IdleState -State $state
        Write-CycleEvent 'INFO' "Idle runner starting (PID $PID); max cycles = $MaxCycles"

        $cycle = 0
        $consecutiveCycleFailures = 0
        while ($state.status -eq 'running') {
            Write-IdleHeartbeat -State @{ status = 'running'; cycle = $cycle; maxCycles = $MaxCycles }
            if (Test-StopRequested) {
                Write-CycleEvent 'INFO' 'Stop file observed; shutting down cleanly'
                Acknowledge-IdleStop
                $state.status = 'stopped'
                break
            }
            if ($MaxCycles -gt 0 -and $cycle -ge $MaxCycles) {
                Write-CycleEvent 'INFO' "Max cycles ($MaxCycles) reached; stopping"
                $state.status = 'stopped'
                break
            }
            try {
                $result = Invoke-IdleCycle
                $consecutiveCycleFailures = 0
            }
            catch {
                $consecutiveCycleFailures++
                $failure = [ordered]@{
                    timestamp = (Get-Date -Format 'o')
                    cycle = $cycle
                    pid = $PID
                    consecutiveFailures = $consecutiveCycleFailures
                    maximumConsecutiveFailures = $MaxConsecutiveCycleFailures
                    errorType = $_.Exception.GetType().FullName
                    error = $_.Exception.Message
                }
                Add-JsonLine -Path (Join-Path $ArtifactRoot 'idle-cycle-failure-ledger.jsonl') -Record $failure
                Write-CycleEvent 'ERROR' "Cycle failure $consecutiveCycleFailures/$MaxConsecutiveCycleFailures`: $($_.Exception.Message)"
                $recovery = Repair-RunningIdleTasks -QueuePath $QueuePath
                Write-CycleEvent 'WARN' "Recovered $($recovery.recoveredRunningTasks) running task(s) after cycle failure"
                if ($consecutiveCycleFailures -ge $MaxConsecutiveCycleFailures) {
                    $state = Get-IdleState
                    $state.status = 'failed'
                    $state.lastError = $_.Exception.Message
                    $state.lastFailureAt = (Get-Date -Format 'o')
                    Save-IdleState -State $state
                    break
                }
                Write-IdleHeartbeat -State @{ status = 'recovering'; cycle = $cycle; maxCycles = $MaxCycles; consecutiveCycleFailures = $consecutiveCycleFailures }
                Start-Sleep -Seconds $LoopDelaySeconds
                $state = Get-IdleState
                continue
            }
            if (-not $result.cycled) {
                if ($MaxCycles -gt 0) {
                    $cycle++
                    $state = Get-IdleState
                    $state.lastCycleAt = (Get-Date -Format 'o')
                    Save-IdleState -State $state
                    continue
                }
                Write-CycleEvent 'INFO' 'No work available; continuing idle wait'
                Start-Sleep -Seconds $LoopDelaySeconds
                $state = Get-IdleState
                continue
            }
            $cycle++
            $state = Get-IdleState
            $state.cyclesRun = [int]$state.cyclesRun + 1
            $state.lastCycleAt = (Get-Date -Format 'o')
            Save-IdleState -State $state
            Invoke-IdleQueueCompaction -Reason 'cycle_completed' | Out-Null
            Start-Sleep -Seconds $LoopDelaySeconds
        }
        if ($state.status -eq 'running') { $state.status = 'stopped' }
        $state.stoppedAt = (Get-Date -Format 'o')
        Save-IdleState -State $state
        Write-IdleHeartbeat -State @{ status = $state.status; cycle = $cycle; maxCycles = $MaxCycles; consecutiveCycleFailures = $consecutiveCycleFailures }
        Set-IdleLockStatus -Status $state.status
        Write-CycleEvent 'INFO' "Idle runner finished with status $($state.status)"
        $state | ConvertTo-Json -Depth 10
    }
}
