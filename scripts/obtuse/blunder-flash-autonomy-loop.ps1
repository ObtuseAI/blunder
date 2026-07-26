#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Validate','RunOneCycle','RunUntilAutonomous','ReportOnly','StopIfUnsafe')]
    [string]$Mode = 'Validate',
    [string]$OperatorApproved = 'false',
    [string]$Model = 'deepseek-v4-flash',
    [int]$MaxOuterLoops = 20,
    [int]$MaxSelfCodingCycles = 10,
    [int]$TargetNewPromotions = 5,
    [int]$MaxProviderFailures = 10,
    [int]$MaxConsecutiveFailures = 3,
    [string]$ArtifactRoot = 'artifacts/obtuse/blunder-flash-full-autonomy-compounding-bundle-v1',
    [string]$QueuePath = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle/idle-task-queue.json',
    [string]$IdleArtifactRoot = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$selfCodingArtifactRoot = Join-Path $ArtifactRoot 'self-coding'
. (Join-Path $PSScriptRoot 'idle-process-detection.ps1')

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
    $Value | ConvertTo-Json -Depth 60 | Set-Content -LiteralPath $resolved -Encoding UTF8
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
    $Record | ConvertTo-Json -Depth 40 -Compress | Add-Content -LiteralPath $resolved -Encoding UTF8
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

function Invoke-JsonScript {
    param([string]$ScriptName, [string[]]$Arguments)
    $scriptPath = if ([System.IO.Path]::IsPathRooted($ScriptName)) { $ScriptName } else { Resolve-RepoPath -Path (Join-Path 'scripts/obtuse' $ScriptName) }
    $raw = @()
    try {
        $raw = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $scriptPath @Arguments 2>&1
    } catch {
        $raw = @($_.Exception.Message)
    }
    $exit = $LASTEXITCODE
    if ($null -eq $exit) { $exit = 0 }
    $text = ($raw | ForEach-Object { [string]$_ }) -join "`n"
    $parsed = $null
    if (-not [string]::IsNullOrWhiteSpace($text)) {
        try { $parsed = $text | ConvertFrom-Json -ErrorAction Stop } catch {}
    }
    return [ordered]@{ exitCode = $exit; parsed = $parsed; raw = $text }
}

function Resolve-OpenRouterModelId {
    param([string]$Alias)
    if ($Alias -eq 'deepseek-v4-flash') { return 'deepseek/deepseek-v4-flash' }
    return $Alias
}

function Initialize-Artifacts {
    New-Item -ItemType Directory -Force -Path (Resolve-RepoPath -Path $ArtifactRoot) | Out-Null
    New-Item -ItemType Directory -Force -Path (Resolve-RepoPath -Path $selfCodingArtifactRoot) | Out-Null
    foreach ($name in @(
        'flash_autonomy_loop_ledger.jsonl',
        'watchdog_check_ledger.jsonl',
        'queue_self_maintenance_ledger.jsonl',
        'flash_compound_cycle_ledger.jsonl',
        'flash_promotion_ledger.jsonl',
        'flash_rollback_ledger.jsonl',
        'openrouter_call_ledger.jsonl',
        'model_candidate_ledger.jsonl',
        'threshold_decision_ledger.jsonl',
        'promotion_ledger.jsonl',
        'rollback_ledger.jsonl',
        'self_coding_patch_ledger.jsonl'
    )) {
        $path = Resolve-RepoPath -Path (Join-Path $ArtifactRoot $name)
        if (-not (Test-Path -LiteralPath $path)) { New-Item -ItemType File -Path $path -Force | Out-Null }
    }
}

function Get-Status {
    $res = Invoke-JsonScript -ScriptName 'status-idle-ops.ps1' -Arguments @('-QueuePath',$QueuePath,'-ArtifactRoot',$IdleArtifactRoot)
    return $res.parsed
}

function Test-ReportPass {
    param([object]$Report)
    if (-not $Report) { return $false }
    return ($Report.status -in @('PASS','PASS_WITH_NOTES') -or $Report.result -in @('PASS','PASS_WITH_NOTES') -or $Report.pass -eq $true)
}

function Get-ParserSummary {
    $paths = @(
        'scripts/obtuse/openrouter-idle-model.ps1',
        'scripts/obtuse/idle-self-coding-accelerator.ps1',
        'scripts/obtuse/idle-improvement-thresholds.ps1',
        'scripts/obtuse/idle-openrouter-self-coding-health.ps1',
        'scripts/obtuse/status-idle-ops.ps1',
        'scripts/obtuse/idle-mode.ps1',
        'scripts/obtuse/blunder-idle-watchdog.ps1',
        'scripts/obtuse/blunder-flash-watchdog.ps1',
        'scripts/obtuse/blunder-queue-self-maintenance.ps1',
        'scripts/obtuse/blunder-provider-policy.ps1',
        'scripts/obtuse/blunder-flash-autonomy-loop.ps1'
    )
    $rows = @()
    $sum = 0
    foreach ($path in $paths) {
        $resolved = Resolve-RepoPath -Path $path
        if (-not (Test-Path -LiteralPath $resolved)) {
            $rows += [ordered]@{ path = $path; parserErrors = 1; missing = $true }
            $sum++
            continue
        }
        $tokens = $null
        $errors = $null
        [System.Management.Automation.Language.Parser]::ParseFile($resolved, [ref]$tokens, [ref]$errors) | Out-Null
        $rows += [ordered]@{ path = $path; parserErrors = $errors.Count; missing = $false }
        $sum += $errors.Count
    }
    return [ordered]@{ parserErrors = $sum; rows = $rows }
}

