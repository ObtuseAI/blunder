#requires -Version 5.1
<#
.SYNOPSIS
    OBTUSE_BLUNDER_LOCAL_FRANKENSTEIN_OPERATIONAL_CLOSURE_V1 bridge.

.DESCRIPTION
    Generates safe, local, artifact/proof/validation/queue/self-coding oriented
    idle-task candidates from prior Dunce missions, deduplicates them against the
    live queue and a candidate ledger, optionally admits them to the idle queue,
    and runs a smoke test through idle-mode.ps1 -Action RunOnce.
#>
[CmdletBinding()]
param(
    [ValidateSet('Validate','GenerateCandidates','AdmitSafe','RunSmoke','ReportOnly')]
    [string]$Mode = 'Validate',
    [string]$OperatorApproved = 'false',
    [string]$QueuePath = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle/idle-task-queue.json',
    [string]$ArtifactRoot = 'artifacts/obtuse/blunder-local-frankenstein-operational-closure-v1'
)

$ErrorActionPreference = 'Stop'
$script:__bridgeMode = $Mode
$script:__bridgeOperatorApproved = $OperatorApproved
$script:__bridgeQueuePath = $QueuePath
$script:__bridgeArtifactRoot = $ArtifactRoot

# ---------------------------------------------------------------------------
# Repo resolution helpers
# ---------------------------------------------------------------------------
$script:__repoRoot = $null
function Resolve-RepoPath {
    param([string]$RelativePath)
    if ([System.IO.Path]::IsPathRooted($RelativePath)) { return $RelativePath }
    if ($null -eq $script:__repoRoot) {
        $scriptDir = $PSScriptRoot
        if (-not $scriptDir) { $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
        # script is at scripts/obtuse, repo root is two levels up
        $script:__repoRoot = Split-Path (Split-Path $scriptDir -Parent) -Parent
    }
    return Join-Path $script:__repoRoot $RelativePath
}

function Test-RepoPath {
    param([string]$RelativePath)
    return Test-Path (Resolve-RepoPath -RelativePath $RelativePath)
}

function Read-RepoJson {
    param([string]$RelativePath)
    $fullPath = Resolve-RepoPath -RelativePath $RelativePath
    if (-not (Test-Path $fullPath)) { return $null }
    $raw = Get-Content $fullPath -Raw -ErrorAction Stop
    if ([string]::IsNullOrWhiteSpace($raw)) { return $null }
    return $raw | ConvertFrom-Json -ErrorAction Stop
}

# ---------------------------------------------------------------------------
# Queue module
# ---------------------------------------------------------------------------
$queueModule = Resolve-RepoPath -RelativePath 'scripts/obtuse/idle-task-queue.ps1'
if (-not (Test-Path $queueModule)) { throw "Queue module not found: $queueModule" }
. $queueModule
$Mode = $script:__bridgeMode
$OperatorApproved = $script:__bridgeOperatorApproved
$QueuePath = $script:__bridgeQueuePath
$ArtifactRoot = $script:__bridgeArtifactRoot

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
$operatorApprovedBool = ($OperatorApproved -eq 'true')
$artifactRoot = Resolve-RepoPath -RelativePath $ArtifactRoot
if (-not (Test-Path $artifactRoot)) { New-Item -ItemType Directory -Force -Path $artifactRoot | Out-Null }

$ledgerPath     = Join-Path $artifactRoot 'dunce_to_self_coding_candidate_ledger.jsonl'
$admissionPath  = Join-Path $artifactRoot 'dunce_to_self_coding_admission_summary.json'
$smokePath      = Join-Path $artifactRoot 'dunce_to_self_coding_smoke_summary.json'

$queueFullPath = Resolve-RepoPath -RelativePath $QueuePath

$idleModeScript = Resolve-RepoPath -RelativePath 'scripts/obtuse/idle-mode.ps1'

# Candidate source definitions (relative to repo root)
$sources = [ordered]@{
    crossMachineFinalReport = 'artifacts/obtuse/blunder-dunce-cross-machine-proof-regression-verification-v1/FINAL_REPORT.json'
    crossMachineManifest    = 'artifacts/obtuse/blunder-dunce-cross-machine-proof-regression-verification-v1/portable_regression_pack_manifest.json'
    substrateFinalReport    = 'artifacts/obtuse/blunder-dunce-regression-substrate-idle-cycle-v1/FINAL_REPORT.json'
    utilityCandidates       = 'artifacts/obtuse/blunder-dunce-utility-scoring-v1/002_NON_DUPLICATE_QUEUE_CANDIDATES.json'
    hypothesisCandidates    = 'artifacts/obtuse/blunder-dunce-hypothesis-promotion-check-v1/002_ARTIFACT_ONLY_QUEUE_CANDIDATES.json'
    substrateCandidates     = 'artifacts/obtuse/blunder-dunce-regression-substrate-idle-cycle-v1/001_SUBSTRATE_QUEUE_CANDIDATES.json'
}

# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------
function Write-JsonFile {
    param([Parameter(Mandatory)] [object]$Value, [Parameter(Mandatory)] [string]$Path)
    $dir = Split-Path $Path -Parent
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $json = $Value | ConvertTo-Json -Depth 10
    [System.IO.File]::WriteAllText($Path, $json, [System.Text.Encoding]::UTF8)
}

function Add-JsonLine {
    param([Parameter(Mandatory)] [string]$Path, [Parameter(Mandatory)] [object]$Record)
    $dir = Split-Path $Path -Parent
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $json = $Record | ConvertTo-Json -Depth 20 -Compress
    Add-Content -Path $Path -Value $json -Encoding UTF8
}

function ConvertTo-OrderedPayload {
    param([object]$Payload)
    $ht = [ordered]@{}
    if ($Payload -is [hashtable] -or $Payload -is [System.Collections.IDictionary]) {
        foreach ($key in ($Payload.Keys | Sort-Object)) {
            $value = $Payload[$key]
            if ($value -is [hashtable] -or $value -is [System.Collections.IDictionary] -or $value -is [PSObject]) {
                $ht[[string]$key] = ConvertTo-OrderedPayload -Payload $value
            }
            else {
                $ht[[string]$key] = $value
            }
        }
    }
    elseif ($Payload -is [PSObject] -or $null -ne $Payload) {
        $properties = @($Payload.PSObject.Properties | Sort-Object -Property Name)
        foreach ($property in $properties) {
            if ($property.Value -is [PSObject] -or $property.Value -is [System.Collections.IDictionary]) {
                $ht[$property.Name] = ConvertTo-OrderedPayload -Payload $property.Value
            }
            else {
                $ht[$property.Name] = $property.Value
            }
        }
    }
    return $ht
}

function Get-DeterministicSignature {
    param([string]$Title, [string]$ActionType, [string]$SourceProject, [object]$Payload)
    $orderedPayload = ConvertTo-OrderedPayload -Payload $Payload
    $payloadJson = $orderedPayload | ConvertTo-Json -Depth 20 -Compress
    $canonical = "$Title|$ActionType|$SourceProject|$payloadJson"
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($canonical)
    $hashBytes = $sha256.ComputeHash($bytes)
    return ($hashBytes | ForEach-Object { $_.ToString('x2') }) -join ''
}

function Test-BlockedKeyword {
    param([string]$Text)
    $lower = $Text.ToLowerInvariant()
    $blocked = @('wanefall', 'showmeai', 'benchmark', 'production', 'mobile', 'adb')
    foreach ($term in $blocked) {
        if ($lower.Contains($term)) { return $true }
    }
    return $false
}

function Get-ExistingQueueTasks {
    try {
        $queue = Get-IdleQueue -Path $queueFullPath
        if (-not ($queue.PSObject.Properties.Name -contains 'tasks')) { return @() }
        if ($null -eq $queue.tasks) { return @() }
        return @($queue.tasks)
    }
    catch {
        return @()
    }
}

function Get-HistoryLedgerRecords {
    if (-not (Test-Path $ledgerPath)) { return @() }
    $records = @()
    foreach ($line in [System.IO.File]::ReadLines($ledgerPath)) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        try { $records += ($line | ConvertFrom-Json) }
        catch { }
    }
    return $records
}

