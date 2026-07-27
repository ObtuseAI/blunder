[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory)]
    [string]$ModulePath,

    [Parameter(Mandatory)]
    [string]$ReleaseManifestPath,

    [Parameter(Mandatory)]
    [string]$DeploymentReceiptPath,

    [Parameter(Mandatory)]
    [string]$ExpectedReleasesRoot,

    [Parameter(Mandatory)]
    [string]$ExpectedCoreConfigPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

try {
    Import-Module -Name $ModulePath -Force -ErrorAction Stop
    $result = Invoke-DumbMoneyDesktopLaunch `
        -ReleaseManifestPath $ReleaseManifestPath `
        -DeploymentReceiptPath $DeploymentReceiptPath `
        -ExpectedReleasesRoot $ExpectedReleasesRoot `
        -ExpectedCoreConfigPath $ExpectedCoreConfigPath
    $result | ConvertTo-Json -Depth 10 -Compress
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 2
}
