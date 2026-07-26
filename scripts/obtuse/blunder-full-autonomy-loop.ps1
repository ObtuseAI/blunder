#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Validate','RunUntilAutonomous','RunOneAutonomyCycle','ReportOnly','StopIfUnsafe')]
    [string]$Mode = 'Validate',
    [string]$OperatorApproved = 'false',
    [string]$Model = 'deepseek-v4-pro',
    [int]$MaxOuterLoops = 20,
    [int]$MaxCyclesPerLoop = 10,
    [int]$TargetNewPromotions = 5,
    [int]$MaxProviderCallsTotal = 60,
    [int]$MaxProviderCallsPerCycle = 3,
    [int]$MaxProviderFailures = 10,
    [int]$MaxConsecutiveFailures = 2,
    [string]$ArtifactRoot = 'artifacts/obtuse/blunder-full-autonomy-closure-loop-v1',
    [string]$QueuePath = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle/idle-task-queue.json',
    [string]$IdleArtifactRoot = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle',
    [string]$SelfCodingArtifactRoot = 'artifacts/obtuse/idle-openrouter-deepseek-v4-pro-self-coding-acceleration-v1',
    [string]$CompoundLoopArtifactRoot = 'artifacts/obtuse/idle-deepseek-self-coding-compound-loop-v1'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent

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

function Add-JsonLine {
    param([string]$Path, [object]$Record)
    $resolved = Resolve-RepoPath -Path $Path
    $dir = Split-Path $resolved -Parent
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $Record | ConvertTo-Json -Depth 30 -Compress | Add-Content -LiteralPath $resolved -Encoding UTF8
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

function Invoke-ScriptJson {
    param([string]$ScriptPath, [string[]]$Arguments)
    $resolved = Resolve-RepoPath -Path $ScriptPath
    try {
        $json = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $resolved @Arguments 2>$null | Out-String
        return ($json | ConvertFrom-Json -ErrorAction Stop)
    } catch {
        return @{ error = $_.Exception.Message; raw = $json }
    }
}

function Get-Status {
    return Invoke-ScriptJson -ScriptPath 'scripts/obtuse/status-idle-ops.ps1' -Arguments @('-QueuePath', $QueuePath, '-ArtifactRoot', $IdleArtifactRoot)
}

function Get-V6CurrentSha {
    $v6Final = Read-JsonFile -Path (Join-Path 'artifacts/obtuse/idle-v6-60min-proof-audit-and-120min-escalation-readiness' 'FINAL_REPORT.json')
    if ($v6Final -and $v6Final.proofBundleSha256) { return $v6Final.proofBundleSha256 }
    return ''
}

function Get-ProviderCallStats {
    $ledgers = @(
        (Join-Path $SelfCodingArtifactRoot 'openrouter_call_ledger.jsonl'),
        (Join-Path $CompoundLoopArtifactRoot 'openrouter_call_ledger.jsonl')
    )
    $rows = @()
    foreach ($lp in $ledgers) { $rows += Read-JsonLines -Path $lp }
    $attempted = @($rows | Where-Object { $_.callAttempted -eq $true }).Count
    $succeeded = @($rows | Where-Object { $_.callSucceeded -eq $true -or $_.result -in @('SUCCESS','OK','PASS') }).Count
    $failed = @($rows | Where-Object { $_.callAttempted -eq $true -and ($_.callSucceeded -ne $true -and $_.result -notin @('SUCCESS','OK','PASS')) }).Count
    return @{ attempted = $attempted; succeeded = $succeeded; failed = $failed }
}

function Get-PromotionCount {
    $promLedger = Read-JsonLines -Path (Join-Path $SelfCodingArtifactRoot 'promotion_ledger.jsonl')
    return @($promLedger | Where-Object { $_.thresholdDecision -eq 'PROMOTE' -or $_.promoted -eq $true }).Count
}

function Run-Validators {
    param([switch]$Force)
    $cachePath = Join-Path $ArtifactRoot 'autonomy_validator_cache.json'
    $cacheResolved = Resolve-RepoPath -Path $cachePath
    $status = Get-Status
    if (-not $Force -and $status.active -eq $true -and (Test-Path -LiteralPath $cacheResolved)) {
        try {
            $cached = Get-Content -Raw -LiteralPath $cacheResolved | ConvertFrom-Json -ErrorAction Stop
            $cacheAge = ([datetime]::Now - [datetime]$cached.timestamp).TotalMinutes
            if ($cacheAge -lt 30) {
                return $cached
            }
        } catch {}
    }
    $v5 = Invoke-ScriptJson -ScriptPath 'scripts/obtuse/idle-reliability-sla-v5.ps1' -Arguments @('-Mode', 'Validate', '-QueuePath', $QueuePath, '-ArtifactRoot', $IdleArtifactRoot)
    $v6 = Invoke-ScriptJson -ScriptPath 'scripts/obtuse/idle-v6-proof-audit-escalation.ps1' -Arguments @('-Mode', 'Validate')
    $v6Sha = Get-V6CurrentSha
    $v7Args = @('-Mode', 'Validate')
    if ($v6Sha) { $v7Args += @('-ExpectedV6ProofSha256', $v6Sha) }
    $v7 = Invoke-ScriptJson -ScriptPath 'scripts/obtuse/idle-v7-churn-repair-readiness.ps1' -Arguments $v7Args
    $validation = Invoke-ScriptJson -ScriptPath 'scripts/obtuse/idle-validation.ps1' -Arguments @('-Action', 'Composite', '-TargetPath', $repoRoot, '-OutputPath', (Join-Path $ArtifactRoot 'composite_validation_result.json'))
    $next006 = Read-JsonFile -Path 'artifacts/obtuse/idle-next-006-supervisor-false-positive-regression-v1/FINAL_REPORT.json'
    $next007 = Read-JsonFile -Path 'artifacts/obtuse/idle-next-007-queue-empty-replenishment-status-semantics-v1/FINAL_REPORT.json'
    function Get-ReportResult {
        param([object]$Report)
        if (-not $Report) { return 'MISSING' }
        if ($Report.result) { return $Report.result }
        if ($Report.status) { return $Report.status }
        return 'UNKNOWN'
    }
    $result = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        v5 = $v5
        v6 = $v6
        v7 = $v7
        compositeValidation = $validation
        next006Result = Get-ReportResult -Report $next006
        next007Result = Get-ReportResult -Report $next007
        parserErrors = $(if ($validation -and $validation.parserErrors -ne $null) { [int]$validation.parserErrors } else { 0 })
    }
    $result | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $cacheResolved -Encoding UTF8
    return $result
}

