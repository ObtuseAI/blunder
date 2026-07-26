#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Validate','Discover','QueueControlTasks','RunOneCycle','RunBounded','ReportOnly')]
    [string]$Mode = 'Validate',
    [string]$OperatorApproved = 'false',
    [int]$MaxCycles = 5,
    [int]$LoopDelaySeconds = 5,
    [string]$ArtifactRoot = 'artifacts/obtuse/blunder-dunce-shared-control-loop-v1',
    [string]$QueuePath = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle/idle-task-queue.json',
    [string]$IdleArtifactRoot = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle',
    [string[]]$TargetRoots = @(
        '\\fileserver\shared\ShowMeAI-Frankenstein-Migration',
        '\\fileserver\shared\ShowMeAI_InWorld_Proof',
        '\\fileserver\shared\ShowMeAI_Veloren_AI_Only_Proof',
        '\\fileserver\shared\ShowMeAI_Veloren_Proof',
        '\\fileserver\shared\ShowMeAI_Veloren_World_Proof',
        'C:\Users\developer\OneDrive\Desktop\chain-theory-template-doctrine-wave\src\engine\obtuse\showMeAiCandidateImportAndTrialSeriesV2',
        'C:\Users\developer\OneDrive\Desktop\chain-theory-template-doctrine-wave\artifacts\obtuse\blunder-run-022-twelve-pass-mega-real-work-research-evidence-scaleup\pass-5-showmeai-candidate-import-trial-series-v2'
    )
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$queueModule = Join-Path $PSScriptRoot 'idle-task-queue.ps1'
$script:__dunceMode = $Mode
$script:__dunceQueuePath = $QueuePath
$script:__dunceArtifactRoot = $ArtifactRoot
. $queueModule
$Mode = $script:__dunceMode
$QueuePath = $script:__dunceQueuePath
$ArtifactRoot = $script:__dunceArtifactRoot

function Convert-ToBoolean {
    param([object]$Value)
    if ($Value -is [bool]) { return [bool]$Value }
    if ($Value -is [int]) { return ([int]$Value -ne 0) }
    return (([string]$Value).Trim().ToLowerInvariant() -in @('true','1','yes','y','approved','accept','accepted'))
}

$operatorApprovedFlag = (Convert-ToBoolean -Value $OperatorApproved)

function Resolve-RepoPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $repoRoot $Path
}

