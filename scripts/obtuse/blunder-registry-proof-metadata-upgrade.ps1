#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Preview','ApplyMetadataOnly','Validate')]
    [string]$Mode = 'Preview',
    [string]$ArtifactRoot = 'artifacts/obtuse/targeted-reward0-family-delta-gates-v1'
)
$ErrorActionPreference = 'Stop'
$repo = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')
$artifact = if ([System.IO.Path]::IsPathRooted($ArtifactRoot)) { $ArtifactRoot } else { Join-Path $repo $ArtifactRoot }
if ($Mode -eq 'ApplyMetadataOnly') {
    throw 'ApplyMetadataOnly is intentionally disabled for this no-launch bundle; use Preview or Validate.'
}
python (Join-Path $PSScriptRoot 'blunder_targeted_reward0.py') registry --mode $Mode --artifact-root $artifact
