#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Validate','ReportOnly','Apply','StopIfUnsafe')]
    [string]$Mode = 'Validate'
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'blunder-full-autonomy-common.ps1')

if ($Mode -eq 'StopIfUnsafe') {
    $record = Write-BlunderAutonomyPolicyRecord -Mode $Mode
    if ($record.result -ne 'PASS') { throw 'Autonomy policy hard-stop check failed.' }
    $record | ConvertTo-Json -Depth 100
    return
}

$record = Write-BlunderAutonomyPolicyRecord -Mode $Mode
Write-BlunderBudgetGateRecord | Out-Null
$record | ConvertTo-Json -Depth 100