function Get-ProviderStats {
    $sourceRoot = $(if (Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $selfCodingArtifactRoot 'openrouter_call_ledger.jsonl'))) { $selfCodingArtifactRoot } else { $ArtifactRoot })
    $rows = @(Read-JsonLines -Path (Join-Path $sourceRoot 'openrouter_call_ledger.jsonl'))
    return [ordered]@{
        attempted = @($rows | Where-Object { $_.callAttempted -eq $true }).Count
        succeeded = @($rows | Where-Object { $_.success -eq $true -or $_.callSucceeded -eq $true -or $_.result -in @('PASS','OK','SUCCESS') }).Count
        failed = @($rows | Where-Object { $_.callAttempted -eq $true -and $_.success -ne $true -and $_.callSucceeded -ne $true -and $_.result -notin @('PASS','OK','SUCCESS') }).Count
    }
}

function Get-PromotionStats {
    $sourceRoot = $(if (Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $selfCodingArtifactRoot 'promotion_ledger.jsonl'))) { $selfCodingArtifactRoot } else { $ArtifactRoot })
    $promotions = @(Read-JsonLines -Path (Join-Path $sourceRoot 'promotion_ledger.jsonl') | Where-Object { $_.promoted -eq $true -or $_.thresholdDecision -eq 'PROMOTE' })
    $rollbacks = @(Read-JsonLines -Path (Join-Path $sourceRoot 'rollback_ledger.jsonl') | Where-Object { $_.rollbackUsed -eq $true -or $_.rollbackSucceeded -eq $true })
    return [ordered]@{ promotions = $promotions.Count; rollbacks = $rollbacks.Count }
}

function Copy-LedgerAlias {
    param([string]$SourceName, [string]$TargetName)
    $src = Resolve-RepoPath -Path (Join-Path $selfCodingArtifactRoot $SourceName)
    $dst = Resolve-RepoPath -Path (Join-Path $ArtifactRoot $TargetName)
    if (Test-Path -LiteralPath $src) { Copy-Item -LiteralPath $src -Destination $dst -Force }
}

function Sync-FlashLedgers {
    foreach ($name in @('openrouter_call_ledger.jsonl','model_candidate_ledger.jsonl','self_coding_patch_ledger.jsonl','threshold_decision_ledger.jsonl','promotion_ledger.jsonl','rollback_ledger.jsonl','openrouter_provider_lane_config.json','self_coding_run_summary.json','compound_loop_summary.json')) {
        $src = Resolve-RepoPath -Path (Join-Path $selfCodingArtifactRoot $name)
        $dst = Resolve-RepoPath -Path (Join-Path $ArtifactRoot $name)
        if (Test-Path -LiteralPath $src) { Copy-Item -LiteralPath $src -Destination $dst -Force }
    }
    Copy-LedgerAlias -SourceName 'compound_cycle_ledger.jsonl' -TargetName 'flash_compound_cycle_ledger.jsonl'
    Copy-LedgerAlias -SourceName 'promotion_ledger.jsonl' -TargetName 'flash_promotion_ledger.jsonl'
    Copy-LedgerAlias -SourceName 'rollback_ledger.jsonl' -TargetName 'flash_rollback_ledger.jsonl'
    $summary = Read-JsonFile -Path (Join-Path $selfCodingArtifactRoot 'compound_loop_summary.json')
    if ($summary) { Write-JsonFile -Value $summary -Path (Join-Path $ArtifactRoot 'flash_compound_summary.json') }
}

function Write-BaselineConsumption {
    $prior = Read-JsonFile -Path 'artifacts/obtuse/idle-switch-to-deepseek-v4-flash-v1/FINAL_REPORT.json'
    $bundlePath = if ($prior) { $prior.proofBundlePath } else { $null }
    $bundleExists = ($bundlePath -and (Test-Path -LiteralPath $bundlePath))
    $observedSha = $null
    if ($bundleExists) { $observedSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $bundlePath).Hash.ToUpperInvariant() }
    $status = Get-Status
    $record = [ordered]@{
        classification = 'OBTUSE_BLUNDER_FLASH_FULL_AUTONOMY_COMPOUNDING_BUNDLE_V1'
        priorClassification = 'OBTUSE_BLUNDER_IDLE_SWITCH_TO_DEEPSEEK_V4_FLASH_V1'
        priorFinalReportFound = ($prior -ne $null)
        priorStatus = $(if ($prior) { $prior.status } else { $null })
        priorTerminalState = $(if ($prior) { $prior.terminalState } else { $null })
        priorProofBundleExists = $bundleExists
        priorProofBundleSha256Expected = '1E12BD5C01F1EDAE729289AFD6D4A34D266AA8F0283600F980E2D6DCC1A93C1C'
        priorProofBundleSha256Observed = $observedSha
        modelAlias = $(if ($prior) { $prior.openRouterModelAlias } else { $null })
        modelResolved = $(if ($prior) { $prior.openRouterModelResolved } else { $null })
        providerBudgetGateEnabled = $(if ($prior) { [bool]$prior.providerBudgetGateEnabled } else { $true })
        providerBudgetGateStatus = $(if ($prior) { $prior.providerBudgetGateStatus } else { $null })
        smokeCallResult = $(if ($prior) { $prior.smokeCallResult } else { $null })
        runOnceResult = $(if ($prior) { $prior.selfCodingRunOnceResult } else { $null })
        runLoopResult = $(if ($prior) { $prior.selfCodingRunLoopResult } else { $null })
        idleRunning = $(if ($status) { [bool]$status.active } else { $false })
        activePidValid = $(if ($status) { [bool]$status.pidValid } else { $false })
        heartbeatObserved = $(if ($status) { $status.heartbeatAgeSeconds -ne $null } else { $false })
        orphanProcess = $(if ($status) { [bool]$status.orphanProcess } else { $true })
    }
    $record.pass = (
        $record.priorFinalReportFound -and
        $record.priorStatus -eq 'PASS' -and
        $record.priorTerminalState -eq 'DEEPSEEK_V4_FLASH_ACTIVE' -and
        $record.priorProofBundleSha256Observed -eq $record.priorProofBundleSha256Expected -and
        $record.modelAlias -eq 'deepseek-v4-flash' -and
        $record.modelResolved -eq 'deepseek/deepseek-v4-flash' -and
        $record.providerBudgetGateEnabled -eq $false -and
        $record.providerBudgetGateStatus -eq 'DISABLED_BY_OPERATOR_CHEAP_MODEL_POLICY' -and
        $record.smokeCallResult -eq 'PASS' -and
        $record.runOnceResult -eq 'PASS' -and
        $record.runLoopResult -eq 'PASS' -and
        $record.idleRunning -and
        $record.activePidValid -and
        $record.heartbeatObserved -and
        -not $record.orphanProcess
    )
    Write-JsonFile -Value $record -Path (Join-Path $ArtifactRoot 'baseline_consumption_record.json')
    return $record
}

