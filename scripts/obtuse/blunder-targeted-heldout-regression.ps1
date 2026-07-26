#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('StaticOnly','FixtureOnly','NoLaunchFull','TargetedSmokeHeldout','Summarize')]
    [string]$Mode = 'NoLaunchFull',
    [string]$ArtifactRoot = 'artifacts/obtuse/targeted-reward0-family-delta-gates-v1'
)
$ErrorActionPreference = 'Stop'
$repo = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')
$artifact = if ([System.IO.Path]::IsPathRooted($ArtifactRoot)) { $ArtifactRoot } else { Join-Path $repo $ArtifactRoot }
python (Join-Path $PSScriptRoot 'blunder_targeted_reward0.py') heldout --mode $Mode --artifact-root $artifact
