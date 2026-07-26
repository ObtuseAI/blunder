#requires -Version 5.1
<#
.SYNOPSIS
    Adapter to discover Dunce / ShowMeAI project paths and identify safe work.

.DESCRIPTION
    Searches common local roots for project directories, reports findings, and
    queues safe tasks only when OperatorApproved is true. Never performs
    destructive actions. Without approval, queue-admission actions return the
    candidate count and write a report artifact without mutating the queue.
#>
[CmdletBinding()]
param(
    [ValidateSet('Discover','IdentifyWork','QueueFallback','ClassifySafeTasks','RunSafeValidations','ReadStatus')]
    [string]$Action = 'Discover',
    [string]$OperatorApproved = 'false',
    [string]$QueuePath = 'artifacts/obtuse/idle-mode-full-operationalization-v1/idle-task-queue.json',
    [string]$ArtifactRoot = 'artifacts/obtuse/idle-mode-full-operationalization-v1',
    [string[]]$SearchRoots = @('C:\src','C:\Users\developer\source','C:\Users\developer\repos','C:\Users\developer\Projects','C:\Users\developer\OneDrive\Desktop')
)

$ErrorActionPreference = 'Stop'

$queueModule = Join-Path $PSScriptRoot 'idle-task-queue.ps1'
$script:__adapterAction = $Action
$script:__adapterOperatorApproved = $OperatorApproved
$script:__adapterQueuePath = $QueuePath
$script:__adapterArtifactRoot = $ArtifactRoot
. $queueModule
$Action = $script:__adapterAction
$OperatorApproved = $script:__adapterOperatorApproved
$QueuePath = $script:__adapterQueuePath
$ArtifactRoot = $script:__adapterArtifactRoot

function Convert-ToStrictBoolean {
    param([string]$Value)
    $normalized = ([string]$Value).Trim().ToLowerInvariant()
    if ($normalized -in @('1','true','yes','y')) { return $true }
    if ($normalized -in @('0','false','no','n','')) { return $false }
    throw "Invalid boolean value for OperatorApproved: $Value"
}

$operatorApprovedBool = Convert-ToStrictBoolean -Value $OperatorApproved

function New-QueueAdmissionRefusal {
    param([string]$ActionName, [int]$CandidateCount)
    return @{
        approved = $false
        admissionState = 'REFUSED_OPERATOR_APPROVAL_REQUIRED'
        action = $ActionName
        candidateTaskCount = $CandidateCount
        tasksAdded = 0
    }
}

function Find-ProjectPaths {
    param([string[]]$Roots)
    $names = @('*dunce*','*showmeai*','*show-me*')
    $found = @()
    foreach ($root in $Roots) {
        if (-not (Test-Path $root)) { continue }
        try {
            $dirs = Get-ChildItem -Path $root -Directory -Recurse -ErrorAction SilentlyContinue |
                Where-Object { $n = $_.Name.ToLower(); foreach ($pat in $names) { if ($n -like $pat) { return $true } } return $false }
            foreach ($d in $dirs) {
                $found += @{ path = $d.FullName; name = $d.Name }
            }
        } catch {}
    }
    return $found
}

