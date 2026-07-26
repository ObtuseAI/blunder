#requires -Version 5.1
<#
.SYNOPSIS
    Shared process detection helpers for Obtuse idle operations.

.DESCRIPTION
    Centralizes idle-runner process matching so controls, supervisors, and
    validators agree on what counts as an active idle-mode process.

    NEXT-006 hardening: distinguishes historical benchmark/provider wording in
    reports, TODOs, runbooks, and proof artifacts from real active
    benchmark/provider work. Evidence is classified into discrete types so
    supervisors fail closed on unclear evidence without false-positiving on
    documentation text.
#>

$ErrorActionPreference = 'Stop'

$script:HistoricalEvidencePatterns = @(
    'benchmark touched\s*:\s*false',
    'provider calls\s*:\s*false',
    'do not relaunch (?:a )?benchmark',
    'do not (?:relaunch|restart|start) .*?benchmark',
    'do not call (?:a )?provider',
    'provider calls remain gated',
    'provider call(?:s)? (?:remain|are|is) gated',
    'future \d+-\w+ command, only after explicit operator approval',
    'future \d+-\w+ command',
    'only after explicit operator approval',
    'historical (?:final )?report text',
    'runbook text',
    'example blocked .*?command',
    'blocked .*?provider/benchmark command',
    'copied .*?FRANKENSTEIN artifact path',
    'proof bundle name',
    'TODO lines? mentioning benchmark/provider',
    'safety invariant summaries?',
    'command text containing benchmark/provider',
    'as historical evidence',
    'benchmark/provider (?:wording|text|mention)',
    'denied launch command',
    'would start benchmark work',
    'would start provider-backed work',
    'final report',
    'FINAL_REPORT',
    'safety invariant (?:summary|report)',
    'benchmark status',
    'benchmark relaunch allowed',
    'provider calls allowed',
    'status-idle-ops\.ps1 output',
    'active benchmark launch command pattern',
    'active provider call command pattern',
    'active provider process example',
    'active benchmark process example',
    'stale process example',
    'PID-reuse suspicion example',
    'safety boundary',
    'benchmark.*?(?:relaunch|launch).*?(?:refused|denied|blocked)',
    'provider.*?(?:call).*?(?:refused|denied|blocked)',
    ': false'
)

$script:DocumentationOnlyCommandPatterns = @(
    'example (?:blocked|denied) (?:provider|benchmark) command',
    'blocked (?:provider|benchmark) command',
    'denied (?:provider|benchmark) command',
    'would start (?:benchmark|provider-backed) work',
    'runbook .*?example',
    'documentation-only',
    'hypothetical .*?(?:benchmark|provider)'
)

$script:ActiveBenchmarkNamePattern = '(?i)^terminal-bench'

$script:ActiveBenchmarkCommandPattern = '(?i)(^|[\\/\s''"=])(run-terminal-bench[^\\/\s''"]*\.(ps1|py|exe)|terminal-bench\.(ps1|py|exe)|terminal_bench)(\s|$|[\\/''"])'

$script:ActiveProviderCommandPatterns = @(
    '(?i)(^|[\\/\s''"=;&:])(OPENAI_API_KEY|OPENROUTER_API_KEY|ANTHROPIC_API_KEY)\s*[=:]\s*["'']?\S',
    '(?i)(^|[\\/\s''"=])(curl|invoke-restmethod|wget|python|python3|node|npm)\s+.*?(openai|openrouter|anthropic)\.(com|io)',
    '(?i)(^|[\\/\s''"=])provider\s+call(\s|$|[;''"])'
)

function Test-IdleStartCommandLine {
    param([string]$CommandLine)
    if ([string]::IsNullOrWhiteSpace($CommandLine)) { return $false }
    $normalized = $CommandLine -replace '/', '\'
    $hasIdleModeFile = $normalized -match '(?i)(^|\s)-File\s+("?[^"\s]*scripts\\obtuse\\idle-mode\.ps1"?|"?[^"\s]*\\idle-mode\.ps1"?|[^\s"]*idle-mode\.ps1)(\s|$)'
    $hasStartAction = $normalized -match '(?i)(^|\s)-Action\s+Start(\s|$)'
    return ($hasIdleModeFile -and $hasStartAction)
}

function Get-IdleProcessById {
    param([object]$PidValue)
    if (-not $PidValue) { return $null }
    try {
        return Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$PidValue)" -ErrorAction SilentlyContinue
    }
    catch {
        return $null
    }
}

function Test-IdleStartProcessAlive {
    param([object]$PidValue)
    $process = Get-IdleProcessById -PidValue $PidValue
    if (-not $process) { return $false }
    return Test-IdleStartCommandLine -CommandLine $process.CommandLine
}

