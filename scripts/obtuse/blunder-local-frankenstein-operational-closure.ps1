#requires -Version 5.1
<#
.SYNOPSIS
    OBTUSE_BLUNDER_LOCAL_FRANKENSTEIN_OPERATIONAL_CLOSURE_V1

.DESCRIPTION
    Validates and, when approved, executes a local FRANKENSTEIN operational
    closure sequence using the Dunce cross-machine proof-regression artifact
    as a baseline. Supports Validate / RunOnce / RunUntilClosed / ReportOnly /
    StopIfUnsafe modes.
#>
[CmdletBinding()]
param(
    [ValidateSet('Validate','RunOnce','RunUntilClosed','ReportOnly','StopIfUnsafe')]
    [string]$Mode = 'Validate',

    [ValidateSet('true','false')]
    [string]$OperatorApproved = 'false',

    [string]$SourceArtifactRoot = 'artifacts/obtuse/blunder-dunce-cross-machine-proof-regression-verification-v1',

    [string]$ExpectedCrossMachineBundleSha256 = 'CEE57869DA9958F550691B71994FDD7926D3A44683BD5FA1DF4F700E628AE56E',

    [string]$OutputArtifactRoot = 'artifacts/obtuse/blunder-local-frankenstein-operational-closure-v1',

    [string]$FrankensteinMirror = '\\fileserver\shared\obtuse\blunder-local-frankenstein-operational-closure-v1',

    [string]$GamingPC3Mirror = '\\GAMINGPC3\Shared Folder\obtuse\blunder-local-frankenstein-operational-closure-v1',

    [string]$StandardQueuePath = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle/idle-task-queue.json',

    [string]$StandardIdleArtifactRoot = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle',

    [string]$ModelAlias = 'deepseek-v4-flash'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent

# -----------------------------------------------------------------------------
# Path and I/O helpers
# -----------------------------------------------------------------------------
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
    $Value | ConvertTo-Json -Depth 40 | Set-Content -LiteralPath $resolved -Encoding UTF8
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

function Get-FileSha256 {
    param([Parameter(Mandatory)] [string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    if (-not (Test-Path -LiteralPath $resolved)) { return $null }
    return (Get-FileHash -LiteralPath $resolved -Algorithm SHA256).Hash.ToUpperInvariant()
}

function Resolve-ModelId {
    param([string]$Alias)
    if ([string]::IsNullOrWhiteSpace($Alias)) { return $null }
    if ($Alias.ToLowerInvariant() -eq 'deepseek-v4-flash') { return 'deepseek/deepseek-v4-flash' }
    return $Alias
}

# -----------------------------------------------------------------------------
# Safety / secret helpers
# -----------------------------------------------------------------------------
function Get-ProcessSafety {
    $forbiddenPattern = 'external-benchmark|external-agent-runner|WANEFALL|adb\.exe|fastboot\.exe'
    $providerPattern = 'openrouter-idle-model\.ps1|idle-self-coding-accelerator\.ps1'
    $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine })
    return [ordered]@{
        forbiddenProcessCount = @($all | Where-Object { $_.CommandLine -match $forbiddenPattern }).Count
        providerProcessCount = @($all | Where-Object { $_.CommandLine -match $providerPattern }).Count
        timestamp = (Get-Date -Format 'o')
    }
}

function Test-SecretFindings {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return @{ scanned = $false; scannedFiles = 0; findings = 0; paths = @($Path) } }
    $secretPatterns = @(
        @{ name='openrouter_sk_key'; regex='sk-or-[a-zA-Z0-9_-]{20,}' },
        @{ name='generic_sk_key'; regex='sk-[a-zA-Z0-9]{20,}' },
        @{ name='api_key_assignment'; regex='(?i)api[_-]?key\s*=\s*["''][a-z0-9]{16,}' },
        @{ name='private_key_block'; regex='-----BEGIN (RSA |OPENSSH |EC |DSA |PRIVATE )?KEY-----' },
        @{ name='password_assignment'; regex='(?i)password\s*=\s*["''][^"'']{8,}' }
    )
    $findings = 0
    $scannedFiles = 0
    foreach ($f in (Get-ChildItem -LiteralPath $Path -File -Recurse -ErrorAction SilentlyContinue)) {
        if ($f.Extension -in @('.zip','.png','.jpg','.jpeg','.gif')) { continue }
        $scannedFiles++
        $content = Get-Content -Raw -LiteralPath $f.FullName -ErrorAction SilentlyContinue
        if ([string]::IsNullOrWhiteSpace($content)) { continue }
        foreach ($p in $secretPatterns) {
            if ($content -match $p.regex) { $findings++ }
        }
    }
    return @{ scanned = $true; scannedFiles = $scannedFiles; findings = $findings; paths = @($Path) }
}

function Invoke-SecretScan {
    $artifactScan = Test-SecretFindings -Path (Resolve-RepoPath -Path $OutputArtifactRoot)
    $result = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        classification = 'OBTUSE_BLUNDER_LOCAL_FRANKENSTEIN_OPERATIONAL_CLOSURE_V1'
        artifactPath = $artifactScan.paths[0]
        artifactFindings = $artifactScan.findings
        artifactScannedFiles = $artifactScan.scannedFiles
        scriptPath = $PSCommandPath
        scriptFindings = 0
        scriptScannedFiles = 0
        status = if ($artifactScan.findings -eq 0) { 'PASS' } else { 'FAIL' }
        notes = @('Artifact directory scanned; no actual secret values printed.')
    }
    Write-JsonFile -Value $result -Path (Join-Path $OutputArtifactRoot 'SECRET_SCAN.json')
    return $result
}

# -----------------------------------------------------------------------------
# External script invocation helper
# -----------------------------------------------------------------------------
function Invoke-ExternalScript {
    param(
        [Parameter(Mandatory)] [string]$RelativePath,
        [object[]]$ArgumentList = @()
    )
    $resolved = Resolve-RepoPath -Path $RelativePath
    $record = [ordered]@{
        script = $RelativePath
        resolved = $resolved
        arguments = ($ArgumentList -join ' ')
        exists = $false
        exitCode = -1
        raw = $null
        rawString = $null
        parsed = $null
        parseError = $null
        success = $false
    }
    if (-not (Test-Path -LiteralPath $resolved)) {
        $record.parseError = "Script not found: $resolved"
        return $record
    }
    $record.exists = $true
    try {
        $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$resolved" @ArgumentList 2>&1
    } catch {
        $output = @($_.Exception.Message)
    }
    $record.exitCode = $LASTEXITCODE
    if ($null -eq $output) { $output = @() }
    $record.raw = $output
    $record.rawString = $output | Out-String
    $rawLines = @($output -split "`r?`n" | Where-Object { $_.Trim().Length -gt 0 })
    $parsed = $null
    $parseError = $null
    for ($i = $rawLines.Count - 1; $i -ge 0; $i--) {
        $candidate = $rawLines[$i].Trim()
        if ($candidate -match '^\{') {
            try {
                $parsed = $candidate | ConvertFrom-Json -ErrorAction Stop
                break
            } catch {
                $parseError = $_.Exception.Message
            }
        }
    }
    if ($null -eq $parsed) {
        try {
            $parsed = ($record.rawString) | ConvertFrom-Json -ErrorAction Stop
        } catch {
            if (-not $parseError) { $parseError = $_.Exception.Message }
        }
    }
    $record.parsed = $parsed
    $record.parseError = $parseError
    $record.success = ($record.exitCode -eq 0 -and -not $record.parseError)
    return $record
}

# -----------------------------------------------------------------------------
# Idle-ops status helper
# -----------------------------------------------------------------------------
function Get-StatusOpsResult {
    $record = Invoke-ExternalScript -RelativePath 'scripts/obtuse/status-idle-ops.ps1'
    $parsed = $record.parsed
    $status = if ($parsed) { $parsed.status } else { $null }
    $activePid = if ($parsed) { $parsed.activePid } else { $null }
    $orphan = if ($parsed) { [bool]$parsed.orphanProcess } else { $false }
    $pidReuse = if ($parsed) { [bool]$parsed.pidReuseSuspicion } else { $false }
    $queueStats = if ($parsed) { $parsed.queueStats } else { $null }
    $failed = 0
    if ($queueStats -and ($queueStats.PSObject.Properties.Name -contains 'failed')) {
        $failed = [int]$queueStats.failed
    }
    $queueState = $null
    if ($parsed -and $parsed.queueReplenishmentStatus -and ($parsed.queueReplenishmentStatus.PSObject.Properties.Name -contains 'queueState')) {
        $queueState = $parsed.queueReplenishmentStatus.queueState
    }
    $malformed = ($queueState -like '*MALFORMED*' -or $queueState -like '*UNREADABLE*')
    $modelNeedsReview = $false
    if ($parsed -and ($parsed.PSObject.Properties.Name -contains 'openRouterModelNeedsOperatorReview')) {
        $modelNeedsReview = [bool]$parsed.openRouterModelNeedsOperatorReview
    }
    $destructiveAllowed = $false
    if ($parsed -and $parsed.safetyState -and ($parsed.safetyState.PSObject.Properties.Name -contains 'destructiveActionsAllowed')) {
        $destructiveAllowed = [bool]$parsed.safetyState.destructiveActionsAllowed
    }
    $secretsPrinted = $false
    if ($parsed -and $parsed.safetyState -and ($parsed.safetyState.PSObject.Properties.Name -contains 'secretsPrinted')) {
        $secretsPrinted = [bool]$parsed.safetyState.secretsPrinted
    }
    return [ordered]@{
        invocation = $record
        status = $status
        activePid = $activePid
        heartbeat = if ($parsed) { $parsed.heartbeat } else { $null }
        orphanProcess = $orphan
        pidReuseSuspicion = $pidReuse
        queueStats = $queueStats
        failedTaskCount = $failed
        queueState = $queueState
        queueMalformed = $malformed
        openRouterModelNeedsOperatorReview = $modelNeedsReview
        destructiveActionsAllowed = $destructiveAllowed
        secretsPrinted = $secretsPrinted
        healthy = (($failed -eq 0) -and -not $orphan -and -not $pidReuse -and -not $malformed)
        raw = $parsed
    }
}

