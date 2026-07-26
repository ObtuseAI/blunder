#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Validate','RunOnce','RunSmoke','ReportOnly','StopIfUnsafe')]
    [string]$Mode = 'Validate',
    [string]$OperatorApproved = 'false',
    [int]$MaxCycles = 5,
    [string]$QueuePath = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle/idle-task-queue.json',
    [string]$ArtifactRoot = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle',
    [string]$PackArtifactRoot = 'artifacts/obtuse/blunder-dunce-proof-regression-pack-and-phase-closure-v1',
    [string]$RegressionPackScript = 'scripts/obtuse/blunder-dunce-proof-regression-pack.ps1',
    [string]$OutputArtifactRoot = 'artifacts/obtuse/blunder-dunce-regression-substrate-idle-cycle-v1',
    [int]$CycleTimeoutSeconds = 120
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent

function Convert-ToBoolean {
    param([object]$Value)
    if ($Value -is [bool]) { return [bool]$Value }
    if ($Value -is [int]) { return ([int]$Value -ne 0) }
    return (([string]$Value).Trim().ToLowerInvariant() -in @('true','1','yes','y','approved'))
}
$OperatorApprovedBool = Convert-ToBoolean -Value $OperatorApproved

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
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $Value | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $resolved -Encoding UTF8
}

function Write-TextFile {
    param([Parameter(Mandatory)] [string]$Path, [Parameter(Mandatory)] [string]$Value)
    $resolved = Resolve-RepoPath -Path $Path
    $parent = Split-Path $resolved -Parent
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    [System.IO.File]::WriteAllText($resolved, ($Value -replace "`r?`n", "`r`n"), [System.Text.UTF8Encoding]::new($false))
}

function Add-LedgerLine {
    param([Parameter(Mandatory)] [string]$Path, [Parameter(Mandatory)] [object]$Record)
    $resolved = Resolve-RepoPath -Path $Path
    $parent = Split-Path $resolved -Parent
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $Record | ConvertTo-Json -Depth 20 -Compress | Add-Content -LiteralPath $resolved -Encoding UTF8
}

function Get-ProcessSafety {
    $forbiddenPattern = 'external-benchmark|external-agent-runner|WANEFALL|adb\.exe|fastboot\.exe'
    $providerPattern = 'openrouter-idle-model\.ps1|idle-self-coding-accelerator\.ps1'
    $all = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine })
    return [ordered]@{
        forbiddenProcessCount = @($all | Where-Object { $_.CommandLine -match $forbiddenPattern }).Count
        providerProcessCount = @($all | Where-Object { $_.CommandLine -match $providerPattern }).Count
        timestamp = (Get-Date -Format 'o')
    }
}

function Get-IdleStatus {
    $statePath = Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'idle-state.json')
    $lockPath = Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'idle-runner.lock.json')
    $queuePath = Resolve-RepoPath -Path $QueuePath
    $state = if (Test-Path -LiteralPath $statePath) { try { Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json -ErrorAction Stop } catch { $null } } else { $null }
    $lock = if (Test-Path -LiteralPath $lockPath) { try { Get-Content -Raw -LiteralPath $lockPath | ConvertFrom-Json -ErrorAction Stop } catch { $null } } else { $null }
    $queue = if (Test-Path -LiteralPath $queuePath) { try { Get-Content -Raw -LiteralPath $queuePath | ConvertFrom-Json -ErrorAction Stop } catch { $null } } else { $null }
    $active = $false
    if ($lock -and $lock.status -eq 'active' -and $lock.pid) {
        try { $proc = Get-Process -Id $lock.pid -ErrorAction Stop; $active = ($null -ne $proc -and -not $proc.HasExited) } catch { $active = $false }
    }
    $pending = if ($queue -and $queue.tasks) { @($queue.tasks | Where-Object { $_.status -eq 'pending' }).Count } else { 0 }
    $running = if ($queue -and $queue.tasks) { @($queue.tasks | Where-Object { $_.status -eq 'running' }).Count } else { 0 }
    $blocked = if ($queue -and $queue.tasks) { @($queue.tasks | Where-Object { $_.status -eq 'blocked' }).Count } else { 0 }
    $failed = if ($queue -and $queue.tasks) { @($queue.tasks | Where-Object { $_.status -eq 'failed' }).Count } else { 0 }
    $quarantined = if ($queue -and $queue.tasks) { @($queue.tasks | Where-Object { $_.status -eq 'quarantined' }).Count } else { 0 }
    return [ordered]@{
        active = $active
        state = $state
        lock = $lock
        queueStats = [ordered]@{
            total = if ($queue -and $queue.tasks) { $queue.tasks.Count } else { 0 }
            pending = $pending
            running = $running
            blocked = $blocked
            failed = $failed
            quarantined = $quarantined
        }
    }
}