function Get-ActiveIdleProcesses {
    param([int[]]$ExcludeProcessIds = @($PID))
    $excluded = @{}
    foreach ($id in @($ExcludeProcessIds)) {
        if ($null -ne $id) { $excluded[[int]$id] = $true }
    }
    $items = @()
    foreach ($process in Get-CimInstance Win32_Process) {
        if ($excluded.ContainsKey([int]$process.ProcessId)) { continue }
        if (Test-IdleStartCommandLine -CommandLine $process.CommandLine) {
            $items += [ordered]@{
                pid = [int]$process.ProcessId
                processId = [int]$process.ProcessId
                name = [string]$process.Name
                commandLine = [string]$process.CommandLine
            }
        }
    }
    return $items
}

function Get-ActiveIdleProcessCount {
    param([int[]]$ExcludeProcessIds = @($PID))
    return @((Get-ActiveIdleProcesses -ExcludeProcessIds $ExcludeProcessIds)).Count
}

function Test-HistoricalEvidenceContext {
    param([string]$CommandLine)
    if ([string]::IsNullOrWhiteSpace($CommandLine)) { return $false }
    $lower = $CommandLine.ToLowerInvariant()
    foreach ($pattern in $script:HistoricalEvidencePatterns) {
        if ($lower -match $pattern) { return $true }
    }
    return $false
}

function Test-DocumentationOnlyCommandContext {
    param([string]$CommandLine)
    if ([string]::IsNullOrWhiteSpace($CommandLine)) { return $false }
    $lower = $CommandLine.ToLowerInvariant()
    foreach ($pattern in $script:DocumentationOnlyCommandPatterns) {
        if ($lower -match $pattern) { return $true }
    }
    if ($lower -match '(?:benchmark|provider).*?(?:example|blocked|denied|would start|hypothetical|documentation)') {
        return $true
    }
    if ($lower -match '(?:example|blocked|denied|would start|hypothetical|documentation).*?(?:benchmark|provider)') {
        return $true
    }
    return $false
}

function Test-ActiveBenchmarkCommandLine {
    param([string]$Name, [string]$CommandLine)
    if ($Name -match $script:ActiveBenchmarkNamePattern) { return $true }
    if ([string]::IsNullOrWhiteSpace($CommandLine)) { return $false }
    if ($CommandLine -match $script:ActiveBenchmarkCommandPattern) { return $true }
    return $false
}

function Test-ActiveProviderCommandLine {
    param([string]$CommandLine)
    if ([string]::IsNullOrWhiteSpace($CommandLine)) { return $false }
    foreach ($pattern in $script:ActiveProviderCommandPatterns) {
        if ($CommandLine -match $pattern) { return $true }
    }
    return $false
}

function Test-TerminalBenchProcessCommandLine {
    param([string]$Name, [string]$CommandLine)
    if ([string]::IsNullOrWhiteSpace($CommandLine) -and [string]::IsNullOrWhiteSpace($Name)) { return $false }
    if (Test-HistoricalEvidenceContext -CommandLine "$Name $CommandLine") { return $false }
    if (Test-DocumentationOnlyCommandContext -CommandLine "$Name $CommandLine") { return $false }
    return Test-ActiveBenchmarkCommandLine -Name $Name -CommandLine $CommandLine
}

function Test-ProviderProcessCommandLine {
    param([string]$CommandLine)
    if ([string]::IsNullOrWhiteSpace($CommandLine)) { return $false }
    if (Test-HistoricalEvidenceContext -CommandLine $CommandLine) { return $false }
    if (Test-DocumentationOnlyCommandContext -CommandLine $CommandLine) { return $false }
    if (Test-ActiveProviderCommandLine -CommandLine $CommandLine) { return $true }
    if ($CommandLine -match '(?i)(OPENAI_API_KEY|OPENROUTER_API_KEY|ANTHROPIC_API_KEY)') { return $true }
    if ($CommandLine -match '(?i)(^|[\\/\s''"=])provider\s+call(\s|$|[;''"])') { return $true }
    return $false
}

<#
.SYNOPSIS
    Classify a command line (and optional process name) into an evidence type.

.DESCRIPTION
    Returns a structured classification for supervisor and validator use.
    Evidence types:
      HISTORICAL_TEXT              - benchmark/provider wording in reports/TODOs/runbooks
      DOCUMENTATION_ONLY_COMMAND   - example blocked commands that should not execute
      ACTIVE_PROCESS               - real active benchmark or provider process
      STALE_PROCESS                - idle process whose PID no longer matches the lock
      PID_REUSE_SUSPECT            - PID exists but command line no longer matches idle runner
      DENIED_LAUNCH_COMMAND        - a command that would start benchmark/provider work
      SAFE_IDLE_PROCESS            - recognized idle-mode start command
      UNKNOWN_REVIEW_REQUIRED      - unclear evidence; fail closed