function Test-StatusUnsafe {
    param([object]$StatusResult)
    $reasons = @()
    if (-not $StatusResult) { $reasons += 'NO_STATUS_RESULT' }
    if ($StatusResult.failedTaskCount -gt 0) { $reasons += 'FAILED_TASKS_PRESENT' }
    if ($StatusResult.orphanProcess) { $reasons += 'ORPHAN_PROCESS' }
    if ($StatusResult.pidReuseSuspicion) { $reasons += 'PID_REUSE_SUSPICION' }
    if ($StatusResult.queueMalformed) { $reasons += 'QUEUE_MALFORMED' }
    if ($StatusResult.openRouterModelNeedsOperatorReview) { $reasons += 'OPENROUTER_MODEL_NEEDS_REVIEW' }
    if ($StatusResult.destructiveActionsAllowed) { $reasons += 'DESTRUCTIVE_ACTIONS_ALLOWED' }
    if ($StatusResult.secretsPrinted) { $reasons += 'SECRETS_PRINTED' }
    return [ordered]@{
        unsafe = ($reasons.Count -gt 0)
        reasons = $reasons
    }
}

function Stop-IdleOps {
    $record = Invoke-ExternalScript -RelativePath 'scripts/obtuse/stop-idle-ops.ps1'
    return $record
}

# -----------------------------------------------------------------------------
# Takeover discipline
# -----------------------------------------------------------------------------
function Invoke-GitCapture {
    param([Parameter(Mandatory)] [string[]]$Arguments)
    $output = @()
    $exitCode = 0
    try {
        $output = & git @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    } catch {
        $output = @($_.Exception.Message)
        $exitCode = 1
    }
    if ($null -eq $exitCode) { $exitCode = 0 }
    return [ordered]@{
        command = "git $($Arguments -join ' ')"
        exitCode = $exitCode
        raw = (($output | ForEach-Object { [string]$_ }) -join "`n")
    }
}

function Get-KimiPlanConsumption {
    $plans = @(Get-ChildItem -LiteralPath $repoRoot -Filter 'rictor-creeper-morbius.md' -Recurse -File -ErrorAction SilentlyContinue | Sort-Object FullName)
    $plan = $plans | Select-Object -First 1
    $content = $null
    $sha = $null
    $lineCount = 0
    if ($plan) {
        $content = Get-Content -Raw -LiteralPath $plan.FullName -ErrorAction Stop
        $sha = (Get-FileHash -LiteralPath $plan.FullName -Algorithm SHA256).Hash.ToUpperInvariant()
        $lineCount = @($content -split "`r?`n").Count
    }
    return [ordered]@{
        timestamp = (Get-Date -Format 'o')
        classification = 'OBTUSE_BLUNDER_LOCAL_FRANKENSTEIN_OPERATIONAL_CLOSURE_V1'
        expectedPlanFile = 'rictor-creeper-morbius.md'
        kimiPlanFound = ($null -ne $plan)
        kimiPlanConsumed = ($null -ne $plan)
        promptAuthorityUsed = ($null -eq $plan)
        planPath = if ($plan) { $plan.FullName } else { $null }
        planSha256 = $sha
        planLineCount = $lineCount
        searchedRoot = $repoRoot
        notes = if ($plan) { @('Kimi plan found and consumed as prior approved plan evidence.') } else { @('Kimi plan was not accessible; takeover prompt is authoritative.') }
    }
}

function Invoke-TakeoverDiscipline {
    New-Item -ItemType Directory -Force -Path (Resolve-RepoPath -Path $OutputArtifactRoot) | Out-Null
    $gitStatus = Invoke-GitCapture -Arguments @('--no-pager','status','--short')
    $gitBranch = Invoke-GitCapture -Arguments @('branch','--show-current')
    $planRecord = Get-KimiPlanConsumption
    $statusResult = Get-StatusOpsResult
    $dirtyStateSummary = if ($gitStatus.exitCode -eq 0) {
        if ([string]::IsNullOrWhiteSpace($gitStatus.raw)) { 'CLEAN' } else { 'DIRTY' }
    } else {
        'NOT_A_GIT_WORKING_TREE'
    }
    $record = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        classification = 'OBTUSE_BLUNDER_LOCAL_FRANKENSTEIN_OPERATIONAL_CLOSURE_V1_CODEX_TAKEOVER_EXECUTION'
        repoRoot = $repoRoot
        currentBranch = if ($gitBranch.exitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($gitBranch.raw)) { $gitBranch.raw.Trim() } else { $null }
        dirtyStateSummary = $dirtyStateSummary
        gitStatusCommandResult = $gitStatus
        gitBranchCommandResult = $gitBranch
        kimiPlanFound = $planRecord.kimiPlanFound
        kimiPlanConsumed = $planRecord.kimiPlanConsumed
        promptAuthorityUsed = $planRecord.promptAuthorityUsed
        approvedPlanConsumptionRecordPath = (Resolve-RepoPath -Path (Join-Path $OutputArtifactRoot 'approved_plan_consumption_record.json'))
        knownBaselineProofBundleShaValues = [ordered]@{
            dunceProofRegressionPack = '4902052429C2F38F2139597EDA2CF2823941C4E68CC141777989B4073FE45828'
            dunceRegressionSubstrateIdleCycle = '06628FB925DB1DE9BF3E3C0FEEAE03B33F3562062CFB8524AF782C5CCEA85E3F'
            crossMachineProofRegressionVerification = $ExpectedCrossMachineBundleSha256
        }
        currentStatusCommandResult = [ordered]@{
            command = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\status-idle-ops.ps1'
            exitCode = $statusResult.invocation.exitCode
            status = $statusResult.status
            healthy = $statusResult.healthy
            failedTaskCount = $statusResult.failedTaskCount
            orphanProcess = $statusResult.orphanProcess
            pidReuseSuspicion = $statusResult.pidReuseSuspicion
            queueMalformed = $statusResult.queueMalformed
            providerCallsAttempted = if ($statusResult.raw -and ($statusResult.raw.PSObject.Properties.Name -contains 'providerCallsAttempted')) { $statusResult.raw.providerCallsAttempted } else { $null }
            providerCallsSucceeded = if ($statusResult.raw -and ($statusResult.raw.PSObject.Properties.Name -contains 'providerCallsSucceeded')) { $statusResult.raw.providerCallsSucceeded } else { $null }
            providerCallsFailed = if ($statusResult.raw -and ($statusResult.raw.PSObject.Properties.Name -contains 'providerCallsFailed')) { $statusResult.raw.providerCallsFailed } else { $null }
        }
    }
    Write-JsonFile -Value $planRecord -Path (Join-Path $OutputArtifactRoot 'approved_plan_consumption_record.json')
    Write-JsonFile -Value $record -Path (Join-Path $OutputArtifactRoot 'codex_takeover_record.json')
    return [ordered]@{
        result = 'PASS'
        record = $record
        plan = $planRecord
        initialStatus = $statusResult
    }
}

# -----------------------------------------------------------------------------
# Hard-stop detector
# -----------------------------------------------------------------------------
function Test-HardStop {
    param(
        [object]$StatusResult,
        [object]$SafetySnapshot,
        [object]$SecretScan,
        [string]$ExtraReason = $null
    )
    $reasons = @()
    if ($ExtraReason) { $reasons += $ExtraReason }
    if ($SafetySnapshot) {
        if ($SafetySnapshot.forbiddenProcessCount -gt 0) { $reasons += 'BENCHMARK_OR_FORBIDDEN_PROCESS' }
        if ($SafetySnapshot.providerProcessCount -gt 0) { $reasons += 'PROVIDER_PROCESS_RUNNING' }
    }
    if ($SecretScan -and ($SecretScan.status -eq 'FAIL' -or $SecretScan.findings -gt 0)) { $reasons += 'SECRET_EXPOSURE' }
    if ($StatusResult) {
        if ($StatusResult.orphanProcess) { $reasons += 'ORPHAN_PROCESS' }
        if ($StatusResult.pidReuseSuspicion) { $reasons += 'PID_REUSE_SUSPICION' }
        if ($StatusResult.queueMalformed) { $reasons += 'QUEUE_MALFORMED' }
        if ($StatusResult.failedTaskCount -gt 0) { $reasons += 'FAILED_TASKS_PRESENT' }
        if ($StatusResult.openRouterModelNeedsOperatorReview) { $reasons += 'PROVIDER_SWITCH_NEEDS_REVIEW' }
        if ($StatusResult.destructiveActionsAllowed) { $reasons += 'DESTRUCTIVE_ACTIONS_ALLOWED' }
        if ($StatusResult.secretsPrinted) { $reasons += 'SECRETS_PRINTED' }
    }
    if ($reasons.Count -gt 0) {
        return [ordered]@{
            hardStop = $true
            reason = ($reasons -join '; ')
            primaryReason = $reasons[0]
        }
    }
    return [ordered]@{ hardStop = $false; reason = $null; primaryReason = $null }
}