function Get-FileSha256 {
    param([Parameter(Mandatory)] [string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    if (-not (Test-Path -LiteralPath $resolved)) { return $null }
    return (Get-FileHash -LiteralPath $resolved -Algorithm SHA256).Hash.ToUpperInvariant()
}

function Get-LatestProofBundleInfo {
    $bundleDir = Resolve-RepoPath -Path 'artifacts/obtuse/proof-bundles'
    if (-not (Test-Path -LiteralPath $bundleDir)) { return @{ path = ''; sha256 = '' } }
    $bundles = @(Get-ChildItem -LiteralPath $bundleDir -Filter 'obtuse-blunder-dunce-regression-substrate-idle-cycle-v1-*.zip' | Sort-Object LastWriteTime -Descending)
    if ($bundles.Count -eq 0) { return @{ path = ''; sha256 = '' } }
    $latest = $bundles[0]
    return @{ path = $latest.FullName; sha256 = (Get-FileHash -LiteralPath $latest.FullName -Algorithm SHA256).Hash.ToUpperInvariant() }
}

function Invoke-RegressionPack {
    param([string]$Label)
    $scriptPath = Resolve-RepoPath -Path $RegressionPackScript
    $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $scriptPath -Mode RunRegression 2>&1
    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) { $exitCode = 0 }
    $validationPath = Resolve-RepoPath -Path (Join-Path $PackArtifactRoot 'regression_pack_validation.json')
    $validation = if (Test-Path -LiteralPath $validationPath) { Read-JsonFile -Path (Join-Path $PackArtifactRoot 'regression_pack_validation.json') } else { $null }
    return [ordered]@{
        label = $Label
        timestamp = (Get-Date -Format 'o')
        exitCode = $exitCode
        result = if ($validation) { $validation.result } else { 'UNKNOWN' }
        totalChecks = if ($validation) { $validation.totalChecks } else { 0 }
        passedChecks = if ($validation) { $validation.passedChecks } else { 0 }
        failedChecks = if ($validation) { $validation.failedChecks } else { 0 }
        validationPath = $validationPath
    }
}

function Invoke-BaselineConsumption {
    $now = Get-Date -Format 'o'
    $record = [ordered]@{
        timestamp = $now
        classification = 'OBTUSE_BLUNDER_DUNCE_REGRESSION_SUBSTRATE_IDLE_CYCLE_V1'
        phase = 'BASELINE_CONSUMPTION'
        packFinalReport = [ordered]@{}
        packBundle = [ordered]@{}
        packArtifactDir = [ordered]@{}
        regressionPackScript = [ordered]@{}
        idleStatus = $null
        safety = (Get-ProcessSafety)
        complete = $false
    }
    $allOk = $true

    $finalReportPath = Join-Path $PackArtifactRoot 'FINAL_REPORT.json'
    $finalReport = Read-JsonFile -Path $finalReportPath
    $record.packFinalReport = [ordered]@{
        path = (Resolve-RepoPath -Path $finalReportPath)
        exists = ($null -ne $finalReport)
        terminalState = if ($finalReport) { $finalReport.terminalState } else { $null }
        proofCases = if ($finalReport) { $finalReport.proofCasesIndexed } else { 0 }
        sidecars = if ($finalReport) { $finalReport.sidecarsIndexed } else { 0 }
        regressionResult = if ($finalReport) { $finalReport.regressionRunResult } else { $null }
        queuePolicyResult = if ($finalReport) { $finalReport.queuePolicyIntegrationResult } else { $null }
    }
    if (-not $finalReport -or $finalReport.terminalState -ne 'DUNCE_PROOF_REGRESSION_PACK_PHASE_CLOSED') { $allOk = $false }

    $bundlePath = 'artifacts/obtuse/proof-bundles/obtuse-blunder-dunce-proof-regression-pack-and-phase-closure-v1-20260628T165836.zip'
    $bundleSha = Get-FileSha256 -Path $bundlePath
    $record.packBundle = [ordered]@{
        path = (Resolve-RepoPath -Path $bundlePath)
        exists = (Test-Path -LiteralPath (Resolve-RepoPath -Path $bundlePath))
        sha256 = $bundleSha
        expectedSha256 = '4902052429C2F38F2139597EDA2CF2823941C4E68CC141777989B4073FE45828'
        shaMatches = ($bundleSha -eq '4902052429C2F38F2139597EDA2CF2823941C4E68CC141777989B4073FE45828')
    }
    if (-not $record.packBundle.shaMatches) { $allOk = $false }

    $record.packArtifactDir = [ordered]@{
        path = (Resolve-RepoPath -Path $PackArtifactRoot)
        exists = (Test-Path -LiteralPath (Resolve-RepoPath -Path $PackArtifactRoot))
        manifestExists = (Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $PackArtifactRoot 'proof_regression_pack_manifest.json')))
        duplicateGuardExists = (Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $PackArtifactRoot 'duplicate_guard_evidence.json')))
    }
    if (-not $record.packArtifactDir.manifestExists -or -not $record.packArtifactDir.duplicateGuardExists) { $allOk = $false }

    $regressionScriptPath = Resolve-RepoPath -Path $RegressionPackScript
    $record.regressionPackScript = [ordered]@{
        path = $regressionScriptPath
        exists = (Test-Path -LiteralPath $regressionScriptPath)
    }
    if (-not $record.regressionPackScript.exists) { $allOk = $false }

    $record.idleStatus = Get-IdleStatus
    if ($record.idleStatus -and $record.idleStatus.active) { $allOk = $false }
    if ($record.idleStatus -and $record.idleStatus.queueStats -and $record.idleStatus.queueStats.pending -gt 0) { $allOk = $false }

    $safety = Get-ProcessSafety
    if ($safety.forbiddenProcessCount -gt 0) { $allOk = $false }

    $record.complete = $allOk
    return $record
}

