#requires -Version 5.1
<#
.SYNOPSIS
    Installs the user-level `blundercode` command shim.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

function Resolve-RepoRoot {
    return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
}

function Write-JsonFile {
    param(
        [string]$Path,
        [object]$Value
    )

    $Value | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Add-PathEntry {
    param([string]$Directory)

    $parts = @([Environment]::GetEnvironmentVariable('Path', 'User') -split ';' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    $present = $parts | Where-Object { $_.TrimEnd('\') -ieq $Directory.TrimEnd('\') }
    if (-not $present) {
        $parts += $Directory
        [Environment]::SetEnvironmentVariable('Path', ($parts -join ';'), 'User')
    }

    $processParts = @($env:Path -split ';' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    $processPresent = $processParts | Where-Object { $_.TrimEnd('\') -ieq $Directory.TrimEnd('\') }
    if (-not $processPresent) {
        $env:Path = ($processParts + $Directory) -join ';'
    }
}

$repo = Resolve-RepoRoot
$launcher = Join-Path $repo 'scripts\obtuse\blundercode.ps1'
if (-not (Test-Path -LiteralPath $launcher)) {
    throw "Blunder Code launcher not found: $launcher"
}

$shim = Join-Path $env:APPDATA 'npm'
New-Item -ItemType Directory -Path $shim -Force | Out-Null
Add-PathEntry -Directory $shim

$cmd = Join-Path $shim 'blundercode.cmd'
@(
    '@echo off',
    ('powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{0}" %*' -f $launcher)
) | Set-Content -LiteralPath $cmd -Encoding ASCII

$root = Join-Path $repo 'artifacts\obtuse\blundercode-command-install'
New-Item -ItemType Directory -Path $root -Force | Out-Null
$record = [ordered]@{
    status = 'BLUNDERCODE_COMMAND_INSTALLED'
    command = 'blundercode'
    shim = $cmd
    launcher = $launcher
    shimDirectory = $shim
    installedAt = (Get-Date).ToString('o')
}
$proof = Join-Path $root 'install-proof.json'
Write-JsonFile -Path $proof -Value $record
$record.proof = $proof
$record | ConvertTo-Json -Depth 8