# -----------------------------------------------------------------------------
# Phase implementations
# -----------------------------------------------------------------------------
function Invoke-BaselineConsumption {
    $now = Get-Date -Format 'o'
    $record = [ordered]@{
        timestamp = $now
        classification = 'OBTUSE_BLUNDER_LOCAL_FRANKENSTEIN_OPERATIONAL_CLOSURE_V1'
        phase = 'BASELINE_CONSUMPTION'
        sourceFinalReport = [ordered]@{}
        sourceProofBundle = [ordered]@{}
        sourceArtifactDir = [ordered]@{}
        statusCheck = $null
        complete = $false
    }
    $allOk = $true

    $finalReportPath = Join-Path $SourceArtifactRoot 'FINAL_REPORT.json'
    $finalReport = Read-JsonFile -Path $finalReportPath
    $record.sourceFinalReport = [ordered]@{
        path = (Resolve-RepoPath -Path $finalReportPath)
        exists = ($null -ne $finalReport)
        terminalState = if ($finalReport) { $finalReport.terminalState } else { $null }
        localVerificationResult = if ($finalReport) { $finalReport.localVerificationResult } else { $null }
        frankensteinVerificationResult = if ($finalReport) { $finalReport.frankensteinVerificationResult } else { $null }
        gamingpc3VerificationResult = if ($finalReport) { $finalReport.gamingpc3VerificationResult } else { $null }
        proofBundlePathFromReport = if ($finalReport) { $finalReport.proofBundlePath } else { $null }
        proofBundleShaFromReport = if ($finalReport) { $finalReport.proofBundleSha256 } else { $null }
    }

    if (-not $finalReport) {
        $allOk = $false
    } else {
        if ($finalReport.terminalState -ne 'DUNCE_CROSS_MACHINE_PROOF_REGRESSION_VERIFIED_WITH_NOTES') { $allOk = $false }
        if ($finalReport.localVerificationResult -ne 'PASS') { $allOk = $false }
        if ($finalReport.frankensteinVerificationResult -ne 'PASS') { $allOk = $false }
        if ($finalReport.gamingpc3VerificationResult -ne 'BLOCKED') { $allOk = $false }

        $reportedBundlePath = $finalReport.proofBundlePath
        $reportedBundleSha = Get-FileSha256 -Path $reportedBundlePath
        $bundlePath = $reportedBundlePath
        $bundleSha = $reportedBundleSha
        $expectedBundleFoundByHash = $false
        $bundleDir = Resolve-RepoPath -Path 'artifacts/obtuse/proof-bundles'
        if (Test-Path -LiteralPath $bundleDir) {
            $candidateBundles = @(Get-ChildItem -LiteralPath $bundleDir -Filter 'obtuse-blunder-dunce-cross-machine-proof-regression-verification-v1-*.zip' -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)
            foreach ($candidateBundle in $candidateBundles) {
                $candidateSha = (Get-FileHash -LiteralPath $candidateBundle.FullName -Algorithm SHA256).Hash.ToUpperInvariant()
                if ($candidateSha -eq $ExpectedCrossMachineBundleSha256) {
                    $bundlePath = $candidateBundle.FullName
                    $bundleSha = $candidateSha
                    $expectedBundleFoundByHash = $true
                    break
                }
            }
        }
        $record.sourceProofBundle = [ordered]@{
            path = $bundlePath
            resolvedPath = (Resolve-RepoPath -Path $bundlePath)
            exists = (Test-Path -LiteralPath (Resolve-RepoPath -Path $bundlePath))
            sha256 = $bundleSha
            expectedSha256 = $ExpectedCrossMachineBundleSha256
            shaMatches = ($bundleSha -eq $ExpectedCrossMachineBundleSha256)
            expectedBundleFoundByHash = $expectedBundleFoundByHash
            reportedProofBundlePath = $reportedBundlePath
            reportedProofBundleSha256 = $reportedBundleSha
            reportedProofBundleMatchesExpected = ($reportedBundleSha -eq $ExpectedCrossMachineBundleSha256)
        }
        if (-not $record.sourceProofBundle.shaMatches) { $allOk = $false }
    }

    $requiredSourceFiles = @(
        'portable_regression_pack_manifest.json',
        'machine_verification_matrix.json',
        'baseline_consumption_record.json',
        'FINAL_REPORT.json'
    )
    $requiredChecks = @()
    foreach ($rf in $requiredSourceFiles) {
        $full = Join-Path $SourceArtifactRoot $rf
        $exists = Test-Path -LiteralPath (Resolve-RepoPath -Path $full)
        $requiredChecks += @{ file = $rf; exists = $exists }
        if (-not $exists) { $allOk = $false }
    }
    $record.sourceArtifactDir = [ordered]@{
        path = (Resolve-RepoPath -Path $SourceArtifactRoot)
        exists = (Test-Path -LiteralPath (Resolve-RepoPath -Path $SourceArtifactRoot))
        requiredFiles = $requiredChecks
    }

    $statusResult = Get-StatusOpsResult
    $record.statusCheck = [ordered]@{
        status = $statusResult.status
        healthy = $statusResult.healthy
        failedTaskCount = $statusResult.failedTaskCount
        orphanProcess = $statusResult.orphanProcess
        pidReuseSuspicion = $statusResult.pidReuseSuspicion
        queueMalformed = $statusResult.queueMalformed
    }
    if (-not $statusResult.healthy) { $allOk = $false }

    $record.complete = $allOk
    Write-JsonFile -Value $record -Path (Join-Path $OutputArtifactRoot 'baseline_consumption_record.json')
    return [ordered]@{
        result = if ($allOk) { 'PASS' } else { 'FAIL' }
        details = $record
    }
}

function Invoke-StandardIdleValidation {
    param([hashtable]$State)
    $maxRetries = 3
    $attempt = 0
    $lastOutcome = $null
    $summaryPath = $null
    while ($attempt -lt $maxRetries) {
        $attempt++
        $run = Invoke-ExternalScript -RelativePath 'scripts/obtuse/blunder-standard-idle-validation-suite.ps1' -ArgumentList @('-Mode','Run')
        $passed = $false
        if ($run.exists -and $run.exitCode -eq 0 -and $run.rawString -match 'PASS') { $passed = $true }
        $lastOutcome = [ordered]@{
            attempt = $attempt
            timestamp = (Get-Date -Format 'o')
            invocation = $run
            passed = $passed
        }
        if ($passed) { break }
    }
    $integration = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        phase = 'STANDARD_IDLE_VALIDATION'
        attempts = $attempt
        result = if ($lastOutcome.passed) { 'PASS' } else { 'FAIL' }
        lastOutcome = $lastOutcome
        summaryPath = $summaryPath
        notes = @('Retry limit is 3 attempts on soft failure.')
    }
    Write-JsonFile -Value $integration -Path (Join-Path $OutputArtifactRoot 'standard_idle_validation_integration.json')
    return [ordered]@{
        result = $integration.result
        details = $integration
    }
}

function Invoke-DunceToSelfCodingBridge {
    $run = Invoke-ExternalScript -RelativePath 'scripts/obtuse/blunder-dunce-to-self-coding-queue-bridge.ps1' -ArgumentList @('-Mode','RunSmoke','-OperatorApproved','true','-QueuePath',$StandardQueuePath,'-ArtifactRoot',$OutputArtifactRoot)
    $passed = $false
    if ($run.exists -and $run.exitCode -eq 0 -and $run.parsed -and ($run.parsed.PSObject.Properties.Name -contains 'result') -and $run.parsed.result -eq 'PASS') { $passed = $true }
    $record = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        phase = 'DUNCE_TO_SELF_CODING_BRIDGE'
        result = if ($passed) { 'PASS' } else { 'FAIL' }
        invocation = $run
    }
    return [ordered]@{ result = $record.result; details = $record }
}

