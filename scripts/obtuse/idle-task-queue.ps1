#requires -Version 5.1
<#
.SYNOPSIS
    Persistent task queue for Blunder idle-mode operations.

.DESCRIPTION
    Supports pending/running/passed/failed/blocked tasks with priority and risk
    scores. Safe, local, no external calls.
#>
[CmdletBinding()]
param(
    [ValidateSet('Init','Add','Next','Update','List','Stats')]
    [string]$Action = 'List',
    [string]$QueuePath = 'artifacts/obtuse/idle-mode-full-operationalization-v1/idle-task-queue.json',
    [Parameter(ValueFromRemainingArguments = $true)]$Remaining
)

$ErrorActionPreference = 'Stop'

function Resolve-QueuePath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path (Get-Location).Path $Path
}

function Initialize-IdleQueue {
    param([string]$Path)
    $Path = Resolve-QueuePath $Path
    $dir = Split-Path $Path -Parent
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    if (-not (Test-Path $Path)) {
        Write-JsonFile -Value @{ version = '1'; tasks = @() } -Path $Path
    }
    return $Path
}

function Write-JsonFile {
    param([Parameter(Mandatory)] [object]$Value, [Parameter(Mandatory)] [string]$Path)
    $json = $Value | ConvertTo-Json -Depth 10
    $directory = Split-Path $Path -Parent
    if ($directory -and -not (Test-Path -LiteralPath $directory)) {
        New-Item -ItemType Directory -Force -Path $directory | Out-Null
    }
    $lastError = $null
    for ($i = 0; $i -lt 10; $i++) {
        $tempPath = "$Path.tmp.$PID.$([Guid]::NewGuid().ToString('N'))"
        $backupPath = "$Path.replace-backup.$PID.$([Guid]::NewGuid().ToString('N'))"
        try {
            [System.IO.File]::WriteAllText($tempPath, ($json -replace "`r?`n", "`r`n"), [System.Text.UTF8Encoding]::new($false))
            if (Test-Path -LiteralPath $Path) {
                [System.IO.File]::Replace($tempPath, $Path, $backupPath, $true)
                Remove-Item -LiteralPath $backupPath -Force -ErrorAction SilentlyContinue
            } else {
                Move-Item -LiteralPath $tempPath -Destination $Path -Force
            }
            return
        }
        catch {
            $lastError = $_.Exception
            Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath $backupPath -Force -ErrorAction SilentlyContinue
            Start-Sleep -Milliseconds 200
        }
    }
    throw $lastError
}

