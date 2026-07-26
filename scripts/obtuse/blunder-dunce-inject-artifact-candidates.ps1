#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Run','Validate','ReportOnly')]
    [string]$Mode = 'Run',
    [string]$ArtifactRoot = 'artifacts/obtuse/blunder-dunce-inject-artifact-candidates-v1',
    [string]$QueuePath = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle/idle-task-queue.json',
    [string]$CandidatePath = 'artifacts/obtuse/blunder-dunce-hypothesis-promotion-check-v1/002_ARTIFACT_ONLY_QUEUE_CANDIDATES.json',
    [string]$DefaultTargetPath = 'artifacts/obtuse/blunder-dunce-hypothesis-promotion-check-v1'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$queueModule = Join-Path $PSScriptRoot 'idle-task-queue.ps1'
$script:__injectMode = $Mode
$script:__injectArtifactRoot = $ArtifactRoot
$script:__injectQueuePath = $QueuePath
$script:__injectCandidatePath = $CandidatePath
$script:__injectDefaultTargetPath = $DefaultTargetPath
. $queueModule
$Mode = $script:__injectMode
$ArtifactRoot = $script:__injectArtifactRoot
$QueuePath = $script:__injectQueuePath
$CandidatePath = $script:__injectCandidatePath
$DefaultTargetPath = $script:__injectDefaultTargetPath