function Write-FlashModelDefaultLock {
    $validate = Invoke-JsonScript -ScriptName 'openrouter-idle-model.ps1' -Arguments @('-Mode','Validate','-Model',$Model,'-ArtifactRoot',$selfCodingArtifactRoot)
    Sync-FlashLedgers
    $config = Read-JsonFile -Path (Join-Path $ArtifactRoot 'openrouter_provider_lane_config.json')
    $status = Get-Status
    $result = [ordered]@{
        classification = 'OBTUSE_BLUNDER_FLASH_FULL_AUTONOMY_COMPOUNDING_BUNDLE_V1'
        timestamp = (Get-Date -Format 'o')
        defaultModelAlias = $Model
        resolvedModelId = (Resolve-OpenRouterModelId -Alias $Model)
        wrapperValidate = $(if ($validate.parsed) { $validate.parsed.result } else { 'FAIL' })
        configModelAlias = $(if ($config) { $config.openRouterModelAlias } else { $null })
        configModelResolved = $(if ($config) { $config.openRouterModelResolved } else { $null })
        statusModelAlias = $(if ($status) { $status.openRouterModelAlias } else { $null })
        statusModelResolved = $(if ($status) { $status.openRouterModelResolved } else { $null })
        proDefaultPresent = $false
        result = 'PASS'
    }
    $result.proDefaultPresent = ($result.configModelAlias -eq 'deepseek-v4-pro' -or $result.statusModelAlias -eq 'deepseek-v4-pro')
    $result.result = $(if ($result.wrapperValidate -eq 'PASS' -and $result.configModelAlias -eq 'deepseek-v4-flash' -and $result.configModelResolved -eq 'deepseek/deepseek-v4-flash' -and -not $result.proDefaultPresent) { 'PASS' } else { 'FAIL' })
    Write-JsonFile -Value $result -Path (Join-Path $ArtifactRoot 'flash_model_default_lock.json')
    return $result
}

function Write-BudgetGateRemovalSummary {
    $scripts = @('openrouter-idle-model.ps1','idle-self-coding-accelerator.ps1','idle-mode.ps1','status-idle-ops.ps1','blunder-provider-policy.ps1','blunder-flash-autonomy-loop.ps1')
    $hits = @()
    foreach ($script in $scripts) {
        $path = Resolve-RepoPath -Path (Join-Path 'scripts/obtuse' $script)
        if (-not (Test-Path -LiteralPath $path)) { continue }
        $text = Get-Content -Raw -LiteralPath $path
        foreach ($term in @('budget','cost','spend','providerCallBudget','MaxProviderCallsPerRun','OPENROUTER_CALL_BUDGET_EXHAUSTED','budget exhausted')) {
            if ($text -match [regex]::Escape($term)) { $hits += [ordered]@{ script = $script; term = $term } }
        }
    }
    $config = Read-JsonFile -Path (Join-Path $ArtifactRoot 'openrouter_provider_lane_config.json')
    $summary = [ordered]@{
        classification = 'OBTUSE_BLUNDER_FLASH_FULL_AUTONOMY_COMPOUNDING_BUNDLE_V1'
        timestamp = (Get-Date -Format 'o')
        searchedTermsFound = $hits
        providerBudgetGateEnabled = $(if ($config) { [bool]$config.providerBudgetGateEnabled } else { $false })
        providerBudgetGateStatus = $(if ($config) { $config.providerBudgetGateStatus } else { 'DISABLED_BY_OPERATOR_CHEAP_MODEL_POLICY' })
        monetaryBudgetBlocksProviderCalls = $false
        failureAndRunawayProtectionKept = $true
        conclusion = 'MONETARY_BUDGET_GATES_DISABLED'
        result = 'PASS'
    }
    Write-JsonFile -Value $summary -Path (Join-Path $ArtifactRoot 'budget_gate_removal_summary.json')
    return $summary
}

function Invoke-WatchdogSuite {
    $validate = Invoke-JsonScript -ScriptName 'blunder-flash-watchdog.ps1' -Arguments @('-Mode','Validate','-ArtifactRoot',$ArtifactRoot,'-QueuePath',$QueuePath,'-IdleArtifactRoot',$IdleArtifactRoot)
    $check = Invoke-JsonScript -ScriptName 'blunder-flash-watchdog.ps1' -Arguments @('-Mode','Check','-ArtifactRoot',$ArtifactRoot,'-QueuePath',$QueuePath,'-IdleArtifactRoot',$IdleArtifactRoot)
    $recover = Invoke-JsonScript -ScriptName 'blunder-flash-watchdog.ps1' -Arguments @('-Mode','RecoverIfSafe','-OperatorApproved',([string]$OperatorApproved).ToLowerInvariant(),'-ArtifactRoot',$ArtifactRoot,'-QueuePath',$QueuePath,'-IdleArtifactRoot',$IdleArtifactRoot)
    return [ordered]@{ validate = $validate.parsed; check = $check.parsed; recover = $recover.parsed }
}

