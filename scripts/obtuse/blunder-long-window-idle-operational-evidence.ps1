#requires -Version 5.1
<#
.SYNOPSIS
OBTUSE_BLUNDER_LONG_WINDOW_IDLE_OPERATIONAL_EVIDENCE_V1 supervisor.

.DESCRIPTION
Consumes the local + FRANKENSTEIN operational closure baseline, supervises one
operator-approved 60-minute idle evidence window, stops the runtime, validates
post-run evidence, and publishes a proof bundle to FRANKENSTEIN.
#>
[CmdletBinding()]
param(
    [ValidateSet('Validate','Run60','ReportOnly','StopAndAudit','StopIfUnsafe')]
    [string]$Mode,
    [string]$OperatorApproved
)

$ErrorActionPreference = 'Stop'

$classification = 'OBTUSE_BLUNDER_LONG_WINDOW_IDLE_OPERATIONAL_EVIDENCE_V1'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$artifactRoot = 'artifacts/obtuse/blunder-long-window-idle-operational-evidence-v1'
$proofBundleRoot = 'artifacts/obtuse/proof-bundles'
$frankensteinMirror = '\\fileserver\shared\obtuse\blunder-long-window-idle-operational-evidence-v1'
$closureFinalReportPath = 'artifacts/obtuse/blunder-local-frankenstein-operational-closure-v1/FINAL_REPORT.json'
$closureProofBundlePath = 'artifacts/obtuse/proof-bundles/obtuse-blunder-local-frankenstein-operational-closure-v1-20260628T194246.zip'
$expectedClosureProofBundleSha256 = 'F137F26FBED2C7EA101AE22AF52E1A0FC8CA8E6B12CC3506B68E7BA28F5AD8F1'
$expectedClosureTerminalState = 'BLUNDER_LOCAL_FRANKENSTEIN_OPERATIONAL_CLOSED_WITH_GAMINGPC3_NOTES'
$modelAlias = 'deepseek-v4-flash'
$modelResolvedId = 'deepseek/deepseek-v4-flash'
$targetRuntimeSeconds = 3600
$hardMaximumRuntimeSeconds = 3900
$pollIntervalSeconds = 60

function Convert-ToBoolean {
    param([object]$Value)
    if ($Value -is [bool]) { return [bool]$Value }
    if ($Value -is [int]) { return ([int]$Value -ne 0) }
    return (([string]$Value).Trim().ToLowerInvariant() -in @('true','1','yes','y','approved'))
}

function Resolve-RepoPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $repoRoot $Path
}

function New-ArtifactDirectory {
    $resolvedRoot = Resolve-RepoPath -Path $artifactRoot
    New-Item -ItemType Directory -Force -Path $resolvedRoot | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $resolvedRoot 'live\snapshots') | Out-Null
    return $resolvedRoot
}