function Add-DiscoveryTasks {
    param([string]$QueuePath, [array]$Paths, [bool]$Approved)
    if ($Paths.Count -eq 0) {
        if (-not $Approved) {
            return New-QueueAdmissionRefusal -ActionName 'DiscoveryFallback' -CandidateCount 4
        }
        Add-IdleTask -QueuePath $QueuePath -Task (New-IdleTask -Title 'Re-scan for Dunce / ShowMeAI paths' `
            -Description 'No Dunce or ShowMeAI project path found. Schedule periodic re-scan.' `
            -Priority 3 -Risk 1 -SourceProject 'dunce/showmeai' -NextAction 'Run Discover again after operator provides path.') | Out-Null
        Add-IdleTask -QueuePath $QueuePath -Task (New-IdleTask -Title 'Audit obtuse PowerShell scripts syntax' `
            -Description 'Fallback safe task: validate all .ps1 scripts in obtuse/scripts parse without errors.' `
            -Priority 8 -Risk 1 -SourceProject 'obtuse' -NextAction 'Record findings and queue JSON validation.' `
            -Payload @{ actionType = 'validate-syntax' }) | Out-Null
        Add-IdleTask -QueuePath $QueuePath -Task (New-IdleTask -Title 'Validate obtuse artifact JSON files' `
            -Description 'Fallback safe task: ensure all JSON artifacts parse and contain no unredacted secret patterns.' `
            -Priority 7 -Risk 1 -SourceProject 'obtuse' -NextAction 'Queue lesson extraction from prior stall report.' `
            -Payload @{ actionType = 'validate-json' }) | Out-Null
        Add-IdleTask -QueuePath $QueuePath -Task (New-IdleTask -Title 'Extract lessons from provider-stall artifact' `
            -Description 'Recursive improvement: read prior FINAL_REPORT, extract lesson, write lessons file, queue next improvement.' `
            -Priority 6 -Risk 1 -SourceProject 'obtuse' -NextAction 'Queue idle health monitor task.' `
            -Payload @{ actionType = 'extract-lessons' }) | Out-Null
        return @{ pathFound = $false; tasksAdded = 4 }
    }

    if (-not $Approved) {
        return New-QueueAdmissionRefusal -ActionName 'DiscoveryProjects' -CandidateCount $Paths.Count
    }

    foreach ($p in $Paths) {
        Add-IdleTask -QueuePath $QueuePath -Task (New-IdleTask -Title "Validate project $($p.name)" `
            -Description "Discovered path: $($p.path). Run syntax/JSON/secret validation only." `
            -Priority 9 -Risk 1 -SourceProject $p.name -NextAction 'Queue deeper safe work after validation passes.' `
            -Payload @{ actionType = 'validate-project'; projectPath = $p.path }) | Out-Null
    }
    return @{ pathFound = $true; tasksAdded = $Paths.Count }
}

function Get-ProjectStatus {
    param([array]$Paths)
    foreach ($p in $Paths) {
        $projectPath = $p.path
        $packageJson = Join-Path $projectPath 'package.json'
        $pyproject = Join-Path $projectPath 'pyproject.toml'
        $scripts = @()
        if (Test-Path $packageJson) {
            try {
                $pkg = Get-Content $packageJson -Raw | ConvertFrom-Json
                if ($pkg.scripts) {
                    $scripts = $pkg.scripts.PSObject.Properties | ForEach-Object {
                        @{ name = $_.Name; command = $_.Value; safe = ($_.Name -match 'lint|test|check|type|build') }
                    }
                }
            }
            catch {}
        }
        @{
            path = $projectPath
            name = $p.name
            hasPackageJson = (Test-Path $packageJson)
            hasPyproject = (Test-Path $pyproject)
            safeScripts = @($scripts | Where-Object { $_.safe })
            blockedActions = @('delete','remove','deploy','publish','push','reset','clean')
        }
    }
}

function Add-SafeClassifiedTasks {
    param([string]$QueuePath, [array]$Statuses, [bool]$Approved)
    $candidateCount = 0
    foreach ($status in $Statuses) {
        $candidateCount++
        $candidateCount += @($status.safeScripts).Count
    }
    if (-not $Approved) {
        return New-QueueAdmissionRefusal -ActionName 'ClassifySafeTasks' -CandidateCount $candidateCount
    }

    $added = 0
    foreach ($status in $Statuses) {
        Add-IdleTask -QueuePath $QueuePath -Task (New-IdleTask -Title "Inspect artifacts for $($status.name)" `
            -Description "Read-only artifact/status inspection for $($status.path)." `
            -Priority 8 -Risk 1 -SourceProject $status.name -NextAction 'Queue only low-risk validations.' `
            -Payload @{ actionType = 'inspect-artifacts'; targetPath = $status.path }) | Out-Null
        $added++
        foreach ($script in @($status.safeScripts)) {
            Add-IdleTask -QueuePath $QueuePath -Task (New-IdleTask -Title "Classified safe script: $($status.name) $($script.name)" `
                -Description "Safe script discovered but not executed automatically: $($script.command)" `
                -Priority 5 -Risk 2 -SourceProject $status.name -NextAction 'Human-visible validation candidate; no provider calls.' `
                -Payload @{ actionType = 'monitor-health'; scriptName = $script.name; projectPath = $status.path }) | Out-Null
            $added++
        }
    }
    return $added
}

$QueuePath = Initialize-IdleQueue -Path $QueuePath
New-Item -ItemType Directory -Force -Path $ArtifactRoot | Out-Null

