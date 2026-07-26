#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$ArtifactRoot = 'artifacts/obtuse/blunder-full-autonomy-closure-loop-v1',
    [string]$OutputPath = 'artifacts/obtuse/blunder-full-autonomy-closure-loop-v1/secret_scan_result.json'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent

function Resolve-RepoPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $repoRoot $Path
}

$out = Resolve-RepoPath -Path $OutputPath
New-Item -ItemType Directory -Force -Path (Split-Path $out -Parent) | Out-Null

$scanPaths = @(
    'scripts/obtuse',
    $ArtifactRoot
)

$secretPatterns = @(
    @{ name = 'openrouter_sk_key_value'; regex = 'sk-or-[a-zA-Z0-9_-]{20,}' }
    @{ name = 'generic_sk_key_value'; regex = 'sk-(?!or\b)[a-zA-Z0-9]{20,}' }
    @{ name = 'api_key_value_assignment'; regex = '(?i)(api[_-]?key|password|secret)\s*=\s*["''][^"'']{16,}["'']' }
    @{ name = 'private_key_block'; regex = '-----BEGIN (RSA |OPENSSH |EC |DSA |PRIVATE )?KEY-----' }
)

$falsePositivePatterns = @(
    '(?i)OPENROUTER_API_KEY\b',
    '(?i)OPENAI_API_KEY\b',
    '(?i)ANTHROPIC_API_KEY\b',
    '(?i)\$env:OPENROUTER_API_KEY',
    '(?i)Get-EnvironmentVariable.*OPENROUTER_API_KEY'
)

$findings = @()
$scannedFiles = 0

foreach ($sp in $scanPaths) {
    $resolvedSp = Resolve-RepoPath -Path $sp
    if (-not (Test-Path -LiteralPath $resolvedSp)) { continue }
    foreach ($f in (Get-ChildItem -LiteralPath $resolvedSp -File -Recurse -ErrorAction SilentlyContinue)) {
        if ($f.Extension -in @('.zip','.png','.jpg','.jpeg','.gif','.mp4','.pdf')) { continue }
        if ($f.Name -match 'secret[-_]?scan|009_SECRET_SCAN|secret_scan_result\.json') { continue }
        $scannedFiles++
        $content = Get-Content -LiteralPath $f.FullName -Raw -ErrorAction SilentlyContinue
        if ([string]::IsNullOrWhiteSpace($content)) { continue }
        foreach ($p in $secretPatterns) {
            $matches = [regex]::Matches($content, $p.regex)
            foreach ($m in $matches) {
                $value = $m.Value
                $isFalsePositive = $false
                foreach ($fp in $falsePositivePatterns) {
                    if ($value -match $fp) { $isFalsePositive = $true; break }
                }
                if (-not $isFalsePositive) {
                    $findings += [ordered]@{
                        file = $f.FullName
                        patternName = $p.name
                        redacted = 'MATCH'
                        matchedLength = $value.Length
                    }
                }
            }
        }
    }
}

$result = [ordered]@{
    classification = 'OBTUSE_BLUNDER_FULL_AUTONOMY_SECRET_SCAN_V1'
    scannedAt = (Get-Date -Format 'o')
    scannedPaths = $scanPaths
    scannedFiles = $scannedFiles
    findings = $findings
    secretsFound = ($findings.Count -gt 0)
    status = $(if ($findings.Count -eq 0) { 'PASS' } else { 'FAIL' })
    notes = @(
        'Scans only Obtuse scripts and the current autonomy artifact directory.',
        'OPENROUTER_API_KEY variable names are treated as false positives.',
        'No actual secret values are printed.'
    )
}

$result | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $out -Encoding UTF8
$result | ConvertTo-Json -Depth 10