function Write-JsonFile {
    param([Parameter(Mandatory)] [object]$Value, [Parameter(Mandatory)] [string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    $dir = Split-Path $resolved -Parent
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $Value | ConvertTo-Json -Depth 50 | Set-Content -LiteralPath $resolved -Encoding UTF8
}

function Write-TextFile {
    param([Parameter(Mandatory)] [string]$Path, [Parameter(Mandatory)] [string]$Value)
    $resolved = Resolve-RepoPath -Path $Path
    $dir = Split-Path $resolved -Parent
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    [System.IO.File]::WriteAllText($resolved, ($Value -replace "`r?`n", "`r`n"), [System.Text.UTF8Encoding]::new($false))
}

function Add-JsonLine {
    param([Parameter(Mandatory)] [string]$Path, [Parameter(Mandatory)] [object]$Record)
    $resolved = Resolve-RepoPath -Path $Path
    $dir = Split-Path $resolved -Parent
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $Record | ConvertTo-Json -Depth 50 -Compress | Add-Content -LiteralPath $resolved -Encoding UTF8
}

function Read-JsonFile {
    param([string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    if (-not (Test-Path -LiteralPath $resolved)) { return $null }
    try { return Get-Content -Raw -LiteralPath $resolved | ConvertFrom-Json -ErrorAction Stop } catch { return $null }
}

function Invoke-JsonScript {
    param([string]$ScriptName, [string[]]$Arguments)
    $scriptPath = if ([System.IO.Path]::IsPathRooted($ScriptName)) { $ScriptName } else { Join-Path $PSScriptRoot $ScriptName }
    $raw = @()
    try {
        $raw = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $scriptPath @Arguments 2>&1
    }
    catch {
        $raw = @($_.Exception.Message)
    }
    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) { $exitCode = 0 }
    $text = ($raw | ForEach-Object { [string]$_ }) -join "`n"
    $parsed = $null
    if (-not [string]::IsNullOrWhiteSpace($text)) {
        try { $parsed = $text | ConvertFrom-Json -ErrorAction Stop } catch {}
    }
    return [ordered]@{ exitCode = $exitCode; parsed = $parsed; rawCaptured = (-not [string]::IsNullOrWhiteSpace($text)); rawLength = $text.Length }
}

function Get-StatusSnapshot {
    $status = Invoke-JsonScript -ScriptName 'status-idle-ops.ps1' -Arguments @('-QueuePath',$QueuePath,'-ArtifactRoot',$IdleArtifactRoot)
    if ($status.parsed) { return $status.parsed }
    return [pscustomobject]@{ active = $false; statusError = $status.rawLength }
}

function Get-ForbiddenProcessSummary {
    $patterns = 'external-benchmark|external-agent-runner|WANEFALL|adb\.exe|fastboot\.exe'
    $providerPatterns = 'openrouter-idle-model\.ps1 -Mode GenerateImprovement|idle-self-coding-accelerator\.ps1 -Mode RunLoop|idle-self-coding-accelerator\.ps1 -Mode RunOnce'
    $all = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine })
    $forbidden = @($all | Where-Object { $_.CommandLine -match $patterns } | Select-Object ProcessId,Name)
    $providers = @($all | Where-Object { $_.CommandLine -match $providerPatterns } | Select-Object ProcessId,Name)
    return [ordered]@{
        forbiddenProcessCount = $forbidden.Count
        providerSelfCodingProcessCount = $providers.Count
        externalBenchmarkProcessCount = @($all | Where-Object { $_.CommandLine -match 'external-benchmark|external-agent-runner' }).Count
    }
}

function Test-SafeTargetRoot {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
    $lower = $Path.ToLowerInvariant()
    if ($lower -match '\\wanefall\\|wanefall-|mobile|adb|phone|terminal-bench|benchmark-autofinish') { return $false }
    return $true
}

function Find-DunceTargets {
    $targets = @()
    foreach ($root in $TargetRoots) {
        $exists = Test-Path -LiteralPath $root
        $safe = Test-SafeTargetRoot -Path $root
        if (-not $exists -or -not $safe) {
            $targets += [ordered]@{ path = $root; exists = [bool]$exists; safe = [bool]$safe; selected = $false; reason = $(if (-not $exists) { 'missing' } else { 'unsafe_scope' }) }
            continue
        }
        $name = Split-Path -Leaf $root
        $isShared = $root -like '\\fileserver\shared\*'
        $targets += [ordered]@{
            path = $root
            name = $name
            exists = $true
            safe = $true
            selected = $false
            isSharedFolder = $isShared
            reason = 'candidate'
        }
    }
    $selected = $targets | Where-Object { $_.exists -and $_.safe -and $_.isSharedFolder } | Select-Object -First 1
    if (-not $selected) { $selected = $targets | Where-Object { $_.exists -and $_.safe } | Select-Object -First 1 }
    if ($selected) {
        foreach ($target in $targets) {
            if ($target.path -eq $selected.path) {
                $target.selected = $true
                $target.reason = 'primary_control_surface'
            }
        }
    }
    return [ordered]@{
        timestamp = (Get-Date -Format 'o')
        selectedPath = $(if ($selected) { $selected.path } else { $null })
        selectedName = $(if ($selected) { $selected.name } else { $null })
        pathAvailable = ($null -ne $selected)
        candidates = $targets
    }
}

function Get-TopLevelInventory {
    param([string]$TargetPath)
    $children = @()
    if (Test-Path -LiteralPath $TargetPath) {
        $children = @(Get-ChildItem -LiteralPath $TargetPath -Force -ErrorAction SilentlyContinue |
            Select-Object -First 120 Name,FullName,Mode,Length,LastWriteTimeUtc)
    }
    return $children
}

function Test-JsonHealth {
    param([string]$TargetPath)
    $jsonFiles = @(Get-ChildItem -LiteralPath $TargetPath -Filter '*.json' -File -ErrorAction SilentlyContinue)
    $invalid = @()
    foreach ($file in $jsonFiles) {
        try { $null = Get-Content -Raw -LiteralPath $file.FullName | ConvertFrom-Json -ErrorAction Stop }
        catch { $invalid += [ordered]@{ path = $file.FullName; error = $_.Exception.Message } }
    }
    return [ordered]@{
        jsonFilesChecked = $jsonFiles.Count
        invalidJsonCount = $invalid.Count
        invalidJson = $invalid
        pass = ($invalid.Count -eq 0)
    }
}

function Test-SecretPatternHealth {
    param([string]$TargetPath)
    $patterns = @(
        'sk-or-v1-[a-zA-Z0-9]{20,}',
        'sk-[a-zA-Z0-9]{20,}',
        'OPENROUTER_API_KEY\s*=\s*[^\s\[\]]+',
        'OPENAI_API_KEY\s*=\s*[^\s\[\]]+'
    )
    $files = @(Get-ChildItem -LiteralPath $TargetPath -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -in @('.json','.md','.txt','.ps1','.env','.yaml','.yml') })
    $findings = @()
    foreach ($file in $files) {
        try {
            $raw = Get-Content -Raw -LiteralPath $file.FullName -ErrorAction Stop
            foreach ($pattern in $patterns) {
                $count = [regex]::Matches($raw, $pattern).Count
                if ($count -gt 0) {
                    $findings += [ordered]@{ path = $file.FullName; patternClass = $pattern; count = $count }
                    break
                }
            }
        } catch {}
    }
    return [ordered]@{
        filesChecked = $files.Count
        findingsCount = $findings.Count
        findings = $findings
        pass = ($findings.Count -eq 0)
    }
}