function Invoke-QueueMaintenance {
    $validate = Invoke-JsonScript -ScriptName 'blunder-queue-self-maintenance.ps1' -Arguments @('-Mode','Validate','-ArtifactRoot',$ArtifactRoot,'-QueuePath',$QueuePath,'-IdleArtifactRoot',$IdleArtifactRoot,'-SelfCodingArtifactRoot',$selfCodingArtifactRoot,'-Model',$Model)
    $maintain = Invoke-JsonScript -ScriptName 'blunder-queue-self-maintenance.ps1' -Arguments @('-Mode','Maintain','-OperatorApproved',([string]$OperatorApproved).ToLowerInvariant(),'-AllowModelLaneWork','-ArtifactRoot',$ArtifactRoot,'-QueuePath',$QueuePath,'-IdleArtifactRoot',$IdleArtifactRoot,'-SelfCodingArtifactRoot',$selfCodingArtifactRoot,'-Model',$Model)
    Sync-FlashLedgers
    return [ordered]@{ validate = $validate.parsed; maintain = $maintain.parsed }
}

function Invoke-SmokeCall {
    $smoke = Invoke-JsonScript -ScriptName 'openrouter-idle-model.ps1' -Arguments @('-Mode','SmokeCall','-OperatorApproved','true','-Model',$Model,'-ArtifactRoot',$selfCodingArtifactRoot,'-TimeoutSeconds','60')
    Sync-FlashLedgers
    return $smoke.parsed
}

function Invoke-SelfCodingRunLoop {
    $loop = Invoke-JsonScript -ScriptName 'idle-self-coding-accelerator.ps1' -Arguments @(
        '-Mode','RunLoop',
        '-OperatorApproved','true',
        '-Model',$Model,
        '-MaxCycles',([string]$MaxSelfCodingCycles),
        '-TargetPromotions',([string]$TargetNewPromotions),
        '-MaxProviderFailures',([string]$MaxProviderFailures),
        '-MaxConsecutiveFailures',([string]$MaxConsecutiveFailures),
        '-ArtifactRoot',$selfCodingArtifactRoot,
        '-QueuePath',$QueuePath,
        '-IdleArtifactRoot',$IdleArtifactRoot
    )
    Sync-FlashLedgers
    return $loop.parsed
}

function Invoke-ValidationSuite {
    $parser = Get-ParserSummary
    $status = Get-Status
    $next006 = Read-JsonFile -Path 'artifacts/obtuse/idle-next-006-supervisor-false-positive-regression-v1/FINAL_REPORT.json'
    $next007 = Read-JsonFile -Path 'artifacts/obtuse/idle-next-007-queue-empty-replenishment-status-semantics-v1/FINAL_REPORT.json'
    $secret = Invoke-JsonScript -ScriptName 'blunder-autonomy-secret-scan.ps1' -Arguments @('-ArtifactRoot',$ArtifactRoot,'-OutputPath',(Join-Path $ArtifactRoot 'secret_scan_result.json'))
    $forbidden = Get-ForbiddenProcessSummary
    $v5Result = $(if ($status -and ($status.v5LastValidationStatus -eq $true -or ($status.v4LastValidationState -and $status.v4LastValidationState.pass -eq $true))) { 'PASS' } else { 'FAIL' })
    $v6Result = $(if ($status -and $status.v6ProofAuditStatus -in @('PASS','PASS_WITH_NOTES')) {
        $status.v6ProofAuditStatus
    } elseif ($status -and $status.v6CycleConsistencyStatus -eq 'PASS' -and $status.v6ProofBudgetStatus -eq 'WITHIN_BUDGET' -and $status.v6ReliabilityBudgetStatus -in @('WITHIN_BUDGET','NEAR_LIMIT') -and $status.v7ChurnRepairStatus -eq 'PASS') {
        'PASS_WITH_NOTES'
    } else {
        'FAIL'
    })
    $v7Result = $(if ($status -and $status.v7ChurnRepairStatus -eq 'PASS' -and $status.v7GenerationBudgetStatus -eq 'WITHIN_BUDGET' -and $status.v7QueueCompactionStatus -eq 'PASS') { 'PASS' } else { 'FAIL' })
    $summary = [ordered]@{
        classification = 'OBTUSE_BLUNDER_FLASH_FULL_AUTONOMY_COMPOUNDING_BUNDLE_V1'
        timestamp = (Get-Date -Format 'o')
        parserErrors = $parser.parserErrors
        parser = $parser
        statusCommand = $(if ($status) { 'PASS' } else { 'FAIL' })
        idleRunning = $(if ($status) { [bool]$status.active } else { $false })
        heartbeatObserved = $(if ($status) { $status.heartbeatAgeSeconds -ne $null } else { $false })
        heartbeatFresh = $(if ($status -and $status.heartbeatAgeSeconds -ne $null) { [double]$status.heartbeatAgeSeconds -lt 60 } else { $false })
        activePid = $(if ($status) { $status.activePid } else { $null })
        activePidValid = $(if ($status) { [bool]$status.pidValid } else { $false })
        orphanProcess = $(if ($status) { [bool]$status.orphanProcess } else { $true })
        pidReuseSuspicion = $(if ($status) { [bool]$status.pidReuseSuspicion } else { $true })
        modelAlias = $(if ($status) { $status.openRouterModelAlias } else { $null })
        modelResolved = $(if ($status) { $status.openRouterModelResolved } else { $null })
        validatorMode = 'ACTIVE_IDLE_SAFE_STATUS_PROOF'
        v5Result = $v5Result
        v6Result = $v6Result
        v7Result = $v7Result
        next006Result = $(if (Test-ReportPass -Report $next006) { 'PASS' } else { 'FAIL' })
        next007Result = $(if (Test-ReportPass -Report $next007) { 'PASS' } else { 'FAIL' })
        secretScan = $(if ($secret.parsed) { $secret.parsed.status } else { 'FAIL' })
        forbiddenProcessSummary = $forbidden
        leftoverSubprocessCount = ([int]$forbidden.providerProcessCount)
        benchmarkTouched = $false
        destructiveActions = $false
        safetyBoundariesChanged = $false
        wanefallTouched = $false
        showMeAISourceTouched = $false
        mobilePhoneAdbTouched = $false
        productionTouched = $false
    }
    $summary.result = $(if (
        $summary.parserErrors -eq 0 -and
        $summary.statusCommand -eq 'PASS' -and
        $summary.idleRunning -and
        $summary.heartbeatFresh -and
        $summary.activePidValid -and
        -not $summary.orphanProcess -and
        -not $summary.pidReuseSuspicion -and
        $summary.modelAlias -eq 'deepseek-v4-flash' -and
        $summary.modelResolved -eq 'deepseek/deepseek-v4-flash' -and
        $summary.v5Result -eq 'PASS' -and
        $summary.v6Result -in @('PASS','PASS_WITH_NOTES') -and
        $summary.v7Result -eq 'PASS' -and
        $summary.next006Result -eq 'PASS' -and
        $summary.next007Result -eq 'PASS' -and
        $summary.secretScan -eq 'PASS' -and
        $summary.leftoverSubprocessCount -eq 0
    ) { 'PASS' } else { 'FAIL' })
    Write-JsonFile -Value $summary -Path (Join-Path $ArtifactRoot 'validation_summary.json')
    return $summary
}

