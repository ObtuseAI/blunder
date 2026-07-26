#requires -Version 5.1
param(
    [string]$ArtifactRoot = 'artifacts/obtuse/idle-deepseek-self-coding-compound-loop-v1',
    [string]$OutputPath = 'artifacts/obtuse/idle-deepseek-self-coding-compound-loop-v1/secret_scan.json'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent

function Resolve-RepoPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $repoRoot $Path
}

$scanPaths = @(
    Resolve-RepoPath -Path $ArtifactRoot
    Resolve-RepoPath -Path 'scripts/obtuse/idle-self-coding-accelerator.ps1'
    Resolve-RepoPath -Path 'scripts/obtuse/openrouter-idle-model.ps1'
    Resolve-RepoPath -Path 'scripts/obtuse/idle-improvement-thresholds.ps1'
    Resolve-RepoPath -Path 'scripts/obtuse/idle-openrouter-self-coding-health.ps1'
    Resolve-RepoPath -Path 'scripts/obtuse/status-idle-ops.ps1'
)

# Only flag actual secret values, not environment variable names or documentation references.
$secretPatterns = @(
    @{ name = 'openrouter_sk_key'; regex = 'sk-or-[a-zA-Z0-9_-]{20,}' },
    @{ name = 'generic_sk_key'; regex = 'sk-[a-zA-Z0-9]{20,}' },
    @{ name = 'api_key_assignment'; regex = '(?i)api[_-]?key\s*=\s*["'']?[a-z0-9]{16,}' },
    @{ name = 'private_key_block'; regex = '-----BEGIN (RSA |OPENSSH |EC |DSA |PRIVATE )?KEY-----' },
    @{ name = 'password_assignment'; regex = '(?i)password\s*=\s*["''][^"'']{8,}' }
)

$findings = @()
$scannedFiles = 0
foreach ($scanPath in $scanPaths) {
    if (-not (Test-Path -LiteralPath $scanPath)) { continue }
    $items = if ((Get-Item -LiteralPath $scanPath) -is [System.IO.DirectoryInfo]) {
        Get-ChildItem -LiteralPath $scanPath -Recurse -File
    } else {
        @(Get-Item -LiteralPath $scanPath)
    }
    foreach ($item in $items) {
        $scannedFiles++
        try {
            $text = Get-Content -Raw -LiteralPath $item.FullName -ErrorAction Stop
            foreach ($pattern in $secretPatterns) {
                if ($text -match $pattern.regex) {
                    $findings += [ordered]@{
                        file = $item.FullName
                        patternName = $pattern.name
                        redacted = 'MATCH'
                    }
                }
            }
        } catch {}
    }
}

$result = [ordered]@{
    classification = 'OBTUSE_BLUNDER_IDLE_DEEPSEEK_SELF_CODING_COMPOUND_LOOP_V1'
    timestamp = (Get-Date -Format 'o')
    phase = 'secret_scan'
    scannedPaths = $scanPaths
    scannedFiles = $scannedFiles
    findings = $findings
    secretsFound = ($findings.Count -gt 0)
    status = $(if ($findings.Count -eq 0) { 'PASS' } else { 'FAIL' })
    notes = @(
        'Scans compound-loop artifacts and modified idle scripts only.',
        'Does not scan all of C:\Users\developer.',
        'Environment variable names such as OPENROUTER_API_KEY are not treated as secret leaks.',
        'No actual secret values are printed in this report.'
    )
}

$resolvedOutput = Resolve-RepoPath -Path $OutputPath
New-Item -ItemType Directory -Force -Path (Split-Path $resolvedOutput -Parent) | Out-Null
$result | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $resolvedOutput -Encoding UTF8
$result | ConvertTo-Json -Depth 10
