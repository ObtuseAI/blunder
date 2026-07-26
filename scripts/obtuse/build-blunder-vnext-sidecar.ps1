param(
    [Parameter(Mandatory = $true)]
    [string]$Root,
    [Parameter(Mandatory = $true)]
    [string]$Python
)

$ErrorActionPreference = 'Stop'
$resolvedRoot = (Resolve-Path -LiteralPath $Root).Path
$pythonPath = (Resolve-Path -LiteralPath $Python).Path
$entrypoint = Join-Path $resolvedRoot 'blunder\api\entrypoint.py'
$workRoot = Join-Path $resolvedRoot 'desktop\sidecar-build'
$binaryRoot = Join-Path $resolvedRoot 'desktop\src-tauri\binaries'
$unqualifiedBinary = Join-Path $workRoot 'dist\blunder-core.exe'
$tauriBinary = Join-Path $binaryRoot 'blunder-core-x86_64-pc-windows-msvc.exe'

if (-not (Test-Path -LiteralPath $entrypoint)) {
    throw "Blunder core entrypoint is missing: $entrypoint"
}
New-Item -ItemType Directory -Force -Path $workRoot, $binaryRoot | Out-Null

& $pythonPath -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --name blunder-core `
    --distpath (Join-Path $workRoot 'dist') `
    --workpath (Join-Path $workRoot 'work') `
    --specpath $workRoot `
    $entrypoint
if ($LASTEXITCODE -ne 0) {
    throw 'PyInstaller failed to build the Blunder core sidecar.'
}
if (-not (Test-Path -LiteralPath $unqualifiedBinary)) {
    throw "PyInstaller did not emit the expected sidecar: $unqualifiedBinary"
}
Copy-Item -LiteralPath $unqualifiedBinary -Destination $tauriBinary -Force
$digest = (Get-FileHash -Algorithm SHA256 -LiteralPath $tauriBinary).Hash

[ordered]@{
    schema = 'obtuse.blunder.sidecar-build.v1'
    status = 'BUILT'
    executable = $tauriBinary
    sha256 = $digest
    bytes = (Get-Item -LiteralPath $tauriBinary).Length
} | ConvertTo-Json -Depth 3