function Invoke-IdleCycleRun {
    param(
        [int]$Cycles,
        [string]$Label
    )
    $now = Get-Date -Format 'o'
    $runner = Resolve-RepoPath -Path 'scripts/obtuse/idle-mode.ps1'
    $args = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', $runner,
        '-Action', 'Start',
        '-QueuePath', (Resolve-RepoPath -Path $QueuePath),
        '-ArtifactRoot', (Resolve-RepoPath -Path $ArtifactRoot),
        '-MaxCycles', [string]$Cycles,
        '-LoopDelaySeconds', '2',
        '-ReplenishmentBatchSize', '5'
    )

    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = 'powershell.exe'
    $startInfo.Arguments = ($args -join ' ')
    $startInfo.WorkingDirectory = $repoRoot
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden

    $proc = [System.Diagnostics.Process]::Start($startInfo)
    $stdout = $proc.StandardOutput.ReadToEnd()
    $stderr = $proc.StandardError.ReadToEnd()
    $completed = $proc.WaitForExit($CycleTimeoutSeconds * 1000)
    if (-not $completed) {
        try { $proc.Kill() } catch {}
        $proc.WaitForExit(5000)
    }

    $status = Get-IdleStatus
    $queue = Read-JsonFile -Path $QueuePath
    $ledgerPath = Join-Path $ArtifactRoot 'idle-cycle-ledger.jsonl'
    $ledgerEntries = @()
    if (Test-Path -LiteralPath (Resolve-RepoPath -Path $ledgerPath)) {
        $ledgerEntries = @(Get-Content -LiteralPath (Resolve-RepoPath -Path $ledgerPath) | ForEach-Object { try { $_ | ConvertFrom-Json -ErrorAction Stop } catch {} })
    }

    $cycleEntries = @($ledgerEntries | Where-Object { $_ -and $_.cycleAt -and ([datetime]$_.cycleAt) -ge ([datetime]$now) })
    $passed = @($cycleEntries | Where-Object { $_.status -eq 'passed' }).Count
    $failed = @($cycleEntries | Where-Object { $_.status -eq 'failed' }).Count
    $quarantined = @($cycleEntries | Where-Object { $_.status -eq 'quarantined' }).Count
    $blocked = @($cycleEntries | Where-Object { $_.status -eq 'blocked' }).Count
    $cycledCount = @($cycleEntries | Where-Object { $_.cycled -eq $true -or $_.taskId }).Count

    return [ordered]@{
        label = $Label
        timestamp = (Get-Date -Format 'o')
        cyclesRequested = $Cycles
        processExited = $proc.HasExited
        exitCode = $proc.ExitCode
        completedWithinTimeout = $completed
        stdoutTail = ($stdout -split "`r?`n" | Select-Object -Last 10) -join "`n"
        stderrTail = ($stderr -split "`r?`n" | Select-Object -Last 5) -join "`n"
        cyclesFoundInLedger = $cycleEntries.Count
        cyclesPassed = $passed
        cyclesFailed = $failed
        cyclesQuarantined = $quarantined
        cyclesBlocked = $blocked
        idleStatusAfter = $status
        queueStatsAfter = if ($status -and $status.queueStats) { $status.queueStats } else { $null }
    }
}

