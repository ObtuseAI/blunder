#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Validate','ReplenishOnce','ReportOnly')]
    [string]$Mode = 'Validate',
    [string]$QueuePath = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle/idle-task-queue.json',
    [string]$ArtifactRoot = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle',
    [switch]$OperatorApproved,
    [switch]$Force,
    [int]$CooldownSeconds = 600,
    [int]$BatchSize = 1,
    [string[]]$DunceCandidatePaths = @(
        'artifacts/obtuse/blunder-dunce-hypothesis-promotion-check-v1/002_ARTIFACT_ONLY_QUEUE_CANDIDATES.json',
        'artifacts/obtuse/blunder-dunce-utility-scoring-v1/002_NON_DUPLICATE_QUEUE_CANDIDATES.json',
        'artifacts/obtuse/blunder-dunce-regression-substrate-idle-cycle-v1/001_SUBSTRATE_QUEUE_CANDIDATES.json'
    ),
    [string]$DunceCandidatePath = 'artifacts/obtuse/blunder-dunce-hypothesis-promotion-check-v1/002_ARTIFACT_ONLY_QUEUE_CANDIDATES.json',
    [string]$DunceCandidateTargetPath = ''
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$queueModule = Join-Path $PSScriptRoot 'idle-task-queue.ps1'
$script:__replQueuePath = $QueuePath
$script:__replArtifactRoot = $ArtifactRoot
. $queueModule
$QueuePath = $script:__replQueuePath
$ArtifactRoot = $script:__replArtifactRoot

function Resolve-LocalPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $repoRoot $Path
}

function Add-LedgerEntry {
    param([string]$Path, [object]$Record)
    $target = Resolve-LocalPath -Path $Path
    $dir = Split-Path $target -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $Record | ConvertTo-Json -Depth 20 -Compress | Add-Content -Path $target -Encoding UTF8
}

function Read-JsonFile {
    param([string]$Path)
    $target = Resolve-LocalPath -Path $Path
    if (-not (Test-Path -LiteralPath $target)) { return $null }
    return Get-Content -Raw -LiteralPath $target | ConvertFrom-Json -ErrorAction Stop
}

function Get-DunceArtifactCandidateCatalog {
    $rows = New-Object System.Collections.ArrayList
    $paths = @($DunceCandidatePaths)
    if ($DunceCandidatePath -and $paths -notcontains $DunceCandidatePath) { $paths += $DunceCandidatePath }
    foreach ($candidatePath in $paths) {
        $doc = Read-JsonFile -Path $candidatePath
        if (-not $doc -or -not $doc.queueCandidates) { continue }
        foreach ($candidate in @($doc.queueCandidates)) {
            if ($candidate.sourceMutationAllowed -or $candidate.providerCallsAllowed -or $candidate.destructiveActionsAllowed) { continue }
            $candidateKey = if ($candidate.taskId) { [string]$candidate.taskId } elseif ($candidate.candidateId) { [string]$candidate.candidateId } else { ([string]$candidate.title -replace '[^A-Za-z0-9]+','-').Trim('-').ToLowerInvariant() }
            $targetPath = if ($candidate.targetPath) { Resolve-LocalPath -Path ([string]$candidate.targetPath) } elseif ($DunceCandidateTargetPath) { Resolve-LocalPath -Path $DunceCandidateTargetPath } else { Resolve-LocalPath -Path (Split-Path $candidatePath -Parent) }
            $policyId = if ($doc.sourceHypothesis -eq 'DUNCE-HYP-005' -or $candidate.generationSignature -like 'dunce-utility-v1*') { 'dunce-utility-v1' } else { 'dunce-artifact-v1' }
            [void]$rows.Add([ordered]@{
            id = "$policyId-$candidateKey"
            title = [string]$candidate.title
            description = "Operator-triggered Dunce artifact-only candidate. Test: $($candidate.measurableTest)"
            priority = 8
            risk = 1
            sourceProject = 'dunce/showmeai'
            nextAction = [string]$candidate.successMetric
            payload = [ordered]@{
                actionType = 'inspect-artifacts'
                targetPath = $targetPath
                controlLane = [string]$candidate.controlLane
                replenishmentPolicyId = $policyId
                replenishmentCatalogId = "$policyId-$candidateKey"
                suppressFollowUp = $true
                operatorTriggeredReplenishment = $true
                proofRequired = $true
                sourceMutationAllowed = $false
                providerCallsAllowed = $false
                destructiveActionsAllowed = $false
                stopCondition = [string]$candidate.stopCondition
            }
        })
        }
    }
    return @($rows)
}

