param(
    [Parameter(Mandatory = $true)]
    [string]$Root
)

$ErrorActionPreference = 'Stop'
$resolvedRoot = (Resolve-Path -LiteralPath $Root).Path
$desktopRoot = Join-Path $resolvedRoot 'desktop'
$cargo = Join-Path $resolvedRoot '.tooling\cargo\bin\cargo.exe'
$rustupHome = Join-Path $resolvedRoot '.tooling\rustup'
$cargoHome = Join-Path $resolvedRoot '.tooling\cargo'
$toolchain = (Get-Content -Raw -LiteralPath (Join-Path $resolvedRoot 'rust-toolchain.toml')) | Select-String 'channel = "([^"]+)"'

if (-not $toolchain.Matches.Success) {
    throw 'rust-toolchain.toml does not declare a pinned channel.'
}
if (-not (Test-Path -LiteralPath (Join-Path $desktopRoot 'package-lock.json'))) {
    throw 'desktop/package-lock.json is missing. Run npm install from desktop before building.'
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw 'Node.js is missing. Install the current supported Node.js release before building Blunder desktop.'
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw 'npm is missing. Install npm with Node.js before building Blunder desktop.'
}
if (-not (Test-Path -LiteralPath $cargo)) {
    throw 'The project-local Rust toolchain is missing. Bootstrap rustup into .tooling with the pinned rust-toolchain.toml channel.'
}

$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (-not (Test-Path -LiteralPath $vswhere)) {
    throw 'Visual Studio Build Tools discovery is missing. Install the Desktop development with C++ workload.'
}
$visualStudio = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $visualStudio) {
    throw 'Visual Studio Desktop development with C++ is required for the Windows Tauri host.'
}

$webViewRoot = Join-Path ${env:ProgramFiles(x86)} 'Microsoft\EdgeWebView\Application'
$webViewVersion = Get-ChildItem -LiteralPath $webViewRoot -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '^\d+\.\d+\.\d+\.\d+$' } |
    Sort-Object Name -Descending |
    Select-Object -First 1
if (-not $webViewVersion) {
    throw 'Microsoft Edge WebView2 Runtime is missing. Install the Evergreen WebView2 Runtime before launching Blunder desktop.'
}

$env:RUSTUP_HOME = $rustupHome
$env:CARGO_HOME = $cargoHome
$actualToolchain = (& $cargo --version).Trim()
$nodeVersion = (& node --version).Trim()
$npmVersion = (& npm --version).Trim()

[ordered]@{
    schema = 'obtuse.blunder.desktop-prerequisites.v1'
    status = 'READY'
    root = $resolvedRoot
    rustToolchain = $toolchain.Matches[0].Groups[1].Value
    cargo = $actualToolchain
    node = $nodeVersion
    npm = $npmVersion
    visualStudio = $visualStudio
    webView2 = $webViewVersion.Name
} | ConvertTo-Json -Depth 4