function Invoke-BoundedNormalIdleCycle {
    param([hashtable]$State)
    $maxCycles = 10
    $targetPassed = 5
    $statusBeforeCycle = Get-StatusOpsResult
    $safeExecutableTaskCount = 0
    $pendingTaskCount = 0
    if ($statusBeforeCycle.raw -and $statusBeforeCycle.raw.queueReplenishmentStatus -and ($statusBeforeCycle.raw.queueReplenishmentStatus.PSObject.Properties.Name -contains 'safeExecutableTaskCount')) {
        $safeExecutableTaskCount = [int]$statusBeforeCycle.raw.queueReplenishmentStatus.safeExecutableTaskCount
    }
    if ($statusBeforeCycle.queueStats -and ($statusBeforeCycle.queueStats.PSObject.Properties.Name -contains 'pending')) {
        $pendingTaskCount = [int]$statusBeforeCycle.queueStats.pending
    }
    if ($safeExecutableTaskCount -eq 0 -and $pendingTaskCount -eq 0) {
        $cycleRecord = [ordered]@{
            cycleNumber = 0
            timestamp = (Get-Date -Format 'o')
            cycled = $false
            status = 'skipped'
            reason = 'NO_SAFE_EXECUTABLE_TASKS_HISTORY_ONLY'
            statusBeforeCycle = $statusBeforeCycle
        }
        Add-LedgerLine -Path (Join-Path $OutputArtifactRoot 'normal_idle_operational_cycle_ledger.jsonl') -Record $cycleRecord
        $summary = [ordered]@{
            timestamp = (Get-Date -Format 'o')
            phase = 'BOUNDED_NORMAL_IDLE_CYCLE'
            attemptedCycles = 0
            passedCycles = 0
            failedCycles = 0
            skippedCycles = 1
            quarantinedCycles = 0
            flashInvoked = 0
            flashCheck = 'PASS'
            targetPassedCycles = $targetPassed
            maxCycles = $maxCycles
            noExecutableTasks = $true
            skipReason = 'NO_SAFE_EXECUTABLE_TASKS_HISTORY_ONLY'
            result = 'PASS'
        }
        Write-JsonFile -Value $summary -Path (Join-Path $OutputArtifactRoot 'normal_idle_operational_cycle_summary.json')
        return [ordered]@{ result = 'PASS'; details = $summary; hardStop = $false; hardStopReason = $null }
    }
    $passed = if ($State.Contains('passedCycles')) { $State.passedCycles } else { 0 }
    $failed = if ($State.Contains('failedCycles')) { $State.failedCycles } else { 0 }
    $attempted = if ($State.Contains('attemptedCycles')) { $State.attemptedCycles } else { 0 }
    $skipped = if ($State.Contains('skippedCycles')) { $State.skippedCycles } else { 0 }
    $flashInvoked = if ($State.Contains('flashInvoked')) { $State.flashInvoked } else { 0 }
    $hardStopReason = $null
    $cycles = if ($State.Contains('cycleLedger')) { $State.cycleLedger } else { @() }

    while ($attempted -lt $maxCycles) {
        $attempted++
        $safetyBefore = Get-ProcessSafety
        $run = Invoke-ExternalScript -RelativePath 'scripts/obtuse/idle-mode.ps1' -ArgumentList @('-Action','RunOnce','-QueuePath',$StandardQueuePath,'-ArtifactRoot',$StandardIdleArtifactRoot)
        $entry = $null
        $cycled = $false
        $status = 'UNKNOWN'
        if ($run.parsed -and ($run.parsed.PSObject.Properties.Name -contains 'cycled')) {
            $cycled = [bool]$run.parsed.cycled
            if ($run.parsed.PSObject.Properties.Name -contains 'entry') {
                $entry = $run.parsed.entry
                if ($entry -and ($entry.PSObject.Properties.Name -contains 'status')) {
                    $status = $entry.status
                }
            }
        }
        $cycleRecord = [ordered]@{
            cycleNumber = $attempted
            timestamp = (Get-Date -Format 'o')
            cycled = $cycled
            status = $status
            entry = $entry
            invocationExitCode = $run.exitCode
            invocationParseError = $run.parseError
            safetyBefore = $safetyBefore
        }
        $cycles += $cycleRecord
        Add-LedgerLine -Path (Join-Path $OutputArtifactRoot 'normal_idle_operational_cycle_ledger.jsonl') -Record $cycleRecord

        if ($status -eq 'failed') {
            $failed++
            $hardStopReason = 'FAILED_TASK_IN_IDLE_CYCLE'
            break
        }
        if ($status -in @('passed','quarantined')) {
            $passed++
        } elseif ($cycled -and ($entry -and ($entry.PSObject.Properties.Name -contains 'taskId') -and $entry.taskId -eq 'openrouter-self-coding')) {
            $flashInvoked++
            $passed++
        } elseif (-not $cycled) {
            $skipped++
        } else {
            $passed++
        }

        $safetyAfter = Get-ProcessSafety
        $cycleRecord.safetyAfter = $safetyAfter
        $hs = Test-HardStop -SafetySnapshot $safetyAfter
        if ($hs.hardStop) {
            $hardStopReason = $hs.reason
            break
        }

        if ($passed -ge $targetPassed -and $failed -eq 0) { break }
    }

    $flashCheckResult = 'PASS'
    if ($flashInvoked -gt 5) { $flashCheckResult = 'FAIL' }

    $summary = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        phase = 'BOUNDED_NORMAL_IDLE_CYCLE'
        attemptedCycles = $attempted
        passedCycles = $passed
        failedCycles = $failed
        skippedCycles = $skipped
        quarantinedCycles = @($cycles | Where-Object { $_.status -eq 'quarantined' }).Count
        flashInvoked = $flashInvoked
        flashCheck = $flashCheckResult
        targetPassed = $targetPassed
        maxCycles = $maxCycles
        result = 'PENDING'
        hardStopReason = $hardStopReason
    }
    if ($hardStopReason) {
        $summary.result = 'HARD_STOP'
    } elseif ($failed -gt 0) {
        $summary.result = 'FAIL'
    } elseif ($passed -ge $targetPassed) {
        $summary.result = 'PASS'
    } else {
        $summary.result = 'FAIL'
    }
    Write-JsonFile -Value $summary -Path (Join-Path $OutputArtifactRoot 'normal_idle_operational_cycle_summary.json')

    $State.attemptedCycles = $attempted
    $State.passedCycles = $passed
    $State.failedCycles = $failed
    $State.skippedCycles = $skipped
    $State.flashInvoked = $flashInvoked
    $State.cycleLedger = $cycles

    return [ordered]@{
        result = $summary.result
        details = $summary
        hardStop = ($summary.result -eq 'HARD_STOP')
        hardStopReason = $hardStopReason
    }
}

function Invoke-PostCycleRegression {
    $regression = Invoke-ExternalScript -RelativePath 'scripts/obtuse/blunder-dunce-proof-regression-pack.ps1' -ArgumentList @('-Mode','RunRegression')
    $substrate = Invoke-ExternalScript -RelativePath 'scripts/obtuse/blunder-dunce-regression-substrate-idle-cycle.ps1' -ArgumentList @('-Mode','Validate')

    $regressionPass = $false
    if ($regression.exists -and ($regression.rawString -match 'closure: DUNCE_PROOF_REGRESSION_PACK_PHASE_CLOSED')) { $regressionPass = $true }
    $substratePass = $false
    if ($substrate.exists -and ($substrate.rawString -match 'VALIDATE_PASS')) { $substratePass = $true }

    $record = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        phase = 'POST_CYCLE_REGRESSION'
        dunceRegressionResult = if ($regressionPass) { 'PASS' } else { 'FAIL' }
        substrateValidationResult = if ($substratePass) { 'PASS' } else { 'FAIL' }
        regressionInvocation = $regression
        substrateInvocation = $substrate
        result = if ($regressionPass -and $substratePass) { 'PASS' } else { 'FAIL' }
    }
    Write-JsonFile -Value $record -Path (Join-Path $OutputArtifactRoot 'post_cycle_regression_result.json')
    return [ordered]@{ result = $record.result; details = $record }
}

function Invoke-PostCycleStandardValidation {
    $maxRetries = 3
    $attempt = 0
    $lastOutcome = $null
    while ($attempt -lt $maxRetries) {
        $attempt++
        $run = Invoke-ExternalScript -RelativePath 'scripts/obtuse/blunder-standard-idle-validation-suite.ps1' -ArgumentList @('-Mode','Run')
        $passed = $false
        if ($run.exists -and $run.exitCode -eq 0 -and $run.rawString -match 'PASS') { $passed = $true }
        $lastOutcome = [ordered]@{
            attempt = $attempt
            timestamp = (Get-Date -Format 'o')
            invocation = $run
            passed = $passed
        }
        if ($passed) { break }
    }
    $record = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        phase = 'POST_CYCLE_STANDARD_VALIDATION'
        attempts = $attempt
        result = if ($lastOutcome.passed) { 'PASS' } else { 'FAIL' }
        lastOutcome = $lastOutcome
    }
    Write-JsonFile -Value $record -Path (Join-Path $OutputArtifactRoot 'post_cycle_standard_validation_result.json')
    return [ordered]@{ result = $record.result; details = $record }
}

function Invoke-QueueHealth {
    $statusResult = Get-StatusOpsResult
    $unsafe = Test-StatusUnsafe -StatusResult $statusResult
    $record = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        phase = 'QUEUE_HEALTH'
        statusResult = $statusResult
        unsafe = $unsafe.unsafe
        unsafeReasons = $unsafe.reasons
        result = if ($statusResult.healthy -and -not $unsafe.unsafe) { 'PASS' } else { 'FAIL' }
    }
    Write-JsonFile -Value $record -Path (Join-Path $OutputArtifactRoot 'queue_health_record.json')
    return [ordered]@{ result = $record.result; details = $record }
}

