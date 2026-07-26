# Bounded secret scan for the provider stall fix proof bundle.
# Does not scan all of C:\Users\developer.
# Does not print actual secret values.
[CmdletBinding()]
param(
    [string]$OutputPath = "artifacts/obtuse/blunder-provider-stall-fix-continuation-root-containment-v1/009_SECRET_SCAN.json"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Push-Location $repoRoot

New-Item -ItemType Directory -Path (Split-Path $OutputPath -Parent) -Force | Out-Null

$scanPaths = @(
    "scripts/obtuse",
    "artifacts/obtuse/blunder-terminal-bench-provider-stall-autopsy-relaunch-fix-v1",
    "artifacts/obtuse/blunder-provider-stall-fix-continuation-root-containment-v1"
)

$secretPatterns = @(
    @{ name = "openrouter_sk_key"; regex = "sk-or-[a-zA-Z0-9_-]{20,}" },
    @{ name = "generic_sk_key"; regex = "sk-[a-zA-Z0-9]{20,}" },
    @{ name = "api_key_assignment"; regex = '(?i)api[_-]?key\s*=\s*["''][a-z0-9]{16,}' },
    @{ name = "openrouter_api_key_label"; regex = "(?i)openrouter[_-]?api[_-]?key" },
    @{ name = "private_key_block"; regex = "-----BEGIN (RSA |OPENSSH |EC |DSA |PRIVATE )?KEY-----" },
    @{ name = "password_assignment"; regex = '(?i)password\s*=\s*["''][^"'']{8,}' }
)

$findings = @()
$scannedFiles = 0

foreach ($sp in $scanPaths) {
    if (-not (Test-Path $sp)) { continue }
    foreach ($f in (Get-ChildItem $sp -File -Recurse -ErrorAction SilentlyContinue)) {
        if ($f.Extension -in @(".zip",".png",".jpg",".jpeg",".gif")) { continue }
        if ($f.Name -match "\.template\.json$") { continue }
        if ($f.Name -match "secret-scan|validate-|test-|009_SECRET_SCAN\.json$") { continue }
        $scannedFiles++
        $content = Get-Content $f.FullName -Raw -ErrorAction SilentlyContinue
        foreach ($p in $secretPatterns) {
            if ($content -match $p.regex) {
                $findings += @{
                    file = $f.FullName
                    patternName = $p.name
                    redacted = "MATCH"
                }
            }
        }
    }
}

$result = @{
    classification = "OBTUSE_BLUNDER_PROVIDER_STALL_FIX_CONTINUATION_AND_ROOT_CONTAINMENT_V1"
    phase = "secret_scan"
    scannedAt = (Get-Date -Format "o")
    scannedPaths = $scanPaths
    scannedFiles = $scannedFiles
    secretsFound = ($findings.Count -gt 0)
    findings = $findings
    status = if ($findings.Count -eq 0) { "PASS" } else { "FAIL" }
    notes = @(
        "Scanned only changed scripts, docs, artifacts, approval templates, and launch specs.",
        "Did not scan all of C:\\Users\\CH.",
        "Template files excluded because they contain placeholder key names only.",
        "No actual secret values are printed in this report."
    )
}

$result | ConvertTo-Json -Depth 10 | Set-Content $OutputPath -Encoding UTF8

Write-Host "SECRET_SCAN_$($result.status)" -ForegroundColor $(if ($result.status -eq "PASS") { "Green" } else { "Red" })
Write-Host "Scanned files: $scannedFiles; Findings: $($findings.Count)"

Pop-Location
