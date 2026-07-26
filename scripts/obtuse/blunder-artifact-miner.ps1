#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Validate','MineOnce','MineLoop','ReportOnly')]
    [string]$Mode = 'Validate',
    [int]$LoopDelaySeconds = 30,
    [int]$MaxIterations = 1
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'blunder-full-autonomy-common.ps1')

$lastRecord = $null
if ($Mode -eq 'MineLoop') {
    for ($i = 0; $i -lt [math]::Max(1, $MaxIterations); $i++) {
        $lastRecord = Write-BlunderArtifactMiningRecord -Mode $Mode
        if ($i -lt ($MaxIterations - 1)) { Start-Sleep -Seconds $LoopDelaySeconds }
    }
}
else {
    $lastRecord = Write-BlunderArtifactMiningRecord -Mode $Mode
}

$lastRecord | ConvertTo-Json -Depth 100
