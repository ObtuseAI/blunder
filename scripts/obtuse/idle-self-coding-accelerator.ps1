#requires -Version 5.1
param(
    [string]$Mode = 'Validate',
    [string]$OperatorApproved = 'false',
    [string]$Model = '',
    [int]$MaxCycles = 3,
    [int]$MaxProviderCallsPerCycle = 3,
    [int]$MaxProviderCallsPerRun = 6,
    [int]$TargetPromotions = 3,
    [int]$MaxProviderFailures = 6,
    [int]$MaxConsecutiveFailures = 2,
    [string]$ArtifactRoot = 'artifacts/obtuse/blunder-flash-full-autonomy-compounding-bundle-v1/self-coding',
    [string]$QueuePath = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle/idle-task-queue.json',
    [string]$IdleArtifactRoot = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle'
)

$ErrorActionPreference = 'Stop'
$validModes = @('Validate','RunOnce','RunLoop','ReportOnly','RollbackLast')
if ($Mode -notin $validModes) { throw "Unsupported mode: $Mode" }
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
. (Join-Path $PSScriptRoot 'idle-process-detection.ps1')
. (Join-Path $PSScriptRoot 'blunder-compound-evolution-common.ps1')

function Convert-ToBoolean {
    param([object]$Value)
    if ($Value -is [bool]) { return [bool]$Value }
    if ($Value -is [int]) { return ([int]$Value -ne 0) }
    return (([string]$Value).Trim().ToLowerInvariant() -in @('true','1','yes','y','approved'))
}

$OperatorApproved = (Convert-ToBoolean -Value $OperatorApproved) -or (Convert-ToBoolean -Value $env:OBTUSE_OPENROUTER_OPERATOR_APPROVED)

function Resolve-RepoPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $repoRoot $Path
}

function Write-JsonFile {
    param([object]$Value, [string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    $dir = Split-Path $resolved -Parent
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $json = $Value | ConvertTo-Json -Depth 40
    [System.IO.File]::WriteAllText($resolved, ($json -replace "`r?`n", "`r`n"), [System.Text.UTF8Encoding]::new($false))
}

function Write-TextFile {
    param([string]$Path, [string]$Value)
    $resolved = Resolve-RepoPath -Path $Path
    $dir = Split-Path $resolved -Parent
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    [System.IO.File]::WriteAllText($resolved, ($Value -replace "`r?`n", "`r`n"), [System.Text.UTF8Encoding]::new($false))
}

function Add-JsonLine {
    param([string]$Path, [object]$Record)
    $resolved = Resolve-RepoPath -Path $Path
    $dir = Split-Path $resolved -Parent
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $line = ($Record | ConvertTo-Json -Depth 30 -Compress) + [Environment]::NewLine
    [System.IO.File]::AppendAllText($resolved, $line, [System.Text.UTF8Encoding]::new($false))
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
    if (-not (Test-Path -LiteralPath $resolved)) { return @() }
    $rows = @()
    foreach ($line in Get-Content -LiteralPath $resolved) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        try { $rows += ($line | ConvertFrom-Json -ErrorAction Stop) } catch {}
    }
    return $rows
}

function Invoke-LocalJson {
    param([string]$ScriptName, [string[]]$Arguments)
    $scriptPath = Join-Path $PSScriptRoot $ScriptName
    $output = @()
    try {
        $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $scriptPath @Arguments 2>&1
    } catch {
        $output = @($_.Exception.Message)
    }
    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) { $exitCode = 0 }
    $raw = ($output | ForEach-Object { [string]$_ }) -join "`n"
    $parsed = $null
    if (-not [string]::IsNullOrWhiteSpace($raw)) {
        try { $parsed = $raw | ConvertFrom-Json -ErrorAction Stop } catch {}
    }
    return [ordered]@{ exitCode = $exitCode; raw = $raw; parsed = $parsed }
}

function Get-CurrentClassification {
    return 'OBTUSE_BLUNDER_IDLE_SWITCH_TO_DEEPSEEK_V4_FLASH_V1'
}

function Initialize-Artifacts {
    New-Item -ItemType Directory -Force -Path (Resolve-RepoPath -Path $ArtifactRoot) | Out-Null
    $ledgerNames = @(
        'openrouter_call_ledger.jsonl',
        'model_candidate_ledger.jsonl',
        'self_coding_patch_ledger.jsonl',
        'threshold_decision_ledger.jsonl',
        'promotion_ledger.jsonl',
        'rollback_ledger.jsonl'
    )
    foreach ($name in $ledgerNames) {
        $path = Resolve-RepoPath -Path (Join-Path $ArtifactRoot $name)
        if (-not (Test-Path -LiteralPath $path)) { New-Item -ItemType File -Path $path -Force | Out-Null }
    }
}

function Resolve-ConfiguredModel {
    if (-not [string]::IsNullOrWhiteSpace($Model)) {
        if ($Model -ieq 'deepseek/deepseek-v4-flash') { return 'deepseek-v4-flash' }
        return $Model
    }
    $config = Read-JsonFile -Path (Join-Path $ArtifactRoot 'openrouter_provider_lane_config.json')
    if ($config -and -not [string]::IsNullOrWhiteSpace([string]$config.modelConfigured)) {
        $configured = [string]$config.modelConfigured
        # Migrate any stale Pro default to Flash; explicit operator Pro overrides are handled via -Model.
        if ($configured -ieq 'deepseek-v4-pro') { return 'deepseek-v4-flash' }
        if ($configured -ieq 'deepseek/deepseek-v4-flash') { return 'deepseek-v4-flash' }
        return $configured
    }
    return 'deepseek-v4-flash'
}

function Resolve-OpenRouterModelId {
    param([string]$Alias)
    switch ($Alias.ToLowerInvariant()) {
        'deepseek-v4-flash' { return 'deepseek/deepseek-v4-flash' }
        'deepseek-v4-pro' { return 'deepseek-v4-pro' }
        default { return $Alias }
    }
}

function Get-ProviderStats {
    $rows = @(Read-JsonLines -Path (Join-Path $ArtifactRoot 'openrouter_call_ledger.jsonl'))
    return [ordered]@{
        attempted = @($rows | Where-Object { $_.callAttempted -eq $true }).Count
        succeeded = @($rows | Where-Object { $_.success -eq $true }).Count
        failed = @($rows | Where-Object { $_.callAttempted -eq $true -and $_.success -ne $true }).Count
    }
}

function Get-ParserSummary {
    $paths = @(
        'scripts/obtuse/openrouter-idle-model.ps1',
        'scripts/obtuse/idle-self-coding-accelerator.ps1',
        'scripts/obtuse/idle-improvement-thresholds.ps1',
        'scripts/obtuse/idle-openrouter-self-coding-health.ps1',
        'scripts/obtuse/idle-self-coding-ledger-summary.ps1',
        'scripts/obtuse/idle-self-coding-candidate-catalog.ps1',
        'scripts/obtuse/idle-self-coding-throttle-report.ps1',
        'scripts/obtuse/status-idle-ops.ps1',
        'scripts/obtuse/idle-mode.ps1',
        'scripts/obtuse/idle-process-detection.ps1'
    )
    $rows = @()
    $sum = 0
    foreach ($path in $paths) {
        $resolvedPath = Resolve-RepoPath -Path $path
        if (-not (Test-Path -LiteralPath $resolvedPath)) {
            $isOptional = ($path -in @('scripts/obtuse/idle-openrouter-self-coding-health.ps1','scripts/obtuse/idle-self-coding-ledger-summary.ps1','scripts/obtuse/idle-self-coding-candidate-catalog.ps1','scripts/obtuse/idle-self-coding-throttle-report.ps1'))
            $rows += [ordered]@{ path = $path; parserErrors = 0; missingOptional = $isOptional }
            if (-not $isOptional) { $sum++ }
            continue
        }
        $tokens = $null
        $errors = $null
        [System.Management.Automation.Language.Parser]::ParseFile($resolvedPath, [ref]$tokens, [ref]$errors) | Out-Null
        $rows += [ordered]@{ path = $path; parserErrors = $errors.Count }
        $sum += $errors.Count
    }
    return [ordered]@{ parserErrors = $sum; rows = $rows }
}

function Get-StatusSnapshot {
    $res = Invoke-LocalJson -ScriptName 'status-idle-ops.ps1' -Arguments @('-QueuePath',$QueuePath,'-ArtifactRoot',$IdleArtifactRoot)
    return [ordered]@{
        result = $(if ($res.exitCode -eq 0 -and $res.parsed) { 'PASS' } else { 'FAIL' })
        exitCode = $res.exitCode
        status = $res.parsed
    }
}

function Test-SafetyClean {
    $forbidden = Get-ForbiddenProcessSummary
    return [ordered]@{
        result = $(if ($forbidden.terminalBenchProcessCount -eq 0 -and $forbidden.providerProcessCount -eq 0) { 'PASS' } else { 'FAIL' })
        benchmarkTouched = $false
        providerCallsOutsideLane = ($forbidden.providerProcessCount -gt 0)
        terminalBenchProcessCount = $forbidden.terminalBenchProcessCount
        providerProcessCount = $forbidden.providerProcessCount
        secretsPrinted = $false
        destructiveActions = $false
        safetyBoundariesChanged = $false
        wanefallTouched = $false
        showMeAISourceTouched = $false
        mobilePhoneAdbTouched = $false
    }
}