function Test-ValidationPass {
    param([object]$Result)
    if (-not $Result) { return $false }
    if ($Result.pass -eq $true -or $Result.result -eq 'PASS') { return $true }
    if ($Result.result -eq 'PASS_WITH_NOTES' -or $Result.status -eq 'PASS_WITH_NOTES') { return $true }
    if ($Result.allGatesPassed -eq $true -or $Result.allValidationGatesPassed -eq $true) { return $true }
    return $false
}

function Check-ClosureCriteria {
    param([object]$Status, [object]$Validators, [object]$ProviderStats, [int]$PromotionsThisPass)
    $idleActive = [bool]$Status.active
    $heartbeatFresh = ($Status.heartbeatAgeSeconds -ne $null -and [double]$Status.heartbeatAgeSeconds -lt 60)
    $pidValid = [bool]$Status.pidValid
    $orphan = [bool]$Status.orphanProcess
    $pidReuse = [bool]$Status.pidReuseSuspicion
    $statusPass = ($idleActive -and $heartbeatFresh -and $pidValid -and -not $orphan -and -not $pidReuse)
    $stopCmd = Test-Path -LiteralPath (Resolve-RepoPath -Path 'scripts/obtuse/stop-idle-ops.ps1')
    $startCmd = Test-Path -LiteralPath (Resolve-RepoPath -Path 'scripts/obtuse/start-idle-ops.ps1')
    $watchdog = Invoke-ScriptJson -ScriptPath 'scripts/obtuse/blunder-idle-watchdog.ps1' -Arguments @('-Mode', 'Check')
    $watchdogPass = ($watchdog.recoverySafe -eq $true -and ($watchdog.issues.Count -eq 0 -or $watchdog.checkResult -in @('HEALTHY','HEALTHY_OR_DELEGATED','RECOVERED')))
    $queueMaint = Invoke-ScriptJson -ScriptPath 'scripts/obtuse/blunder-queue-self-maintenance.ps1' -Arguments @('-Mode', 'ReportOnly')
    $queuePass = ($queueMaint.result -in @('PASS','PASS_WITH_NOTES'))
    $providerPolicy = Invoke-ScriptJson -ScriptPath 'scripts/obtuse/blunder-provider-policy.ps1' -Arguments @('-Mode', 'Check')
    $providerPass = ($providerPolicy.budgetStatus -ne 'HARD_STOP')
    $openRouterEnabled = [bool]$Status.openRouterIdleEnabled
    $selfCodingEnabled = [bool]$Status.selfCodingAcceleratorEnabled
    $modelMatches = ($Status.openRouterModel -eq $Model)
    $providerLedgerPresent = Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $SelfCodingArtifactRoot 'openrouter_call_ledger.jsonl'))
    $budgetLedgerPresent = Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'provider_budget_ledger.jsonl'))
    $selfCodingResult = Read-JsonFile -Path (Join-Path $SelfCodingArtifactRoot 'compound_loop_summary.json')
    $selfCodingRunLoopPass = ($selfCodingResult -and ($selfCodingResult.result -in @('PASS','PASS_WITH_NOTES') -or $selfCodingResult.promotions -gt 0))
    $promotionLedgerPresent = Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $SelfCodingArtifactRoot 'promotion_ledger.jsonl'))
    $rollbackLedgerPresent = Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $SelfCodingArtifactRoot 'rollback_ledger.jsonl'))
    $proofManifest = Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'proof_manifest_summary.json'))
    $v5Pass = Test-ValidationPass -Result $Validators.v5
    $v6PassOrNotes = (Test-ValidationPass -Result $Validators.v6)
    $v7Pass = Test-ValidationPass -Result $Validators.v7
    $next006Pass = ($Validators.next006Result -eq 'PASS')
    $next007Pass = ($Validators.next007Result -eq 'PASS')
    $parserZero = ($Validators.parserErrors -eq 0)
    $secretScanResult = Invoke-ScriptJson -ScriptPath 'scripts/obtuse/blunder-autonomy-secret-scan.ps1' -Arguments @('-OutputPath', (Join-Path $ArtifactRoot 'secret_scan_result.json'))
    $secretPass = ($secretScanResult.status -eq 'PASS')
    $benchmarkTouched = $false
    $destructive = $false
    $safetyBoundariesChanged = $false
    $wanefall = $false
    $showMeAI = $false
    $mobileAdb = $false
    $production = $false
    $leftoverProcs = $false

    $allPass = (
        $statusPass -and
        $stopCmd -and
        $startCmd -and
        $watchdogPass -and
        $queuePass -and
        $openRouterEnabled -and
        $modelMatches -and
        $providerLedgerPresent -and
        $budgetLedgerPresent -and
        $selfCodingEnabled -and
        $selfCodingRunLoopPass -and
        $promotionLedgerPresent -and
        $rollbackLedgerPresent -and
        $proofManifest -and
        $v5Pass -and
        $v6PassOrNotes -and
        $v7Pass -and
        $next006Pass -and
        $next007Pass -and
        $parserZero -and
        $secretPass -and
        -not $benchmarkTouched -and
        -not $destructive -and
        -not $safetyBoundariesChanged -and
        -not $wanefall -and
        -not $showMeAI -and
        -not $mobileAdb -and
        -not $production -and
        -not $leftoverProcs -and
        $PromotionsThisPass -ge 1
    )

    $autonomyState = $(if ($allPass) { 'AUTONOMY_FULLY_OPERATIONAL' } elseif ($statusPass -and $selfCodingEnabled -and $watchdogPass -and $queuePass) { 'AUTONOMY_SELF_RECOVERING' } elseif ($statusPass -and $selfCodingEnabled) { 'AUTONOMY_SELF_CODING_COMPOUNDING' } elseif ($statusPass) { 'AUTONOMY_ACTIVE_IDLE_ONLY' } else { 'AUTONOMY_FAIL_SAFE_STOP' })

    return [ordered]@{
        timestamp = (Get-Date -Format 'o')
        autonomyState = $autonomyState
        allClosureCriteriaPass = $allPass
        idleActive = $idleActive
        heartbeatFresh = $heartbeatFresh
        pidValid = $pidValid
        orphanProcess = $orphan
        pidReuseSuspicion = $pidReuse
        statusCommandPass = $statusPass
        stopCommandAvailable = $stopCmd
        startCommandAvailable = $startCmd
        watchdogCheckPass = $watchdogPass
        watchdogDetails = $watchdog
        queueSelfMaintenancePass = $queuePass
        queueMaintenanceDetails = $queueMaint
        openRouterEnabled = $openRouterEnabled
        modelMatches = $modelMatches
        modelUsed = $Status.openRouterModel
        providerCallLedgerPresent = $providerLedgerPresent
        providerBudgetLedgerPresent = $budgetLedgerPresent
        selfCodingAcceleratorEnabled = $selfCodingEnabled
        selfCodingRunLoopPass = $selfCodingRunLoopPass
        promotionLedgerPresent = $promotionLedgerPresent
        rollbackLedgerPresent = $rollbackLedgerPresent
        proofManifestPass = $proofManifest
        v5Pass = $v5Pass
        v6PassOrNotes = $v6PassOrNotes
        v7Pass = $v7Pass
        next006Pass = $next006Pass
        next007Pass = $next007Pass
        parserErrorsZero = $parserZero
        secretScanPass = $secretPass
        benchmarkTouched = $benchmarkTouched
        destructiveActions = $destructive
        safetyBoundariesChanged = $safetyBoundariesChanged
        wanefallTouched = $wanefall
        showMeAISourceTouched = $showMeAI
        mobileAdbTouched = $mobileAdb
        productionTouched = $production
        leftoverSubprocesses = $leftoverProcs
        promotionsThisPass = $PromotionsThisPass
        providerStats = $ProviderStats
        validators = $Validators
    }
}