# ---------------------------------------------------------------------------
# Candidate generation
# ---------------------------------------------------------------------------
function New-SafeCandidate {
    param(
        [Parameter(Mandatory)] [string]$Title,
        [Parameter(Mandatory)] [string]$ActionType,
        [string]$Description = '',
        [string]$NextAction = '',
        [hashtable]$Payload = @{}
    )
    if (Test-BlockedKeyword -Text $Title) { throw "Blocked keyword in title: $Title" }
    if (Test-BlockedKeyword -Text $Description) { throw "Blocked keyword in description: $Description" }
    if (@('validate-json','validate-project','inspect-artifacts','monitor-health','identify-work') -notcontains $ActionType) {
        throw "Unsupported actionType: $ActionType"
    }

    $candidate = [ordered]@{
        title         = $Title
        description   = $Description
        status        = 'candidate'
        priority      = 5
        risk          = 1
        sourceProject = 'obtuse'
        nextAction    = $NextAction
        payload       = [ordered]@{
            actionType      = $ActionType
            suppressFollowUp = $true
        }
        createdAt     = (Get-Date -Format 'o')
    }

    foreach ($key in $Payload.Keys) {
        if ($key -eq 'actionType' -or $key -eq 'suppressFollowUp') { continue }
        $candidate.payload[$key] = $Payload[$key]
    }

    $signature = Get-DeterministicSignature -Title $candidate.title -ActionType $candidate.payload.actionType -SourceProject $candidate.sourceProject -Payload $candidate.payload
    $candidate['signature'] = $signature
    return $candidate
}