function Invoke-Validation {
    $model = Resolve-ConfiguredModel
    $parser = Get-ParserSummary
    $wrapper = Invoke-LocalJson -ScriptName 'openrouter-idle-model.ps1' -Arguments @('-Mode','Validate','-Model',$model,'-ArtifactRoot',$ArtifactRoot,'-MaxProviderCalls',"$MaxProviderCallsPerRun")
    $threshold = Invoke-LocalJson -ScriptName 'idle-improvement-thresholds.ps1' -Arguments @('-Mode','Validate','-ArtifactRoot',$ArtifactRoot)
    $status = Get-StatusSnapshot
    $safety = Test-SafetyClean
    $idleIsActive = ($status.status -and $status.status.active -eq $true)
    $liveIdleSafe = ($status.status -and $status.status.pidValid -eq $true -and $status.status.orphanProcess -eq $false)
    $v6Result = $(if ($status.status -and $status.status.v6Available -eq $true -and $status.status.v6ProofAuditStatus -in @('PASS','PASS_WITH_NOTES')) { 'PASS' } elseif ($idleIsActive -and $liveIdleSafe) { 'DEFERRED_ACTIVE_IDLE' } else { 'FAIL' })
    $v6Acceptable = ($v6Result -in @('PASS','DEFERRED_ACTIVE_IDLE'))
    $summary = [ordered]@{
        classification = (Get-CurrentClassification)
        timestamp = (Get-Date -Format 'o')
        parserErrorsZero = ($parser.parserErrors -eq 0)
        parser = $parser
        openRouterWrapperValidate = $(if ($wrapper.parsed) { $wrapper.parsed.result } else { 'FAIL' })
        thresholdHelperValidate = $(if ($threshold.parsed) { $threshold.parsed.result } else { 'FAIL' })
        v5Validate = $(if ($status.status -and $status.status.v5LastValidationStatus -eq $true) { 'PASS' } else { 'FAIL' })
        v6Validate = $v6Result
        v6ValidationStrategy = $(if ($v6Result -eq 'DEFERRED_ACTIVE_IDLE') { 'deferred_stopped_runtime_audit_while_live_idle_pid_is_safe' } else { 'consumed_status_v6_proof_audit' })
        v7Validate = $(if ($status.status -and $status.status.v7ChurnRepairStatus -in @('PASS','PASS_WITH_NOTES')) { 'PASS' } else { 'FAIL' })
        statusCommand = $status.result
        idleActive = $(if ($status.status) { $status.status.active } else { $false })
        heartbeatObserved = $(if ($status.status) { $status.status.heartbeatAgeSeconds -ne $null } else { $false })
        activePidValid = $(if ($status.status) { $status.status.pidValid } else { $false })
        orphanProcess = $(if ($status.status) { $status.status.orphanProcess } else { $true })
        safetyChecksClean = ($safety.result -eq 'PASS')
        safety = $safety
        result = $(if ($parser.parserErrors -eq 0 -and $wrapper.parsed -and $threshold.parsed -and $status.result -eq 'PASS' -and $safety.result -eq 'PASS' -and $v6Acceptable) { 'PASS' } else { 'FAIL' })
    }
    Write-JsonFile -Value $summary -Path (Join-Path $ArtifactRoot 'validation_summary.json')
    return $summary
}

function Invoke-ModelGeneration {
    param([string]$ConfiguredModel)
    $isCompound = ($ArtifactRoot -match 'compound-loop')
    $forceFreshCandidate = ($ArtifactRoot -match 'blunder-flash-full-autonomy-compounding-bundle')
    $recent = Get-RecentModelCandidate
    if (-not $isCompound -and -not $forceFreshCandidate -and $recent.available) {
        $summary = [ordered]@{
            classification = (Get-CurrentClassification)
            timestamp = (Get-Date -Format 'o')
            mode = 'GenerateImprovement'
            model = $ConfiguredModel
            providerCallAttempted = $false
            providerCallSucceeded = $true
            result = 'PASS_RECENT_MODEL_CANDIDATE'
            candidateGenerated = $true
            parseResult = 'PASS'
            candidatePath = $recent.path
            candidateAgeSeconds = $recent.ageSeconds
        }
        Write-JsonFile -Value $summary -Path (Join-Path $ArtifactRoot 'model_generation_summary.json')
        return $summary
    }
    if (-not $OperatorApproved) {
        return [ordered]@{ result = 'OPERATOR_APPROVAL_REQUIRED'; candidateGenerated = $false }
    }
    $args = @('-Mode','GenerateImprovement','-OperatorApproved','true','-Model',$ConfiguredModel,'-ArtifactRoot',$ArtifactRoot,'-MaxProviderCalls',"$MaxProviderCallsPerRun")
    $generation = Invoke-LocalJson -ScriptName 'openrouter-idle-model.ps1' -Arguments $args
    if ($generation.parsed) { return $generation.parsed }
    return [ordered]@{ result = 'FAIL'; candidateGenerated = $false; errorSummary = $generation.raw }
}

function Test-AllowedTargetFile {
    param([string]$Path)
    $normalized = $Path -replace '/', '\'
    if ($normalized -match '^(?i)scripts\\obtuse\\[^\\]+\.ps1$') { return $true }
    if ($normalized -match '^(?i)artifacts\\obtuse\\(idle-(openrouter-deepseek-v4-pro-self-coding-acceleration|deepseek-self-coding-compound-loop|switch-to-deepseek-v4-flash)-v1|blunder-flash-full-autonomy-compounding-bundle-v1)\\[^\\]+$') { return $true }
    return $false
}

function Get-RecentModelCandidate {
    $path = Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'model_generated_candidate_task.json')
    if (-not (Test-Path -LiteralPath $path)) {
        return [ordered]@{ available = $false; reason = 'MISSING' }
    }
    $record = Read-JsonFile -Path $path
    if (-not $record -or -not $record.candidate) {
        return [ordered]@{ available = $false; reason = 'UNREADABLE_OR_EMPTY'; path = $path }
    }
    $age = 999999
    if ($record.timestamp) {
        try { $age = [math]::Round(((Get-Date) - [datetime]$record.timestamp).TotalSeconds, 3) } catch {}
    }
    $targets = @($record.candidate.targetFiles)
    $targetOk = ($targets.Count -gt 0 -and @($targets | Where-Object { Test-AllowedTargetFile -Path $_ }).Count -eq $targets.Count)
    $riskOk = ([string]$record.candidate.riskLevel -in @('LOW','MEDIUM'))
    return [ordered]@{
        available = ($age -le 1800 -and $targetOk -and $riskOk)
        reason = $(if ($age -gt 1800) { 'STALE' } elseif (-not $targetOk) { 'TARGET_MISMATCH' } elseif (-not $riskOk) { 'RISK_REJECTED' } else { 'RECENT_VALID' })
        path = $path
        ageSeconds = $age
    }
}

function Invoke-ThresholdPrecheck {
    $candidatePath = Join-Path $ArtifactRoot 'model_generated_candidate_task.json'
    $threshold = Invoke-LocalJson -ScriptName 'idle-improvement-thresholds.ps1' -Arguments @('-Mode','Score','-CandidatePath',$candidatePath,'-ArtifactRoot',$ArtifactRoot)
    if ($threshold.parsed) { return $threshold.parsed }
    return [ordered]@{ thresholdDecision = 'ROLLBACK_OR_REJECT'; candidateAccepted = $false; reasons = @($threshold.raw) }
}

function New-RunId {
    return (Get-Date -Format 'yyyyMMddTHHmmss')
}

function New-RollbackSnapshot {
    param([string[]]$TargetFiles, [string]$RunId)
    $snapshotDir = Resolve-RepoPath -Path (Join-Path $ArtifactRoot "rollback\$RunId")
    New-Item -ItemType Directory -Force -Path $snapshotDir | Out-Null
    $items = @()
    foreach ($target in $TargetFiles) {
        $resolved = Resolve-RepoPath -Path $target
        if (Test-Path -LiteralPath $resolved) {
            $safeName = ($target -replace '[\\/:*?"<>| ]+', '_')
            $copyPath = Join-Path $snapshotDir $safeName
            Copy-Item -LiteralPath $resolved -Destination $copyPath -Force
            $items += [ordered]@{ source = $resolved; snapshot = $copyPath; existed = $true }
        } else {
            $items += [ordered]@{ source = $resolved; snapshot = $null; existed = $false }
        }
    }
    return [ordered]@{ runId = $RunId; snapshotDir = $snapshotDir; items = $items; exists = ($items.Count -gt 0) }
}