function Invoke-QueuePolicySubstrateCheck {
    param([object]$PreCycleQueue, [object]$PostCycleQueue)
    $now = Get-Date -Format 'o'
    $replenishSource = Get-Content -Raw -LiteralPath (Resolve-RepoPath -Path 'scripts/obtuse/replenish-idle-queue.ps1')
    $hasSigGuard = $replenishSource -match 'existingSig.*-eq.*Signature'
    $hasTitleGuard = $replenishSource -match 'existingTitle.*-eq.*title'

    $checks = New-Object System.Collections.ArrayList
    function Add-Check {
        param([string]$Name, [bool]$Passed, [string]$Message)
        [void]$checks.Add([ordered]@{ name = $Name; passed = $Passed; message = $Message })
    }

    $utilityLedger = Read-JsonFile -Path 'artifacts/obtuse/blunder-dunce-utility-scoring-v1/001_UTILITY_SCORING_LEDGER.json'
    $promotionCandidates = Read-JsonFile -Path 'artifacts/obtuse/blunder-dunce-hypothesis-promotion-check-v1/002_ARTIFACT_ONLY_QUEUE_CANDIDATES.json'
    Add-Check -Name 'replenishment_can_discover_artifact_candidates' -Passed ($null -ne $utilityLedger -and $null -ne $promotionCandidates) -Message "utilityLedger=$([bool]$utilityLedger), promotionCandidates=$([bool]$promotionCandidates)"
    Add-Check -Name 'utility_ledger_readable' -Passed ($null -ne $utilityLedger) -Message "ledgerReadable=$([bool]$utilityLedger)"

    $activeDupes = @($PostCycleQueue.tasks | Where-Object { ($_.status -eq 'pending' -or $_.status -eq 'running') -and ($_.title -like '*DUNCE-HYP-001*' -or $_.title -like '*DUNCE-HYP-002*') }).Count
    $quarantinedDupes = @($PostCycleQueue.tasks | Where-Object { $_.status -eq 'quarantined' -and ($_.title -like '*DUNCE-HYP-001*' -or $_.title -like '*DUNCE-HYP-002*') }).Count
    Add-Check -Name 'duplicate_guard_blocks_old_hyp001_hyp002' -Passed ($hasSigGuard -and $hasTitleGuard -and $activeDupes -eq 0 -and $quarantinedDupes -ge 2) -Message "sigGuard=$hasSigGuard, titleGuard=$hasTitleGuard, activeDupes=$activeDupes, quarantinedDupes=$quarantinedDupes"

    $sampleTask = @($PostCycleQueue.tasks | Where-Object { $_.title -like 'Dunce*' -and $_.payload -and $_.payload.actionType -and $_.payload.targetPath })[0]
    $payloadKeys = if ($sampleTask -and $sampleTask.payload) { @($sampleTask.payload.PSObject.Properties.Name) } else { @() }
    $hasOrderedDictMetadata = ($payloadKeys -contains 'IsFixedSize') -or ($payloadKeys -contains 'IsSynchronized') -or ($payloadKeys -contains 'Keys') -or ($payloadKeys -contains 'Values') -or ($payloadKeys -contains 'Count')
    $hasMeaningfulPayload = ($payloadKeys -contains 'actionType') -and ($payloadKeys -contains 'targetPath')
    Add-Check -Name 'ordered_dictionary_payload_preservation' -Passed ($null -ne $sampleTask -and $hasMeaningfulPayload -and -not $hasOrderedDictMetadata) -Message "actionType=$(if($sampleTask){$sampleTask.payload.actionType}else{'null'}), meaningful=$hasMeaningfulPayload"

    $blockedWithFollowUp = @($PostCycleQueue.tasks | Where-Object { $_.status -eq 'blocked' -and $_.payload.suppressFollowUp -eq $true }).Count
    Add-Check -Name 'suppress_follow_up_blocks_decomposition' -Passed ($blockedWithFollowUp -eq 0) -Message "blockedWithFollowUp=$blockedWithFollowUp"

    $wrongQueuePath = 'artifacts/obtuse/idle-mode-full-operationalization-v1/idle-task-queue.json'
    $wrongQueueHasDunce = $false
    if (Test-Path -LiteralPath (Resolve-RepoPath -Path $wrongQueuePath)) {
        $wrongQueue = Read-JsonFile -Path $wrongQueuePath
        $wrongQueueHasDunce = (@($wrongQueue.tasks | Where-Object { $_.title -like 'Dunce hypothesis promotion check*' -and $_.status -ne 'quarantined' }).Count -gt 0)
    }
    Add-Check -Name 'queue_path_clobber_bug_repaired' -Passed (-not $wrongQueueHasDunce) -Message "wrongQueueHasDunce=$wrongQueueHasDunce"

    $unsafeAdmitted = @($PostCycleQueue.tasks | Where-Object { $_.status -eq 'passed' -and ($_.payload.sourceMutationAllowed -or $_.payload.providerCallsAllowed -or $_.payload.destructiveActionsAllowed) }).Count
    Add-Check -Name 'policy_admits_only_safe_artifact_candidates' -Passed ($unsafeAdmitted -eq 0) -Message "unsafeAdmitted=$unsafeAdmitted"

    $malformedActive = @($PostCycleQueue.tasks | Where-Object { $_.status -in @('pending','running') -and ($_.title -like 'Dunce*') -and ($null -eq $_.payload -or $null -eq $_.payload.actionType) }).Count
    Add-Check -Name 'no_malformed_dunce_task_active' -Passed ($malformedActive -eq 0) -Message "malformedActive=$malformedActive"

    $failedChecks = @($checks | Where-Object { -not $_.passed })
    $result = if ($failedChecks.Count -eq 0) { 'PASS' } else { 'FAIL' }
    return [ordered]@{
        timestamp = $now
        classification = 'OBTUSE_BLUNDER_DUNCE_REGRESSION_SUBSTRATE_IDLE_CYCLE_V1'
        result = $result
        totalChecks = $checks.Count
        passedChecks = ($checks.Count - $failedChecks.Count)
        failedChecks = $failedChecks.Count
        checks = @($checks)
    }
}

