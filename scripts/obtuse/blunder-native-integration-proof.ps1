#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Validate')]
    [string]$Mode = 'Validate'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent

function Test-SourceContract {
    param(
        [Parameter(Mandatory)] [string]$RelativePath,
        [Parameter(Mandatory)] [string[]]$Markers
    )
    $path = Join-Path $repoRoot $RelativePath
    if (-not (Test-Path -LiteralPath $path)) { return $false }
    $text = Get-Content -Raw -LiteralPath $path
    foreach ($marker in $Markers) {
        if (-not $text.Contains($marker)) { return $false }
    }
    return $true
}

function Invoke-NativeIntegrationTests {
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = 'python.exe'
    $info.WorkingDirectory = $repoRoot
    $info.Arguments = '-m unittest -q tests.test_blunder_shell_v2 tests.test_blunder_cognition tests.test_blunder_autonomy_and_evolution tests.test_blunder_capabilities tests.test_blunder_quorum_kernel'
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = [System.Diagnostics.Process]::Start($info)
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    $finished = $process.WaitForExit(120000)
    if (-not $finished) {
        Stop-Process -Id $process.Id -Force
        $process.WaitForExit()
    }
    return [ordered]@{
        pass = ($finished -and $process.ExitCode -eq 0)
        exitCode = $(if ($finished) { $process.ExitCode } else { 124 })
        output = (($stdout.Result + [Environment]::NewLine + $stderr.Result).Trim())
    }
}

$capabilityManifests = @(Get-ChildItem -LiteralPath (Join-Path $repoRoot 'blunder/capability_packs') -Recurse -Filter 'manifest.json' -File)
$capabilityManifestContractsValid = ($capabilityManifests.Count -ge 7)
foreach ($manifest in $capabilityManifests) {
    $contract = Get-Content -LiteralPath $manifest.FullName -Raw | ConvertFrom-Json
    if ($contract.schema -ne 'obtuse.blunder.capability-plugin.v1' -or $contract.firstParty -ne $true -or $contract.enabled -ne $true) {
        $capabilityManifestContractsValid = $false
    }
}

$checks = [ordered]@{
    cognition = Test-SourceContract -RelativePath 'blunder/cognition.py' -Markers @('build_evidence','validate_strategy','select_plan','history_adjustment')
    nativeAgent = Test-SourceContract -RelativePath 'blunder/native_agent.py' -Markers @('request_strategy','snapshot_changes','restore_snapshot','selected_targets')
    focusShell = Test-SourceContract -RelativePath 'blunder/shell/service.py' -Markers @('providerApproved','validate_task','blunder_native_agent.py')
    missionControl = (
        (Test-SourceContract -RelativePath 'blunder/shell/intelligence.py' -Markers @('task_briefing','mission_control_state','restore_checkpoint','checkpoint_is_current','workspace_search')) -and
        (Test-SourceContract -RelativePath 'blunder/shell/server.py' -Markers @('/api/briefing','/api/missions','/api/checkpoints/','/api/search')) -and
        (Test-SourceContract -RelativePath 'blunder/shell/static/app.js' -Markers @('buildBriefing','loadMissions','openCheckpoint','restoreSelectedCheckpoint'))
    )
    capabilityFoundry = (
        (Test-SourceContract -RelativePath 'blunder/capabilities/registry.py' -Markers @('load_registry','route_task','action_for_tool','dispatch_hooks','ALLOWED_PERMISSIONS','ALLOWED_ACTIONS')) -and
        (Test-SourceContract -RelativePath 'blunder/shell/server.py' -Markers @('/api/capabilities','route_capabilities','invoke_capability_tool')) -and
        $capabilityManifestContractsValid
    )
    cohesiveAutonomy = Test-SourceContract -RelativePath 'scripts/obtuse/blunder-continuous-autonomy.ps1' -Markers @('IDLE_RUNTIME_BOUND','Get-AutonomyRuntimeProcessState','identityMatch')
    idleResilience = Test-SourceContract -RelativePath 'scripts/obtuse/idle-mode.ps1' -Markers @('MaxConsecutiveCycleFailures','idle-cycle-failure-ledger.jsonl','Invoke-BoundedJsonPowerShell','terminalEvidenceArchived')
    benchmarkIsolation = Test-SourceContract -RelativePath 'scripts/obtuse/blunder-full-autonomy-common.ps1' -Markers @('Test-BlunderBenchmarkIsolationContract','runtimeLaunchesBenchmark')
}
$tests = Invoke-NativeIntegrationTests
$failedChecks = @($checks.Keys | Where-Object { $checks[$_] -ne $true })
if ($tests.pass -ne $true) { $failedChecks += 'nativeIntegrationTests' }
$pass = ($failedChecks.Count -eq 0)

[ordered]@{
    schema = 'obtuse.blunder.native-integration-proof.v1'
    timestamp = (Get-Date -Format 'o')
    mode = $Mode
    status = $(if ($pass) { 'PASS' } else { 'FAIL' })
    pass = $pass
    checks = $checks
    tests = $tests
    failedChecks = $failedChecks
    providerCallsStarted = $false
    queueMutationPerformed = $false
    benchmarkLaunchesStarted = $false
} | ConvertTo-Json -Depth 20