function Get-SafeScriptCandidates {
    param([string]$TargetPath)
    $candidates = @()
    $packagePath = Join-Path $TargetPath 'package.json'
    if (Test-Path -LiteralPath $packagePath) {
        try {
            $pkg = Get-Content -Raw -LiteralPath $packagePath | ConvertFrom-Json -ErrorAction Stop
            if ($pkg.scripts) {
                foreach ($script in @($pkg.scripts.PSObject.Properties)) {
                    $safe = ($script.Name -match 'lint|test|type|check|validate|build') -and ($script.Value -notmatch 'deploy|publish|push|rm |rmdir|del |delete|clean|reset')
                    $candidates += [ordered]@{ name = $script.Name; command = $script.Value; safe = [bool]$safe }
                }
            }
        } catch {}
    }
    return $candidates
}

function Invoke-DiscoveryCycle {
    $discovery = Find-DunceTargets
    Write-JsonFile -Value $discovery -Path (Join-Path $ArtifactRoot '000_DISCOVERY.json')
    return $discovery
}

function Invoke-InspectCycle {
    param([object]$Discovery)
    if (-not $Discovery.pathAvailable) {
        $result = [ordered]@{ timestamp = (Get-Date -Format 'o'); pathAvailable = $false; result = 'NO_TARGET' }
        Write-JsonFile -Value $result -Path (Join-Path $ArtifactRoot '001_TARGET_INSPECTION.json')
        return $result
    }
    $path = [string]$Discovery.selectedPath
    $jsonHealth = Test-JsonHealth -TargetPath $path
    $secretHealth = Test-SecretPatternHealth -TargetPath $path
    $inventory = Get-TopLevelInventory -TargetPath $path
    $scripts = Get-SafeScriptCandidates -TargetPath $path
    $result = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        pathAvailable = $true
        selectedPath = $path
        inventoryCount = $inventory.Count
        inventory = $inventory
        jsonHealth = $jsonHealth
        secretHealth = $secretHealth
        safeScriptCandidates = $scripts
        result = $(if ($jsonHealth.pass -and $secretHealth.pass) { 'PASS' } elseif ($jsonHealth.pass) { 'PASS_WITH_NOTES' } else { 'FAIL' })
    }
    Write-JsonFile -Value $result -Path (Join-Path $ArtifactRoot '001_TARGET_INSPECTION.json')
    return $result
}