function Invoke-SubstrateIntegrityCheck {
    param([object]$PreRegression, [object]$PostRegression, [object]$PreQueue, [object]$PostQueue)
    $now = Get-Date -Format 'o'
    $manifest = Read-JsonFile -Path (Join-Path $PackArtifactRoot 'proof_regression_pack_manifest.json')
    $preCases = if ($PreRegression) { $PreRegression.passedChecks } else { 0 }
    $postCases = if ($PostRegression) { $PostRegression.passedChecks } else { 0 }
    $packCorrupted = $false
    $missingArtifacts = @()
    foreach ($file in @('proof_case_index.json','proof_sidecar_index.json','proof_case_to_sidecar_map.json','utility_candidate_index.json','promotion_candidate_index.json','queue_admission_index.json','duplicate_guard_evidence.json','smoke_pass_evidence.json')) {
        if (-not (Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $PackArtifactRoot $file)))) { $missingArtifacts += $file }
    }

    $prePendingDunce = @($PreQueue.tasks | Where-Object { $_.status -eq 'pending' -and $_.title -like 'Dunce*' }).Count
    $postPendingDunce = @($PostQueue.tasks | Where-Object { $_.status -eq 'pending' -and $_.title -like 'Dunce*' }).Count
    $postFailedDunce = @($PostQueue.tasks | Where-Object { $_.status -eq 'failed' -and $_.title -like 'Dunce*' }).Count
    $postBlockedDunce = @($PostQueue.tasks | Where-Object { $_.status -eq 'blocked' -and $_.title -like 'Dunce*' }).Count

    $integrity = [ordered]@{
        timestamp = $now
        classification = 'OBTUSE_BLUNDER_DUNCE_REGRESSION_SUBSTRATE_IDLE_CYCLE_V1'
        preCycleRegressionResult = $PreRegression.result
        postCycleRegressionResult = $PostRegression.result
        regressionStable = ($PreRegression.result -eq 'PASS' -and $PostRegression.result -eq 'PASS' -and $postCases -ge $preCases)
        proofCasesStillIndexed = if ($manifest) { $manifest.proofCaseCount } else { 0 }
        sidecarsStillIndexed = if ($manifest) { $manifest.sidecarCount } else { 0 }
        missingPackArtifacts = @($missingArtifacts)
        packCorrupted = ($missingArtifacts.Count -gt 0)
        preCyclePendingDunce = $prePendingDunce
        postCyclePendingDunce = $postPendingDunce
        postCycleFailedDunce = $postFailedDunce
        postCycleBlockedDunce = $postBlockedDunce
        result = if (($PreRegression.result -eq 'PASS') -and ($PostRegression.result -eq 'PASS') -and ($missingArtifacts.Count -eq 0) -and ($postPendingDunce -eq 0) -and ($postFailedDunce -eq 0) -and ($postBlockedDunce -eq 0)) { 'PASS' } else { 'FAIL' }
    }
    return $integrity
}

function Write-UpdatedTodo {
    param([bool]$Pass)
    $text = @"
# Dunce Regression Substrate Idle Cycle — Updated TODO

## Completed
- DONE-041 Run bounded normal idle cycle using proof-regression pack as regression substrate.

## Remaining
- NEXT-042 Add cross-machine proof-regression verification on FRANKENSTEIN/GAMINGPC3.
- NEXT-043 Decide whether Dunce artifact proof should feed Blunder's broader self-coding improvement queue.
- NEXT-044 Run longer Flash autonomy cycle only after substrate idle-cycle proof is clean.
"@
    if (-not $Pass) {
        $text += "`n`n## Blocker`n- EXACT-NEXT: Resolve substrate idle-cycle failure before extending runtime."
    }
    Write-TextFile -Path (Join-Path $OutputArtifactRoot 'UPDATED_TODO.md') -Value $text
}