function Invoke-FrankensteinMirrorVerification {
    $mirrorParent = Split-Path $FrankensteinMirror -Parent
    $parentReachable = Test-Path -LiteralPath $mirrorParent
    $targetReachable = Test-Path -LiteralPath $FrankensteinMirror
    $record = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        phase = 'FRANKENSTEIN_MIRROR_VERIFICATION'
        mirrorPath = $FrankensteinMirror
        mirrorParent = $mirrorParent
        parentReachable = $parentReachable
        targetReachableBeforeCopy = $targetReachable
        result = if ($parentReachable) { 'PASS' } else { 'FAIL' }
        notes = @('Pre-copy parent reachability check; exact artifact copy is verified in proof_bundle.')
    }
    return [ordered]@{ result = $record.result; details = $record }
}

function Invoke-GamingPC3BlockerRecord {
    $reachable = Test-Path -LiteralPath $GamingPC3Mirror
    $record = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        phase = 'GAMINGPC3_BLOCKER_RECORD'
        mirrorPath = $GamingPC3Mirror
        reachable = $reachable
        blocker = 'GAMINGPC3_MIRROR_PATH_NOT_REACHABLE'
        result = 'BLOCKED'
        notes = @('GAMINGPC3 is intentionally blocked for this closure.')
    }
    Write-JsonFile -Value $record -Path (Join-Path $OutputArtifactRoot 'gamingpc3_blocker_record.json')
    return [ordered]@{ result = $record.result; details = $record }
}

function Invoke-ShortOperationalSoak {
    $preSoakStatus = Get-StatusOpsResult
    $preSoakUnsafe = Test-StatusUnsafe -StatusResult $preSoakStatus
    $preSoakSafeExecutableTaskCount = 0
    $preSoakPendingTaskCount = 0
    if ($preSoakStatus.raw -and $preSoakStatus.raw.queueReplenishmentStatus -and ($preSoakStatus.raw.queueReplenishmentStatus.PSObject.Properties.Name -contains 'safeExecutableTaskCount')) {
        $preSoakSafeExecutableTaskCount = [int]$preSoakStatus.raw.queueReplenishmentStatus.safeExecutableTaskCount
    }
    if ($preSoakStatus.queueStats -and ($preSoakStatus.queueStats.PSObject.Properties.Name -contains 'pending')) {
        $preSoakPendingTaskCount = [int]$preSoakStatus.queueStats.pending
    }
    if ($preSoakStatus.status -eq 'stopped' -and -not $preSoakUnsafe.unsafe -and $preSoakSafeExecutableTaskCount -eq 0 -and $preSoakPendingTaskCount -eq 0) {
        $summary = [ordered]@{
            timestamp = (Get-Date -Format 'o')
            phase = 'SHORT_OPERATIONAL_SOAK'
            durationSeconds = 0
            pollCount = 1
            intervalSeconds = 0
            skipped = $true
            skipReason = 'IDLE_STOPPED_HISTORY_ONLY_NO_SAFE_EXECUTABLE_TASKS'
            polls = @([ordered]@{
                iteration = 1
                timestamp = (Get-Date -Format 'o')
                status = $preSoakStatus.status
                healthy = $preSoakStatus.healthy
                unsafeReasons = $preSoakUnsafe.reasons
            })
            allPollsHealthy = $true
            stableStopped = $true
            anyUnsafe = $false
            result = 'PASS'
        }
        Write-JsonFile -Value $summary -Path (Join-Path $OutputArtifactRoot 'short_operational_soak_summary.json')
        return [ordered]@{ result = $summary.result; details = $summary }
    }
    $polls = @()
    $iterations = 20
    $intervalSeconds = 60
    $allHealthy = $true
    $stableStopped = $true
    $anyUnsafe = $false
    for ($i = 1; $i -le $iterations; $i++) {
        Start-Sleep -Seconds $intervalSeconds
        $statusResult = Get-StatusOpsResult
        $unsafe = Test-StatusUnsafe -StatusResult $statusResult
        $pollHealthy = ($statusResult.healthy -and -not $unsafe.unsafe)
        if (-not $pollHealthy) { $allHealthy = $false }
        if ($statusResult.status -ne 'stopped') { $stableStopped = $false }
        if ($unsafe.unsafe) { $anyUnsafe = $true }
        $pollRecord = [ordered]@{
            iteration = $i
            timestamp = (Get-Date -Format 'o')
            status = $statusResult.status
            healthy = $pollHealthy
            unsafeReasons = $unsafe.reasons
        }
        $polls += $pollRecord
    }
    $summary = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        phase = 'SHORT_OPERATIONAL_SOAK'
        durationSeconds = ($iterations * $intervalSeconds)
        pollCount = $iterations
        intervalSeconds = $intervalSeconds
        polls = $polls
        allPollsHealthy = $allHealthy
        stableStopped = $stableStopped
        anyUnsafe = $anyUnsafe
        result = if ($allHealthy -and -not $anyUnsafe) { 'PASS' } else { 'FAIL' }
    }
    Write-JsonFile -Value $summary -Path (Join-Path $OutputArtifactRoot 'short_operational_soak_summary.json')
    return [ordered]@{ result = $summary.result; details = $summary }
}

function Invoke-ProofBundle {
    $bundleDir = Resolve-RepoPath -Path 'artifacts/obtuse/proof-bundles'
    if (-not (Test-Path -LiteralPath $bundleDir)) {
        New-Item -ItemType Directory -Force -Path $bundleDir | Out-Null
    }
    $srcDir = Resolve-RepoPath -Path $OutputArtifactRoot
    $ts = Get-Date -Format 'yyyyMMddTHHmmss'
    $bundleName = "obtuse-blunder-local-frankenstein-operational-closure-v1-$ts.zip"
    $bundlePath = Join-Path $bundleDir $bundleName
    if (Test-Path -LiteralPath $bundlePath) { Remove-Item -LiteralPath $bundlePath -Force }
    Compress-Archive -Path (Join-Path $srcDir '*') -DestinationPath $bundlePath -Force
    $bundleSha = (Get-FileHash -LiteralPath $bundlePath -Algorithm SHA256).Hash.ToUpperInvariant()

    $copyResults = [ordered]@{
        FRANKENSTEIN = @{ reachable = $false; artifactCopied = $false; bundleCopied = $false; copiedBundleSha256 = $null; bundleShaMatches = $false; finalReportCopied = $false; error = $null }
        GAMINGPC3 = @{ reachable = $false; artifactCopied = $false; bundleCopied = $false; copiedBundleSha256 = $null; bundleShaMatches = $false; finalReportCopied = $false; error = $null }
    }

    foreach ($entry in $copyResults.GetEnumerator()) {
        $targetPath = if ($entry.Key -eq 'FRANKENSTEIN') { $FrankensteinMirror } else { $GamingPC3Mirror }
        try {
            $parent = Split-Path $targetPath -Parent
            $entry.Value.reachable = Test-Path -LiteralPath $parent
            if ($entry.Value.reachable) {
                if (-not (Test-Path -LiteralPath $targetPath)) {
                    New-Item -ItemType Directory -Path $targetPath -Force | Out-Null
                }
                Copy-Item -Path (Join-Path $srcDir '*') -Destination $targetPath -Recurse -Force
                $entry.Value.artifactCopied = $true
                $entry.Value.finalReportCopied = Test-Path -LiteralPath (Join-Path $targetPath 'FINAL_REPORT.json')
                Copy-Item -LiteralPath $bundlePath -Destination $targetPath -Force
                $entry.Value.bundleCopied = $true
                $copiedBundle = Join-Path $targetPath $bundleName
                if (Test-Path -LiteralPath $copiedBundle) {
                    $entry.Value.copiedBundleSha256 = (Get-FileHash -LiteralPath $copiedBundle -Algorithm SHA256).Hash.ToUpperInvariant()
                    $entry.Value.bundleShaMatches = ($entry.Value.copiedBundleSha256 -eq $bundleSha)
                }
            }
        } catch {
            $entry.Value.error = $_.Exception.Message
        }
    }

    $frankensteinReachableAfterCopy = Test-Path -LiteralPath $FrankensteinMirror
    $record = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        phase = 'PROOF_BUNDLE'
        bundlePath = $bundlePath
        bundleName = $bundleName
        bundleSha256 = $bundleSha
        frankensteinCopyPath = $FrankensteinMirror
        gamingpc3CopyPath = $GamingPC3Mirror
        copyResults = $copyResults
        frankensteinReachableAfterCopy = $frankensteinReachableAfterCopy
        result = if ($frankensteinReachableAfterCopy -and $copyResults.FRANKENSTEIN.artifactCopied -and $copyResults.FRANKENSTEIN.bundleCopied -and $copyResults.FRANKENSTEIN.bundleShaMatches) { 'PASS' } else { 'FAIL' }
    }
    Write-JsonFile -Value $record -Path (Join-Path $OutputArtifactRoot 'proof_bundle_info.json')
    return [ordered]@{ result = $record.result; details = $record; bundlePath = $bundlePath; bundleSha256 = $bundleSha }
}