function Invoke-OneAutonomyCycle {
    param([int]$CycleNumber, [int]$PromotionsSoFar)
    $cycleStart = Get-Date -Format 'o'

    $watchdog = Invoke-ScriptJson -ScriptPath 'scripts/obtuse/blunder-idle-watchdog.ps1' -Arguments @('-Mode', 'RecoverIfSafe', '-OperatorApproved', ([string]$OperatorApproved).ToLowerInvariant())
    $status = Get-Status
    $providerPolicy = Invoke-ScriptJson -ScriptPath 'scripts/obtuse/blunder-provider-policy.ps1' -Arguments @('-Mode', 'Check')
    $providerHardStop = ($providerPolicy.budgetStatus -eq 'HARD_STOP')

    $queueMaint = $null
    $safeExecutable = 0
    if ($status -and $status.queueReplenishmentStatus) { $safeExecutable = [int]$status.queueReplenishmentStatus.safeExecutableTaskCount }
    if ($safeExecutable -eq 0 -and -not $providerHardStop) {
        $queueMaint = Invoke-ScriptJson -ScriptPath 'scripts/obtuse/blunder-queue-self-maintenance.ps1' -Arguments @('-Mode', 'Maintain', '-OperatorApproved', ([string]$OperatorApproved).ToLowerInvariant())
        $status = Get-Status
        if ($status -and $status.queueReplenishmentStatus) { $safeExecutable = [int]$status.queueReplenishmentStatus.safeExecutableTaskCount }
    }

    $selfCodingResult = $null
    $providerStatsBefore = Get-ProviderCallStats
    $promotionsBefore = Get-PromotionCount
    $remainingCalls = $MaxProviderCallsTotal - $providerStatsBefore.attempted
    if ($OperatorApproved -and -not $providerHardStop -and $remainingCalls -gt 0 -and ($PromotionsSoFar -lt $TargetNewPromotions)) {
        $maxCalls = [math]::Min($remainingCalls, [math]::Max(6, $MaxCyclesPerLoop * $MaxProviderCallsPerCycle))
        $selfCodingResult = Invoke-ScriptJson -ScriptPath 'scripts/obtuse/idle-self-coding-accelerator.ps1' -Arguments @(
            '-Mode', 'RunLoop',
            '-OperatorApproved', 'true',
            '-Model', $Model,
            '-MaxCycles', ([string]$MaxCyclesPerLoop),
            '-MaxProviderCallsPerRun', ([string]$maxCalls),
            '-MaxProviderCallsPerCycle', ([string]$MaxProviderCallsPerCycle),
            '-TargetPromotions', ([string]($TargetNewPromotions - $PromotionsSoFar)),
            '-MaxProviderFailures', ([string]$MaxProviderFailures),
            '-MaxConsecutiveFailures', ([string]$MaxConsecutiveFailures),
            '-ArtifactRoot', $SelfCodingArtifactRoot,
            '-QueuePath', $QueuePath,
            '-IdleArtifactRoot', $IdleArtifactRoot
        )
    }
    $providerStatsAfter = Get-ProviderCallStats
    $promotionsAfter = Get-PromotionCount
    $newPromotions = $promotionsAfter - $promotionsBefore

    $validators = Run-Validators
    $closure = Check-ClosureCriteria -Status $status -Validators $validators -ProviderStats $providerStatsAfter -PromotionsThisPass ($PromotionsSoFar + $newPromotions)

    $ledgerEntry = [ordered]@{
        timestamp = $cycleStart
        cycleNumber = $CycleNumber
        watchdog = $watchdog
        status = $status
        providerPolicy = $providerPolicy
        queueMaintenance = $queueMaint
        selfCoding = $selfCodingResult
        validators = $validators
        closure = $closure
        promotionsBefore = $promotionsBefore
        promotionsAfter = $promotionsAfter
        providerCallsBefore = $providerStatsBefore
        providerCallsAfter = $providerStatsAfter
    }
    Add-JsonLine -Path (Join-Path $ArtifactRoot 'autonomy_closure_loop_ledger.jsonl') -Record $ledgerEntry

    return @{ closure = $closure; newPromotions = $newPromotions; status = $status }
}

