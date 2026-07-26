#requires -Version 5.1
param(
    [ValidateSet('Validate','ReportOnly')]
    [string]$Mode = 'Validate',
    [string]$ArtifactRoot = 'artifacts/obtuse/blunder-s-plus-readiness-v1'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent

function Resolve-RepoPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $repoRoot $Path
}

function Read-JsonFile {
    param([string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    if (-not (Test-Path -LiteralPath $resolved)) { return $null }
    try { return Get-Content -Raw -LiteralPath $resolved | ConvertFrom-Json -ErrorAction Stop } catch { return $null }
}

function Write-JsonFile {
    param([object]$Value, [string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    $parent = Split-Path $resolved -Parent
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $Value | ConvertTo-Json -Depth 60 | Set-Content -LiteralPath $resolved -Encoding UTF8
}

function Invoke-JsonScript {
    param([string]$ScriptName, [string[]]$Arguments)
    $scriptPath = Resolve-RepoPath -Path (Join-Path 'scripts/obtuse' $ScriptName)
    $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $scriptPath @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) { $exitCode = 0 }
    $raw = ($output | ForEach-Object { [string]$_ }) -join "`n"
    $parsed = $null
    try { $parsed = $raw | ConvertFrom-Json -ErrorAction Stop } catch {}
    return [ordered]@{ exitCode = $exitCode; parsed = $parsed; raw = $raw }
}

function Get-LatestDryRun {
    $root = Resolve-RepoPath -Path 'artifacts/obtuse/blunder-dunce-import-and-benchmark-appliance-mode-v1'
    if (-not (Test-Path -LiteralPath $root)) { return $null }
    $files = @(Get-ChildItem -LiteralPath $root -Recurse -Filter 'dry_run_summary.json' -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)
    if ($files.Count -eq 0) { return $null }
    $obj = Read-JsonFile -Path $files[0].FullName
    if ($obj) { $obj | Add-Member -NotePropertyName path -NotePropertyValue $files[0].FullName -Force }
    return $obj
}

$scorecardRun = Invoke-JsonScript -ScriptName 'blunder-s-tier-operating-scorecard.ps1' -Arguments @('-Mode','Validate')
$repairRun = Invoke-JsonScript -ScriptName 'blunder-capability-repair-packets.ps1' -Arguments @('-Mode','Validate')
$autonomyRun = Invoke-JsonScript -ScriptName 'blunder-continuous-autonomy.ps1' -Arguments @('-Mode','Status')
$contract = Read-JsonFile -Path (Join-Path $ArtifactRoot 'self_improvement_loop_contract.json')
$dryRun = Get-LatestDryRun

$gates = @(
    [ordered]@{ name = 'sTierScorecardPerfect'; pass = ($scorecardRun.parsed -and [int]$scorecardRun.parsed.score -eq [int]$scorecardRun.parsed.maxScore -and [string]$scorecardRun.parsed.verdict -eq 'S_TIER_READY'); actual = "score=$($scorecardRun.parsed.score)/$($scorecardRun.parsed.maxScore); verdict=$($scorecardRun.parsed.verdict)" },
    [ordered]@{ name = 'benchmarkCleanDryRunProof'; pass = ($dryRun -and [string]$dryRun.result -eq 'SIMULATED_SUCCESS' -and $dryRun.proofBundle.fileCount -ge 3); actual = $(if ($dryRun) { "result=$($dryRun.result); files=$($dryRun.proofBundle.fileCount)" } else { 'missing' }) },
    [ordered]@{ name = 'capabilityRepairPacketCoverage'; pass = ($repairRun.parsed -and [string]$repairRun.parsed.result -eq 'PASS' -and [int]$repairRun.parsed.packetCount -eq [int]$repairRun.parsed.failingFamilyCount); actual = "packets=$($repairRun.parsed.packetCount); failingFamilies=$($repairRun.parsed.failingFamilyCount)" },
    [ordered]@{ name = 'selfImprovementContractActive'; pass = ($contract -and [string]$contract.contractStatus -eq 'ACTIVE' -and $contract.hardStops.benchmarkLaunchWithoutApproval -eq $false -and $contract.hardStops.providerCallWithoutApproval -eq $false); actual = $(if ($contract) { "status=$($contract.contractStatus)" } else { 'missing' }) },
    [ordered]@{ name = 'longRuntimeReadyOnGreenReliability'; pass = ($autonomyRun.parsed -and $autonomyRun.parsed.runForeverReady -eq $true -and $autonomyRun.parsed.runForeverReadiness.continuousRunEligibility -eq $true -and [string]$autonomyRun.parsed.runForeverReadiness.reliabilityBudgetStatus -eq 'WITHIN_BUDGET'); actual = "runForeverReady=$($autonomyRun.parsed.runForeverReady); eligibility=$($autonomyRun.parsed.runForeverReadiness.continuousRunEligibility); reliability=$($autonomyRun.parsed.runForeverReadiness.reliabilityBudgetStatus)" }
)

$failed = @($gates | Where-Object { $_.pass -ne $true })
$report = [ordered]@{
    schema = 'obtuse.blunder.s_plus_readiness.v1'
    timestamp = (Get-Date -Format 'o')
    mode = $Mode
    score = ($gates.Count - $failed.Count)
    maxScore = $gates.Count
    verdict = $(if ($failed.Count -eq 0) { 'S_PLUS_READY_NO_LIVE_LAUNCH' } else { 'S_PLUS_NEEDS_REPAIR' })
    gates = $gates
    failedGates = @($failed | ForEach-Object { $_.name })
    evidence = [ordered]@{
        scorecardJsonPath = $scorecardRun.parsed.jsonPath
        dryRunPath = $(if ($dryRun) { $dryRun.path } else { $null })
        repairSummaryPath = Resolve-RepoPath -Path 'artifacts/obtuse/blunder-capability-repair-packets-v1/repair_packets_summary.json'
        selfImprovementContractPath = Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'self_improvement_loop_contract.json')
    }
    policy = [ordered]@{
        liveBenchmarkLaunched = $false
        providerCalled = $false
        queueAdmissionPerformed = $false
        longRuntimeAuthorized = $false
    }
}

Write-JsonFile -Value $report -Path (Join-Path $ArtifactRoot 's_plus_readiness.latest.json')
$report | ConvertTo-Json -Depth 60
