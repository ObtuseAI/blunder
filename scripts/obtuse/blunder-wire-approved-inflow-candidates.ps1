#requires -Version 5.1
<#
.SYNOPSIS
    Wire approved Blunder inflow candidates into the live idle autonomy queue.

.DESCRIPTION
    Reads the proof-native promotion bridge ledger, promotes only eligible active
    skill tokens, writes durable active-token/registry ledgers, and inserts
    duplicate-guarded low-risk idle tasks. No provider calls, benchmark launches,
    or source mutations are performed.
#>
[CmdletBinding()]
param(
    [ValidateSet('Validate','Wire','ReportOnly')]
    [string]$Mode = 'Validate',
    [string]$QueuePath = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle/idle-task-queue.json',
    [string]$IdleArtifactRoot = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle',
    [string]$InflowArtifactRoot = 'artifacts/blunder/inflow',
    [string]$ReportRoot = 'artifacts/blunder/inflow/real-autonomy-wiring'
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$queueModule = Join-Path $PSScriptRoot 'idle-task-queue.ps1'
$script:__wireAction = 'List'
$script:__wireQueuePath = $QueuePath
. $queueModule
$QueuePath = $script:__wireQueuePath

function Resolve-RepoPath {
    param([Parameter(Mandatory)] [string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $repoRoot $Path
}

function Write-JsonFile {
    param([Parameter(Mandatory)] [object]$Value, [Parameter(Mandatory)] [string]$Path)
    $dir = Split-Path $Path -Parent
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    $Value | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $Path -Encoding UTF8 -Force
}

function Add-JsonLine {
    param([Parameter(Mandatory)] [string]$Path, [Parameter(Mandatory)] [object]$Record)
    $dir = Split-Path $Path -Parent
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    Add-Content -LiteralPath $Path -Encoding UTF8 -Value ($Record | ConvertTo-Json -Depth 30 -Compress)
}

function Read-JsonLines {
    param([Parameter(Mandatory)] [string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "JSONL file not found: $Path"
    }
    $records = New-Object System.Collections.ArrayList
    $lineNumber = 0
    foreach ($line in Get-Content -LiteralPath $Path) {
        $lineNumber++
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        try {
            [void]$records.Add(($line | ConvertFrom-Json -ErrorAction Stop))
        }
        catch {
            throw "Invalid JSONL at $Path line $lineNumber`: $($_.Exception.Message)"
        }
    }
    return @($records)
}

function Get-CandidateKind {
    param([Parameter(Mandatory)] [string]$CandidateId)
    if ($CandidateId -like '*-validation-rule') { return 'validation-rule' }
    if ($CandidateId -like '*-benchmark-drill') { return 'benchmark-drill' }
    if ($CandidateId -like '*-agent-antipattern') { return 'agent-antipattern' }
    if ($CandidateId -like '*-workflow-pattern') { return 'workflow-pattern' }
    return 'general'
}

function New-WiredTaskPayload {
    param([Parameter(Mandatory)] [object]$Candidate, [Parameter(Mandatory)] [string]$Kind)
    $base = @{
        candidateId = [string]$Candidate.candidate_id
        sourceRecordId = [string]$Candidate.source_record_id
        skillTokenId = [string]$Candidate.skill_token_id
        activeSkillToken = $true
        promotionStatus = [string]$Candidate.promotion_status
        promotionReason = [string]$Candidate.reason
        replenishmentPolicyId = 'inflow-approved-v2'
        replenishmentCatalogId = "inflow-approved-$($Candidate.candidate_id)"
        suppressFollowUp = $true
        sourceMutationAllowed = $false
        providerCallsAllowed = $false
        destructiveActionsAllowed = $false
        liveBenchmarkLaunchAllowed = $false
    }

    switch ($Kind) {
        'validation-rule' {
            $base['actionType'] = 'validate-json'
            $base['targetPath'] = $InflowArtifactRoot
        }
        'benchmark-drill' {
            $base['actionType'] = 'inspect-artifacts'
            $base['targetPath'] = 'artifacts/obtuse'
            $base['drillMode'] = 'offline-prep-only'
        }
        'agent-antipattern' {
            $base['actionType'] = 'improve-idle-loop'
            $base['targetPath'] = $IdleArtifactRoot
        }
        'workflow-pattern' {
            $base['actionType'] = 'monitor-health'
            $base['targetPath'] = $IdleArtifactRoot
        }
        default {
            $base['actionType'] = 'inspect-artifacts'
            $base['targetPath'] = $InflowArtifactRoot
        }
    }
    return $base
}

function New-WiredIdleTask {
    param([Parameter(Mandatory)] [object]$Candidate)
    $kind = Get-CandidateKind -CandidateId ([string]$Candidate.candidate_id)
    $payload = New-WiredTaskPayload -Candidate $Candidate -Kind $kind
    $title = "Apply approved inflow token $($kind) $(([string]$Candidate.candidate_id).Substring(0, 8))"
    $task = New-IdleTask -Title $title `
        -Description 'Approved inflow token converted to a local idle autonomy task with proof-only effects.' `
        -Priority $(if ($kind -eq 'validation-rule') { 9 } elseif ($kind -eq 'benchmark-drill') { 8 } else { 7 }) `
        -Risk 1 `
        -SourceProject 'obtuse/blunder-inflow' `
        -NextAction 'Consume approved token through existing safe idle action and write proof evidence.' `
        -Payload $payload
    $task['candidateId'] = [string]$Candidate.candidate_id
    $task['skillTokenId'] = [string]$Candidate.skill_token_id
    $task['candidateKind'] = $kind
    $task['generationSignature'] = "inflow-approved-v2|$($Candidate.candidate_id)"
    $task['replenishmentPolicyId'] = 'inflow-approved-v2'
    $task['replenishmentCatalogId'] = "inflow-approved-$($Candidate.candidate_id)"
    return $task
}

function Test-TaskAlreadyWired {
    param([array]$Tasks, [Parameter(Mandatory)] [string]$CandidateId)
    $signature = "inflow-approved-v2|$CandidateId"
    foreach ($task in $Tasks) {
        if (($task.PSObject.Properties.Name -contains 'generationSignature') -and [string]$task.generationSignature -eq $signature) {
            return $true
        }
        if (($task.PSObject.Properties.Name -contains 'candidateId') -and [string]$task.candidateId -eq $CandidateId) {
            return $true
        }
        if ($task.payload -and ($task.payload.PSObject.Properties.Name -contains 'candidateId') -and [string]$task.payload.candidateId -eq $CandidateId) {
            return $true
        }
    }
    return $false
}

function Get-ExistingWiredTask {
    param([array]$Tasks, [Parameter(Mandatory)] [string]$CandidateId)
    $signature = "inflow-approved-v2|$CandidateId"
    foreach ($task in $Tasks) {
        if (($task.PSObject.Properties.Name -contains 'generationSignature') -and [string]$task.generationSignature -eq $signature) {
            return $task
        }
        if (($task.PSObject.Properties.Name -contains 'candidateId') -and [string]$task.candidateId -eq $CandidateId) {
            return $task
        }
        if ($task.payload -and ($task.payload.PSObject.Properties.Name -contains 'candidateId') -and [string]$task.payload.candidateId -eq $CandidateId) {
            return $task
        }
    }
    return $null
}

Push-Location $repoRoot
try {
    $resolvedQueuePath = Initialize-IdleQueue -Path $QueuePath
    $resolvedInflowRoot = Resolve-RepoPath -Path $InflowArtifactRoot
    $resolvedReportRoot = Resolve-RepoPath -Path $ReportRoot
    $promotionLedgerPath = Join-Path $resolvedInflowRoot 'promotion_bridge_ledger.jsonl'
    $capabilityLedgerPath = Join-Path $resolvedInflowRoot 'capability_candidates.jsonl'
    $tokenLedgerPath = Join-Path $resolvedInflowRoot 'active_skill_tokens.jsonl'
    $registryPath = Join-Path $resolvedInflowRoot 'active_capability_registry.json'
    $wiringLedgerPath = Join-Path $resolvedReportRoot 'idle_candidate_wiring_ledger.jsonl'
    $reportPath = Join-Path $resolvedReportRoot 'real_autonomy_wiring_report.json'

    $promotionRecords = Read-JsonLines -Path $promotionLedgerPath
    $capabilityRecords = if (Test-Path -LiteralPath $capabilityLedgerPath) { Read-JsonLines -Path $capabilityLedgerPath } else { @() }
    $approved = @($promotionRecords | Where-Object {
        $_.eligible -eq $true -and
        $_.active_skill_token -eq $true -and
        $_.promotion_status -eq 'PROMOTED_AUTONOMOUS_THRESHOLD_PASS'
    })
    $queue = Get-IdleQueue -Path $resolvedQueuePath
    $tasks = @($queue.tasks)
    $added = New-Object System.Collections.ArrayList
    $existing = New-Object System.Collections.ArrayList
    $reconciled = New-Object System.Collections.ArrayList
    $preparedTasks = New-Object System.Collections.ArrayList

    foreach ($candidate in $approved) {
        $candidateId = [string]$candidate.candidate_id
        $task = New-WiredIdleTask -Candidate $candidate
        [void]$preparedTasks.Add($task)
        $existingTask = Get-ExistingWiredTask -Tasks $tasks -CandidateId $candidateId
        if ($existingTask) {
            [void]$existing.Add($candidateId)
            if ($Mode -eq 'Wire') {
                $kind = Get-CandidateKind -CandidateId $candidateId
                $payload = New-WiredTaskPayload -Candidate $candidate -Kind $kind
                if ($null -eq $existingTask.payload) {
                    $existingTask | Add-Member -NotePropertyName payload -NotePropertyValue ([pscustomobject]@{}) -Force
                }
                foreach ($key in $payload.Keys) {
                    if ($existingTask.payload.PSObject.Properties.Name -contains $key) {
                        $existingTask.payload.$key = $payload[$key]
                    }
                    else {
                        $existingTask.payload | Add-Member -NotePropertyName $key -NotePropertyValue $payload[$key] -Force
                    }
                }
                if ($existingTask.PSObject.Properties.Name -contains 'candidateKind') {
                    $existingTask.candidateKind = $kind
                }
                else {
                    $existingTask | Add-Member -NotePropertyName candidateKind -NotePropertyValue $kind -Force
                }
                if ($existingTask.PSObject.Properties.Name -contains 'updatedAt') {
                    $existingTask.updatedAt = (Get-Date -Format 'o')
                }
                if ($kind -eq 'validation-rule' -and $existingTask.status -in @('failed','blocked','quarantined')) {
                    $existingTask.status = 'pending'
                    $existingTask.simulationResult = $null
                    $existingTask.executionResult = $null
                    $existingTask.validationResult = $null
                    $existingTask.nextAction = 'Retry approved inflow validation against scoped proof root.'
                }
                [void]$reconciled.Add([ordered]@{
                    candidateId = $candidateId
                    taskId = $existingTask.id
                    status = $existingTask.status
                    actionType = $existingTask.payload.actionType
                    targetPath = $existingTask.payload.targetPath
                })
                Add-JsonLine -Path $wiringLedgerPath -Record ([ordered]@{
                    timestamp = (Get-Date -Format 'o')
                    event = 'RECONCILED_EXISTING_IDLE_QUEUE_TASK'
                    candidateId = $candidateId
                    skillTokenId = [string]$candidate.skill_token_id
                    taskId = $existingTask.id
                    actionType = $existingTask.payload.actionType
                    targetPath = $existingTask.payload.targetPath
                    queuePath = $resolvedQueuePath
                })
            }
            continue
        }
        if ($Mode -eq 'Wire') {
            Add-IdleTask -QueuePath $resolvedQueuePath -Task $task | Out-Null
            [void]$added.Add([ordered]@{
                candidateId = $candidateId
                taskId = $task.id
                actionType = $task.payload.actionType
                generationSignature = $task.generationSignature
            })
            Add-JsonLine -Path $wiringLedgerPath -Record ([ordered]@{
                timestamp = (Get-Date -Format 'o')
                event = 'WIRED_TO_IDLE_QUEUE'
                candidateId = $candidateId
                skillTokenId = [string]$candidate.skill_token_id
                taskId = $task.id
                actionType = $task.payload.actionType
                queuePath = $resolvedQueuePath
            })
        }
    }
    if ($Mode -eq 'Wire' -and $reconciled.Count -gt 0) {
        Save-IdleQueue -Queue $queue -Path $resolvedQueuePath
    }

    if ($Mode -eq 'Wire') {
        if (Test-Path -LiteralPath $tokenLedgerPath) { Remove-Item -LiteralPath $tokenLedgerPath -Force }
        foreach ($candidate in $approved) {
            Add-JsonLine -Path $tokenLedgerPath -Record ([ordered]@{
                timestamp = (Get-Date -Format 'o')
                tokenStatus = 'active'
                candidateId = [string]$candidate.candidate_id
                sourceRecordId = [string]$candidate.source_record_id
                skillTokenId = [string]$candidate.skill_token_id
                promotionReason = [string]$candidate.reason
                rollbackPathRequired = [bool]$candidate.rollback_path_required
                demotionPathRequired = [bool]$candidate.demotion_path_required
                idleQueueSignature = "inflow-approved-v2|$($candidate.candidate_id)"
            })
        }
    }

    $registry = [ordered]@{
        schema = 'blunder.inflow.active_capability_registry.v1'
        timestamp = (Get-Date -Format 'o')
        mode = $Mode
        queuePath = $resolvedQueuePath
        inflowArtifactRoot = $resolvedInflowRoot
        approvedCandidateCount = $approved.Count
        preparedTaskCount = $preparedTasks.Count
        addedTaskCount = $added.Count
        alreadyWiredCount = $existing.Count
        reconciledTaskCount = $reconciled.Count
        activeSkillTokensPath = $tokenLedgerPath
        wiringLedgerPath = $wiringLedgerPath
        liveBenchmarkLaunched = $false
        providerCallsAllowed = $false
        sourceMutationAllowed = $false
        destructiveActionsAllowed = $false
        candidates = @($approved | ForEach-Object {
            $candidate = $_
            $kind = Get-CandidateKind -CandidateId ([string]$candidate.candidate_id)
            $sourceCapability = @($capabilityRecords | Where-Object { [string]$_.candidate_id -eq [string]$candidate.candidate_id } | Select-Object -First 1)
            [ordered]@{
                candidateId = [string]$candidate.candidate_id
                sourceRecordId = [string]$candidate.source_record_id
                skillTokenId = [string]$candidate.skill_token_id
                kind = $kind
                targetType = $(if ($sourceCapability.Count -gt 0) { [string]$sourceCapability[0].target_type } else { $kind })
                idleActionType = (New-WiredTaskPayload -Candidate $candidate -Kind $kind).actionType
                queueSignature = "inflow-approved-v2|$($candidate.candidate_id)"
            }
        })
    }
    if ($Mode -eq 'Wire') {
        Write-JsonFile -Value $registry -Path $registryPath
    }

    $finalQueue = Get-IdleQueue -Path $resolvedQueuePath
    $stats = Get-QueueStats -QueuePath $resolvedQueuePath
    $report = [ordered]@{
        classification = 'BLUNDER_APPROVED_INFLOW_CANDIDATES_REAL_AUTONOMY_WIRING_V1'
        status = $(if ($approved.Count -gt 0) { 'PASS' } else { 'NO_APPROVED_CANDIDATES' })
        mode = $Mode
        timestamp = (Get-Date -Format 'o')
        repoRoot = $repoRoot
        queuePath = $resolvedQueuePath
        inflowArtifactRoot = $resolvedInflowRoot
        reportRoot = $resolvedReportRoot
        approvedCandidateCount = $approved.Count
        addedTaskCount = $added.Count
        alreadyWiredCount = $existing.Count
        reconciledTaskCount = $reconciled.Count
        queueTaskCount = @($finalQueue.tasks).Count
        queueStats = $stats
        activeSkillTokensPath = $tokenLedgerPath
        activeCapabilityRegistryPath = $registryPath
        wiringLedgerPath = $wiringLedgerPath
        liveBenchmarkLaunched = $false
        providerCallsMade = $false
        sourceMutationPerformed = $false
        destructiveActionPerformed = $false
        addedTasks = @($added)
        reconciledTasks = @($reconciled)
        existingCandidates = @($existing)
    }
    Write-JsonFile -Value $report -Path $reportPath
    $report | ConvertTo-Json -Depth 30
}
finally {
    Pop-Location
}