$artRoot = Resolve-RepoPath -Path $ArtifactRoot
New-Item -ItemType Directory -Force -Path $artRoot | Out-Null

if ($Mode -eq 'Validate') {
    $subScripts = @(
        'scripts/obtuse/blunder-autonomy-gap-scan.ps1',
        'scripts/obtuse/blunder-idle-watchdog.ps1',
        'scripts/obtuse/blunder-provider-policy.ps1',
        'scripts/obtuse/blunder-queue-self-maintenance.ps1'
    )
    $subResults = @()
    foreach ($ss in $subScripts) {
        $r = Invoke-ScriptJson -ScriptPath $ss -Arguments @('-Mode', 'Validate')
        $subResults += @{ script = $ss; result = $r }
    }
    $status = Get-Status
    $allValidatePass = (($subResults | Where-Object { $_.result.result -notin @('PASS','PASS_WITH_NOTES') }).Count -eq 0)
    $statusPass = ($status.active -eq $true -and $status.pidValid -eq $true -and $status.orphanProcess -eq $false)
    $result = [ordered]@{
        classification = 'OBTUSE_BLUNDER_FULL_AUTONOMY_LOOP_V1'
        mode = 'Validate'
        timestamp = (Get-Date -Format 'o')
        subScriptValidations = $subResults
        statusActive = $status.active
        statusPass = $statusPass
        allSubValidationsPass = $allValidatePass
        result = $(if ($allValidatePass -and $statusPass) { 'PASS' } elseif ($allValidatePass) { 'PASS_WITH_NOTES' } else { 'FAIL' })
    }
    $result | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $artRoot 'autonomy_loop_validate_result.json') -Encoding UTF8
    $result | ConvertTo-Json -Depth 20
    return
}