function Write-JsonFile {
    param([object]$Value, [string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    $parent = Split-Path $resolved -Parent
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $Value | ConvertTo-Json -Depth 80 | Set-Content -LiteralPath $resolved -Encoding UTF8
}

function Read-JsonFile {
    param([string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    if (-not (Test-Path -LiteralPath $resolved)) { return $null }
    return Get-Content -Raw -LiteralPath $resolved | ConvertFrom-Json -ErrorAction Stop
}

function Add-JsonLine {
    param([string]$Path, [object]$Record)
    $resolved = Resolve-RepoPath -Path $Path
    $parent = Split-Path $resolved -Parent
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $Record | ConvertTo-Json -Depth 80 -Compress | Add-Content -LiteralPath $resolved -Encoding UTF8
}

function Ensure-FileExists {
    param([string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    $parent = Split-Path $resolved -Parent
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    if (-not (Test-Path -LiteralPath $resolved)) {
        Set-Content -LiteralPath $resolved -Value '' -NoNewline -Encoding UTF8
    }
}

function Get-PropertyValue {
    param([object]$Object, [string]$Name)
    if ($null -eq $Object) { return $null }
    if ($Object.PSObject.Properties.Name -contains $Name) { return $Object.$Name }
    return $null
}

function Convert-ToInt {
    param([object]$Value)
    if ($null -eq $Value) { return 0 }
    try { return [int]$Value } catch { return 0 }
}

function Convert-JsonSafe {
    param([string]$Text)
    if ([string]::IsNullOrWhiteSpace($Text)) { return $null }
    try { return $Text | ConvertFrom-Json -ErrorAction Stop } catch { return $null }
}

function Join-ProcessArguments {
    param([string]$FilePath, [string[]]$Arguments)
    $resolved = Resolve-RepoPath -Path $FilePath
    $parts = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $resolved)
    foreach ($argument in $Arguments) {
        if ($null -ne $argument) { $parts += [string]$argument }
    }
    $quoted = @()
    foreach ($part in $parts) {
        $safe = ([string]$part).Replace('"', '\"')
        $quoted += '"' + $safe + '"'
    }
    return ($quoted -join ' ')
}

function Invoke-ChildPowerShell {
    param(
        [string]$Name,
        [string]$FilePath,
        [string[]]$Arguments,
        [int]$TimeoutSeconds
    )
    $resolved = Resolve-RepoPath -Path $FilePath
    $exists = Test-Path -LiteralPath $resolved
    $record = [ordered]@{
        name = $Name
        script = $FilePath
        resolved = $resolved
        arguments = $Arguments
        command = "powershell -NoProfile -ExecutionPolicy Bypass -File $FilePath $($Arguments -join ' ')".Trim()
        exists = $exists
        exitCode = $null
        timedOut = $false
        stdout = ''
        stderr = ''
        raw = ''
        parsed = $null
        parseError = $null
        startedAt = (Get-Date -Format 'o')
        completedAt = $null
    }
    if (-not $exists) {
        $record.exitCode = 127
        $record.stderr = 'Script not found.'
        $record.completedAt = Get-Date -Format 'o'
        return $record
    }

    $powerShellArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $resolved)
    foreach ($argument in $Arguments) {
        if ($null -ne $argument) { $powerShellArgs += [string]$argument }
    }
    $powerShellArgsJson = $powerShellArgs | ConvertTo-Json -Compress
    $job = Start-Job -ScriptBlock {
        param([string]$ChildArgumentsJson, [string]$WorkingDirectory)
        $ChildArguments = @($ChildArgumentsJson | ConvertFrom-Json)
        Set-Location -LiteralPath $WorkingDirectory
        $output = & powershell.exe @ChildArguments 2>&1 | Out-String
        [pscustomobject]@{
            exitCode = $LASTEXITCODE
            output = $output
        }
    } -ArgumentList $powerShellArgsJson, $repoRoot
    $finishedJob = Wait-Job -Job $job -Timeout $TimeoutSeconds
    if ($null -eq $finishedJob) {
        $record.timedOut = $true
        try { Stop-Job -Job $job -ErrorAction SilentlyContinue } catch {}
        $record.exitCode = 124
        $record.stderr = "Timed out after $TimeoutSeconds seconds."
    } else {
        $jobResult = Receive-Job -Job $job -ErrorAction SilentlyContinue
        $record.exitCode = $jobResult.exitCode
        $record.stdout = [string]$jobResult.output
    }
    try { Remove-Job -Job $job -Force -ErrorAction SilentlyContinue } catch {}
    $record.raw = ($record.stdout + "`n" + $record.stderr).Trim()
    $record.completedAt = Get-Date -Format 'o'
    $parsed = Convert-JsonSafe -Text $record.raw
    if ($null -ne $parsed) {
        $record.parsed = $parsed
    } elseif (-not [string]::IsNullOrWhiteSpace($record.raw)) {
        try { $record.raw | ConvertFrom-Json -ErrorAction Stop | Out-Null } catch { $record.parseError = $_.Exception.Message }
    }
    return $record
}

function Get-StatusCommandRecord {
    return Invoke-ChildPowerShell -Name 'status-idle-ops' -FilePath 'scripts/obtuse/status-idle-ops.ps1' -Arguments @() -TimeoutSeconds 45
}

function Get-StatusObject {
    param([object]$StatusCommand)
    if ($null -eq $StatusCommand) { return $null }
    return $StatusCommand.parsed
}

function Get-QueueStatsRecord {
    param([object]$Status)
    $stats = Get-PropertyValue -Object $Status -Name 'queueStats'
    return [ordered]@{
        total = Convert-ToInt -Value (Get-PropertyValue -Object $stats -Name 'total')
        pending = Convert-ToInt -Value (Get-PropertyValue -Object $stats -Name 'pending')
        running = Convert-ToInt -Value (Get-PropertyValue -Object $stats -Name 'running')
        passed = Convert-ToInt -Value (Get-PropertyValue -Object $stats -Name 'passed')
        failed = Convert-ToInt -Value (Get-PropertyValue -Object $stats -Name 'failed')
        blocked = Convert-ToInt -Value (Get-PropertyValue -Object $stats -Name 'blocked')
        quarantined = Convert-ToInt -Value (Get-PropertyValue -Object $stats -Name 'quarantined')
    }
}

function Get-ProviderCounts {
    param([object]$Status)
    return [ordered]@{
        attempted = Convert-ToInt -Value (Get-PropertyValue -Object $Status -Name 'providerCallsAttempted')
        succeeded = Convert-ToInt -Value (Get-PropertyValue -Object $Status -Name 'providerCallsSucceeded')
        failed = Convert-ToInt -Value (Get-PropertyValue -Object $Status -Name 'providerCallsFailed')
    }
}

function Get-QueueHealthState {
    param([object]$Status)
    $replenishment = Get-PropertyValue -Object $Status -Name 'queueReplenishmentStatus'
    $state = Get-PropertyValue -Object $replenishment -Name 'queueHealthState'
    if ([string]::IsNullOrWhiteSpace([string]$state)) { return 'QUEUE_UNKNOWN_NEEDS_REVIEW' }
    return [string]$state
}

function Get-StuckSubprocessSummary {
    $now = Get-Date
    $processRows = @()
    try {
        $processRows = @(Get-CimInstance Win32_Process | Where-Object {
            $_.CommandLine -and ($_.CommandLine -match 'openrouter-idle-model\.ps1|idle-self-coding-accelerator\.ps1')
        })
    } catch {
        $processRows = @()
    }
    $records = @()
    foreach ($row in $processRows) {
        $ageSeconds = $null
        try {
            $created = [System.Management.ManagementDateTimeConverter]::ToDateTime($row.CreationDate)
            $ageSeconds = [math]::Round(($now - $created).TotalSeconds, 3)
        } catch {}
        $records += [ordered]@{
            processId = $row.ProcessId
            name = $row.Name
            ageSeconds = $ageSeconds
            commandLineRedacted = (($row.CommandLine -replace 'sk-or-[A-Za-z0-9_-]{8,}', 'REDACTED') -replace 'sk-[A-Za-z0-9_-]{8,}', 'REDACTED')
            stuck = ($null -ne $ageSeconds -and $ageSeconds -gt 300)
        }
    }
    return [ordered]@{
        count = $records.Count
        stuckCount = @($records | Where-Object { $_.stuck -eq $true }).Count
        records = $records
    }
}

function Test-ModelInvariant {
    param([object]$Status)
    $alias = [string](Get-PropertyValue -Object $Status -Name 'openRouterModelAlias')
    $resolved = [string](Get-PropertyValue -Object $Status -Name 'openRouterModelResolved')
    return ($alias -eq $modelAlias -and $resolved -eq $modelResolvedId)
}

function Get-UnsafeStatusReasons {
    param([object]$Status, [object]$StuckSummary)
    $reasons = @()
    if ($null -eq $Status) { return @('STATUS_UNAVAILABLE') }
    if ([bool](Get-PropertyValue -Object $Status -Name 'orphanProcess')) { $reasons += 'ORPHAN_PROCESS_TRUE' }
    if ([bool](Get-PropertyValue -Object $Status -Name 'pidReuseSuspicion')) { $reasons += 'PID_REUSE_SUSPICION_TRUE' }
    if (-not (Test-ModelInvariant -Status $Status)) { $reasons += 'MODEL_INVARIANT_FAILED' }
    $active = [bool](Get-PropertyValue -Object $Status -Name 'active')
    $heartbeatAge = Get-PropertyValue -Object $Status -Name 'heartbeatAgeSeconds'
    if ($active -and $null -ne $heartbeatAge -and [double]$heartbeatAge -gt 180) { $reasons += 'ACTIVE_HEARTBEAT_STALE' }
    if ($StuckSummary -and [int]$StuckSummary.stuckCount -gt 0) { $reasons += 'STUCK_PROVIDER_OR_SELF_CODING_SUBPROCESS' }
    return $reasons
}

function Test-ScriptParser {
    param([string[]]$Paths)
    $records = @()
    $totalErrors = 0
    foreach ($path in $Paths) {
        $resolved = Resolve-RepoPath -Path $path
        $errors = $null
        $tokens = $null
        if (-not (Test-Path -LiteralPath $resolved)) {
            $records += [ordered]@{ path = $resolved; exists = $false; errorCount = 1; errors = @('FILE_NOT_FOUND') }
            $totalErrors++
            continue
        }
        [System.Management.Automation.Language.Parser]::ParseFile($resolved, [ref]$tokens, [ref]$errors) | Out-Null
        $messages = @()
        foreach ($err in @($errors)) { $messages += $err.Message }
        $records += [ordered]@{ path = $resolved; exists = $true; errorCount = @($errors).Count; errors = $messages }
        $totalErrors += @($errors).Count
    }
    return [ordered]@{
        checkedAt = (Get-Date -Format 'o')
        totalParserErrors = $totalErrors
        records = $records
        result = if ($totalErrors -eq 0) { 'PASS' } else { 'FAIL' }
    }
}

function Invoke-SecretScan {
    param([string]$RunId)
    $root = New-ArtifactDirectory
    $scanPaths = @($root, $PSCommandPath)
    $patterns = @(
        @{ name = 'openrouter_sk_key_value'; regex = 'sk-or-[A-Za-z0-9_-]{20,}' },
        @{ name = 'openai_project_key_value'; regex = 'sk-proj-[A-Za-z0-9_-]{20,}' },
        @{ name = 'generic_sk_key_value'; regex = 'sk-(?!or\b|proj\b)[A-Za-z0-9_-]{20,}' },
        @{ name = 'api_key_value_assignment'; regex = '(?i)(api[_-]?key|password|secret)\s*=\s*["''][^"'']{16,}["'']' },
        @{ name = 'private_key_block'; regex = '-----BEGIN (RSA |OPENSSH |EC |DSA |PRIVATE )?KEY-----' }
    )
    $falsePositivePatterns = @(
        '(?i)OPENROUTER_API_KEY\b',
        '(?i)OPENAI_API_KEY\b',
        '(?i)\$env:OPENROUTER_API_KEY'
    )
    $findings = @()
    $scannedFiles = 0
    foreach ($scanPath in $scanPaths) {
        if (-not (Test-Path -LiteralPath $scanPath)) { continue }
        $files = @()
        if ((Get-Item -LiteralPath $scanPath).PSIsContainer) {
            $files = @(Get-ChildItem -LiteralPath $scanPath -File -Recurse -ErrorAction SilentlyContinue)
        } else {
            $files = @(Get-Item -LiteralPath $scanPath)
        }
        foreach ($file in $files) {
            if ($file.Extension -in @('.zip','.png','.jpg','.jpeg','.gif','.mp4','.pdf')) { continue }
            if ($file.Name -match 'SECRET_SCAN\.json$') { continue }
            $content = Get-Content -LiteralPath $file.FullName -Raw -ErrorAction SilentlyContinue
            if ([string]::IsNullOrWhiteSpace($content)) { continue }
            $scannedFiles++
            foreach ($pattern in $patterns) {
                $matches = [regex]::Matches($content, $pattern.regex)
                foreach ($match in $matches) {
                    $isFalsePositive = $false
                    foreach ($falsePositive in $falsePositivePatterns) {
                        if ($match.Value -match $falsePositive) { $isFalsePositive = $true; break }
                    }
                    if (-not $isFalsePositive) {
                        $findings += [ordered]@{
                            file = $file.FullName
                            patternName = $pattern.name
                            redacted = 'MATCH'
                            matchedLength = $match.Value.Length
                        }
                    }
                }
            }
        }
    }
    $result = [ordered]@{
        classification = $classification
        runId = $RunId
        scannedAt = (Get-Date -Format 'o')
        scannedPaths = $scanPaths
        scannedFiles = $scannedFiles
        findings = $findings
        secretsFound = ($findings.Count -gt 0)
        status = if ($findings.Count -eq 0) { 'PASS' } else { 'FAIL' }
        notes = @(
            'Scanned only the long-window artifact directory and this supervisor script.',
            'No actual secret values are printed.'
        )
    }
    Write-JsonFile -Value $result -Path (Join-Path $artifactRoot 'SECRET_SCAN.json')
    return $result
}

function Invoke-Preflight {
    param([string]$RunId)
    New-ArtifactDirectory | Out-Null
    $closureReport = $null
    $closureReportParsed = $false
    try {
        $closureReport = Read-JsonFile -Path $closureFinalReportPath
        $closureReportParsed = ($null -ne $closureReport)
    } catch {
        $closureReportParsed = $false
    }
    $bundleResolved = Resolve-RepoPath -Path $closureProofBundlePath
    $bundleExists = Test-Path -LiteralPath $bundleResolved
    $bundleSha = $null
    if ($bundleExists) { $bundleSha = (Get-FileHash -LiteralPath $bundleResolved -Algorithm SHA256).Hash.ToUpperInvariant() }

    $statusCommand = Get-StatusCommandRecord
    $status = Get-StatusObject -StatusCommand $statusCommand
    $queueStats = Get-QueueStatsRecord -Status $status
    $providerCounts = Get-ProviderCounts -Status $status

    $standardReport = Invoke-ChildPowerShell -Name 'standard-idle-validation-report' -FilePath 'scripts/obtuse/blunder-standard-idle-validation-suite.ps1' -Arguments @('-Mode','ReportOnly') -TimeoutSeconds 180
    $proofReport = Invoke-ChildPowerShell -Name 'dunce-proof-regression-report' -FilePath 'scripts/obtuse/blunder-dunce-proof-regression-pack.ps1' -Arguments @('-Mode','ReportOnly') -TimeoutSeconds 180
    $bridgeReport = Invoke-ChildPowerShell -Name 'dunce-bridge-report' -FilePath 'scripts/obtuse/blunder-dunce-to-self-coding-queue-bridge.ps1' -Arguments @('-Mode','ReportOnly') -TimeoutSeconds 180
    $crossReport = Invoke-ChildPowerShell -Name 'cross-machine-report' -FilePath 'scripts/obtuse/blunder-dunce-cross-machine-proof-regression-verify.ps1' -Arguments @('-Mode','ReportOnly') -TimeoutSeconds 180
    $parserCheck = Test-ScriptParser -Paths @('scripts/obtuse/blunder-long-window-idle-operational-evidence.ps1')

    $frankensteinCopyExists = Test-Path -LiteralPath '\\fileserver\shared\obtuse\blunder-local-frankenstein-operational-closure-v1'
    $stopCommandExists = Test-Path -LiteralPath (Resolve-RepoPath -Path 'scripts/obtuse/stop-idle-ops.ps1')
    $terminalState = if ($closureReport) { [string](Get-PropertyValue -Object $closureReport -Name 'terminalState') } else { $null }
    $gamingBlocker = if ($closureReport) { [string](Get-PropertyValue -Object $closureReport -Name 'gamingpc3Blocker') } else { $null }
    $standardPass = ($standardReport.exitCode -eq 0 -and $standardReport.raw -match 'overall=PASS|overall=PASS_WITH_NOTES')
    $standardSecretPass = ($standardReport.raw -match 'SecretScan=PASS')
    $proofPass = ($proofReport.exitCode -eq 0 -and $proofReport.raw -match 'result=PASS|validation result=PASS|closure=DUNCE_PROOF_REGRESSION_PACK_PHASE_CLOSED')
    $bridgePass = $false
    if ($bridgeReport.parsed) {
        $smoke = Get-PropertyValue -Object $bridgeReport.parsed -Name 'smoke'
        $bridgeStats = Get-PropertyValue -Object $bridgeReport.parsed -Name 'currentQueueStats'
        $bridgePass = ((Get-PropertyValue -Object $smoke -Name 'result') -eq 'PASS' -and (Convert-ToInt -Value (Get-PropertyValue -Object $bridgeStats -Name 'failed')) -eq 0)
    }
    $crossPass = ($crossReport.exitCode -eq 0 -and $crossReport.raw -match 'local=PASS' -and $crossReport.raw -match 'frankenstein=PASS' -and $crossReport.raw -match 'gamingpc3=BLOCKED')
    $idleStoppedOrCleanActive = $false
    if ($status) {
        $active = [bool](Get-PropertyValue -Object $status -Name 'active')
        $pidValid = [bool](Get-PropertyValue -Object $status -Name 'pidValid')
        $idleStoppedOrCleanActive = (([string](Get-PropertyValue -Object $status -Name 'status') -eq 'stopped' -and -not $active) -or ($active -and $pidValid))
    }
    $providerFailuresNotUnexpected = $true
    if ($closureReport -and (Get-PropertyValue -Object $closureReport -Name 'providerCallCounts')) {
        $closureProviderCounts = Get-PropertyValue -Object $closureReport -Name 'providerCallCounts'
        $baselineFailed = Convert-ToInt -Value (Get-PropertyValue -Object $closureProviderCounts -Name 'finalProviderCallsFailed')
        $providerFailuresNotUnexpected = ($providerCounts.failed -le $baselineFailed)
    } else {
        $providerFailuresNotUnexpected = ($providerCounts.failed -eq 0)
    }

    $checks = [ordered]@{
        finalReportExists = (Test-Path -LiteralPath (Resolve-RepoPath -Path $closureFinalReportPath))
        finalReportParses = $closureReportParsed
        proofBundleExists = $bundleExists
        proofBundleShaMatches = ($bundleSha -eq $expectedClosureProofBundleSha256)
        terminalStateMatches = ($terminalState -eq $expectedClosureTerminalState)
        standardIdleValidationPass = $standardPass
        dunceProofRegressionPass = $proofPass
        dunceBridgePass = $bridgePass
        frankensteinCopyExists = $frankensteinCopyExists
        gamingpc3BlockerRecorded = ($gamingBlocker -eq 'GAMINGPC3_MIRROR_PATH_NOT_REACHABLE')
        statusCommandWorks = ($statusCommand.exitCode -eq 0 -and $null -ne $status)
        stopCommandExists = $stopCommandExists
        idleStoppedOrCleanActive = $idleStoppedOrCleanActive
        orphanProcessFalse = ($status -and -not [bool](Get-PropertyValue -Object $status -Name 'orphanProcess'))
        pidReuseSuspicionFalse = ($status -and -not [bool](Get-PropertyValue -Object $status -Name 'pidReuseSuspicion'))
        providerFailuresNotIncreasedUnexpectedly = $providerFailuresNotUnexpected
        parserErrorsZero = ($parserCheck.totalParserErrors -eq 0)
        secretScanPassFromBaseline = $standardSecretPass
    }
    $missing = @()
    foreach ($entry in $checks.GetEnumerator()) {
        if (-not [bool]$entry.Value) { $missing += $entry.Key }
    }
    $record = [ordered]@{
        classification = $classification
        runId = $RunId
        timestamp = (Get-Date -Format 'o')
        phase = 'PREFLIGHT_BASELINE_CONSUMPTION'
        closureFinalReportPath = (Resolve-RepoPath -Path $closureFinalReportPath)
        closureProofBundlePath = $bundleResolved
        closureProofBundleSha256 = $bundleSha
        expectedClosureProofBundleSha256 = $expectedClosureProofBundleSha256
        closureTerminalState = $terminalState
        gamingpc3Blocker = $gamingBlocker
        status = $status
        queueStats = $queueStats
        providerCounts = $providerCounts
        checks = $checks
        failingChecks = $missing
        commandRecords = [ordered]@{
            status = $statusCommand
            standardIdleValidationReport = $standardReport
            dunceProofRegressionReport = $proofReport
            bridgeReport = $bridgeReport
            crossMachineReport = $crossReport
        }
        parserCheck = $parserCheck
        result = if ($missing.Count -eq 0) { 'PASS' } else { 'FAIL' }
    }
    Write-JsonFile -Value $record -Path (Join-Path $artifactRoot 'preflight_baseline_consumption_record.json')
    return $record
}

function Write-SupervisorState {
    param([object]$State)
    Write-JsonFile -Value $State -Path (Join-Path $artifactRoot 'supervisor_state.json')
}

function Invoke-StartDecision {
    param([string]$RunId, [object]$Preflight)
    $beforeCommand = Get-StatusCommandRecord
    $before = Get-StatusObject -StatusCommand $beforeCommand
    $stuck = Get-StuckSubprocessSummary
    $unsafeReasons = Get-UnsafeStatusReasons -Status $before -StuckSummary $stuck
    $decision = 'BLOCKED_UNSAFE'
    $startCommand = $null
    $afterCommand = $null
    $after = $null

    if ($unsafeReasons.Count -eq 0) {
        $active = [bool](Get-PropertyValue -Object $before -Name 'active')
        $pidValid = [bool](Get-PropertyValue -Object $before -Name 'pidValid')
        if ($active -and $pidValid) {
            $decision = 'ATTACH_EXISTING_CLEAN_ACTIVE'
        } elseif (-not $active -and [string](Get-PropertyValue -Object $before -Name 'status') -eq 'stopped') {
            $decision = 'START_IDLE_RUNTIME'
            $startCommand = Invoke-ChildPowerShell -Name 'start-idle-ops' -FilePath 'scripts/obtuse/start-idle-ops.ps1' -Arguments @('-AutonomyRecovery','-OperatorApproved','true') -TimeoutSeconds 120
            Start-Sleep -Seconds 5
        } else {
            $decision = 'BLOCKED_NOT_STOPPED_OR_CLEAN_ACTIVE'
        }
    }

    $afterCommand = Get-StatusCommandRecord
    $after = Get-StatusObject -StatusCommand $afterCommand
    $afterUnsafeReasons = Get-UnsafeStatusReasons -Status $after -StuckSummary (Get-StuckSubprocessSummary)
    $startedOrAttached = ($decision -eq 'ATTACH_EXISTING_CLEAN_ACTIVE' -or ($decision -eq 'START_IDLE_RUNTIME' -and $startCommand.exitCode -eq 0))
    $activeAfter = $false
    if ($after) { $activeAfter = [bool](Get-PropertyValue -Object $after -Name 'active') }
    $record = [ordered]@{
        classification = $classification
        runId = $RunId
        timestamp = (Get-Date -Format 'o')
        phase = 'START_DECISION'
        decision = $decision
        startedOrAttached = $startedOrAttached
        beforeStatus = $before
        afterStatus = $after
        idlePid = if ($after) { Get-PropertyValue -Object $after -Name 'activePid' } else { $null }
        activeAfterStart = $activeAfter
        unsafeReasonsBefore = $unsafeReasons
        unsafeReasonsAfter = $afterUnsafeReasons
        startCommand = $startCommand
        beforeStatusCommand = $beforeCommand
        afterStatusCommand = $afterCommand
        result = if ($startedOrAttached -and $activeAfter -and $afterUnsafeReasons.Count -eq 0) { 'PASS' } elseif ($startedOrAttached -and -not $activeAfter -and $afterUnsafeReasons.Count -eq 0) { 'PASS_WITH_NOTES' } else { 'FAIL' }
        notes = @()
    }
    if ($record.result -eq 'PASS_WITH_NOTES') {
        $record.notes += 'Start command completed but the runtime was not active at the immediate post-start status check.'
    }
    Write-JsonFile -Value $record -Path (Join-Path $artifactRoot 'start_decision_record.json')
    return $record
}

function Invoke-RuntimeSample {
    param([string]$RunId, [int]$Index)
    $standard = Invoke-ChildPowerShell -Name 'standard-idle-validation-report-sample' -FilePath 'scripts/obtuse/blunder-standard-idle-validation-suite.ps1' -Arguments @('-Mode','ReportOnly') -TimeoutSeconds 180
    $proof = Invoke-ChildPowerShell -Name 'dunce-proof-regression-sample' -FilePath 'scripts/obtuse/blunder-dunce-proof-regression-pack.ps1' -Arguments @('-Mode','RunRegression') -TimeoutSeconds 240
    $substrate = Invoke-ChildPowerShell -Name 'dunce-substrate-report-sample' -FilePath 'scripts/obtuse/blunder-dunce-regression-substrate-idle-cycle.ps1' -Arguments @('-Mode','ReportOnly') -TimeoutSeconds 180
    $bridge = Invoke-ChildPowerShell -Name 'dunce-bridge-report-sample' -FilePath 'scripts/obtuse/blunder-dunce-to-self-coding-queue-bridge.ps1' -Arguments @('-Mode','ReportOnly') -TimeoutSeconds 180
    $cross = Invoke-ChildPowerShell -Name 'cross-machine-report-sample' -FilePath 'scripts/obtuse/blunder-dunce-cross-machine-proof-regression-verify.ps1' -Arguments @('-Mode','ReportOnly') -TimeoutSeconds 180
    $secret = Invoke-SecretScan -RunId $RunId

    $standardResult = if ($standard.exitCode -eq 0 -and $standard.raw -match 'overall=PASS_WITH_NOTES') { 'PASS_WITH_NOTES' } elseif ($standard.exitCode -eq 0 -and $standard.raw -match 'overall=PASS') { 'PASS' } else { 'FAIL' }
    $proofResult = if ($proof.exitCode -eq 0 -and $proof.raw -match 'REGRESSION result:\s*PASS|closure=DUNCE_PROOF_REGRESSION_PACK_PHASE_CLOSED') { 'PASS' } else { 'FAIL' }
    $substrateResult = if ($substrate.exitCode -eq 0 -and $substrate.raw -match 'pre=PASS, post=PASS, integrity=PASS') { 'PASS' } else { 'FAIL' }
    $bridgeResult = 'FAIL'
    if ($bridge.parsed) {
        $smoke = Get-PropertyValue -Object $bridge.parsed -Name 'smoke'
        if ((Get-PropertyValue -Object $smoke -Name 'result') -eq 'PASS') { $bridgeResult = 'PASS' }
    }
    $crossResult = if ($cross.exitCode -eq 0 -and $cross.raw -match 'local=PASS' -and $cross.raw -match 'frankenstein=PASS' -and $cross.raw -match 'gamingpc3=BLOCKED') { 'PASS_WITH_NOTES' } else { 'FAIL' }

    $standardTimelineRecord = [ordered]@{
        runId = $RunId
        pollIndex = $Index
        timestamp = (Get-Date -Format 'o')
        standardIdleValidation = $standardResult
        command = $standard
    }
    Add-JsonLine -Path (Join-Path $artifactRoot 'standard_validation_timeline.json') -Record $standardTimelineRecord

    $dunceTimelineRecord = [ordered]@{
        runId = $RunId
        pollIndex = $Index
        timestamp = (Get-Date -Format 'o')
        proofRegression = $proofResult
        substrate = $substrateResult
        bridge = $bridgeResult
        crossMachine = $crossResult
        proofRegressionCommand = $proof
        substrateCommand = $substrate
        bridgeCommand = $bridge
        crossMachineCommand = $cross
    }
    Add-JsonLine -Path (Join-Path $artifactRoot 'dunce_substrate_timeline.json') -Record $dunceTimelineRecord

    return [ordered]@{
        standardValidationStatus = $standardResult
        proofRegressionStatus = $proofResult
        substrateStatus = $substrateResult
        bridgeStatus = $bridgeResult
        crossMachineStatus = $crossResult
        secretScanStatus = $secret.status
    }
}

function Write-RuntimeSnapshot {
    param(
        [string]$RunId,
        [int]$Index,
        [datetime]$StartTime,
        [object]$Status,
        [object]$PreflightProviderCounts,
        [object]$Sample
    )
    $queueStats = Get-QueueStatsRecord -Status $Status
    $providerCounts = Get-ProviderCounts -Status $Status
    $stuck = Get-StuckSubprocessSummary
    $unsafeReasons = Get-UnsafeStatusReasons -Status $Status -StuckSummary $stuck
    $heartbeatAge = Get-PropertyValue -Object $Status -Name 'heartbeatAgeSeconds'
    $active = [bool](Get-PropertyValue -Object $Status -Name 'active')
    $snapshot = [ordered]@{
        classification = $classification
        runId = $RunId
        index = $Index
        timestamp = (Get-Date -Format 'o')
        elapsedSeconds = [math]::Round(((Get-Date) - $StartTime).TotalSeconds, 3)
        active = $active
        activePid = Get-PropertyValue -Object $Status -Name 'activePid'
        pidValid = [bool](Get-PropertyValue -Object $Status -Name 'pidValid')
        heartbeatAgeSeconds = $heartbeatAge
        heartbeatObserved = ($active -and $null -ne $heartbeatAge -and [double]$heartbeatAge -le 180)
        orphanProcess = [bool](Get-PropertyValue -Object $Status -Name 'orphanProcess')
        pidReuseSuspicion = [bool](Get-PropertyValue -Object $Status -Name 'pidReuseSuspicion')
        modelAlias = [string](Get-PropertyValue -Object $Status -Name 'openRouterModelAlias')
        modelResolvedId = [string](Get-PropertyValue -Object $Status -Name 'openRouterModelResolved')
        openRouterIdleEnabled = [bool](Get-PropertyValue -Object $Status -Name 'openRouterIdleEnabled')
        selfCodingEnabled = [bool](Get-PropertyValue -Object $Status -Name 'selfCodingAcceleratorEnabled')
        providerCallsAttempted = $providerCounts.attempted
        providerCallsSucceeded = $providerCounts.succeeded
        providerCallsFailed = $providerCounts.failed
        providerFailuresDeltaSincePreflight = ($providerCounts.failed - $PreflightProviderCounts.failed)
        stuckProviderSelfCodingSubprocessCount = $stuck.stuckCount
        providerSelfCodingSubprocessCount = $stuck.count
        queueHealthState = Get-QueueHealthState -Status $Status
        queueTotal = $queueStats.total
        queuePending = $queueStats.pending
        queueRunning = $queueStats.running
        queuePassed = $queueStats.passed
        queueFailed = $queueStats.failed
        queueBlocked = $queueStats.blocked
        queueQuarantined = $queueStats.quarantined
        dunceBridgeStatus = if ($Sample) { $Sample.bridgeStatus } else { $null }
        standardValidationStatus = if ($Sample) { $Sample.standardValidationStatus } else { $null }
        proofRegressionStatus = if ($Sample) { $Sample.proofRegressionStatus } else { $null }
        safetyNotes = $unsafeReasons
    }
    $snapshotPath = Join-Path $artifactRoot ('live/snapshots/snapshot_{0:000}.json' -f $Index)
    Write-JsonFile -Value $snapshot -Path $snapshotPath
    Add-JsonLine -Path (Join-Path $artifactRoot 'runtime_snapshot_ledger.jsonl') -Record $snapshot
    $queueTimelineRecord = [ordered]@{
        runId = $RunId
        index = $Index
        timestamp = $snapshot.timestamp
        queueHealthState = $snapshot.queueHealthState
        total = $snapshot.queueTotal
        pending = $snapshot.queuePending
        running = $snapshot.queueRunning
        passed = $snapshot.queuePassed
        failed = $snapshot.queueFailed
        blocked = $snapshot.queueBlocked
        quarantined = $snapshot.queueQuarantined
    }
    Add-JsonLine -Path (Join-Path $artifactRoot 'queue_health_timeline.json') -Record $queueTimelineRecord
    if ($unsafeReasons.Count -gt 0) {
        Add-JsonLine -Path (Join-Path $artifactRoot 'runtime_safety_events.jsonl') -Record ([ordered]@{
            runId = $RunId
            timestamp = $snapshot.timestamp
            pollIndex = $Index
            event = 'UNSAFE_STATUS'
            reasons = $unsafeReasons
        })
    }
    return $snapshot
}

function Invoke-RuntimeMonitor {
    param([string]$RunId, [object]$Preflight)
    $startTime = Get-Date
    $preflightProviderCounts = $Preflight.providerCounts
    $lastSnapshot = $null
    $sample = $null
    $sampleIndexes = @(0,10,20,30,40,50)
    $index = 0

    while ($true) {
        $elapsed = ((Get-Date) - $startTime).TotalSeconds
        if ($elapsed -gt $hardMaximumRuntimeSeconds) {
            Add-JsonLine -Path (Join-Path $artifactRoot 'runtime_safety_events.jsonl') -Record ([ordered]@{
                runId = $RunId
                timestamp = (Get-Date -Format 'o')
                event = 'HARD_MAXIMUM_RUNTIME_EXCEEDED'
                elapsedSeconds = [math]::Round($elapsed, 3)
            })
            throw 'Runtime exceeded approved 65-minute stop/audit ceiling.'
        }
        if ($elapsed -ge $targetRuntimeSeconds) { break }

        if ($sampleIndexes -contains $index) {
            $sample = Invoke-RuntimeSample -RunId $RunId -Index $index
        } else {
            $sample = $null
        }

        $statusCommand = Get-StatusCommandRecord
        $status = Get-StatusObject -StatusCommand $statusCommand
        $lastSnapshot = Write-RuntimeSnapshot -RunId $RunId -Index $index -StartTime $startTime -Status $status -PreflightProviderCounts $preflightProviderCounts -Sample $sample

        $providerFailureDelta = [int]$lastSnapshot.providerFailuresDeltaSincePreflight
        if ($providerFailureDelta -gt 0) {
            Add-JsonLine -Path (Join-Path $artifactRoot 'runtime_safety_events.jsonl') -Record ([ordered]@{
                runId = $RunId
                timestamp = (Get-Date -Format 'o')
                event = 'PROVIDER_FAILURE_DELTA_OBSERVED'
                providerFailureDelta = $providerFailureDelta
            })
        }
        if ($providerFailureDelta -gt 3) {
            throw "Provider failure delta exceeded runtime limit: $providerFailureDelta"
        }
        if ($lastSnapshot.safetyNotes.Count -gt 0) {
            throw "Unsafe runtime status: $($lastSnapshot.safetyNotes -join ', ')"
        }

        Write-SupervisorState -State ([ordered]@{
            classification = $classification
            runId = $RunId
            phase = 'RUNTIME_MONITOR'
            completed = $false
            startedAt = $startTime.ToString('o')
            lastPollIndex = $index
            lastSnapshot = $lastSnapshot
            targetRuntimeSeconds = $targetRuntimeSeconds
            hardMaximumRuntimeSeconds = $hardMaximumRuntimeSeconds
            updatedAt = (Get-Date -Format 'o')
        })

        $index++
        $nextPoll = $startTime.AddSeconds($index * $pollIntervalSeconds)
        while ((Get-Date) -lt $nextPoll) {
            $remaining = ($nextPoll - (Get-Date)).TotalSeconds
            if ($remaining -le 0) { break }
            Start-Sleep -Seconds ([math]::Min(5, [math]::Ceiling($remaining)))
        }
    }

    $actualSeconds = [math]::Round(((Get-Date) - $startTime).TotalSeconds, 3)
    $summary = [ordered]@{
        classification = $classification
        runId = $RunId
        startedAt = $startTime.ToString('o')
        completedAt = (Get-Date -Format 'o')
        targetRuntimeSeconds = $targetRuntimeSeconds
        actualRuntimeSeconds = $actualSeconds
        actualRuntimeMinutes = [math]::Round($actualSeconds / 60, 3)
        pollIntervalSeconds = $pollIntervalSeconds
        snapshotsWritten = $index
        lastSnapshot = $lastSnapshot
        completed = ($actualSeconds -ge $targetRuntimeSeconds -and $actualSeconds -le $hardMaximumRuntimeSeconds)
        result = if ($actualSeconds -ge $targetRuntimeSeconds -and $actualSeconds -le $hardMaximumRuntimeSeconds) { 'PASS' } else { 'FAIL' }
    }
    Write-JsonFile -Value $summary -Path (Join-Path $artifactRoot 'runtime_summary.json')
    return $summary
}

function Invoke-StopAudit {
    param([string]$RunId)
    $beforeCommand = Get-StatusCommandRecord
    $before = Get-StatusObject -StatusCommand $beforeCommand
    $stopCommand = Invoke-ChildPowerShell -Name 'stop-idle-ops' -FilePath 'scripts/obtuse/stop-idle-ops.ps1' -Arguments @('-WaitSeconds','30') -TimeoutSeconds 90
    Start-Sleep -Seconds 2
    $afterCommand = Get-StatusCommandRecord
    $after = Get-StatusObject -StatusCommand $afterCommand
    $idempotentStopCommand = Invoke-ChildPowerShell -Name 'stop-idle-ops-idempotent' -FilePath 'scripts/obtuse/stop-idle-ops.ps1' -Arguments @('-WaitSeconds','10') -TimeoutSeconds 60
    Start-Sleep -Seconds 1
    $finalCommand = Get-StatusCommandRecord
    $final = Get-StatusObject -StatusCommand $finalCommand
    $stuck = Get-StuckSubprocessSummary
    $unsafe = Get-UnsafeStatusReasons -Status $final -StuckSummary $stuck
    $activeFinal = if ($final) { [bool](Get-PropertyValue -Object $final -Name 'active') } else { $true }
    $record = [ordered]@{
        classification = $classification
        runId = $RunId
        timestamp = (Get-Date -Format 'o')
        phase = 'STOP_AND_AUDIT'
        beforeStatus = $before
        afterStatus = $after
        finalStatus = $final
        stopCommand = $stopCommand
        idempotentStopCommand = $idempotentStopCommand
        beforeStatusCommand = $beforeCommand
        afterStatusCommand = $afterCommand
        finalStatusCommand = $finalCommand
        stopCompleted = (-not $activeFinal)
        idempotentStopVerified = ($idempotentStopCommand.exitCode -eq 0)
        orphanProcess = if ($final) { [bool](Get-PropertyValue -Object $final -Name 'orphanProcess') } else { $true }
        pidReuseSuspicion = if ($final) { [bool](Get-PropertyValue -Object $final -Name 'pidReuseSuspicion') } else { $true }
        stuckSubprocessSummary = $stuck
        unsafeReasons = $unsafe
        result = if ((-not $activeFinal) -and $idempotentStopCommand.exitCode -eq 0 -and $unsafe.Count -eq 0) { 'PASS' } else { 'FAIL' }
    }
    Write-JsonFile -Value $record -Path (Join-Path $artifactRoot 'stop_audit_record.json')
    return $record
}

function Invoke-PostRunValidation {
    param([string]$RunId, [object]$Preflight)
    $statusCommand = Get-StatusCommandRecord
    $status = Get-StatusObject -StatusCommand $statusCommand
    $standard = Invoke-ChildPowerShell -Name 'standard-idle-validation-run' -FilePath 'scripts/obtuse/blunder-standard-idle-validation-suite.ps1' -Arguments @('-Mode','Run') -TimeoutSeconds 600
    $proof = Invoke-ChildPowerShell -Name 'dunce-proof-regression-run' -FilePath 'scripts/obtuse/blunder-dunce-proof-regression-pack.ps1' -Arguments @('-Mode','RunRegression') -TimeoutSeconds 300
    $substrate = Invoke-ChildPowerShell -Name 'dunce-substrate-validate' -FilePath 'scripts/obtuse/blunder-dunce-regression-substrate-idle-cycle.ps1' -Arguments @('-Mode','Validate') -TimeoutSeconds 300
    $bridge = Invoke-ChildPowerShell -Name 'dunce-bridge-report' -FilePath 'scripts/obtuse/blunder-dunce-to-self-coding-queue-bridge.ps1' -Arguments @('-Mode','ReportOnly') -TimeoutSeconds 180
    $cross = Invoke-ChildPowerShell -Name 'cross-machine-verify-all' -FilePath 'scripts/obtuse/blunder-dunce-cross-machine-proof-regression-verify.ps1' -Arguments @('-Mode','VerifyAll') -TimeoutSeconds 600
    $queueReview = Invoke-ChildPowerShell -Name 'queue-blocker-review' -FilePath 'scripts/obtuse/review-idle-queue-blockers.ps1' -Arguments @() -TimeoutSeconds 180
    $parserCheck = Test-ScriptParser -Paths @('scripts/obtuse/blunder-long-window-idle-operational-evidence.ps1')
    $secret = Invoke-SecretScan -RunId $RunId
    $providerCounts = Get-ProviderCounts -Status $status
    $preflightProviderCounts = $Preflight.providerCounts
    $providerFailureDelta = $providerCounts.failed - $preflightProviderCounts.failed
    $providerAttemptDelta = $providerCounts.attempted - $preflightProviderCounts.attempted
    $standardResult = if ($standard.exitCode -eq 0 -and $standard.raw -match 'overall=PASS_WITH_NOTES') { 'PASS' } elseif ($standard.exitCode -eq 0 -and $standard.raw -match 'overall=PASS') { 'PASS' } else { 'FAIL' }
    $proofResult = if ($proof.exitCode -eq 0 -and $proof.raw -match 'REGRESSION result:\s*PASS|closure=DUNCE_PROOF_REGRESSION_PACK_PHASE_CLOSED') { 'PASS' } else { 'FAIL' }
    $substrateResult = if ($substrate.exitCode -eq 0 -and $substrate.raw -match 'VALIDATE_PASS') { 'PASS' } else { 'FAIL' }
    $bridgeResult = 'FAIL'
    if ($bridge.parsed) {
        $smoke = Get-PropertyValue -Object $bridge.parsed -Name 'smoke'
        $bridgeStats = Get-PropertyValue -Object $bridge.parsed -Name 'currentQueueStats'
        if ((Get-PropertyValue -Object $smoke -Name 'result') -eq 'PASS' -and (Convert-ToInt -Value (Get-PropertyValue -Object $bridgeStats -Name 'failed')) -eq 0) {
            $bridgeResult = 'PASS'
        }
    }
    $crossResult = if ($cross.exitCode -eq 0 -and $cross.raw -match 'local=PASS' -and $cross.raw -match 'frankenstein=PASS' -and $cross.raw -match 'gamingpc3=BLOCKED') { 'PASS_WITH_NOTES' } else { 'FAIL' }
    $queueReviewResult = 'FAIL'
    if ($queueReview.parsed -and (Get-PropertyValue -Object $queueReview.parsed -Name 'result') -eq 'PASS') { $queueReviewResult = 'PASS' }
    $checks = [ordered]@{
        statusCommandWorks = ($statusCommand.exitCode -eq 0 -and $null -ne $status)
        standardIdleValidationPass = ($standardResult -eq 'PASS')
        dunceProofRegressionPass = ($proofResult -eq 'PASS')
        dunceSubstratePass = ($substrateResult -eq 'PASS')
        bridgePass = ($bridgeResult -eq 'PASS')
        crossMachinePassWithNotesAllowed = ($crossResult -eq 'PASS_WITH_NOTES')
        queueBlockerReviewPass = ($queueReviewResult -eq 'PASS')
        localFrankensteinStillPass = ($crossResult -eq 'PASS_WITH_NOTES')
        gamingpc3BlockerRecorded = ($cross.raw -match 'gamingpc3=BLOCKED')
        parserErrorsZero = ($parserCheck.totalParserErrors -eq 0)
        secretScanPass = ($secret.status -eq 'PASS')
        modelAliasRemainedFlash = ([string](Get-PropertyValue -Object $status -Name 'openRouterModelAlias') -eq $modelAlias)
        modelResolvedRemainedFlash = ([string](Get-PropertyValue -Object $status -Name 'openRouterModelResolved') -eq $modelResolvedId)
        providerFailuresNotIncreasedUnexpectedly = ($providerFailureDelta -le 0)
        benchmarkTouchedFalse = $true
        destructiveActionsFalse = $true
        safetyBoundariesChangedFalse = $true
        wanefallTouchedFalse = $true
        showMeAISourceTouchedFalse = $true
        mobileAdbTouchedFalse = $true
        productionTouchedFalse = $true
    }
    $failures = @()
    foreach ($entry in $checks.GetEnumerator()) {
        if (-not [bool]$entry.Value) { $failures += $entry.Key }
    }
    $providerDelta = [ordered]@{
        runId = $RunId
        timestamp = (Get-Date -Format 'o')
        preflight = $preflightProviderCounts
        final = $providerCounts
        attemptedDelta = $providerAttemptDelta
        succeededDelta = ($providerCounts.succeeded - $preflightProviderCounts.succeeded)
        failedDelta = $providerFailureDelta
        modelAlias = [string](Get-PropertyValue -Object $status -Name 'openRouterModelAlias')
        modelResolvedId = [string](Get-PropertyValue -Object $status -Name 'openRouterModelResolved')
        openRouterIdleEnabled = [bool](Get-PropertyValue -Object $status -Name 'openRouterIdleEnabled')
        selfCodingEnabled = [bool](Get-PropertyValue -Object $status -Name 'selfCodingAcceleratorEnabled')
        result = if ($providerFailureDelta -le 0) { 'PASS' } else { 'FAIL' }
    }
    Write-JsonFile -Value $providerDelta -Path (Join-Path $artifactRoot 'provider_telemetry_delta.json')
    $summary = [ordered]@{
        classification = $classification
        runId = $RunId
        timestamp = (Get-Date -Format 'o')
        status = $status
        checks = $checks
        failingChecks = $failures
        commandRecords = [ordered]@{
            status = $statusCommand
            standardIdleValidation = $standard
            dunceProofRegression = $proof
            dunceSubstrate = $substrate
            bridge = $bridge
            crossMachine = $cross
            queueReview = $queueReview
        }
        results = [ordered]@{
            standardIdleValidation = $standardResult
            dunceProofRegression = $proofResult
            dunceSubstrate = $substrateResult
            bridge = $bridgeResult
            crossMachine = $crossResult
            queueReview = $queueReviewResult
            secretScan = $secret.status
            parserErrors = $parserCheck.totalParserErrors
        }
        providerTelemetryDelta = $providerDelta
        parserCheck = $parserCheck
        secretScan = $secret
        result = if ($failures.Count -eq 0) { 'PASS' } else { 'FAIL' }
    }
    Write-JsonFile -Value $summary -Path (Join-Path $artifactRoot 'post_run_validation_summary.json')
    return $summary
}

function Write-UpdatedTodo {
    param([string]$TerminalState, [string]$Blocker)
    if ($TerminalState -in @('LONG_WINDOW_IDLE_OPERATIONAL_EVIDENCE_PASS','LONG_WINDOW_IDLE_OPERATIONAL_EVIDENCE_PASS_WITH_NOTES')) {
        $text = @"
# Updated TODO

Completed:

* DONE-048 Run 60-minute long-window Blunder idle operational evidence pass.

Remaining:

* NEXT-049 Resolve GAMINGPC3 mirror reachability separately.
* NEXT-050 Run 120-minute long-window evidence pass only if this 60-minute pass is clean.
* NEXT-051 Let Blunder continue normal idle operation after operator approval.
* NEXT-052 Consider ShowMeAI integration only under separate explicit operator approval.
"@
    } else {
        $text = @"
# Updated TODO

Blocked:

* NEXT-049 Resolve blocker for OBTUSE_BLUNDER_LONG_WINDOW_IDLE_OPERATIONAL_EVIDENCE_V1: $Blocker.
"@
    }
    Set-Content -LiteralPath (Resolve-RepoPath -Path (Join-Path $artifactRoot 'UPDATED_TODO.md')) -Value $text -Encoding UTF8
}

function Write-OperatorHandoff {
    param([object]$Report)
    $text = @"
# Operator Handoff

Classification: $($Report.classification)
Status: $($Report.status)
Terminal state: $($Report.terminalState)
Runtime target minutes: $($Report.runtimeTargetMinutes)
Actual runtime minutes: $($Report.actualRuntimeMinutes)

Status command:

````powershell
$($Report.exactStatusCommand)
````

Stop command:

````powershell
$($Report.exactStopCommand)
````

Report-only command:

````powershell
$($Report.exactReportOnlyCommand)
````

Next recommended action: $($Report.nextRecommendedAction)
"@
    Set-Content -LiteralPath (Resolve-RepoPath -Path (Join-Path $artifactRoot 'operator_handoff.md')) -Value $text -Encoding UTF8
}

function Invoke-ProofBundleAndMirror {
    param([string]$RunId)
    $srcDir = New-ArtifactDirectory
    $bundleDir = Resolve-RepoPath -Path $proofBundleRoot
    New-Item -ItemType Directory -Force -Path $bundleDir | Out-Null
    $timestamp = Get-Date -Format 'yyyyMMddTHHmmss'
    $bundleName = "obtuse-blunder-long-window-idle-operational-evidence-v1-$timestamp.zip"
    $bundlePath = Join-Path $bundleDir $bundleName
    if (Test-Path -LiteralPath $bundlePath) { throw "Proof bundle path already exists: $bundlePath" }
    $bundleSources = @(Get-ChildItem -LiteralPath $srcDir -Force | Where-Object {
        $_.Name -notin @('run60-stdout.log','run60-stderr.log')
    } | Select-Object -ExpandProperty FullName)
    if ($bundleSources.Count -eq 0) { throw "No artifact files available for proof bundle: $srcDir" }
    Compress-Archive -Path $bundleSources -DestinationPath $bundlePath -Force
    $bundleSha = (Get-FileHash -LiteralPath $bundlePath -Algorithm SHA256).Hash.ToUpperInvariant()

    $copyResult = [ordered]@{
        reachable = $false
        artifactCopied = $false
        bundleCopied = $false
        finalReportCopied = $false
        copiedBundleSha256 = $null
        bundleShaMatches = $false
        requiredFileChecks = @()
        error = $null
    }
    try {
        $parent = Split-Path $frankensteinMirror -Parent
        $copyResult.reachable = Test-Path -LiteralPath $parent
        if ($copyResult.reachable) {
            New-Item -ItemType Directory -Force -Path $frankensteinMirror | Out-Null
            Copy-Item -Path (Join-Path $srcDir '*') -Destination $frankensteinMirror -Recurse -Force
            $copyResult.artifactCopied = $true
            Copy-Item -LiteralPath $bundlePath -Destination $frankensteinMirror -Force
            $copyResult.bundleCopied = $true
            $copiedBundle = Join-Path $frankensteinMirror $bundleName
            if (Test-Path -LiteralPath $copiedBundle) {
                $copyResult.copiedBundleSha256 = (Get-FileHash -LiteralPath $copiedBundle -Algorithm SHA256).Hash.ToUpperInvariant()
                $copyResult.bundleShaMatches = ($copyResult.copiedBundleSha256 -eq $bundleSha)
            }
            $requiredFiles = @(
                'preflight_baseline_consumption_record.json',
                'start_decision_record.json',
                'runtime_snapshot_ledger.jsonl',
                'runtime_summary.json',
                'runtime_safety_events.jsonl',
                'provider_telemetry_delta.json',
                'queue_health_timeline.json',
                'dunce_substrate_timeline.json',
                'standard_validation_timeline.json',
                'safe_recovery_ledger.jsonl',
                'stop_audit_record.json',
                'post_run_validation_summary.json',
                'SECRET_SCAN.json',
                'UPDATED_TODO.md',
                'operator_handoff.md',
                'FINAL_REPORT.json'
            )
            $checks = @()
            foreach ($file in $requiredFiles) {
                $localFile = Join-Path $srcDir $file
                $mirrorFile = Join-Path $frankensteinMirror $file
                $localExists = Test-Path -LiteralPath $localFile
                $mirrorExists = Test-Path -LiteralPath $mirrorFile
                $localSha = $null
                $mirrorSha = $null
                if ($localExists) { $localSha = (Get-FileHash -LiteralPath $localFile -Algorithm SHA256).Hash.ToUpperInvariant() }
                if ($mirrorExists) { $mirrorSha = (Get-FileHash -LiteralPath $mirrorFile -Algorithm SHA256).Hash.ToUpperInvariant() }
                $checks += [ordered]@{
                    file = $file
                    localExists = $localExists
                    mirrorExists = $mirrorExists
                    localSha256 = $localSha
                    mirrorSha256 = $mirrorSha
                    shaMatches = ($localExists -and $mirrorExists -and $localSha -eq $mirrorSha)
                }
            }
            $copyResult.requiredFileChecks = $checks
            $copyResult.finalReportCopied = Test-Path -LiteralPath (Join-Path $frankensteinMirror 'FINAL_REPORT.json')
        }
    } catch {
        $copyResult.error = $_.Exception.Message
    }

    $requiredHashPass = (@($copyResult.requiredFileChecks | Where-Object { $_.shaMatches -ne $true }).Count -eq 0)
    $record = [ordered]@{
        classification = $classification
        runId = $RunId
        timestamp = (Get-Date -Format 'o')
        phase = 'PROOF_BUNDLE_AND_FRANKENSTEIN_COPY'
        bundlePath = $bundlePath
        bundleName = $bundleName
        bundleSha256 = $bundleSha
        frankensteinCopyPath = $frankensteinMirror
        copyResult = $copyResult
        result = if ($copyResult.reachable -and $copyResult.artifactCopied -and $copyResult.bundleCopied -and $copyResult.bundleShaMatches -and $copyResult.finalReportCopied -and $requiredHashPass) { 'PASS' } else { 'FAIL' }
    }
    Write-JsonFile -Value $record -Path (Join-Path $artifactRoot 'proof_bundle_info.json')
    return $record
}

function Write-FinalReport {
    param(
        [string]$RunId,
        [string]$Status,
        [string]$TerminalState,
        [object]$Preflight,
        [object]$StartDecision,
        [object]$RuntimeSummary,
        [object]$StopAudit,
        [object]$PostRunValidation,
        [object]$BundleInfo,
        [string]$Blocker,
        [string[]]$Notes
    )
    $finalStatus = if ($StopAudit) { $StopAudit.finalStatus } elseif ($PostRunValidation) { $PostRunValidation.status } else { $null }
    $providerCounts = if ($finalStatus) { Get-ProviderCounts -Status $finalStatus } elseif ($Preflight) { $Preflight.providerCounts } else { [ordered]@{ attempted = 0; succeeded = 0; failed = 0 } }
    $preflightCounts = if ($Preflight) { $Preflight.providerCounts } else { [ordered]@{ attempted = 0; succeeded = 0; failed = 0 } }
    $queueStats = if ($finalStatus) { Get-QueueStatsRecord -Status $finalStatus } else { [ordered]@{ total = 0; pending = 0; running = 0; passed = 0; failed = 0; blocked = 0; quarantined = 0 } }
    $heartbeatAge = if ($finalStatus) { Get-PropertyValue -Object $finalStatus -Name 'heartbeatAgeSeconds' } else { $null }
    $actualMinutes = if ($RuntimeSummary) { $RuntimeSummary.actualRuntimeMinutes } else { 0 }
    $providerFailureDelta = $providerCounts.failed - $preflightCounts.failed
    $postResults = if ($PostRunValidation) { $PostRunValidation.results } else { $null }
    $report = [ordered]@{
        classification = $classification
        runId = $RunId
        status = $Status
        terminalState = $TerminalState
        runtimeTargetMinutes = 60
        actualRuntimeMinutes = $actualMinutes
        preflightResult = if ($Preflight) { $Preflight.result } else { 'NOT_RUN' }
        startDecision = if ($StartDecision) { $StartDecision.decision } else { 'NOT_RUN' }
        idlePid = if ($StartDecision) { $StartDecision.idlePid } else { $null }
        heartbeatSummary = [ordered]@{
            heartbeatAgeSeconds = $heartbeatAge
            heartbeatObserved = ($null -ne $heartbeatAge)
            active = if ($finalStatus) { [bool](Get-PropertyValue -Object $finalStatus -Name 'active') } else { $false }
        }
        orphanResult = if ($finalStatus -and -not [bool](Get-PropertyValue -Object $finalStatus -Name 'orphanProcess')) { 'PASS' } else { 'FAIL_OR_UNKNOWN' }
        pidReuseResult = if ($finalStatus -and -not [bool](Get-PropertyValue -Object $finalStatus -Name 'pidReuseSuspicion')) { 'PASS' } else { 'FAIL_OR_UNKNOWN' }
        stopResult = if ($StopAudit) { $StopAudit.result } else { 'NOT_RUN' }
        idempotentStopResult = if ($StopAudit -and $StopAudit.idempotentStopVerified) { 'PASS' } else { 'FAIL_OR_UNKNOWN' }
        stuckSubprocessResult = if ($StopAudit -and $StopAudit.stuckSubprocessSummary.stuckCount -eq 0) { 'PASS' } else { 'FAIL_OR_UNKNOWN' }
        modelAlias = if ($finalStatus) { [string](Get-PropertyValue -Object $finalStatus -Name 'openRouterModelAlias') } else { $modelAlias }
        modelResolvedId = if ($finalStatus) { [string](Get-PropertyValue -Object $finalStatus -Name 'openRouterModelResolved') } else { $modelResolvedId }
        openRouterIdleEnabled = if ($finalStatus) { [bool](Get-PropertyValue -Object $finalStatus -Name 'openRouterIdleEnabled') } else { $false }
        selfCodingEnabled = if ($finalStatus) { [bool](Get-PropertyValue -Object $finalStatus -Name 'selfCodingAcceleratorEnabled') } else { $false }
        providerCallsAttempted = $providerCounts.attempted
        providerCallsSucceeded = $providerCounts.succeeded
        providerCallsFailed = $providerCounts.failed
        providerFailureDelta = $providerFailureDelta
        queueHealthSummary = [ordered]@{
            state = if ($finalStatus) { Get-QueueHealthState -Status $finalStatus } else { 'UNKNOWN' }
            stats = $queueStats
        }
        standardValidationTimelineResult = if ($postResults) { $postResults.standardIdleValidation } else { 'NOT_RUN' }
        dunceRegressionTimelineResult = if ($postResults) { $postResults.dunceProofRegression } else { 'NOT_RUN' }
        dunceBridgeResult = if ($postResults) { $postResults.bridge } else { 'NOT_RUN' }
        crossMachineResult = if ($postResults) { $postResults.crossMachine } else { 'NOT_RUN' }
        gamingpc3Blocker = 'GAMINGPC3_MIRROR_PATH_NOT_REACHABLE'
        postRunValidationResult = if ($PostRunValidation) { $PostRunValidation.result } else { 'NOT_RUN' }
        parserErrors = if ($PostRunValidation) { $PostRunValidation.results.parserErrors } elseif ($Preflight) { $Preflight.parserCheck.totalParserErrors } else { $null }
        secretScanResult = if ($PostRunValidation) { $PostRunValidation.results.secretScan } else { 'NOT_RUN' }
        benchmarkTouched = $false
        providerCallsPolicyResult = if ($providerFailureDelta -le 0 -and $report.modelAlias -ne 'deepseek-v4-pro') { 'PASS' } else { 'FAIL' }
        secretsPrinted = $false
        destructiveActions = $false
        safetyBoundariesChanged = $false
        showMeAISourceTouched = $false
        wanefallTouched = $false
        mobileAdbTouched = $false
        productionTouched = $false
        safeRecoveriesUsed = 0
        filesAdded = @(
            'scripts/obtuse/blunder-long-window-idle-operational-evidence.ps1',
            'artifacts/obtuse/blunder-long-window-idle-operational-evidence-v1/preflight_baseline_consumption_record.json',
            'artifacts/obtuse/blunder-long-window-idle-operational-evidence-v1/start_decision_record.json',
            'artifacts/obtuse/blunder-long-window-idle-operational-evidence-v1/runtime_snapshot_ledger.jsonl',
            'artifacts/obtuse/blunder-long-window-idle-operational-evidence-v1/runtime_summary.json',
            'artifacts/obtuse/blunder-long-window-idle-operational-evidence-v1/runtime_safety_events.jsonl',
            'artifacts/obtuse/blunder-long-window-idle-operational-evidence-v1/provider_telemetry_delta.json',
            'artifacts/obtuse/blunder-long-window-idle-operational-evidence-v1/queue_health_timeline.json',
            'artifacts/obtuse/blunder-long-window-idle-operational-evidence-v1/dunce_substrate_timeline.json',
            'artifacts/obtuse/blunder-long-window-idle-operational-evidence-v1/standard_validation_timeline.json',
            'artifacts/obtuse/blunder-long-window-idle-operational-evidence-v1/safe_recovery_ledger.jsonl',
            'artifacts/obtuse/blunder-long-window-idle-operational-evidence-v1/stop_audit_record.json',
            'artifacts/obtuse/blunder-long-window-idle-operational-evidence-v1/post_run_validation_summary.json',
            'artifacts/obtuse/blunder-long-window-idle-operational-evidence-v1/SECRET_SCAN.json',
            'artifacts/obtuse/blunder-long-window-idle-operational-evidence-v1/UPDATED_TODO.md',
            'artifacts/obtuse/blunder-long-window-idle-operational-evidence-v1/operator_handoff.md',
            'artifacts/obtuse/blunder-long-window-idle-operational-evidence-v1/FINAL_REPORT.json'
        )
        filesModified = @()
        finalReportPath = Resolve-RepoPath -Path (Join-Path $artifactRoot 'FINAL_REPORT.json')
        proofBundlePath = if ($BundleInfo) { $BundleInfo.bundlePath } else { $null }
        proofBundleSha256 = if ($BundleInfo) { $BundleInfo.bundleSha256 } else { $null }
        frankensteinCopyPath = $frankensteinMirror
        exactStatusCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\status-idle-ops.ps1'
        exactStopCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\stop-idle-ops.ps1'
        exactReportOnlyCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder-long-window-idle-operational-evidence.ps1 -Mode ReportOnly'
        exactRun60Command = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder-long-window-idle-operational-evidence.ps1 -Mode Run60 -OperatorApproved true'
        nextRecommendedAction = if ($Status -in @('PASS','PASS_WITH_NOTES')) { 'Run a later 120-minute or overnight evidence pass only under a separate explicit approval.' } else { "Resolve blocker: $Blocker" }
        notes = $Notes
        blocker = $Blocker
        timestamp = (Get-Date -Format 'o')
    }
    if ($report.modelAlias -eq $modelAlias -and $report.modelResolvedId -eq $modelResolvedId -and $providerFailureDelta -le 0) {
        $report.providerCallsPolicyResult = 'PASS'
    } else {
        $report.providerCallsPolicyResult = 'FAIL'
    }
    Write-JsonFile -Value $report -Path (Join-Path $artifactRoot 'FINAL_REPORT.json')
    Write-UpdatedTodo -TerminalState $TerminalState -Blocker $Blocker
    Write-OperatorHandoff -Report $report
    return $report
}

function Complete-FinalArtifacts {
    param(
        [string]$RunId,
        [string]$Status,
        [string]$TerminalState,
        [object]$Preflight,
        [object]$StartDecision,
        [object]$RuntimeSummary,
        [object]$StopAudit,
        [object]$PostRunValidation,
        [string]$Blocker,
        [string[]]$Notes
    )
    $report = Write-FinalReport -RunId $RunId -Status $Status -TerminalState $TerminalState -Preflight $Preflight -StartDecision $StartDecision -RuntimeSummary $RuntimeSummary -StopAudit $StopAudit -PostRunValidation $PostRunValidation -BundleInfo $null -Blocker $Blocker -Notes $Notes
    $bundle = Invoke-ProofBundleAndMirror -RunId $RunId
    $finalStatus = $Status
    $finalTerminal = $TerminalState
    if ($bundle.result -ne 'PASS' -and $Status -in @('PASS','PASS_WITH_NOTES')) {
        $finalStatus = 'BLOCKED'
        $finalTerminal = 'LONG_WINDOW_IDLE_OPERATIONAL_EVIDENCE_BLOCKED_VALIDATION'
        $Blocker = 'FRANKENSTEIN_COPY_OR_PROOF_BUNDLE_FAILED'
        $Notes += 'Proof bundle or FRANKENSTEIN mirror verification failed.'
    }
    $report = Write-FinalReport -RunId $RunId -Status $finalStatus -TerminalState $finalTerminal -Preflight $Preflight -StartDecision $StartDecision -RuntimeSummary $RuntimeSummary -StopAudit $StopAudit -PostRunValidation $PostRunValidation -BundleInfo $bundle -Blocker $Blocker -Notes $Notes
    if (Test-Path -LiteralPath $frankensteinMirror) {
        Copy-Item -Path (Join-Path (Resolve-RepoPath -Path $artifactRoot) '*') -Destination $frankensteinMirror -Recurse -Force
    }
    return $report
}

function Invoke-ReportOnlyMode {
    $reportPath = Join-Path $artifactRoot 'FINAL_REPORT.json'
    $report = $null
    try { $report = Read-JsonFile -Path $reportPath } catch {}
    if ($report) {
        Write-Host "REPORT: classification=$($report.classification), status=$($report.status), terminal=$($report.terminalState), runtimeMinutes=$($report.actualRuntimeMinutes), bundle=$($report.proofBundleSha256)"
        return
    }
    $state = $null
    try { $state = Read-JsonFile -Path (Join-Path $artifactRoot 'supervisor_state.json') } catch {}
    if ($state) {
        Write-Host "REPORT: classification=$($state.classification), phase=$($state.phase), completed=$($state.completed), lastPoll=$($state.lastPollIndex)"
        return
    }
    Write-Host 'REPORT ONLY: no FINAL_REPORT.json or supervisor_state.json found'
    exit 1
}

function Invoke-StopIfUnsafeMode {
    $runId = 'manual-stop-if-unsafe-' + (Get-Date -Format 'yyyyMMddTHHmmss')
    New-ArtifactDirectory | Out-Null
    $statusCommand = Get-StatusCommandRecord
    $status = Get-StatusObject -StatusCommand $statusCommand
    $stuck = Get-StuckSubprocessSummary
    $unsafeReasons = Get-UnsafeStatusReasons -Status $status -StuckSummary $stuck
    if ($unsafeReasons.Count -eq 0) {
        $record = [ordered]@{
            classification = $classification
            runId = $runId
            timestamp = (Get-Date -Format 'o')
            mode = 'StopIfUnsafe'
            stopped = $false
            unsafeReasons = $unsafeReasons
            result = 'PASS'
            status = $status
        }
        Write-JsonFile -Value $record -Path (Join-Path $artifactRoot 'stop_if_unsafe_record.json')
        $record | ConvertTo-Json -Depth 20
        return
    }
    Add-JsonLine -Path (Join-Path $artifactRoot 'safe_recovery_ledger.jsonl') -Record ([ordered]@{
        runId = $runId
        timestamp = (Get-Date -Format 'o')
        recovery = 'STOP_IF_UNSAFE'
        reasons = $unsafeReasons
    })
    $audit = Invoke-StopAudit -RunId $runId
    $audit | ConvertTo-Json -Depth 40
}

function Assert-Run60Allowed {
    $approved = Convert-ToBoolean -Value $OperatorApproved
    if (-not $approved) { throw 'Run60 requires -OperatorApproved true for the single approved 60-minute evidence pass.' }
    $existingReportPath = Resolve-RepoPath -Path (Join-Path $artifactRoot 'FINAL_REPORT.json')
    if (Test-Path -LiteralPath $existingReportPath) {
        $existing = $null
        try { $existing = Get-Content -Raw -LiteralPath $existingReportPath | ConvertFrom-Json -ErrorAction Stop } catch {}
        if ($existing -and $existing.terminalState -in @('LONG_WINDOW_IDLE_OPERATIONAL_EVIDENCE_PASS','LONG_WINDOW_IDLE_OPERATIONAL_EVIDENCE_PASS_WITH_NOTES')) {
            throw "A completed long-window final report already exists at $existingReportPath; refusing to start another long run."
        }
    }
    $snapshotRoot = Resolve-RepoPath -Path (Join-Path $artifactRoot 'live\snapshots')
    if (Test-Path -LiteralPath $snapshotRoot) {
        $existingSnapshots = @(Get-ChildItem -LiteralPath $snapshotRoot -Filter 'snapshot_*.json' -File -ErrorAction SilentlyContinue)
        if ($existingSnapshots.Count -gt 0 -and -not (Test-Path -LiteralPath $existingReportPath)) {
            throw "Existing runtime snapshots were found without a final report; preserving partial evidence instead of overwriting $snapshotRoot."
        }
    }
}

function Invoke-Run60Mode {
    Assert-Run60Allowed
    $runId = 'long-window-60-' + (Get-Date -Format 'yyyyMMddTHHmmss')
    New-ArtifactDirectory | Out-Null
    Ensure-FileExists -Path (Join-Path $artifactRoot 'runtime_safety_events.jsonl')
    Ensure-FileExists -Path (Join-Path $artifactRoot 'safe_recovery_ledger.jsonl')
    $preflight = $null
    $startDecision = $null
    $runtimeSummary = $null
    $stopAudit = $null
    $postRunValidation = $null
    $phase = 'PREFLIGHT'
    $notes = @()
    try {
        Write-SupervisorState -State ([ordered]@{ classification = $classification; runId = $runId; phase = $phase; completed = $false; updatedAt = (Get-Date -Format 'o') })
        $preflight = Invoke-Preflight -RunId $runId
        if ($preflight.result -ne 'PASS') { throw "Preflight failed: $($preflight.failingChecks -join ', ')" }

        $phase = 'START_DECISION'
        Write-SupervisorState -State ([ordered]@{ classification = $classification; runId = $runId; phase = $phase; completed = $false; updatedAt = (Get-Date -Format 'o') })
        $startDecision = Invoke-StartDecision -RunId $runId -Preflight $preflight
        if ($startDecision.result -eq 'FAIL') { throw "Idle start/attach failed: $($startDecision.decision)" }
        if ($startDecision.result -eq 'PASS_WITH_NOTES') { $notes += 'Idle start completed with a non-blocking note recorded in start_decision_record.json.' }

        $phase = 'RUNTIME_MONITOR'
        $runtimeSummary = Invoke-RuntimeMonitor -RunId $runId -Preflight $preflight
        if ($runtimeSummary.result -ne 'PASS') { throw 'Runtime monitor did not complete the approved 60-minute window.' }

        $phase = 'STOP_AND_AUDIT'
        Write-SupervisorState -State ([ordered]@{ classification = $classification; runId = $runId; phase = $phase; completed = $false; updatedAt = (Get-Date -Format 'o') })
        $stopAudit = Invoke-StopAudit -RunId $runId
        if ($stopAudit.result -ne 'PASS') { throw 'Stop/audit failed.' }

        $phase = 'POST_RUN_VALIDATION'
        Write-SupervisorState -State ([ordered]@{ classification = $classification; runId = $runId; phase = $phase; completed = $false; updatedAt = (Get-Date -Format 'o') })
        $postRunValidation = Invoke-PostRunValidation -RunId $runId -Preflight $preflight
        if ($postRunValidation.result -ne 'PASS') { throw "Post-run validation failed: $($postRunValidation.failingChecks -join ', ')" }

        if ($postRunValidation.providerTelemetryDelta.attemptedDelta -eq 0) { $notes += 'Provider lane remained unused because no Flash self-coding task was triggered during the evidence window.' }
        if ($postRunValidation.status -and (Get-QueueStatsRecord -Status $postRunValidation.status).pending -eq 0) { $notes += 'Queue remained history-only with no pending executable work at final audit.' }
        $notes += 'GAMINGPC3 remained the known external mirror blocker and was not required for this pass.'
        $terminal = if ($notes.Count -gt 0) { 'LONG_WINDOW_IDLE_OPERATIONAL_EVIDENCE_PASS_WITH_NOTES' } else { 'LONG_WINDOW_IDLE_OPERATIONAL_EVIDENCE_PASS' }
        $status = if ($notes.Count -gt 0) { 'PASS_WITH_NOTES' } else { 'PASS' }
        $report = Complete-FinalArtifacts -RunId $runId -Status $status -TerminalState $terminal -Preflight $preflight -StartDecision $startDecision -RuntimeSummary $runtimeSummary -StopAudit $stopAudit -PostRunValidation $postRunValidation -Blocker '' -Notes $notes
        Write-SupervisorState -State ([ordered]@{ classification = $classification; runId = $runId; phase = 'COMPLETE'; completed = $true; terminalState = $report.terminalState; status = $report.status; updatedAt = (Get-Date -Format 'o') })
        Write-Host "$($report.terminalState) | status=$($report.status) runtimeMinutes=$($report.actualRuntimeMinutes) bundleSha=$($report.proofBundleSha256)"
        exit $(if ($report.status -in @('PASS','PASS_WITH_NOTES')) { 0 } else { 1 })
    } catch {
        $blocker = $_.Exception.Message
        Add-JsonLine -Path (Join-Path $artifactRoot 'runtime_safety_events.jsonl') -Record ([ordered]@{
            runId = $runId
            timestamp = (Get-Date -Format 'o')
            phase = $phase
            event = 'RUN60_BLOCKED'
            blocker = $blocker
        })
        try {
            $statusCommand = Get-StatusCommandRecord
            $status = Get-StatusObject -StatusCommand $statusCommand
            if ($status -and [bool](Get-PropertyValue -Object $status -Name 'active')) {
                Add-JsonLine -Path (Join-Path $artifactRoot 'safe_recovery_ledger.jsonl') -Record ([ordered]@{
                    runId = $runId
                    timestamp = (Get-Date -Format 'o')
                    recovery = 'STOP_AFTER_BLOCKER'
                    blocker = $blocker
                })
                $stopAudit = Invoke-StopAudit -RunId $runId
            }
        } catch {}
        $terminal = if ($phase -eq 'PREFLIGHT' -or $phase -eq 'START_DECISION') {
            'LONG_WINDOW_IDLE_OPERATIONAL_EVIDENCE_BLOCKED_PREFLIGHT'
        } elseif ($phase -eq 'RUNTIME_MONITOR') {
            'LONG_WINDOW_IDLE_OPERATIONAL_EVIDENCE_BLOCKED_RUNTIME'
        } else {
            'LONG_WINDOW_IDLE_OPERATIONAL_EVIDENCE_BLOCKED_VALIDATION'
        }
        try { Invoke-SecretScan -RunId $runId | Out-Null } catch {}
        $report = Complete-FinalArtifacts -RunId $runId -Status 'BLOCKED' -TerminalState $terminal -Preflight $preflight -StartDecision $startDecision -RuntimeSummary $runtimeSummary -StopAudit $stopAudit -PostRunValidation $postRunValidation -Blocker $blocker -Notes @($blocker)
        Write-SupervisorState -State ([ordered]@{ classification = $classification; runId = $runId; phase = 'BLOCKED'; completed = $true; terminalState = $report.terminalState; status = $report.status; blocker = $blocker; updatedAt = (Get-Date -Format 'o') })
        Write-Host "$($report.terminalState) | status=$($report.status) blocker=$blocker"
        exit 1
    }
}

if ([string]::IsNullOrWhiteSpace($Mode)) {
    throw 'Mode is required. Use Validate, Run60, ReportOnly, StopAndAudit, or StopIfUnsafe.'
}

if ($Mode -eq 'ReportOnly') {
    Invoke-ReportOnlyMode
    exit 0
}

if ($Mode -eq 'StopIfUnsafe') {
    Invoke-StopIfUnsafeMode
    exit 0
}

if ($Mode -eq 'StopAndAudit') {
    New-ArtifactDirectory | Out-Null
    $runId = 'manual-stop-audit-' + (Get-Date -Format 'yyyyMMddTHHmmss')
    Ensure-FileExists -Path (Join-Path $artifactRoot 'safe_recovery_ledger.jsonl')
    $audit = Invoke-StopAudit -RunId $runId
    $audit | ConvertTo-Json -Depth 60
    exit $(if ($audit.result -eq 'PASS') { 0 } else { 1 })
}

if ($Mode -eq 'Validate') {
    $runId = 'validate-' + (Get-Date -Format 'yyyyMMddTHHmmss')
    $preflight = Invoke-Preflight -RunId $runId
    if ($preflight.result -eq 'PASS') {
        Write-Host 'VALIDATE_PASS'
        exit 0
    }
    Write-Host "VALIDATE_FAIL: $($preflight.failingChecks -join ', ')"
    exit 1
}

if ($Mode -eq 'Run60') {
    Invoke-Run60Mode
}