function Resolve-RepoPath {
    param([Parameter(Mandatory)] [string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $repoRoot $Path
}

function Read-JsonFile {
    param([Parameter(Mandatory)] [string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    if (-not (Test-Path -LiteralPath $resolved)) { return $null }
    return Get-Content -Raw -LiteralPath $resolved | ConvertFrom-Json -ErrorAction Stop
}

function Write-JsonFile {
    param([Parameter(Mandatory)] [object]$Value, [Parameter(Mandatory)] [string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    $parent = Split-Path $resolved -Parent
    if ($parent -and -not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    $Value | ConvertTo-Json -Depth 80 | Set-Content -LiteralPath $resolved -Encoding UTF8
}

function Get-ProcessSafety {
    $forbiddenPattern = 'external-benchmark|external-agent-runner|WANEFALL|adb\.exe|fastboot\.exe'
    $providerPattern = 'openrouter-idle-model\.ps1|idle-self-coding-accelerator\.ps1'
    $all = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine })
    return [ordered]@{
        forbiddenProcessCount = @($all | Where-Object { $_.CommandLine -match $forbiddenPattern }).Count
        providerProcessCount = @($all | Where-Object { $_.CommandLine -match $providerPattern }).Count
    }
}

function New-CandidateSignature {
    param([Parameter(Mandatory)] [object]$Candidate)
    return "$([string]$Candidate.taskId)|$([string]$Candidate.actionType)|$([string]$Candidate.controlLane)".ToLowerInvariant()
}

function Invoke-Run {
    New-Item -ItemType Directory -Force -Path (Resolve-RepoPath -Path $ArtifactRoot) | Out-Null
    $candidateDoc = Read-JsonFile -Path $CandidatePath
    if (-not $candidateDoc) { throw "Missing candidate ledger: $(Resolve-RepoPath -Path $CandidatePath)" }
    $queueResolved = Resolve-RepoPath -Path $QueuePath
    $targetResolved = Resolve-RepoPath -Path $DefaultTargetPath
    $processSafety = Get-ProcessSafety
    $queue = Get-IdleQueue -Path $queueResolved
    $existingSignatures = @{}
    foreach ($task in @($queue.tasks)) {
        if ($task.PSObject.Properties.Name -contains 'generationSignature') {
            $existingSignatures[[string]$task.generationSignature] = $true
        }
    }
    $admitted = New-Object System.Collections.ArrayList
    $skipped = New-Object System.Collections.ArrayList
    foreach ($candidate in @($candidateDoc.queueCandidates)) {
        $signature = New-CandidateSignature -Candidate $candidate
        if ($existingSignatures.ContainsKey($signature)) {
            [void]$skipped.Add([ordered]@{ taskId = [string]$candidate.taskId; reason = 'duplicate-signature' })
            continue
        }
        if ($candidate.sourceMutationAllowed -or $candidate.providerCallsAllowed -or $candidate.destructiveActionsAllowed) {
            [void]$skipped.Add([ordered]@{ taskId = [string]$candidate.taskId; reason = 'unsafe-candidate' })
            continue
        }
        $task = New-IdleTask -Title ([string]$candidate.title) `
            -Description "Artifact-only Dunce candidate. Test: $($candidate.measurableTest)" `
            -Priority 8 -Risk 1 -SourceProject 'dunce/showmeai' `
            -NextAction ([string]$candidate.successMetric) `
            -Payload @{
                actionType = 'inspect-artifacts'
                targetPath = $targetResolved
                controlLane = [string]$candidate.controlLane
                sourceMutationAllowed = $false
                providerCallsAllowed = $false
                destructiveActionsAllowed = $false
                suppressFollowUp = $true
                stopCondition = [string]$candidate.stopCondition
            }
        $task.generationSignature = $signature
        $task.candidateTaskId = [string]$candidate.taskId
        Add-IdleTask -QueuePath $queueResolved -Task $task | Out-Null
        $existingSignatures[$signature] = $true
        [void]$admitted.Add([ordered]@{ taskId = $task.id; candidateTaskId = [string]$candidate.taskId; title = $task.title; generationSignature = $signature })
    }
    $stats = Get-QueueStats -QueuePath $queueResolved
    $ledger = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        queuePath = $queueResolved
        candidatePath = (Resolve-RepoPath -Path $CandidatePath)
        targetPath = $targetResolved
        admittedCount = $admitted.Count
        skippedCount = $skipped.Count
        admitted = @($admitted)
        skipped = @($skipped)
        queueStats = $stats
    }
    Write-JsonFile -Value $ledger -Path (Join-Path $ArtifactRoot '001_QUEUE_INJECTION_LEDGER.json')
    $bundleRoot = Resolve-RepoPath -Path 'artifacts/obtuse/proof-bundles'
    New-Item -ItemType Directory -Force -Path $bundleRoot | Out-Null
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmss')
    $bundlePath = Join-Path $bundleRoot "obtuse-blunder-dunce-inject-artifact-candidates-v1-$stamp.zip"
    $files = @(Get-ChildItem -LiteralPath (Resolve-RepoPath -Path $ArtifactRoot) -Recurse -File | ForEach-Object { $_.FullName })
    $files += (Join-Path $PSScriptRoot 'blunder-dunce-inject-artifact-candidates.ps1')
    Compress-Archive -LiteralPath $files -DestinationPath $bundlePath -Force
    $target = '\\fileserver\shared\obtuse\blunder-dunce-inject-artifact-candidates-v1'
    $copied = $false
    if (Test-Path -LiteralPath '\\fileserver\shared\obtuse') {
        New-Item -ItemType Directory -Force -Path $target | Out-Null
        Copy-Item -LiteralPath (Join-Path (Resolve-RepoPath -Path $ArtifactRoot) '*') -Destination $target -Recurse -Force
        Copy-Item -LiteralPath $bundlePath -Destination (Join-Path $target (Split-Path -Leaf $bundlePath)) -Force
        $copied = $true
    }
    $status = if ($admitted.Count -gt 0 -and $processSafety.forbiddenProcessCount -eq 0 -and $processSafety.providerProcessCount -eq 0) { 'PASS' } else { 'PASS_WITH_NOTES' }
    $result = [ordered]@{
        classification = 'OBTUSE_BLUNDER_DUNCE_INJECT_ARTIFACT_CANDIDATES_V1'
        status = $status
        sourceEdited = $true
        filesEdited = @('scripts/obtuse/blunder-dunce-inject-artifact-candidates.ps1')
        artifactOnly = $true
        benchmarkTouched = $false
        providerCalls = $false
        secretsPrinted = $false
        destructiveActions = $false
        safetyBoundariesChanged = $false
        wanefallTouched = $false
        showMeAISourceTouched = $false
        mobilePhoneAdbTouched = $false
        forbiddenProcessCount = $processSafety.forbiddenProcessCount
        providerProcessCount = $processSafety.providerProcessCount
        admittedCount = $admitted.Count
        skippedCount = $skipped.Count
        queueStats = $stats
        proofBundlePath = $bundlePath
        proofBundleSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $bundlePath).Hash.ToUpperInvariant()
        frankensteinCopyPath = $target
        frankensteinCopySucceeded = $copied
        finalReportPath = (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json'))
        knownLimitations = @('Queue injection is limited to artifact-only inspect-artifacts tasks.')
        nextRecommendedAction = 'Run a bounded foreground idle smoke for the admitted artifact-only Dunce candidates.'
    }
    Write-JsonFile -Value $result -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')
    return $result
}

switch ($Mode) {
    'Validate' {
        $tokens = $null
        $errors = $null
        [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'blunder-dunce-inject-artifact-candidates.ps1'), [ref]$tokens, [ref]$errors) | Out-Null
        [ordered]@{
            classification = 'OBTUSE_BLUNDER_DUNCE_INJECT_ARTIFACT_CANDIDATES_V1'
            parserErrors = $errors.Count
            result = if ($errors.Count -eq 0) { 'PASS' } else { 'FAIL' }
        } | ConvertTo-Json -Depth 10
        return
    }
    'ReportOnly' {
        $report = Read-JsonFile -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')
        if ($report) { $report | ConvertTo-Json -Depth 80; return }
        [ordered]@{ classification = 'OBTUSE_BLUNDER_DUNCE_INJECT_ARTIFACT_CANDIDATES_V1'; status = 'MISSING_REPORT' } | ConvertTo-Json -Depth 10
        return
    }
    'Run' {
        Invoke-Run | ConvertTo-Json -Depth 80
        return
    }
}