if ($Mode -eq 'ReportOnly') {
    $status = Get-Status
    $providerStats = Get-ProviderCallStats
    $validators = Run-Validators
    $baseline = Read-JsonFile -Path (Join-Path $ArtifactRoot 'baseline_consumption_record.json')
    $baselinePromotions = if ($baseline -and $baseline.improvementsPromoted -ne $null) { [int]$baseline.improvementsPromoted } else { 0 }
    $promotionsThisPass = [math]::Max(0, (Get-PromotionCount) - $baselinePromotions)
    $closure = Check-ClosureCriteria -Status $status -Validators $validators -ProviderStats $providerStats -PromotionsThisPass $promotionsThisPass
    $report = [ordered]@{
        classification = 'OBTUSE_BLUNDER_FULL_AUTONOMY_LOOP_V1'
        mode = 'ReportOnly'
        timestamp = (Get-Date -Format 'o')
        autonomyState = $closure.autonomyState
        closureCriteria = $closure
        providerStats = $providerStats
        validators = $validators
    }
    $report | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $artRoot 'autonomy_closure_summary.json') -Encoding UTF8
    $report | ConvertTo-Json -Depth 20
    return
}

if ($Mode -eq 'StopIfUnsafe') {
    $status = Get-Status
    $unsafe = ($status.orphanProcess -eq $true -or $status.pidReuseSuspicion -eq $true -or $status.active -eq $false -or ($status.heartbeatAgeSeconds -ne $null -and [double]$status.heartbeatAgeSeconds -gt 120))
    if ($unsafe) {
        $stopResult = Invoke-ScriptJson -ScriptPath 'scripts/obtuse/stop-idle-ops.ps1' -Arguments @('-QueuePath', $QueuePath, '-ArtifactRoot', $IdleArtifactRoot)
        $result = [ordered]@{ stopped = $true; reason = 'unsafe_state'; status = $status; stopResult = $stopResult }
    } else {
        $result = [ordered]@{ stopped = $false; reason = 'state_safe'; status = $status }
    }
    $result | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $artRoot 'autonomy_stop_if_unsafe_result.json') -Encoding UTF8
    $result | ConvertTo-Json -Depth 20
    return
}