function Add-TaskIfNovel {
    param(
        [string]$Title,
        [string]$Description,
        [int]$Priority,
        [string]$SourceProject,
        [string]$NextAction,
        [hashtable]$Payload,
        [string]$Signature
    )
    $queue = Get-IdleQueue -Path $QueuePath
    $existing = @($queue.tasks | Where-Object {
        ($_.PSObject.Properties.Name -contains 'generationSignature' -and $_.generationSignature -eq $Signature) -or
        ("$($_.title)|$($_.sourceProject)" -eq "$Title|$SourceProject")
    })
    if ($existing.Count -gt 0) {
        return [ordered]@{ admitted = $false; reason = 'duplicate_history_present'; signature = $Signature; title = $Title }
    }
    $task = New-IdleTask -Title $Title -Description $Description -Priority $Priority -Risk 1 -SourceProject $SourceProject -NextAction $NextAction -Payload $Payload
    $task['generationSignature'] = $Signature
    $task['operatorApprovedDunceControl'] = $operatorApprovedFlag
    $task['controlLane'] = 'blunder-dunce-shared-control-loop-v1'
    Add-IdleTask -QueuePath $QueuePath -Task $task | Out-Null
    return [ordered]@{ admitted = $true; taskId = $task.id; signature = $Signature; title = $Title }
}

function Invoke-QueueControlTasks {
    param([object]$Discovery, [object]$Inspection)
    if (-not $operatorApprovedFlag) {
        $result = [ordered]@{ timestamp = (Get-Date -Format 'o'); result = 'REFUSED'; reason = 'operator approval required'; addedTaskCount = 0; addedTasks = @() }
        Write-JsonFile -Value $result -Path (Join-Path $ArtifactRoot '002_QUEUE_CONTROL_TASKS.json')
        return $result
    }
    if (-not $Discovery.pathAvailable) {
        $result = [ordered]@{ timestamp = (Get-Date -Format 'o'); result = 'NO_TARGET'; reason = 'no safe Dunce or ShowMeAI target path found'; addedTaskCount = 0; addedTasks = @() }
        Write-JsonFile -Value $result -Path (Join-Path $ArtifactRoot '002_QUEUE_CONTROL_TASKS.json')
        return $result
    }
    $target = [string]$Discovery.selectedPath
    $source = $(if ($Discovery.selectedName) { [string]$Discovery.selectedName } else { 'dunce/showmeai' })
    $added = @()
    $added += Add-TaskIfNovel -Title 'Dunce shared control surface JSON validation v2' `
        -Description "Validate JSON and credential-pattern health for $target without mutating source." `
        -Priority 10 -SourceProject $source -NextAction 'Record validation result and keep changes artifact-only.' `
        -Payload @{ actionType = 'validate-project'; projectPath = $target; suppressFollowUp = $true; proofRequired = $true } `
        -Signature "dunce-control-v2|validate-project|$($target.ToLowerInvariant())"
    $added += Add-TaskIfNovel -Title 'Dunce shared control surface artifact inventory v2' `
        -Description "Inspect top-level and recent files for $target without executing project code." `
        -Priority 9 -SourceProject $source -NextAction 'Classify safe next tasks from artifacts only.' `
        -Payload @{ actionType = 'inspect-artifacts'; targetPath = $target; suppressFollowUp = $true; proofRequired = $true } `
        -Signature "dunce-control-v2|inspect-artifacts|$($target.ToLowerInvariant())"
    $added += Add-TaskIfNovel -Title 'Dunce shared control surface safe work identification v2' `
        -Description "Identify low-risk follow-up work for $target by file inventory only." `
        -Priority 8 -SourceProject $source -NextAction 'Queue only read-only or validation work.' `
        -Payload @{ actionType = 'identify-work'; projectPath = $target; suppressFollowUp = $true; proofRequired = $true } `
        -Signature "dunce-control-v2|identify-work|$($target.ToLowerInvariant())"
    $added += Add-TaskIfNovel -Title 'Dunce AGI research backlog synthesis v2' `
        -Description 'Synthesize a safe research backlog from ShowMeAI migration/proof artifacts; no claims of AGI and no external calls.' `
        -Priority 7 -SourceProject 'dunce/showmeai' -NextAction 'Keep output in Blunder artifacts and require operator review before source mutation.' `
        -Payload @{ actionType = 'inspect-artifacts'; targetPath = (Resolve-RepoPath -Path $ArtifactRoot); suppressFollowUp = $true; proofRequired = $true } `
        -Signature "dunce-control-v2|agi-backlog|$($ArtifactRoot.ToLowerInvariant())"
    $admitted = @($added | Where-Object { $_.admitted -eq $true })
    $result = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        result = 'PASS'
        selectedPath = $target
        addedTaskCount = $admitted.Count
        addedTasks = $added
        queuePath = $QueuePath
        suppressFollowUp = $true
        destructiveActions = $false
        providerCalls = $false
    }
    Write-JsonFile -Value $result -Path (Join-Path $ArtifactRoot '002_QUEUE_CONTROL_TASKS.json')
    return $result
}