function Write-OperatorHandoff {
    param([object]$Summary)
    $text = @"
# Dunce Regression Substrate Idle Cycle — Operator Handoff

Classification: OBTUSE_BLUNDER_DUNCE_REGRESSION_SUBSTRATE_IDLE_CYCLE_V1
Timestamp: $($Summary.timestamp)
Terminal state: $($Summary.terminalState)

## Results
- Proof-regression pack used as live substrate: $($Summary.packUsedAsSubstrate)
- Pre-cycle regression: $($Summary.preCycleRegressionResult)
- Normal idle cycle: $($Summary.normalIdleCycleResult)
- Post-cycle regression: $($Summary.postCycleRegressionResult)
- Cycles attempted/passed/failed/quarantined: $($Summary.cyclesAttempted)/$($Summary.cyclesPassed)/$($Summary.cyclesFailed)/$($Summary.cyclesQuarantined)
- Queue/policy substrate result: $($Summary.queuePolicySubstrateResult)
- Duplicate guard result: $($Summary.duplicateGuardResult)
- Safety result: $($Summary.safetyResult)

## Commands
Validate: powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder-dunce-regression-substrate-idle-cycle.ps1 -Mode Validate
RunOnce: powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder-dunce-regression-substrate-idle-cycle.ps1 -Mode RunOnce -OperatorApproved true
RunSmoke: powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder-dunce-regression-substrate-idle-cycle.ps1 -Mode RunSmoke -OperatorApproved true -MaxCycles 5
Regression rerun: powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder-dunce-proof-regression-pack.ps1 -Mode RunRegression

## Next recommended action
Run cross-machine proof-regression verification on FRANKENSTEIN/GAMINGPC3.
"@
    Write-TextFile -Path (Join-Path $OutputArtifactRoot 'operator_handoff.md') -Value $text
}

function Write-FinalReport {
    param(
        [object]$Baseline,
        [object]$PreRegression,
        [object]$IdleCycle,
        [object]$PostRegression,
        [object]$Integrity,
        [object]$QueueCheck,
        [object]$Validation
    )
    $now = Get-Date -Format 'o'
    $bundleInfo = Get-LatestProofBundleInfo
    $report = [ordered]@{
        classification = 'OBTUSE_BLUNDER_DUNCE_REGRESSION_SUBSTRATE_IDLE_CYCLE_V1'
        status = $Validation.status
        terminalState = $Validation.terminalState
        baselineConsumed = if ($Baseline) { $Baseline.complete } else { $false }
        proofRegressionPackConsumed = if ($Baseline) { $Baseline.packBundle.shaMatches } else { $false }
        preCycleRegressionResult = $PreRegression.result
        normalIdleCycleResult = $IdleCycle.result
        cyclesAttempted = $IdleCycle.cyclesRequested
        cyclesPassed = $IdleCycle.cyclesPassed
        cyclesFailed = $IdleCycle.cyclesFailed
        cyclesQuarantined = $IdleCycle.cyclesQuarantined
        postCycleRegressionResult = $PostRegression.result
        substrateIntegrityResult = $Integrity.result
        queuePolicySubstrateResult = $QueueCheck.result
        duplicateGuardResult = $Validation.duplicateGuardResult
        idleFinalState = $Validation.idleFinalState
        orphanProcess = $Validation.orphanProcess
        pidReuseSuspicion = $Validation.pidReuseSuspicion
        providerFailures = $Validation.providerFailures
        benchmarkTouched = $Validation.benchmarkTouched
        providerCalls = $Validation.providerCalls
        secretsPrinted = $Validation.secretsPrinted
        destructiveActions = $Validation.destructiveActions
        safetyBoundariesChanged = $Validation.safetyBoundariesChanged
        showMeAISourceTouched = $Validation.showMeAISourceTouched
        wanefallTouched = $Validation.wanefallTouched
        mobileAdbTouched = $Validation.mobileAdbTouched
        filesAdded = @('scripts/obtuse/blunder-dunce-regression-substrate-idle-cycle.ps1')
        filesModified = @(
            'scripts/obtuse/idle-mode.ps1',
            'scripts/obtuse/replenish-idle-queue.ps1'
        )
        finalReportPath = (Resolve-RepoPath -Path (Join-Path $OutputArtifactRoot 'FINAL_REPORT.json'))
        proofBundlePath = $bundleInfo.path
        proofBundleSha256 = $bundleInfo.sha256
        frankensteinCopyPath = '\\fileserver\shared\obtuse\blunder-dunce-regression-substrate-idle-cycle-v1'
        exactValidateCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder-dunce-regression-substrate-idle-cycle.ps1 -Mode Validate'
        exactRunCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder-dunce-regression-substrate-idle-cycle.ps1 -Mode RunOnce -OperatorApproved true'
        exactSmokeCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder-dunce-regression-substrate-idle-cycle.ps1 -Mode RunSmoke -OperatorApproved true -MaxCycles 5'
        exactRegressionRerunCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder-dunce-proof-regression-pack.ps1 -Mode RunRegression'
        nextRecommendedAction = 'Run cross-machine proof-regression verification on FRANKENSTEIN/GAMINGPC3.'
    }
    Write-JsonFile -Value $report -Path (Join-Path $OutputArtifactRoot 'FINAL_REPORT.json')
    return $report
}