$result = @{
    timestamp = (Get-Date -Format 'o')
    action = $Action
    pathFound = $false
    paths = @()
    tasksAdded = 0
    queueAdmissionApproved = $operatorApprovedBool
    admissionState = $(if ($operatorApprovedBool) { 'APPROVED' } else { 'REPORT_ONLY_OPERATOR_APPROVAL_REQUIRED' })
}

switch ($Action) {
    'Discover' {
        $paths = Find-ProjectPaths -Roots $SearchRoots
        $result.paths = $paths
        $result.pathFound = ($paths.Count -gt 0)
        $added = Add-DiscoveryTasks -QueuePath $QueuePath -Paths $paths -Approved $operatorApprovedBool
        $result.tasksAdded = $added.tasksAdded
        if ($added.admissionState) { $result.admissionState = $added.admissionState; $result.candidateTaskCount = $added.candidateTaskCount }
    }
    'IdentifyWork' {
        $paths = Find-ProjectPaths -Roots $SearchRoots
        $result.paths = $paths
        $result.pathFound = ($paths.Count -gt 0)
        if ($result.pathFound) {
            if (-not $operatorApprovedBool) {
                $result.candidateTaskCount = $paths.Count
                $result.admissionState = 'REFUSED_OPERATOR_APPROVAL_REQUIRED'
                break
            }
            foreach ($p in $paths) {
                Add-IdleTask -QueuePath $QueuePath -Task (New-IdleTask -Title "List safe tasks for $($p.name)" `
                    -Description "Inspect $($p.path) for lint/tests/docs without destructive changes." `
                    -Priority 8 -Risk 1 -SourceProject $p.name -NextAction 'Run idle-validation Composite on project.' `
                    -Payload @{ actionType = 'identify-work'; projectPath = $p.path }) | Out-Null
                $result.tasksAdded++
            }
        }
    }
    'QueueFallback' {
        $added = Add-DiscoveryTasks -QueuePath $QueuePath -Paths @() -Approved $operatorApprovedBool
        $result.pathFound = $false
        $result.tasksAdded = $added.tasksAdded
        if ($added.admissionState) { $result.admissionState = $added.admissionState; $result.candidateTaskCount = $added.candidateTaskCount }
    }
    'ReadStatus' {
        $paths = Find-ProjectPaths -Roots $SearchRoots
        $result.paths = Get-ProjectStatus -Paths $paths
        $result.pathFound = ($paths.Count -gt 0)
    }
    'ClassifySafeTasks' {
        $paths = Find-ProjectPaths -Roots $SearchRoots
        $statuses = @(Get-ProjectStatus -Paths $paths)
        $result.paths = $statuses
        $result.pathFound = ($paths.Count -gt 0)
        $added = Add-SafeClassifiedTasks -QueuePath $QueuePath -Statuses $statuses -Approved $operatorApprovedBool
        if ($added -is [hashtable]) {
            $result.tasksAdded = $added.tasksAdded
            $result.admissionState = $added.admissionState
            $result.candidateTaskCount = $added.candidateTaskCount
        } else {
            $result.tasksAdded = $added
        }
    }
    'RunSafeValidations' {
        $paths = Find-ProjectPaths -Roots $SearchRoots
        $result.paths = $paths
        $result.pathFound = ($paths.Count -gt 0)
        if ($result.pathFound) {
            if (-not $operatorApprovedBool) {
                $result.candidateTaskCount = $paths.Count
                $result.admissionState = 'REFUSED_OPERATOR_APPROVAL_REQUIRED'
                break
            }
            foreach ($p in $paths) {
                Add-IdleTask -QueuePath $QueuePath -Task (New-IdleTask -Title "Validate project $($p.name)" `
                    -Description "Run non-destructive composite validation for $($p.path)." `
                    -Priority 9 -Risk 1 -SourceProject $p.name -NextAction 'Record validation result and queue safe follow-up.' `
                    -Payload @{ actionType = 'validate-project'; projectPath = $p.path }) | Out-Null
                $result.tasksAdded++
            }
        }
    }
}

$result | ConvertTo-Json -Depth 10 | Set-Content -Path (Join-Path $ArtifactRoot 'dunce-adapter-result.json') -Force
$result | ConvertTo-Json -Depth 10
