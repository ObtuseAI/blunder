#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$ArtifactRoot = 'artifacts/obtuse/blunder-full-autonomy-closure-loop-v1',
    [string]$ProofBundlesDir = 'artifacts/obtuse/proof-bundles',
    [string]$FrankensteinRoot = '\\fileserver\shared\obtuse\blunder-full-autonomy-closure-loop-v1'
)
$ErrorActionPreference = 'Stop'

function Resolve-RepoPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return (Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path }
    $joined = Join-Path (Get-Location).Path $Path
    return (Resolve-Path -LiteralPath $joined -ErrorAction Stop).Path
}

$artifactRoot = Resolve-RepoPath -Path $ArtifactRoot
$proofDir = Resolve-RepoPath -Path $ProofBundlesDir
New-Item -ItemType Directory -Force -Path $proofDir | Out-Null
$stamp = Get-Date -Format 'yyyyMMddTHHmmss'
$zipName = "obtuse-blunder-full-autonomy-closure-loop-v1-$stamp.zip"
$zipPath = Join-Path $proofDir $zipName

$files = @(Get-ChildItem -LiteralPath $artifactRoot -Recurse -File | ForEach-Object { $_.FullName })
if ($files.Count -eq 0) { throw "No files found in artifact root" }
Compress-Archive -LiteralPath $files -DestinationPath $zipPath -Force
$sha = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath).Hash.ToUpperInvariant()

$result = [ordered]@{
    classification = 'OBTUSE_BLUNDER_FULL_AUTONOMY_CLOSURE_LOOP_V1'
    timestamp = (Get-Date -Format 'o')
    zipPath = $zipPath
    zipName = $zipName
    sha256 = $sha
    copied = $false
    frankensteinPath = $null
    frankensteinSha256 = $null
    error = $null
}

if (-not (Test-Path -LiteralPath '\\fileserver\shared')) {
    $result.error = 'FRANKENSTEIN share root not reachable.'
    $result | ConvertTo-Json -Depth 10
    return
}

try {
    New-Item -ItemType Directory -Force -Path $FrankensteinRoot | Out-Null
    $targetZip = Join-Path $FrankensteinRoot $zipName
    Copy-Item -LiteralPath $zipPath -Destination $targetZip -Force
    $artifactTarget = Join-Path $FrankensteinRoot (Split-Path $artifactRoot -Leaf)
    if (Test-Path -LiteralPath $artifactTarget) { Remove-Item -LiteralPath $artifactTarget -Recurse -Force }
    Copy-Item -LiteralPath $artifactRoot -Destination $artifactTarget -Recurse -Force
    $targetSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $targetZip).Hash.ToUpperInvariant()
    $result.copied = ($sha -eq $targetSha)
    $result.frankensteinPath = $targetZip
    $result.frankensteinSha256 = $targetSha
    if (-not $result.copied) { $result.error = 'SHA-256 mismatch after copy.' }
} catch {
    $result.error = $_.ToString()
}

$result | ConvertTo-Json -Depth 10
