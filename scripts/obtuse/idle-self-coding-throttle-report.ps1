#requires -Version 5.1
param(
    [ValidateSet('Validate','Report')]
    [string]$Mode = 'Validate',
    [string]$ArtifactRoot = 'artifacts/obtuse/blunder-flash-full-autonomy-compounding-bundle-v1/self-coding',
    [int]$CooldownSeconds = 300
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

$run = Read-JsonFile -Path (Join-Path $ArtifactRoot 'self_coding_run_summary.json')
$calls = @(Read-JsonLines -Path (Join-Path $ArtifactRoot 'openrouter_call_ledger.jsonl'))
$ageSeconds = $null
if ($run -and $run.timestamp) {
    try { $ageSeconds = [math]::Round(((Get-Date) - [datetime]$run.timestamp).TotalSeconds, 3) } catch {}
}
$remaining = if ($ageSeconds -ne $null) { [math]::Max(0, [math]::Round($CooldownSeconds - $ageSeconds, 3)) } else { $null }
$failed = @($calls | Where-Object { $_.callAttempted -eq $true -and $_.success -ne $true -and $_.callSucceeded -ne $true -and $_.result -notin @('PASS','OK','SUCCESS') }).Count
$result = [ordered]@{
    classification = 'OBTUSE_BLUNDER_SELF_CODING_THROTTLE_REPORT_V1'
    timestamp = (Get-Date -Format 'o')
    mode = $Mode
    artifactRoot = Resolve-RepoPath -Path $ArtifactRoot
    lastRunAt = $(if ($run) { $run.timestamp } else { $null })
    lastRunAgeSeconds = $ageSeconds
    cooldownSeconds = $CooldownSeconds
    cooldownRemainingSeconds = $remaining
    cooldownState = $(if ($remaining -eq $null) { 'UNKNOWN' } elseif ($remaining -gt 0) { 'COOLING_DOWN' } else { 'READY' })
    providerCallsAttempted = @($calls | Where-Object { $_.callAttempted -eq $true }).Count
    providerCallsFailed = $failed
    failureGuardState = $(if ($failed -eq 0) { 'CLEAN' } elseif ($failed -lt 3) { 'WATCH' } else { 'REVIEW' })
    result = 'PASS'
}
$result | ConvertTo-Json -Depth 20