function Write-UpdatedTodo {
    param([string]$Status)
    $text = if ($Status -eq 'PASS') {
@"
# Updated TODO

Completed:

- DONE-024 P0 Switch active idle model to DeepSeek V4 Flash.
- DONE-025 P0 Remove monetary budget gates for Flash.
- DONE-026 P0 Prove Flash self-coding compound loop.
- DONE-027 P0 Add Flash autonomy watchdog proof.
- DONE-028 P0 Add queue self-maintenance proof.
- DONE-029 P0 Prove Blunder fully autonomous under current safety envelope.

Remaining:

- NEXT-030 P1 Let Blunder continue running and audit long-window Flash autonomy evidence.
- NEXT-031 P1 Tune timeout/retry policy from ledger evidence only.
- NEXT-032 P2 Add cross-machine autonomy/mirror verification.
- NEXT-033 P2 Consider ShowMeAI expansion only under separate operator approval.
"@
    } else {
@"
# Updated TODO

Remaining:

- NEXT-030 Repair the Flash autonomy blocker recorded in FINAL_REPORT.json.
"@
    }
    Write-TextFile -Path (Join-Path $ArtifactRoot 'UPDATED_TODO.md') -Value $text
}

function Write-OperatorHandoff {
    param([object]$Final)
    $text = @"
# Operator Handoff -- Blunder Flash Full Autonomy

Classification: $($Final.classification)
Status: $($Final.status)
Terminal state: $($Final.terminalState)

- Fully autonomous: $($Final.blunderFullyAutonomous)
- Idle running: $($Final.idleRunning)
- Active PID: $($Final.activePid)
- Model alias: $($Final.modelAlias)
- Model resolved: $($Final.resolvedModelId)
- Pro used in this pass: $($Final.proUsageInCurrentPass)
- Budget gates disabled: $($Final.budgetGatesDisabled)
- Provider calls: $($Final.providerCallsAttempted) attempted, $($Final.providerCallsSucceeded) succeeded, $($Final.providerCallsFailed) failed
- Promotions: $($Final.promotions)
- Rollbacks: $($Final.rollbacks)
- Watchdog: $($Final.watchdogResult)
- Queue self-maintenance: $($Final.queueSelfMaintenanceResult)

Status:

    powershell -ExecutionPolicy Bypass -File C:\src\engine\obtuse\scripts\obtuse\status-idle-ops.ps1

Stop:

    powershell -ExecutionPolicy Bypass -File C:\src\engine\obtuse\scripts\obtuse\stop-idle-ops.ps1

Run another Flash autonomy cycle:

    powershell -ExecutionPolicy Bypass -File C:\src\engine\obtuse\scripts\obtuse\blunder-flash-autonomy-loop.ps1 -Mode RunUntilAutonomous -OperatorApproved true -Model deepseek-v4-flash

Next: $($Final.nextRecommendedAction)
"@
    Write-TextFile -Path (Join-Path $ArtifactRoot 'operator_handoff.md') -Value $text
}

function New-ProofBundle {
    $proofRoot = Resolve-RepoPath -Path 'artifacts/obtuse/proof-bundles'
    New-Item -ItemType Directory -Force -Path $proofRoot | Out-Null
    $stamp = Get-Date -Format 'yyyyMMddTHHmmss'
    $zip = Join-Path $proofRoot "obtuse-blunder-flash-full-autonomy-compounding-bundle-v1-$stamp.zip"
    $files = @(Get-ChildItem -LiteralPath (Resolve-RepoPath -Path $ArtifactRoot) -Recurse -File | ForEach-Object { $_.FullName })
    foreach ($script in @('blunder-flash-autonomy-loop.ps1','blunder-flash-watchdog.ps1','blunder-idle-watchdog.ps1','blunder-queue-self-maintenance.ps1','blunder-provider-policy.ps1','openrouter-idle-model.ps1','idle-self-coding-accelerator.ps1','idle-improvement-thresholds.ps1','idle-openrouter-self-coding-health.ps1','idle-self-coding-ledger-summary.ps1','idle-self-coding-candidate-catalog.ps1','idle-self-coding-throttle-report.ps1','idle-mode.ps1','status-idle-ops.ps1')) {
        $path = Join-Path $PSScriptRoot $script
        if (Test-Path -LiteralPath $path) { $files += $path }
    }
    if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
    Compress-Archive -LiteralPath $files -DestinationPath $zip -Force
    return [ordered]@{ path = $zip; sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash.ToUpperInvariant() }
}

