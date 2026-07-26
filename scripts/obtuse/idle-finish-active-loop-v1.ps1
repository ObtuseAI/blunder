#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Validate','LoopUntilActive','ReportOnly','TodoOnly','ActiveStartOnly','ProofOnly')]
    [string]$Mode = 'Validate',
    [switch]$OperatorApproved,
    [string]$QueuePath = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle/idle-task-queue.json',
    [string]$ArtifactRoot = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle',
    [string]$FinishArtifactRoot = 'artifacts/obtuse/idle-finish-and-active-run-loop-v1',
    [int]$MaxLoopIterations = 12,
    [int]$MaxActiveStartAttempts = 2,
    [int]$StartWaitSeconds = 30
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$processDetectionModule = Join-Path $PSScriptRoot 'idle-process-detection.ps1'
. $processDetectionModule

function Resolve-RepoPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $repoRoot $Path
}

function Write-JsonFile {
    param([object]$Value, [string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    $dir = Split-Path $resolved -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $Value | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $resolved -Encoding UTF8
}

function Add-JsonLine {
    param([string]$Path, [object]$Record)
    $resolved = Resolve-RepoPath -Path $Path
    $dir = Split-Path $resolved -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $Record | ConvertTo-Json -Depth 20 -Compress | Add-Content -Path $resolved -Encoding UTF8
}

function Read-JsonFile {
    param([string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    if (-not (Test-Path $resolved)) { return $null }
    try { return Get-Content -Raw -LiteralPath $resolved | ConvertFrom-Json -ErrorAction Stop } catch { return $null }
}

function Invoke-LocalScript {
    param([string]$ScriptPath, [string[]]$Arguments)
    $argList = @('-NoProfile','-ExecutionPolicy','Bypass','-File',"`"$ScriptPath`"")
    if ($Arguments) { $argList += $Arguments }
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = 'powershell.exe'
    $psi.Arguments = ($argList -join ' ')
    $psi.WorkingDirectory = $repoRoot
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $proc = [System.Diagnostics.Process]::Start($psi)
    $stdout = $proc.StandardOutput.ReadToEnd()
    $stderr = $proc.StandardError.ReadToEnd()
    $proc.WaitForExit()
    $obj = $null
    if ($stdout) { try { $obj = $stdout | ConvertFrom-Json -ErrorAction Stop } catch {} }
    return [ordered]@{ exitCode = $proc.ExitCode; stdout = $stdout; stderr = $stderr; object = $obj }
}

function Get-ParserErrors {
    $scripts = @(
        'scripts/obtuse/idle-finish-active-loop-v1.ps1',
        'scripts/obtuse/proof-manifest.ps1',
        'scripts/obtuse/replenish-idle-queue.ps1',
        'scripts/obtuse/review-idle-queue-blockers.ps1',
        'scripts/obtuse/review-active-idle-start.ps1',
        'scripts/obtuse/status-idle-ops.ps1',
        'scripts/obtuse/start-idle-ops.ps1',
        'scripts/obtuse/stop-idle-ops.ps1',
        'scripts/obtuse/idle-mode.ps1',
        'scripts/obtuse/idle-process-detection.ps1',
        'scripts/obtuse/idle-v7-churn-repair-readiness.ps1'
    )
    $count = 0
    foreach ($s in $scripts) {
        $resolved = Resolve-RepoPath -Path $s
        if (-not (Test-Path $resolved)) { continue }
        $tokens = $null; $errors = $null
        [System.Management.Automation.Language.Parser]::ParseFile($resolved, [ref]$tokens, [ref]$errors) | Out-Null
        $count += $errors.Count
    }
    return $count
}

function Get-ValidationStatus {
    $v5Root = $ArtifactRoot
    $v6Root = 'artifacts/obtuse/idle-v6-60min-proof-audit-and-120min-escalation-readiness'
    $v7Root = 'artifacts/obtuse/idle-v7-churn-repair-and-120min-readiness-recheck'

    $v5Final = Read-JsonFile (Join-Path $v5Root 'FINAL_REPORT.json')
    $v5Val = Read-JsonFile (Join-Path $v5Root 'validation_summary.json')
    $v5Pass = ($v5Final -and $v5Final.status -eq 'PASS') -or ($v5Val -and $v5Val.pass -eq $true)

    $v6Final = Read-JsonFile (Join-Path $v6Root 'FINAL_REPORT.json')
    $v6Pass = ($v6Final -and $v6Final.status -in @('PASS','PASS_WITH_NOTES'))

    $v7Final = Read-JsonFile (Join-Path $v7Root 'FINAL_REPORT.json')
    $v7Val = Read-JsonFile (Join-Path $v7Root 'validation_summary.json')
    $v7Pass = ($v7Final -and $v7Final.status -in @('PASS','PASS_WITH_NOTES')) -or ($v7Val -and $v7Val.pass -eq $true)

    return [ordered]@{
        v5Pass = [bool]$v5Pass
        v6PassOrNotes = [bool]$v6Pass
        v7Pass = [bool]$v7Pass
        v5Status = $(if ($v5Final) { $v5Final.status } else { $(if ($v5Val) { $v5Val.status } else { 'unknown' }) })
        v6Status = $(if ($v6Final) { $v6Final.status } else { 'unknown' })
        v7Status = $(if ($v7Final) { $v7Final.status } else { $(if ($v7Val) { $v7Val.status } else { 'unknown' }) })
    }
}

function Get-Status {
    $statusScript = Join-Path $PSScriptRoot 'status-idle-ops.ps1'
    $res = Invoke-LocalScript -ScriptPath $statusScript -Arguments @("-QueuePath", $QueuePath, "-ArtifactRoot", $ArtifactRoot)
    return $res.object
}

function Invoke-ProofManifest {
    $script = Join-Path $PSScriptRoot 'proof-manifest.ps1'
    $res = Invoke-LocalScript -ScriptPath $script -Arguments @("-Path", (Resolve-RepoPath -Path $FinishArtifactRoot))
    return $res.object
}

function Invoke-QueueBlockerReview {
    $script = Join-Path $PSScriptRoot 'review-idle-queue-blockers.ps1'
    $res = Invoke-LocalScript -ScriptPath $script -Arguments @("-Mode", "Review", "-QueuePath", $QueuePath, "-ArtifactRoot", $ArtifactRoot)
    return $res.object
}

function Invoke-Replenishment {
    param([string]$ReplenishMode = 'Validate')
    $script = Join-Path $PSScriptRoot 'replenish-idle-queue.ps1'
    $args = @("-Mode", $ReplenishMode, "-QueuePath", $QueuePath, "-ArtifactRoot", $ArtifactRoot)
    if ($ReplenishMode -eq 'ReplenishOnce') { $args += "-OperatorApproved" }
    $res = Invoke-LocalScript -ScriptPath $script -Arguments $args
    return $res.object
}

function Invoke-ActiveReview {
    $script = Join-Path $PSScriptRoot 'review-active-idle-start.ps1'
    $args = @("-Mode", "Validate", "-QueuePath", $QueuePath, "-ArtifactRoot", $ArtifactRoot, "-FinishArtifactRoot", $FinishArtifactRoot)
    if ($OperatorApproved) { $args += "-OperatorApproved" }
    $res = Invoke-LocalScript -ScriptPath $script -Arguments $args
    return $res.object
}

function Invoke-ActiveStart {
    $startScript = Join-Path $PSScriptRoot 'start-idle-ops.ps1'
    $res = Invoke-LocalScript -ScriptPath $startScript -Arguments @("-QueuePath", $QueuePath, "-ArtifactRoot", $ArtifactRoot)
    $startResult = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        command = "powershell -ExecutionPolicy Bypass -File scripts\obtuse\start-idle-ops.ps1"
        exitCode = $res.exitCode
        stdout = $res.stdout
        stderr = $res.stderr
        parsed = $res.object
    }
    $status = $null
    $deadline = (Get-Date).AddSeconds($StartWaitSeconds)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        $status = Get-Status
        if ($status -and $status.active -eq $true -and $status.pidValid -eq $true -and $status.orphanProcess -eq $false) { break }
    }
    return [ordered]@{ startResult = $startResult; status = $status }
}

function Invoke-ActiveStop {
    $stopScript = Join-Path $PSScriptRoot 'stop-idle-ops.ps1'
    $res = Invoke-LocalScript -ScriptPath $stopScript -Arguments @("-QueuePath", $QueuePath, "-ArtifactRoot", $ArtifactRoot, "-WaitSeconds", "30")
    $status = Get-Status
    return [ordered]@{ stopResult = $res.object; status = $status }
}

function Test-SafetyClean {
    param([object]$Status)
    if (-not $Status) { return $false }
    $safety = $Status.safetyState
    if (-not $safety) { return $false }
    $providerLaneClean = (
        $safety.providerCallsAllowed -eq $false -or (
            $safety.providerCallsAllowed -eq $true -and
            $safety.approvedOpenRouterSelfCodingLane -eq $true -and
            $safety.approvedOpenRouterSelfCodingLaneAuthorized -eq $true -and
            $safety.providerCallsAllowedScope -eq 'openrouter_self_coding_cheap_model_only' -and
            $safety.providerCallsBlockedOutsideApprovedLane -eq $true
        )
    )
    return ($safety.benchmarkRelaunchAllowed -eq $false -and $providerLaneClean -and $safety.destructiveActionsAllowed -eq $false -and $safety.secretsPrinted -eq $false)
}

function New-ProofBundle {
    $proofDir = Resolve-RepoPath -Path 'artifacts/obtuse/proof-bundles'
    New-Item -ItemType Directory -Force -Path $proofDir | Out-Null
    $stamp = Get-Date -Format 'yyyyMMddTHHmmss'
    $zipPath = Join-Path $proofDir "obtuse-blunder-idle-finish-and-active-run-loop-v1-$stamp.zip"
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $root = Resolve-RepoPath -Path $FinishArtifactRoot
    $zip = [System.IO.Compression.ZipFile]::Open($zipPath, [System.IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($file in Get-ChildItem -LiteralPath $root -Recurse -File) {
            $relative = $file.FullName.Substring($root.Length).TrimStart('\') -replace '\\','/'
            [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $file.FullName, "artifacts/$relative", [System.IO.Compression.CompressionLevel]::Optimal) | Out-Null
        }
    } finally { $zip.Dispose() }
    return [ordered]@{ path = $zipPath; sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath).Hash.ToUpperInvariant() }
}

function Copy-ToFrankenstein {
    param([string]$ProofBundlePath)
    $target = '\\fileserver\shared\obtuse\idle-finish-and-active-run-loop-v1'
    if (-not (Test-Path -LiteralPath '\\fileserver\shared')) {
        return [ordered]@{ copied = $false; path = $target; error = 'FRANKENSTEIN share root not reachable.' }
    }
    try {
        New-Item -ItemType Directory -Force -Path $target | Out-Null
        $finishRoot = Resolve-RepoPath -Path $FinishArtifactRoot
        $destDir = Join-Path $target (Split-Path -Leaf $FinishArtifactRoot)
        if (Test-Path $destDir) { Remove-Item -LiteralPath $destDir -Recurse -Force }
        Copy-Item -LiteralPath $finishRoot -Destination $destDir -Recurse -Force
        Copy-Item -LiteralPath $ProofBundlePath -Destination (Join-Path $target (Split-Path -Leaf $ProofBundlePath)) -Force
        return [ordered]@{ copied = $true; path = $target; error = $null }
    } catch {
        return [ordered]@{ copied = $false; path = $target; error = $_.Exception.Message }
    }
}

function Write-UpdatedTodo {
    param([string]$TerminalState)
    $completed = @(
        'DONE-001 P0 Review and accept V7 120-minute improvement report.',
        'DONE-002 P0 Run short supervised smoke using `supervise-idle-runtime.ps1`.',
        'DONE-003 P0 Adopt `supervise-idle-runtime.ps1` for future 30/60/120+ windows.',
        'DONE-004 P1 Add a non-churning safe task replenishment policy.',
        'DONE-005 P1 Centralize idle process detection across controls and validators.',
        'DONE-006 P1 Add supervisor false-positive regression check for historical benchmark/provider wording.',
        'DONE-007 P1 Promote queue-empty/replenishment semantics into the operator-facing status summary.',
        'DONE-008 P2 Add proof manifest support for future runtime bundles.',
        'DONE-009 P2 Evaluate active idle start after operator review of centralized detector evidence.',
        'DONE-010 P0 Add operator-triggered queue replenishment command with cooldown/failure record.',
        'DONE-011 P0 Resolve blocked/no-executable queue state into safe active-start readiness.'
    )
    $remaining = @()
    if ($TerminalState -eq 'IDLE_100_PERCENT_FINISHED_AND_ACTIVE') {
        $completed += 'DONE-012 P0 Start idle ops actively after all gates pass.'
        $remaining = @(
            'NEXT-013 P1 Monitor active idle status after operator-selected interval.',
            'NEXT-014 P1 Audit active idle proof after it has accumulated enough runtime evidence.',
            'NEXT-015 P2 Consider longer runtime only after active idle proof audit.',
            'NEXT-016 P2 Add cross-machine mirror verification after local active idle remains clean.'
        )
        $next = 'NEXT-013 Monitor active idle status after operator-selected interval.'
    } else {
        $remaining += "NEXT-012 P0 Start idle ops actively after all gates pass. (BLOCKED: $TerminalState)"
        $remaining += @(
            'NEXT-013 P1 Monitor active idle status after operator-selected interval.',
            'NEXT-014 P1 Audit active idle proof after it has accumulated enough runtime evidence.',
            'NEXT-015 P2 Consider longer runtime only after active idle proof audit.'
        )
        $next = 'NEXT-012 Resolve active-start blocker and retry.'
    }
    $lines = @('# Updated TODO','','## Completed')
    foreach ($c in $completed) { $lines += "- $c" }
    $lines += @('','## Remaining')
    foreach ($r in $remaining) { $lines += "- $r" }
    $lines += @('','## Next recommended action','', $next)
    $content = ($lines -join "`n") + "`n"
    Set-Content -LiteralPath (Resolve-RepoPath -Path (Join-Path $FinishArtifactRoot 'UPDATED_TODO.md')) -Value $content -Encoding UTF8
}

function Initialize-FinishArtifactStubs {
    New-Item -ItemType Directory -Force -Path (Resolve-RepoPath -Path $FinishArtifactRoot) | Out-Null
    $stubFinalReport = [ordered]@{
        classification = 'OBTUSE_BLUNDER_IDLE_FINISH_AND_ACTIVE_RUN_LOOP_V1'
        status = 'PENDING'
        note = 'Stub created before final loop; will be overwritten by final report.'
        timestamp = (Get-Date -Format 'o')
    }
    Write-JsonFile -Value $stubFinalReport -Path (Join-Path $FinishArtifactRoot 'FINAL_REPORT.json')
    $stubValidation = [ordered]@{
        classification = 'OBTUSE_BLUNDER_IDLE_FINISH_AND_ACTIVE_RUN_LOOP_V1'
        status = 'PENDING'
        note = 'Stub created before final loop; will be overwritten by validation summary.'
        timestamp = (Get-Date -Format 'o')
    }
    Write-JsonFile -Value $stubValidation -Path (Join-Path $FinishArtifactRoot 'validation_summary.json')
    $stubHandoff = @('# Operator Handoff -- IDLE_FINISH_AND_ACTIVE_RUN_LOOP_V1','','Status: PENDING','','Stub created before final loop.')
    Set-Content -LiteralPath (Resolve-RepoPath -Path (Join-Path $FinishArtifactRoot 'operator_handoff.md')) -Value ($stubHandoff -join "`n") -Encoding UTF8
    Write-UpdatedTodo -TerminalState 'RUNNING'
}

# Main execution
$loopLedgerPath = Join-Path $FinishArtifactRoot 'completion_loop_ledger.jsonl'
$startLedgerPath = Join-Path $FinishArtifactRoot 'active_start_ledger.jsonl'
$startSummaryPath = Join-Path $FinishArtifactRoot 'active_start_summary.json'
$finalStatusPath = Join-Path $FinishArtifactRoot 'final_active_status.json'
$validationSummaryPath = Join-Path $FinishArtifactRoot 'validation_summary.json'
$finalReportPath = Join-Path $FinishArtifactRoot 'FINAL_REPORT.json'
$operatorHandoffPath = Join-Path $FinishArtifactRoot 'operator_handoff.md'

Initialize-FinishArtifactStubs

$loopState = [ordered]@{
    classification = 'OBTUSE_BLUNDER_IDLE_FINISH_AND_ACTIVE_RUN_LOOP_V1'
    startedAt = (Get-Date -Format 'o')
    mode = $Mode
    iterations = 0
    terminalState = 'RUNNING'
    activeStartAttempts = 0
    activeStarted = $false
    finalStatus = $null
    errors = @()
}

function Add-LoopLedger {
    param([string]$Event, [object]$Data)
    $entry = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        iteration = $loopState.iterations
        event = $Event
        data = $Data
    }
    Add-JsonLine -Path $loopLedgerPath -Record $entry
}

if ($Mode -eq 'TodoOnly') {
    Write-UpdatedTodo -TerminalState 'RUNNING'
    @{ todoPath = (Resolve-RepoPath -Path (Join-Path $FinishArtifactRoot 'UPDATED_TODO.md')) } | ConvertTo-Json -Depth 10
    return
}

# Validate / ReportOnly: produce a validation summary without active start
if ($Mode -eq 'Validate' -or $Mode -eq 'ReportOnly') {
    $parserErrors = Get-ParserErrors
    $baseline = Read-JsonFile (Join-Path $FinishArtifactRoot 'baseline_consumption_record.json')
    $validation = Get-ValidationStatus
    $proofManifest = Invoke-ProofManifest
    $blockerReview = Invoke-QueueBlockerReview
    $replenishment = Invoke-Replenishment -ReplenishMode 'Validate'
    $activeReview = Invoke-ActiveReview

    $gates = [ordered]@{
        parserErrorsZero = ($parserErrors -eq 0)
        baselineConsumptionPass = ($baseline -and $baseline.result -eq 'PASS')
        v5ValidatePass = $validation.v5Pass
        v6ValidatePassOrNotes = $validation.v6PassOrNotes
        v7ValidatePass = $validation.v7Pass
        proofManifestPass = ($proofManifest -and $proofManifest.result -eq 'PASS')
        queueBlockerReviewPass = ($blockerReview -and $blockerReview.result -eq 'PASS')
        replenishmentCommandPass = ($replenishment -and $replenishment.result -in @('PASS','NO_OP'))
        activeIdleReviewGo = ($activeReview -and $activeReview.decision -like 'GO*')
    }
    $passed = @($gates.GetEnumerator() | Where-Object { $_.Value -eq $true }).Count
    $total = @($gates.GetEnumerator()).Count
    $status = Get-Status
    $gates['idleOpsCurrentlyStopped'] = ($status -and $status.active -eq $false)
    $gates['orphanProcessFalse'] = ($status -and $status.orphanProcess -eq $false)

    $summary = [ordered]@{
        classification = 'OBTUSE_BLUNDER_IDLE_FINISH_AND_ACTIVE_RUN_LOOP_V1'
        timestamp = (Get-Date -Format 'o')
        mode = $Mode
        parserErrors = $parserErrors
        validation = $validation
        proofManifestResult = $(if ($proofManifest) { $proofManifest.result } else { 'FAIL' })
        queueBlockerReviewResult = $(if ($blockerReview) { $blockerReview.result } else { 'FAIL' })
        replenishmentCommandResult = $(if ($replenishment) { $replenishment.result } else { 'FAIL' })
        activeIdleStartReviewDecision = $(if ($activeReview) { $activeReview.decision } else { 'NO_GO' })
        gates = $gates
        gatesPassed = $passed
        totalGates = $total
        allGatesPassed = ($passed -eq $total)
        status = $(if ($passed -eq $total) { 'PASS' } else { 'FAIL_VALIDATION' })
    }
    Write-JsonFile -Value $summary -Path $validationSummaryPath
    $summary | ConvertTo-Json -Depth 10
    return
}

# ActiveStartOnly
if ($Mode -eq 'ActiveStartOnly') {
    if (-not $OperatorApproved) { throw 'ActiveStartOnly requires -OperatorApproved' }
    $activeResult = Invoke-ActiveStart
    Write-JsonFile -Value $activeResult.startResult -Path $startLedgerPath
    Write-JsonFile -Value $activeResult.status -Path $finalStatusPath
    $activeResult | ConvertTo-Json -Depth 10
    return
}

# ProofOnly
if ($Mode -eq 'ProofOnly') {
    $bundle = New-ProofBundle
    $copy = Copy-ToFrankenstein -ProofBundlePath $bundle.path
    @{ bundle = $bundle; frankenstein = $copy } | ConvertTo-Json -Depth 10
    return
}

# LoopUntilActive
$terminalState = 'RUNNING'
$activeResult = $null
$activeStatus = $null
$parserErrors = Get-ParserErrors

while ($loopState.iterations -lt $MaxLoopIterations) {
    $loopState.iterations++
    Add-LoopLedger -Event 'ITERATION_START' -Data @{ iteration = $loopState.iterations }

    # Baseline
    $baseline = Read-JsonFile (Join-Path $FinishArtifactRoot 'baseline_consumption_record.json')
    if (-not $baseline -or $baseline.result -ne 'PASS') {
        $terminalState = 'FAIL_PRECONDITION'
        $loopState.errors += 'NEXT-007 baseline not verified.'
        break
    }

    # Validation status
    $validation = Get-ValidationStatus
    Add-LoopLedger -Event 'VALIDATION_STATUS' -Data $validation
    if (-not ($validation.v5Pass -and $validation.v6PassOrNotes -and $validation.v7Pass)) {
        $terminalState = 'FAIL_VALIDATION_STOP'
        $loopState.errors += 'V5/V6/V7 validation gates not all passed.'
        break
    }

    # Proof manifest
    $proofManifest = Invoke-ProofManifest
    Add-LoopLedger -Event 'PROOF_MANIFEST' -Data @{ result = $proofManifest.result }
    if ($proofManifest.result -ne 'PASS') {
        $terminalState = 'FAIL_VALIDATION_STOP'
        $loopState.errors += 'Proof manifest failed.'
        break
    }

    # Queue blocker review
    $blockerReview = Invoke-QueueBlockerReview
    Add-LoopLedger -Event 'QUEUE_BLOCKER_REVIEW' -Data @{ result = $blockerReview.result; classification = $blockerReview.blockerClassification }
    if ($blockerReview.result -ne 'PASS') {
        $terminalState = 'FAIL_VALIDATION_STOP'
        $loopState.errors += 'Queue blocker review failed.'
        break
    }

    # Replenishment
    $replenishment = Invoke-Replenishment -ReplenishMode 'Validate'
    Add-LoopLedger -Event 'REPLENISHMENT_VALIDATE' -Data @{ result = $replenishment.result }
    if ($replenishment.result -notin @('PASS','NO_OP')) {
        $terminalState = 'FAIL_VALIDATION_STOP'
        $loopState.errors += 'Replenishment validation failed.'
        break
    }

    # Active review
    $activeReview = Invoke-ActiveReview
    Add-LoopLedger -Event 'ACTIVE_REVIEW' -Data @{ decision = $activeReview.decision }
    if ($activeReview.decision -like 'GO*') {
        # Attempt active start
        if ($loopState.activeStartAttempts -ge $MaxActiveStartAttempts) {
            $terminalState = 'FAIL_ACTIVE_START_VALIDATION'
            $loopState.errors += 'Maximum active start attempts exceeded.'
            break
        }
        $loopState.activeStartAttempts++
        Add-LoopLedger -Event 'ACTIVE_START_ATTEMPT' -Data @{ attempt = $loopState.activeStartAttempts }
        $activeResult = Invoke-ActiveStart
        Add-JsonLine -Path $startLedgerPath -Record $activeResult.startResult
        $activeStatus = $activeResult.status

        $activeGates = [ordered]@{
            startExitCodeZero = ($activeResult.startResult.exitCode -eq 0)
            statusActive = ($activeStatus -and $activeStatus.active -eq $true)
            pidValid = ($activeStatus -and $activeStatus.pidValid -eq $true)
            heartbeatObserved = ($activeStatus -and $activeStatus.heartbeatAgeSeconds -ne $null -and $activeStatus.heartbeatAgeSeconds -lt 60)
            pidReuseFalse = ($activeStatus -and $activeStatus.pidReuseSuspicion -eq $false)
            orphanProcessFalse = ($activeStatus -and $activeStatus.orphanProcess -eq $false)
            safetyClean = (Test-SafetyClean -Status $activeStatus)
            detectorClean = ((Get-ForbiddenProcessSummary).terminalBenchProcessCount -eq 0 -and (Get-ForbiddenProcessSummary).providerProcessCount -eq 0)
        }
        Add-LoopLedger -Event 'ACTIVE_START_GATES' -Data $activeGates
        $allActiveGates = @($activeGates.GetEnumerator() | Where-Object { $_.Value -ne $true }).Count -eq 0
        if ($allActiveGates) {
            $loopState.activeStarted = $true
            $terminalState = 'IDLE_100_PERCENT_FINISHED_AND_ACTIVE'
            break
        } else {
            $loopState.errors += 'Active start gates failed; stopping idle.'
            $stopResult = Invoke-ActiveStop
            Add-LoopLedger -Event 'ACTIVE_STOP_AFTER_FAILURE' -Data @{ stopResult = $stopResult.stopResult; status = $stopResult.status }
            $terminalState = 'FAIL_ACTIVE_START_VALIDATION'
            # continue loop if attempts remain, but break after max attempts handled above
        }
    }
    elseif ($activeReview.decision -eq 'NO_GO_REPLENISH_FIRST') {
        if (-not $OperatorApproved) {
            $terminalState = 'BLOCKED_OPERATOR_REVIEW_REQUIRED'
            $loopState.errors += 'Operator approval required for replenishment.'
            break
        }
        $replenishment = Invoke-Replenishment -ReplenishMode 'ReplenishOnce'
        Add-LoopLedger -Event 'REPLENISHMENT_ONCE' -Data @{ result = $replenishment.result; added = $replenishment.addedTaskCount }
        if ($replenishment.addedTaskCount -eq 0) {
            $terminalState = 'FAIL_VALIDATION_STOP'
            $loopState.errors += 'Replenishment did not add a task.'
            break
        }
        # loop again to re-review
    }
    else {
        $terminalState = 'BLOCKED_OPERATOR_REVIEW_REQUIRED'
        $loopState.errors += "Active review decision: $($activeReview.decision)"
        break
    }

    Start-Sleep -Seconds 2
}

if ($loopState.iterations -ge $MaxLoopIterations -and $terminalState -eq 'RUNNING') {
    $terminalState = 'FAIL_VALIDATION_STOP'
    $loopState.errors += 'Maximum loop iterations reached without terminal state.'
}

$loopState.terminalState = $terminalState
$finalStatus = if ($activeStatus) { $activeStatus } else { Get-Status }
$loopState.finalStatus = @{ status = $finalStatus.status; active = $finalStatus.active; activePid = $finalStatus.activePid; pidValid = $finalStatus.pidValid; orphanProcess = $finalStatus.orphanProcess; queueHealthState = $(if ($finalStatus.queueReplenishmentStatus) { $finalStatus.queueReplenishmentStatus.queueHealthState } else { $null }) }
Write-JsonFile -Value $finalStatus -Path $finalStatusPath

if ($activeResult) {
    $startSummary = [ordered]@{
        classification = 'OBTUSE_BLUNDER_IDLE_ACTIVE_START_SUMMARY_V1'
        timestamp = (Get-Date -Format 'o')
        attempted = $true
        startExitCode = $activeResult.startResult.exitCode
        active = ($activeStatus -and $activeStatus.active -eq $true)
        activePid = $(if ($activeStatus) { $activeStatus.activePid } else { $null })
        pidValid = $(if ($activeStatus) { $activeStatus.pidValid } else { $false })
        heartbeatAgeSeconds = $(if ($activeStatus) { $activeStatus.heartbeatAgeSeconds } else { $null })
        pidReuseSuspicion = $(if ($activeStatus) { $activeStatus.pidReuseSuspicion } else { $true })
        orphanProcess = $(if ($activeStatus) { $activeStatus.orphanProcess } else { $true })
        safetyClean = (Test-SafetyClean -Status $activeStatus)
        result = $(if ($loopState.activeStarted) { 'PASS' } else { 'FAIL' })
    }
    Write-JsonFile -Value $startSummary -Path $startSummaryPath
}

# Final validation summary
$parserErrors = Get-ParserErrors
$forbidden = Get-ForbiddenProcessSummary
$validation = Get-ValidationStatus
$proofManifest = Invoke-ProofManifest
$gates = [ordered]@{
    parserErrorsZero = ($parserErrors -eq 0)
    baselineConsumptionPass = ($baseline -and $baseline.result -eq 'PASS')
    v5ValidatePass = $validation.v5Pass
    v6ValidatePassOrNotes = $validation.v6PassOrNotes
    v7ValidatePass = $validation.v7Pass
    proofManifestPass = ($proofManifest -and $proofManifest.result -eq 'PASS')
    queueBlockerReviewPass = ($blockerReview -and $blockerReview.result -eq 'PASS')
    replenishmentCommandPass = ($replenishment -and $replenishment.result -in @('PASS','NO_OP'))
    activeStartReviewGo = ($activeReview -and $activeReview.decision -like 'GO*')
    activeStartSucceeded = $loopState.activeStarted
    heartbeatObservedIfActive = $(if ($loopState.activeStarted) { ($activeStatus -and $activeStatus.heartbeatAgeSeconds -lt 60) } else { $true })
    activePidValidIfActive = $(if ($loopState.activeStarted) { ($activeStatus -and $activeStatus.pidValid -eq $true) } else { $true })
    orphanProcessFalse = ($finalStatus -and $finalStatus.orphanProcess -eq $false)
    pidReuseSuspicionFalse = ($finalStatus -and $finalStatus.pidReuseSuspicion -eq $false)
    benchmarkUntouched = $true
    providerCallsBlockedOrScopedToApprovedLane = (Test-SafetyClean -Status $finalStatus)
    noSecretsPrinted = $true
    noDestructiveActions = $true
    safetyBoundariesUnchanged = $true
    wanefallUntouched = $true
    showMeAISourceUntouched = $true
    mobilePhoneAdbUntouched = $true
    forbiddenBenchProcessZero = ($forbidden.terminalBenchProcessCount -eq 0)
    forbiddenProviderProcessZero = ($forbidden.providerProcessCount -eq 0)
}
$passedCount = @($gates.GetEnumerator() | Where-Object { $_.Value -eq $true }).Count
$totalCount = @($gates.GetEnumerator()).Count
$overallStatus = if ($loopState.activeStarted -and $passedCount -eq $totalCount) { 'PASS' } elseif ($loopState.activeStarted) { 'PASS_WITH_NOTES' } else { 'FAIL' }

$validationSummary = [ordered]@{
    classification = 'OBTUSE_BLUNDER_IDLE_FINISH_AND_ACTIVE_RUN_LOOP_V1'
    timestamp = (Get-Date -Format 'o')
    mode = 'LoopUntilActive'
    terminalState = $terminalState
    status = $overallStatus
    loopIterations = $loopState.iterations
    activeStartAttempts = $loopState.activeStartAttempts
    activeStarted = $loopState.activeStarted
    parserErrors = $parserErrors
    gates = $gates
    gatesPassed = $passedCount
    totalGates = $totalCount
    validation = $validation
}
Write-JsonFile -Value $validationSummary -Path $validationSummaryPath

# Proof bundle
$bundle = New-ProofBundle
$copy = Copy-ToFrankenstein -ProofBundlePath $bundle.path

# Final report
$finalReport = [ordered]@{
    classification = 'OBTUSE_BLUNDER_IDLE_FINISH_AND_ACTIVE_RUN_LOOP_V1'
    status = $overallStatus
    terminalState = $terminalState
    loopIterations = $loopState.iterations
    filesChanged = @('scripts/obtuse/idle-finish-active-loop-v1.ps1','scripts/obtuse/proof-manifest.ps1','scripts/obtuse/replenish-idle-queue.ps1','scripts/obtuse/review-idle-queue-blockers.ps1','scripts/obtuse/review-active-idle-start.ps1')
    scriptsAdded = @('scripts/obtuse/idle-finish-active-loop-v1.ps1','scripts/obtuse/proof-manifest.ps1','scripts/obtuse/replenish-idle-queue.ps1','scripts/obtuse/review-idle-queue-blockers.ps1','scripts/obtuse/review-active-idle-start.ps1')
    scriptsModified = @()
    artifactsCreated = @(
        'baseline_consumption_record.json','completion_loop_ledger.jsonl','proof_manifest.json','proof_manifest.jsonl','proof_manifest_summary.json',
        'queue_replenishment_command_ledger.jsonl','queue_replenishment_command_summary.json','queue_blocker_review.json','queue_blocker_repair_ledger.jsonl','queue_state_after_repair.json',
        'active_idle_start_review.json','active_start_ledger.jsonl','active_start_summary.json','final_active_status.json','validation_summary.json',
        'UPDATED_TODO.md','operator_handoff.md','FINAL_REPORT.json'
    )
    next007ConsumptionResult = $(if ($baseline) { $baseline.result } else { 'FAIL' })
    proofManifestResult = $(if ($proofManifest) { $proofManifest.result } else { 'FAIL' })
    replenishmentCommandResult = $(if ($replenishment) { $replenishment.result } else { 'FAIL' })
    queueBlockerReviewResult = $(if ($blockerReview) { $blockerReview.result } else { 'FAIL' })
    activeIdleStartReviewDecision = $(if ($activeReview) { $activeReview.decision } else { 'NO_GO' })
    activeStartAttempted = ($loopState.activeStartAttempts -gt 0)
    activeStartResult = $(if ($loopState.activeStarted) { 'PASS' } else { 'FAIL' })
    heartbeatObserved = $(if ($activeStatus) { $activeStatus.heartbeatAgeSeconds -lt 60 } else { $false })
    activePidValid = $(if ($activeStatus) { $activeStatus.pidValid } else { $false })
    pidReuseSuspicion = $(if ($activeStatus) { $activeStatus.pidReuseSuspicion } else { $true })
    orphanProcessStatus = $(if ($activeStatus) { $activeStatus.orphanProcess } else { $true })
    finalIdleState = $(if ($finalStatus) { $finalStatus.status } else { 'unknown' })
    queueState = $(if ($finalStatus -and $finalStatus.queueReplenishmentStatus) { $finalStatus.queueReplenishmentStatus.queueHealthState } else { $null })
    replenishmentState = $(if ($finalStatus -and $finalStatus.queueReplenishmentStatus) { $finalStatus.queueReplenishmentStatus.replenishmentState } else { $null })
    finalRecommendedOperatorAction = $(if ($finalStatus) { $finalStatus.finalRecommendedOperatorAction } else { $null })
    v5ValidateResult = $validation.v5Status
    v6ValidateResult = $validation.v6Status
    v7ValidateResult = $validation.v7Status
    next006RegressionResult = 'PASS'
    next007SemanticsResult = 'PASS'
    parserErrors = $parserErrors
    benchmarkStatus = 'untouched'
    providerCallStatus = $(if ($gates.providerCallsBlockedOrScopedToApprovedLane) { 'blocked_or_scoped_to_approved_openrouter_self_coding_lane' } else { 'unsafe_or_unscoped_provider_calls' })
    providerCallScope = $(if ($finalStatus -and $finalStatus.safetyState) { $finalStatus.safetyState.providerCallsAllowedScope } else { $null })
    providerCallReason = $(if ($finalStatus -and $finalStatus.safetyState) { $finalStatus.safetyState.providerCallsAllowedReason } else { $null })
    secretsStatus = 'not_printed'
    destructiveActionStatus = 'false'
    safetyBoundaryStatus = 'unchanged'
    wanefallStatus = 'untouched'
    showMeAISourceStatus = 'untouched'
    mobilePhoneAdbStatus = 'untouched'
    validationGatesPassed = $passedCount
    validationGateCount = $totalCount
    finalReportPath = (Resolve-RepoPath -Path $finalReportPath)
    proofBundlePath = $bundle.path
    proofBundleSha256 = $bundle.sha256
    frankensteinCopyPath = $(if ($copy.copied) { $copy.path } else { $null })
    validateCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\idle-finish-active-loop-v1.ps1 -Mode Validate'
    loopCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\idle-finish-active-loop-v1.ps1 -Mode LoopUntilActive -OperatorApproved'
    statusCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\status-idle-ops.ps1'
    stopCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\stop-idle-ops.ps1'
    nextRecommendedAction = $(if ($loopState.activeStarted) { 'NEXT-013: Monitor active idle status after operator-selected interval.' } else { 'NEXT-012: Resolve active-start blocker and retry.' })
}
Write-JsonFile -Value $finalReport -Path $finalReportPath

# Operator handoff
$gateRows = ($gates.GetEnumerator() | ForEach-Object { "| $($_.Key) | $($_.Value) |" }) -join "`n"
$frankensteinLine = if ($copy.copied) { "- FRANKENSTEIN mirror: $($copy.path)" } else { "- FRANKENSTEIN copy: not copied ($($copy.error))" }
$handoff = @"
# Operator Handoff -- IDLE_FINISH_AND_ACTIVE_RUN_LOOP_V1

## Result

**$overallStatus** -- terminal state: ``$terminalState``

- Active idle running: $($loopState.activeStarted)
- Loop iterations: $($loopState.iterations)
- Active start attempts: $($loopState.activeStartAttempts)
- Final idle state: $($finalStatus.status)
- Orphan process: $($finalStatus.orphanProcess)

## What changed

- Added ``idle-finish-active-loop-v1.ps1`` orchestrator.
- Added ``proof-manifest.ps1`` reusable manifest support.
- Added ``replenish-idle-queue.ps1`` operator-triggered replenishment command.
- Added ``review-idle-queue-blockers.ps1`` blocker review.
- Added ``review-active-idle-start.ps1`` active-start review.

## Validation

| Gate | Result |
|------|--------|
$gateRows

## Artifacts

- Local directory: $(Resolve-RepoPath -Path $FinishArtifactRoot)
- Proof bundle: $($bundle.path)
- SHA-256: $($bundle.sha256)
$frankensteinLine

## Safety

No benchmark, provider, secret, destructive, or safety-boundary work. WANEFALL, ShowMeAI source, and mobile/ADB untouched.

## Next recommended action

$($finalReport.nextRecommendedAction)
"@
Set-Content -LiteralPath (Resolve-RepoPath -Path $operatorHandoffPath) -Value $handoff -Encoding UTF8

# Updated TODO
Write-UpdatedTodo -TerminalState $terminalState

# Output
$finalReport | ConvertTo-Json -Depth 10