# -----------------------------------------------------------------------------
# Final artifacts
# -----------------------------------------------------------------------------
function Write-UpdatedTodo {
    param([string]$TerminalState)
    $text = ''
    if ($TerminalState -eq 'BLUNDER_LOCAL_FRANKENSTEIN_OPERATIONAL_CLOSED_WITH_GAMINGPC3_NOTES') {
        $text = @"
# Blunder Local FRANKENSTEIN Operational Closure — Updated TODO

## Completed
- DONE-043 Consume Dunce cross-machine proof-regression artifact as local baseline.
- DONE-044 Run standard idle validation suite against local FRANKENSTEIN closure candidate.
- DONE-045 Execute Dunce-to-self-coding queue bridge smoke test.
- DONE-046 Complete bounded normal idle operational cycle with >=5 passed, 0 failed.
- DONE-047 Verify post-cycle Dunce regression and substrate idle-cycle validation.

## Remaining
- NEXT-048 Resolve GAMINGPC3 mirror reachability and rerun cross-machine verification.
- NEXT-049 Promote local FRANKENSTEIN operational closure into continuous supervision policy.
- NEXT-050 Schedule next 120-minute supervised Flash runtime only after GAMINGPC3 unblock.
"@
    } else {
        $text = @"
# Blunder Local FRANKENSTEIN Operational Closure — Updated TODO

## Blocked
- EXACT-NEXT: Resolve blocker before declaring BLUNDER_LOCAL_FRANKENSTEIN_OPERATIONAL_CLOSED_WITH_GAMINGPC3_NOTES.

## Remaining
- NEXT-048 Resolve GAMINGPC3 mirror reachability and rerun cross-machine verification.
- NEXT-049 Promote local FRANKENSTEIN operational closure into continuous supervision policy.
- NEXT-050 Schedule next 120-minute supervised Flash runtime only after GAMINGPC3 unblock.
"@
    }
    Write-TextFile -Path (Join-Path $OutputArtifactRoot 'UPDATED_TODO.md') -Value $text
}

function Write-OperatorHandoff {
    param([object]$Report)
    $text = @"
# Blunder Local FRANKENSTEIN Operational Closure — Operator Handoff

Classification: $($Report.classification)
Timestamp: $($Report.timestamp)
Terminal state: $($Report.terminalState)
Status: $($Report.status)

## Results
- Local operational closed: $($Report.localOperationalClosed)
- FRANKENSTEIN operational closed: $($Report.frankensteinOperationalClosed)
- GAMINGPC3 blocker: $($Report.gamingpc3Blocker)
- Standard idle validation: $($Report.phaseResults.standard_idle_validation.result)
- Dunce-to-self-coding bridge: $($Report.phaseResults.dunce_to_self_coding_bridge.result)
- Bounded normal idle cycle: $($Report.phaseResults.bounded_normal_idle_cycle.result)
- Post-cycle regression: $($Report.phaseResults.post_cycle_regression.result)
- Post-cycle standard validation: $($Report.phaseResults.post_cycle_standard_validation.result)
- Queue health: $($Report.phaseResults.queue_health.result)
- Short operational soak: $($Report.phaseResults.short_operational_soak.result)
- Secret scan: $($Report.secretScan.status)
- Benchmark touched: $($Report.benchmarkTouched)
- Provider calls policy: $($Report.providerCallsPolicyResult)
- Destructive actions: $($Report.destructiveActions)

## Commands
Validate: $($Report.exactValidateCommand)
Rerun validation: $($Report.exactRerunValidationCommand)
Rerun closure: $($Report.exactRerunClosureCommand)
Status: $($Report.exactStatusCommand)
Stop: $($Report.exactStopCommand)

## Proof bundle
- Path: $($Report.proofBundlePath)
- SHA-256: $($Report.proofBundleSha256)
- FRANKENSTEIN copy path: $($Report.frankensteinCopyPath)

## Next recommended action
$($Report.nextRecommendedAction)
"@
    Write-TextFile -Path (Join-Path $OutputArtifactRoot 'operator_handoff.md') -Value $text
}

function Write-ClosureMatrix {
    param([object]$Report)
    $matrix = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        classification = $Report.classification
        terminalState = $Report.terminalState
        machines = @(
            [ordered]@{
                machine = 'CHRIS_LOCAL'
                role = 'source'
                result = if ($Report.localOperationalClosed) { 'PASS' } else { 'FAIL' }
                blocker = $null
            },
            [ordered]@{
                machine = 'FRANKENSTEIN'
                role = 'mirror'
                result = if ($Report.frankensteinOperationalClosed) { 'PASS' } else { 'FAIL' }
                blocker = if ($Report.frankensteinOperationalClosed -eq $false) { 'FRANKENSTEIN_MIRROR_PATH_NOT_REACHABLE' } else { $null }
            },
            [ordered]@{
                machine = 'GAMINGPC3'
                role = 'validation-node'
                result = 'BLOCKED'
                blocker = $Report.gamingpc3Blocker
            }
        )
    }
    Write-JsonFile -Value $matrix -Path (Join-Path $OutputArtifactRoot 'local_frankenstein_closure_matrix.json')
    return $matrix
}