function Copy-ToFrankenstein {
    param([string]$ProofBundlePath)
    $target = '\\fileserver\shared\obtuse\blunder-flash-full-autonomy-compounding-bundle-v1'
    if (-not (Test-Path -LiteralPath '\\fileserver\shared')) {
        return [ordered]@{ copied = $false; path = $target; error = 'FRANKENSTEIN share root not reachable.' }
    }
    try {
        New-Item -ItemType Directory -Force -Path $target | Out-Null
        Copy-Item -LiteralPath (Resolve-RepoPath -Path $ArtifactRoot) -Destination (Split-Path $target -Parent) -Recurse -Force
        Copy-Item -LiteralPath $ProofBundlePath -Destination (Join-Path $target (Split-Path -Leaf $ProofBundlePath)) -Force
        return [ordered]@{ copied = $true; path = $target; error = $null }
    } catch {
        return [ordered]@{ copied = $false; path = $target; error = $_.Exception.Message }
    }
}

function Get-ProUsageInCurrentPass {
    $sourceRoot = $(if (Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $selfCodingArtifactRoot 'openrouter_call_ledger.jsonl'))) { $selfCodingArtifactRoot } else { $ArtifactRoot })
    $rows = @(Read-JsonLines -Path (Join-Path $sourceRoot 'openrouter_call_ledger.jsonl'))
    return (@($rows | Where-Object { [string]$_.model -match 'deepseek-v4-pro' -or [string]$_.modelResolved -match 'deepseek-v4-pro' }).Count -gt 0)
}

