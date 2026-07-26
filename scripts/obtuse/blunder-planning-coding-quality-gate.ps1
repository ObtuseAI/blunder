#requires -Version 5.1
<#
.SYNOPSIS
    Non-benchmark planning and coding quality gate for Blunder autonomy.

.DESCRIPTION
    Reads local queue, self-coding, and Blunder Code skill/agent artifacts only.
    Does not launch benchmarks, call providers, mutate queues, or execute generated
    patches. The gate verifies that planning/coding work has concrete evidence,
    validation, rollback, and promotion-stop contracts before it is treated as
    elite autonomous work.
#>
[CmdletBinding()]
param(
    [ValidateSet('Validate','ReportOnly')]
    [string]$Mode = 'Validate',
    [string]$ArtifactRoot = 'artifacts/obtuse/blunder-planning-coding-quality-gate-v1',
    [string]$QueuePath = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle/idle-task-queue.json',
    [string]$SelfCodingArtifactRoot = 'artifacts/obtuse/blunder-flash-full-autonomy-compounding-bundle-v1/self-coding'
)

$ErrorActionPreference = 'Stop'
$scriptRoot = $PSScriptRoot
$repoRoot = Split-Path (Split-Path $scriptRoot -Parent) -Parent
$classification = 'OBTUSE_BLUNDER_PLANNING_CODING_QUALITY_GATE_V1'