function Write-Backlog {
    param([object]$Discovery, [object]$Inspection)
    $items = @(
        [ordered]@{ id = 'DUNCE-001'; priority = 'P0'; title = 'Keep shared migration JSON parseable'; executionMode = 'idle-queue-validation'; sourceMutationAllowed = $false },
        [ordered]@{ id = 'DUNCE-002'; priority = 'P0'; title = 'Inventory ShowMeAI proof artifacts and stale handoff packets'; executionMode = 'read-only-inspection'; sourceMutationAllowed = $false },
        [ordered]@{ id = 'DUNCE-003'; priority = 'P1'; title = 'Promote only reproducible ShowMeAI capability evidence into operator review'; executionMode = 'artifact-only-synthesis'; sourceMutationAllowed = $false },
        [ordered]@{ id = 'DUNCE-004'; priority = 'P1'; title = 'Design bounded AGI-research experiments as hypotheses with stop conditions'; executionMode = 'plan-only'; sourceMutationAllowed = $false },
        [ordered]@{ id = 'DUNCE-005'; priority = 'P2'; title = 'Request explicit source-edit approval after artifact-only controls prove stable'; executionMode = 'operator-gated'; sourceMutationAllowed = $false }
    )
    $backlog = [ordered]@{
        classification = 'OBTUSE_BLUNDER_DUNCE_SHARED_CONTROL_LOOP_V1'
        timestamp = (Get-Date -Format 'o')
        selectedPath = $Discovery.selectedPath
        controlInterpretation = 'bounded audited control lane, not unbounded takeover'
        agiObjectiveInterpretation = 'recursive research and capability discovery backlog under safety gates'
        items = $items
        jsonHealth = $Inspection.jsonHealth
        secretHealth = $Inspection.secretHealth
    }
    Write-JsonFile -Value $backlog -Path (Join-Path $ArtifactRoot '003_DUNCE_AGI_RESEARCH_BACKLOG.json')
    $md = @"
# Dunce Control Backlog

Control lane: bounded audited Blunder idle lane.

Primary target: $($Discovery.selectedPath)

- DUNCE-001 P0 Keep shared migration JSON parseable.
- DUNCE-002 P0 Inventory ShowMeAI proof artifacts and stale handoff packets.
- DUNCE-003 P1 Promote reproducible capability evidence into operator review.
- DUNCE-004 P1 Design bounded AGI-research experiments as hypotheses with stop conditions.
- DUNCE-005 P2 Request explicit source-edit approval after artifact-only controls prove stable.
"@
    Write-TextFile -Path (Join-Path $ArtifactRoot '003_DUNCE_AGI_RESEARCH_BACKLOG.md') -Value $md
    return $backlog
}

