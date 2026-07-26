#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Validate','ReplenishOnce','ReplenishLoop','ReportOnly')]
    [string]$Mode = 'Validate',
    [int]$LoopDelaySeconds = 30,
    [int]$MaxIterations = 1
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'blunder-full-autonomy-common.ps1')

$admit = ($Mode -in @('ReplenishOnce','ReplenishLoop'))
$lastRecord = $null
if ($Mode -eq 'ReplenishLoop') {
    for ($i = 0; $i -lt [math]::Max(1, $MaxIterations); $i++) {
        $lastRecord = Write-BlunderQueueReplenishmentRecord -Mode $Mode -AdmitCandidates $admit
        if ($i -lt ($MaxIterations - 1)) { Start-Sleep -Seconds $LoopDelaySeconds }
    }
}
else {
    $lastRecord = Write-BlunderQueueReplenishmentRecord -Mode $Mode -AdmitCandidates $admit
}

$lastRecord | ConvertTo-Json -Depth 100