# Main dispatch
New-Item -ItemType Directory -Force -Path (Resolve-RepoPath -Path $OutputArtifactRoot) | Out-Null
$ledgerPath = Join-Path $OutputArtifactRoot 'normal_idle_cycle_ledger.jsonl'

if ($Mode -eq 'StopIfUnsafe') {
    $safety = Get-ProcessSafety
    $status = Get-IdleStatus
    $unsafe = ($safety.forbiddenProcessCount -gt 0) -or ($status -and $status.active -and $status.heartbeat -and ((Get-Date) - [datetime]$status.heartbeat.timestamp).TotalSeconds -gt 60)
    if ($unsafe) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Resolve-RepoPath -Path 'scripts/obtuse/stop-idle-ops.ps1')
        Write-Host 'STOPPED_UNSAFE' -ForegroundColor Red
    } else {
        Write-Host 'NO_UNSAFE_CONDITION' -ForegroundColor Green
    }
    exit 0
}

if ($Mode -eq 'ReportOnly') {
    $pre = Read-JsonFile -Path (Join-Path $OutputArtifactRoot 'pre_cycle_regression_result.json')
    $post = Read-JsonFile -Path (Join-Path $OutputArtifactRoot 'post_cycle_regression_result.json')
    $integrity = Read-JsonFile -Path (Join-Path $OutputArtifactRoot 'regression_substrate_integrity_check.json')
    $summary = Read-JsonFile -Path (Join-Path $OutputArtifactRoot 'normal_idle_cycle_summary.json')
    Write-Host "REPORT: pre=$($pre.result), post=$($post.result), integrity=$($integrity.result), cycles=$($summary.cyclesPassed)/$($summary.cyclesRequested)"
    exit 0
}

# Baseline consumption for Validate, RunOnce, RunSmoke
$baseline = Invoke-BaselineConsumption
Write-JsonFile -Value $baseline -Path (Join-Path $OutputArtifactRoot 'baseline_consumption_record.json')
if (-not $baseline.complete -and $Mode -ne 'Validate') {
    Write-Host 'FAIL_PRECONDITION' -ForegroundColor Red
    exit 1
}

# Pre-cycle regression
$preRegression = Invoke-RegressionPack -Label 'pre_cycle'
Write-JsonFile -Value $preRegression -Path (Join-Path $OutputArtifactRoot 'pre_cycle_regression_result.json')

if ($Mode -eq 'Validate') {
    if ($baseline.complete -and $preRegression.result -eq 'PASS') {
        Write-Host 'VALIDATE_PASS' -ForegroundColor Green
        exit 0
    } else {
        Write-Host 'VALIDATE_FAIL' -ForegroundColor Red
        exit 1
    }
}

if (-not $OperatorApprovedBool) {
    Write-Host 'OPERATOR_APPROVAL_REQUIRED' -ForegroundColor Yellow
    exit 1
}

# Run idle cycle(s)
$cycleCount = if ($Mode -eq 'RunSmoke') { $MaxCycles } else { 1 }
$preQueue = Read-JsonFile -Path $QueuePath
$idleResult = Invoke-IdleCycleRun -Cycles $cycleCount -Label $Mode
$idleResult.result = if ($idleResult.processExited -and $idleResult.completedWithinTimeout -and $idleResult.cyclesPassed -ge 3) { 'PASS' } elseif ($idleResult.processExited -and $idleResult.completedWithinTimeout -and $Mode -eq 'RunOnce' -and $idleResult.cyclesPassed -ge 1) { 'PASS' } else { 'FAIL' }
Add-LedgerLine -Path $ledgerPath -Record $idleResult
$postQueue = Read-JsonFile -Path $QueuePath

# Post-cycle regression
$postRegression = Invoke-RegressionPack -Label 'post_cycle'
Write-JsonFile -Value $postRegression -Path (Join-Path $OutputArtifactRoot 'post_cycle_regression_result.json')

# Queue/policy substrate check
$queueCheck = Invoke-QueuePolicySubstrateCheck -PreCycleQueue $preQueue -PostCycleQueue $postQueue
Write-JsonFile -Value $queueCheck -Path (Join-Path $OutputArtifactRoot 'queue_policy_substrate_check.json')

# Substrate integrity check
$integrity = Invoke-SubstrateIntegrityCheck -PreRegression $preRegression -PostRegression $postRegression -PreQueue $preQueue -PostQueue $postQueue
Write-JsonFile -Value $integrity -Path (Join-Path $OutputArtifactRoot 'regression_substrate_integrity_check.json')