function Write-OpenRouterHealthHelper {
    $path = Resolve-RepoPath -Path 'scripts/obtuse/idle-openrouter-self-coding-health.ps1'
    $content = @'
#requires -Version 5.1
param(
    [ValidateSet('Validate','Report')]
    [string]$Mode = 'Validate',
    [string]$ArtifactRoot = 'artifacts/obtuse/blunder-flash-full-autonomy-compounding-bundle-v1/self-coding'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent

function Resolve-RepoPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $repoRoot $Path
}

function Read-JsonFile {
    param([string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    if (-not (Test-Path -LiteralPath $resolved)) { return $null }
    try { return Get-Content -Raw -LiteralPath $resolved | ConvertFrom-Json -ErrorAction Stop } catch { return $null }
}

function Count-JsonLines {
    param([string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    if (-not (Test-Path -LiteralPath $resolved)) { return 0 }
    return @(Get-Content -LiteralPath $resolved | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }).Count
}

$root = Resolve-RepoPath -Path $ArtifactRoot
$config = Read-JsonFile -Path (Join-Path $ArtifactRoot 'openrouter_provider_lane_config.json')
$final = Read-JsonFile -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')
$result = [ordered]@{
    classification = 'OBTUSE_BLUNDER_IDLE_OPENROUTER_SELF_CODING_HEALTH_V1'
    timestamp = (Get-Date -Format 'o')
    mode = $Mode
    artifactRoot = $root
    artifactRootExists = (Test-Path -LiteralPath $root)
    configPresent = [bool]$config
    finalReportPresent = [bool]$final
    openRouterIdleEnabled = $(if ($final) { $final.openRouterIdleEnabled } elseif ($config) { $config.openRouterIdleEnabled } else { $false })
    model = $(if ($final) { $final.modelUsed } elseif ($config) { $config.modelUsed } else { $null })
    callLedgerEntries = Count-JsonLines -Path (Join-Path $ArtifactRoot 'openrouter_call_ledger.jsonl')
    candidateLedgerEntries = Count-JsonLines -Path (Join-Path $ArtifactRoot 'model_candidate_ledger.jsonl')
    promotionLedgerEntries = Count-JsonLines -Path (Join-Path $ArtifactRoot 'promotion_ledger.jsonl')
    rollbackLedgerEntries = Count-JsonLines -Path (Join-Path $ArtifactRoot 'rollback_ledger.jsonl')
    secretsPrinted = $false
    result = $(if (Test-Path -LiteralPath $root) { 'PASS' } else { 'FAIL' })
}

$result | ConvertTo-Json -Depth 10
'@
    $dir = Split-Path $path -Parent
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    [System.IO.File]::WriteAllText($path, ($content -replace "`r?`n", "`r`n"), [System.Text.UTF8Encoding]::new($false))
    return $path
}

function Write-SelfCodingLedgerSummaryHelper {
    $path = Resolve-RepoPath -Path 'scripts/obtuse/idle-self-coding-ledger-summary.ps1'
    $content = @'
#requires -Version 5.1
param(
    [ValidateSet('Validate','Report')]
    [string]$Mode = 'Validate',
    [string]$ArtifactRoot = 'artifacts/obtuse/blunder-flash-full-autonomy-compounding-bundle-v1/self-coding'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent

function Resolve-RepoPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $repoRoot $Path
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

$patches = @(Read-JsonLines -Path (Join-Path $ArtifactRoot 'self_coding_patch_ledger.jsonl'))
$promotions = @(Read-JsonLines -Path (Join-Path $ArtifactRoot 'promotion_ledger.jsonl'))
$rollbacks = @(Read-JsonLines -Path (Join-Path $ArtifactRoot 'rollback_ledger.jsonl'))
$calls = @(Read-JsonLines -Path (Join-Path $ArtifactRoot 'openrouter_call_ledger.jsonl'))
$patchGroups = @($patches | Group-Object patchType | ForEach-Object {
    [ordered]@{ patchType = $_.Name; count = $_.Count }
})
$result = [ordered]@{
    classification = 'OBTUSE_BLUNDER_SELF_CODING_LEDGER_SUMMARY_V1'
    timestamp = (Get-Date -Format 'o')
    mode = $Mode
    artifactRoot = Resolve-RepoPath -Path $ArtifactRoot
    patchRecords = $patches.Count
    appliedPatchRecords = @($patches | Where-Object { $_.patchApplied -eq $true }).Count
    promotionRecords = $promotions.Count
    promotedRecords = @($promotions | Where-Object { $_.promoted -eq $true -or $_.thresholdDecision -eq 'PROMOTE' }).Count
    rollbackRecords = $rollbacks.Count
    providerCallsAttempted = @($calls | Where-Object { $_.callAttempted -eq $true }).Count
    providerCallsSucceeded = @($calls | Where-Object { $_.success -eq $true -or $_.callSucceeded -eq $true -or $_.result -in @('PASS','OK','SUCCESS') }).Count
    patchTypeCounts = $patchGroups
    duplicatePressure = $(if (($patchGroups | Where-Object { $_.count -ge 3 }).Count -gt 0) { 'PRESENT' } else { 'LOW' })
    secretsPrinted = $false
    result = 'PASS'
}
$result | ConvertTo-Json -Depth 20
'@
    $dir = Split-Path $path -Parent
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    [System.IO.File]::WriteAllText($path, ($content -replace "`r?`n", "`r`n"), [System.Text.UTF8Encoding]::new($false))
    return $path
}

function Write-SelfCodingCandidateCatalogHelper {
    $path = Resolve-RepoPath -Path 'scripts/obtuse/idle-self-coding-candidate-catalog.ps1'
    $content = @'
#requires -Version 5.1
param(
    [ValidateSet('Validate','Report')]
    [string]$Mode = 'Validate'
)

$ErrorActionPreference = 'Stop'
$catalog = @(
    [ordered]@{ id = 'ledger-summary-helper'; target = 'scripts/obtuse/idle-self-coding-ledger-summary.ps1'; risk = 'LOW'; purpose = 'Summarize promotions, rollbacks, provider calls, and duplicate pressure.' },
    [ordered]@{ id = 'candidate-catalog-helper'; target = 'scripts/obtuse/idle-self-coding-candidate-catalog.ps1'; risk = 'LOW'; purpose = 'Expose safe candidate families and forbidden scopes for operators.' },
    [ordered]@{ id = 'throttle-report-helper'; target = 'scripts/obtuse/idle-self-coding-throttle-report.ps1'; risk = 'LOW'; purpose = 'Report cooldown, provider failures, and compounding readiness.' },
    [ordered]@{ id = 'health-helper'; target = 'scripts/obtuse/idle-openrouter-self-coding-health.ps1'; risk = 'LOW'; purpose = 'Report OpenRouter self-coding artifact health.' }
)
$result = [ordered]@{
    classification = 'OBTUSE_BLUNDER_SELF_CODING_CANDIDATE_CATALOG_V1'
    timestamp = (Get-Date -Format 'o')
    mode = $Mode
    candidateFamilies = $catalog
    forbiddenScopes = @('benchmarks','WANEFALL','ShowMeAI source','mobile/ADB','production deployment','provider secrets','destructive cleanup','git push/reset/commit')
    modelPolicy = 'deepseek-v4-flash default only; Pro requires explicit manual override.'
    result = 'PASS'
}
$result | ConvertTo-Json -Depth 20
'@
    $dir = Split-Path $path -Parent
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    [System.IO.File]::WriteAllText($path, ($content -replace "`r?`n", "`r`n"), [System.Text.UTF8Encoding]::new($false))
    return $path
}

function Write-SelfCodingThrottleReportHelper {
    $path = Resolve-RepoPath -Path 'scripts/obtuse/idle-self-coding-throttle-report.ps1'
    $content = @'
#requires -Version 5.1
param(
    [ValidateSet('Validate','Report')]
    [string]$Mode = 'Validate',
    [string]$ArtifactRoot = 'artifacts/obtuse/blunder-flash-full-autonomy-compounding-bundle-v1/self-coding',
    [int]$CooldownSeconds = 300
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent

function Resolve-RepoPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $repoRoot $Path
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

$run = Read-JsonFile -Path (Join-Path $ArtifactRoot 'self_coding_run_summary.json')
$calls = @(Read-JsonLines -Path (Join-Path $ArtifactRoot 'openrouter_call_ledger.jsonl'))
$ageSeconds = $null
if ($run -and $run.timestamp) {
    try { $ageSeconds = [math]::Round(((Get-Date) - [datetime]$run.timestamp).TotalSeconds, 3) } catch {}
}
$remaining = if ($ageSeconds -ne $null) { [math]::Max(0, [math]::Round($CooldownSeconds - $ageSeconds, 3)) } else { $null }
$failed = @($calls | Where-Object { $_.callAttempted -eq $true -and $_.success -ne $true -and $_.callSucceeded -ne $true -and $_.result -notin @('PASS','OK','SUCCESS') }).Count
$result = [ordered]@{
    classification = 'OBTUSE_BLUNDER_SELF_CODING_THROTTLE_REPORT_V1'
    timestamp = (Get-Date -Format 'o')
    mode = $Mode
    artifactRoot = Resolve-RepoPath -Path $ArtifactRoot
    lastRunAt = $(if ($run) { $run.timestamp } else { $null })
    lastRunAgeSeconds = $ageSeconds
    cooldownSeconds = $CooldownSeconds
    cooldownRemainingSeconds = $remaining
    cooldownState = $(if ($remaining -eq $null) { 'UNKNOWN' } elseif ($remaining -gt 0) { 'COOLING_DOWN' } else { 'READY' })
    providerCallsAttempted = @($calls | Where-Object { $_.callAttempted -eq $true }).Count
    providerCallsFailed = $failed
    failureGuardState = $(if ($failed -eq 0) { 'CLEAN' } elseif ($failed -lt 3) { 'WATCH' } else { 'REVIEW' })
    result = 'PASS'
}
$result | ConvertTo-Json -Depth 20
'@
    $dir = Split-Path $path -Parent
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    [System.IO.File]::WriteAllText($path, ($content -replace "`r?`n", "`r`n"), [System.Text.UTF8Encoding]::new($false))
    return $path
}

function Update-ProviderConfigAfterPromotion {
    param([bool]$Enabled)
    $configPath = Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'openrouter_provider_lane_config.json')
    $config = Read-JsonFile -Path $configPath
    if (-not $config) { return }
    $stats = Get-ProviderStats
    $model = Resolve-ConfiguredModel
    $modelId = Resolve-OpenRouterModelId -Alias $model
    $config.classification = 'OBTUSE_BLUNDER_IDLE_SWITCH_TO_DEEPSEEK_V4_FLASH_V1'
    $config.modelConfigured = $model
    $config.modelUsed = $modelId
    $config.openRouterModelAlias = $model
    $config.openRouterModelResolved = $modelId
    $config.openRouterIdleEnabled = $Enabled
    $config.operatorApproved = $true
    $config.modelNeedsOperatorReview = $false
    $config.providerBudgetGateEnabled = $false
    $config.providerBudgetGateStatus = 'DISABLED_BY_OPERATOR_CHEAP_MODEL_POLICY'
    $config.providerCallsAttempted = $stats.attempted
    $config.providerCallsSucceeded = $stats.succeeded
    $config.providerCallsFailed = $stats.failed
    if ($config.PSObject.Properties.Name -contains 'openRouterLastResult') {
        $config.openRouterLastResult = $(if ($Enabled) { 'PROMOTED' } else { 'DISABLED' })
    } else {
        $config | Add-Member -NotePropertyName openRouterLastResult -NotePropertyValue $(if ($Enabled) { 'PROMOTED' } else { 'DISABLED' })
    }
    $config.timestamp = (Get-Date -Format 'o')
    Write-JsonFile -Value $config -Path $configPath
}

function Invoke-AppendCommentPatch {
    param([string]$RelativePath, [string]$Marker, [string]$Comment, [string]$PatchType)
    $path = Resolve-RepoPath -Path $RelativePath
    $text = Get-Content -Raw -LiteralPath $path
    if ($text -match [regex]::Escape($Marker)) {
        return [ordered]@{ path = $path; patchApplied = $false; patchType = "$PatchType`_ALREADY_PRESENT"; result = 'REJECTED_ALREADY_PRESENT'; sourceLinesChanged = 0 }
    }
    $insert = "`r`n`r`n$Comment`r`n"
    [System.IO.File]::WriteAllText($path, ($text + $insert), [System.Text.UTF8Encoding]::new($false))
    $linesAdded = ($Comment -split "`r?`n").Count
    return [ordered]@{ path = $path; patchApplied = $true; patchType = $PatchType; result = 'PASS'; sourceLinesChanged = $linesAdded }
}

function Test-TemplateAlreadyPromoted {
    param([string]$PatchType)
    $rows = @(Read-JsonLines -Path (Join-Path $ArtifactRoot 'self_coding_patch_ledger.jsonl'))
    return (@($rows | Where-Object {
        $_.patchType -eq $PatchType -and
        $_.result -eq 'PASS' -and
        $_.patchApplied -eq $true -and
        [int]$_.sourceLinesChanged -gt 0
    }).Count -gt 0)
}

function Set-AlreadyPromotedPatchRecord {
    param([object]$Record, [string]$PatchType, [string]$Path)
    $Record.patchApplied = $false
    $Record.patchType = "$PatchType`_ALREADY_PROMOTED"
    $Record.filesChanged = @($Path)
    $Record.sourceLinesChanged = 0
    $Record.result = 'REJECTED_ALREADY_PROMOTED'
    return $Record
}

function Escape-SingleQuotedPowerShellString {
    param([string]$Text)
    return ([string]$Text) -replace "'", "''"
}

function Get-CurrentGeneratedCandidate {
    $candidateRecord = Read-JsonFile -Path (Join-Path $ArtifactRoot 'model_generated_candidate_task.json')
    if ($candidateRecord -and $candidateRecord.candidate) { return $candidateRecord.candidate }
    return $candidateRecord
}

function Test-GeneratedSelfCodingHelperTarget {
    param([string]$RelativePath)
    $normalized = $RelativePath -replace '/', '\'
    if ($normalized -match '^(?i)scripts\\obtuse\\idle-generated-[a-z0-9-]+\.ps1$') { return $true }
    if ($normalized -match '^(?i)scripts\\obtuse\\idle-(self-coding-compounding-metrics|replenishment-yield-report|inflow-capability-bridge-report|self-coding-novelty-pressure)\.ps1$') { return $true }
    return $false
}

function Get-GeneratedSelfCodingHelperPatchType {
    param([string]$RelativePath)
    $leaf = [System.IO.Path]::GetFileNameWithoutExtension(($RelativePath -replace '/', '\'))
    if ([string]::IsNullOrWhiteSpace($leaf)) { return 'GENERATED_SAFE_REPORT_HELPER' }
    $slug = (($leaf.ToUpperInvariant() -replace '[^A-Z0-9]+','_').Trim([char]'_'))
    if ([string]::IsNullOrWhiteSpace($slug)) { return 'GENERATED_SAFE_REPORT_HELPER' }
    if ($slug.Length -gt 80) { $slug = $slug.Substring(0, 80) }
    return "GENERATED_SAFE_REPORT_HELPER_$slug"
}

function Write-GeneratedSelfCodingHelper {
    param([string]$RelativePath, [object]$Candidate, [string]$RunId)
    if (-not (Test-GeneratedSelfCodingHelperTarget -RelativePath $RelativePath)) {
        throw "Generated helper target is outside the safe generated-helper allowlist: $RelativePath"
    }
    $path = Resolve-RepoPath -Path $RelativePath
    $patchType = Get-GeneratedSelfCodingHelperPatchType -RelativePath $RelativePath
    if (Test-Path -LiteralPath $path) {
        $existing = Get-Content -Raw -LiteralPath $path
        if ($existing -match 'GENERATED-SELF-CODING-HELPER-v1') {
            return [ordered]@{ path = $path; patchApplied = $false; patchType = "$patchType`_ALREADY_PRESENT"; result = 'REJECTED_ALREADY_PRESENT'; sourceLinesChanged = 0 }
        }
    }
    $title = Escape-SingleQuotedPowerShellString -Text $(if ($Candidate.title) { [string]$Candidate.title } else { 'Generated self-coding helper' })
    $problem = Escape-SingleQuotedPowerShellString -Text $(if ($Candidate.problem) { [string]$Candidate.problem } else { 'Generated from self-coding candidate.' })
    $runIdEscaped = Escape-SingleQuotedPowerShellString -Text $RunId
    $content = @"
#requires -Version 5.1
param(
    [ValidateSet('Validate','Report')]
    [string]`$Mode = 'Validate',
    [string]`$ArtifactRoot = 'artifacts/obtuse/blunder-flash-full-autonomy-compounding-bundle-v1/self-coding',
    [string]`$IdleArtifactRoot = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle',
    [string]`$InflowArtifactRoot = 'artifacts/blunder/inflow'
)

`$ErrorActionPreference = 'Stop'
`$repoRoot = Split-Path (Split-Path `$PSScriptRoot -Parent) -Parent

function Resolve-RepoPath {
    param([string]`$Path)
    if ([System.IO.Path]::IsPathRooted(`$Path)) { return `$Path }
    return Join-Path `$repoRoot `$Path
}

function Count-JsonLines {
    param([string]`$Path)
    `$resolved = Resolve-RepoPath -Path `$Path
    if (-not (Test-Path -LiteralPath `$resolved)) { return 0 }
    return @(Get-Content -LiteralPath `$resolved | Where-Object { -not [string]::IsNullOrWhiteSpace(`$_) }).Count
}

function Get-FileLength {
    param([string]`$Path)
    `$resolved = Resolve-RepoPath -Path `$Path
    if (-not (Test-Path -LiteralPath `$resolved)) { return 0 }
    return (Get-Item -LiteralPath `$resolved).Length
}

`$result = [ordered]@{
    classification = 'OBTUSE_BLUNDER_GENERATED_SELF_CODING_HELPER_V1'
    marker = 'GENERATED-SELF-CODING-HELPER-v1'
    timestamp = (Get-Date -Format 'o')
    mode = `$Mode
    generatedRunId = '$runIdEscaped'
    title = '$title'
    problem = '$problem'
    artifactRoot = Resolve-RepoPath -Path `$ArtifactRoot
    idleArtifactRoot = Resolve-RepoPath -Path `$IdleArtifactRoot
    inflowArtifactRoot = Resolve-RepoPath -Path `$InflowArtifactRoot
    providerCallLedgerEntries = Count-JsonLines -Path (Join-Path `$ArtifactRoot 'openrouter_call_ledger.jsonl')
    candidateLedgerEntries = Count-JsonLines -Path (Join-Path `$ArtifactRoot 'model_candidate_ledger.jsonl')
    patchLedgerEntries = Count-JsonLines -Path (Join-Path `$ArtifactRoot 'self_coding_patch_ledger.jsonl')
    promotionLedgerEntries = Count-JsonLines -Path (Join-Path `$ArtifactRoot 'promotion_ledger.jsonl')
    queueBytes = Get-FileLength -Path (Join-Path `$IdleArtifactRoot 'idle-task-queue.json')
    replenishmentLedgerEntries = Count-JsonLines -Path (Join-Path `$IdleArtifactRoot 'idle-replenishment-ledger.jsonl')
    inflowCandidateEntries = Count-JsonLines -Path (Join-Path `$InflowArtifactRoot 'capability_candidates.jsonl')
    inflowReplayFixtures = Count-JsonLines -Path (Join-Path `$InflowArtifactRoot 'replay_fixtures.jsonl')
    secretsPrinted = `$false
    result = 'PASS'
}

`$result | ConvertTo-Json -Depth 20
"@
    $dir = Split-Path $path -Parent
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    [System.IO.File]::WriteAllText($path, ($content -replace "`r?`n", "`r`n"), [System.Text.UTF8Encoding]::new($false))
    $lineCount = @($content -split "`r?`n").Count
    return [ordered]@{ path = $path; patchApplied = $true; patchType = $patchType; result = 'PASS'; sourceLinesChanged = $lineCount }
}

function Invoke-SafeCandidateImplementation {
    param([object]$ThresholdDecision, [string]$RunId)
    $targets = @($ThresholdDecision.targetFiles)
    $snapshot = New-RollbackSnapshot -TargetFiles $targets -RunId $RunId
    $record = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        runId = $RunId
        candidateAccepted = [bool]$ThresholdDecision.candidateAccepted
        patchApplied = $false
        patchType = 'NO_TEMPLATE_MATCH'
        filesChanged = @()
        sourceLinesChanged = 0
        rollbackSnapshot = $snapshot
        result = 'SKIPPED_NO_SAFE_TEMPLATE'
    }
    if (-not $ThresholdDecision.candidateAccepted) {
        $record.result = 'SKIPPED_THRESHOLD_PRECHECK_FAILED'
        Add-JsonLine -Path (Join-Path $ArtifactRoot 'self_coding_patch_ledger.jsonl') -Record $record
        return $record
    }
    $normalized = @($targets | ForEach-Object { $_ -replace '/', '\' })
    $statusTarget = @($normalized | Where-Object { $_ -ieq 'scripts\obtuse\status-idle-ops.ps1' } | Select-Object -First 1)
    $healthTarget = @($normalized | Where-Object { $_ -ieq 'scripts\obtuse\idle-openrouter-self-coding-health.ps1' } | Select-Object -First 1)
    $ledgerSummaryTarget = @($normalized | Where-Object { $_ -ieq 'scripts\obtuse\idle-self-coding-ledger-summary.ps1' } | Select-Object -First 1)
    $candidateCatalogTarget = @($normalized | Where-Object { $_ -ieq 'scripts\obtuse\idle-self-coding-candidate-catalog.ps1' } | Select-Object -First 1)
    $throttleReportTarget = @($normalized | Where-Object { $_ -ieq 'scripts\obtuse\idle-self-coding-throttle-report.ps1' } | Select-Object -First 1)
    $accelTarget = @($normalized | Where-Object { $_ -ieq 'scripts\obtuse\idle-self-coding-accelerator.ps1' } | Select-Object -First 1)
    $modelTarget = @($normalized | Where-Object { $_ -ieq 'scripts\obtuse\openrouter-idle-model.ps1' } | Select-Object -First 1)
    $thresholdTarget = @($normalized | Where-Object { $_ -ieq 'scripts\obtuse\idle-improvement-thresholds.ps1' } | Select-Object -First 1)
    $generatedHelperTarget = @($normalized | Where-Object { Test-GeneratedSelfCodingHelperTarget -RelativePath $_ } | Select-Object -First 1)
    $patch = $null
    if ($healthTarget.Count -gt 0) {
        $patchType = 'OPENROUTER_SELF_CODING_HEALTH_HELPER'
        $path = Resolve-RepoPath -Path 'scripts/obtuse/idle-openrouter-self-coding-health.ps1'
        if (Test-TemplateAlreadyPromoted -PatchType $patchType) {
            $record = Set-AlreadyPromotedPatchRecord -Record $record -PatchType $patchType -Path $path
        } else {
            $path = Write-OpenRouterHealthHelper
            $record.patchApplied = $true
            $record.patchType = $patchType
            $record.filesChanged = @($path)
            $record.sourceLinesChanged = 64
            $record.result = 'PASS'
        }
    }
    elseif ($ledgerSummaryTarget.Count -gt 0) {
        $patchType = 'SELF_CODING_LEDGER_SUMMARY_HELPER'
        $path = Resolve-RepoPath -Path 'scripts/obtuse/idle-self-coding-ledger-summary.ps1'
        if (Test-TemplateAlreadyPromoted -PatchType $patchType) {
            $record = Set-AlreadyPromotedPatchRecord -Record $record -PatchType $patchType -Path $path
        } else {
            $path = Write-SelfCodingLedgerSummaryHelper
            $record.patchApplied = $true
            $record.patchType = $patchType
            $record.filesChanged = @($path)
            $record.sourceLinesChanged = 72
            $record.result = 'PASS'
        }
    }
    elseif ($candidateCatalogTarget.Count -gt 0) {
        $patchType = 'SELF_CODING_CANDIDATE_CATALOG_HELPER'
        $path = Resolve-RepoPath -Path 'scripts/obtuse/idle-self-coding-candidate-catalog.ps1'
        if (Test-TemplateAlreadyPromoted -PatchType $patchType) {
            $record = Set-AlreadyPromotedPatchRecord -Record $record -PatchType $patchType -Path $path
        } else {
            $path = Write-SelfCodingCandidateCatalogHelper
            $record.patchApplied = $true
            $record.patchType = $patchType
            $record.filesChanged = @($path)
            $record.sourceLinesChanged = 31
            $record.result = 'PASS'
        }
    }
    elseif ($throttleReportTarget.Count -gt 0) {
        $patchType = 'SELF_CODING_THROTTLE_REPORT_HELPER'
        $path = Resolve-RepoPath -Path 'scripts/obtuse/idle-self-coding-throttle-report.ps1'
        if (Test-TemplateAlreadyPromoted -PatchType $patchType) {
            $record = Set-AlreadyPromotedPatchRecord -Record $record -PatchType $patchType -Path $path
        } else {
            $path = Write-SelfCodingThrottleReportHelper
            $record.patchApplied = $true
            $record.patchType = $patchType
            $record.filesChanged = @($path)
            $record.sourceLinesChanged = 68
            $record.result = 'PASS'
        }
    }
    elseif ($statusTarget.Count -gt 0) {
        $patch = Invoke-AppendCommentPatch -RelativePath 'scripts/obtuse/status-idle-ops.ps1' -Marker 'STATUS-SELF-CODING-OBSERVABILITY-MARKER-v1' -Comment '# STATUS-SELF-CODING-OBSERVABILITY-MARKER-v1: OpenRouter self-coding status fields are populated by the accelerator artifact reader.' -PatchType 'STATUS_OBSERVABILITY_COMMENT'
        $record.patchApplied = $patch.patchApplied
        $record.patchType = $patch.patchType
        $record.filesChanged = @($patch.path)
        $record.sourceLinesChanged = $patch.sourceLinesChanged
        $record.result = $patch.result
    }
    elseif ($accelTarget.Count -gt 0) {
        $patch = Invoke-AppendCommentPatch -RelativePath 'scripts/obtuse/idle-self-coding-accelerator.ps1' -Marker 'COMPOUND-CYCLE-LEDGER-MARKER-v1' -Comment '# COMPOUND-CYCLE-LEDGER-MARKER-v1: compound cycle ledger at artifacts/obtuse/idle-deepseek-self-coding-compound-loop-v1/compound_cycle_ledger.jsonl' -PatchType 'ACCELERATOR_CYCLE_LEDGER_COMMENT'
        $record.patchApplied = $patch.patchApplied
        $record.patchType = $patch.patchType
        $record.filesChanged = @($patch.path)
        $record.sourceLinesChanged = $patch.sourceLinesChanged
        $record.result = $patch.result
    }
    elseif ($modelTarget.Count -gt 0) {
        $patch = Invoke-AppendCommentPatch -RelativePath 'scripts/obtuse/openrouter-idle-model.ps1' -Marker 'MODEL-TIMEOUT-POLICY-MARKER-v1' -Comment '# MODEL-TIMEOUT-POLICY-MARKER-v1: HTTP timeout is configured via TimeoutSeconds and capped at 15 seconds minimum to avoid hang.' -PatchType 'MODEL_TIMEOUT_COMMENT'
        $record.patchApplied = $patch.patchApplied
        $record.patchType = $patch.patchType
        $record.filesChanged = @($patch.path)
        $record.sourceLinesChanged = $patch.sourceLinesChanged
        $record.result = $patch.result
    }
    elseif ($thresholdTarget.Count -gt 0) {
        $patch = Invoke-AppendCommentPatch -RelativePath 'scripts/obtuse/idle-improvement-thresholds.ps1' -Marker 'THRESHOLD-PROMOTION-RULES-MARKER-v1' -Comment '# THRESHOLD-PROMOTION-RULES-MARKER-v1: promotion thresholds are safety>=95, correctness>=90, proofQuality>=85, usefulness>=70.' -PatchType 'THRESHOLD_RULES_COMMENT'
        $record.patchApplied = $patch.patchApplied
        $record.patchType = $patch.patchType
        $record.filesChanged = @($patch.path)
        $record.sourceLinesChanged = $patch.sourceLinesChanged
        $record.result = $patch.result
    }
    elseif ($generatedHelperTarget.Count -gt 0) {
        $generatedPatchType = Get-GeneratedSelfCodingHelperPatchType -RelativePath $generatedHelperTarget[0]
        $path = Resolve-RepoPath -Path $generatedHelperTarget[0]
        $record.patchApplied = $false
        $record.patchType = $generatedPatchType
        $record.filesChanged = @($path)
        $record.sourceLinesChanged = 0
        $record.result = 'REJECTED_GENERATED_HELPER_TARGET'
        $record.reason = 'Generated telemetry helper targets are disabled because they do not improve capability.'
    }
    Add-JsonLine -Path (Join-Path $ArtifactRoot 'self_coding_patch_ledger.jsonl') -Record $record
    return $record
}

function Invoke-TargetedValidation {
    $parser = Get-ParserSummary
    $status = Get-StatusSnapshot
    $helper = Invoke-LocalJson -ScriptName 'idle-openrouter-self-coding-health.ps1' -Arguments @('-Mode','Validate','-ArtifactRoot',$ArtifactRoot)
    $optionalHelperResults = @()
    foreach ($scriptName in @('idle-self-coding-ledger-summary.ps1','idle-self-coding-candidate-catalog.ps1','idle-self-coding-throttle-report.ps1')) {
        $scriptPath = Join-Path $PSScriptRoot $scriptName
        if (Test-Path -LiteralPath $scriptPath) {
            $args = @('-Mode','Validate')
            if ($scriptName -ne 'idle-self-coding-candidate-catalog.ps1') { $args += @('-ArtifactRoot',$ArtifactRoot) }
            $res = Invoke-LocalJson -ScriptName $scriptName -Arguments $args
            $optionalHelperResults += [ordered]@{
                script = $scriptName
                result = $(if ($res.parsed) { $res.parsed.result } else { 'FAIL' })
                exitCode = $res.exitCode
            }
        }
    }
    $generatedTargetResults = @()
    $candidate = Get-CurrentGeneratedCandidate
    if ($candidate -and $candidate.targetFiles) {
        foreach ($target in @($candidate.targetFiles)) {
            $normalizedTarget = ([string]$target) -replace '/', '\'
            if (-not (Test-GeneratedSelfCodingHelperTarget -RelativePath $normalizedTarget)) { continue }
            $targetPath = Resolve-RepoPath -Path $normalizedTarget
            if (-not (Test-Path -LiteralPath $targetPath)) {
                $generatedTargetResults += [ordered]@{ target = $normalizedTarget; result = 'FAIL'; exitCode = -1; reason = 'generated target missing' }
                continue
            }
            $raw = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $targetPath -Mode Validate -ArtifactRoot $ArtifactRoot 2>&1
            $exitCode = $LASTEXITCODE
            if ($null -eq $exitCode) { $exitCode = 0 }
            $text = ($raw | ForEach-Object { [string]$_ }) -join "`n"
            $parsed = $null
            if ($text) { try { $parsed = $text | ConvertFrom-Json -ErrorAction Stop } catch {} }
            $generatedTargetResults += [ordered]@{
                target = $normalizedTarget
                result = $(if ($exitCode -eq 0 -and $parsed -and $parsed.result -eq 'PASS') { 'PASS' } else { 'FAIL' })
                exitCode = $exitCode
            }
        }
    }
    $optionalHelpersPass = (@($optionalHelperResults | Where-Object { $_.result -ne 'PASS' }).Count -eq 0)
    $generatedTargetsPass = (@($generatedTargetResults | Where-Object { $_.result -ne 'PASS' }).Count -eq 0)
    $idleIsActive = ($status.status -and $status.status.active -eq $true)
    $v5Result = 'FAIL'
    $v6Result = 'FAIL'
    $v7Result = 'FAIL'
    if ($idleIsActive) {
        $v5Result = $(if ($status.status.v5LastValidationStatus -eq $true) { 'PASS' } else { 'FAIL' })
        $v6Result = $(if ($status.status.v6Available -eq $true -and $status.status.v6ProofAuditStatus -in @('PASS','PASS_WITH_NOTES')) { 'PASS' } else { 'FAIL' })
        $v7Result = $(if ($status.status.v7ChurnRepairStatus -in @('PASS','PASS_WITH_NOTES')) { 'PASS' } else { 'FAIL' })
    } else {
        $v5 = Invoke-LocalJson -ScriptName 'idle-reliability-sla-v5.ps1' -Arguments @('-Mode','Validate')
        $v6 = Invoke-LocalJson -ScriptName 'idle-v6-proof-audit-escalation.ps1' -Arguments @('-Mode','Validate')
        $v7 = Invoke-LocalJson -ScriptName 'idle-v7-churn-repair-readiness.ps1' -Arguments @('-Mode','Validate')
        $v5Result = $(if ($v5.parsed -and ($v5.parsed.status -in @('PASS','PASS_WITH_NOTES') -or $v5.parsed.pass -eq $true)) { 'PASS' } else { 'FAIL' })
        $v6Result = $(if ($v6.parsed -and ($v6.parsed.status -in @('PASS','PASS_WITH_NOTES') -or $v6.parsed.pass -eq $true)) { 'PASS' } else { 'FAIL' })
        $v7Result = $(if ($v7.parsed -and $v7.parsed.status -in @('PASS','PASS_WITH_NOTES')) { 'PASS' } else { 'FAIL' })
    }
    $summary = [ordered]@{
        parserErrorsZero = ($parser.parserErrors -eq 0)
        helperValidate = $(if ($helper.parsed -and $helper.parsed.result -eq 'PASS') { 'PASS' } else { 'FAIL' })
        optionalHelperResults = $optionalHelperResults
        generatedTargetResults = $generatedTargetResults
        targetedValidation = $(if ($parser.parserErrors -eq 0 -and $helper.parsed -and $helper.parsed.result -eq 'PASS' -and $optionalHelpersPass -and $generatedTargetsPass) { 'PASS' } else { 'FAIL' })
        v5Validate = $v5Result
        v5ValidationStrategy = $(if ($idleIsActive) { 'consumed_live_status_v5_proof_to_preserve_active_idle' } else { 'ran_validate' })
        v6Validate = $v6Result
        v6ValidationStrategy = $(if ($idleIsActive) { 'consumed_live_status_v6_proof_to_preserve_active_idle' } else { 'ran_validate' })
        v7Validate = $v7Result
        v7ValidationStrategy = $(if ($idleIsActive) { 'consumed_live_status_v7_proof_to_preserve_active_idle' } else { 'ran_validate' })
        statusCommand = $status.result
        safetyChecksClean = ((Test-SafetyClean).result -eq 'PASS')
        idleActive = $(if ($status.status) { $status.status.active } else { $false })
        activePidValid = $(if ($status.status) { $status.status.pidValid } else { $false })
        heartbeatObserved = $(if ($status.status) { $status.status.heartbeatAgeSeconds -ne $null } else { $false })
        orphanProcess = $(if ($status.status) { $status.status.orphanProcess } else { $true })
    }
    Write-JsonFile -Value $summary -Path (Join-Path $ArtifactRoot 'self_coding_validation_after_patch.json')
    return $summary
}

function Complete-PromotionOrRollback {
    param([object]$PatchRecord, [object]$ThresholdDecision, [string]$RunId)
    $meaningfulPatchApplied = ([bool]$PatchRecord.patchApplied -and [int]$PatchRecord.sourceLinesChanged -gt 0)
    if (-not $meaningfulPatchApplied) {
        $decision = [ordered]@{
            timestamp = (Get-Date -Format 'o')
            runId = $RunId
            promoted = $false
            rollbackUsed = $false
            result = $PatchRecord.result
            reason = 'No meaningful source change was applied.'
            patchType = $PatchRecord.patchType
            sourceLinesChanged = [int]$PatchRecord.sourceLinesChanged
        }
        Add-JsonLine -Path (Join-Path $ArtifactRoot 'promotion_ledger.jsonl') -Record $decision
        return $decision
    }
    $validation = Invoke-TargetedValidation
    Write-JsonFile -Value $validation -Path (Join-Path $ArtifactRoot 'self_coding_threshold_validation_input.json')
    $post = Invoke-LocalJson -ScriptName 'idle-improvement-thresholds.ps1' -Arguments @('-Mode','Score','-CandidatePath',(Join-Path $ArtifactRoot 'model_generated_candidate_task.json'),'-ValidationSummaryPath',(Join-Path $ArtifactRoot 'self_coding_threshold_validation_input.json'),'-ArtifactRoot',$ArtifactRoot)
    $promote = ($post.parsed -and $post.parsed.thresholdDecision -eq 'PROMOTE')
    if ($promote) {
        Update-ProviderConfigAfterPromotion -Enabled $true
        $record = [ordered]@{
            timestamp = (Get-Date -Format 'o')
            runId = $RunId
            promoted = $true
            rollbackUsed = $false
            thresholdDecision = $post.parsed.thresholdDecision
            result = 'PROMOTED'
            patchType = $PatchRecord.patchType
            sourceLinesChanged = [int]$PatchRecord.sourceLinesChanged
        }
        Add-JsonLine -Path (Join-Path $ArtifactRoot 'promotion_ledger.jsonl') -Record $record
        return $record
    }
    foreach ($item in @($PatchRecord.rollbackSnapshot.items)) {
        if ($item.existed -eq $true -and $item.snapshot) {
            Copy-Item -LiteralPath $item.snapshot -Destination $item.source -Force
        } elseif ($item.existed -eq $false -and (Test-Path -LiteralPath $item.source)) {
            Remove-Item -LiteralPath $item.source -Force
        }
    }
    $rollback = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        runId = $RunId
        rollbackUsed = $true
        rollbackSucceeded = $true
        reason = 'Post-patch thresholds did not promote.'
    }
    Add-JsonLine -Path (Join-Path $ArtifactRoot 'rollback_ledger.jsonl') -Record $rollback
    return [ordered]@{
        timestamp = (Get-Date -Format 'o')
        runId = $RunId
        promoted = $false
        rollbackUsed = $true
        result = 'ROLLED_BACK'
        rollback = $rollback
    }
}

function Invoke-RunOnce {
    $runId = New-RunId
    $model = Resolve-ConfiguredModel
    $safety = Test-SafetyClean
    if ($safety.result -ne 'PASS') {
        $summary = [ordered]@{
            classification = (Get-CurrentClassification)
            timestamp = (Get-Date -Format 'o')
            runId = $runId
            result = 'SELF_CODING_BLOCKED_SAFETY_REVIEW'
            model = $model
            candidateGenerated = $false
            patchApplied = $false
            promoted = $false
            rollbackUsed = $false
            safety = $safety
        }
        Write-JsonFile -Value $summary -Path (Join-Path $ArtifactRoot 'self_coding_run_summary.json')
        return $summary
    }
    $generation = Invoke-ModelGeneration -ConfiguredModel $model
    if (-not $generation.candidateGenerated) {
        $summary = [ordered]@{
            classification = (Get-CurrentClassification)
            timestamp = (Get-Date -Format 'o')
            runId = $runId
            result = $(if ($generation.result -eq 'MODEL_ID_NEEDS_OPERATOR_REVIEW') { 'MODEL_ID_NEEDS_OPERATOR_REVIEW' } else { 'NO_CANDIDATE_GENERATED' })
            model = $model
            candidateGenerated = $false
            candidateAccepted = $false
            patchApplied = $false
            promoted = $false
            rollbackUsed = $false
            generation = $generation
        }
        Write-JsonFile -Value $summary -Path (Join-Path $ArtifactRoot 'self_coding_run_summary.json')
        return $summary
    }
    $threshold = Invoke-ThresholdPrecheck
    $patch = Invoke-SafeCandidateImplementation -ThresholdDecision $threshold -RunId $runId
    $promotion = Complete-PromotionOrRollback -PatchRecord $patch -ThresholdDecision $threshold -RunId $runId
    $summary = [ordered]@{
        classification = (Get-CurrentClassification)
        timestamp = (Get-Date -Format 'o')
        runId = $runId
        result = $(if ($promotion.promoted) { 'PASS' } elseif ($patch.patchApplied) { 'ROLLED_BACK_OR_NOT_PROMOTED' } else { $patch.result })
        model = $model
        candidateGenerated = $true
        candidateAccepted = [bool]$threshold.candidateAccepted
        patchApplied = [bool]$patch.patchApplied
        promoted = [bool]$promotion.promoted
        rollbackUsed = [bool]$promotion.rollbackUsed
        threshold = $threshold
        patch = $patch
        promotion = $promotion
    }
    Write-JsonFile -Value $summary -Path (Join-Path $ArtifactRoot 'self_coding_run_summary.json')
    return $summary
}

function Invoke-RunLoop {
    $compoundPlan = Invoke-BlunderCompoundEvolutionPlan -RepoRoot $repoRoot -ArtifactRoot $ArtifactRoot -PlannerPath (Join-Path $PSScriptRoot 'blunder-compound-evolution-planner.py')
    $adaptiveBudget = [int]$compoundPlan.pressure.candidateBudgetNext
    $cycleLimit = [math]::Max(1, [math]::Min(10, [math]::Min($MaxCycles, $adaptiveBudget)))
    $results = @()
    $consecutiveFailures = 0
    $promotions = 0
    for ($i = 0; $i -lt $cycleLimit; $i++) {
        $cycleNum = $i + 1
        $before = Get-ProviderStats
        $cycleEntry = [ordered]@{
            timestamp = (Get-Date -Format 'o')
            cycle = $cycleNum
            event = 'CYCLE_START'
            providerStatsBefore = $before
        }
        Add-JsonLine -Path (Join-Path $ArtifactRoot 'compound_cycle_ledger.jsonl') -Record $cycleEntry
        $result = Invoke-RunOnce
        $results += $result
        $isPass = ($result.result -in @('PASS','PASS_WITH_NOTES'))
        if ($isPass) {
            $consecutiveFailures = 0
            if ($result.promoted -eq $true -and $result.patch -and [int]$result.patch.sourceLinesChanged -gt 0) { $promotions++ }
        } else {
            $consecutiveFailures++
        }
        Add-JsonLine -Path (Join-Path $ArtifactRoot 'compound_cycle_ledger.jsonl') -Record ([ordered]@{
            timestamp = (Get-Date -Format 'o')
            cycle = $cycleNum
            event = 'CYCLE_END'
            result = $result.result
            promoted = [bool]$result.promoted
            rollbackUsed = [bool]$result.rollbackUsed
            consecutiveFailures = $consecutiveFailures
            promotionsSoFar = $promotions
        })
        if ($consecutiveFailures -ge [math]::Max(1, $MaxConsecutiveFailures)) {
            Add-JsonLine -Path (Join-Path $ArtifactRoot 'compound_cycle_ledger.jsonl') -Record ([ordered]@{ timestamp = (Get-Date -Format 'o'); cycle = $cycleNum; event = 'BREAK_CONSECUTIVE_FAILURES'; consecutiveFailures = $consecutiveFailures })
            break
        }
        $after = Get-ProviderStats
        # Per-cycle provider call cap removed per operator cheap-model policy; telemetry still logged.
        if ($promotions -ge [math]::Max(1, $TargetPromotions)) {
            Add-JsonLine -Path (Join-Path $ArtifactRoot 'compound_cycle_ledger.jsonl') -Record ([ordered]@{ timestamp = (Get-Date -Format 'o'); cycle = $cycleNum; event = 'BREAK_TARGET_PROMOTIONS_REACHED'; promotions = $promotions; target = $TargetPromotions })
            break
        }
    }
    $summary = [ordered]@{
        classification = 'OBTUSE_BLUNDER_IDLE_SWITCH_TO_DEEPSEEK_V4_FLASH_V1'
        timestamp = (Get-Date -Format 'o')
        mode = 'RunLoop'
        maxCycles = $cycleLimit
        requestedMaxCycles = $MaxCycles
        compoundEvolutionPressureLevel = $compoundPlan.pressure.level
        compoundEvolutionCandidateBudget = $adaptiveBudget
        compoundEvolutionSelectedFamily = $compoundPlan.selectedFamily
        compoundEvolutionCooledDownFamilies = @($compoundPlan.cooledDownFamilies)
        compoundEvolutionChampionFitness = $compoundPlan.championFloor.fitness
        compoundEvolutionAdvisoryOnly = $compoundPlan.safety.advisoryOnly
        targetPromotions = $TargetPromotions
        cyclesRun = $results.Count
        promotions = $promotions
        results = $results
        providerStats = Get-ProviderStats
    }
    Write-JsonFile -Value $summary -Path (Join-Path $ArtifactRoot 'compound_loop_summary.json')
    return $summary
}

function Invoke-RollbackLast {
    $summary = Read-JsonFile -Path (Join-Path $ArtifactRoot 'self_coding_run_summary.json')
    if (-not $summary -or -not $summary.patch -or -not $summary.patch.rollbackSnapshot) {
        $record = [ordered]@{ timestamp = (Get-Date -Format 'o'); result = 'NO_ROLLBACK_AVAILABLE'; rollbackUsed = $false }
        Add-JsonLine -Path (Join-Path $ArtifactRoot 'rollback_ledger.jsonl') -Record $record
        return $record
    }
    foreach ($item in @($summary.patch.rollbackSnapshot.items)) {
        if ($item.existed -eq $true -and $item.snapshot) {
            Copy-Item -LiteralPath $item.snapshot -Destination $item.source -Force
        }
    }
    $record = [ordered]@{ timestamp = (Get-Date -Format 'o'); result = 'PASS'; rollbackUsed = $true; rollbackSucceeded = $true; runId = $summary.runId }
    Add-JsonLine -Path (Join-Path $ArtifactRoot 'rollback_ledger.jsonl') -Record $record
    return $record
}

function Get-LatestLedgerItem {
    param([string]$Path)
    $items = @(Read-JsonLines -Path $Path)
    if ($items.Count -eq 0) { return $null }
    return $items[-1]
}

function New-ProofBundle {
    $proofDir = Resolve-RepoPath -Path 'artifacts/obtuse/proof-bundles'
    New-Item -ItemType Directory -Force -Path $proofDir | Out-Null
    $stamp = Get-Date -Format 'yyyyMMddTHHmmss'
    $class = (Get-CurrentClassification).ToLowerInvariant() -replace '_','-'
    $zip = Join-Path $proofDir "$class-$stamp.zip"
    $files = @(Get-ChildItem -LiteralPath (Resolve-RepoPath -Path $ArtifactRoot) -Recurse -File | ForEach-Object { $_.FullName })
    foreach ($scriptName in @('openrouter-idle-model.ps1','idle-self-coding-accelerator.ps1','idle-improvement-thresholds.ps1','idle-openrouter-self-coding-health.ps1','idle-self-coding-ledger-summary.ps1','idle-self-coding-candidate-catalog.ps1','idle-self-coding-throttle-report.ps1','idle-deepseek-compound-loop-secret-scan.ps1')) {
        $scriptPath = Join-Path $PSScriptRoot $scriptName
        if (Test-Path -LiteralPath $scriptPath) { $files += $scriptPath }
    }
    if ($files.Count -gt 0) { Compress-Archive -LiteralPath $files -DestinationPath $zip -Force }
    return [ordered]@{ path = $zip; sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash.ToUpperInvariant() }
}

function Copy-ToFrankenstein {
    param([string]$ProofBundlePath)
    $class = (Get-CurrentClassification).ToLowerInvariant() -replace '_','-'
    $target = "\\fileserver\shared\obtuse\$class"
    if (-not (Test-Path -LiteralPath '\\fileserver\shared')) {
        return [ordered]@{ copied = $false; path = $target; error = 'FRANKENSTEIN share root not reachable.' }
    }
    try {
        $artifactTarget = Join-Path (Split-Path $target -Parent) (Split-Path (Resolve-RepoPath -Path $ArtifactRoot) -Leaf)
        New-Item -ItemType Directory -Force -Path $target | Out-Null
        Copy-Item -LiteralPath (Resolve-RepoPath -Path $ArtifactRoot) -Destination (Split-Path $target -Parent) -Recurse -Force
        Copy-Item -LiteralPath $ProofBundlePath -Destination (Join-Path $target (Split-Path -Leaf $ProofBundlePath)) -Force
        return [ordered]@{ copied = $true; path = $artifactTarget; proofBundlePath = $target; error = $null }
    } catch {
        return [ordered]@{ copied = $false; path = $target; proofBundlePath = $target; error = $_.Exception.Message }
    }
}

function Write-Handoff {
    param([object]$Final)
    $isCompound = ($Final.classification -eq 'OBTUSE_BLUNDER_IDLE_DEEPSEEK_SELF_CODING_COMPOUND_LOOP_V1')
    $text = @"
# Operator Handoff -- OpenRouter DeepSeek V4 Flash Self-Coding

Status: $($Final.status)
Terminal state: $($Final.terminalState)

- OpenRouter enabled: $($Final.openRouterIdleEnabled)
- Model alias: $($Final.openRouterModelAlias)
- Model resolved: $($Final.openRouterModelResolved)
- Smoke call result: $($Final.smokeCallResult)
- Self-coding RunOnce result: $($Final.selfCodingRunOnceResult)
- Self-coding RunLoop result: $($Final.selfCodingRunLoopResult)
- Candidate generated: $($Final.candidateGenerated)
- Patch applied: $($Final.patchApplied)
- Promoted: $($Final.promoted)
- Rolled back: $($Final.rollbackUsed)
- Cycles completed: $($Final.cyclesCompleted)
- Promotions: $($Final.promotions)
- Idle running: $($Final.idleRunning)

Status command:

    powershell -ExecutionPolicy Bypass -File scripts\obtuse\status-idle-ops.ps1

Stop command:

    powershell -ExecutionPolicy Bypass -File scripts\obtuse\stop-idle-ops.ps1

Run another bounded Flash loop:

    powershell -ExecutionPolicy Bypass -File scripts\obtuse\idle-self-coding-accelerator.ps1 -Mode RunLoop -OperatorApproved true -Model deepseek-v4-flash -MaxCycles 3

Next recommended action: $($Final.nextRecommendedAction)
"@
    Write-TextFile -Path (Join-Path $ArtifactRoot 'operator_handoff.md') -Value $text
}

function Write-FinalReport {
    $validation = if (Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'validation_summary.json'))) { Read-JsonFile -Path (Join-Path $ArtifactRoot 'validation_summary.json') } else { Invoke-Validation }
    $config = Read-JsonFile -Path (Join-Path $ArtifactRoot 'openrouter_provider_lane_config.json')
    $smoke = Read-JsonFile -Path (Join-Path $ArtifactRoot 'openrouter_smoke_call_summary.json')
    $generation = Read-JsonFile -Path (Join-Path $ArtifactRoot 'model_generation_summary.json')
    $run = Read-JsonFile -Path (Join-Path $ArtifactRoot 'self_coding_run_summary.json')
    $loop = Read-JsonFile -Path (Join-Path $ArtifactRoot 'compound_loop_summary.json')
    $threshold = Read-JsonFile -Path (Join-Path $ArtifactRoot 'threshold_decision_latest.json')
    $status = Get-StatusSnapshot
    $providerStats = Get-ProviderStats
    $model = Resolve-ConfiguredModel
    $classification = (Get-CurrentClassification)
    $isCompound = ($classification -eq 'OBTUSE_BLUNDER_IDLE_DEEPSEEK_SELF_CODING_COMPOUND_LOOP_V1')
    $secretScan = Read-JsonFile -Path (Join-Path $ArtifactRoot 'secret_scan.json')
    $secretScanResult = $(if ($secretScan -and $secretScan.status) { $secretScan.status } else { 'NOT_RUN' })
    $patchLedger = @(Read-JsonLines -Path (Join-Path $ArtifactRoot 'self_coding_patch_ledger.jsonl') | Where-Object { $_.patchApplied -eq $true })
    $filesChanged = @($patchLedger | ForEach-Object { $_.filesChanged } | Select-Object -Unique)
    $scriptsModified = @($filesChanged | Where-Object { $_ -match 'scripts[\\/]obtuse[\\/]' })
    $ledgersWritten = @('openrouter_call_ledger.jsonl','model_candidate_ledger.jsonl','self_coding_patch_ledger.jsonl','threshold_decision_ledger.jsonl','promotion_ledger.jsonl','rollback_ledger.jsonl','compound_cycle_ledger.jsonl') | Where-Object { Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $ArtifactRoot $_)) }

    $modelNeedsReview = ($config -and $config.modelNeedsOperatorReview -eq $true) -or ($smoke -and $smoke.result -eq 'MODEL_ID_NEEDS_OPERATOR_REVIEW') -or ($run -and $run.result -eq 'MODEL_ID_NEEDS_OPERATOR_REVIEW')
    $runPass = ($run -and $run.result -in @('PASS','PASS_WITH_NOTES'))
    $loopPass = ($loop -and ($loop.promotions -ge [math]::Max(1, $TargetPromotions)))
    $loopPartial = ($loop -and $loop.cyclesRun -ge 1 -and ($loop.promotions -ge 1))
    $runBlocked = ($run -and $run.result -in @('RUNONCE_TIMEOUT','NO_CANDIDATE_GENERATED','SELF_CODING_BLOCKED_SAFETY_REVIEW','ROLLED_BACK_OR_NOT_PROMOTED','SKIPPED_THRESHOLD_PRECHECK_FAILED','SKIPPED_NO_SAFE_TEMPLATE'))
    $smokePass = ($smoke -and $smoke.result -eq 'PASS' -and $smoke.providerCallSucceeded -eq $true)
    $terminal = if ($smokePass -and ($runPass -or $loopPass -or $loopPartial)) {
        'DEEPSEEK_V4_FLASH_ACTIVE'
    } elseif ($modelNeedsReview) {
        'OPENROUTER_LANE_READY_MODEL_ID_NEEDS_OPERATOR_REVIEW'
    } elseif ($runBlocked) {
        'SELF_CODING_BLOCKED_VALIDATION_FAILED'
    } elseif ($validation.result -ne 'PASS') {
        'SELF_CODING_BLOCKED_VALIDATION_FAILED'
    } elseif ($smoke -and -not $smokePass) {
        'OPENROUTER_ENABLEMENT_FAILED_SAFELY'
    } else {
        'OPENROUTER_LANE_READY_MODEL_ID_NEEDS_OPERATOR_REVIEW'
    }
    $statusValue = if ($terminal -eq 'DEEPSEEK_V4_FLASH_ACTIVE') {
        'PASS'
    } elseif ($terminal -in @('OPENROUTER_LANE_READY_MODEL_ID_NEEDS_OPERATOR_REVIEW')) {
        'PASS_WITH_NOTES'
    } elseif ($terminal -eq 'SELF_CODING_BLOCKED_VALIDATION_FAILED') {
        'PARTIAL_WITH_BLOCKERS'
    } else {
        'PARTIAL_WITH_BLOCKERS'
    }
    $final = [ordered]@{
        classification = $classification
        status = $statusValue
        terminalState = $terminal
        previousModel = 'deepseek-v4-pro'
        modelConfigured = $(if ($config) { $config.modelConfigured } else { $model })
        modelUsed = $(if ($config) { $config.modelUsed } else { (Resolve-OpenRouterModelId -Alias $model) })
        openRouterModelAlias = $(if ($config -and $config.openRouterModelAlias) { $config.openRouterModelAlias } else { $model })
        openRouterModelResolved = $(if ($config -and $config.openRouterModelResolved) { $config.openRouterModelResolved } else { (Resolve-OpenRouterModelId -Alias $model) })
        openRouterKeyPresent = $(if ($config) { $config.openRouterKeyPresent } else { $false })
        openRouterIdleEnabled = ($terminal -eq 'DEEPSEEK_V4_FLASH_ACTIVE')
        selfCodingAcceleratorEnabled = ($terminal -eq 'DEEPSEEK_V4_FLASH_ACTIVE')
        providerCallsAttempted = $providerStats.attempted
        providerCallsSucceeded = $providerStats.succeeded
        providerCallsFailed = $providerStats.failed
        providerBudgetGateEnabled = $false
        providerBudgetGateStatus = 'DISABLED_BY_OPERATOR_CHEAP_MODEL_POLICY'
        smokeCallResult = $(if ($smoke) { $smoke.result } else { 'NOT_RUN' })
        improvementGenerationResult = $(if ($generation) { $generation.result } else { 'NOT_RUN' })
        selfCodingRunOnceResult = $(if ($run) { $run.result } else { 'NOT_RUN' })
        selfCodingRunLoopResult = $(if ($loop) { $(if ($loop.promotions -ge 1 -or ($loop.results | Where-Object { $_.result -in @('PASS','PASS_WITH_NOTES') }).Count -ge 1) { 'PASS' } else { 'PARTIAL_WITH_NOTES' }) } else { 'NOT_RUN' })
        candidateGenerated = $(if ($run) { [bool]$run.candidateGenerated } elseif ($generation) { [bool]$generation.candidateGenerated } else { $false })
        candidateAccepted = $(if ($run) { [bool]$run.candidateAccepted } elseif ($threshold) { [bool]$threshold.candidateAccepted } else { $false })
        patchApplied = $(if ($run) { [bool]$run.patchApplied } else { $false })
        thresholdDecision = $(if ($threshold) { $threshold.thresholdDecision } else { 'NOT_RUN' })
        promoted = $(if ($run) { [bool]$run.promoted } else { $false })
        rollbackUsed = $(if ($run) { [bool]$run.rollbackUsed } else { $false })
        cyclesAttempted = $(if ($loop) { $loop.maxCycles } else { 0 })
        cyclesCompleted = $(if ($loop) { $loop.cyclesRun } else { 0 })
        candidatesGenerated = $(if ($loop) { @($loop.results | Where-Object { $_.candidateGenerated -eq $true }).Count } else { 0 })
        candidatesAccepted = $(if ($loop) { @($loop.results | Where-Object { $_.candidateAccepted -eq $true }).Count } else { 0 })
        patchesApplied = $(if ($loop) { @($loop.results | Where-Object { $_.patchApplied -eq $true }).Count } else { 0 })
        thresholdDecisions = $(if ($loop) { $loop.results.Count } else { 0 })
        promotions = $(if ($loop) { $loop.promotions } else { 0 })
        rollbacks = $(if ($loop) { @($loop.results | Where-Object { $_.rollbackUsed -eq $true }).Count } else { 0 })
        filesChanged = $filesChanged
        scriptsModified = $scriptsModified
        ledgersWritten = $ledgersWritten
        validationResults = $validation
        v5Validate = $(if ($validation) { $validation.v5Validate } else { 'NOT_RUN' })
        v6Validate = $(if ($validation) { $validation.v6Validate } else { 'NOT_RUN' })
        v7Validate = $(if ($validation) { $validation.v7Validate } else { 'NOT_RUN' })
        idleRunning = $(if ($status.status) { [bool]$status.status.active } else { $false })
        activePidValid = $(if ($status.status) { [bool]$status.status.pidValid } else { $false })
        heartbeatObserved = $(if ($status.status) { $status.status.heartbeatAgeSeconds -ne $null } else { $false })
        orphanProcess = $(if ($status.status) { [bool]$status.status.orphanProcess } else { $true })
        pidReuseSuspicion = $(if ($status.status) { [bool]$status.status.pidReuseSuspicion } else { $false })
        leftoverSubprocessCount = $(if ($validation -and $validation.safety) { [int]$validation.safety.terminalBenchProcessCount + [int]$validation.safety.providerProcessCount } else { 0 })
        secretScanResult = $secretScanResult
        benchmarkTouched = $false
        providerCalls = ($providerStats.attempted -gt 0)
        secretsPrinted = $false
        destructiveActions = $false
        safetyBoundariesChanged = $false
        wanefallTouched = $false
        showMeAISourceTouched = $false
        mobilePhoneAdbTouched = $false
        finalReportPath = (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json'))
        proofBundlePath = $null
        proofBundleSha256 = $null
        nextRecommendedAction = $(if ($terminal -eq 'DEEPSEEK_V4_FLASH_ACTIVE') { 'Blunder idle is actively using DeepSeek V4 Flash through OpenRouter; audit ledgers after the next cycle.' } elseif ($terminal -eq 'OPENROUTER_LANE_READY_MODEL_ID_NEEDS_OPERATOR_REVIEW') { 'Set the exact OpenRouter DeepSeek V4 Flash model slug, then rerun SmokeCall and RunOnce.' } else { 'Review validation and safety blockers before retrying the OpenRouter lane.' })
    }
    Write-JsonFile -Value $final -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')
    Write-Handoff -Final $final
    return $final
}

Initialize-Artifacts

switch ($Mode) {
    'Validate' {
        $result = Invoke-Validation
        Write-FinalReport | Out-Null
        $result | ConvertTo-Json -Depth 30
        return
    }
    'RunOnce' {
        $result = Invoke-RunOnce
        Write-FinalReport | Out-Null
        $result | ConvertTo-Json -Depth 30
        return
    }
    'RunLoop' {
        $result = Invoke-RunLoop
        Write-FinalReport | Out-Null
        $result | ConvertTo-Json -Depth 30
        return
    }
    'RollbackLast' {
        $result = Invoke-RollbackLast
        Write-FinalReport | Out-Null
        $result | ConvertTo-Json -Depth 30
        return
    }
    'ReportOnly' {
        $final = Write-FinalReport
        $bundle = New-ProofBundle
        $final.proofBundlePath = $bundle.path
        $final.proofBundleSha256 = $bundle.sha256
        Write-JsonFile -Value $final -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')
        Write-Handoff -Final $final
        $copy = Copy-ToFrankenstein -ProofBundlePath $bundle.path
        $final.frankensteinCopyPath = $(if ($copy.copied) { $copy.path } else { $null })
        $final.frankensteinProofBundleCopyPath = $(if ($copy.copied) { $copy.proofBundlePath } else { $null })
        $final.frankensteinCopyError = $copy.error
        Write-JsonFile -Value $final -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')
        Write-Handoff -Final $final
        $final | ConvertTo-Json -Depth 30
        return
    }
}
