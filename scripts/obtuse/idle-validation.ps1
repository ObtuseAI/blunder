#requires -Version 5.1
<#
.SYNOPSIS
    Safety and correctness validators for idle-mode tasks.

.DESCRIPTION
    Non-destructive checks: PowerShell syntax, JSON parseability, secret-pattern
    detection (redacted output), path existence. No provider calls.
#>
[CmdletBinding()]
param(
    [ValidateSet('Syntax','Json','Secrets','Path','Composite')]
    [string]$Action = 'Composite',
    [string]$TargetPath = 'C:\src\engine\obtuse',
    [string]$OutputPath = ''
)

$ErrorActionPreference = 'Stop'

function Test-PowerShellSyntax {
    param([string]$Path)
    $maxFiles = [int]::MaxValue
    $maxBytes = 1MB
    $excludedSegments = @('\.git\', '\node_modules\', '\.venv\', '\.venv-blunder-shell\', '\site-packages\', '\__pycache__\', '\.next\', '\dist\', '\build\', '\coverage\', '\quarantined-generated-self-coding-helpers-')
    if ([System.IO.Path]::GetFileName(([System.IO.Path]::GetFullPath($Path)).TrimEnd('\')) -ine 'artifacts') {
        $excludedSegments += '\artifacts\'
    }
    $allFiles = @(Get-ChildItem -Path $Path -Filter '*.ps1' -File -Recurse -ErrorAction SilentlyContinue | Sort-Object FullName)
    $files = @()
    $skipped = @()
    foreach ($f in $allFiles) {
        $normalizedPath = $f.FullName.Replace('/', '\')
        $excluded = $false
        foreach ($segment in $excludedSegments) {
            if ($normalizedPath -like "*$segment*") {
                $excluded = $true
                break
            }
        }
        if ($excluded) {
            if ($skipped.Count -lt 25) {
                $skipped += @{ file = $f.FullName; reason = 'excluded_path_segment'; bytes = $f.Length }
            }
            continue
        }
        if ($f.Length -gt $maxBytes) {
            if ($skipped.Count -lt 25) {
                $skipped += @{ file = $f.FullName; reason = 'file_too_large_for_idle_syntax_health'; bytes = $f.Length }
            }
            continue
        }
        if ($files.Count -ge $maxFiles) {
            if ($skipped.Count -lt 25) {
                $skipped += @{ file = $f.FullName; reason = 'scan_file_limit_reached'; bytes = $f.Length }
            }
            continue
        }
        $files += $f
    }
    $findings = @()
    foreach ($f in $files) {
        $errors = $null
        $null = [System.Management.Automation.PSParser]::Tokenize((Get-Content -Raw -Path $f.FullName), [ref]$errors)
        if ($errors) {
            $findings += @{ file = $f.FullName; errors = ($errors | Select-Object -First 5 | ForEach-Object { $_.Message }) }
        }
    }
    return @{
        pass = ($findings.Count -eq 0)
        bounded = $true
        filesDiscovered = $allFiles.Count
        filesChecked = $files.Count
        filesSkipped = [Math]::Max(0, ($allFiles.Count - $files.Count))
        maxFiles = $maxFiles
        maxBytes = $maxBytes
        skippedSample = $skipped
        findings = $findings
    }
}

function Test-JsonFiles {
    param([string]$Path)

    $maxFiles = [int]::MaxValue
    $maxBytes = 2MB
    $excludedSegments = @('\.git\', '\node_modules\', '\.venv\', '\.venv-blunder-shell\', '\site-packages\', '\__pycache__\', '\.next\', '\dist\', '\build\', '\coverage\', '\quarantined-generated-self-coding-helpers-')
    if ([System.IO.Path]::GetFileName(([System.IO.Path]::GetFullPath($Path)).TrimEnd('\')) -ine 'artifacts') {
        $excludedSegments += '\artifacts\'
    }
    $allFiles = @(Get-ChildItem -Path $Path -Filter '*.json' -File -Recurse -ErrorAction SilentlyContinue | Sort-Object FullName)
    $files = @()
    $skipped = @()
    foreach ($f in $allFiles) {
        $normalizedPath = $f.FullName.Replace('/', '\')
        $excluded = $false
        foreach ($segment in $excludedSegments) {
            if ($normalizedPath -like "*$segment*") {
                $excluded = $true
                break
            }
        }
        if ($excluded -or $f.Name -eq 'package-lock.json') {
            if ($skipped.Count -lt 25) {
                $skipped += @{ file = $f.FullName; reason = 'excluded_path_segment'; bytes = $f.Length }
            }
            continue
        }
        if ($f.Length -gt $maxBytes) {
            if ($skipped.Count -lt 25) {
                $skipped += @{ file = $f.FullName; reason = 'file_too_large_for_idle_json_health'; bytes = $f.Length }
            }
            continue
        }
        if ($files.Count -ge $maxFiles) {
            if ($skipped.Count -lt 25) {
                $skipped += @{ file = $f.FullName; reason = 'scan_file_limit_reached'; bytes = $f.Length }
            }
            continue
        }
        $files += $f
    }

    $findings = @()
    foreach ($f in $files) {
        try {
            $null = Get-Content -Raw -Path $f.FullName | ConvertFrom-Json
        } catch {
            $findings += @{ file = $f.FullName; error = $_.Exception.Message }
        }
    }
    return @{
        pass = ($findings.Count -eq 0)
        bounded = $true
        filesDiscovered = $allFiles.Count
        filesChecked = $files.Count
        filesSkipped = [Math]::Max(0, ($allFiles.Count - $files.Count))
        maxFiles = $maxFiles
        maxBytes = $maxBytes
        skippedSample = $skipped
        findings = $findings
    }
}

function Test-SecretPatterns {
    param([string]$Path)
    $maxFiles = [int]::MaxValue
    $maxBytes = 2MB
    $excludedSegments = @('\.git\', '\node_modules\', '\.venv\', '\.venv-blunder-shell\', '\site-packages\', '\__pycache__\', '\.next\', '\dist\', '\build\', '\coverage\', '\quarantined-generated-self-coding-helpers-')
    if ([System.IO.Path]::GetFileName(([System.IO.Path]::GetFullPath($Path)).TrimEnd('\')) -ine 'artifacts') {
        $excludedSegments += '\artifacts\'
    }
    $allFiles = @(Get-ChildItem -Path $Path -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -in @('.json','.ps1','.txt','.yaml','.yml','.env') -or $_.Name -like '*command*' } |
        Sort-Object FullName)
    $files = @()
    $skipped = @()
    foreach ($f in $allFiles) {
        $normalizedPath = $f.FullName.Replace('/', '\')
        $excluded = $false
        foreach ($segment in $excludedSegments) {
            if ($normalizedPath -like "*$segment*") {
                $excluded = $true
                break
            }
        }
        if ($excluded) {
            if ($skipped.Count -lt 25) {
                $skipped += @{ file = $f.FullName; reason = 'excluded_path_segment'; bytes = $f.Length }
            }
            continue
        }
        if ($f.Length -gt $maxBytes) {
            if ($skipped.Count -lt 25) {
                $skipped += @{ file = $f.FullName; reason = 'file_too_large_for_idle_secret_scan'; bytes = $f.Length }
            }
            continue
        }
        if ($files.Count -ge $maxFiles) {
            if ($skipped.Count -lt 25) {
                $skipped += @{ file = $f.FullName; reason = 'scan_file_limit_reached'; bytes = $f.Length }
            }
            continue
        }
        $files += $f
    }
    $patterns = @(
        'sk-or-[A-Za-z0-9_-]{20,}',
        'sk-proj-[A-Za-z0-9_-]{20,}',
        'sk-[A-Za-z0-9_-]{20,}'
    )
    $findings = @()
    $readFailures = @()
    foreach ($f in $files) {
        try {
            $raw = Get-Content -Raw -Path $f.FullName -ErrorAction Stop
            foreach ($pat in $patterns) {
                $matches = [regex]::Matches($raw, $pat)
                if ($matches.Count -gt 0) {
                    $findings += @{ file = $f.FullName; patternClass = $pat; count = $matches.Count }
                    break
                }
            }
        } catch {
            if ($readFailures.Count -lt 25) {
                $readFailures += @{ file = $f.FullName; error = $_.Exception.Message }
            }
        }
    }
    return @{
        pass = ($findings.Count -eq 0)
        bounded = $true
        filesDiscovered = $allFiles.Count
        filesChecked = $files.Count
        filesSkipped = [Math]::Max(0, ($allFiles.Count - $files.Count))
        maxFiles = $maxFiles
        maxBytes = $maxBytes
        skippedSample = $skipped
        readFailureSample = $readFailures
        findings = $findings
        note = 'Secret-pattern scan completed. Values are not logged.'
    }
}

function Test-PathExists {
    param([string]$Path)
    $exists = Test-Path $Path
    return @{ pass = $exists; path = $Path; exists = $exists }
}

$result = @{ timestamp = (Get-Date -Format 'o'); action = $Action; pass = $true; details = @{} }

switch ($Action) {
    'Syntax' { $result.details.syntax = Test-PowerShellSyntax -Path $TargetPath }
    'Json'   { $result.details.json = Test-JsonFiles -Path $TargetPath }
    'Secrets'{ $result.details.secrets = Test-SecretPatterns -Path $TargetPath }
    'Path'   { $result.details.path = Test-PathExists -Path $TargetPath }
    'Composite' {
        $result.details.syntax = Test-PowerShellSyntax -Path $TargetPath
        $result.details.json = Test-JsonFiles -Path $TargetPath
        $result.details.secrets = Test-SecretPatterns -Path $TargetPath
    }
}

# Composite pass if all sub-checks pass (secrets scan is always informational)
$result.pass = ($result.details.Values | ForEach-Object { $_.pass } | Where-Object { -not $_ }).Count -eq 0

if ($OutputPath) {
    $dir = Split-Path $OutputPath -Parent
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $result | ConvertTo-Json -Depth 10 | Set-Content -Path $OutputPath -Force
}

$result | ConvertTo-Json -Depth 10