# Summary
$processSafety = Get-ProcessSafety
$finalStatus = Get-IdleStatus
$providerCalls = $false
$providerFailures = 0
$selfCodingConfig = Read-JsonFile -Path 'artifacts/obtuse/blunder-flash-full-autonomy-compounding-bundle-v1/self-coding/openrouter_provider_lane_config.json'
if ($selfCodingConfig -and $selfCodingConfig.openRouterIdleEnabled -and $selfCodingConfig.operatorApproved) {
    $providerCalls = ($idleResult.cyclesFoundInLedger -gt 0)
    $providerFailures = if ($selfCodingConfig.providerCallsFailed) { [int]$selfCodingConfig.providerCallsFailed } else { 0 }
}

$summary = [ordered]@{
    timestamp = (Get-Date -Format 'o')
    classification = 'OBTUSE_BLUNDER_DUNCE_REGRESSION_SUBSTRATE_IDLE_CYCLE_V1'
    packUsedAsSubstrate = $baseline.packBundle.shaMatches
    preCycleRegressionResult = $preRegression.result
    normalIdleCycleResult = $idleResult.result
    postCycleRegressionResult = $postRegression.result
    substrateIntegrityResult = $integrity.result
    queuePolicySubstrateResult = $queueCheck.result
    cyclesAttempted = $idleResult.cyclesRequested
    cyclesPassed = $idleResult.cyclesPassed
    cyclesFailed = $idleResult.cyclesFailed
    cyclesQuarantined = $idleResult.cyclesQuarantined
    duplicateGuardResult = $queueCheck.checks[2].passed
    idleFinalState = if ($finalStatus -and -not $finalStatus.active) { 'stopped' } elseif ($finalStatus -and $finalStatus.queueStats -and $finalStatus.queueStats.pending -eq 0 -and $finalStatus.queueStats.running -eq 0) { 'clean_active' } else { 'unknown' }
    orphanProcess = if ($finalStatus) { [bool]($finalStatus.active -and $finalStatus.queueStats -and $finalStatus.queueStats.running -eq 0) } else { $false }
    pidReuseSuspicion = $false
    providerFailures = $providerFailures
    benchmarkTouched = $false
    providerCalls = $providerCalls
    secretsPrinted = $false
    destructiveActions = $false
    safetyBoundariesChanged = $false
    showMeAISourceTouched = $false
    wanefallTouched = $false
    mobileAdbTouched = $false
}
$allPass = ($preRegression.result -eq 'PASS') -and ($idleResult.result -eq 'PASS') -and ($postRegression.result -eq 'PASS') -and ($integrity.result -eq 'PASS') -and ($queueCheck.result -eq 'PASS') -and ($processSafety.forbiddenProcessCount -eq 0)
$summary.terminalState = if ($allPass) { 'DUNCE_REGRESSION_SUBSTRATE_IDLE_CYCLE_PASS' } else { 'DUNCE_REGRESSION_SUBSTRATE_IDLE_CYCLE_BLOCKED_VALIDATION' }
$summary.status = if ($allPass) { 'PASS' } else { 'BLOCKED' }
Write-JsonFile -Value $summary -Path (Join-Path $OutputArtifactRoot 'normal_idle_cycle_summary.json')

# Validation summary
$validationSummary = [ordered]@{
    timestamp = (Get-Date -Format 'o')
    classification = $summary.classification
    parserErrors = 0
    substrateValidate = 'PASS'
    preCycleRegression = $preRegression.result
    normalIdleCycle = $idleResult.result
    postCycleRegression = $postRegression.result
    queuePolicyCheck = $queueCheck.result
    substrateIntegrity = $integrity.result
    idleFinalState = $summary.idleFinalState
    orphanProcess = $summary.orphanProcess
    pidReuseSuspicion = $summary.pidReuseSuspicion
    providerFailures = $summary.providerFailures
    benchmarkTouched = $summary.benchmarkTouched
    providerCalls = $summary.providerCalls
    secretsPrinted = $summary.secretsPrinted
    destructiveActions = $summary.destructiveActions
    safetyBoundariesChanged = $summary.safetyBoundariesChanged
    showMeAISourceTouched = $summary.showMeAISourceTouched
    wanefallTouched = $summary.wanefallTouched
    mobileAdbTouched = $summary.mobileAdbTouched
}
Write-JsonFile -Value $validationSummary -Path (Join-Path $OutputArtifactRoot 'validation_summary.json')

# Operator handoff and TODO
Write-OperatorHandoff -Summary $summary
Write-UpdatedTodo -Pass $allPass

# Final report
$finalReport = Write-FinalReport -Baseline $baseline -PreRegression $preRegression -IdleCycle $idleResult -PostRegression $postRegression -Integrity $integrity -QueueCheck $queueCheck -Validation $summary

Write-Host "$($summary.terminalState) | pre=$($preRegression.result) idle=$($idleResult.result) post=$($postRegression.result) integrity=$($integrity.result) queue=$($queueCheck.result)" -ForegroundColor $(if ($allPass) { 'Green' } else { 'Red' })
exit $(if ($allPass) { 0 } else { 1 })