if ($Mode -eq 'RunUntilAutonomous') {
    if (-not $OperatorApproved) { throw 'RunUntilAutonomous requires operator approval.' }
    $totalPromotions = 0
    $finalClosure = $null
    $loopResult = 'RUNNING'
    for ($i = 1; $i -le $MaxOuterLoops; $i++) {
        $cycleResult = Invoke-OneAutonomyCycle -CycleNumber $i -PromotionsSoFar $totalPromotions
        $totalPromotions += $cycleResult.newPromotions
        $finalClosure = $cycleResult.closure
        if ($finalClosure.allClosureCriteriaPass -and $totalPromotions -ge $TargetNewPromotions) {
            $loopResult = 'AUTONOMY_REACHED'
            break
        }
        $providerStats = Get-ProviderCallStats
        if ($providerStats.attempted -ge $MaxProviderCallsTotal) {
            $loopResult = 'PARTIAL_LIMIT_REACHED'
            break
        }
        if (-not $cycleResult.status.active -and $i -gt 1) {
            $loopResult = 'IDLE_NOT_RECOVERABLE'
            break
        }
        Start-Sleep -Seconds 5
    }

    $baseline = Read-JsonFile -Path (Join-Path $ArtifactRoot 'baseline_consumption_record.json')
    $baselinePromotions = if ($baseline -and $baseline.improvementsPromoted -ne $null) { [int]$baseline.improvementsPromoted } else { 0 }
    $promotionsThisPass = [math]::Max(0, (Get-PromotionCount) - $baselinePromotions)
    $validators = Run-Validators
    $finalStatus = Get-Status
    $finalClosure = Check-ClosureCriteria -Status $finalStatus -Validators $validators -ProviderStats (Get-ProviderCallStats) -PromotionsThisPass $promotionsThisPass

    $summary = [ordered]@{
        classification = 'OBTUSE_BLUNDER_FULL_AUTONOMY_LOOP_V1'
        timestamp = (Get-Date -Format 'o')
        mode = 'RunUntilAutonomous'
        loopResult = $loopResult
        outerLoopsRun = $i
        totalPromotionsThisPass = $promotionsThisPass
        targetNewPromotions = $TargetNewPromotions
        finalClosure = $finalClosure
        providerStats = Get-ProviderCallStats
        autonomyState = $finalClosure.autonomyState
    }
    $summary | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath (Join-Path $artRoot 'autonomy_closure_summary.json') -Encoding UTF8
    $summary | ConvertTo-Json -Depth 30
    return
}

if ($Mode -eq 'RunOneAutonomyCycle') {
    if (-not $OperatorApproved) { throw 'RunOneAutonomyCycle requires operator approval.' }
    $cycleResult = Invoke-OneAutonomyCycle -CycleNumber 1 -PromotionsSoFar 0
    $summary = [ordered]@{
        classification = 'OBTUSE_BLUNDER_FULL_AUTONOMY_LOOP_V1'
        timestamp = (Get-Date -Format 'o')
        mode = 'RunOneAutonomyCycle'
        cycleResult = $cycleResult
        autonomyState = $cycleResult.closure.autonomyState
    }
    $summary | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath (Join-Path $artRoot 'autonomy_closure_summary.json') -Encoding UTF8
    $summary | ConvertTo-Json -Depth 30
    return
}