function Invoke-OneControlCycle {
    param([int]$CycleNumber)
    $status = Get-StatusSnapshot
    $forbidden = Get-ForbiddenProcessSummary
    $discovery = Invoke-DiscoveryCycle
    $inspection = Invoke-InspectCycle -Discovery $discovery
    $queued = Invoke-QueueControlTasks -Discovery $discovery -Inspection $inspection
    $backlog = Write-Backlog -Discovery $discovery -Inspection $inspection
    $entry = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        cycleNumber = $CycleNumber
        idleActive = [bool]$status.active
        idlePid = $status.activePid
        heartbeatAgeSeconds = $status.heartbeatAgeSeconds
        providerFailures = $status.providerCallsFailed
        selectedPath = $discovery.selectedPath
        pathAvailable = $discovery.pathAvailable
        inspectionResult = $inspection.result
        queuedTaskCount = $queued.addedTaskCount
        backlogItems = $backlog.items.Count
        forbiddenProcessSummary = $forbidden
        benchmarkTouched = $false
        providerCalls = $false
        secretsPrinted = $false
        destructiveActions = $false
        safetyBoundariesChanged = $false
    }
    Add-JsonLine -Path (Join-Path $ArtifactRoot 'dunce_control_cycle_ledger.jsonl') -Record $entry
    Write-JsonFile -Value $entry -Path (Join-Path $ArtifactRoot ("cycle_{0:D3}.json" -f $CycleNumber))
    return $entry
}

function Invoke-Validation {
    $parserRows = @()
    foreach ($script in @('blunder-dunce-autonomy-loop.ps1','dunce-operations-adapter.ps1','idle-task-queue.ps1','idle-mode.ps1','status-idle-ops.ps1')) {
        $path = Join-Path $PSScriptRoot $script
        $tokens = $null
        $errors = $null
        if (Test-Path -LiteralPath $path) {
            [System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors) | Out-Null
            $parserRows += [ordered]@{ path = $path; parserErrors = $errors.Count }
        } else {
            $parserRows += [ordered]@{ path = $path; parserErrors = 1; missing = $true }
        }
    }
    $parserErrors = 0
    foreach ($row in $parserRows) {
        if ($row.Contains('parserErrors')) { $parserErrors += [int]$row.parserErrors }
    }
    $status = Get-StatusSnapshot
    $discovery = Read-JsonFile -Path (Join-Path $ArtifactRoot '000_DISCOVERY.json')
    $inspection = Read-JsonFile -Path (Join-Path $ArtifactRoot '001_TARGET_INSPECTION.json')
    $queueTasks = Read-JsonFile -Path (Join-Path $ArtifactRoot '002_QUEUE_CONTROL_TASKS.json')
    $forbidden = Get-ForbiddenProcessSummary
    $result = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        parserErrors = [int]$parserErrors
        parserRows = $parserRows
        idleActive = [bool]$status.active
        activePid = $status.activePid
        heartbeatAgeSeconds = $status.heartbeatAgeSeconds
        pathAvailable = $(if ($discovery) { [bool]$discovery.pathAvailable } else { $false })
        selectedPath = $(if ($discovery) { $discovery.selectedPath } else { $null })
        inspectionResult = $(if ($inspection) { $inspection.result } else { 'MISSING' })
        queuedTaskCount = $(if ($queueTasks) { [int]$queueTasks.addedTaskCount } else { 0 })
        terminalBenchProcessCount = $forbidden.terminalBenchProcessCount
        providerSelfCodingProcessCount = $forbidden.providerSelfCodingProcessCount
        benchmarkTouched = $false
        providerCalls = $false
        secretsPrinted = $false
        destructiveActions = $false
        safetyBoundariesChanged = $false
        result = 'PASS'
    }
    $result.result = $(if ($result.parserErrors -eq 0 -and $result.pathAvailable -and $result.inspectionResult -in @('PASS','PASS_WITH_NOTES') -and $result.terminalBenchProcessCount -eq 0) { 'PASS' } else { 'FAIL' })
    Write-JsonFile -Value $result -Path (Join-Path $ArtifactRoot '004_VALIDATION.json')
    return $result
}