function Get-EvidencePresence {
    $presence = [ordered]@{}
    foreach ($key in $sources.Keys) {
        $presence[$key] = Test-RepoPath -RelativePath $sources[$key]
    }
    return $presence
}

function Read-CandidateSourceTasks {
    param([string]$RelativePath)
    $obj = Read-RepoJson -RelativePath $RelativePath
    if ($null -eq $obj) { return @() }
    if ($obj -is [array]) { return $obj }
    if ($obj.PSObject.Properties.Name -contains 'candidates') { return @($obj.candidates) }
    if ($obj.PSObject.Properties.Name -contains 'tasks') { return @($obj.tasks) }
    if ($obj.PSObject.Properties.Name -contains 'queue_candidates') { return @($obj.queue_candidates) }
    return @()
}

function Get-SourceDerivedCandidates {
    $candidates = [System.Collections.ArrayList]@()
    $presence = Get-EvidencePresence

    # Idea 1: re-check cross-machine proof bundle if manifest exists
    if ($presence.crossMachineManifest) {
        $manifest = Read-RepoJson -RelativePath $sources.crossMachineManifest
        $manifestPath = $null
        if ($manifest -and $manifest.PSObject.Properties.Name -contains 'packPath') { $manifestPath = $manifest.packPath }
        if (-not $manifestPath -and $manifest -and $manifest.PSObject.Properties.Name -contains 'manifestPath') { $manifestPath = $manifest.manifestPath }
        if (-not $manifestPath) { $manifestPath = $sources.crossMachineManifest }

        $c = New-SafeCandidate `
            -Title 'Cross-machine proof bundle SHA re-check on local artifact' `
            -ActionType 'validate-json' `
            -Description 'Re-validate the portable regression pack manifest JSON and any embedded SHA entries locally.' `
            -NextAction 'Validate manifest JSON structure and checksum consistency.' `
            -Payload @{ targetPath = $manifestPath; validationKind = 'manifest-sha' }
        [void]$candidates.Add($c)
    }

    # Idea 2: mirror reachability audit if cross-machine final report exists
    if ($presence.crossMachineFinalReport) {
        $report = Read-RepoJson -RelativePath $sources.crossMachineFinalReport
        $mirrorPath = $null
        if ($report -and $report.PSObject.Properties.Name -contains 'mirrorPath') { $mirrorPath = $report.mirrorPath }
        if ($report -and $report.PSObject.Properties.Name -contains 'localMirror') { $mirrorPath = $report.localMirror }
        if (-not $mirrorPath) { $mirrorPath = 'artifacts/obtuse/blunder-dunce-proof-regression-pack-and-phase-closure-v1' }

        $c = New-SafeCandidate `
            -Title 'FRANKENSTEIN mirror reachability audit' `
            -ActionType 'monitor-health' `
            -Description 'Check that the local FRANKENSTEIN mirror directory is reachable and contains expected closure artifacts.' `
            -NextAction 'List closure artifacts and verify directory health.' `
            -Payload @{ targetPath = $mirrorPath; healthKind = 'mirror-reachability' }
        [void]$candidates.Add($c)
    }

    # Idea 3: standard idle validation suite refresh (always useful)
    $validationScript = 'scripts/obtuse/blunder-standard-idle-validation-suite.ps1'
    $c = New-SafeCandidate `
        -Title 'Standard idle validation suite refresh' `
        -ActionType 'validate-project' `
        -Description 'Run the standard idle validation suite in Validate mode to refresh local state.' `
        -NextAction 'Run blunder-standard-idle-validation-suite.ps1 -Mode Validate.' `
        -Payload @{ projectPath = (Resolve-RepoPath -RelativePath ''); scriptPath = $validationScript; mode = 'Validate' }
    [void]$candidates.Add($c)

    # Idea 4: substrate queue policy duplicate guard audit
    if ($presence.substrateCandidates -or $presence.substrateFinalReport) {
        $target = $sources.substrateCandidates
        if (-not $presence.substrateCandidates) { $target = $sources.substrateFinalReport }
        $c = New-SafeCandidate `
            -Title 'Dunce substrate queue policy duplicate guard audit' `
            -ActionType 'inspect-artifacts' `
            -Description 'Inspect substrate queue candidates and verify duplicate-guard policy compliance.' `
            -NextAction 'Compare substrate candidates against live queue signatures.' `
            -Payload @{ targetPath = $target; inspectKind = 'duplicate-guard' }
        [void]$candidates.Add($c)
    }

    # Idea 5: GAMINGPC3 blocker documentation review
    if ($presence.crossMachineFinalReport -or $presence.substrateFinalReport) {
        $reportPath = $sources.crossMachineFinalReport
        if (-not $presence.crossMachineFinalReport) { $reportPath = $sources.substrateFinalReport }
        $c = New-SafeCandidate `
            -Title 'GAMINGPC3 blocker documentation review' `
            -ActionType 'inspect-artifacts' `
            -Description 'Record any GAMINGPC3-specific blockers found in Dunce reports as safe identify-work items.' `
            -NextAction 'Scan report for blocker markers and document remediation path.' `
            -Payload @{ targetPath = $reportPath; workKind = 'blocker-documentation' }
        [void]$candidates.Add($c)
    }

    # Idea 6: utility-scoring non-duplicate queue candidate re-inspection
    if ($presence.utilityCandidates) {
        $c = New-SafeCandidate `
            -Title 'Utility scoring non-duplicate queue candidate re-inspection' `
            -ActionType 'inspect-artifacts' `
            -Description 'Re-inspect utility-scored non-duplicate candidates for local admission readiness.' `
            -NextAction 'Validate candidate JSON and confirm local-only scope.' `
            -Payload @{ targetPath = $sources.utilityCandidates; inspectKind = 'candidate-readiness' }
        [void]$candidates.Add($c)
    }

    # Idea 7: hypothesis promotion artifact-only queue candidate re-inspection
    if ($presence.hypothesisCandidates) {
        $c = New-SafeCandidate `
            -Title 'Hypothesis promotion artifact-only queue candidate re-inspection' `
            -ActionType 'inspect-artifacts' `
            -Description 'Re-inspect artifact-only queue candidates from hypothesis promotion for safe local admission.' `
            -NextAction 'Validate candidate list and confirm no external dependencies.' `
            -Payload @{ targetPath = $sources.hypothesisCandidates; inspectKind = 'artifact-only-readiness' }
        [void]$candidates.Add($c)
    }

    # Idea 8: cross-machine proof final report structural validation
    if ($presence.crossMachineFinalReport) {
        $c = New-SafeCandidate `
            -Title 'Cross-machine proof final report structural validation' `
            -ActionType 'validate-json' `
            -Description 'Validate the structural integrity of the cross-machine proof regression final report.' `
            -NextAction 'Check required top-level keys and status field.' `
            -Payload @{ targetPath = $sources.crossMachineFinalReport; validationKind = 'structural' }
        [void]$candidates.Add($c)
    }

    return @($candidates)
}

# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------
function Test-CandidateDuplicate {
    param([object]$Candidate, [array]$QueueTasks, [array]$HistoryRecords)
    foreach ($task in $QueueTasks) {
        if ($task.title -and $task.title.ToLowerInvariant() -eq $Candidate.title.ToLowerInvariant()) { return $true }
        if ($task.PSObject.Properties.Name -contains 'signature') {
            if ($task.signature -eq $Candidate.signature) { return $true }
        }
        if ($task.PSObject.Properties.Name -contains 'payload' -and $task.payload) {
            $taskPayloadSig = $null
            if ($task.payload.PSObject.Properties.Name -contains 'signature') { $taskPayloadSig = $task.payload.signature }
            if ($taskPayloadSig -and $taskPayloadSig -eq $Candidate.signature) { return $true }
        }
    }
    foreach ($record in $HistoryRecords) {
        if ($record.title -and $record.title.ToLowerInvariant() -eq $Candidate.title.ToLowerInvariant()) { return $true }
        if ($record.signature -and $record.signature -eq $Candidate.signature) { return $true }
    }
    return $false
}

# ---------------------------------------------------------------------------
# Admission helpers
# ---------------------------------------------------------------------------
function Convert-CandidateToIdleTask {
    param([object]$Candidate)
    return New-IdleTask `
        -Title $Candidate.title `
        -Description $Candidate.description `
        -Priority $Candidate.priority `
        -Risk $Candidate.risk `
        -SourceProject $Candidate.sourceProject `
        -NextAction $Candidate.nextAction `
        -Payload $Candidate.payload
}

function New-AdmissionSummary {
    param(
        [array]$Candidates = @(),
        [array]$AdmittedIds = @(),
        [array]$DuplicateBlocked = @(),
        [array]$Errors = @(),
        [string]$Mode = 'GenerateCandidates'
    )
    return [ordered]@{
        generatedAt      = (Get-Date -Format 'o')
        mode             = $Mode
        totalCandidates  = $Candidates.Count
        admittedCount    = $AdmittedIds.Count
        duplicateBlocked = $DuplicateBlocked.Count
        admittedTaskIds  = @($AdmittedIds)
        duplicatesBlockedTitles = @($DuplicateBlocked)
        errors           = @($Errors)
    }
}

function New-SmokeSummary {
    param(
        [int]$CyclesRun = 0,
        [int]$Passed = 0,
        [int]$Failed = 0,
        [int]$Quarantined = 0,
        [object]$FinalStats = $null,
        [string]$Result = 'PENDING',
        [array]$CycleResults = @(),
        [array]$Errors = @()
    )
    if ($null -eq $FinalStats) { $FinalStats = [ordered]@{ total = 0; pending = 0; running = 0; passed = 0; failed = 0; blocked = 0; quarantined = 0 } }
    return [ordered]@{
        generatedAt   = (Get-Date -Format 'o')
        cyclesRun     = $CyclesRun
        passedCount   = $Passed
        failedCount   = $Failed
        quarantinedCount = $Quarantined
        finalQueueStats = $FinalStats
        cycleResults  = @($CycleResults)
        result        = $Result
        errors        = @($Errors)
    }
}

# ---------------------------------------------------------------------------
# Mode implementations
# ---------------------------------------------------------------------------
function Invoke-ValidateMode {
    $presence = Get-EvidencePresence
    $queueAccessible = $false
    $queueError = $null
    try {
        $stats = Get-QueueStats -QueuePath $queueFullPath
        $queueAccessible = $true
    }
    catch {
        $queueError = $_.Exception.Message
    }

    $summary = [ordered]@{
        generatedAt     = (Get-Date -Format 'o')
        mode            = 'Validate'
        sourcesPresent  = $presence
        queuePath       = $queueFullPath
        queueAccessible = $queueAccessible
        queueStats      = if ($queueAccessible) { $stats } else { $null }
        queueError      = $queueError
        artifactRoot    = $artifactRoot
        idleModeScript  = $idleModeScript
        idleModePresent = (Test-Path $idleModeScript)
    }

    Write-JsonFile -Value $summary -Path $admissionPath
    Write-Host ($summary | ConvertTo-Json -Depth 20 -Compress)
    return $summary
}

function Invoke-GenerateCandidatesMode {
    $presence = Get-EvidencePresence
    $anySource = $false
    foreach ($key in $presence.Keys) { if ($presence[$key]) { $anySource = $true; break } }

    if (-not $anySource) {
        $summary = New-AdmissionSummary -Mode 'GenerateCandidates' -Errors @('No candidate source artifacts found.')
        Write-JsonFile -Value $summary -Path $admissionPath
        if (Test-Path $ledgerPath) { Remove-Item $ledgerPath -Force }
        Write-Host ($summary | ConvertTo-Json -Depth 20 -Compress)
        return $summary
    }

    $rawCandidates = Get-SourceDerivedCandidates
    $queueTasks = Get-ExistingQueueTasks
    $history = Get-HistoryLedgerRecords

    $uniqueCandidates = [System.Collections.ArrayList]@()
    $duplicateBlocked = [System.Collections.ArrayList]@()
    foreach ($candidate in $rawCandidates) {
        if (Test-CandidateDuplicate -Candidate $candidate -QueueTasks $queueTasks -HistoryRecords $history) {
            [void]$duplicateBlocked.Add($candidate.title)
        }
        else {
            [void]$uniqueCandidates.Add($candidate)
        }
    }

    # Write ledger (always overwrite during generation)
    if (Test-Path $ledgerPath) { Remove-Item $ledgerPath -Force }
    foreach ($candidate in $uniqueCandidates) {
        Add-JsonLine -Path $ledgerPath -Record $candidate
    }

    $summary = New-AdmissionSummary `
        -Candidates $uniqueCandidates `
        -DuplicateBlocked $duplicateBlocked `
        -Mode 'GenerateCandidates'
    $summary['rawGeneratedCount'] = $rawCandidates.Count
    $summary['uniqueCandidateCount'] = $uniqueCandidates.Count
    Write-JsonFile -Value $summary -Path $admissionPath
    Write-Host ($summary | ConvertTo-Json -Depth 20 -Compress)
    return $summary
}