#>
function Get-CommandLineEvidenceClassification {
    param(
        [string]$Name = '',
        [string]$CommandLine = ''
    )
    $fullText = "$Name $CommandLine".Trim()
    if ([string]::IsNullOrWhiteSpace($fullText)) {
        return [ordered]@{
            evidenceType = 'UNKNOWN_REVIEW_REQUIRED'
            category = 'NONE'
            reason = 'No process name or command line provided.'
        }
    }

    if (Test-IdleStartCommandLine -CommandLine $CommandLine) {
        return [ordered]@{
            evidenceType = 'SAFE_IDLE_PROCESS'
            category = 'IDLE'
            reason = 'Command line matches idle-mode start pattern.'
        }
    }

    if (Test-HistoricalEvidenceContext -CommandLine $fullText) {
        return [ordered]@{
            evidenceType = 'HISTORICAL_TEXT'
            category = 'NONE'
            reason = 'Matched historical report/TODO/runbook/safety-invariant context.'
        }
    }

    if (Test-DocumentationOnlyCommandContext -CommandLine $fullText) {
        return [ordered]@{
            evidenceType = 'DOCUMENTATION_ONLY_COMMAND'
            category = 'NONE'
            reason = 'Command is an example, blocked pattern, or documentation-only.'
        }
    }

    if ($Name -match $script:ActiveBenchmarkNamePattern) {
        return [ordered]@{
            evidenceType = 'ACTIVE_PROCESS'
            category = 'BENCHMARK'
            reason = 'Process name starts with terminal-bench.'
        }
    }

    if ($CommandLine -match $script:ActiveBenchmarkCommandPattern) {
        return [ordered]@{
            evidenceType = 'DENIED_LAUNCH_COMMAND'
            category = 'BENCHMARK'
            reason = 'Command line would execute a terminal-bench script.'
        }
    }

    if (Test-ActiveProviderCommandLine -CommandLine $CommandLine) {
        return [ordered]@{
            evidenceType = 'DENIED_LAUNCH_COMMAND'
            category = 'PROVIDER'
            reason = 'Command line would perform a provider call or set a provider API key.'
        }
    }

    if ($CommandLine -match '(?i)(OPENAI_API_KEY|OPENROUTER_API_KEY|ANTHROPIC_API_KEY)') {
        return [ordered]@{
            evidenceType = 'ACTIVE_PROCESS'
            category = 'PROVIDER'
            reason = 'Running process command line references a provider API key name.'
        }
    }

    return [ordered]@{
        evidenceType = 'UNKNOWN_REVIEW_REQUIRED'
        category = 'NONE'
        reason = 'Evidence does not match any recognized safe, historical, or active pattern.'
    }
}

<#
.SYNOPSIS
    Classify an idle lock PID and compare it to the actual process.
#>
function Get-IdleProcessEvidence {
    param(
        [object]$LockPid = $null,
        [string]$LockCommandLine = ''
    )
    if (-not $LockPid) {
        return [ordered]@{
            evidenceType = 'UNKNOWN_REVIEW_REQUIRED'
            category = 'NONE'
            reason = 'No lock PID provided.'
            pid = $null
            alive = $false
            pidReuseSuspected = $false
        }
    }
    $process = Get-IdleProcessById -PidValue $LockPid
    $alive = Test-IdleStartProcessAlive -PidValue $LockPid
    $pidReuseSuspected = [bool]($process -and -not $alive)
    $evidenceType = if ($alive) { 'SAFE_IDLE_PROCESS' } elseif ($process) { 'PID_REUSE_SUSPECT' } else { 'STALE_PROCESS' }
    $reason = if ($alive) {
        'Lock PID is alive and matches idle-mode start command.'
    } elseif ($process) {
        "Lock PID $LockPid exists but command line does not match idle-mode start."
    } else {
        "Lock PID $LockPid does not reference a running process."
    }
    return [ordered]@{
        evidenceType = $evidenceType
        category = 'IDLE'
        reason = $reason
        pid = [int]$LockPid
        alive = $alive
        pidReuseSuspected = $pidReuseSuspected
        observedName = $(if ($process) { $process.Name } else { $null })
        observedCommandLine = $(if ($process) { $process.CommandLine } else { $null })
        expectedCommandLine = $LockCommandLine
    }
}

function Get-ForbiddenProcessSummary {
    param([int[]]$ExcludeProcessIds = @($PID))
    $excluded = @{}
    foreach ($id in @($ExcludeProcessIds)) {
        if ($null -ne $id) { $excluded[[int]$id] = $true }
    }
    $terminalBench = @()
    $provider = @()
    $terminalBenchClassifications = @()
    $providerClassifications = @()
    foreach ($process in Get-CimInstance Win32_Process) {
        if ($excluded.ContainsKey([int]$process.ProcessId)) { continue }
        $classification = Get-CommandLineEvidenceClassification -Name $process.Name -CommandLine $process.CommandLine
        if ($classification.category -eq 'BENCHMARK') {
            $terminalBench += $process
            $terminalBenchClassifications += $classification
            continue
        }
        if ($classification.category -eq 'PROVIDER') {
            $provider += $process
            $providerClassifications += $classification
        }
    }
    return [ordered]@{
        terminalBenchProcessCount = $terminalBench.Count
        providerProcessCount = $provider.Count
        terminalBenchProcesses = @($terminalBench | Select-Object ProcessId,Name,CommandLine)
        providerProcesses = @($provider | Select-Object ProcessId,Name,CommandLine)
        terminalBenchClassifications = $terminalBenchClassifications
        providerClassifications = $providerClassifications
    }
}