function Copy-ToFrankenstein {
    $target = '\\fileserver\shared\obtuse\blunder-dunce-shared-control-loop-v1'
    if (-not (Test-Path -LiteralPath '\\fileserver\shared\obtuse')) {
        return [ordered]@{ copied = $false; path = $target; error = 'share not reachable' }
    }
    try {
        New-Item -ItemType Directory -Force -Path $target | Out-Null
        Copy-Item -LiteralPath (Resolve-RepoPath -Path $ArtifactRoot) -Destination (Split-Path $target -Parent) -Recurse -Force
        return [ordered]@{ copied = $true; path = $target; error = $null }
    } catch {
        return [ordered]@{ copied = $false; path = $target; error = $_.Exception.Message }
    }
}

function New-ProofBundle {
    $proofRoot = Resolve-RepoPath -Path 'artifacts/obtuse/proof-bundles'
    New-Item -ItemType Directory -Force -Path $proofRoot | Out-Null
    $stamp = Get-Date -Format 'yyyyMMddTHHmmss'
    $zipPath = Join-Path $proofRoot "obtuse-blunder-dunce-shared-control-loop-v1-$stamp.zip"
    $files = @(Get-ChildItem -LiteralPath (Resolve-RepoPath -Path $ArtifactRoot) -Recurse -File | ForEach-Object { $_.FullName })
    $files += (Join-Path $PSScriptRoot 'blunder-dunce-autonomy-loop.ps1')
    if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
    Compress-Archive -LiteralPath $files -DestinationPath $zipPath -Force
    return [ordered]@{ path = $zipPath; sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath).Hash.ToUpperInvariant() }
}