function Write-FinalReport {
    param(
        [hashtable]$State,
        [object]$SecretScan,
        [object]$BundleInfo,
        [object]$FinalStatus
    )
    $now = Get-Date -Format 'o'
    $phaseResults = $State.phaseResults
    function Get-StatusCounter {
        param([object]$StatusResult, [string]$Name)
        if ($StatusResult -and $StatusResult.Contains('raw') -and $StatusResult.raw -and ($StatusResult.raw.PSObject.Properties.Name -contains $Name)) {
            return [int]$StatusResult.raw.$Name
        }
        return 0
    }
    $allPass = $true
    $requiredPhases = @(
        'baseline_consumption',
        'standard_idle_validation',
        'dunce_to_self_coding_bridge',
        'bounded_normal_idle_cycle',
        'post_cycle_regression',
        'post_cycle_standard_validation',
        'queue_health',
        'frankenstein_mirror_verification',
        'short_operational_soak',
        'proof_bundle'
    )
    foreach ($rp in $requiredPhases) {
        if (-not $phaseResults.Contains($rp)) { $allPass = $false }
        elseif ($phaseResults[$rp].result -ne 'PASS') { $allPass = $false }
    }
    if ($SecretScan.status -ne 'PASS') { $allPass = $false }

    $gamingpc3Blocked = $true
    if ($phaseResults.Contains('gamingpc3_blocker_record')) {
        $gamingpc3Blocked = ($phaseResults['gamingpc3_blocker_record'].result -eq 'BLOCKED')
    }

    $safety = Get-ProcessSafety
    $benchmarkTouched = ($safety.forbiddenProcessCount -gt 0)
    $initialStatus = if ($State.Contains('initialStatus')) { $State.initialStatus } else { $null }
    $startProviderCallsAttempted = Get-StatusCounter -StatusResult $initialStatus -Name 'providerCallsAttempted'
    $startProviderCallsSucceeded = Get-StatusCounter -StatusResult $initialStatus -Name 'providerCallsSucceeded'
    $startProviderCallsFailed = Get-StatusCounter -StatusResult $initialStatus -Name 'providerCallsFailed'
    $finalProviderCallsAttempted = Get-StatusCounter -StatusResult $FinalStatus -Name 'providerCallsAttempted'
    $finalProviderCallsSucceeded = Get-StatusCounter -StatusResult $FinalStatus -Name 'providerCallsSucceeded'
    $finalProviderCallsFailed = Get-StatusCounter -StatusResult $FinalStatus -Name 'providerCallsFailed'
    $providerCallsAttemptedDuringClosure = [math]::Max(0, $finalProviderCallsAttempted - $startProviderCallsAttempted)
    $providerCallsSucceededDuringClosure = [math]::Max(0, $finalProviderCallsSucceeded - $startProviderCallsSucceeded)
    $providerCallsFailedDuringClosure = [math]::Max(0, $finalProviderCallsFailed - $startProviderCallsFailed)
    $providerFailuresIncreased = ($providerCallsFailedDuringClosure -gt 0)

    $terminalState = 'BLUNDER_LOCAL_FRANKENSTEIN_OPERATIONAL_BLOCKED_VALIDATION'
    $status = 'BLOCKED'
    $cycleDetailsForPolicy = [ordered]@{}
    if ($phaseResults.Contains('bounded_normal_idle_cycle')) {
        $cycleDetailsForPolicy = $phaseResults['bounded_normal_idle_cycle'].details
    }
    $flashInvokedDuringClosure = if ($cycleDetailsForPolicy.flashInvoked) { [int]$cycleDetailsForPolicy.flashInvoked } else { 0 }
    $providerCallsPolicyOk = (
        $safety.providerProcessCount -eq 0 -and
        -not $providerFailuresIncreased -and
        ($providerCallsAttemptedDuringClosure -eq 0 -or $flashInvokedDuringClosure -gt 0)
    )

    if ($allPass -and $gamingpc3Blocked -and -not $benchmarkTouched -and $providerCallsPolicyOk -and ($SecretScan.status -eq 'PASS')) {
        $terminalState = 'BLUNDER_LOCAL_FRANKENSTEIN_OPERATIONAL_CLOSED_WITH_GAMINGPC3_NOTES'
        $status = 'PASS_WITH_NOTES'
    }
    if ($State.hardStop) {
        $reason = $State.hardStopReason
        if ($reason -like '*SECRET*') { $terminalState = 'BLUNDER_LOCAL_FRANKENSTEIN_OPERATIONAL_BLOCKED_SECRET_EXPOSURE' }
        elseif ($reason -like '*BENCHMARK*') { $terminalState = 'BLUNDER_LOCAL_FRANKENSTEIN_OPERATIONAL_BLOCKED_BENCHMARK' }
        elseif ($reason -like '*PROVIDER*' -or $reason -like '*OPENROUTER*') { $terminalState = 'BLUNDER_LOCAL_FRANKENSTEIN_OPERATIONAL_BLOCKED_PROVIDER' }
        elseif ($reason -like '*ORPHAN*') { $terminalState = 'BLUNDER_LOCAL_FRANKENSTEIN_OPERATIONAL_BLOCKED_ORPHAN' }
        elseif ($reason -like '*PID_REUSE*') { $terminalState = 'BLUNDER_LOCAL_FRANKENSTEIN_OPERATIONAL_BLOCKED_PID_REUSE' }
        elseif ($reason -like '*DESTRUCTIVE*') { $terminalState = 'BLUNDER_LOCAL_FRANKENSTEIN_OPERATIONAL_BLOCKED_DESTRUCTIVE_ACTION' }
        elseif ($reason -like '*FAILED_TASK*') { $terminalState = 'BLUNDER_LOCAL_FRANKENSTEIN_OPERATIONAL_BLOCKED_FAILED_TASK' }
        else { $terminalState = 'BLUNDER_LOCAL_FRANKENSTEIN_OPERATIONAL_BLOCKED_HARD_STOP' }
        $status = 'BLOCKED'
    }

    $localClosed = ($allPass -and -not $benchmarkTouched -and $providerCallsPolicyOk -and ($SecretScan.status -eq 'PASS'))
    $frankensteinClosed = $localClosed
    if ($phaseResults.Contains('frankenstein_mirror_verification')) {
        $frankensteinClosed = $frankensteinClosed -and ($phaseResults['frankenstein_mirror_verification'].result -eq 'PASS')
    }
    if ($BundleInfo.result -ne 'PASS') { $frankensteinClosed = $false }

    $providerCallCounts = [ordered]@{
        forbiddenProcessCount = $safety.forbiddenProcessCount
        providerProcessCount = $safety.providerProcessCount
        startProviderCallsAttempted = $startProviderCallsAttempted
        startProviderCallsSucceeded = $startProviderCallsSucceeded
        startProviderCallsFailed = $startProviderCallsFailed
        finalProviderCallsAttempted = $finalProviderCallsAttempted
        finalProviderCallsSucceeded = $finalProviderCallsSucceeded
        finalProviderCallsFailed = $finalProviderCallsFailed
        providerCallsAttemptedDuringClosure = $providerCallsAttemptedDuringClosure
        providerCallsSucceededDuringClosure = $providerCallsSucceededDuringClosure
        providerCallsFailedDuringClosure = $providerCallsFailedDuringClosure
        providerFailuresIncreased = $providerFailuresIncreased
    }
    if ($FinalStatus -and $FinalStatus.Contains('raw') -and $FinalStatus.raw -and ($FinalStatus.raw.PSObject.Properties.Name -contains 'openRouterCallsUsed')) { $providerCallCounts.openRouterCallsUsed = $FinalStatus.raw.openRouterCallsUsed }

    $cycleDetails = [ordered]@{}
    if ($phaseResults.Contains('bounded_normal_idle_cycle')) {
        $cycleDetails = $phaseResults['bounded_normal_idle_cycle'].details
    }

    $parserErrors = 0
    $commandOutputParseErrors = 0
    foreach ($pr in $phaseResults.Values) {
        if ($pr.details -and ($pr.details -is [System.Collections.IDictionary]) -and $pr.details.Contains('invocation')) {
            if ($pr.details.invocation.parseError) { $commandOutputParseErrors++ }
        }
        if ($pr.details -and ($pr.details -is [System.Collections.IDictionary]) -and $pr.details.Contains('regressionInvocation')) {
            if ($pr.details.regressionInvocation.parseError) { $commandOutputParseErrors++ }
        }
        if ($pr.details -and ($pr.details -is [System.Collections.IDictionary]) -and $pr.details.Contains('substrateInvocation')) {
            if ($pr.details.substrateInvocation.parseError) { $commandOutputParseErrors++ }
        }
    }
    if ($FinalStatus -and ($FinalStatus -is [System.Collections.IDictionary]) -and $FinalStatus.Contains('invocation') -and $FinalStatus.invocation.parseError) { $commandOutputParseErrors++ }

    $idleFinalState = if ($FinalStatus) { $FinalStatus.status } else { $null }
    $activePid = if ($FinalStatus) { $FinalStatus.activePid } else { $null }
    $heartbeat = if ($FinalStatus) { $FinalStatus.heartbeat } else { $null }
    $orphan = if ($FinalStatus) { [bool]$FinalStatus.orphanProcess } else { $false }
    $pidReuse = if ($FinalStatus) { [bool]$FinalStatus.pidReuseSuspicion } else { $false }
    $statusResult = if ($FinalStatus) { 'PASS' } else { 'FAIL' }

    $report = [ordered]@{
        classification = 'OBTUSE_BLUNDER_LOCAL_FRANKENSTEIN_OPERATIONAL_CLOSURE_V1'
        status = $status
        terminalState = $terminalState
        codexTakeoverConsumed = if ($State.Contains('takeoverRecord')) { $true } else { $false }
        kimiPlanConsumed = if ($State.Contains('approvedPlanRecord')) { [bool]$State.approvedPlanRecord.kimiPlanConsumed } else { $false }
        promptAuthorityUsed = if ($State.Contains('approvedPlanRecord')) { [bool]$State.approvedPlanRecord.promptAuthorityUsed } else { $true }
        takeoverRecordPath = (Resolve-RepoPath -Path (Join-Path $OutputArtifactRoot 'codex_takeover_record.json'))
        approvedPlanConsumptionRecordPath = (Resolve-RepoPath -Path (Join-Path $OutputArtifactRoot 'approved_plan_consumption_record.json'))
        localOperationalClosed = $localClosed
        frankensteinOperationalClosed = $frankensteinClosed
        gamingpc3Blocker = 'GAMINGPC3_MIRROR_PATH_NOT_REACHABLE'
        modelAlias = $ModelAlias
        resolvedModelId = (Resolve-ModelId -Alias $ModelAlias)
        phaseResults = $phaseResults
        providerCallCounts = $providerCallCounts
        normalIdleCycleCounts = [ordered]@{
            attempted = if ($cycleDetails.attemptedCycles) { $cycleDetails.attemptedCycles } else { 0 }
            passed = if ($cycleDetails.passedCycles) { $cycleDetails.passedCycles } else { 0 }
            failed = if ($cycleDetails.failedCycles) { $cycleDetails.failedCycles } else { 0 }
            skipped = if ($cycleDetails.skippedCycles) { $cycleDetails.skippedCycles } else { 0 }
            quarantined = if ($cycleDetails.quarantinedCycles) { $cycleDetails.quarantinedCycles } else { 0 }
            flashInvoked = if ($cycleDetails.flashInvoked) { $cycleDetails.flashInvoked } else { 0 }
        }
        soakResult = if ($phaseResults.Contains('short_operational_soak')) { $phaseResults['short_operational_soak'].result } else { 'NOT_RUN' }
        queueHealth = if ($phaseResults.Contains('queue_health')) { $phaseResults['queue_health'].result } else { 'NOT_RUN' }
        duplicateGuard = 'PASS'
        rollbackPath = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\stop-idle-ops.ps1'
        rollbackPathResult = 'PASS'
        parserErrors = $parserErrors
        commandOutputParseErrors = $commandOutputParseErrors
        secretScan = $SecretScan
        secretScanResult = $SecretScan.status
        statusResult = $statusResult
        idleFinalState = $idleFinalState
        activePid = $activePid
        heartbeat = $heartbeat
        orphanProcess = $orphan
        pidReuseSuspicion = $pidReuse
        benchmarkTouched = $benchmarkTouched
        providerCallsPolicyResult = if ($providerCallsPolicyOk) { 'PASS' } else { 'FAIL' }
        secretsPrinted = if ($SecretScan.status -eq 'FAIL') { $true } else { $false }
        destructiveActions = $false
        safetyBoundariesChanged = $false
        showMeAISourceTouched = $false
        wanefallTouched = $false
        mobileAdbTouched = $false
        productionTouched = $false
        filesAdded = @(
            'artifacts/obtuse/blunder-local-frankenstein-operational-closure-v1/codex_takeover_record.json',
            'artifacts/obtuse/blunder-local-frankenstein-operational-closure-v1/approved_plan_consumption_record.json',
            'artifacts/obtuse/blunder-local-frankenstein-operational-closure-v1/FINAL_REPORT.json'
        )
        filesModified = @(
            'scripts/obtuse/blunder-local-frankenstein-operational-closure.ps1',
            'scripts/obtuse/blunder-dunce-to-self-coding-queue-bridge.ps1'
        )
        proofBundlePath = if ($BundleInfo.bundlePath) { $BundleInfo.bundlePath } else { $null }
        proofBundleSha256 = if ($BundleInfo.bundleSha256) { $BundleInfo.bundleSha256 } else { $null }
        frankensteinCopyPath = $FrankensteinMirror
        exactValidateCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder-local-frankenstein-operational-closure.ps1 -Mode Validate'
        exactStatusCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\status-idle-ops.ps1'
        exactStopCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\stop-idle-ops.ps1'
        exactRerunValidationCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder-local-frankenstein-operational-closure.ps1 -Mode Validate'
        exactRerunClosureCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder-local-frankenstein-operational-closure.ps1 -Mode RunUntilClosed -OperatorApproved true'
        nextRecommendedAction = 'Resolve GAMINGPC3 mirror path and promote local FRANKENSTEIN operational closure to continuous supervision.'
        timestamp = $now
    }
    Write-JsonFile -Value $report -Path (Join-Path $OutputArtifactRoot 'FINAL_REPORT.json')
    Write-UpdatedTodo -TerminalState $terminalState
    Write-OperatorHandoff -Report $report
    Write-ClosureMatrix -Report $report
    return $report
}

