#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string]$Path,
    [string]$OutputDir = '',
    [string[]]$RequiredFiles = @(),
    [switch]$AsJson
)

$ErrorActionPreference = 'Stop'

function Resolve-PathSafe {
    param([string]$P)
    if ([System.IO.Path]::IsPathRooted($P)) { return (Resolve-Path -LiteralPath $P -ErrorAction Stop).Path }
    $joined = Join-Path (Get-Location).Path $P
    return (Resolve-Path -LiteralPath $joined -ErrorAction Stop).Path
}

$artifactRoot = Resolve-PathSafe -P $Path
if (-not $OutputDir) { $OutputDir = $artifactRoot }
$outDir = Resolve-PathSafe -P $OutputDir
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$defaultRequiredFiles = @(
    'validation_summary.json'
    'FINAL_REPORT.json'
    'operator_handoff.md'
    'UPDATED_TODO.md'
)
$requiredFiles = if ($RequiredFiles -and $RequiredFiles.Count -gt 0) { $RequiredFiles } else { $defaultRequiredFiles }

function Get-FileCategory {
    param([string]$Name, [string]$Extension)
    switch ($Extension.ToLowerInvariant()) {
        '.json' { return 'json' }
        '.jsonl' { return 'jsonl' }
        '.md' { return 'markdown' }
        '.ps1' { return 'powershell' }
        '.zip' { return 'proof_bundle' }
        '.log' { return 'log' }
        default { return 'other' }
    }
}

$manifest = [System.Collections.ArrayList]@()
$files = Get-ChildItem -LiteralPath $artifactRoot -Recurse -File -ErrorAction SilentlyContinue
$totalSize = 0
foreach ($file in $files) {
    $relative = $file.FullName.Substring($artifactRoot.Length).TrimStart('\','/') -replace '\\','/'
    $sha = (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash.ToUpperInvariant()
    $category = Get-FileCategory -Name $file.Name -Extension $file.Extension
    $required = ($file.Name -in $requiredFiles)
    $entry = [ordered]@{
        relative_path = $relative
        file_name = $file.Name
        extension = $file.Extension.TrimStart('.')
        size_bytes = $file.Length
        last_write_time_utc = $file.LastWriteTimeUtc.ToString('o')
        sha256 = $sha
        category = $category
        required_for_report = $required
        missing = $false
        notes = ''
    }
    [void]$manifest.Add($entry)
    $totalSize += $file.Length
}

$missingRequired = @()
foreach ($req in $requiredFiles) {
    $found = @($files | Where-Object { $_.Name -eq $req }).Count -gt 0
    if (-not $found) {
        $missingRequired += $req
        [void]$manifest.Add([ordered]@{
            relative_path = $req
            file_name = $req
            extension = ([System.IO.Path]::GetExtension($req)).TrimStart('.')
            size_bytes = 0
            last_write_time_utc = $null
            sha256 = $null
            category = 'missing'
            required_for_report = $true
            missing = $true
            notes = 'Required file not found in artifact directory'
        })
    }
}

$manifestJsonPath = Join-Path $outDir 'proof_manifest.json'
$manifestJsonlPath = Join-Path $outDir 'proof_manifest.jsonl'
$manifestSummaryPath = Join-Path $outDir 'proof_manifest_summary.json'

$manifestArray = @($manifest | Sort-Object relative_path)
$manifestArray | ConvertTo-Json -Depth 10 | Set-Content -Path $manifestJsonPath -Force
$manifestLines = @($manifestArray | ForEach-Object { $_ | ConvertTo-Json -Depth 10 -Compress })
[System.IO.File]::WriteAllLines($manifestJsonlPath, $manifestLines, [System.Text.UTF8Encoding]::new($false))

$manifestContent = [System.IO.File]::ReadAllBytes($manifestJsonPath)
$manifestSha = ([System.BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash($manifestContent))).Replace('-','').ToUpperInvariant()

$summary = [ordered]@{
    classification = 'OBTUSE_BLUNDER_IDLE_PROOF_MANIFEST_V1'
    timestamp = (Get-Date -Format 'o')
    artifact_root = $artifactRoot
    generated_at_utc = (Get-Date -Format 'u')
    file_count = $files.Count
    total_size_bytes = $totalSize
    required_file_count = $requiredFiles.Count
    missing_required_count = $missingRequired.Count
    missing_required_files = $missingRequired
    final_report_present = ($files | Where-Object { $_.Name -eq 'FINAL_REPORT.json' }).Count -gt 0
    operator_handoff_present = ($files | Where-Object { $_.Name -eq 'operator_handoff.md' }).Count -gt 0
    updated_todo_present = ($files | Where-Object { $_.Name -eq 'UPDATED_TODO.md' }).Count -gt 0
    validation_summary_present = ($files | Where-Object { $_.Name -eq 'validation_summary.json' }).Count -gt 0
    proof_bundle_present = ($files | Where-Object { $_.Extension -eq '.zip' }).Count -gt 0
    manifest_sha256 = $manifestSha
    result = $(if ($missingRequired.Count -eq 0) { 'PASS' } else { 'FAIL' })
}

$summary | ConvertTo-Json -Depth 10 | Set-Content -Path $manifestSummaryPath -Force
$summary | ConvertTo-Json -Depth 10