$ledgerPath = Join-Path $ArtifactRoot 'queue_replenishment_command_ledger.jsonl'
$summaryPath = Join-Path $ArtifactRoot 'queue_replenishment_command_summary.json'
$replenishmentStatePath = Join-Path $ArtifactRoot 'idle-replenishment-state.json'

$now = Get-Date -Format 'o'

# Load queue stats
$queue = Get-IdleQueue -Path $QueuePath
$stats = Get-QueueStats -QueuePath $QueuePath
$pending = [int]$stats.pending
$running = [int]$stats.running
$safeExecutable = $pending + $running
$disabled = [int]$stats.disabled
$blocked = [int]$stats.blocked
$quarantined = [int]$stats.quarantined
$failed = [int]$stats.failed

# Load replenishment state
$replState = $null
if (Test-Path (Resolve-LocalPath -Path $replenishmentStatePath)) {
    try { $replState = Get-Content (Resolve-LocalPath -Path $replenishmentStatePath) -Raw | ConvertFrom-Json } catch { $replState = $null }
}
$lastReplenishedAt = if ($replState -and $replState.lastReplenishedAt) { $replState.lastReplenishedAt } else { $null }
$cooldownRemaining = $null
if ($lastReplenishedAt) {
    try {
        $last = [datetime]$lastReplenishedAt
        $age = ([datetime](Get-Date) - $last).TotalSeconds
        $cooldownRemaining = [math]::Max(0, $CooldownSeconds - $age)
    } catch {}
}

$canReplenish = $true
$reasons = @()
if ($safeExecutable -gt 0) { $canReplenish = $false; $reasons += 'SAFE_EXECUTABLE_WORK_EXISTS' }
if ($cooldownRemaining -and $cooldownRemaining -gt 0 -and -not $Force) { $canReplenish = $false; $reasons += 'COOLDOWN_ACTIVE' }
if (-not $OperatorApproved) { $canReplenish = $false; $reasons += 'OPERATOR_APPROVAL_MISSING' }

$catalog = @()
$catalog += Get-DunceArtifactCandidateCatalog
$catalog += @(
    [ordered]@{
        id = 'idle-control-syntax'
        title = 'Replenishment validate idle control syntax'
        description = 'Operator-triggered safe replenishment task: validate local idle-control PowerShell syntax.'
        priority = 6
        risk = 1
        sourceProject = 'obtuse'
        nextAction = 'Validate idle-control syntax and do not generate a follow-up.'
        payload = [ordered]@{
            actionType = 'validate-syntax'
            replenishmentPolicyId = 'non-churning-v1'
            replenishmentCatalogId = 'idle-control-syntax'
            suppressFollowUp = $true
            operatorTriggeredReplenishment = $true
            proofRequired = $true
        }
    },
    [ordered]@{
        id = 'idle-artifact-json'
        title = 'Replenishment validate idle artifact JSON health'
        description = 'Operator-triggered safe replenishment task: validate local Obtuse artifact JSON parseability.'
        priority = 5
        risk = 1
        sourceProject = 'obtuse'
        nextAction = 'Validate artifact JSON and do not generate a follow-up.'
        payload = [ordered]@{
            actionType = 'validate-json'
            replenishmentPolicyId = 'non-churning-v1'
            replenishmentCatalogId = 'idle-artifact-json'
            suppressFollowUp = $true
            operatorTriggeredReplenishment = $true
            proofRequired = $true
        }
    },
    [ordered]@{
        id = 'idle-queue-health'
        title = 'Replenishment idle queue health snapshot'
        description = 'Operator-triggered safe replenishment task: capture queue and idle-state health without spawning recursive work.'
        priority = 4
        risk = 1
        sourceProject = 'obtuse'
        nextAction = 'Capture queue health and do not generate a follow-up.'
        payload = [ordered]@{
            actionType = 'monitor-health'
            replenishmentPolicyId = 'non-churning-v1'
            replenishmentCatalogId = 'idle-queue-health'
            suppressFollowUp = $true
            operatorTriggeredReplenishment = $true
            proofRequired = $true
        }
    },
    [ordered]@{
        id = 'idle-mega-proof-manifest'
        title = 'Replenishment validate mega bundle proof manifest'
        description = 'Operator-triggered safe replenishment task: validate the mega finish active appliance bundle proof manifest.'
        priority = 5
        risk = 1
        sourceProject = 'obtuse'
        nextAction = 'Validate proof manifest and do not generate a follow-up.'
        payload = [ordered]@{
            actionType = 'validate-proof-manifest'
            replenishmentPolicyId = 'non-churning-v1'
            replenishmentCatalogId = 'idle-mega-proof-manifest'
            suppressFollowUp = $true
            operatorTriggeredReplenishment = $true
            proofRequired = $true
        }
    },
    [ordered]@{
        id = 'idle-mega-active-review'
        title = 'Replenishment validate active idle start readiness'
        description = 'Operator-triggered safe replenishment task: validate active idle start readiness gates.'
        priority = 5
        risk = 1
        sourceProject = 'obtuse'
        nextAction = 'Validate active start readiness and do not generate a follow-up.'
        payload = [ordered]@{
            actionType = 'validate-active-readiness'
            replenishmentPolicyId = 'non-churning-v1'
            replenishmentCatalogId = 'idle-mega-active-review'
            suppressFollowUp = $true
            operatorTriggeredReplenishment = $true
            proofRequired = $true
        }
    },
    [ordered]@{
        id = 'idle-mega-status-check'
        title = 'Replenishment capture idle status snapshot'
        description = 'Operator-triggered safe replenishment task: capture a fresh idle status snapshot for evidence.'
        priority = 4
        risk = 1
        sourceProject = 'obtuse'
        nextAction = 'Capture status snapshot and do not generate a follow-up.'
        payload = [ordered]@{
            actionType = 'capture-status'
            replenishmentPolicyId = 'non-churning-v1'
            replenishmentCatalogId = 'idle-mega-status-check'
            suppressFollowUp = $true
            operatorTriggeredReplenishment = $true
            proofRequired = $true
        }
    }
)