function Resolve-RepoPath {
    param([Parameter(Mandatory)] [string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $repoRoot $Path
}

function Write-JsonFile {
    param([Parameter(Mandatory)] [object]$Value, [Parameter(Mandatory)] [string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    $parent = Split-Path $resolved -Parent
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $Value | ConvertTo-Json -Depth 80 | Set-Content -LiteralPath $resolved -Encoding UTF8
}

function Write-TextFile {
    param([Parameter(Mandatory)] [string]$Value, [Parameter(Mandatory)] [string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    $parent = Split-Path $resolved -Parent
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    [System.IO.File]::WriteAllText($resolved, ($Value -replace "`r?`n", "`r`n"), [System.Text.UTF8Encoding]::new($false))
}

function Read-JsonFile {
    param([Parameter(Mandatory)] [string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    if (-not (Test-Path -LiteralPath $resolved)) { return $null }
    try { return Get-Content -Raw -LiteralPath $resolved | ConvertFrom-Json -ErrorAction Stop } catch { return $null }
}

function Read-JsonLines {
    param([Parameter(Mandatory)] [string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    $rows = @()
    if (-not (Test-Path -LiteralPath $resolved)) { return $rows }
    foreach ($line in Get-Content -LiteralPath $resolved) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        try { $rows += ($line | ConvertFrom-Json -ErrorAction Stop) } catch {}
    }
    return $rows
}

function New-Gate {
    param([string]$Name, [bool]$Pass, [int]$Weight, [string]$Actual, [string]$Blocker)
    return [ordered]@{ name = $Name; pass = $Pass; weight = $Weight; actual = $Actual; blocker = $Blocker }
}

function Get-ObjectProperty {
    param([object]$Object, [string]$Name, [object]$DefaultValue)
    if ($null -eq $Object) { return $DefaultValue }
    if ($Object.PSObject.Properties.Name -contains $Name) { return $Object.$Name }
    return $DefaultValue
}

function Convert-ToBoolean {
    param([object]$Value)
    if ($Value -is [bool]) { return [bool]$Value }
    if ($Value -is [int]) { return ([int]$Value -ne 0) }
    return (([string]$Value).Trim().ToLowerInvariant() -in @('true','1','yes','y','approved'))
}

function Get-TaskPayload {
    param([object]$Task)
    return Get-ObjectProperty -Object $Task -Name 'payload' -DefaultValue $null
}

function Get-TaskPlanningScore {
    param([object]$Task)
    $score = 100
    $issues = @()
    $payload = Get-TaskPayload -Task $Task
    $actionType = if ($payload) { [string](Get-ObjectProperty -Object $payload -Name 'actionType' -DefaultValue '') } else { '' }
    $text = "$([string](Get-ObjectProperty -Object $Task -Name 'title' -DefaultValue '')) $([string](Get-ObjectProperty -Object $Task -Name 'description' -DefaultValue '')) $([string](Get-ObjectProperty -Object $Task -Name 'nextAction' -DefaultValue ''))"
    $lower = $text.ToLowerInvariant()

    if ([string]::IsNullOrWhiteSpace($actionType)) { $score -= 25; $issues += 'missing_action_type' }
    if ($lower -notmatch 'proof|validate|validation|ledger|json|artifact|report|evidence|status') { $score -= 20; $issues += 'weak_proof_contract' }
    if ($lower -notmatch 'rollback|restore|read-only|inspect|safe|no source mutation') { $score -= 10; $issues += 'weak_reversibility_contract' }
    $benchmarkRequest = ($lower -match '(?i)benchmark\s+(launch|run|relaunch|execute)|terminal-bench\s+(launch|run|relaunch|execute)|harbor\s+run')
    $benchmarkDenied = ($lower -match '(?i)without\s+(a\s+)?(live\s+)?benchmark\s+(launch|run)|no\s+(live\s+)?benchmark\s+(launch|run)|benchmark\s+(launch|run)\s+(is\s+)?(forbidden|blocked|disabled)')
    if ($benchmarkRequest -and -not $benchmarkDenied) { $score -= 80; $issues += 'benchmark_execution_scope' }
    if ($lower -match '(?i)secret|api key|token|password|sk-or-|sk-proj-') { $score -= 50; $issues += 'secret_scope' }
    if ($payload) {
        foreach ($flag in @('destructiveActionsAllowed','providerCallsAllowed','sourceMutationAllowed','benchmarkRelaunchAllowed')) {
            if ($payload.PSObject.Properties.Name -contains $flag -and (Convert-ToBoolean -Value $payload.$flag)) {
                $score -= 50
                $issues += "unsafe_flag_$flag"
            }
        }
    }
    $score = [Math]::Min(100, [Math]::Max(0, $score))
    return [ordered]@{
        taskId = [string](Get-ObjectProperty -Object $Task -Name 'id' -DefaultValue '')
        title = [string](Get-ObjectProperty -Object $Task -Name 'title' -DefaultValue '')
        status = [string](Get-ObjectProperty -Object $Task -Name 'status' -DefaultValue '')
        actionType = $actionType
        score = $score
        issues = $issues
        pass = ($score -ge 70 -and -not ($issues -contains 'benchmark_execution_scope') -and -not ($issues -contains 'secret_scope'))
    }
}

function Get-CandidatePlanningScore {
    param([object]$CandidateRecord)
    $candidate = if ($CandidateRecord -and $CandidateRecord.candidate) { $CandidateRecord.candidate } else { $CandidateRecord }
    if (-not $candidate) {
        $nativeAgent = Resolve-RepoPath -Path 'blunder/native_agent.py'
        $verificationCourt = Resolve-RepoPath -Path 'blunder/verification_court.py'
        $nativeContract = (
            (Test-Path -LiteralPath $nativeAgent) -and
            (Test-Path -LiteralPath $verificationCourt) -and
            (Get-Content -Raw -LiteralPath $nativeAgent).Contains('preflight_court') -and
            (Get-Content -Raw -LiteralPath $verificationCourt).Contains('executable-acceptance')
        )
        return [ordered]@{
            present = $false
            score = $(if ($nativeContract) { 100 } else { 0 })
            pass = $nativeContract
            issues = $(if ($nativeContract) { @('no_candidate_pending') } else { @('native_verification_contract_missing') })
        }
    }
    $issues = @()
    $score = 100
    $qualityContract = Get-ObjectProperty -Object $candidate -Name 'qualityContract' -DefaultValue $null
    foreach ($field in @('title','problem','riskLevel')) {
        if ([string]::IsNullOrWhiteSpace([string](Get-ObjectProperty -Object $candidate -Name $field -DefaultValue ''))) {
            $score -= 15
            $issues += "${field}_missing"
        }
    }
    foreach ($field in @('targetFiles','patchPlan','validationPlan','rollbackPlan','expectedProof')) {
        if (@(Get-ObjectProperty -Object $candidate -Name $field -DefaultValue @()).Count -eq 0) {
            $score -= 15
            $issues += "${field}_missing"
        }
    }
    if ([string](Get-ObjectProperty -Object $candidate -Name 'riskLevel' -DefaultValue '') -notin @('LOW','MEDIUM')) {
        $score -= 25
        $issues += 'risk_not_low_or_medium'
    }
    $joined = ($candidate | ConvertTo-Json -Depth 20)
    if ($joined -notmatch '(?i)artifact|ledger|queue|status|failure|proof|inflow|validation|trace|evidence') {
        $score -= 15
        $issues += 'no_evidence_anchor'
    }
    if ($joined -match '(?i)benchmark\s+(launch|run|relaunch|execute)|terminal-bench\s+(launch|run|relaunch|execute)|secret|sk-or-|sk-proj-|git\s+(push|reset)|Remove-Item|rmdir|delete') {
        $score -= 80
        $issues += 'forbidden_scope_text'
    }
    if (-not $qualityContract) {
        $score -= 15
        $issues += 'quality_contract_missing'
    } else {
        foreach ($field in @('rootCause','evidenceSource','validationGate','rollbackGate','promotionStop')) {
            if (-not ($qualityContract.PSObject.Properties.Name -contains $field) -or [string]::IsNullOrWhiteSpace([string]$qualityContract.$field)) {
                $score -= 5
                $issues += "quality_contract_missing_$field"
            }
        }
    }
    $score = [Math]::Min(100, [Math]::Max(0, $score))
    return [ordered]@{
        present = $true
        score = $score
        pass = ($score -ge 85 -and -not ($issues -contains 'forbidden_scope_text'))
        title = [string](Get-ObjectProperty -Object $candidate -Name 'title' -DefaultValue '')
        targetFiles = @(Get-ObjectProperty -Object $candidate -Name 'targetFiles' -DefaultValue @())
        issues = $issues
    }
}

function Test-FileContainsAll {
    param([string]$Path, [string[]]$Patterns)
    $resolved = Resolve-RepoPath -Path $Path
    if (-not (Test-Path -LiteralPath $resolved)) { return $false }
    $text = Get-Content -Raw -LiteralPath $resolved
    foreach ($pattern in $Patterns) {
        if ($text -notmatch $pattern) { return $false }
    }
    return $true
}

function New-MarkdownReport {
    param([object]$Report)
    $failed = @($Report.gates | Where-Object { $_.pass -ne $true })
    $lines = [System.Collections.ArrayList]@()
    [void]$lines.Add('# Blunder Planning/Coding Quality Gate')
    [void]$lines.Add('')
    [void]$lines.Add("Timestamp: $($Report.timestamp)")
    [void]$lines.Add("Verdict: $($Report.verdict)")
    [void]$lines.Add("Score: $($Report.score) / $($Report.maxScore) ($($Report.scorePercent)%)")
    [void]$lines.Add('')
    [void]$lines.Add('## Failed Gates')
    if ($failed.Count -eq 0) {
        [void]$lines.Add('- none')
    } else {
        foreach ($gate in $failed) { [void]$lines.Add("- $($gate.name): $($gate.actual)") }
    }
    [void]$lines.Add('')
    [void]$lines.Add('## Evidence')
    [void]$lines.Add("- active queue tasks: $($Report.evidence.queue.activeTaskCount)")
    [void]$lines.Add("- weak active task specs: $($Report.evidence.queue.weakActiveTaskCount)")
    [void]$lines.Add("- candidate planning score: $($Report.evidence.selfCodingCandidate.score)")
    [void]$lines.Add("- duplicate pressure: $($Report.evidence.selfCodingLedger.duplicatePressure)")
    [void]$lines.Add("- threshold planningQuality: $($Report.evidence.thresholdValidate.thresholds.planningQuality)")
    return ($lines -join "`r`n")
}

function Invoke-QualityGate {
    $runId = Get-Date -Format 'yyyyMMddTHHmmss'
    $queue = Read-JsonFile -Path $QueuePath
    $activeTasks = @()
    if ($queue -and $queue.tasks) {
        $activeTasks = @($queue.tasks | Where-Object { $_.status -in @('pending','running','blocked','quarantined') })
    }
    $taskScores = @($activeTasks | ForEach-Object { Get-TaskPlanningScore -Task $_ })
    $weakTasks = @($taskScores | Where-Object { $_.pass -ne $true })
    $candidate = Read-JsonFile -Path (Join-Path $SelfCodingArtifactRoot 'model_generated_candidate_task.json')
    $candidateScore = Get-CandidatePlanningScore -CandidateRecord $candidate
    $ledger = $null
    try { $ledger = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $scriptRoot 'idle-self-coding-ledger-summary.ps1') -Mode Validate -ArtifactRoot $SelfCodingArtifactRoot | ConvertFrom-Json } catch {}
    $threshold = $null
    try { $threshold = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $scriptRoot 'idle-improvement-thresholds.ps1') -Mode Validate -ArtifactRoot $SelfCodingArtifactRoot | ConvertFrom-Json } catch {}

    $agentOk = Test-FileContainsAll -Path 'blunder/native_agent.py' -Patterns @('validate_change_path','snapshot_changes','restore_snapshot','run_validation')
    $shellOk = Test-FileContainsAll -Path 'blunder/shell/service.py' -Patterns @('providerApproved','validate_task','blunder_native_agent.py')
    $artifactDir = Resolve-RepoPath -Path $ArtifactRoot
    New-Item -ItemType Directory -Force -Path $artifactDir | Out-Null

    $noUnsafeActive = (@($taskScores | Where-Object { $_.issues -contains 'benchmark_execution_scope' -or $_.issues -contains 'secret_scope' -or ($_.issues -join ',') -match 'unsafe_flag' }).Count -eq 0)
    $gates = @(
        (New-Gate -Name 'queueReadable' -Pass ($queue -ne $null) -Weight 10 -Actual "queuePresent=$($queue -ne $null); activeTasks=$($activeTasks.Count)" -Blocker 'Idle queue must be readable.'),
        (New-Gate -Name 'noUnsafeActiveTaskSpecs' -Pass $noUnsafeActive -Weight 14 -Actual "unsafeActive=$((-not $noUnsafeActive)); weakActive=$($weakTasks.Count)" -Blocker 'Active planning tasks must not request benchmark execution, secrets, provider spend, or destructive flags.'),
        (New-Gate -Name 'activeTaskPlanningQuality' -Pass ($weakTasks.Count -le 2) -Weight 10 -Actual "weakActiveTaskCount=$($weakTasks.Count)" -Blocker 'Too many active tasks have weak planning contracts.'),
        (New-Gate -Name 'candidatePlanningContract' -Pass ([bool]$candidateScore.pass) -Weight 16 -Actual "score=$($candidateScore.score); issues=$($candidateScore.issues -join ',')" -Blocker 'Latest self-coding candidate needs concrete evidence, validation, rollback, and promotion-stop fields.'),
        (New-Gate -Name 'thresholdPlanningQualityEnabled' -Pass ($threshold -and $threshold.thresholds.planningQuality -ge 85) -Weight 12 -Actual "planningQuality=$($threshold.thresholds.planningQuality)" -Blocker 'Threshold scorer must enforce planningQuality.'),
        (New-Gate -Name 'selfCodingDuplicatePressureLow' -Pass ($ledger -and [string]$ledger.duplicatePressure -ne 'PRESENT') -Weight 10 -Actual "duplicatePressure=$($ledger.duplicatePressure)" -Blocker 'Duplicate self-coding pressure must stay low.'),
        (New-Gate -Name 'nativeAgentRollbackContract' -Pass $agentOk -Weight 8 -Actual "nativeAgentContractPresent=$agentOk" -Blocker 'Native Blunder must retain bounded paths, snapshots, validation, and rollback.'),
        (New-Gate -Name 'focusShellApprovalContract' -Pass $shellOk -Weight 8 -Actual "shellApprovalContractPresent=$shellOk" -Blocker 'Focus Shell must require explicit approval before native model-backed tasks.'),
        (New-Gate -Name 'qualityArtifactWritable' -Pass (Test-Path -LiteralPath $artifactDir) -Weight 6 -Actual "artifactRoot=$artifactDir" -Blocker 'Quality gate artifact directory must be writable.'),
        (New-Gate -Name 'noBenchmarkExecutionPolicy' -Pass $true -Weight 6 -Actual 'launchesStarted=false; providersCalled=false; queueMutation=false' -Blocker 'This gate must remain no-launch and no-provider.')
    )

    $score = 0
    $maxScore = 0
    foreach ($gate in $gates) {
        $maxScore += [int]$gate.weight
        if ($gate.pass -eq $true) { $score += [int]$gate.weight }
    }
    $scorePercent = if ($maxScore -gt 0) { [Math]::Round(($score / $maxScore) * 100, 2) } else { 0 }
    $failed = @($gates | Where-Object { $_.pass -ne $true })
    $verdict = if ($failed.Count -eq 0 -and $scorePercent -ge 95) { 'PLANNING_CODING_ELITE_READY' } elseif ($scorePercent -ge 85) { 'PLANNING_CODING_STRONG_WITH_NOTES' } else { 'PLANNING_CODING_REPAIR_REQUIRED' }

    $report = [ordered]@{
        classification = $classification
        timestamp = (Get-Date -Format 'o')
        mode = $Mode
        runId = $runId
        score = $score
        maxScore = $maxScore
        scorePercent = $scorePercent
        verdict = $verdict
        gates = $gates
        evidence = [ordered]@{
            queue = [ordered]@{
                queuePath = Resolve-RepoPath -Path $QueuePath
                activeTaskCount = $activeTasks.Count
                weakActiveTaskCount = $weakTasks.Count
                taskScores = $taskScores
            }
            selfCodingCandidate = $candidateScore
            selfCodingLedger = $ledger
            thresholdValidate = $threshold
        }
        policy = [ordered]@{
            launchesStarted = $false
            providersCalled = $false
            queueMutationPerformed = $false
            benchmarkWorkExcluded = $true
        }
    }
    $jsonPath = Join-Path $ArtifactRoot "planning_coding_quality_gate.$runId.json"
    $mdPath = Join-Path $ArtifactRoot "planning_coding_quality_gate.$runId.md"
    Write-JsonFile -Value $report -Path $jsonPath
    Write-JsonFile -Value $report -Path (Join-Path $ArtifactRoot 'planning_coding_quality_gate.latest.json')
    $markdown = New-MarkdownReport -Report $report
    Write-TextFile -Value $markdown -Path $mdPath
    Write-TextFile -Value $markdown -Path (Join-Path $ArtifactRoot 'planning_coding_quality_gate.latest.md')
    $report['jsonPath'] = Resolve-RepoPath -Path $jsonPath
    $report['markdownPath'] = Resolve-RepoPath -Path $mdPath
    return $report
}

$result = Invoke-QualityGate
$result | ConvertTo-Json -Depth 80
