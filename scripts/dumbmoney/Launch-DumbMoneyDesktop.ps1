[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$ReleaseManifestPath,

    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$DeploymentReceiptPath,

    [switch]$DryRun,

    [switch]$Start
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($DryRun -and $Start) {
    throw '-DryRun and -Start are mutually exclusive.'
}
if ($PSVersionTable.PSVersion -lt [Version]'7.4') {
    throw 'DumbMoney desktop launch requires PowerShell 7.4 or newer.'
}

$modulePath = Join-Path -Path $PSScriptRoot -ChildPath 'DumbMoney.DesktopLauncher.psm1'
Import-Module -Name $modulePath -Force -ErrorAction Stop

$programFiles = [System.Environment]::GetFolderPath(
    [System.Environment+SpecialFolder]::ProgramFiles
)
if ([string]::IsNullOrWhiteSpace($programFiles)) {
    throw 'The Windows Program Files directory could not be resolved.'
}
$releasesRoot = Join-Path `
    -Path $programFiles `
    -ChildPath 'DumbMoney\releases'
$coreConfigPath = 'C:\ProgramData\DumbMoney\config\core-runner.v1.json'

try {
    $result = Invoke-DumbMoneyDesktopLaunch `
        -ReleaseManifestPath $ReleaseManifestPath `
        -DeploymentReceiptPath $DeploymentReceiptPath `
        -ExpectedReleasesRoot $releasesRoot `
        -ExpectedCoreConfigPath $coreConfigPath `
        -Start:$Start
    $result | ConvertTo-Json -Depth 10 -Compress
}
catch {
    [Console]::Error.WriteLine(
        "DumbMoney desktop launch failed safely: $($_.Exception.Message)"
    )
    exit 2
}