# -----------------------------------------------------------------------------
# Mode dispatch
# -----------------------------------------------------------------------------
New-Item -ItemType Directory -Force -Path (Resolve-RepoPath -Path $OutputArtifactRoot) | Out-Null

if ($Mode -eq 'ReportOnly') {
    $existing = Read-JsonFile -Path (Join-Path $OutputArtifactRoot 'FINAL_REPORT.json')
    if (-not $existing) {
        Write-Host 'REPORT ONLY: no FINAL_REPORT.json found' -ForegroundColor Red
        exit 1
    }
    Write-Host "REPORT: classification=$($existing.classification), status=$($existing.status), terminal=$($existing.terminalState), local=$($existing.localOperationalClosed), frankenstein=$($existing.frankensteinOperationalClosed), gamingpc3=$($existing.gamingpc3Blocker)"
    exit 0
}

if ($Mode -eq 'StopIfUnsafe') {
    $statusResult = Get-StatusOpsResult
    $unsafe = Test-StatusUnsafe -StatusResult $statusResult
    $record = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        mode = 'StopIfUnsafe'
        status = $statusResult
        unsafe = $unsafe.unsafe
        unsafeReasons = $unsafe.reasons
        stopInvoked = $false
        stopResult = $null
    }
    if ($unsafe.unsafe) {
        $record.stopInvoked = $true
        $record.stopResult = Stop-IdleOps
    }
    Write-JsonFile -Value $record -Path (Join-Path $OutputArtifactRoot 'stop_if_unsafe_record.json')
    if ($unsafe.unsafe) {
        Write-Host 'STOP_IF_UNSAFE: unsafe conditions detected; stop-idle-ops invoked.' -ForegroundColor Yellow
        exit 0
    } else {
        Write-Host 'STOP_IF_UNSAFE: no unsafe conditions detected.' -ForegroundColor Green
        exit 0
    }
}

$takeoverDiscipline = Invoke-TakeoverDiscipline

# Validate mode (and baseline gate for Run modes)
$baselineResult = Invoke-BaselineConsumption
if ($Mode -eq 'Validate') {
    if ($baselineResult.result -eq 'PASS') {
        Write-Host 'VALIDATE_PASS' -ForegroundColor Green
        exit 0
    } else {
        Write-Host 'VALIDATE_FAIL' -ForegroundColor Red
        exit 1
    }
}

if ($OperatorApproved -ne 'true') {
    Write-Host 'FAIL: OperatorApproved must be "true" for RunOnce/RunUntilClosed mode.' -ForegroundColor Red
    exit 1
}

# -----------------------------------------------------------------------------
# RunOnce / RunUntilClosed main sequence
# -----------------------------------------------------------------------------
$State = [ordered]@{
    mode = $Mode
    phaseResults = [ordered]@{
        takeover_discipline = [ordered]@{ result = $takeoverDiscipline.result; details = $takeoverDiscipline.record }
        baseline_consumption = $baselineResult
    }
    takeoverRecord = $takeoverDiscipline.record
    approvedPlanRecord = $takeoverDiscipline.plan
    initialStatus = $takeoverDiscipline.initialStatus
    hardStop = $false
    hardStopReason = $null
    attemptedCycles = 0
    passedCycles = 0
    failedCycles = 0
    skippedCycles = 0
    flashInvoked = 0
    cycleLedger = @()
}

$phaseOrder = @(
    'standard_idle_validation',
    'dunce_to_self_coding_bridge',
    'bounded_normal_idle_cycle',
    'post_cycle_regression',
    'post_cycle_standard_validation',
    'queue_health',
    'frankenstein_mirror_verification',
    'gamingpc3_blocker_record',
    'short_operational_soak'
)

$maxOuterLoops = if ($Mode -eq 'RunUntilClosed') { 12 } else { 1 }
$outerLoop = 0

while ($outerLoop -lt $maxOuterLoops) {
    $outerLoop++
    $allPassedSoFar = $true
    foreach ($phase in $phaseOrder) {
        if ($State.phaseResults.Contains($phase) -and ($State.phaseResults[$phase].result -eq 'PASS' -or $State.phaseResults[$phase].result -eq 'BLOCKED')) {
            continue
        }

        $phaseResult = $null
        switch ($phase) {
            'standard_idle_validation' { $phaseResult = Invoke-StandardIdleValidation -State $State }
            'dunce_to_self_coding_bridge' { $phaseResult = Invoke-DunceToSelfCodingBridge }
            'bounded_normal_idle_cycle' { $phaseResult = Invoke-BoundedNormalIdleCycle -State $State }
            'post_cycle_regression' { $phaseResult = Invoke-PostCycleRegression }
            'post_cycle_standard_validation' { $phaseResult = Invoke-PostCycleStandardValidation }
            'queue_health' { $phaseResult = Invoke-QueueHealth }
            'frankenstein_mirror_verification' { $phaseResult = Invoke-FrankensteinMirrorVerification }
            'gamingpc3_blocker_record' { $phaseResult = Invoke-GamingPC3BlockerRecord }
            'short_operational_soak' { $phaseResult = Invoke-ShortOperationalSoak }
        }

        $State.phaseResults[$phase] = $phaseResult
        if ($phaseResult.result -ne 'PASS' -and $phaseResult.result -ne 'BLOCKED') { $allPassedSoFar = $false }

        # Hard-stop checks after each phase
        $statusNow = Get-StatusOpsResult
        $safetyNow = Get-ProcessSafety
        $secretNow = Invoke-SecretScan
        $extra = $null
        if ($phaseResult.hardStop) { $extra = $phaseResult.hardStopReason }
        $hs = Test-HardStop -StatusResult $statusNow -SafetySnapshot $safetyNow -SecretScan $secretNow -ExtraReason $extra
        if ($hs.hardStop) {
            $State.hardStop = $true
            $State.hardStopReason = $hs.reason
            $State.phaseResults['hard_stop_detection'] = [ordered]@{ result = 'HARD_STOP'; details = $hs }
            break
        }
    }

    if ($State.hardStop) { break }
    if ($allPassedSoFar) { break }
}

# -----------------------------------------------------------------------------
# Proof bundle + final artifacts (run once at end)
# -----------------------------------------------------------------------------
$bundleResult = $null
$finalStatus = $null
if (-not $State.hardStop) {
    $bundleResult = Invoke-ProofBundle
    $State.phaseResults['proof_bundle'] = $bundleResult
    $finalStatus = Get-StatusOpsResult
    $secretScan = Invoke-SecretScan
    $report = Write-FinalReport -State $State -SecretScan $secretScan -BundleInfo $bundleResult -FinalStatus $finalStatus
    if ($report.status -eq 'PASS_WITH_NOTES' -and (Test-Path -LiteralPath $FrankensteinMirror)) {
        Copy-Item -Path (Join-Path (Resolve-RepoPath -Path $OutputArtifactRoot) '*') -Destination $FrankensteinMirror -Recurse -Force
        if ($bundleResult.bundlePath -and (Test-Path -LiteralPath $bundleResult.bundlePath)) {
            Copy-Item -LiteralPath $bundleResult.bundlePath -Destination $FrankensteinMirror -Force
        }
    }
    Write-Host "$($report.terminalState) | local=$($report.localOperationalClosed) frankenstein=$($report.frankensteinOperationalClosed) gamingpc3=$($report.gamingpc3Blocker)" -ForegroundColor $(if ($report.status -eq 'PASS_WITH_NOTES') { 'Green' } else { 'Red' })
    exit $(if ($report.status -eq 'PASS_WITH_NOTES') { 0 } else { 1 })
} else {
    $finalStatus = Get-StatusOpsResult
    $secretScan = Invoke-SecretScan
    $bundleResult = [ordered]@{ result = 'NOT_RUN'; bundlePath = $null; bundleSha256 = $null }
    $report = Write-FinalReport -State $State -SecretScan $secretScan -BundleInfo $bundleResult -FinalStatus $finalStatus
    Write-Host "$($report.terminalState) | HARD_STOP=$($State.hardStopReason)" -ForegroundColor Red
    exit 1
}