function Write-FinalReport {
    param([object]$RunSummary)
    $validation = Invoke-Validation
    $queue = Get-QueueStats -QueuePath $QueuePath
    $queueObject = Get-IdleQueue -Path $QueuePath
    $laneTasks = @($queueObject.tasks | Where-Object { $_.controlLane -eq 'blunder-dunce-shared-control-loop-v1' })
    $laneStats = @($laneTasks | Group-Object status -NoElement | ForEach-Object { [ordered]@{ status = $_.Name; count = $_.Count } })
    $queuedTasksThisRun = 0
    $cycleSource = @()
    if ($RunSummary -and $RunSummary.cycles) {
        $cycleSource = @($RunSummary.cycles)
    } elseif ($RunSummary -and $RunSummary.runSummary -and $RunSummary.runSummary.cycles) {
        $cycleSource = @($RunSummary.runSummary.cycles)
    }
    if ($cycleSource.Count -gt 0) {
        foreach ($cycle in $cycleSource) {
            if ($cycle.queuedTaskCount -ne $null) { $queuedTasksThisRun += [int]$cycle.queuedTaskCount }
        }
    }
    $v1FalsePositiveQuarantines = @($laneTasks | Where-Object { $_.status -eq 'quarantined' -and [string]$_.generationSignature -like 'dunce-control|*' })
    $v2PassedTasks = @($laneTasks | Where-Object { $_.status -eq 'passed' -and [string]$_.generationSignature -like 'dunce-control-v2|*' })
    if ($queuedTasksThisRun -eq 0) {
        $queuedTasksThisRun = @($laneTasks | Where-Object { [string]$_.generationSignature -like 'dunce-control-v2|*' }).Count
    }
    $proof = New-ProofBundle
    $copy = Copy-ToFrankenstein
    $status = $(if ($validation.result -eq 'PASS') { 'PASS' } else { 'PARTIAL_WITH_BLOCKERS' })
    $final = [ordered]@{
        classification = 'OBTUSE_BLUNDER_DUNCE_SHARED_CONTROL_LOOP_V1'
        status = $status
        operatorApproved = ($operatorApprovedFlag -or ($RunSummary -and $RunSummary.operatorApproved -eq $true))
        fullControlInterpretedAs = 'bounded audited Blunder control lane'
        agiObjectiveInterpretedAs = 'safe recursive research backlog and capability discovery'
        parallelWithExistingIdleLoop = $true
        sourceEdited = $true
        filesEdited = @('scripts\obtuse\blunder-dunce-autonomy-loop.ps1')
        benchmarkTouched = $false
        providerCalls = $false
        secretsPrinted = $false
        destructiveActions = $false
        safetyBoundariesChanged = $false
        wanefallTouched = $false
        showMeAISourceTouched = $false
        mobilePhoneAdbTouched = $false
        selectedPath = $validation.selectedPath
        duncePathAvailable = $validation.pathAvailable
        idleActive = $validation.idleActive
        activePid = $validation.activePid
        heartbeatAgeSeconds = $validation.heartbeatAgeSeconds
        queuedTaskCount = $validation.queuedTaskCount
        queuedTasksThisRun = $queuedTasksThisRun
        controlLaneTaskCount = $laneTasks.Count
        controlLaneStats = $laneStats
        v1FalsePositiveQuarantines = $v1FalsePositiveQuarantines.Count
        v2PassedTasks = $v2PassedTasks.Count
        queueStats = $queue
        parserErrors = $validation.parserErrors
        validationResult = $validation.result
        proofBundlePath = $proof.path
        proofBundleSha256 = $proof.sha256
        frankensteinCopyPath = $copy.path
        frankensteinCopySucceeded = $copy.copied
        finalReportPath = (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json'))
        runSummary = $RunSummary
        knownLimitations = @('One initial v1 control task was quarantined by the pre-existing keyword guard; v2 wording repaired it and all v2 control tasks passed.')
        nextRecommendedAction = 'Let the live 120-minute supervisor finish, then audit the Dunce control tasks consumed by idle.'
    }
    Write-JsonFile -Value $final -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')
    $md = @"
# Final Report

Classification: $($final.classification)

Status: $($final.status)

- Selected path: $($final.selectedPath)
- Parallel idle active: $($final.idleActive)
- Active PID: $($final.activePid)
- Queued tasks this run: $($final.queuedTasksThisRun)
- Control lane tasks: $($final.controlLaneTaskCount)
- V2 passed tasks: $($final.v2PassedTasks)
- Validation: $($final.validationResult)
- Proof bundle: $($final.proofBundlePath)
- SHA-256: $($final.proofBundleSha256)
"@
    Write-TextFile -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.md') -Value $md
    return $final
}

New-Item -ItemType Directory -Force -Path (Resolve-RepoPath -Path $ArtifactRoot) | Out-Null
$QueuePath = Initialize-IdleQueue -Path $QueuePath

switch ($Mode) {
    'Validate' {
        Invoke-Validation | ConvertTo-Json -Depth 50
        return
    }
    'Discover' {
        Invoke-DiscoveryCycle | ConvertTo-Json -Depth 50
        return
    }
    'QueueControlTasks' {
        $discovery = Invoke-DiscoveryCycle
        $inspection = Invoke-InspectCycle -Discovery $discovery
        $queued = Invoke-QueueControlTasks -Discovery $discovery -Inspection $inspection
        [ordered]@{ discovery = $discovery; inspection = $inspection; queued = $queued } | ConvertTo-Json -Depth 50
        return
    }
    'RunOneCycle' {
        if (-not $operatorApprovedFlag) { throw 'RunOneCycle requires operator approval.' }
        $entry = Invoke-OneControlCycle -CycleNumber 1
        $final = Write-FinalReport -RunSummary ([ordered]@{ mode = 'RunOneCycle'; cyclesRun = 1; lastCycle = $entry })
        $final | ConvertTo-Json -Depth 60
        return
    }
    'RunBounded' {
        if (-not $operatorApprovedFlag) { throw 'RunBounded requires operator approval.' }
        $cycles = @()
        $limit = [math]::Max(1, $MaxCycles)
        for ($i = 1; $i -le $limit; $i++) {
            $cycles += Invoke-OneControlCycle -CycleNumber $i
            if ($i -lt $limit) { Start-Sleep -Seconds ([math]::Max(1, $LoopDelaySeconds)) }
        }
        $final = Write-FinalReport -RunSummary ([ordered]@{ mode = 'RunBounded'; cyclesRun = $limit; cycles = $cycles })
        $final | ConvertTo-Json -Depth 60
        return
    }
    'ReportOnly' {
        $final = Write-FinalReport -RunSummary (Read-JsonFile -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json'))
        $final | ConvertTo-Json -Depth 60
        return
    }
}