function Get-IdleQueue {
    param([string]$Path)
    $Path = Initialize-IdleQueue -Path $Path
    try {
        $queue = Get-Content $Path -Raw | ConvertFrom-Json -ErrorAction Stop
        if (-not ($queue.PSObject.Properties.Name -contains 'tasks')) {
            $queue | Add-Member -NotePropertyName tasks -NotePropertyValue @()
        }
        if ($null -eq $queue.tasks) {
            $queue.tasks = @()
        }
        return $queue
    }
    catch {
        $raw = Get-Content $Path -Raw -ErrorAction SilentlyContinue
        $recoveryPath = "$Path.bad-json-$(Get-Date -Format 'yyyyMMddTHHmmss').txt"
        [System.IO.File]::WriteAllText($recoveryPath, [string]$raw, [System.Text.UTF8Encoding]::new($false))
        @{
            version = '1'
            recoveredFromBadJson = $true
            recoverySource = $recoveryPath
            tasks = @(
                New-IdleTask -Title 'Repair recovered idle queue state' `
                    -Description "Queue JSON was malformed and was preserved at $recoveryPath. Continue with safe validation tasks." `
                    -Priority 10 -Risk 1 -SourceProject 'obtuse' -NextAction 'Validate queue and artifact JSON.' `
                    -Payload @{ actionType = 'validate-json'; targetPath = (Split-Path $Path -Parent) }
            )
        } | ForEach-Object { Write-JsonFile -Value $_ -Path $Path }
        return Get-Content $Path -Raw | ConvertFrom-Json -ErrorAction Stop
    }
}

function Save-IdleQueue {
    param([object]$Queue, [string]$Path)
    $Path = Resolve-QueuePath $Path
    Write-JsonFile -Value $Queue -Path $Path
}

function ConvertTo-IdleTaskPayload {
    param([object]$Payload)
    if ($null -eq $Payload) { return @{} }
    if ($Payload -is [hashtable]) { return $Payload }
    if ($Payload -is [System.Collections.IDictionary]) {
        $convertedDictionary = @{}
        foreach ($key in $Payload.Keys) {
            $convertedDictionary[[string]$key] = $Payload[$key]
        }
        return $convertedDictionary
    }
    $converted = @{}
    foreach ($property in @($Payload.PSObject.Properties)) {
        $converted[$property.Name] = $property.Value
    }
    return $converted
}

function New-IdleTask {
    param(
        [Parameter(Mandatory)] [string]$Title,
        [string]$Description = '',
        [int]$Priority = 5,
        [int]$Risk = 1,
        [string]$SourceProject = 'obtuse',
        [string]$NextAction = '',
        [object]$Payload = @{}
    )
    $normalizedPayload = ConvertTo-IdleTaskPayload -Payload $Payload
    return @{
        id = [Guid]::NewGuid().ToString('N')
        title = $Title
        description = $Description
        status = 'pending'
        priority = $Priority
        risk = $Risk
        sourceProject = $SourceProject
        nextAction = $NextAction
        payload = $normalizedPayload
        simulationResult = $null
        executionResult = $null
        validationResult = $null
        createdAt = (Get-Date -Format 'o')
        updatedAt = (Get-Date -Format 'o')
    }
}

function Add-IdleTask {
    param(
        [Parameter(Mandatory)] [string]$QueuePath,
        [Parameter(Mandatory)] [object]$Task
    )
    $queue = Get-IdleQueue -Path $QueuePath
    if (-not ($queue.PSObject.Properties.Name -contains 'tasks')) {
        $queue | Add-Member -NotePropertyName tasks -NotePropertyValue @()
    }
    $tasks = [System.Collections.ArrayList]@($queue.tasks)
    $tasks.Add($Task) | Out-Null
    $queue.tasks = $tasks
    Save-IdleQueue -Queue $queue -Path $QueuePath
    return $Task
}

function Get-NextIdleTask {
    param([string]$QueuePath)
    $queue = Get-IdleQueue -Path $QueuePath
    $next = $queue.tasks |
        Where-Object { $_.status -eq 'pending' -or $_.status -eq 'running' } |
        Sort-Object -Property @{Expression = { $_.risk }; Ascending = $true }, @{Expression = { $_.priority }; Descending = $true }, createdAt |
        Select-Object -First 1
    return $next
}

function Update-IdleTask {
    param(
        [Parameter(Mandatory)] [string]$QueuePath,
        [Parameter(Mandatory)] [string]$TaskId,
        [ValidateSet('pending','running','passed','failed','blocked','quarantined')]
        [string]$Status,
        [object]$SimulationResult = $null,
        [object]$ExecutionResult = $null,
        [object]$ValidationResult = $null,
        [string]$NextAction = $null
    )
    $queue = Get-IdleQueue -Path $QueuePath
    $task = $queue.tasks | Where-Object { $_.id -eq $TaskId } | Select-Object -First 1
    if (-not $task) { throw "Task $TaskId not found" }
    $task.status = $Status
    $task.updatedAt = (Get-Date -Format 'o')
    if ($null -ne $SimulationResult) { $task.simulationResult = $SimulationResult }
    if ($null -ne $ExecutionResult) { $task.executionResult = $ExecutionResult }
    if ($null -ne $ValidationResult) { $task.validationResult = $ValidationResult }
    if ($null -ne $NextAction) { $task.nextAction = $NextAction }
    Save-IdleQueue -Queue $queue -Path $QueuePath
    return $task
}

function Get-QueueStats {
    param([string]$QueuePath)
    $queue = Get-IdleQueue -Path $QueuePath
    if (-not ($queue.PSObject.Properties.Name -contains 'tasks')) {
        $queue | Add-Member -NotePropertyName tasks -NotePropertyValue @()
    }
    $statuses = $queue.tasks | Group-Object -Property status -NoElement
    $stats = @{ total = $queue.tasks.Count }
    foreach ($g in $statuses) { $stats[$g.Name] = $g.Count }
    foreach ($s in @('pending','running','passed','failed','blocked','quarantined')) {
        if (-not $stats.ContainsKey($s)) { $stats[$s] = 0 }
    }
    return $stats
}

function Repair-RunningIdleTasks {
    param([string]$QueuePath)
    $queue = Get-IdleQueue -Path $QueuePath
    if (-not ($queue.PSObject.Properties.Name -contains 'tasks')) {
        $queue | Add-Member -NotePropertyName tasks -NotePropertyValue @()
    }
    $recovered = 0
    foreach ($task in @($queue.tasks)) {
        if ($task.status -eq 'running') {
            $task.status = 'pending'
            $task.updatedAt = (Get-Date -Format 'o')
            $task.nextAction = 'Recovered from prior interrupted idle run; retry safely.'
            $recovered++
        }
    }
    Save-IdleQueue -Queue $queue -Path $QueuePath
    return @{ recoveredRunningTasks = $recovered }
}

# Command-line surface (run only when invoked directly, not when dot-sourced)
if ($MyInvocation.InvocationName -ne '.') {
$QueuePath = Initialize-IdleQueue -Path $QueuePath

switch ($Action) {
    'Init' {
        Write-Host "Queue initialized at $QueuePath"
    }
    'Add' {
        # Expects hashtable passed via -Remaining (not friendly); use runner or dot-source instead
        throw 'Add action requires dot-sourced use of Add-IdleTask.'
    }
    'Next' {
        $task = Get-NextIdleTask -QueuePath $QueuePath
        if ($task) { $task | ConvertTo-Json -Depth 10 } else { Write-Host 'NO_PENDING_TASK' }
    }
    'Update' {
        throw 'Update action requires dot-sourced use of Update-IdleTask.'
    }
    'List' {
        $queue = Get-IdleQueue -Path $QueuePath
        $queue | ConvertTo-Json -Depth 10
    }
    'Stats' {
        Get-QueueStats -QueuePath $QueuePath | ConvertTo-Json -Depth 10
    }
}
}
