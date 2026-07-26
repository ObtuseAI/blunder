#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Validate','Check','RecoverIfSafe','ReportOnly')]
    [string]$Mode = 'Check',
    [string]$ArtifactRoot = 'artifacts/obtuse/blunder-flash-full-autonomy-compounding-bundle-v1',
    [string]$QueuePath = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle/idle-task-queue.json',
    [string]$IdleArtifactRoot = 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle',
    [string]$OperatorApproved = 'false',
    [int]$MaxHeartbeatAgeSeconds = 60
)

$ErrorActionPreference = 'Stop'
$delegate = Join-Path $PSScriptRoot 'blunder-idle-watchdog.ps1'
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $delegate `
    -Mode $Mode `
    -ArtifactRoot $ArtifactRoot `
    -QueuePath $QueuePath `
    -IdleArtifactRoot $IdleArtifactRoot `
    -OperatorApproved $OperatorApproved `
    -MaxHeartbeatAgeSeconds $MaxHeartbeatAgeSeconds
exit $LASTEXITCODE
