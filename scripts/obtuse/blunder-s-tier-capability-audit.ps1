#requires -Version 5.1
<#
.SYNOPSIS
    Audits Blunder coding, autonomy, self-improvement, intelligence, and capability readiness.

.DESCRIPTION
    Local-only S-tier audit. It reads existing validators and artifacts, writes
    proof under artifacts/obtuse, and does not start live benchmarks, provider
    calls, queue mutation, destructive actions, or long idle loops.
#>
[CmdletBinding()]
param(
    [ValidateSet('Validate','ReportOnly')]
    [string]$Mode = 'Validate',
    [string]$ArtifactRoot = 'artifacts/obtuse/blunder-s-tier-autonomous-capability-audit-v1',
    [string]$SelfCodingArtifactRoot = 'artifacts/obtuse/blunder-flash-full-autonomy-compounding-bundle-v1/self-coding'
)

$ErrorActionPreference = 'Stop'
$scriptRoot = $PSScriptRoot
$repoRoot = Split-Path (Split-Path $scriptRoot -Parent) -Parent
$classification = 'OBTUSE_BLUNDER_S_TIER_AUTONOMOUS_CAPABILITY_AUDIT_V1'

function Resolve-RepoPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $repoRoot $Path
}

function Write-JsonFile {
    param([object]$Value, [string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    $dir = Split-Path $resolved -Parent
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    $Value | ConvertTo-Json -Depth 80 | Set-Content -LiteralPath $resolved -Encoding UTF8
}

function Write-TextFile {
    param([string]$Value, [string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    $dir = Split-Path $resolved -Parent
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    [System.IO.File]::WriteAllText($resolved, ($Value -replace "`r?`n", "`r`n"), [System.Text.UTF8Encoding]::new($false))
}

function Read-JsonFile {
    param([string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    if (-not (Test-Path -LiteralPath $resolved)) { return $null }
    try { return Get-Content -Raw -LiteralPath $resolved | ConvertFrom-Json -ErrorAction Stop } catch { return $null }
}

function Read-JsonLines {
    param([string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    $rows = @()
    if (-not (Test-Path -LiteralPath $resolved)) { return $rows }
    foreach ($line in Get-Content -LiteralPath $resolved) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        try { $rows += ($line | ConvertFrom-Json -ErrorAction Stop) } catch {}
    }
    return $rows
}

function Test-True {
    param([object]$Value)
    if ($Value -is [bool]) { return [bool]$Value }
    return ([string]$Value).Trim().ToLowerInvariant() -in @('true','1','yes','pass','ok')
}

function Invoke-JsonScript {
    param(
        [string]$Name,
        [string]$ScriptPath,
        [string[]]$Arguments,
        [string]$OutputRoot,
        [int]$TimeoutSeconds
    )

    $stdout = Join-Path $OutputRoot "$Name.stdout.json"
    $stderr = Join-Path $OutputRoot "$Name.stderr.log"
    $args = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $ScriptPath) + $Arguments
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = 'powershell.exe'
    $info.WorkingDirectory = $repoRoot
    $info.Arguments = (($args | ForEach-Object { '"' + ([string]$_).Replace('"', '\"') + '"' }) -join ' ')
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true

    $proc = [System.Diagnostics.Process]::Start($info)
    $out = $proc.StandardOutput.ReadToEndAsync()
    $err = $proc.StandardError.ReadToEndAsync()
    $finished = $proc.WaitForExit($TimeoutSeconds * 1000)
    if (-not $finished) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        $proc.WaitForExit()
    }
    Set-Content -LiteralPath $stdout -Value $out.Result -Encoding UTF8
    Set-Content -LiteralPath $stderr -Value $err.Result -Encoding UTF8
    $json = $null
    $parseError = $null
    try {
        $json = Get-Content -Raw -LiteralPath $stdout | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        $parseError = $_.Exception.Message
    }

    return [ordered]@{
        name = $Name
        script = $ScriptPath
        arguments = $Arguments
        exitCode = if ($finished) { $proc.ExitCode } else { 124 }
        timedOut = -not $finished
        stdout = $stdout
        stderr = $stderr
        json = $json
        parseError = $parseError
    }
}

function New-Gate {
    param(
        [string]$Category,
        [string]$Name,
        [bool]$Pass,
        [int]$Weight,
        [bool]$Critical,
        [string]$Detail
    )
    return [ordered]@{
        category = $Category
        name = $Name
        pass = $Pass
        weight = $Weight
        critical = $Critical
        detail = $Detail
    }
}

function Test-CognitionContract {
    $sourcePath = Join-Path $repoRoot 'blunder/cognition.py'
    $runnerPath = Join-Path $repoRoot 'blunder/native_agent.py'
    $testPath = Join-Path $repoRoot 'tests/test_blunder_cognition.py'
    if (-not (Test-Path -LiteralPath $sourcePath) -or -not (Test-Path -LiteralPath $runnerPath) -or -not (Test-Path -LiteralPath $testPath)) {
        return $false
    }
    $source = Get-Content -Raw -LiteralPath $sourcePath
    $runner = Get-Content -Raw -LiteralPath $runnerPath
    $requiredSource = @('REQUIRED_LENSES','build_evidence','validate_strategy','diversity_score','history_adjustment','select_plan')
    $requiredRunner = @('request_strategy','load_learning_history','selected_targets','preflight_court','compile_context','brokered_run')
    foreach ($marker in $requiredSource) {
        if (-not $source.Contains($marker)) { return $false }
    }
    foreach ($marker in $requiredRunner) {
        if (-not $runner.Contains($marker)) { return $false }
    }
    return $true
}

function Invoke-CognitionTests {
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = 'python.exe'
    $info.WorkingDirectory = $repoRoot
    $info.Arguments = '-m unittest -q tests.test_blunder_cognition'
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = [System.Diagnostics.Process]::Start($info)
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    $finished = $process.WaitForExit(60000)
    if (-not $finished) {
        Stop-Process -Id $process.Id -Force
        $process.WaitForExit()
    }
    return [ordered]@{
        pass = ($finished -and $process.ExitCode -eq 0)
        detail = (($stdout.Result + [Environment]::NewLine + $stderr.Result).Trim())
    }
}

function Get-CandidateQuality {
    param([string]$Path)
    $record = Read-JsonFile -Path $Path
    $candidate = if ($record -and $record.candidate) { $record.candidate } else { $record }
    $issues = @()
    if (-not $candidate) {
        $nativeContract = Test-CognitionContract
        return [ordered]@{
            present = $false
            pass = $nativeContract
            score = $(if ($nativeContract) { 100 } else { 0 })
            issues = $(if ($nativeContract) { @('no_candidate_pending') } else { @('native_verification_contract_missing') })
        }
    }
    foreach ($field in @('title','problem','targetFiles','patchPlan','validationPlan','rollbackPlan','expectedProof','riskLevel','qualityContract')) {
        if (-not ($candidate.PSObject.Properties.Name -contains $field) -or $null -eq $candidate.$field -or [string]::IsNullOrWhiteSpace([string]($candidate.$field | Out-String))) {
            $issues += "${field}_missing"
        }
    }
    if ([string]$candidate.riskLevel -ne 'LOW') { $issues += 'risk_not_low' }
    if (@($candidate.targetFiles).Count -ne 1) { $issues += 'target_file_count_not_one' }
    foreach ($target in @($candidate.targetFiles)) {
        if ([string]$target -notmatch '^(?i)scripts[\\/]obtuse[\\/](idle-generated-compound-\d+-.+|idle-self-coding-accelerator|openrouter-idle-model|idle-improvement-thresholds|blunder-compound-evolution-common)\.ps1$') {
            $issues += "target_not_approved_self_improvement_surface:$target"
        }
    }
    $contract = $candidate.qualityContract
    foreach ($field in @('rootCause','evidenceSource','validationGate','rollbackGate','promotionStop')) {
        if (-not $contract -or -not ($contract.PSObject.Properties.Name -contains $field) -or [string]::IsNullOrWhiteSpace([string]$contract.$field)) {
            $issues += "quality_contract_missing_$field"
        }
    }
    $joined = ($candidate | ConvertTo-Json -Depth 20)
    if ($joined -match '(?i)benchmark\s+(launch|run|relaunch|execute)|terminal-bench\s+(launch|run|relaunch|execute)|git\s+(push|reset)|secret|sk-or-|sk-proj-|Remove-Item|delete|wipe|destroy') {
        $issues += 'forbidden_scope_text'
    }
    $score = [Math]::Max(0, 100 - ($issues.Count * 10))
    return [ordered]@{
        pass = ($issues.Count -eq 0)
        score = $score
        title = [string]$candidate.title
        targetFiles = @($candidate.targetFiles)
        issues = $issues
    }
}

function Get-RecentMutationMetrics {
    param([string]$ArtifactRoot)
    $rows = @(Read-JsonLines -Path (Join-Path $ArtifactRoot 'self_coding_patch_ledger.jsonl'))
    $recent = @($rows | Select-Object -Last 50)
    $applied = @($recent | Where-Object { $_.patchApplied -eq $true })
    $meaningful = @($applied | Where-Object { [int]($_.sourceLinesChanged -as [int]) -gt 0 })
    $rollback = @($applied | Where-Object { $_.rollbackSnapshot -and $_.rollbackSnapshot.exists -eq $true })
    $families = @()
    foreach ($row in $recent) {
        $patchType = [string]$row.patchType
        if ($patchType -match 'IDLE_GENERATED_COMPOUND_\d+_([A-Z0-9]+)') {
            $families += $Matches[1]
        } elseif (-not [string]::IsNullOrWhiteSpace($patchType)) {
            $families += $patchType
        }
    }
    $familyCount = @($families | Sort-Object -Unique).Count
    $recentCount = $recent.Count
    $appliedRate = if ($recentCount -gt 0) { [Math]::Round($applied.Count / $recentCount, 4) } else { 0 }
    $rollbackRate = if ($applied.Count -gt 0) { [Math]::Round($rollback.Count / $applied.Count, 4) } else { 0 }
    $meaningfulRate = if ($applied.Count -gt 0) { [Math]::Round($meaningful.Count / $applied.Count, 4) } else { 0 }
    return [ordered]@{
        recentRecords = $recentCount
        recentApplied = $applied.Count
        familyCount = $familyCount
        appliedRate = $appliedRate
        rollbackRate = $rollbackRate
        meaningfulRate = $meaningfulRate
        families = @($families | Sort-Object -Unique)
    }
}

function Get-CategoryScores {
    param([object[]]$Gates)
    $scores = @()
    $categories = @($Gates | ForEach-Object { [string]$_['category'] } | Sort-Object -Unique)
    foreach ($category in $categories) {
        $group = @($Gates | Where-Object { [string]$_['category'] -eq $category })
        $max = 0
        $score = 0
        foreach ($gate in $group) {
            $max += [int]$gate.weight
            if ($gate.pass -eq $true) { $score += [int]$gate.weight }
        }
        $scores += [ordered]@{
            category = $category
            score = $score
            maxScore = $max
            scorePercent = if ($max -gt 0) { [Math]::Round(($score / $max) * 100, 2) } else { 0 }
            failed = @($group | Where-Object { $_.pass -ne $true } | ForEach-Object { $_.name })
        }
    }
    return @($scores | Sort-Object category)
}

function Get-ProbeSummaries {
    param([object]$Probes)
    $items = @()
    foreach ($key in $Probes.Keys) {
        $probe = $Probes[$key]
        $json = $probe.json
        $items += [ordered]@{
            name = $key
            exitCode = $probe.exitCode
            timedOut = $probe.timedOut
            stdout = $probe.stdout
            stderr = $probe.stderr
            parseError = $probe.parseError
            status = if ($json -and ($json.PSObject.Properties.Name -contains 'status')) { [string]$json.status } else { $null }
            result = if ($json -and ($json.PSObject.Properties.Name -contains 'result')) { [string]$json.result } else { $null }
            verdict = if ($json -and ($json.PSObject.Properties.Name -contains 'verdict')) { [string]$json.verdict } else { $null }
            pass = if ($json -and ($json.PSObject.Properties.Name -contains 'pass')) { $json.pass } else { $null }
        }
    }
    return $items
}

function New-MarkdownReport {
    param([object]$Report)
    $failed = @($Report.gates | Where-Object { $_.pass -ne $true })
    $lines = [System.Collections.ArrayList]@()
    [void]$lines.Add('# Blunder S-Tier Autonomous Capability Audit')
    [void]$lines.Add('')
    [void]$lines.Add("Timestamp: $($Report.timestamp)")
    [void]$lines.Add("Verdict: $($Report.verdict)")
    [void]$lines.Add("Score: $($Report.score) / $($Report.maxScore) ($($Report.scorePercent)%)")
    [void]$lines.Add("Provider calls started: $($Report.policy.providerCallsStarted)")
    [void]$lines.Add("Live launches started: $($Report.policy.liveLaunchesStarted)")
    [void]$lines.Add("Queue mutation performed: $($Report.policy.queueMutationPerformed)")
    [void]$lines.Add('')
    [void]$lines.Add('## Category Scores')
    foreach ($category in $Report.categoryScores) {
        [void]$lines.Add("- $($category.category): $($category.scorePercent)%")
    }
    [void]$lines.Add('')
    [void]$lines.Add('## Failed Gates')
    if ($failed.Count -eq 0) {
        [void]$lines.Add('- none')
    } else {
        foreach ($gate in $failed) {
            [void]$lines.Add("- $($gate.category)/$($gate.name): $($gate.detail)")
        }
    }
    [void]$lines.Add('')
    [void]$lines.Add('## Evidence')
    [void]$lines.Add("- planning gate: $($Report.evidence.planning.verdict) / $($Report.evidence.planning.scorePercent)%")
    [void]$lines.Add("- idle status: $($Report.evidence.idle.status), reliability=$($Report.evidence.idle.reliabilityBudgetStatus), action=$($Report.evidence.idle.finalRecommendedOperatorAction)")
    [void]$lines.Add("- recent mutation families: $($Report.evidence.recentMutations.familyCount), rollbackRate=$($Report.evidence.recentMutations.rollbackRate)")
    [void]$lines.Add("- candidate quality: $($Report.evidence.candidateQuality.score), issues=$($Report.evidence.candidateQuality.issues.Count)")
    return ($lines -join "`r`n")
}

$runId = Get-Date -Format 'yyyyMMdd-HHmmss'
$runRoot = Resolve-RepoPath -Path (Join-Path $ArtifactRoot $runId)
New-Item -ItemType Directory -Force -Path $runRoot | Out-Null

$scripts = [ordered]@{
    nativeIntegration = Join-Path $scriptRoot 'blunder-native-integration-proof.ps1'
    planning = Join-Path $scriptRoot 'blunder.ps1'
    status = Join-Path $scriptRoot 'status-idle-ops.ps1'
    intelligence = Join-Path $scriptRoot 'idle-intelligence-v2.ps1'
    localValidation = Join-Path $scriptRoot 'idle-validation.ps1'
    ledger = Join-Path $scriptRoot 'idle-self-coding-ledger-summary.ps1'
    thresholds = Join-Path $scriptRoot 'idle-improvement-thresholds.ps1'
    selfCodingHealth = Join-Path $scriptRoot 'idle-openrouter-self-coding-health.ps1'
    candidateCatalog = Join-Path $scriptRoot 'idle-self-coding-candidate-catalog.ps1'
    dunceBridge = Join-Path $scriptRoot 'blunder-dunce-to-self-coding-queue-bridge.ps1'
    capabilityPackets = Join-Path $scriptRoot 'blunder-capability-repair-packets.ps1'
}

$probes = [ordered]@{}
$probes.nativeIntegration = Invoke-JsonScript -Name 'native-integration' -ScriptPath $scripts.nativeIntegration -Arguments @('-Mode','Validate') -OutputRoot $runRoot -TimeoutSeconds 160
$probes.planning = Invoke-JsonScript -Name 'planning' -ScriptPath $scripts.planning -Arguments @('-Command','PlanningCodingGate') -OutputRoot $runRoot -TimeoutSeconds 90
$probes.status = Invoke-JsonScript -Name 'status' -ScriptPath $scripts.status -Arguments @() -OutputRoot $runRoot -TimeoutSeconds 90
$probes.intelligence = Invoke-JsonScript -Name 'intelligence' -ScriptPath $scripts.intelligence -Arguments @('-Action','ValidateGates') -OutputRoot $runRoot -TimeoutSeconds 90
$probes.localValidation = Invoke-JsonScript -Name 'local-validation' -ScriptPath $scripts.localValidation -Arguments @('-Action','Composite','-TargetPath',$repoRoot) -OutputRoot $runRoot -TimeoutSeconds 120
$probes.ledger = Invoke-JsonScript -Name 'self-coding-ledger' -ScriptPath $scripts.ledger -Arguments @('-Mode','Validate') -OutputRoot $runRoot -TimeoutSeconds 60
$probes.thresholds = Invoke-JsonScript -Name 'thresholds' -ScriptPath $scripts.thresholds -Arguments @('-Mode','Validate') -OutputRoot $runRoot -TimeoutSeconds 60
$probes.selfCodingHealth = Invoke-JsonScript -Name 'self-coding-health' -ScriptPath $scripts.selfCodingHealth -Arguments @('-Mode','Validate') -OutputRoot $runRoot -TimeoutSeconds 60
$probes.candidateCatalog = Invoke-JsonScript -Name 'candidate-catalog' -ScriptPath $scripts.candidateCatalog -Arguments @('-Mode','Validate') -OutputRoot $runRoot -TimeoutSeconds 60
$probes.dunceBridge = Invoke-JsonScript -Name 'dunce-bridge' -ScriptPath $scripts.dunceBridge -Arguments @('-Mode','Validate') -OutputRoot $runRoot -TimeoutSeconds 60
$probes.capabilityPackets = Invoke-JsonScript -Name 'capability-packets' -ScriptPath $scripts.capabilityPackets -Arguments @('-Mode','Validate') -OutputRoot $runRoot -TimeoutSeconds 60

$nativeIntegration = $probes.nativeIntegration.json
$planning = $probes.planning.json
$status = $probes.status.json
$intelligence = $probes.intelligence.json
$localValidation = $probes.localValidation.json
$ledger = $probes.ledger.json
$thresholds = $probes.thresholds.json
$selfCodingHealth = $probes.selfCodingHealth.json
$candidateCatalog = $probes.candidateCatalog.json
$dunceBridge = $probes.dunceBridge.json
$capabilityPackets = $probes.capabilityPackets.json

$cognitionContract = Test-CognitionContract
$cognitionTests = Invoke-CognitionTests
$cognitionTestsPass = [bool]$cognitionTests.pass
$cognitionTestDetail = [string]$cognitionTests.detail

$candidateQuality = Get-CandidateQuality -Path (Join-Path $SelfCodingArtifactRoot 'model_generated_candidate_task.json')
$generationState = Read-JsonFile -Path (Join-Path $SelfCodingArtifactRoot 'model_generation_summary.json')
$thresholdState = Read-JsonFile -Path (Join-Path $SelfCodingArtifactRoot 'threshold_decision_latest.json')
$candidateLaneSafe = (
    ($thresholdState -and $thresholdState.candidateAccepted -eq $true) -or
    ($generationState -and [string]$generationState.result -eq 'NO_CAPABILITY_CANDIDATE' -and $generationState.providerCallAttempted -ne $true) -or
    (-not $candidateQuality.present -and $status -and $status.safetyState.providerCallsAllowed -ne $true)
)
$recentMutations = Get-RecentMutationMetrics -ArtifactRoot $SelfCodingArtifactRoot

$queueStats = if ($status) { $status.queueStats } else { $null }
$safetyState = if ($status) { $status.safetyState } else { $null }
$idleHealthy = (
    $status -and
    $status.active -eq $true -and
    $status.pidValid -eq $true -and
    $status.orphanProcess -ne $true -and
    $status.pidReuseSuspicion -ne $true -and
    [string]$status.finalRecommendedOperatorAction -eq 'NO_ACTION_IDLE_HEALTHY'
)
$safetyClean = (
    $safetyState -and
    $safetyState.destructiveActionsAllowed -ne $true -and
    $safetyState.secretsPrinted -ne $true -and
    $safetyState.benchmarkRelaunchAllowed -ne $true -and
    $safetyState.safetyBoundaryModificationAllowed -ne $true
)
$queueClean = (
    $queueStats -and
    [int]$queueStats.failed -eq 0 -and
    [int]$queueStats.blocked -eq 0 -and
    [int]$queueStats.quarantined -eq 0
)
$noMutationAttempts = ([int]$recentMutations.recentRecords -eq 0)
$liveReliabilityGreen = (
    $idleHealthy -and
    $queueClean -and
    $status.rawRunnerStatus -and
    [int]$status.rawRunnerStatus.state.cyclesRun -ge 3
)
$capabilityPackCount = @(Get-ChildItem -LiteralPath (Join-Path $repoRoot 'blunder/capability_packs') -Directory -ErrorAction SilentlyContinue).Count
$firstPartyCapabilityFabric = (
    (Test-Path -LiteralPath (Join-Path $repoRoot 'blunder/capabilities/registry.py')) -and
    $capabilityPackCount -ge 6
)
$bridgeSources = if ($dunceBridge -and $dunceBridge.sourcesPresent) { @($dunceBridge.sourcesPresent.PSObject.Properties | Where-Object { $_.Value -eq $true }) } else { @() }
$bridgeSourceTotal = if ($dunceBridge -and $dunceBridge.sourcesPresent) { @($dunceBridge.sourcesPresent.PSObject.Properties).Count } else { 0 }

$gates = @(
    (New-Gate -Category 'coding' -Name 'nativeIntegrationProofPass' -Pass ($nativeIntegration -and $nativeIntegration.pass -eq $true) -Weight 12 -Critical $true -Detail "status=$(if ($nativeIntegration) { $nativeIntegration.status } else { 'missing' })"),
    (New-Gate -Category 'coding' -Name 'planningCodingElite' -Pass ($planning -and [string]$planning.verdict -eq 'PLANNING_CODING_ELITE_READY' -and [double]$planning.scorePercent -ge 95) -Weight 12 -Critical $true -Detail "verdict=$(if ($planning) { $planning.verdict } else { 'missing' }); score=$(if ($planning) { $planning.scorePercent } else { 'missing' })"),
    (New-Gate -Category 'coding' -Name 'localValidationPass' -Pass ($localValidation -and $localValidation.pass -eq $true) -Weight 8 -Critical $true -Detail "pass=$(if ($localValidation) { $localValidation.pass } else { 'missing' })"),
    (New-Gate -Category 'selfImprovement' -Name 'thresholdContractPass' -Pass ($thresholds -and [string]$thresholds.result -eq 'PASS' -and [int]$thresholds.thresholds.planningQuality -ge 85) -Weight 10 -Critical $true -Detail "result=$(if ($thresholds) { $thresholds.result } else { 'missing' }); planningQuality=$(if ($thresholds) { $thresholds.thresholds.planningQuality } else { 'missing' })"),
    (New-Gate -Category 'selfImprovement' -Name 'duplicatePressureLow' -Pass ($ledger -and [string]$ledger.duplicatePressure -eq 'LOW' -and [int]$ledger.activeDuplicateGroupCount -eq 0) -Weight 8 -Critical $true -Detail "duplicatePressure=$(if ($ledger) { $ledger.duplicatePressure } else { 'missing' }); activeGroups=$(if ($ledger) { $ledger.activeDuplicateGroupCount } else { 'missing' })"),
    (New-Gate -Category 'selfImprovement' -Name 'recentMutationRollbackCoverage' -Pass ($noMutationAttempts -or ($recentMutations.recentRecords -ge 10 -and [double]$recentMutations.rollbackRate -ge 0.95 -and [double]$recentMutations.meaningfulRate -ge 0.95)) -Weight 8 -Critical $true -Detail "recent=$($recentMutations.recentRecords); noAttempts=$noMutationAttempts; rollbackRate=$($recentMutations.rollbackRate); meaningfulRate=$($recentMutations.meaningfulRate)"),
    (New-Gate -Category 'selfImprovement' -Name 'recentMutationFamilyDiversity' -Pass ($noMutationAttempts -or [int]$recentMutations.familyCount -ge 6) -Weight 6 -Critical $false -Detail "families=$($recentMutations.familyCount); noAttempts=$noMutationAttempts"),
    (New-Gate -Category 'selfImprovement' -Name 'selfCodingHealthPass' -Pass ($selfCodingHealth -and [string]$selfCodingHealth.result -eq 'PASS' -and $selfCodingHealth.secretsPrinted -ne $true) -Weight 8 -Critical $true -Detail "result=$(if ($selfCodingHealth) { $selfCodingHealth.result } else { 'missing' }); calls=$(if ($selfCodingHealth) { $selfCodingHealth.callLedgerEntries } else { 'missing' })"),
    (New-Gate -Category 'autonomy' -Name 'idleRuntimeHealthy' -Pass $idleHealthy -Weight 12 -Critical $true -Detail "status=$(if ($status) { $status.status } else { 'missing' }); action=$(if ($status) { $status.finalRecommendedOperatorAction } else { 'missing' })"),
    (New-Gate -Category 'autonomy' -Name 'reliabilityBudgetGreen' -Pass (($status -and [string]$status.reliabilityBudgetStatus -eq 'WITHIN_BUDGET') -or $liveReliabilityGreen) -Weight 10 -Critical $true -Detail "artifact=$(if ($status) { $status.reliabilityBudgetStatus } else { 'missing' }); liveEvidence=$liveReliabilityGreen"),
    (New-Gate -Category 'autonomy' -Name 'queueClean' -Pass $queueClean -Weight 8 -Critical $true -Detail "failed=$(if ($queueStats) { $queueStats.failed } else { 'missing' }); blocked=$(if ($queueStats) { $queueStats.blocked } else { 'missing' }); quarantined=$(if ($queueStats) { $queueStats.quarantined } else { 'missing' })"),
    (New-Gate -Category 'autonomy' -Name 'safetyStateClean' -Pass $safetyClean -Weight 10 -Critical $true -Detail "destructive=$(if ($safetyState) { $safetyState.destructiveActionsAllowed } else { 'missing' }); benchmark=$(if ($safetyState) { $safetyState.benchmarkRelaunchAllowed } else { 'missing' }); secrets=$(if ($safetyState) { $safetyState.secretsPrinted } else { 'missing' })"),
    (New-Gate -Category 'intelligence' -Name 'idleIntelligenceV2Pass' -Pass ($intelligence -and $intelligence.pass -eq $true) -Weight 10 -Critical $true -Detail "pass=$(if ($intelligence) { $intelligence.pass } else { 'missing' })"),
    (New-Gate -Category 'intelligence' -Name 'candidateQualityContractComplete' -Pass ([bool]$candidateQuality.pass) -Weight 10 -Critical $true -Detail "score=$($candidateQuality.score); issues=$($candidateQuality.issues -join ',')"),
    (New-Gate -Category 'intelligence' -Name 'candidateLaneDecisionSound' -Pass $candidateLaneSafe -Weight 6 -Critical $true -Detail "accepted=$(if ($thresholdState) { $thresholdState.candidateAccepted } else { 'missing' }); generation=$(if ($generationState) { $generationState.result } else { 'missing' }); providerAttempted=$(if ($generationState) { $generationState.providerCallAttempted } else { 'missing' })"),
    (New-Gate -Category 'intelligence' -Name 'nativeCognitionContractComplete' -Pass $cognitionContract -Weight 10 -Critical $true -Detail "contract=$cognitionContract"),
    (New-Gate -Category 'intelligence' -Name 'nativeCognitionTestsPass' -Pass $cognitionTestsPass -Weight 8 -Critical $true -Detail "pass=$cognitionTestsPass; output=$cognitionTestDetail"),
    (New-Gate -Category 'intelligence' -Name 'candidateCatalogPass' -Pass ($candidateCatalog -and [string]$candidateCatalog.result -eq 'PASS' -and @($candidateCatalog.forbiddenScopes).Count -ge 6) -Weight 6 -Critical $false -Detail "result=$(if ($candidateCatalog) { $candidateCatalog.result } else { 'missing' }); forbiddenScopes=$(if ($candidateCatalog) { @($candidateCatalog.forbiddenScopes).Count } else { 0 })"),
    (New-Gate -Category 'capability' -Name 'firstPartyCapabilityFabricSealed' -Pass $firstPartyCapabilityFabric -Weight 8 -Critical $true -Detail "packs=$capabilityPackCount; registry=$firstPartyCapabilityFabric"),
    (New-Gate -Category 'capability' -Name 'capabilityRepairPacketsPass' -Pass ($capabilityPackets -and [string]$capabilityPackets.result -eq 'PASS' -and $capabilityPackets.liveLaunchAllowed -eq $false -and $capabilityPackets.providerCallsAllowed -eq $false) -Weight 8 -Critical $true -Detail "result=$(if ($capabilityPackets) { $capabilityPackets.result } else { 'missing' }); packets=$(if ($capabilityPackets) { $capabilityPackets.packetCount } else { 'missing' })"),
    (New-Gate -Category 'capability' -Name 'capabilityFailurePacketsAvailable' -Pass ($capabilityPackets -and [int]$capabilityPackets.packetCount -eq [int]$capabilityPackets.failingFamilyCount) -Weight 6 -Critical $false -Detail "packetCount=$(if ($capabilityPackets) { $capabilityPackets.packetCount } else { 'missing' }); failingFamilies=$(if ($capabilityPackets) { $capabilityPackets.failingFamilyCount } else { 'missing' })")
)

$maxScore = 0
$score = 0
foreach ($gate in $gates) {
    $maxScore += [int]$gate.weight
    if ($gate.pass -eq $true) { $score += [int]$gate.weight }
}
$scorePercent = if ($maxScore -gt 0) { [Math]::Round(($score / $maxScore) * 100, 2) } else { 0 }
$criticalFailures = @($gates | Where-Object { $_.critical -eq $true -and $_.pass -ne $true })
$failed = @($gates | Where-Object { $_.pass -ne $true })
$verdict = if ($criticalFailures.Count -eq 0 -and $scorePercent -ge 98) {
    'BLUNDER_S_TIER_AUTONOMOUS_CAPABILITY_READY'
} elseif ($criticalFailures.Count -eq 0 -and $scorePercent -ge 92) {
    'BLUNDER_AUTONOMOUS_CAPABILITY_STRONG_WITH_NOTES'
} else {
    'BLUNDER_AUTONOMOUS_CAPABILITY_REPAIR_REQUIRED'
}

$report = [ordered]@{
    classification = $classification
    timestamp = (Get-Date -Format 'o')
    mode = $Mode
    runId = $runId
    repoRoot = $repoRoot
    artifactRoot = $runRoot
    verdict = $verdict
    score = $score
    maxScore = $maxScore
    scorePercent = $scorePercent
    criticalFailureCount = $criticalFailures.Count
    failedGateCount = $failed.Count
    gates = $gates
    categoryScores = Get-CategoryScores -Gates $gates
    evidence = [ordered]@{
        idle = if ($status) { [ordered]@{ status = $status.status; active = $status.active; pidValid = $status.pidValid; reliabilityBudgetStatus = $status.reliabilityBudgetStatus; finalRecommendedOperatorAction = $status.finalRecommendedOperatorAction; queueStats = $status.queueStats; safetyState = $status.safetyState } } else { $null }
        planning = if ($planning) { [ordered]@{ verdict = $planning.verdict; scorePercent = $planning.scorePercent } } else { $null }
        nativeIntegration = if ($nativeIntegration) { [ordered]@{ status = $nativeIntegration.status; pass = $nativeIntegration.pass; failedChecks = @($nativeIntegration.failedChecks).Count } } else { $null }
        intelligence = if ($intelligence) { [ordered]@{ pass = $intelligence.pass; gates = $intelligence.gates } } else { $null }
        selfCodingLedger = if ($ledger) { [ordered]@{ patchRecords = $ledger.patchRecords; appliedPatchRecords = $ledger.appliedPatchRecords; duplicatePressure = $ledger.duplicatePressure; activeDuplicateGroupCount = $ledger.activeDuplicateGroupCount; providerCallsAttempted = $ledger.providerCallsAttempted; providerCallsSucceeded = $ledger.providerCallsSucceeded } } else { $null }
        recentMutations = $recentMutations
        candidateQuality = $candidateQuality
        candidateLane = [ordered]@{
            safe = $candidateLaneSafe
            candidateAccepted = $(if ($thresholdState) { $thresholdState.candidateAccepted } else { $null })
            thresholdDecision = $(if ($thresholdState) { $thresholdState.thresholdDecision } else { $null })
            generationResult = $(if ($generationState) { $generationState.result } else { $null })
            providerCallAttempted = $(if ($generationState) { $generationState.providerCallAttempted } else { $null })
        }
        capabilityPackets = if ($capabilityPackets) { [ordered]@{ result = $capabilityPackets.result; failingFamilyCount = $capabilityPackets.failingFamilyCount; packetCount = $capabilityPackets.packetCount } } else { $null }
        dunceBridge = if ($dunceBridge) { [ordered]@{ queueAccessible = $dunceBridge.queueAccessible; sourcesPresent = $dunceBridge.sourcesPresent } } else { $null }
        localValidation = if ($localValidation) { [ordered]@{ pass = $localValidation.pass; syntaxPass = $localValidation.details.syntax.pass; jsonPass = $localValidation.details.json.pass; secretsPass = $localValidation.details.secrets.pass } } else { $null }
    }
    probes = Get-ProbeSummaries -Probes $probes
    policy = [ordered]@{
        providerCallsStarted = $false
        liveLaunchesStarted = $false
        queueMutationPerformed = $false
        destructiveActionsStarted = $false
        benchmarkWorkExcluded = $true
    }
}

$summaryPath = Join-Path $runRoot 'SUMMARY.json'
$latestSummaryPath = Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'SUMMARY.latest.json')
$reportPath = Join-Path $runRoot 'REPORT.md'
$latestReportPath = Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'REPORT.latest.md')
$markdown = New-MarkdownReport -Report $report
Write-JsonFile -Value $report -Path $summaryPath
Write-JsonFile -Value $report -Path $latestSummaryPath
Write-TextFile -Value $markdown -Path $reportPath
Write-TextFile -Value $markdown -Path $latestReportPath
$report['summaryPath'] = $summaryPath
$report['reportPath'] = $reportPath
$report | ConvertTo-Json -Depth 80

if ($Mode -eq 'Validate' -and $verdict -eq 'BLUNDER_AUTONOMOUS_CAPABILITY_REPAIR_REQUIRED') {
    exit 1
}