function Invoke-AdmitSafeMode {
    # Prefer ledger if it exists; otherwise regenerate candidates.
    $candidates = @()
    if (Test-Path $ledgerPath) {
        foreach ($line in [System.IO.File]::ReadLines($ledgerPath)) {
            if ([string]::IsNullOrWhiteSpace($line)) { continue }
            try { $candidates += ($line | ConvertFrom-Json) }
            catch { }
        }
    }
    if ($candidates.Count -eq 0) {
        $genSummary = Invoke-GenerateCandidatesMode
        if ($genSummary.totalCandidates -eq 0) { return $genSummary }
        $candidates = @()
        foreach ($line in [System.IO.File]::ReadLines($ledgerPath)) {
            if ([string]::IsNullOrWhiteSpace($line)) { continue }
            try { $candidates += ($line | ConvertFrom-Json) }
            catch { }
        }
    }

    $queueTasks = Get-ExistingQueueTasks
    $history = @()
    $admittedIds = [System.Collections.ArrayList]@()
    $duplicateBlocked = [System.Collections.ArrayList]@()
    $errors = [System.Collections.ArrayList]@()

    foreach ($candidate in $candidates) {
        try {
            if (Test-CandidateDuplicate -Candidate $candidate -QueueTasks $queueTasks -HistoryRecords $history) {
                [void]$duplicateBlocked.Add($candidate.title)
                continue
            }
            $task = Convert-CandidateToIdleTask -Candidate $candidate
            # stamp signature onto payload for future duplicate detection
            if (-not ($task.payload.ContainsKey('signature'))) {
                $task.payload['signature'] = $candidate.signature
            }
            $added = Add-IdleTask -QueuePath $queueFullPath -Task $task
            [void]$admittedIds.Add($added.id)
            # refresh queue tasks for strict duplicate avoidance within this run
            $queueTasks = Get-ExistingQueueTasks
        }
        catch {
            [void]$errors.Add("$($candidate.title): $($_.Exception.Message)")
        }
    }

    $summary = New-AdmissionSummary `
        -Candidates $candidates `
        -AdmittedIds $admittedIds `
        -DuplicateBlocked $duplicateBlocked `
        -Errors $errors `
        -Mode 'AdmitSafe'
    Write-JsonFile -Value $summary -Path $admissionPath
    Write-Host ($summary | ConvertTo-Json -Depth 20 -Compress)
    return $summary
}

function Invoke-RunSmokeMode {
    if (-not $operatorApprovedBool) {
        throw 'RunSmoke mode requires -OperatorApproved true.'
    }

    # Step 1: generate and admit safe candidates
    $admitSummary = Invoke-AdmitSafeMode
    $errors = [System.Collections.ArrayList]@()
    if ($admitSummary.errors.Count -gt 0) { foreach ($e in $admitSummary.errors) { [void]$errors.Add($e) } }

    # Step 2: smoke run
    $cycleResults = [System.Collections.ArrayList]@()
    $cyclesRun = 0
    $maxCycles = 10
    $result = 'PASS'

    for ($i = 0; $i -lt $maxCycles; $i++) {
        $stats = Get-QueueStats -QueuePath $queueFullPath
        if ($stats.pending -eq 0) {
            if ($cyclesRun -eq 0) {
                # queue already empty after admission; record no-op only if there were no pending tasks to begin with
            }
            break
        }

        $cycleRecord = [ordered]@{
            cycle = ($i + 1)
            startedAt = (Get-Date -Format 'o')
            idleModeExitCode = $null
            error = $null
        }
        try {
            $idleArgs = @('-Action','RunOnce','-QueuePath',$QueuePath,'-ArtifactRoot',$ArtifactRoot)
            $processArgs = @('-NoProfile','-ExecutionPolicy','Bypass','-File',$idleModeScript) + $idleArgs
            $process = Start-Process -FilePath 'powershell.exe' -ArgumentList $processArgs -Wait -PassThru -NoNewWindow -WorkingDirectory (Resolve-RepoPath -RelativePath '')
            $cycleRecord.idleModeExitCode = $process.ExitCode
        }
        catch {
            $cycleRecord.error = $_.Exception.Message
            [void]$errors.Add($_.Exception.Message)
        }
        $cycleRecord.finishedAt = (Get-Date -Format 'o')
        $statsAfter = Get-QueueStats -QueuePath $queueFullPath
        $cycleRecord.statsAfter = $statsAfter
        [void]$cycleResults.Add($cycleRecord)
        $cyclesRun++

        if ($statsAfter.pending -eq 0 -and $statsAfter.failed -eq 0) { break }
        if ($statsAfter.pending -eq $stats.pending -and $statsAfter.failed -eq $stats.failed) {
            # no progress this cycle; stop early to avoid infinite loop
            break
        }
    }

    $finalStats = Get-QueueStats -QueuePath $queueFullPath
    $passed = $finalStats.passed
    $failed = $finalStats.failed
    $quarantined = $finalStats.quarantined

    # Count active duplicates by title in pending/running tasks
    $activeDuplicateCount = 0
    $queue = Get-IdleQueue -Path $queueFullPath
    $activeTitles = @($queue.tasks | Where-Object { $_.status -eq 'pending' -or $_.status -eq 'running' } | ForEach-Object { $_.title.ToLowerInvariant() })
    $activeTitleGroups = $activeTitles | Group-Object
    foreach ($g in $activeTitleGroups) { if ($g.Count -gt 1) { $activeDuplicateCount += $g.Count } }

    # Success criteria
    $generatedForGate = $admitSummary.totalCandidates
    if ($admitSummary -is [System.Collections.IDictionary] -and $admitSummary.Contains('rawGeneratedCount')) {
        $generatedForGate = [int]$admitSummary['rawGeneratedCount']
    }
    elseif ($admitSummary.PSObject.Properties.Name -contains 'rawGeneratedCount') {
        $generatedForGate = [int]$admitSummary.rawGeneratedCount
    }
    if ($generatedForGate -lt 3) { $result = 'FAIL'; [void]$errors.Add("Insufficient candidates generated: $generatedForGate") }
    if ($admitSummary.admittedTaskIds.Count -ne $admitSummary.admittedCount) { $result = 'FAIL'; [void]$errors.Add('Admitted count mismatch.') }
    if ($finalStats.pending -ne 0) { $result = 'FAIL'; [void]$errors.Add("Pending tasks remain: $($finalStats.pending)") }
    if ($finalStats.failed -ne 0) { $result = 'FAIL'; [void]$errors.Add("Failed tasks remain: $($finalStats.failed)") }
    if ($activeDuplicateCount -ne 0) { $result = 'FAIL'; [void]$errors.Add("Active duplicates detected: $activeDuplicateCount") }

    $summary = New-SmokeSummary `
        -CyclesRun $cyclesRun `
        -Passed $passed `
        -Failed $failed `
        -Quarantined $quarantined `
        -FinalStats $finalStats `
        -Result $result `
        -CycleResults $cycleResults `
        -Errors $errors
    Write-JsonFile -Value $summary -Path $smokePath
    Write-Host ($summary | ConvertTo-Json -Depth 20 -Compress)
    return $summary
}

function Invoke-ReportOnlyMode {
    $summary = [ordered]@{
        generatedAt   = (Get-Date -Format 'o')
        mode          = 'ReportOnly'
        ledgerPresent = (Test-Path $ledgerPath)
        ledgerLines   = 0
        admissionPresent = (Test-Path $admissionPath)
        smokePresent  = (Test-Path $smokePath)
        admission     = $null
        smoke         = $null
        currentQueueStats = $null
    }
    if ($summary.ledgerPresent) {
        $summary.ledgerLines = [System.IO.File]::ReadAllLines($ledgerPath).Count
    }
    if ($summary.admissionPresent) {
        try { $summary.admission = Get-Content $admissionPath -Raw | ConvertFrom-Json }
        catch { $summary.admission = $_.Exception.Message }
    }
    if ($summary.smokePresent) {
        try { $summary.smoke = Get-Content $smokePath -Raw | ConvertFrom-Json }
        catch { $summary.smoke = $_.Exception.Message }
    }
    try {
        $summary.currentQueueStats = Get-QueueStats -QueuePath $queueFullPath
    }
    catch {
        $summary.currentQueueStats = $_.Exception.Message
    }
    Write-Host ($summary | ConvertTo-Json -Depth 20 -Compress)
    return $summary
}

# ---------------------------------------------------------------------------
# Main dispatch
# ---------------------------------------------------------------------------
switch ($Mode) {
    'Validate' {
        $result = Invoke-ValidateMode
        if ($result.queueAccessible -and $result.idleModePresent) { exit 0 }
        exit 1
    }
    'GenerateCandidates' {
        $result = Invoke-GenerateCandidatesMode
        if ($result.errors.Count -eq 0) { exit 0 }
        exit 1
    }
    'AdmitSafe' {
        $result = Invoke-AdmitSafeMode
        if ($result.errors.Count -eq 0) { exit 0 }
        exit 1
    }
    'RunSmoke' {
        $result = Invoke-RunSmokeMode
        if ($result.result -eq 'PASS') { exit 0 }
        exit 1
    }
    'ReportOnly' {
        [void](Invoke-ReportOnlyMode)
        exit 0
    }
    default              { throw "Unknown mode: $Mode" }
}