function Get-ExistingSignatureCount {
    param([string]$Signature)
    $count = 0
    foreach ($task in @($queue.tasks)) {
        $existingSig = if ($task.generationSignature) { [string]$task.generationSignature } else { '' }
        if ($existingSig -and $existingSig -eq $Signature) { $count++ }
    }
    return $count
}

function Test-SignatureBlockedByHistory {
    param([string]$Signature)
    foreach ($task in @($queue.tasks)) {
        $existingSig = if ($task.generationSignature) { [string]$task.generationSignature } else { '' }
        if ($existingSig -and $existingSig -eq $Signature -and $task.status -in @('disabled','passed','failed','quarantined')) {
            return $true
        }
    }
    return $false
}

function Test-CatalogEntryBlockedByHistory {
    param([object]$Entry, [string]$Signature)
    foreach ($task in @($queue.tasks)) {
        $existingSig = if ($task.generationSignature) { [string]$task.generationSignature } else { '' }
        $existingTitle = if ($task.title) { [string]$task.title } else { '' }
        if ($existingSig -and $existingSig -eq $Signature -and $task.status -in @('disabled','passed','failed','quarantined','pending','running')) {
            return $true
        }
        if ($existingTitle -and $existingTitle -eq [string]$Entry.title -and $task.status -in @('passed','failed','quarantined','pending','running')) {
            return $true
        }
    }
    return $false
}

function Get-ReplenishmentSignature {
    param([object]$Entry)
    $policy = if ($Entry.payload.replenishmentPolicyId) { [string]$Entry.payload.replenishmentPolicyId } else { 'operator-triggered-v1' }
    return "replenishment|$policy|$($Entry.id)"
}

