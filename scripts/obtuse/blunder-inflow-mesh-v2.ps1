#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Validate','Start','Stop','Status','AuditOnly','RunOnce','IngestOnly','ExtractOnly','ReplayOnly','PromoteEligibleOnly','IdleAutonomySafeLoop')]
    [string]$Mode = 'Status'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$artifactRoot = Join-Path $repoRoot 'artifacts\blunder\inflow'
$runtimeRoot = Join-Path $repoRoot 'artifacts\blunder\inflow-runtime'
$statePath = Join-Path $runtimeRoot 'runtime_state.json'
$stopPath = Join-Path $runtimeRoot 'stop_requested.json'

function Write-JsonFile {
    param([Parameter(Mandatory)] [object]$Value, [Parameter(Mandatory)] [string]$Path)
    $parent = Split-Path $Path -Parent
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $Value | ConvertTo-Json -Depth 50 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Read-JsonFile {
    param([Parameter(Mandatory)] [string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    return Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json -ErrorAction Stop
}

function Invoke-Consumer {
    param([Parameter(Mandatory)] [string]$ConsumerMode)
    Push-Location $repoRoot
    try {
        $output = & python -m blunder.inflow.consumer --repo-root $repoRoot --artifact-root $artifactRoot --mode $ConsumerMode 2>&1
        $exit = $LASTEXITCODE
        return [ordered]@{
            mode = $ConsumerMode
            exitCode = $exit
            output = ($output -join "`n")
        }
    }
    finally {
        Pop-Location
    }
}

function Invoke-PythonTests {
    $stdoutPath = Join-Path $env:TEMP "blunder-inflow-tests-stdout-$([Guid]::NewGuid().ToString('N')).log"
    $stderrPath = Join-Path $env:TEMP "blunder-inflow-tests-stderr-$([Guid]::NewGuid().ToString('N')).log"
    $process = Start-Process -FilePath 'python' `
        -ArgumentList @('-m','unittest','discover','-s','tests') `
        -WorkingDirectory $repoRoot `
        -NoNewWindow `
        -Wait `
        -PassThru `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath
    $stdout = ''
    $stderr = ''
    if (Test-Path -LiteralPath $stdoutPath) { $stdout = [System.IO.File]::ReadAllText($stdoutPath) }
    if (Test-Path -LiteralPath $stderrPath) { $stderr = [System.IO.File]::ReadAllText($stderrPath) }
    Remove-Item -LiteralPath $stdoutPath,$stderrPath -Force -ErrorAction SilentlyContinue
    return [ordered]@{
        exitCode = $process.ExitCode
        output = ($stdout + $stderr)
    }
}

if ($Mode -eq 'Stop') {
    $stopRecord = [ordered]@{
        classification = 'BLUNDER_RECURSIVE_DATA_INFLOW_MESH_V2'
        requested = $true
        requestedAt = (Get-Date -Format 'o')
    }
    Write-JsonFile -Value $stopRecord -Path $stopPath
    Write-JsonFile -Value ([ordered]@{
        classification = 'BLUNDER_RECURSIVE_DATA_INFLOW_MESH_V2'
        status = 'STOPPED'
        updatedAt = (Get-Date -Format 'o')
        reason = 'Operator stop requested.'
        artifactRoot = $artifactRoot
    }) -Path $statePath
    Read-JsonFile -Path $statePath | ConvertTo-Json -Depth 50
    return
}

if ($Mode -eq 'Status') {
    [ordered]@{
        classification = 'BLUNDER_RECURSIVE_DATA_INFLOW_MESH_V2'
        mode = 'Status'
        timestamp = (Get-Date -Format 'o')
        runtime = (Read-JsonFile -Path $statePath)
        finalReport = (Read-JsonFile -Path (Join-Path $artifactRoot 'final_report.json'))
        validationSummary = (Read-JsonFile -Path (Join-Path $artifactRoot 'validation_summary.json'))
        stopRequested = (Test-Path -LiteralPath $stopPath)
        artifactRoot = $artifactRoot
    } | ConvertTo-Json -Depth 80
    return
}

if ($Mode -eq 'Validate') {
    $testRun = Invoke-PythonTests
    $testExit = $testRun.exitCode
    $run = Invoke-Consumer -ConsumerMode 'RunOnce'
    $testSummary = [ordered]@{
        command = 'python -m unittest discover -s tests'
        exitCode = $testExit
        output = $testRun.output
        passed = ($testExit -eq 0)
    }
    Write-JsonFile -Value $testSummary -Path (Join-Path $artifactRoot 'test_summary.json')
    $finalReportPath = Join-Path $artifactRoot 'final_report.json'
    if (Test-Path -LiteralPath $finalReportPath) {
        $report = Read-JsonFile -Path $finalReportPath
        $report.tests_run = $testSummary
        if ($report.PSObject.Properties.Name -contains 'pass_fail_summary') {
            $report.pass_fail_summary | Add-Member -NotePropertyName tests -NotePropertyValue $testSummary -Force
        }
        Write-JsonFile -Value $report -Path $finalReportPath
    }
    [ordered]@{
        classification = 'BLUNDER_RECURSIVE_DATA_INFLOW_MESH_V2'
        mode = 'Validate'
        timestamp = (Get-Date -Format 'o')
        testsExitCode = $testExit
        testsOutput = $testRun.output
        runOnce = $run
        result = $(if ($testExit -eq 0 -and $run.exitCode -eq 0) { 'PASS' } else { 'FAIL' })
    } | ConvertTo-Json -Depth 80
    if ($testExit -ne 0 -or $run.exitCode -ne 0) { exit 1 }
    return
}

if ($Mode -eq 'Start') {
    if (Test-Path -LiteralPath $stopPath) {
        Remove-Item -LiteralPath $stopPath -Force
    }
    Write-JsonFile -Value ([ordered]@{
        classification = 'BLUNDER_RECURSIVE_DATA_INFLOW_MESH_V2'
        status = 'RUNNING'
        updatedAt = (Get-Date -Format 'o')
        artifactRoot = $artifactRoot
    }) -Path $statePath
    $run = Invoke-Consumer -ConsumerMode 'IdleAutonomySafeLoop'
    Write-JsonFile -Value ([ordered]@{
        classification = 'BLUNDER_RECURSIVE_DATA_INFLOW_MESH_V2'
        status = $(if ($run.exitCode -eq 0) { 'STOPPED_CLEANLY_AFTER_SAFE_LOOP' } else { 'STOPPED_AFTER_FAILURE' })
        updatedAt = (Get-Date -Format 'o')
        artifactRoot = $artifactRoot
        lastRun = $run
    }) -Path $statePath
    Read-JsonFile -Path $statePath | ConvertTo-Json -Depth 80
    if ($run.exitCode -ne 0) { exit 1 }
    return
}

$runMode = $Mode
$runResult = Invoke-Consumer -ConsumerMode $runMode
$runResult | ConvertTo-Json -Depth 80
if ($runResult.exitCode -ne 0) { exit 1 }