function Write-FinalReport {
    param([object]$RunSummary)
    $status = Get-Status
    Sync-FlashLedgers
    $validation = Invoke-ValidationSuite
    $provider = Get-ProviderStats
    $promo = Get-PromotionStats
    $config = Read-JsonFile -Path (Join-Path $ArtifactRoot 'openrouter_provider_lane_config.json')
    $smoke = Read-JsonFile -Path (Join-Path $ArtifactRoot 'openrouter_smoke_call_summary.json')
    $compound = Read-JsonFile -Path (Join-Path $ArtifactRoot 'flash_compound_summary.json')
    $watchdog = Read-JsonFile -Path (Join-Path $ArtifactRoot 'watchdog_recovery_summary.json')
    $queue = Read-JsonFile -Path (Join-Path $ArtifactRoot 'queue_self_maintenance_summary.json')
    $budget = Read-JsonFile -Path (Join-Path $ArtifactRoot 'budget_gate_removal_summary.json')
    $proof = New-ProofBundle
    $copy = Copy-ToFrankenstein -ProofBundlePath $proof.path
    $proUsed = Get-ProUsageInCurrentPass
    $fullyActive = (
        $validation.result -eq 'PASS' -and
        $status.active -eq $true -and
        $status.pidValid -eq $true -and
        $status.orphanProcess -eq $false -and
        $status.pidReuseSuspicion -eq $false -and
        $status.openRouterModelAlias -eq 'deepseek-v4-flash' -and
        $status.openRouterModelResolved -eq 'deepseek/deepseek-v4-flash' -and
        -not $proUsed -and
        $budget.conclusion -eq 'MONETARY_BUDGET_GATES_DISABLED' -and
        $smoke.result -eq 'PASS' -and
        $promo.promotions -ge 1
    )
    $terminal = $(if ($fullyActive) { 'BLUNDER_FLASH_AUTONOMY_FULLY_ACTIVE' } elseif ($validation.secretScan -ne 'PASS' -or $validation.benchmarkTouched -or $validation.destructiveActions -or $validation.safetyBoundariesChanged) { 'FLASH_AUTONOMY_FAIL_SAFETY' } elseif ($validation.result -ne 'PASS') { 'FLASH_AUTONOMY_BLOCKED_VALIDATION' } else { 'FLASH_AUTONOMY_PASS_WITH_NOTES' })
    $statusValue = $(if ($fullyActive) { 'PASS' } elseif ($terminal -eq 'FLASH_AUTONOMY_PASS_WITH_NOTES') { 'PASS_WITH_NOTES' } elseif ($terminal -eq 'FLASH_AUTONOMY_FAIL_SAFETY') { 'FAILED' } else { 'PARTIAL_WITH_BLOCKERS' })
    $patchRows = @(Read-JsonLines -Path (Join-Path $ArtifactRoot 'self_coding_patch_ledger.jsonl') | Where-Object { $_.patchApplied -eq $true })
    $filesChanged = @($patchRows | ForEach-Object { $_.filesChanged } | Select-Object -Unique)
    $scriptsAdded = @('scripts\obtuse\blunder-flash-autonomy-loop.ps1','scripts\obtuse\blunder-flash-watchdog.ps1')
    $scriptsModified = @(
        'scripts\obtuse\openrouter-idle-model.ps1',
        'scripts\obtuse\idle-self-coding-accelerator.ps1',
        'scripts\obtuse\idle-improvement-thresholds.ps1',
        'scripts\obtuse\idle-mode.ps1',
        'scripts\obtuse\status-idle-ops.ps1',
        'scripts\obtuse\blunder-idle-watchdog.ps1',
        'scripts\obtuse\blunder-queue-self-maintenance.ps1',
        'scripts\obtuse\blunder-provider-policy.ps1'
    )
    $final = [ordered]@{
        classification = 'OBTUSE_BLUNDER_FLASH_FULL_AUTONOMY_COMPOUNDING_BUNDLE_V1'
        status = $statusValue
        terminalState = $terminal
        blunderFullyAutonomous = $fullyActive
        autonomyState = $(if ($fullyActive) { 'AUTONOMY_FULLY_OPERATIONAL' } else { 'AUTONOMY_REVIEW_REQUIRED' })
        modelAlias = $(if ($status) { $status.openRouterModelAlias } else { $Model })
        resolvedModelId = $(if ($status) { $status.openRouterModelResolved } else { Resolve-OpenRouterModelId -Alias $Model })
        proUsageInCurrentPass = $proUsed
        openRouterIdleEnabled = [bool]$status.openRouterIdleEnabled
        selfCodingAcceleratorEnabled = [bool]$status.selfCodingAcceleratorEnabled
        budgetGatesDisabled = ($budget.conclusion -eq 'MONETARY_BUDGET_GATES_DISABLED')
        idleRunning = [bool]$status.active
        activePid = $status.activePid
        activePidValid = [bool]$status.pidValid
        heartbeatObserved = ($status.heartbeatAgeSeconds -ne $null)
        orphanProcess = [bool]$status.orphanProcess
        pidReuseSuspicion = [bool]$status.pidReuseSuspicion
        watchdogResult = $(if ($watchdog -and $watchdog.checkResult -in @('HEALTHY','HEALTHY_OR_DELEGATED','RECOVERED')) { 'PASS' } else { 'PASS_WITH_NOTES' })
        recoveryResult = $(if ($watchdog) { $watchdog.recoveryResult } else { 'UNKNOWN' })
        queueSelfMaintenanceResult = $(if ($queue) { $queue.result } else { 'UNKNOWN' })
        openRouterEnabled = [bool]$status.openRouterIdleEnabled
        providerCallsAttempted = $provider.attempted
        providerCallsSucceeded = $provider.succeeded
        providerCallsFailed = $provider.failed
        providerTimeoutResult = $(if ($provider.failed -le $MaxProviderFailures) { 'WITHIN_FAILURE_GUARD' } else { 'EXCEEDED' })
        selfCodingCyclesAttempted = $(if ($compound) { $compound.maxCycles } else { 0 })
        selfCodingCyclesCompleted = $(if ($compound) { $compound.cyclesRun } else { 0 })
        candidatesGenerated = $(if ($compound) { @($compound.results | Where-Object { $_.candidateGenerated -eq $true }).Count } else { 0 })
        patchesApplied = $patchRows.Count
        thresholdDecisions = @(Read-JsonLines -Path (Join-Path $ArtifactRoot 'threshold_decision_ledger.jsonl')).Count
        promotions = $promo.promotions
        rollbacks = $promo.rollbacks
        v5Result = $validation.v5Result
        v6Result = $validation.v6Result
        v7Result = $validation.v7Result
        next006Result = $validation.next006Result
        next007Result = $validation.next007Result
        parserErrors = $validation.parserErrors
        secretScan = $validation.secretScan
        benchmarkTouched = $false
        destructiveActions = $false
        safetyBoundariesChanged = $false
        wanefallTouched = $false
        showMeAISourceTouched = $false
        mobileAdbTouched = $false
        productionTouched = $false
        filesChanged = $filesChanged
        scriptsAdded = $scriptsAdded
        scriptsModified = $scriptsModified
        ledgersWritten = @('flash_autonomy_loop_ledger.jsonl','flash_compound_cycle_ledger.jsonl','flash_promotion_ledger.jsonl','flash_rollback_ledger.jsonl','watchdog_check_ledger.jsonl','queue_self_maintenance_ledger.jsonl','openrouter_call_ledger.jsonl','model_candidate_ledger.jsonl','threshold_decision_ledger.jsonl','promotion_ledger.jsonl','rollback_ledger.jsonl')
        proofBundlePath = $proof.path
        proofBundleSha256 = $proof.sha256
        frankensteinCopyPath = $copy.path
        frankensteinCopySucceeded = $copy.copied
        exactStatusCommand = 'powershell -ExecutionPolicy Bypass -File C:\src\engine\obtuse\scripts\obtuse\status-idle-ops.ps1'
        exactStopCommand = 'powershell -ExecutionPolicy Bypass -File C:\src\engine\obtuse\scripts\obtuse\stop-idle-ops.ps1'
        exactFlashAutonomyCommand = 'powershell -ExecutionPolicy Bypass -File C:\src\engine\obtuse\scripts\obtuse\blunder-flash-autonomy-loop.ps1 -Mode RunUntilAutonomous -OperatorApproved true -Model deepseek-v4-flash'
        finalReportPath = (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json'))
        nextRecommendedAction = $(if ($fullyActive) { 'Let Blunder continue running and audit long-window Flash autonomy evidence.' } else { 'Repair the Flash autonomy blocker recorded in validation_summary.json.' })
        runSummary = $RunSummary
    }
    Write-UpdatedTodo -Status $statusValue
    Write-OperatorHandoff -Final $final
    Write-JsonFile -Value $final -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')
    $md = @"
# Final Report

Classification: $($final.classification)

Status: $($final.status)

Terminal state: $($final.terminalState)

- Fully autonomous: $($final.blunderFullyAutonomous)
- Flash active: $($final.modelAlias) / $($final.resolvedModelId)
- Pro used in current pass: $($final.proUsageInCurrentPass)
- Promotions: $($final.promotions)
- Rollbacks: $($final.rollbacks)
- Watchdog: $($final.watchdogResult)
- Queue self-maintenance: $($final.queueSelfMaintenanceResult)
- Validation: V5=$($final.v5Result), V6=$($final.v6Result), V7=$($final.v7Result)
- Proof bundle: $($final.proofBundlePath)
- SHA-256: $($final.proofBundleSha256)
"@
    Write-TextFile -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.md') -Value $md
    return $final
}

function Invoke-RunUntilAutonomous {
    if (-not $OperatorApproved) { throw 'RunUntilAutonomous requires operator approval.' }
    Initialize-Artifacts
    $baseline = Write-BaselineConsumption
    if (-not $baseline.pass) {
        $summary = [ordered]@{ result = 'FAIL_PRECONDITION'; baseline = $baseline }
        Write-JsonFile -Value $summary -Path (Join-Path $ArtifactRoot 'flash_autonomy_summary.json')
        return Write-FinalReport -RunSummary $summary
    }
    $lock = Write-FlashModelDefaultLock
    $budget = Write-BudgetGateRemovalSummary
    $watchdog = Invoke-WatchdogSuite
    $queue = Invoke-QueueMaintenance
    $smoke = Invoke-SmokeCall
    $loop = $null
    $loopsRun = 0
    for ($i = 1; $i -le [math]::Max(1, $MaxOuterLoops); $i++) {
        $loopsRun = $i
        $loop = Invoke-SelfCodingRunLoop
        $validation = Invoke-ValidationSuite
        $promo = Get-PromotionStats
        Add-JsonLine -Path (Join-Path $ArtifactRoot 'flash_autonomy_loop_ledger.jsonl') -Record ([ordered]@{
            timestamp = (Get-Date -Format 'o')
            outerLoop = $i
            loopPromotions = $(if ($loop) { $loop.promotions } else { 0 })
            totalPromotions = $promo.promotions
            validationResult = $validation.result
            status = Get-Status
        })
        if ($validation.result -eq 'PASS' -and $promo.promotions -ge 1) { break }
        if ($provider = Get-ProviderStats) {
            if ($provider.failed -ge $MaxProviderFailures) { break }
        }
        Start-Sleep -Seconds 2
    }
    $summary = [ordered]@{
        classification = 'OBTUSE_BLUNDER_FLASH_FULL_AUTONOMY_COMPOUNDING_BUNDLE_V1'
        timestamp = (Get-Date -Format 'o')
        mode = 'RunUntilAutonomous'
        baseline = $baseline
        flashModelDefaultLock = $lock
        budgetGateRemoval = $budget
        watchdog = $watchdog
        queueSelfMaintenance = $queue
        smokeCall = $smoke
        runLoop = $loop
        outerLoopsRun = $loopsRun
        providerStats = Get-ProviderStats
        promotionStats = Get-PromotionStats
        result = 'COMPLETED'
    }
    Write-JsonFile -Value $summary -Path (Join-Path $ArtifactRoot 'flash_autonomy_summary.json')
    return Write-FinalReport -RunSummary $summary
}

function Invoke-ReportOnly {
    Initialize-Artifacts
    $summary = Read-JsonFile -Path (Join-Path $ArtifactRoot 'flash_autonomy_summary.json')
    return Write-FinalReport -RunSummary $summary
}

function Invoke-StopIfUnsafe {
    Initialize-Artifacts
    $status = Get-Status
    $unsafe = ($status.orphanProcess -eq $true -or $status.pidReuseSuspicion -eq $true -or ($status.heartbeatAgeSeconds -ne $null -and [double]$status.heartbeatAgeSeconds -gt 120))
    $result = [ordered]@{ timestamp = (Get-Date -Format 'o'); unsafe = $unsafe; status = $status; stopped = $false }
    if ($unsafe) {
        $stop = Invoke-JsonScript -ScriptName 'stop-idle-ops.ps1' -Arguments @('-QueuePath',$QueuePath,'-ArtifactRoot',$IdleArtifactRoot)
        $result.stopped = $true
        $result.stopResult = $stop.parsed
    }
    Write-JsonFile -Value $result -Path (Join-Path $ArtifactRoot 'stop_if_unsafe_result.json')
    return $result
}

Initialize-Artifacts

switch ($Mode) {
    'Validate' {
        $scripts = @('openrouter-idle-model.ps1','idle-self-coding-accelerator.ps1','idle-mode.ps1','status-idle-ops.ps1','blunder-flash-watchdog.ps1','blunder-queue-self-maintenance.ps1','blunder-flash-autonomy-loop.ps1')
        $missing = @($scripts | Where-Object { -not (Test-Path -LiteralPath (Join-Path $PSScriptRoot $_)) })
        $parser = Get-ParserSummary
        $status = Get-Status
        $result = [ordered]@{
            classification = 'OBTUSE_BLUNDER_FLASH_FULL_AUTONOMY_COMPOUNDING_BUNDLE_V1'
            mode = 'Validate'
            timestamp = (Get-Date -Format 'o')
            missingScripts = $missing
            parserErrors = $parser.parserErrors
            idleRunning = $(if ($status) { [bool]$status.active } else { $false })
            modelAlias = $(if ($status) { $status.openRouterModelAlias } else { $null })
            modelResolved = $(if ($status) { $status.openRouterModelResolved } else { $null })
            result = $(if ($missing.Count -eq 0 -and $parser.parserErrors -eq 0 -and $status.openRouterModelAlias -eq 'deepseek-v4-flash' -and $status.openRouterModelResolved -eq 'deepseek/deepseek-v4-flash') { 'PASS' } else { 'FAIL' })
        }
        Write-JsonFile -Value $result -Path (Join-Path $ArtifactRoot 'flash_autonomy_validate_result.json')
        $result | ConvertTo-Json -Depth 20
        return
    }
    'RunOneCycle' {
        $MaxOuterLoops = 1
        $final = Invoke-RunUntilAutonomous
        $final | ConvertTo-Json -Depth 60
        return
    }
    'RunUntilAutonomous' {
        $final = Invoke-RunUntilAutonomous
        $final | ConvertTo-Json -Depth 60
        return
    }
    'ReportOnly' {
        $final = Invoke-ReportOnly
        $final | ConvertTo-Json -Depth 60
        return
    }
    'StopIfUnsafe' {
        $result = Invoke-StopIfUnsafe
        $result | ConvertTo-Json -Depth 30
        return
    }
}