$addedTasks = @()
$decision = 'SKIP'
$decisionReason = ($reasons -join '; ')
if ($Mode -eq 'ReplenishOnce' -and $canReplenish) {
    $batchSize = [math]::Max(1, [math]::Min($BatchSize, $catalog.Count))
    $cursor = if ($replState -and $replState.cursor -ne $null) { [int]$replState.cursor } else { 0 }
    for ($candidateIndex = 0; $candidateIndex -lt $catalog.Count; $candidateIndex++) {
        $candidateEntry = $catalog[$candidateIndex]
        if ($candidateEntry.id -notlike 'dunce-*') { continue }
        $candidateSignature = Get-ReplenishmentSignature -Entry $candidateEntry
        if (-not (Test-SignatureBlockedByHistory -Signature $candidateSignature)) {
            $cursor = $candidateIndex
            break
        }
    }
    $attempts = 0
    while ($addedTasks.Count -lt $batchSize -and $attempts -lt $catalog.Count) {
        $entry = $catalog[$cursor % $catalog.Count]
        $policy = if ($entry.payload.replenishmentPolicyId) { [string]$entry.payload.replenishmentPolicyId } else { 'operator-triggered-v1' }
        $signature = Get-ReplenishmentSignature -Entry $entry
        $cursor = ($cursor + 1) % $catalog.Count
        $attempts++
        if ((Get-ExistingSignatureCount -Signature $signature) -ge 2) {
            $decision = 'REJECT_DUPLICATE'
            $decisionReason = 'Operator-triggered replenishment signature already appears twice in queue history'
            continue
        }
        if (Test-CatalogEntryBlockedByHistory -Entry $entry -Signature $signature) {
            $decision = 'REJECT_BLOCKED_HISTORY'
            $decisionReason = 'Operator-triggered replenishment signature matches a disabled/passed/failed/quarantined blocker'
            continue
        }
        $task = New-IdleTask -Title $entry.title -Description $entry.description `
            -Priority ([int]$entry.priority) -Risk ([int]$entry.risk) -SourceProject $entry.sourceProject `
            -NextAction $entry.nextAction -Payload $entry.payload
        $task['generationSignature'] = $signature
        $task['replenishmentPolicyId'] = $policy
        $task['replenishmentCatalogId'] = $entry.id
        $task['operatorTriggeredReplenishment'] = $true
        $task['replenishmentReason'] = 'No safe executable work in blocked queue; operator-triggered safe task.'
        Add-IdleTask -QueuePath $QueuePath -Task $task | Out-Null
        $addedTasks += [ordered]@{
            taskId = $task.id
            title = $task.title
            catalogId = $entry.id
            actionType = $entry.payload.actionType
            signature = $signature
            source = 'operator-triggered-replenishment-v1'
            reason = $task.replenishmentReason
        }
    }
    if ($addedTasks.Count -gt 0) {
        $decision = 'ADMIT_REPLENISHMENT_BATCH'
        $decisionReason = 'Operator-triggered replenishment admitted one safe local validation task.'
        $updatedState = [ordered]@{
            version = '1'
            cursor = $cursor
            lastReplenishedAt = $now
            policy = 'non-churning-v1'
            cooldownSeconds = $CooldownSeconds
            batchSize = $addedTasks.Count
            operatorTriggered = $true
        }
        $updatedState | ConvertTo-Json -Depth 10 | Set-Content -Path (Resolve-LocalPath -Path $replenishmentStatePath) -Force
    }
}

$ledgerEntry = [ordered]@{
    timestamp = $now
    mode = $Mode
    operatorApproved = [bool]$OperatorApproved
    decision = $decision
    decisionReason = $decisionReason
    addedTaskCount = $addedTasks.Count
    addedTasks = $addedTasks
    queueStatsBefore = $stats
    cooldownSeconds = $CooldownSeconds
    cooldownRemainingSeconds = $(if ($cooldownRemaining -ne $null) { [math]::Round($cooldownRemaining,3) } else { $null })
    canReplenish = $canReplenish
    blockers = $reasons
}
Add-LedgerEntry -Path $ledgerPath -Record $ledgerEntry

$queueAfter = Get-IdleQueue -Path $QueuePath
$statsAfter = Get-QueueStats -QueuePath $QueuePath

$summary = [ordered]@{
    classification = 'OBTUSE_BLUNDER_IDLE_OPERATOR_TRIGGERED_QUEUE_REPLENISHMENT_V1'
    timestamp = $now
    mode = $Mode
    operatorApproved = [bool]$OperatorApproved
    result = $(if ($Mode -eq 'Validate' -or ($Mode -eq 'ReportOnly')) { 'PASS' } elseif ($addedTasks.Count -gt 0) { 'PASS' } elseif ($decision -in @('SKIP','REJECT_DUPLICATE','REJECT_BLOCKED_HISTORY','COOLDOWN_ACTIVE')) { 'NO_OP' } else { 'NO_OP' })
    decision = $decision
    decisionReason = $decisionReason
    addedTaskCount = $addedTasks.Count
    addedTasks = $addedTasks
    queueStatsBefore = $stats
    queueStatsAfter = $statsAfter
    cooldownSeconds = $CooldownSeconds
    cooldownRemainingSeconds = $(if ($cooldownRemaining -ne $null) { [math]::Round($cooldownRemaining,3) } else { $null })
    canReplenish = $canReplenish
    blockers = $reasons
    ledgerPath = $ledgerPath
}

$summary | ConvertTo-Json -Depth 10 | Set-Content -Path (Resolve-LocalPath -Path $summaryPath) -Force
$summary | ConvertTo-Json -Depth 10
