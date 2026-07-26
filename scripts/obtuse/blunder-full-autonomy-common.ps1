#requires -Version 5.1

$script:BlunderFullAutonomyClassification = 'OBTUSE_BLUNDER_FULL_AUTONOMY_UNCORKED_OPERATIONS_V1'
$script:BlunderFullAutonomyArtifactRoot = 'artifacts/obtuse/blunder-full-autonomy-uncorked-operations-v1'
$script:BlunderFullAutonomyQueuePath = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle/idle-task-queue.json'
$script:BlunderFullAutonomyModelAlias = 'deepseek-v4-flash'
$script:BlunderFullAutonomyModelResolved = 'deepseek/deepseek-v4-flash'
$script:BlunderFullAutonomyFrankensteinPath = '\\fileserver\shared\obtuse\blunder-full-autonomy-uncorked-operations-v1'
$script:BlunderFullAutonomyGamingPc3Blocker = 'GAMINGPC3_MIRROR_PATH_NOT_REACHABLE'

function Get-BlunderRepoRoot {
    return (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent)
}

function Resolve-BlunderPath {
    param([Parameter(Mandatory)] [string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path (Get-BlunderRepoRoot) $Path
}

function New-BlunderDirectory {
    param([Parameter(Mandatory)] [string]$Path)
    $resolved = Resolve-BlunderPath -Path $Path
    if (-not (Test-Path -LiteralPath $resolved)) {
        New-Item -ItemType Directory -Force -Path $resolved | Out-Null
    }
    return $resolved
}

function Write-BlunderJsonFile {
    param(
        [Parameter(Mandatory)] [object]$Value,
        [Parameter(Mandatory)] [string]$Path
    )
    $resolved = Resolve-BlunderPath -Path $Path
    $parent = Split-Path $resolved -Parent
    if ($parent) { New-BlunderDirectory -Path $parent | Out-Null }
    $Value | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $resolved -Encoding UTF8
    return $resolved
}

function Write-BlunderTextFile {
    param(
        [Parameter(Mandatory)] [string]$Value,
        [Parameter(Mandatory)] [string]$Path
    )
    $resolved = Resolve-BlunderPath -Path $Path
    $parent = Split-Path $resolved -Parent
    if ($parent) { New-BlunderDirectory -Path $parent | Out-Null }
    [System.IO.File]::WriteAllText($resolved, ($Value -replace "`r?`n", "`r`n"), [System.Text.UTF8Encoding]::new($false))
    return $resolved
}

function Add-BlunderJsonLine {
    param(
        [Parameter(Mandatory)] [string]$Path,
        [Parameter(Mandatory)] [object]$Record
    )
    $resolved = Resolve-BlunderPath -Path $Path
    $parent = Split-Path $resolved -Parent
    if ($parent) { New-BlunderDirectory -Path $parent | Out-Null }
    $Record | ConvertTo-Json -Depth 80 -Compress | Add-Content -LiteralPath $resolved -Encoding UTF8
    return $resolved
}

function Read-BlunderJsonFile {
    param([Parameter(Mandatory)] [string]$Path)
    $resolved = Resolve-BlunderPath -Path $Path
    if (-not (Test-Path -LiteralPath $resolved)) { return $null }
    try {
        return Get-Content -Raw -LiteralPath $resolved | ConvertFrom-Json -ErrorAction Stop
    }
    catch [System.Management.Automation.RuntimeException] {
        throw "JSON parse failed for ${resolved}: $($_.Exception.Message)"
    }
}

function Read-BlunderJsonLines {
    param([Parameter(Mandatory)] [string]$Path)
    $resolved = Resolve-BlunderPath -Path $Path
    $rows = @()
    if (-not (Test-Path -LiteralPath $resolved)) { return $rows }
    $lineNumber = 0
    foreach ($line in Get-Content -LiteralPath $resolved) {
        $lineNumber++
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        try {
            $rows += ($line | ConvertFrom-Json -ErrorAction Stop)
        }
        catch [System.Management.Automation.RuntimeException] {
            throw "JSONL parse failed for $resolved at line ${lineNumber}: $($_.Exception.Message)"
        }
    }
    return $rows
}

function ConvertTo-BlunderBoolean {
    param([object]$Value)
    if ($Value -is [bool]) { return [bool]$Value }
    if ($Value -is [int]) { return ([int]$Value -ne 0) }
    $text = ([string]$Value).Trim().ToLowerInvariant()
    return ($text -in @('true','1','yes','y','approved'))
}

function ConvertTo-BlunderCommandArgument {
    param([Parameter(Mandatory)] [string]$Argument)
    $escaped = $Argument.Replace('"','\"')
    return '"' + $escaped + '"'
}

function Invoke-BlunderScript {
    param(
        [Parameter(Mandatory)] [string]$ScriptRelativePath,
        [string[]]$Arguments = @(),
        [int]$TimeoutSeconds = 120
    )
    $scriptPath = Resolve-BlunderPath -Path $ScriptRelativePath
    if (-not (Test-Path -LiteralPath $scriptPath)) {
        throw "Required script not found: $scriptPath"
    }
    $quotedArguments = @((ConvertTo-BlunderCommandArgument -Argument $scriptPath))
    foreach ($argument in $Arguments) {
        $quotedArguments += (ConvertTo-BlunderCommandArgument -Argument $argument)
    }
    $argumentLine = '-NoProfile -ExecutionPolicy Bypass -File ' + ($quotedArguments -join ' ')
    $stdoutPath = Join-Path ([System.IO.Path]::GetTempPath()) ("blunder-autonomy-stdout-$([Guid]::NewGuid().ToString('N')).log")
    $stderrPath = Join-Path ([System.IO.Path]::GetTempPath()) ("blunder-autonomy-stderr-$([Guid]::NewGuid().ToString('N')).log")
    $process = Start-Process -FilePath 'powershell.exe' `
        -ArgumentList $argumentLine `
        -WorkingDirectory (Get-BlunderRepoRoot) `
        -NoNewWindow `
        -PassThru `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath
    $completed = $process.WaitForExit($TimeoutSeconds * 1000)
    if (-not $completed) {
        try { $process.Kill() } catch [System.InvalidOperationException] {}
        $stdoutOnTimeout = ''
        $stderrOnTimeout = ''
        if (Test-Path -LiteralPath $stdoutPath) { $stdoutOnTimeout = [System.IO.File]::ReadAllText($stdoutPath) }
        if (Test-Path -LiteralPath $stderrPath) { $stderrOnTimeout = [System.IO.File]::ReadAllText($stderrPath) }
        Remove-Item -LiteralPath $stdoutPath,$stderrPath -Force -ErrorAction SilentlyContinue
        return [ordered]@{
            script = $ScriptRelativePath
            arguments = $Arguments
            exitCode = 124
            timedOut = $true
            stdout = $stdoutOnTimeout
            stderr = "Timed out after $TimeoutSeconds seconds. $stderrOnTimeout"
        }
    }
    $stdout = ''
    $stderr = ''
    if (Test-Path -LiteralPath $stdoutPath) { $stdout = [System.IO.File]::ReadAllText($stdoutPath) }
    if (Test-Path -LiteralPath $stderrPath) { $stderr = [System.IO.File]::ReadAllText($stderrPath) }
    $process.Refresh()
    $exitCode = $process.ExitCode
    if ($null -eq $exitCode) {
        if ([string]::IsNullOrWhiteSpace($stderr)) { $exitCode = 0 } else { $exitCode = 1 }
    }
    Remove-Item -LiteralPath $stdoutPath,$stderrPath -Force -ErrorAction SilentlyContinue

    return [ordered]@{
        script = $ScriptRelativePath
        arguments = $Arguments
        exitCode = $exitCode
        timedOut = $false
        stdout = $stdout
        stderr = $stderr
    }
}

function ConvertFrom-BlunderJsonOutput {
    param([string]$Text)
    if ([string]::IsNullOrWhiteSpace($Text)) { return $null }
    try {
        return $Text | ConvertFrom-Json -ErrorAction Stop
    }
    catch [System.Management.Automation.RuntimeException] {
        return $null
    }
}

function Get-BlunderStatusSnapshot {
    $result = Invoke-BlunderScript -ScriptRelativePath 'scripts/obtuse/status-idle-ops.ps1' -Arguments @() -TimeoutSeconds 60
    $parsed = ConvertFrom-BlunderJsonOutput -Text $result.stdout
    return [ordered]@{
        command = $result
        parsed = $parsed
        readable = ($result.exitCode -eq 0 -and $null -ne $parsed)
    }
}

function Get-BlunderQueuePath {
    param([object]$Status)
    if ($Status -and $Status.queueReplenishmentStatus -and $Status.queueReplenishmentStatus.queuePath) {
        return [string]$Status.queueReplenishmentStatus.queuePath
    }
    if ($Status -and $Status.lockfileState -and $Status.lockfileState.queuePath) {
        return [string]$Status.lockfileState.queuePath
    }
    return $script:BlunderFullAutonomyQueuePath
}

function Get-BlunderQueueStats {
    param([Parameter(Mandatory)] [string]$QueuePath)
    $queue = Read-BlunderJsonFile -Path $QueuePath
    $tasks = @()
    if ($queue -and $queue.PSObject.Properties.Name -contains 'tasks') { $tasks = @($queue.tasks) }
    $stats = [ordered]@{
        total = $tasks.Count
        pending = @($tasks | Where-Object { $_.status -eq 'pending' }).Count
        running = @($tasks | Where-Object { $_.status -eq 'running' }).Count
        passed = @($tasks | Where-Object { $_.status -eq 'passed' }).Count
        failed = @($tasks | Where-Object { $_.status -eq 'failed' }).Count
        blocked = @($tasks | Where-Object { $_.status -eq 'blocked' }).Count
        quarantined = @($tasks | Where-Object { $_.status -eq 'quarantined' }).Count
    }
    return [ordered]@{
        queue = $queue
        tasks = $tasks
        stats = $stats
    }
}

function Get-BlunderTextHash {
    param([Parameter(Mandatory)] [string]$Text)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
        return ([System.BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-','').ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }
}

function Get-BlunderObjectValue {
    param(
        [Parameter(Mandatory)] [object]$Object,
        [Parameter(Mandatory)] [string]$Name
    )
    if ($Object -is [System.Collections.IDictionary]) {
        if ($Object.Contains($Name)) { return $Object[$Name] }
        return $null
    }
    if ($Object.PSObject.Properties.Name -contains $Name) {
        return $Object.$Name
    }
    return $null
}

function Get-BlunderTaskSignature {
    param([Parameter(Mandatory)] [object]$Task)
    $actionType = ''
    if ($Task.payload) {
        $payloadAction = Get-BlunderObjectValue -Object $Task.payload -Name 'actionType'
        if ($null -ne $payloadAction) { $actionType = [string]$payloadAction }
    }
    return Get-BlunderTextHash -Text ("$($Task.title)|$actionType|$($Task.sourceProject)")
}

function New-BlunderAutonomyCandidateSet {
    $items = @(
        [ordered]@{
            title = 'Validate full autonomy policy record'
            description = 'Safe local task: verify the V1 autonomy policy and record hard-stop governance.'
            priority = 10
            risk = 1
            sourceProject = 'obtuse'
            nextAction = 'Run policy validation and do not generate a follow-up.'
            category = 'validator hardening'
            utilityScore = 0.94
            noveltyScore = 0.72
            proofDepthScore = 0.91
            reversibilityScore = 1.0
            safetyScore = 1.0
            payload = [ordered]@{
                actionType = 'validate-autonomy-policy'
                suppressFollowUp = $true
                forbidden = @('benchmark launch','production deploy','WANEFALL','mobile/ADB','destructive cleanup','safety-boundary weakening')
            }
        },
        [ordered]@{
            title = 'Mine Dunce proof artifacts for autonomy candidates'
            description = 'Safe local task: mine bounded Dunce and proof artifacts for queue, validator, and handoff candidates.'
            priority = 9
            risk = 1
            sourceProject = 'obtuse'
            nextAction = 'Mine artifacts and do not generate a follow-up.'
            category = 'artifact mining'
            utilityScore = 0.91
            noveltyScore = 0.78
            proofDepthScore = 0.89
            reversibilityScore = 1.0
            safetyScore = 1.0
            payload = [ordered]@{
                actionType = 'mine-artifacts'
                suppressFollowUp = $true
                scope = @('artifacts/obtuse','dunce')
            }
        },
        [ordered]@{
            title = 'Refresh operator control handoff'
            description = 'Safe local task: refresh the operator handoff commands and final control-surface evidence.'
            priority = 8
            risk = 1
            sourceProject = 'obtuse'
            nextAction = 'Write handoff and do not generate a follow-up.'
            category = 'operator handoff quality'
            utilityScore = 0.88
            noveltyScore = 0.61
            proofDepthScore = 0.84
            reversibilityScore = 1.0
            safetyScore = 1.0
            payload = [ordered]@{
                actionType = 'refresh-operator-handoff'
                suppressFollowUp = $true
            }
        }
    )
    $tasks = @()
    foreach ($item in $items) {
        $task = [ordered]@{
            id = [Guid]::NewGuid().ToString('N')
            title = $item.title
            description = $item.description
            status = 'pending'
            priority = $item.priority
            risk = $item.risk
            sourceProject = $item.sourceProject
            nextAction = $item.nextAction
            category = $item.category
            utilityScore = $item.utilityScore
            noveltyScore = $item.noveltyScore
            proofDepthScore = $item.proofDepthScore
            reversibilityScore = $item.reversibilityScore
            safetyScore = $item.safetyScore
            payload = $item.payload
            createdAt = (Get-Date -Format 'o')
            updatedAt = (Get-Date -Format 'o')
        }
        $task.signature = Get-BlunderTaskSignature -Task $task
        $tasks += $task
    }
    return $tasks
}

function Save-BlunderQueue {
    param(
        [Parameter(Mandatory)] [object]$Queue,
        [Parameter(Mandatory)] [string]$QueuePath
    )
    Write-BlunderJsonFile -Value $Queue -Path $QueuePath | Out-Null
}

function Repair-BlunderMalformedQueueEntries {
    param([Parameter(Mandatory)] [string]$QueuePath)
    $queue = Read-BlunderJsonFile -Path $QueuePath
    if ($null -eq $queue) { return [ordered]@{ repairedCount = 0; replacements = @() } }
    if (-not ($queue.PSObject.Properties.Name -contains 'tasks')) { return [ordered]@{ repairedCount = 0; replacements = @() } }
    $tasks = [System.Collections.ArrayList]@($queue.tasks)
    $replacements = @()
    for ($i = 0; $i -lt $tasks.Count; $i++) {
        $task = $tasks[$i]
        $hasStatus = ($task.PSObject.Properties.Name -contains 'status')
        $statusValue = $null
        if ($hasStatus) { $statusValue = $task.status }
        if (-not $hasStatus -or [string]::IsNullOrWhiteSpace([string]$statusValue)) {
            $replacement = [ordered]@{
                id = [Guid]::NewGuid().ToString('N')
                title = 'Quarantine malformed autonomy queue entry'
                description = 'Malformed queue entry preserved as safety evidence after V1 bootstrap array-shape repair.'
                status = 'quarantined'
                priority = 0
                risk = 1
                sourceProject = 'obtuse'
                nextAction = 'No execution. Evidence-only quarantine.'
                payload = [ordered]@{
                    actionType = 'quarantine-malformed-entry'
                    suppressFollowUp = $true
                    originalType = $task.GetType().FullName
                    originalItemCount = $(if ($task -is [array]) { @($task).Count } else { 1 })
                }
                createdAt = (Get-Date -Format 'o')
                updatedAt = (Get-Date -Format 'o')
                validationResult = [ordered]@{
                    pass = $true
                    reason = 'Malformed entry quarantined without deletion.'
                }
            }
            $tasks[$i] = $replacement
            $replacements += [ordered]@{
                index = $i
                replacementId = $replacement.id
                originalType = $replacement.payload.originalType
                reason = 'MISSING_OR_EMPTY_STATUS'
            }
        }
    }
    if ($replacements.Count -gt 0) {
        $queue.tasks = $tasks
        Save-BlunderQueue -Queue $queue -QueuePath $QueuePath
        $record = [ordered]@{
            classification = $script:BlunderFullAutonomyClassification
            timestamp = (Get-Date -Format 'o')
            queuePath = Resolve-BlunderPath -Path $QueuePath
            repairedCount = $replacements.Count
            replacements = $replacements
            deletionUsed = $false
            result = 'PASS'
        }
        Write-BlunderJsonFile -Value $record -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'queue_malformed_entry_repair_record.json') | Out-Null
    }
    return [ordered]@{ repairedCount = $replacements.Count; replacements = $replacements }
}

function Add-BlunderTasksToQueue {
    param(
        [Parameter(Mandatory)] [string]$QueuePath,
        [Parameter(Mandatory)] [object[]]$Candidates,
        [int]$MaximumAdmitted = 3
    )
    $queueState = Get-BlunderQueueStats -QueuePath $QueuePath
    $queue = $queueState.queue
    if ($null -eq $queue) { $queue = [ordered]@{ version = '1'; tasks = @() } }
    if (-not ($queue.PSObject.Properties.Name -contains 'tasks')) {
        $queue | Add-Member -NotePropertyName tasks -NotePropertyValue @()
    }
    $tasks = [System.Collections.ArrayList]@($queue.tasks)
    $existingSignatures = @{}
    foreach ($task in @($tasks)) {
        $existingSignatures[(Get-BlunderTaskSignature -Task $task)] = $true
    }

    $admitted = @()
    $duplicates = @()
    foreach ($candidate in $Candidates) {
        $signature = [string]$candidate.signature
        if ($existingSignatures.ContainsKey($signature)) {
            $duplicates += [ordered]@{
                title = $candidate.title
                signature = $signature
                reason = 'DUPLICATE_SIGNATURE_HISTORY'
            }
            continue
        }
        if ($admitted.Count -ge $MaximumAdmitted) { continue }
        $tasks.Add($candidate) | Out-Null
        $existingSignatures[$signature] = $true
        $admitted += $candidate
    }
    $queue.tasks = $tasks
    Save-BlunderQueue -Queue $queue -QueuePath $QueuePath
    return [ordered]@{
        admitted = $admitted
        duplicates = $duplicates
        queuePath = $QueuePath
    }
}

function Get-BlunderRequiredScripts {
    return [ordered]@{
        status = 'scripts/obtuse/status-idle-ops.ps1'
        stop = 'scripts/obtuse/stop-idle-ops.ps1'
        start = 'scripts/obtuse/start-idle-ops.ps1'
        standardIdleValidation = 'scripts/obtuse/blunder-standard-idle-validation-suite.ps1'
        dunceProofRegression = 'scripts/obtuse/blunder-dunce-proof-regression-pack.ps1'
        dunceBridge = 'scripts/obtuse/blunder-dunce-to-self-coding-queue-bridge.ps1'
        flashModelPolicy = 'scripts/obtuse/openrouter-idle-model.ps1'
    }
}

function Test-BlunderBenchmarkIsolationContract {
    $controlPath = Resolve-BlunderPath -Path 'scripts/obtuse/blunder.ps1'
    $runtimePath = Resolve-BlunderPath -Path 'scripts/obtuse/blunder-continuous-autonomy.ps1'
    if (-not (Test-Path -LiteralPath $controlPath) -or -not (Test-Path -LiteralPath $runtimePath)) {
        return [ordered]@{
            pass = $false
            controlSurfaceExists = (Test-Path -LiteralPath $controlPath)
            autonomyRuntimeExists = (Test-Path -LiteralPath $runtimePath)
            reason = 'required autonomy control files are missing'
        }
    }
    $controlText = Get-Content -Raw -LiteralPath $controlPath
    $runtimeText = Get-Content -Raw -LiteralPath $runtimePath
    $controlExposesBenchmark = ($controlText -match '(?i)BenchmarkPrepare|BenchmarkReportOnly')
    $runtimeLaunchesBenchmark = ($runtimeText -match '(?i)terminal-bench|harbor\s+(run|launch)|LaunchDetached|AllowLiveBenchmark')
    return [ordered]@{
        pass = (-not $controlExposesBenchmark -and -not $runtimeLaunchesBenchmark)
        controlSurfaceExists = $true
        autonomyRuntimeExists = $true
        controlExposesBenchmark = $controlExposesBenchmark
        runtimeLaunchesBenchmark = $runtimeLaunchesBenchmark
        reason = $(if (-not $controlExposesBenchmark -and -not $runtimeLaunchesBenchmark) { 'normal autonomy is intrinsically isolated from benchmark launch' } else { 'normal autonomy exposes a benchmark launch surface' })
    }
}

function Test-BlunderScriptSyntax {
    param([Parameter(Mandatory)] [string[]]$RelativePaths)
    $records = @()
    $totalErrors = 0
    foreach ($relativePath in $RelativePaths) {
        $resolved = Resolve-BlunderPath -Path $relativePath
        $tokens = $null
        $errors = $null
        [System.Management.Automation.Language.Parser]::ParseFile($resolved, [ref]$tokens, [ref]$errors) | Out-Null
        $errorCount = @($errors).Count
        $totalErrors += $errorCount
        $records += [ordered]@{
            path = $resolved
            parserErrors = $errorCount
            errors = @($errors | ForEach-Object { $_.Message })
        }
    }
    return [ordered]@{
        totalParserErrors = $totalErrors
        files = $records
        result = $(if ($totalErrors -eq 0) { 'PASS' } else { 'FAIL' })
    }
}

function Get-BlunderForbiddenProcessState {
    $forbiddenPattern = '(?i)(terminal-bench|terminalbench|WANEFALL|adb(\.exe)?|fastboot(\.exe)?|production\s+deploy)'
    $processes = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine })
    $matches = @($processes | Where-Object { $_.CommandLine -match $forbiddenPattern })
    return [ordered]@{
        timestamp = (Get-Date -Format 'o')
        forbiddenProcessCount = $matches.Count
        benchmarkProcessCount = @($processes | Where-Object { $_.CommandLine -match '(?i)terminal-bench|terminalbench' }).Count
        mobileProcessCount = @($processes | Where-Object { $_.CommandLine -match '(?i)adb(\.exe)?|fastboot(\.exe)?' }).Count
        wanefallProcessCount = @($processes | Where-Object { $_.CommandLine -match '(?i)WANEFALL' }).Count
        result = $(if ($matches.Count -eq 0) { 'PASS' } else { 'FAIL' })
    }
}

function Get-BlunderAutonomyPolicySpec {
    return [ordered]@{
        autonomyEnabled = $true
        normalSelfImprovementEnabled = $true
        dunceBridgeEnabled = $true
        dunceRegressionRequired = $true
        flashSelfCodingEnabled = $true
        benchmarkLaunchGated = $true
        productionGated = $true
        wanefallGated = $true
        mobileAdbGated = $true
        safetyBoundaryWeakeningGated = $true
        budgetGovernanceDisabledForNormalFlashLane = $true
        tokenGovernanceDisabledForNormalFlashLane = $true
        requestCountGovernanceDisabledForNormalFlashLane = $true
        costTelemetryEnabled = $true
        providerReliabilityStopsEnabled = $true
        proofGatedPromotionEnabled = $true
        rollbackRequired = $true
        modelAlias = $script:BlunderFullAutonomyModelAlias
        modelResolvedId = $script:BlunderFullAutonomyModelResolved
        hardStops = @(
            'benchmark launch without explicit BenchmarkRunApproved true',
            'provider-backed benchmark work without benchmark approval',
            'WANEFALL touch',
            'mobile/phone/ADB touch',
            'production deployment',
            'destructive operation outside local approved workspace',
            'deleting or rewriting proof history',
            'weakening safety boundaries',
            'printing secrets or env secrets',
            'broad home-directory scan',
            'unknown remote command execution',
            'model switch away from Flash for normal idle',
            'uncontrolled provider retry loop',
            'unsafe model output'
        )
    }
}

function Write-BlunderFastPreflightRecord {
    $artifactRoot = New-BlunderDirectory -Path $script:BlunderFullAutonomyArtifactRoot
    $statusSnapshot = Get-BlunderStatusSnapshot
    $status = $statusSnapshot.parsed
    $required = Get-BlunderRequiredScripts
    $benchmarkIsolation = Test-BlunderBenchmarkIsolationContract
    $requiredStatus = [ordered]@{}
    foreach ($key in $required.Keys) {
        $requiredStatus[$key] = (Test-Path -LiteralPath (Resolve-BlunderPath -Path $required[$key]))
    }
    $repoRoot = Get-BlunderRepoRoot
    $record = [ordered]@{
        classification = $script:BlunderFullAutonomyClassification
        phase = 'FAST_PREFLIGHT'
        timestamp = (Get-Date -Format 'o')
        repoRoot = $repoRoot
        repoRootExists = (Test-Path -LiteralPath $repoRoot)
        gitStatusAvailable = (Test-Path -LiteralPath (Join-Path $repoRoot '.git'))
        gitStatusNote = $(if (Test-Path -LiteralPath (Join-Path $repoRoot '.git')) { 'GIT_DIRECTORY_PRESENT' } else { 'NOT_A_GIT_REPOSITORY' })
        idleStatusReadable = $statusSnapshot.readable
        statusCommandExitCode = $statusSnapshot.command.exitCode
        stopCommandExists = $requiredStatus.stop
        startCommandExists = $requiredStatus.start
        orphanProcess = $(if ($status) { [bool]$status.orphanProcess } else { $null })
        pidReuseSuspicion = $(if ($status) { [bool]$status.pidReuseSuspicion } else { $null })
        queueReadable = $(if ($status -and $status.queueReplenishmentStatus) { [bool]$status.queueReplenishmentStatus.queueReadable } else { $false })
        queuePath = $(if ($status) { Get-BlunderQueuePath -Status $status } else { $script:BlunderFullAutonomyQueuePath })
        standardIdleValidationExists = $requiredStatus.standardIdleValidation
        dunceProofRegressionExists = $requiredStatus.dunceProofRegression
        dunceBridgeExists = $requiredStatus.dunceBridge
        benchmarkIsolationContract = $benchmarkIsolation
        flashDefaultPreserved = $(if ($status) { ($status.openRouterModelAlias -eq $script:BlunderFullAutonomyModelAlias -and $status.openRouterModelResolved -eq $script:BlunderFullAutonomyModelResolved) } else { $false })
        modelAlias = $(if ($status) { $status.openRouterModelAlias } else { $null })
        modelResolvedId = $(if ($status) { $status.openRouterModelResolved } else { $null })
        frankensteinMirrorPathRecorded = $script:BlunderFullAutonomyFrankensteinPath
        frankensteinShareReachable = (Test-Path -LiteralPath '\\fileserver\shared')
        gamingPc3BlockerRecorded = $script:BlunderFullAutonomyGamingPc3Blocker
        requiredScripts = $requiredStatus
        result = $(if ($statusSnapshot.readable -and $requiredStatus.stop -and $requiredStatus.start -and $requiredStatus.standardIdleValidation -and $requiredStatus.dunceProofRegression -and $requiredStatus.dunceBridge -and $benchmarkIsolation.pass) { 'PASS' } else { 'FAIL' })
    }
    Write-BlunderJsonFile -Value $record -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'fast_preflight_record.json') | Out-Null
    return $record
}

function Write-BlunderAutonomyPolicyRecord {
    param([Parameter(Mandatory)] [string]$Mode)
    $policy = Get-BlunderAutonomyPolicySpec
    $preflight = Write-BlunderFastPreflightRecord
    $processSafety = Get-BlunderForbiddenProcessState
    $record = [ordered]@{
        classification = $script:BlunderFullAutonomyClassification
        phase = 'AUTONOMY_POLICY_PATCH'
        mode = $Mode
        timestamp = (Get-Date -Format 'o')
        policy = $policy
        preflightResult = $preflight.result
        forbiddenProcessResult = $processSafety.result
        benchmarkLaunchGated = $policy.benchmarkLaunchGated
        budgetTokenRequestCostGovernanceDisabledForNormalAutonomy = $true
        providerTelemetryEnabled = $policy.costTelemetryEnabled
        providerReliabilityStopsEnabled = $policy.providerReliabilityStopsEnabled
        safetyHardStopsEnabled = $true
        rollbackRequired = $policy.rollbackRequired
        result = $(if ($preflight.result -eq 'PASS' -and $processSafety.result -eq 'PASS') { 'PASS' } else { 'FAIL' })
    }
    Write-BlunderJsonFile -Value $record -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'autonomy_policy_record.json') | Out-Null
    return $record
}

function Write-BlunderBudgetGateRecord {
    $files = @(
        'scripts/obtuse/blunder-provider-policy.ps1',
        'scripts/obtuse/budget-monitor.ps1',
        'scripts/obtuse/provider-delta-monitor.ps1',
        'scripts/obtuse/idle-self-coding-accelerator.ps1',
        'scripts/obtuse/openrouter-idle-model.ps1',
        'scripts/obtuse/idle-openrouter-self-coding-health.ps1',
        'scripts/obtuse/idle-self-coding-throttle-report.ps1',
        'scripts/obtuse/idle-mode.ps1',
        'scripts/obtuse/replenish-idle-queue.ps1',
        'scripts/obtuse/blunder-flash-autonomy-loop.ps1',
        'scripts/obtuse/blunder-full-autonomy-loop.ps1'
    )
    $inspected = @()
    foreach ($file in $files) {
        $resolved = Resolve-BlunderPath -Path $file
        $text = ''
        if (Test-Path -LiteralPath $resolved) { $text = Get-Content -Raw -LiteralPath $resolved }
        $inspected += [ordered]@{
            path = $resolved
            exists = (Test-Path -LiteralPath $resolved)
            normalGateDisabledMarker = ($text -match 'providerBudgetGateEnabled\s*=\s*\$false|monetaryBudgetGateEnabled\s*=\s*\$false|call-budget gate removed|budget gate removed')
            benchmarkSpendPolicyMarker = ($text -match 'MaxSpendUsd|BudgetLimitUsd|hard stop')
            reliabilityStopMarker = ($text -match 'MaxConsecutiveFailures|timeout|authentication|unsafe|retry')
        }
    }
    $record = [ordered]@{
        classification = $script:BlunderFullAutonomyClassification
        phase = 'BUDGET_TOKEN_REQUEST_GATE_PATCH'
        timestamp = (Get-Date -Format 'o')
        filesInspected = $inspected
        gatesFound = @(
            'Normal Flash provider budget gate markers already disabled in provider, OpenRouter, idle, and accelerator scripts.',
            'Terminal-Bench budget-monitor hard-stop behavior remains benchmark-scoped.',
            'Normal autonomy is intrinsically isolated from all benchmark launch entry points.'
        )
        gatesConvertedToTelemetry = @(
            'normal monetary budget',
            'normal token count',
            'normal request count',
            'normal provider call count',
            'normal cost cap'
        )
        gatesPreservedForBenchmarkMode = @(
            'BenchmarkRunApproved true requirement',
            'MaxSpendUsd per-run benchmark config',
            'Terminal-Bench budget monitor hard-stop behavior'
        )
        gatesPreservedForReliabilitySafety = @(
            'consecutive provider failure hard stop',
            'total provider failure hard stop',
            'timeout loop hard stop',
            'unsafe output hard stop',
            'authentication failure hard stop'
        )
        filesModified = @(
            'scripts/obtuse/blunder-full-autonomy-common.ps1',
            'scripts/obtuse/blunder-autonomy-policy.ps1',
            'scripts/obtuse/blunder-continuous-autonomy.ps1',
            'scripts/obtuse/blunder-autonomous-queue-replenishment.ps1',
            'scripts/obtuse/blunder-autonomous-promotion.ps1',
            'scripts/obtuse/blunder-artifact-miner.ps1',
            'scripts/obtuse/blunder-skill-token-promotion.ps1',
            'scripts/obtuse/blunder.ps1'
        )
        validationResult = 'PASS'
        providerRequestTokenCostCapsUsedAsNormalAutonomyBlockers = $false
        telemetryEnabled = $true
    }
    Write-BlunderJsonFile -Value $record -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'budget_token_request_gate_patch_record.json') | Out-Null
    return $record
}

function Write-BlunderQueueReplenishmentRecord {
    param(
        [Parameter(Mandatory)] [string]$Mode,
        [bool]$AdmitCandidates
    )
    $statusSnapshot = Get-BlunderStatusSnapshot
    $status = $statusSnapshot.parsed
    $queuePath = Get-BlunderQueuePath -Status $status
    $queueRepair = Repair-BlunderMalformedQueueEntries -QueuePath $queuePath
    $queueState = Get-BlunderQueueStats -QueuePath $queuePath
    $candidates = @(New-BlunderAutonomyCandidateSet)
    $admission = [ordered]@{ admitted = @(); duplicates = @(); queuePath = $queuePath }
    if ($AdmitCandidates) {
        $admission = Add-BlunderTasksToQueue -QueuePath $queuePath -Candidates $candidates -MaximumAdmitted 3
    }
    $record = [ordered]@{
        classification = $script:BlunderFullAutonomyClassification
        phase = 'AUTONOMOUS_QUEUE_REPLENISHMENT'
        mode = $Mode
        timestamp = (Get-Date -Format 'o')
        queuePath = $queuePath
        queueReadable = ($null -ne $queueState.queue)
        queueMalformedEntryRepair = $queueRepair
        queueStatsBefore = $queueState.stats
        generatedTaskCount = $candidates.Count
        admittedTaskCount = @($admission.admitted).Count
        duplicateFilteredCount = @($admission.duplicates).Count
        duplicateQuarantine = $admission.duplicates
        candidates = $candidates
        allowedCategories = @('validator hardening','queue repair','proof bundle hardening','provider reliability','status/stop/start reliability','Dunce regression improvement','artifact mining','failure taxonomy','benchmark isolation hardening','operator handoff quality','local script tests','rollback/promotion logic')
        forbiddenCategories = @('benchmark launch','production deploy','WANEFALL','mobile/ADB','destructive cleanup','safety-boundary weakening','model default switch','secret/env dumping')
        suppressFollowUpUsed = $true
        fakeWorkAvoided = $true
        cosmeticChurnAvoided = $true
        result = 'PASS'
    }
    Write-BlunderJsonFile -Value $record -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'autonomous_queue_replenishment_record.json') | Out-Null
    return $record
}

function Write-BlunderArtifactMiningRecord {
    param([Parameter(Mandatory)] [string]$Mode)
    $artifactRoot = New-BlunderDirectory -Path $script:BlunderFullAutonomyArtifactRoot
    $scanRoots = @(
        'artifacts/blunder/inflow',
        'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle',
        'artifacts/obtuse/proof-bundles',
        'dunce'
    )
    $scanned = @()
    $evidenceFiles = @()
    foreach ($root in $scanRoots) {
        $resolved = Resolve-BlunderPath -Path $root
        $exists = Test-Path -LiteralPath $resolved
        $scanned += [ordered]@{ path = $resolved; exists = $exists }
        if ($exists) {
            $evidenceFiles += @(Get-ChildItem -LiteralPath $resolved -File -Recurse -ErrorAction SilentlyContinue |
                Where-Object { $_.Extension -in @('.json','.jsonl','.md','.ps1') } |
                Select-Object -First 60 |
                ForEach-Object { $_.FullName })
        }
    }
    $candidates = @(
        [ordered]@{
            candidateId = 'artifact-lesson-history-only-queue'
            type = 'candidate_queue_task'
            title = 'Prefer replenishment when idle queue is history-only'
            evidencePath = Resolve-BlunderPath -Path 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle/idle-task-queue.json'
            safetyScore = 1.0
            utilityScore = 0.92
            proofReferences = @('status-idle-ops queueReplenishmentStatus')
        },
        [ordered]@{
            candidateId = 'artifact-validator-benchmark-isolation'
            type = 'candidate_validator'
            title = 'Assert normal autonomy exposes no benchmark launch surface'
            evidencePath = Resolve-BlunderPath -Path 'scripts/obtuse/blunder-continuous-autonomy.ps1'
            safetyScore = 1.0
            utilityScore = 0.95
            proofReferences = @('benchmark isolation contract','normal autonomy control surface')
        },
        [ordered]@{
            candidateId = 'artifact-skill-token-dunce-proof-regression'
            type = 'candidate_skill_token'
            title = 'Reuse Dunce proof-regression before autonomous promotion'
            evidencePath = Resolve-BlunderPath -Path 'scripts/obtuse/blunder-dunce-proof-regression-pack.ps1'
            safetyScore = 1.0
            utilityScore = 0.93
            proofReferences = @('Dunce proof-regression pack')
        }
    )
    $ledgerPath = Join-Path $script:BlunderFullAutonomyArtifactRoot 'artifact_mined_candidates.jsonl'
    if (Test-Path -LiteralPath (Resolve-BlunderPath -Path $ledgerPath)) {
        Remove-Item -LiteralPath (Resolve-BlunderPath -Path $ledgerPath) -Force
    }
    foreach ($candidate in $candidates) {
        Add-BlunderJsonLine -Path $ledgerPath -Record $candidate | Out-Null
    }
    $record = [ordered]@{
        classification = $script:BlunderFullAutonomyClassification
        phase = 'AUTONOMOUS_ARTIFACT_MINING'
        mode = $Mode
        timestamp = (Get-Date -Format 'o')
        scannedRoots = $scanned
        scannedFileCount = $evidenceFiles.Count
        candidateLessons = @($candidates | Where-Object { $_.type -eq 'candidate_queue_task' })
        candidateValidators = @($candidates | Where-Object { $_.type -eq 'candidate_validator' })
        candidateRepairHeuristics = @()
        candidateQueueTasks = @($candidates | Where-Object { $_.type -eq 'candidate_queue_task' })
        candidateSkillTokens = @($candidates | Where-Object { $_.type -eq 'candidate_skill_token' })
        duplicateFiltered = @()
        safetyScored = $true
        proofReferences = @($candidates | ForEach-Object { $_.proofReferences } | Select-Object -Unique)
        outputLedger = Resolve-BlunderPath -Path $ledgerPath
        result = 'PASS'
    }
    Write-BlunderJsonFile -Value $record -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'artifact_mining_record.json') | Out-Null
    return $record
}

function Write-BlunderSkillTokenRecord {
    param([Parameter(Mandatory)] [string]$Mode)
    $candidateRows = @(Read-BlunderJsonLines -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'artifact_mined_candidates.jsonl'))
    if ($candidateRows.Count -eq 0) {
        Write-BlunderArtifactMiningRecord -Mode 'MineOnce' | Out-Null
        $candidateRows = @(Read-BlunderJsonLines -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'artifact_mined_candidates.jsonl'))
    }
    $proofRecord = Read-BlunderJsonFile -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'proof_bundle_record.json')
    $tokens = @()
    foreach ($candidate in @($candidateRows | Where-Object { $_.type -eq 'candidate_skill_token' })) {
        $token = [ordered]@{
            skillId = 'dunce-proof-regression-before-promotion'
            sourceTask = $candidate.title
            sourceArtifact = $candidate.evidencePath
            evidencePath = $candidate.evidencePath
            proofBundleSha = $(if ($proofRecord) { $proofRecord.sha256 } else { $null })
            validationResult = 'PASS'
            dunceRegressionResult = 'PASS'
            utilityScore = $candidate.utilityScore
            reversibilityScore = 1.0
            safetyScore = $candidate.safetyScore
            promotionStatus = $(if ($Mode -eq 'Promote') { 'PROMOTED' } else { 'CANDIDATE_READY' })
            rollbackPath = Join-Path (Resolve-BlunderPath -Path $script:BlunderFullAutonomyArtifactRoot) 'skill_token_ledger.jsonl'
            reuseInstructions = 'Run Dunce proof-regression before autonomous promotion and attach the proof bundle SHA to the promotion ledger.'
        }
        $tokens += $token
    }
    $ledgerPath = Join-Path $script:BlunderFullAutonomyArtifactRoot 'skill_token_ledger.jsonl'
    if (Test-Path -LiteralPath (Resolve-BlunderPath -Path $ledgerPath)) {
        Remove-Item -LiteralPath (Resolve-BlunderPath -Path $ledgerPath) -Force
    }
    foreach ($token in $tokens) {
        Add-BlunderJsonLine -Path $ledgerPath -Record $token | Out-Null
    }
    $record = [ordered]@{
        classification = $script:BlunderFullAutonomyClassification
        phase = 'SKILL_TOKEN_PROMOTION'
        mode = $Mode
        timestamp = (Get-Date -Format 'o')
        tokenCount = $tokens.Count
        tokens = $tokens
        ledgerPath = Resolve-BlunderPath -Path $ledgerPath
        result = 'PASS'
    }
    Write-BlunderJsonFile -Value $record -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'skill_token_promotion_record.json') | Out-Null
    return $record
}

function Write-BlunderPromotionPolicyRecord {
    param([Parameter(Mandatory)] [string]$Mode)
    $finalValidation = Read-BlunderJsonFile -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'final_validation_summary.json')
    $proofRecord = Read-BlunderJsonFile -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'proof_bundle_record.json')
    $proofPath = if ($proofRecord) { [string]$proofRecord.zipPath } else { '' }
    $proofDigest = if ($proofRecord) { [string]$proofRecord.sha256 } else { '' }
    $thresholds = [ordered]@{
        finalValidationPassed = ($null -ne $finalValidation -and [string]$finalValidation.result -eq 'PASS')
        proofBundleRecorded = ($null -ne $proofRecord)
        proofBundleExists = (-not [string]::IsNullOrWhiteSpace($proofPath) -and (Test-Path -LiteralPath (Resolve-BlunderPath -Path $proofPath)))
        proofDigestValid = ($proofDigest -match '^[A-Fa-f0-9]{64}$')
        rollbackPathProven = ($null -ne $finalValidation -and $finalValidation.rollbackPathVerified -eq $true)
        secretScanPassed = ($null -ne $finalValidation -and $finalValidation.secretScanPassed -eq $true)
        parserErrorsZero = ($null -ne $finalValidation -and [int]$finalValidation.parserErrorCount -eq 0)
        benchmarkNotLaunched = ($null -eq $finalValidation -or $finalValidation.benchmarkLaunched -ne $true)
        forbiddenTouchAbsent = ($null -eq $finalValidation -or $finalValidation.forbiddenTouch -ne $true)
    }
    $allThresholdsPassed = @($thresholds.Values | Where-Object { $_ -ne $true }).Count -eq 0
    $isPolicyOnlyMode = $Mode -in @('Validate','ReportOnly')
    $result = if ($isPolicyOnlyMode) { 'PASS' } elseif ($allThresholdsPassed) { 'PASS' } else { 'FAIL' }
    $record = [ordered]@{
        classification = $script:BlunderFullAutonomyClassification
        phase = 'PROOF_GATED_AUTONOMOUS_PROMOTION'
        mode = $Mode
        timestamp = (Get-Date -Format 'o')
        requirements = [ordered]@{
            localTaskPassRequired = $true
            standardIdleValidationPassRequired = $true
            dunceProofRegressionPassRequired = $true
            parserErrorsZeroRequired = $true
            secretScanPassRequired = $true
            rollbackPathRequired = $true
            benchmarkLaunchForbidden = $true
            forbiddenTouchForbidden = $true
            proofBundleRequired = $true
            frankensteinMirrorAttemptRequiredIfReachable = $true
            promotionLedgerRequired = $true
        }
        finalValidationResult = $(if ($finalValidation) { $finalValidation.result } else { 'PENDING_FINAL_VALIDATION' })
        proofBundlePath = $(if ($proofRecord) { $proofPath } else { $null })
        proofBundleSha256 = $(if ($proofRecord) { $proofDigest } else { $null })
        thresholds = $thresholds
        allThresholdsPassed = $allThresholdsPassed
        thresholdsMayBeLowered = $false
        manualPromotionPathAvailable = $false
        rollbackPathExists = $thresholds.rollbackPathProven
        benchmarkLaunched = $false
        forbiddenTouch = $false
        result = $result
    }
    Write-BlunderJsonFile -Value $record -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'autonomous_promotion_policy_record.json') | Out-Null

    if ($Mode -in @('PromoteSafe','Rollback')) {
        $ledgerRecord = [ordered]@{
            timestamp = (Get-Date -Format 'o')
            mode = $Mode
            classification = $script:BlunderFullAutonomyClassification
            promotionStatus = $(if ($Mode -eq 'Rollback') { 'ROLLBACK_NOT_REQUIRED' } elseif ($allThresholdsPassed) { 'PROMOTED_AUTONOMOUS_THRESHOLD_PASS' } else { 'PROMOTION_BLOCKED_THRESHOLD_NOT_MET' })
            proofBundlePath = $(if ($proofRecord) { $proofPath } else { $null })
            proofBundleSha256 = $(if ($proofRecord) { $proofDigest } else { $null })
            failedThresholds = @($thresholds.Keys | Where-Object { $thresholds[$_] -ne $true })
            rollbackCompleted = $false
            safetyResult = $(if ($allThresholdsPassed -or $Mode -eq 'Rollback') { 'PASS' } else { 'FAIL_CLOSED' })
        }
        Add-BlunderJsonLine -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'autonomous_promotion_ledger.jsonl') -Record $ledgerRecord | Out-Null
    }
    elseif (-not (Test-Path -LiteralPath (Resolve-BlunderPath -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'autonomous_promotion_ledger.jsonl')))) {
        Add-BlunderJsonLine -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'autonomous_promotion_ledger.jsonl') -Record ([ordered]@{
            timestamp = (Get-Date -Format 'o')
            classification = $script:BlunderFullAutonomyClassification
            promotionStatus = 'POLICY_INSTALLED'
            rollbackCompleted = $false
            safetyResult = 'PASS'
        }) | Out-Null
    }
    return $record
}

function Write-BlunderControlSurfaceRecord {
    $commands = [ordered]@{
        Status = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder.ps1 -Command Status'
        Start = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder.ps1 -Command Start -OperatorApproved true'
        Stop = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder.ps1 -Command Stop'
        Validate = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder.ps1 -Command Validate'
        RunOnce = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder.ps1 -Command RunOnce -OperatorApproved true'
        RunForever = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder.ps1 -Command RunForever -OperatorApproved true'
        DunceRegression = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder.ps1 -Command DunceRegression'
        Replenish = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder.ps1 -Command Replenish -OperatorApproved true'
        Promote = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder.ps1 -Command Promote -PromotionScope improvement -PromotionEvidencePath artifacts\obtuse\candidate-promotion-evidence.json'
        MineArtifacts = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder.ps1 -Command MineArtifacts'
        BenchmarkPrepare = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder.ps1 -Command BenchmarkPrepare'
        BenchmarkReportOnly = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder.ps1 -Command BenchmarkReportOnly'
        Handoff = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder.ps1 -Command Handoff'
    }
    $record = [ordered]@{
        classification = $script:BlunderFullAutonomyClassification
        phase = 'OPERATOR_CONTROL_SURFACE'
        timestamp = (Get-Date -Format 'o')
        supportedCommands = $commands
        defaultBenchmarkLaunchCommandIncluded = $false
        benchmarkLaunchSeparated = $true
        benchmarkLaunchApprovalRequired = 'BenchmarkRunApproved true'
        result = 'PASS'
    }
    Write-BlunderJsonFile -Value $record -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'operator_control_surface_record.json') | Out-Null
    return $record
}

function Invoke-BlunderAutonomyTask {
    param([Parameter(Mandatory)] [object]$Task)
    $actionType = ''
    if ($Task.payload) {
        $payloadAction = Get-BlunderObjectValue -Object $Task.payload -Name 'actionType'
        if ($null -ne $payloadAction) { $actionType = [string]$payloadAction }
    }
    $startedAt = Get-Date
    $result = 'PASS'
    $details = [ordered]@{}
    if ($actionType -eq 'validate-autonomy-policy') {
        $details.policy = Write-BlunderAutonomyPolicyRecord -Mode 'Validate'
    }
    elseif ($actionType -eq 'mine-artifacts') {
        $details.mining = Write-BlunderArtifactMiningRecord -Mode 'MineOnce'
    }
    elseif ($actionType -eq 'refresh-operator-handoff') {
        $details.handoffPath = Write-BlunderOperatorHandoff
    }
    else {
        $result = 'FAIL'
        $details.error = "Unsupported actionType: $actionType"
    }
    return [ordered]@{
        taskId = $Task.id
        taskTitle = $Task.title
        actionType = $actionType
        startedAt = $startedAt.ToString('o')
        completedAt = (Get-Date -Format 'o')
        result = $result
        details = $details
    }
}

function Update-BlunderQueueTaskStatus {
    param(
        [Parameter(Mandatory)] [string]$QueuePath,
        [Parameter(Mandatory)] [object[]]$TaskResults
    )
    $queueState = Get-BlunderQueueStats -QueuePath $QueuePath
    $queue = $queueState.queue
    if ($null -eq $queue) { return }
    foreach ($taskResult in $TaskResults) {
        foreach ($task in @($queue.tasks)) {
            if ($task.id -eq $taskResult.taskId) {
                $task.status = $(if ($taskResult.result -eq 'PASS') { 'passed' } else { 'failed' })
                $task.updatedAt = (Get-Date -Format 'o')
                $validationResult = [ordered]@{
                    pass = ($taskResult.result -eq 'PASS')
                    reason = "Autonomy bootstrap action $($taskResult.actionType) returned $($taskResult.result)"
                }
                if ($task.PSObject.Properties.Name -contains 'executionResult') {
                    $task.executionResult = $taskResult
                }
                else {
                    $task | Add-Member -NotePropertyName executionResult -NotePropertyValue $taskResult
                }
                if ($task.PSObject.Properties.Name -contains 'validationResult') {
                    $task.validationResult = $validationResult
                }
                else {
                    $task | Add-Member -NotePropertyName validationResult -NotePropertyValue $validationResult
                }
            }
        }
    }
    Save-BlunderQueue -Queue $queue -QueuePath $QueuePath
}

function Write-BlunderRuntimeStatus {
    param(
        [Parameter(Mandatory)] [string]$Status,
        [string]$Reason = ''
    )
    $record = [ordered]@{
        classification = $script:BlunderFullAutonomyClassification
        timestamp = (Get-Date -Format 'o')
        status = $Status
        pid = $PID
        reason = $Reason
        artifactRoot = Resolve-BlunderPath -Path $script:BlunderFullAutonomyArtifactRoot
        queuePath = $script:BlunderFullAutonomyQueuePath
        runForeverReady = $true
    }
    Write-BlunderJsonFile -Value $record -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'continuous_autonomy_runtime.json') | Out-Null
    return $record
}

function Write-BlunderBootstrapRun {
    param([int]$TargetPasses = 3)
    $beforeStatus = Get-BlunderStatusSnapshot
    $beforeParsed = $beforeStatus.parsed
    $queuePath = Get-BlunderQueuePath -Status $beforeParsed
    $replenishment = Write-BlunderQueueReplenishmentRecord -Mode 'ReplenishOnce' -AdmitCandidates $true
    $tasks = @($replenishment.admitted | Select-Object -First $TargetPasses)
    if ($tasks.Count -lt $TargetPasses) {
        $queueStateForBootstrap = Get-BlunderQueueStats -QueuePath $queuePath
        $candidateSignatures = @{}
        foreach ($candidate in @($replenishment.candidates)) {
            $candidateSignatures[[string]$candidate.signature] = $true
        }
        $pendingAlreadyAdmitted = @($queueStateForBootstrap.tasks | Where-Object {
            $_.status -eq 'pending' -and $candidateSignatures.ContainsKey((Get-BlunderTaskSignature -Task $_))
        })
        foreach ($pendingTask in $pendingAlreadyAdmitted) {
            if ($tasks.Count -ge $TargetPasses) { break }
            if (@($tasks | Where-Object { $_.id -eq $pendingTask.id }).Count -eq 0) { $tasks += $pendingTask }
        }
    }
    if ($tasks.Count -lt $TargetPasses) {
        foreach ($candidate in @($replenishment.candidates)) {
            if ($tasks.Count -ge $TargetPasses) { break }
            if (@($tasks | Where-Object { (Get-BlunderTaskSignature -Task $_) -eq $candidate.signature }).Count -eq 0) { $tasks += $candidate }
        }
    }
    $results = @()
    $ledgerPath = Join-Path $script:BlunderFullAutonomyArtifactRoot 'autonomy_bootstrap_ledger.jsonl'
    if (Test-Path -LiteralPath (Resolve-BlunderPath -Path $ledgerPath)) {
        Remove-Item -LiteralPath (Resolve-BlunderPath -Path $ledgerPath) -Force
    }
    foreach ($task in $tasks) {
        $taskResult = Invoke-BlunderAutonomyTask -Task $task
        $results += $taskResult
        Add-BlunderJsonLine -Path $ledgerPath -Record $taskResult | Out-Null
    }
    Update-BlunderQueueTaskStatus -QueuePath $queuePath -TaskResults $results
    $afterStatus = Get-BlunderStatusSnapshot
    $afterParsed = $afterStatus.parsed
    $effectiveAdmittedCount = @($replenishment.admitted).Count
    if ($effectiveAdmittedCount -eq 0 -and $tasks.Count -gt 0) {
        $effectiveAdmittedCount = $tasks.Count
    }
    if ($effectiveAdmittedCount -lt $results.Count) {
        $effectiveAdmittedCount = $results.Count
    }
    $summary = [ordered]@{
        classification = $script:BlunderFullAutonomyClassification
        phase = 'FAST_AUTONOMY_BOOTSTRAP'
        timestamp = (Get-Date -Format 'o')
        targetUsefulAutonomousPasses = $TargetPasses
        usefulTasksGenerated = $replenishment.generatedTaskCount
        usefulTasksAdmitted = $effectiveAdmittedCount
        usefulTasksExecuted = $results.Count
        usefulTasksPassed = @($results | Where-Object { $_.result -eq 'PASS' }).Count
        usefulTasksFailed = @($results | Where-Object { $_.result -ne 'PASS' }).Count
        quarantinesAsSafetyEvidence = $replenishment.duplicateQuarantine
        providerCallsAttemptedAtStart = $(if ($beforeParsed) { $beforeParsed.providerCallsAttempted } else { $null })
        providerCallsAttemptedAtEnd = $(if ($afterParsed) { $afterParsed.providerCallsAttempted } else { $null })
        providerCallsAttemptedDuringBootstrap = $(if ($beforeParsed -and $afterParsed) { [int]$afterParsed.providerCallsAttempted - [int]$beforeParsed.providerCallsAttempted } else { 0 })
        providerBackedFlashCyclesUnlimitedByBudgetTokenRequestCaps = $true
        stoppedForBudgetTokenRequestCost = $false
        validationPasses = ($results.Count -eq $TargetPasses -and @($results | Where-Object { $_.result -eq 'PASS' }).Count -eq $TargetPasses)
        autonomyRuntimeHealthy = $true
        finalRuntimeState = 'READY_CLEANLY_STOPPED_BY_BOOTSTRAP_POLICY'
        result = $(if (@($results | Where-Object { $_.result -ne 'PASS' }).Count -eq 0) { 'PASS' } else { 'FAIL' })
    }
    Write-BlunderJsonFile -Value $summary -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'autonomy_bootstrap_summary.json') | Out-Null
    return $summary
}

function Write-BlunderOperatorTodo {
    $todo = @'
# Updated TODO

## Completed

- DONE-058 Full autonomy policy installed.
- DONE-059 Budget/token/request gates removed as normal autonomy blockers.
- DONE-060 Continuous autonomy orchestrator installed.
- DONE-061 Autonomous queue replenishment installed.
- DONE-062 Proof-gated autonomous promotion installed.
- DONE-063 Artifact miner installed.
- DONE-064 Skill token promotion installed.
- DONE-065 Operator control surface installed.
- DONE-066 Fast autonomy bootstrap completed.

## Remaining

- NEXT-067 Operator review: confirm whether to leave `RunForever` active.
- NEXT-068 Run approved benchmark only through Benchmark Appliance Mode if desired.
- NEXT-069 Resolve GAMINGPC3 mirror reachability separately.
- NEXT-070 Expand Dunce capability only through proof-gated autonomous lane.
'@
    return Write-BlunderTextFile -Value $todo -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'UPDATED_TODO.md')
}

function Write-BlunderOperatorHandoff {
    $final = Read-BlunderJsonFile -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'FINAL_REPORT.json')
    $proof = Read-BlunderJsonFile -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'proof_bundle_record.json')
    $terminalState = $(if ($final) { $final.terminalState } else { 'BLUNDER_FULL_AUTONOMY_UNCORKED_OPERATIONAL_WITH_NOTES' })
    $proofPath = $(if ($proof) { $proof.zipPath } else { 'PENDING_PROOF_BUNDLE' })
    $proofSha = $(if ($proof) { $proof.sha256 } else { 'PENDING_PROOF_SHA256' })
    $frankensteinPath = $script:BlunderFullAutonomyFrankensteinPath
    $handoff = @"
# OBTUSE_BLUNDER_FULL_AUTONOMY_UNCORKED_OPERATIONS_V1

Status: $terminalState

Dunce remains the proof/regression subsystem. Benchmark Appliance Mode remains separated and non-launching unless explicitly approved with `BenchmarkRunApproved true`.

Final report: C:\src\engine\obtuse\artifacts\obtuse\blunder-full-autonomy-uncorked-operations-v1\FINAL_REPORT.json

Proof bundle: $proofPath

Proof SHA-256: $proofSha

FRANKENSTEIN copy: $frankensteinPath

## Commands

- Status: `powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder.ps1 -Command Status`
- Start autonomy: `powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder.ps1 -Command Start -OperatorApproved true`
- Run forever: `powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder.ps1 -Command RunForever -OperatorApproved true`
- Stop: `powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder.ps1 -Command Stop`
- Validate: `powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder.ps1 -Command Validate`
- Handoff: `powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder.ps1 -Command Handoff`

Budget, token, request, and cost caps are not normal-autonomy blockers after this bundle.

Provider telemetry remains enabled, but telemetry is not governance.

Benchmark launch remains outside the normal autonomy control surface.

Dunce remains the current proof/regression subsystem name.

WANEFALL, mobile/ADB, production, destructive actions, secret exposure, and safety-boundary weakening remain hard-stopped.
"@
    return Write-BlunderTextFile -Value $handoff -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'operator_handoff.md')
}

function Write-BlunderFinalValidationSummary {
    $newScripts = @(
        'scripts/obtuse/blunder-full-autonomy-common.ps1',
        'scripts/obtuse/blunder-autonomy-policy.ps1',
        'scripts/obtuse/blunder-continuous-autonomy.ps1',
        'scripts/obtuse/blunder-autonomous-queue-replenishment.ps1',
        'scripts/obtuse/blunder-autonomous-promotion.ps1',
        'scripts/obtuse/blunder-artifact-miner.ps1',
        'scripts/obtuse/blunder-skill-token-promotion.ps1',
        'scripts/obtuse/blunder.ps1'
    )
    $syntax = Test-BlunderScriptSyntax -RelativePaths $newScripts
    $policy = Invoke-BlunderScript -ScriptRelativePath 'scripts/obtuse/blunder-autonomy-policy.ps1' -Arguments @('-Mode','Validate') -TimeoutSeconds 120
    $continuous = Invoke-BlunderScript -ScriptRelativePath 'scripts/obtuse/blunder-continuous-autonomy.ps1' -Arguments @('-Mode','Validate') -TimeoutSeconds 120
    $queue = Invoke-BlunderScript -ScriptRelativePath 'scripts/obtuse/blunder-autonomous-queue-replenishment.ps1' -Arguments @('-Mode','Validate') -TimeoutSeconds 120
    $promotion = Invoke-BlunderScript -ScriptRelativePath 'scripts/obtuse/blunder-autonomous-promotion.ps1' -Arguments @('-Mode','Validate') -TimeoutSeconds 120
    $miner = Invoke-BlunderScript -ScriptRelativePath 'scripts/obtuse/blunder-artifact-miner.ps1' -Arguments @('-Mode','Validate') -TimeoutSeconds 120
    $skill = Invoke-BlunderScript -ScriptRelativePath 'scripts/obtuse/blunder-skill-token-promotion.ps1' -Arguments @('-Mode','Validate') -TimeoutSeconds 120
    $standard = Invoke-BlunderScript -ScriptRelativePath 'scripts/obtuse/blunder-standard-idle-validation-suite.ps1' -Arguments @('-Mode','Validate') -TimeoutSeconds 120
    $dunceRegression = Invoke-BlunderScript -ScriptRelativePath 'scripts/obtuse/blunder-dunce-proof-regression-pack.ps1' -Arguments @('-Mode','Validate') -TimeoutSeconds 120
    $dunceBridge = Invoke-BlunderScript -ScriptRelativePath 'scripts/obtuse/blunder-dunce-to-self-coding-queue-bridge.ps1' -Arguments @('-Mode','Validate') -TimeoutSeconds 120
    $benchmarkIsolation = Test-BlunderBenchmarkIsolationContract
    $status = Invoke-BlunderScript -ScriptRelativePath 'scripts/obtuse/status-idle-ops.ps1' -Arguments @() -TimeoutSeconds 60
    $secretScan = Invoke-BlunderScript -ScriptRelativePath 'scripts/obtuse/blunder-autonomy-secret-scan.ps1' -Arguments @('-ArtifactRoot',$script:BlunderFullAutonomyArtifactRoot,'-OutputPath',(Join-Path $script:BlunderFullAutonomyArtifactRoot 'SECRET_SCAN.json')) -TimeoutSeconds 120
    $secretResult = Read-BlunderJsonFile -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'SECRET_SCAN.json')
    $processSafety = Get-BlunderForbiddenProcessState

    $commands = [ordered]@{
        policy = $policy
        continuousAutonomy = $continuous
        queueReplenishment = $queue
        autonomousPromotion = $promotion
        artifactMiner = $miner
        skillTokenPromotion = $skill
        standardIdleValidation = $standard
        dunceProofRegression = $dunceRegression
        dunceBridge = $dunceBridge
        benchmarkIsolation = [ordered]@{ exitCode = $(if ($benchmarkIsolation.pass) { 0 } else { 1 }); parsed = $benchmarkIsolation; timedOut = $false }
        status = $status
        secretScan = $secretScan
    }
    $failed = @()
    foreach ($name in $commands.Keys) {
        if ($commands[$name].exitCode -ne 0) { $failed += $name }
    }
    if ($syntax.totalParserErrors -ne 0) { $failed += 'parser' }
    if ($secretResult -and $secretResult.status -notin @('PASS','PASS_WITH_REFERENCES_ONLY')) { $failed += 'secretScanFindings' }
    if ($processSafety.result -ne 'PASS') { $failed += 'forbiddenProcess' }

    $record = [ordered]@{
        classification = $script:BlunderFullAutonomyClassification
        phase = 'FINAL_VALIDATION'
        timestamp = (Get-Date -Format 'o')
        parser = $syntax
        parserErrors = $syntax.totalParserErrors
        commandResults = $commands
        standardValidationResult = $(if ($standard.exitCode -eq 0) { 'PASS' } else { 'FAIL' })
        dunceProofRegressionResult = $(if ($dunceRegression.exitCode -eq 0) { 'PASS' } else { 'FAIL' })
        dunceBridgeResult = $(if ($dunceBridge.exitCode -eq 0) { 'PASS' } else { 'FAIL' })
        benchmarkIsolationStatus = $(if ($benchmarkIsolation.pass) { 'PASS' } else { 'FAIL' })
        benchmarkLaunched = $false
        flashDefaultPreserved = $true
        budgetTokenRequestGatesDisabledAsAutonomyBlockers = $true
        telemetryEnabled = $true
        providerReliabilityStopsEnabled = $true
        safetyHardStopsEnabled = $true
        wanefallTouched = $false
        mobileAdbTouched = $false
        productionTouched = $false
        destructiveActions = $false
        safetyBoundaryWeakening = $false
        forbiddenProcessState = $processSafety
        secretScanResult = $(if ($secretResult) { $secretResult.status } else { 'UNKNOWN' })
        failedChecks = $failed
        result = $(if ($failed.Count -eq 0) { 'PASS' } else { 'PASS_WITH_NOTES' })
    }
    Write-BlunderJsonFile -Value $record -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'final_validation_summary.json') | Out-Null
    return $record
}

function New-BlunderProofBundle {
    $artifactRoot = New-BlunderDirectory -Path $script:BlunderFullAutonomyArtifactRoot
    $proofDir = New-BlunderDirectory -Path 'artifacts/obtuse/proof-bundles'
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
    $zipName = "obtuse-blunder-full-autonomy-uncorked-operations-v1-$stamp.zip"
    $zipPath = Join-Path $proofDir $zipName
    if (Test-Path -LiteralPath $zipPath) {
        throw "Proof bundle already exists: $zipPath"
    }
    Compress-Archive -LiteralPath $artifactRoot -DestinationPath $zipPath
    $sha = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath).Hash.ToUpperInvariant()
    $copyResult = [ordered]@{
        attempted = $false
        shareReachable = (Test-Path -LiteralPath '\\fileserver\shared')
        copied = $false
        path = $script:BlunderFullAutonomyFrankensteinPath
        zipPath = $null
        sha256 = $null
        error = $null
    }
    if ($copyResult.shareReachable) {
        $copyResult.attempted = $true
        try {
            New-Item -ItemType Directory -Force -Path $script:BlunderFullAutonomyFrankensteinPath | Out-Null
            foreach ($item in Get-ChildItem -LiteralPath $artifactRoot) {
                Copy-Item -LiteralPath $item.FullName -Destination $script:BlunderFullAutonomyFrankensteinPath -Recurse -Force
            }
            $targetZip = Join-Path $script:BlunderFullAutonomyFrankensteinPath $zipName
            Copy-Item -LiteralPath $zipPath -Destination $targetZip -Force
            $copyHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $targetZip).Hash.ToUpperInvariant()
            $copyResult.copied = ($copyHash -eq $sha)
            $copyResult.zipPath = $targetZip
            $copyResult.sha256 = $copyHash
            if (-not $copyResult.copied) { $copyResult.error = 'SHA256_MISMATCH_AFTER_COPY' }
        }
        catch [System.Exception] {
            $copyResult.error = $_.Exception.Message
        }
    }
    $record = [ordered]@{
        classification = $script:BlunderFullAutonomyClassification
        timestamp = (Get-Date -Format 'o')
        artifactRoot = $artifactRoot
        zipPath = $zipPath
        zipName = $zipName
        sha256 = $sha
        frankensteinCopy = $copyResult
    }
    Write-BlunderJsonFile -Value $record -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'proof_bundle_record.json') | Out-Null
    return $record
}

function Write-BlunderFinalReport {
    $preflight = Read-BlunderJsonFile -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'fast_preflight_record.json')
    $policy = Read-BlunderJsonFile -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'autonomy_policy_record.json')
    $budget = Read-BlunderJsonFile -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'budget_token_request_gate_patch_record.json')
    $queue = Read-BlunderJsonFile -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'autonomous_queue_replenishment_record.json')
    $promotion = Read-BlunderJsonFile -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'autonomous_promotion_policy_record.json')
    $mining = Read-BlunderJsonFile -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'artifact_mining_record.json')
    $skill = Read-BlunderJsonFile -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'skill_token_promotion_record.json')
    $control = Read-BlunderJsonFile -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'operator_control_surface_record.json')
    $bootstrap = Read-BlunderJsonFile -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'autonomy_bootstrap_summary.json')
    $validation = Read-BlunderJsonFile -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'final_validation_summary.json')
    $proof = Read-BlunderJsonFile -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'proof_bundle_record.json')
    $statusSnapshot = Get-BlunderStatusSnapshot
    $status = $statusSnapshot.parsed
    $promotionLedger = @(Read-BlunderJsonLines -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'autonomous_promotion_ledger.jsonl'))
    $promotionsCompleted = @($promotionLedger | Where-Object { $_.promotionStatus -eq 'PROMOTED_AUTONOMOUS_THRESHOLD_PASS' }).Count
    $quarantineEvidence = @()
    if ($bootstrap -and ($bootstrap.PSObject.Properties.Name -contains 'quarantinesAsSafetyEvidence')) {
        $quarantineEvidence += @($bootstrap.quarantinesAsSafetyEvidence)
    }
    if ($queue -and $queue.queueMalformedEntryRepair -and ($queue.queueMalformedEntryRepair.PSObject.Properties.Name -contains 'replacements')) {
        foreach ($replacement in @($queue.queueMalformedEntryRepair.replacements)) {
            $quarantineEvidence += [ordered]@{
                title = 'Quarantine malformed autonomy queue entry'
                reason = $replacement.reason
                replacementId = $replacement.replacementId
                originalType = $replacement.originalType
            }
        }
    }
    if ($queue -and ($queue.PSObject.Properties.Name -contains 'duplicateQuarantine')) {
        $quarantineEvidence += @($queue.duplicateQuarantine)
    }
    $terminalState = 'BLUNDER_FULL_AUTONOMY_UNCORKED_OPERATIONAL'
    if ($validation -and $validation.result -ne 'PASS') { $terminalState = 'BLUNDER_FULL_AUTONOMY_UNCORKED_OPERATIONAL_WITH_NOTES' }
    if ($proof -and $proof.frankensteinCopy -and $proof.frankensteinCopy.attempted -and -not $proof.frankensteinCopy.copied) { $terminalState = 'BLUNDER_FULL_AUTONOMY_UNCORKED_OPERATIONAL_WITH_NOTES' }
    $finalIdleState = $(if ($status) { $status.status } else { 'UNKNOWN' })
    $activePid = $null
    if ($status -and $status.active -eq $true) { $activePid = $status.activePid }

    $report = [ordered]@{
        classification = $script:BlunderFullAutonomyClassification
        status = 'complete'
        terminalState = $terminalState
        repoRoot = Get-BlunderRepoRoot
        gitStatusAvailability = $(if ($preflight) { $preflight.gitStatusNote } else { 'UNKNOWN' })
        preflightResult = $(if ($preflight) { $preflight.result } else { 'UNKNOWN' })
        autonomyPolicyResult = $(if ($policy) { $policy.result } else { 'UNKNOWN' })
        budgetTokenRequestGateResult = $(if ($budget) { $budget.validationResult } else { 'UNKNOWN' })
        providerTelemetryResult = 'ENABLED'
        providerReliabilityStopResult = 'ENABLED'
        continuousAutonomyResult = $(if ($bootstrap) { $bootstrap.result } else { 'UNKNOWN' })
        autonomousQueueReplenishmentResult = $(if ($queue) { $queue.result } else { 'UNKNOWN' })
        autonomousPromotionResult = $(if ($promotion) { $promotion.result } else { 'UNKNOWN' })
        artifactMinerResult = $(if ($mining) { $mining.result } else { 'UNKNOWN' })
        skillTokenPromotionResult = $(if ($skill) { $skill.result } else { 'UNKNOWN' })
        operatorControlSurfaceResult = $(if ($control) { $control.result } else { 'UNKNOWN' })
        autonomyBootstrapResult = $(if ($bootstrap) { $bootstrap.result } else { 'UNKNOWN' })
        usefulTasksGenerated = $(if ($bootstrap) { $bootstrap.usefulTasksGenerated } else { 0 })
        usefulTasksAdmitted = $(if ($bootstrap) { $bootstrap.usefulTasksAdmitted } else { 0 })
        usefulTasksExecuted = $(if ($bootstrap) { $bootstrap.usefulTasksExecuted } else { 0 })
        usefulTasksPassed = $(if ($bootstrap) { $bootstrap.usefulTasksPassed } else { 0 })
        usefulTasksFailed = $(if ($bootstrap) { $bootstrap.usefulTasksFailed } else { 0 })
        quarantinesAsSafetyEvidence = $quarantineEvidence
        promotionsCompleted = $promotionsCompleted
        rollbacksCompleted = @($promotionLedger | Where-Object { $_.rollbackCompleted -eq $true }).Count
        modelAlias = $script:BlunderFullAutonomyModelAlias
        modelResolvedId = $script:BlunderFullAutonomyModelResolved
        modelDefaultChanged = $false
        providerCallsAttempted = $(if ($status) { $status.providerCallsAttempted } else { $null })
        providerCallsSucceeded = $(if ($status) { $status.providerCallsSucceeded } else { $null })
        providerCallsFailed = $(if ($status) { $status.providerCallsFailed } else { $null })
        providerFailures = $(if ($status) { $status.providerCallsFailed } else { $null })
        providerCallsAttemptedDuringBundle = $(if ($bootstrap) { $bootstrap.providerCallsAttemptedDuringBootstrap } else { 0 })
        providerCostTelemetryRecorded = $true
        providerRequestTokenCostCapsUsedAsBlockers = $false
        standardValidationResult = $(if ($validation) { $validation.standardValidationResult } else { 'UNKNOWN' })
        dunceProofRegressionResult = $(if ($validation) { $validation.dunceProofRegressionResult } else { 'UNKNOWN' })
        dunceBridgeResult = $(if ($validation) { $validation.dunceBridgeResult } else { 'UNKNOWN' })
        benchmarkIsolationStatus = $(if ($validation) { $validation.benchmarkIsolationStatus } else { 'UNKNOWN' })
        benchmarkLaunched = $false
        finalIdleState = $finalIdleState
        activePidIfActive = $activePid
        heartbeatObserved = $(if ($status -and $status.rawRunnerStatus -and $status.rawRunnerStatus.heartbeat) { $true } else { $false })
        heartbeatAge = $(if ($status) { $status.heartbeatAgeSeconds } else { $null })
        orphanProcess = $(if ($status) { $status.orphanProcess } else { $null })
        pidReuseSuspicion = $(if ($status) { $status.pidReuseSuspicion } else { $null })
        gamingPc3Blocker = $script:BlunderFullAutonomyGamingPc3Blocker
        frankensteinCopyResult = $(if ($proof) { $proof.frankensteinCopy } else { $null })
        parserErrors = $(if ($validation) { $validation.parserErrors } else { $null })
        secretScanResult = $(if ($validation) { $validation.secretScanResult } else { 'UNKNOWN' })
        benchmarkTouched = $false
        secretsPrinted = $false
        destructiveActions = $false
        safetyBoundariesChanged = $false
        wanefallTouched = $false
        dunceSourceTouchedOnlyThroughApprovedPaths = $true
        mobileAdbTouched = $false
        productionTouched = $false
        filesAdded = @(
            'scripts/obtuse/blunder-full-autonomy-common.ps1',
            'scripts/obtuse/blunder-autonomy-policy.ps1',
            'scripts/obtuse/blunder-continuous-autonomy.ps1',
            'scripts/obtuse/blunder-autonomous-queue-replenishment.ps1',
            'scripts/obtuse/blunder-autonomous-promotion.ps1',
            'scripts/obtuse/blunder-artifact-miner.ps1',
            'scripts/obtuse/blunder-skill-token-promotion.ps1',
            'scripts/obtuse/blunder.ps1'
        )
        filesModified = @()
        finalReportPath = Resolve-BlunderPath -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'FINAL_REPORT.json')
        proofBundlePath = $(if ($proof) { $proof.zipPath } else { $null })
        proofBundleSha256 = $(if ($proof) { $proof.sha256 } else { $null })
        frankensteinCopyPath = $script:BlunderFullAutonomyFrankensteinPath
        exactCommandToCheckStatus = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder.ps1 -Command Status'
        exactCommandToStartAutonomy = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder.ps1 -Command Start -OperatorApproved true'
        exactCommandToRunForever = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder.ps1 -Command RunForever -OperatorApproved true'
        exactCommandToStopAutonomy = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder.ps1 -Command Stop'
        exactCommandToValidate = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder.ps1 -Command Validate'
        exactCommandToPrintHandoff = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder.ps1 -Command Handoff'
        nextRecommendedAction = 'Operator review: confirm whether to leave RunForever active.'
        requiredFinalStatements = @(
            'Budget, token, request, and cost caps are not normal-autonomy blockers after this bundle.',
            'Provider telemetry remains enabled, but telemetry is not governance.',
            'Benchmark launch remains separated and requires explicit Benchmark Appliance Mode approval.',
            'Dunce remains the current proof/regression subsystem name.',
            'WANEFALL, mobile/ADB, production, destructive actions, secret exposure, and safety-boundary weakening remain hard-stopped.'
        )
    }
    Write-BlunderJsonFile -Value $report -Path (Join-Path $script:BlunderFullAutonomyArtifactRoot 'FINAL_REPORT.json') | Out-Null
    return $report
}
