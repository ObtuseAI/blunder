Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:DesktopReceiptSchema = 'dumbmoney.desktop-deployment-receipt.v1'
$script:ReleaseManifestSchema = 'dumbmoney.release-manifest.v1'
$script:CoreArtifactName = 'core-runner-config'
$script:DesktopArtifactName = 'desktop-executable'
$script:ProductionCoreConfigPath = 'C:\ProgramData\DumbMoney\config\core-runner.v1.json'
$script:DesktopExecutableName = 'DumbMoney.exe'
$script:Sha256Pattern = '^[0-9a-f]{64}$'
$script:IdentifierPattern = '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'

function Assert-DumbMoneyPowerShellRuntime {
    [CmdletBinding()]
    param()

    if ($PSVersionTable.PSVersion -lt [Version]'7.4') {
        throw 'DumbMoney desktop launch requires PowerShell 7.4 or newer.'
    }
    if ($null -eq ('System.Text.Json.JsonDocument' -as [type])) {
        throw 'System.Text.Json is required for duplicate-safe JSON parsing.'
    }
}

function Assert-NoDuplicateJsonProperties {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [object]$Element,

        [Parameter(Mandatory)]
        [string]$Context
    )

    $kind = $Element.ValueKind.ToString()
    if ($kind -eq 'Object') {
        $names = [System.Collections.Generic.HashSet[string]]::new(
            [System.StringComparer]::Ordinal
        )
        foreach ($property in $Element.EnumerateObject()) {
            if (-not $names.Add($property.Name)) {
                throw "$Context contains duplicate JSON property '$($property.Name)'."
            }
            Assert-NoDuplicateJsonProperties `
                -Element $property.Value `
                -Context "$Context.$($property.Name)"
        }
        return
    }
    if ($kind -eq 'Array') {
        $index = 0
        foreach ($item in $Element.EnumerateArray()) {
            Assert-NoDuplicateJsonProperties `
                -Element $item `
                -Context "$Context[$index]"
            $index += 1
        }
    }
}

function Read-StrictJsonObject {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$LiteralPath,

        [Parameter(Mandatory)]
        [string]$Context,

        [Parameter(Mandatory)]
        [long]$MaximumBytes
    )

    $item = Get-Item -LiteralPath $LiteralPath -Force -ErrorAction Stop
    if (-not $item.PSIsContainer -and $item.Length -gt $MaximumBytes) {
        throw "$Context exceeds its maximum byte length."
    }
    if ($item.PSIsContainer) {
        throw "$Context must be a file."
    }

    $encoding = [System.Text.UTF8Encoding]::new($false, $true)
    try {
        $text = [System.IO.File]::ReadAllText($item.FullName, $encoding)
    }
    catch {
        throw "$Context is not strict UTF-8 JSON."
    }

    $document = $null
    try {
        $document = [System.Text.Json.JsonDocument]::Parse($text)
        if ($document.RootElement.ValueKind.ToString() -ne 'Object') {
            throw "$Context root must be a JSON object."
        }
        Assert-NoDuplicateJsonProperties `
            -Element $document.RootElement `
            -Context $Context
        $value = $text | ConvertFrom-Json -AsHashtable -Depth 100
        if ($value -isnot [System.Collections.IDictionary]) {
            throw "$Context root must be a JSON object."
        }
        return $value
    }
    catch {
        throw "$Context is invalid: $($_.Exception.Message)"
    }
    finally {
        if ($null -ne $document) {
            $document.Dispose()
        }
    }
}

function Assert-ExactObjectFields {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [System.Collections.IDictionary]$Value,

        [Parameter(Mandatory)]
        [string[]]$Expected,

        [Parameter(Mandatory)]
        [string]$Context
    )

    $expectedSet = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::Ordinal
    )
    foreach ($field in $Expected) {
        [void]$expectedSet.Add($field)
    }
    $actualSet = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::Ordinal
    )
    foreach ($field in $Value.Keys) {
        if ($field -isnot [string]) {
            throw "$Context contains a non-string property name."
        }
        [void]$actualSet.Add([string]$field)
    }
    if (-not $expectedSet.SetEquals($actualSet)) {
        $missing = @($expectedSet | Where-Object { -not $actualSet.Contains($_) } | Sort-Object)
        $unknown = @($actualSet | Where-Object { -not $expectedSet.Contains($_) } | Sort-Object)
        throw "$Context fields are invalid; missing=$($missing -join ','); unknown=$($unknown -join ',')."
    }
}

function Get-RequiredString {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [System.Collections.IDictionary]$Value,

        [Parameter(Mandatory)]
        [string]$Field,

        [Parameter(Mandatory)]
        [string]$Context,

        [int]$MaximumLength = 4096
    )

    $candidate = $Value[$Field]
    if (
        $candidate -isnot [string] -or
        [string]::IsNullOrWhiteSpace($candidate) -or
        $candidate.Length -gt $MaximumLength -or
        $candidate.Contains("`0") -or
        $candidate.Contains("`r") -or
        $candidate.Contains("`n")
    ) {
        throw "$Context.$Field must be bounded non-empty text."
    }
    return [string]$candidate
}

function Get-RequiredSha256 {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [System.Collections.IDictionary]$Value,

        [Parameter(Mandatory)]
        [string]$Field,

        [Parameter(Mandatory)]
        [string]$Context
    )

    $candidate = Get-RequiredString `
        -Value $Value `
        -Field $Field `
        -Context $Context `
        -MaximumLength 64
    if ($candidate -cnotmatch $script:Sha256Pattern) {
        throw "$Context.$Field must be 64 lowercase hexadecimal characters."
    }
    return $candidate
}

function Get-CanonicalAbsolutePath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Path,

        [Parameter(Mandatory)]
        [string]$Context
    )

    if (
        -not [System.IO.Path]::IsPathFullyQualified($Path) -or
        $Path -notmatch '^[A-Za-z]:\\'
    ) {
        throw "$Context must be a fully qualified local drive path."
    }
    if ($Path.Substring(2).Contains(':')) {
        throw "$Context must not contain an alternate data stream."
    }
    try {
        return [System.IO.Path]::GetFullPath($Path).TrimEnd(
            [System.IO.Path]::DirectorySeparatorChar,
            [System.IO.Path]::AltDirectorySeparatorChar
        )
    }
    catch {
        throw "$Context is not a canonical local path."
    }
}

function Test-PathEqual {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Left,

        [Parameter(Mandatory)]
        [string]$Right
    )

    return [System.StringComparer]::OrdinalIgnoreCase.Equals($Left, $Right)
}

function Assert-PathBeneath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Candidate,

        [Parameter(Mandatory)]
        [string]$Root,

        [Parameter(Mandatory)]
        [string]$Context
    )

    $prefix = $Root.TrimEnd('\') + '\'
    if (
        (Test-PathEqual -Left $Candidate -Right $Root) -or
        -not $Candidate.StartsWith(
            $prefix,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "$Context must resolve strictly beneath the immutable release root."
    }
}

function Assert-NoReparseChain {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Path,

        [Parameter(Mandatory)]
        [string]$Context
    )

    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (
        ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
    ) {
        throw "$Context contains a reparse point."
    }
    $current = if ($item.PSIsContainer) {
        $item
    }
    else {
        $item.Directory
    }
    while ($null -ne $current) {
        if (
            ($current.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            throw "$Context contains a reparse point."
        }
        $current = $current.Parent
    }
}

function Get-FileEvidence {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$LiteralPath,

        [Parameter(Mandatory)]
        [string]$Context,

        [switch]$RequirePortableExecutable
    )

    $item = Get-Item -LiteralPath $LiteralPath -Force -ErrorAction Stop
    if ($item.PSIsContainer) {
        throw "$Context must be a file."
    }
    Assert-NoReparseChain -Path $item.FullName -Context $Context

    $stream = [System.IO.File]::Open(
        $item.FullName,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::Read
    )
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $digestBytes = $sha.ComputeHash($stream)
        $digest = [System.Convert]::ToHexString($digestBytes).ToLowerInvariant()
        $length = $stream.Length
        if ($RequirePortableExecutable) {
            if ($length -lt 2) {
                throw "$Context is not a Windows portable executable."
            }
            $stream.Position = 0
            $first = $stream.ReadByte()
            $second = $stream.ReadByte()
            if ($first -ne 0x4d -or $second -ne 0x5a) {
                throw "$Context is not a Windows portable executable."
            }
        }
        return [pscustomobject]@{
            Path = $item.FullName
            Sha256 = $digest
            Bytes = $length
        }
    }
    finally {
        $sha.Dispose()
        $stream.Dispose()
    }
}

function Get-SafeReleaseRelativePath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Value,

        [Parameter(Mandatory)]
        [string]$Context
    )

    if (
        [System.IO.Path]::IsPathRooted($Value) -or
        $Value.Contains(':') -or
        $Value.Contains("`0")
    ) {
        throw "$Context must be a release-relative path."
    }
    $segments = @($Value -split '[\\/]')
    if (
        $segments.Count -eq 0 -or
        @($segments | Where-Object { $_ -in @('', '.', '..') }).Count -ne 0
    ) {
        throw "$Context contains an unsafe path segment."
    }
    return $segments -join [System.IO.Path]::DirectorySeparatorChar
}

function Get-InstalledArtifact {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [System.Collections.IDictionary]$Manifest,

        [Parameter(Mandatory)]
        [string]$Name
    )

    $artifacts = $Manifest['installed_artifacts']
    if (
        $artifacts -isnot [System.Collections.IEnumerable] -or
        $artifacts -is [string] -or
        $artifacts -is [System.Collections.IDictionary]
    ) {
        throw 'release manifest installed_artifacts must be an array.'
    }
    $matches = @(
        $artifacts |
            Where-Object {
                $_ -is [System.Collections.IDictionary] -and
                $_['name'] -ceq $Name
            }
    )
    if ($matches.Count -ne 1) {
        throw "release manifest must contain exactly one $Name artifact."
    }
    return [System.Collections.IDictionary]$matches[0]
}

function Get-ReleaseFileRecord {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [System.Collections.IDictionary]$Manifest,

        [Parameter(Mandatory)]
        [string]$RelativePath
    )

    $files = $Manifest['files']
    if (
        $files -isnot [System.Collections.IEnumerable] -or
        $files -is [string] -or
        $files -is [System.Collections.IDictionary]
    ) {
        throw 'release manifest files must be an array.'
    }
    $matches = @()
    foreach ($record in $files) {
        if (
            $record -isnot [System.Collections.IDictionary] -or
            $record['path'] -isnot [string]
        ) {
            continue
        }
        try {
            $recordPath = Get-SafeReleaseRelativePath `
                -Value ([string]$record['path']) `
                -Context 'release file path'
        }
        catch {
            continue
        }
        if (
            [System.StringComparer]::OrdinalIgnoreCase.Equals(
                $recordPath,
                $RelativePath
            )
        ) {
            $matches += $record
        }
    }
    if ($matches.Count -ne 1) {
        throw 'release manifest must contain exactly one files record for the desktop executable.'
    }
    return [System.Collections.IDictionary]$matches[0]
}

function Resolve-DumbMoneyDesktopLaunchPlan {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$ReleaseManifestPath,

        [Parameter(Mandatory)]
        [string]$DeploymentReceiptPath,

        [Parameter(Mandatory)]
        [string]$ExpectedReleasesRoot,

        [Parameter(Mandatory)]
        [string]$ExpectedCoreConfigPath
    )

    Assert-DumbMoneyPowerShellRuntime

    $manifestInput = Get-CanonicalAbsolutePath `
        -Path $ReleaseManifestPath `
        -Context 'release manifest path'
    $receiptInput = Get-CanonicalAbsolutePath `
        -Path $DeploymentReceiptPath `
        -Context 'deployment receipt path'
    $expectedReleases = Get-CanonicalAbsolutePath `
        -Path $ExpectedReleasesRoot `
        -Context 'expected releases root'
    $expectedCoreConfig = Get-CanonicalAbsolutePath `
        -Path $ExpectedCoreConfigPath `
        -Context 'expected Core config path'

    Assert-NoReparseChain -Path $receiptInput -Context 'deployment receipt path'
    $receipt = Read-StrictJsonObject `
        -LiteralPath $receiptInput `
        -Context 'desktop deployment receipt' `
        -MaximumBytes 65536
    Assert-ExactObjectFields `
        -Value $receipt `
        -Expected @(
            'schema_version',
            'receipt_id',
            'release_id',
            'release_root',
            'release_manifest_path',
            'release_manifest_sha256',
            'desktop_executable_path',
            'desktop_executable_sha256',
            'approved'
        ) `
        -Context 'desktop deployment receipt'

    if ($receipt['schema_version'] -cne $script:DesktopReceiptSchema) {
        throw 'desktop deployment receipt schema_version is unsupported.'
    }
    $receiptId = Get-RequiredString `
        -Value $receipt `
        -Field 'receipt_id' `
        -Context 'desktop deployment receipt' `
        -MaximumLength 128
    if ($receiptId -cnotmatch $script:IdentifierPattern) {
        throw 'desktop deployment receipt receipt_id is invalid.'
    }
    if ($receipt['approved'] -isnot [bool] -or $receipt['approved'] -ne $true) {
        throw 'desktop deployment receipt must contain approved=true.'
    }
    $releaseId = Get-RequiredString `
        -Value $receipt `
        -Field 'release_id' `
        -Context 'desktop deployment receipt' `
        -MaximumLength 128
    if (
        $releaseId -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' -or
        $releaseId -match '(?i)(TO_BE_RESOLVED|CHANGEME|PLACEHOLDER)'
    ) {
        throw 'desktop deployment receipt release_id is invalid or unresolved.'
    }
    $receiptRoot = Get-CanonicalAbsolutePath `
        -Path (
            Get-RequiredString `
                -Value $receipt `
                -Field 'release_root' `
                -Context 'desktop deployment receipt'
        ) `
        -Context 'desktop deployment receipt release_root'
    $expectedReleaseRoot = Get-CanonicalAbsolutePath `
        -Path (Join-Path -Path $expectedReleases -ChildPath $releaseId) `
        -Context 'expected immutable release root'
    if (-not (Test-PathEqual -Left $receiptRoot -Right $expectedReleaseRoot)) {
        throw 'desktop deployment receipt release_root is outside the expected release directory.'
    }
    Assert-NoReparseChain -Path $receiptRoot -Context 'immutable release root'

    $receiptManifest = Get-CanonicalAbsolutePath `
        -Path (
            Get-RequiredString `
                -Value $receipt `
                -Field 'release_manifest_path' `
                -Context 'desktop deployment receipt'
        ) `
        -Context 'desktop deployment receipt release_manifest_path'
    $expectedManifest = Get-CanonicalAbsolutePath `
        -Path (Join-Path -Path $receiptRoot -ChildPath 'release-manifest.json') `
        -Context 'expected release manifest path'
    if (
        -not (Test-PathEqual -Left $manifestInput -Right $receiptManifest) -or
        -not (Test-PathEqual -Left $manifestInput -Right $expectedManifest)
    ) {
        throw 'release manifest input, receipt binding, and immutable release path differ.'
    }
    Assert-NoReparseChain -Path $manifestInput -Context 'release manifest path'
    $manifestEvidence = Get-FileEvidence `
        -LiteralPath $manifestInput `
        -Context 'release manifest'
    $receiptManifestDigest = Get-RequiredSha256 `
        -Value $receipt `
        -Field 'release_manifest_sha256' `
        -Context 'desktop deployment receipt'
    if ($manifestEvidence.Sha256 -cne $receiptManifestDigest) {
        throw 'release manifest bytes do not match the operator-approved deployment receipt.'
    }

    $manifest = Read-StrictJsonObject `
        -LiteralPath $manifestInput `
        -Context 'release manifest' `
        -MaximumBytes 16777216
    if ($manifest['schema_version'] -cne $script:ReleaseManifestSchema) {
        throw 'release manifest schema_version is unsupported.'
    }
    if ($manifest['release_id'] -cne $releaseId) {
        throw 'release manifest release_id does not match the deployment receipt.'
    }
    $booleanManifestFields = @(
        'immutable',
        'broker_actions_authorized',
        'hosted_control_plane',
        'telemetry_publication'
    )
    foreach ($field in $booleanManifestFields) {
        if ($manifest[$field] -isnot [bool]) {
            throw "release manifest $field must be a JSON boolean."
        }
    }
    if (
        $manifest['release_state'] -cne 'SEALED' -or
        $manifest['immutable'] -ne $true -or
        $manifest['broker_actions_authorized'] -ne $false -or
        $manifest['distribution'] -cne 'PRIVATE_LOCAL_ONLY' -or
        $manifest['hosted_control_plane'] -ne $false -or
        $manifest['telemetry_publication'] -ne $false
    ) {
        throw 'release manifest is not a sealed immutable private-local release.'
    }

    $coreArtifact = Get-InstalledArtifact `
        -Manifest $manifest `
        -Name $script:CoreArtifactName
    Assert-ExactObjectFields `
        -Value $coreArtifact `
        -Expected @(
            'name',
            'state',
            'source_path',
            'install_path',
            'sha256',
            'bytes',
            'component'
        ) `
        -Context 'core-runner-config artifact'
    if (
        $coreArtifact['state'] -cne 'SEALED' -or
        $coreArtifact['component'] -cne 'blunder'
    ) {
        throw 'core-runner-config artifact must be SEALED and owned by blunder.'
    }
    $coreInstallPath = Get-CanonicalAbsolutePath `
        -Path (
            Get-RequiredString `
                -Value $coreArtifact `
                -Field 'install_path' `
                -Context 'core-runner-config artifact'
        ) `
        -Context 'core-runner-config install_path'
    if (-not (Test-PathEqual -Left $coreInstallPath -Right $expectedCoreConfig)) {
        throw 'core-runner-config install_path is not the fixed production path.'
    }
    $coreDigest = Get-RequiredSha256 `
        -Value $coreArtifact `
        -Field 'sha256' `
        -Context 'core-runner-config artifact'
    $coreBytes = $coreArtifact['bytes']
    if (
        $coreBytes -isnot [long] -and
        $coreBytes -isnot [int]
    ) {
        throw 'core-runner-config artifact bytes must be an integer.'
    }
    if ($coreBytes -le 0) {
        throw 'core-runner-config artifact bytes must be positive.'
    }
    $coreSourceRelative = Get-SafeReleaseRelativePath `
        -Value (
            Get-RequiredString `
                -Value $coreArtifact `
                -Field 'source_path' `
                -Context 'core-runner-config artifact'
        ) `
        -Context 'core-runner-config source_path'
    $coreSourcePath = Get-CanonicalAbsolutePath `
        -Path (Join-Path -Path $receiptRoot -ChildPath $coreSourceRelative) `
        -Context 'core-runner-config immutable source'
    Assert-PathBeneath `
        -Candidate $coreSourcePath `
        -Root $receiptRoot `
        -Context 'core-runner-config immutable source'
    $coreSourceEvidence = Get-FileEvidence `
        -LiteralPath $coreSourcePath `
        -Context 'core-runner-config immutable source'
    if (
        $coreSourceEvidence.Sha256 -cne $coreDigest -or
        $coreSourceEvidence.Bytes -ne [long]$coreBytes
    ) {
        throw 'core-runner-config immutable source differs from its manifest pin.'
    }
    $installedCoreEvidence = Get-FileEvidence `
        -LiteralPath $coreInstallPath `
        -Context 'installed Core runner config'
    if (
        $installedCoreEvidence.Sha256 -cne $coreDigest -or
        $installedCoreEvidence.Bytes -ne [long]$coreBytes
    ) {
        throw 'installed Core runner config differs from its immutable release pin.'
    }

    $desktopPath = Get-CanonicalAbsolutePath `
        -Path (
            Get-RequiredString `
                -Value $receipt `
                -Field 'desktop_executable_path' `
                -Context 'desktop deployment receipt'
        ) `
        -Context 'desktop executable path'
    Assert-PathBeneath `
        -Candidate $desktopPath `
        -Root $receiptRoot `
        -Context 'desktop executable path'
    if (
        -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
            [System.IO.Path]::GetFileName($desktopPath),
            $script:DesktopExecutableName
        )
    ) {
        throw "desktop executable must be named $script:DesktopExecutableName."
    }
    $expectedDesktopPath = Get-CanonicalAbsolutePath `
        -Path (
            Join-Path `
                -Path $receiptRoot `
                -ChildPath "desktop\$script:DesktopExecutableName"
        ) `
        -Context 'expected desktop executable path'
    if (-not (Test-PathEqual -Left $desktopPath -Right $expectedDesktopPath)) {
        throw 'desktop executable must use the exact immutable release desktop path.'
    }
    $desktopDigest = Get-RequiredSha256 `
        -Value $receipt `
        -Field 'desktop_executable_sha256' `
        -Context 'desktop deployment receipt'

    $desktopArtifact = Get-InstalledArtifact `
        -Manifest $manifest `
        -Name $script:DesktopArtifactName
    Assert-ExactObjectFields `
        -Value $desktopArtifact `
        -Expected @(
            'name',
            'state',
            'source_path',
            'install_path',
            'sha256',
            'bytes',
            'component'
        ) `
        -Context 'desktop-executable artifact'
    if (
        $desktopArtifact['state'] -cne 'SEALED' -or
        $desktopArtifact['component'] -cne 'blunder'
    ) {
        throw 'desktop-executable artifact must be SEALED and owned by blunder.'
    }
    $desktopInstallPath = Get-CanonicalAbsolutePath `
        -Path (
            Get-RequiredString `
                -Value $desktopArtifact `
                -Field 'install_path' `
                -Context 'desktop-executable artifact'
        ) `
        -Context 'desktop-executable install_path'
    if (-not (Test-PathEqual -Left $desktopInstallPath -Right $desktopPath)) {
        throw 'desktop-executable install_path must equal the receipt desktop path.'
    }
    $desktopSourceRelative = Get-SafeReleaseRelativePath `
        -Value (
            Get-RequiredString `
                -Value $desktopArtifact `
                -Field 'source_path' `
                -Context 'desktop-executable artifact'
        ) `
        -Context 'desktop-executable source_path'
    $desktopSourcePath = Get-CanonicalAbsolutePath `
        -Path (Join-Path -Path $receiptRoot -ChildPath $desktopSourceRelative) `
        -Context 'desktop-executable immutable source'
    Assert-PathBeneath `
        -Candidate $desktopSourcePath `
        -Root $receiptRoot `
        -Context 'desktop-executable immutable source'
    if (-not (Test-PathEqual -Left $desktopSourcePath -Right $desktopPath)) {
        throw 'desktop-executable source_path must resolve to the receipt desktop path.'
    }
    $artifactDesktopDigest = Get-RequiredSha256 `
        -Value $desktopArtifact `
        -Field 'sha256' `
        -Context 'desktop-executable artifact'
    if ($artifactDesktopDigest -cne $desktopDigest) {
        throw 'desktop executable digest differs between manifest and receipt.'
    }
    $desktopBytes = $desktopArtifact['bytes']
    if (
        ($desktopBytes -isnot [long] -and $desktopBytes -isnot [int]) -or
        $desktopBytes -le 0
    ) {
        throw 'desktop-executable artifact bytes must be a positive integer.'
    }

    $desktopFileRecord = Get-ReleaseFileRecord `
        -Manifest $manifest `
        -RelativePath $desktopSourceRelative
    Assert-ExactObjectFields `
        -Value $desktopFileRecord `
        -Expected @('path', 'sha256', 'bytes', 'component') `
        -Context 'desktop executable release files record'
    if ($desktopFileRecord['component'] -cne 'blunder') {
        throw 'desktop executable release files record must be owned by blunder.'
    }
    $fileDesktopDigest = Get-RequiredSha256 `
        -Value $desktopFileRecord `
        -Field 'sha256' `
        -Context 'desktop executable release files record'
    $fileDesktopBytes = $desktopFileRecord['bytes']
    if (
        ($fileDesktopBytes -isnot [long] -and $fileDesktopBytes -isnot [int]) -or
        $fileDesktopBytes -le 0
    ) {
        throw 'desktop executable release files record bytes must be a positive integer.'
    }
    if (
        $fileDesktopDigest -cne $desktopDigest -or
        [long]$fileDesktopBytes -ne [long]$desktopBytes
    ) {
        throw 'desktop executable release files record differs from the installed artifact pin.'
    }

    $desktopEvidence = Get-FileEvidence `
        -LiteralPath $desktopPath `
        -Context 'desktop executable' `
        -RequirePortableExecutable
    if (
        $desktopEvidence.Sha256 -cne $desktopDigest -or
        $desktopEvidence.Bytes -ne [long]$desktopBytes
    ) {
        throw 'desktop executable differs from its manifest and operator-approved receipt pins.'
    }

    return [pscustomobject][ordered]@{
        Schema = 'dumbmoney.desktop-launch-plan.v1'
        Status = 'VERIFIED'
        WouldLaunch = $false
        ReceiptId = $receiptId
        ReleaseId = $releaseId
        ReleaseRoot = $receiptRoot
        ReleaseManifestPath = $manifestInput
        ReleaseManifestSha256 = $receiptManifestDigest
        DeploymentReceiptPath = $receiptInput
        CoreRunnerConfigPath = $coreInstallPath
        CoreRunnerConfigSha256 = $coreDigest
        DesktopExecutablePath = $desktopPath
        DesktopExecutableSha256 = $desktopDigest
        WorkingDirectory = [System.IO.Path]::GetDirectoryName($desktopPath)
        ChildEnvironment = [ordered]@{
            DUMBMONEY_CORE_CONFIG_SHA256 = $coreDigest
        }
    }
}

function Start-DumbMoneyDesktopProcess {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [psobject]$Plan
    )

    if ($Plan.Schema -cne 'dumbmoney.desktop-launch-plan.v1') {
        throw 'desktop launch plan schema is invalid.'
    }
    $environment = @{
        DUMBMONEY_CORE_CONFIG_SHA256 = [string]$Plan.CoreRunnerConfigSha256
    }
    if ($environment.Count -ne 1) {
        throw 'desktop child environment must contain exactly one launcher value.'
    }
    $desktopEvidence = Get-FileEvidence `
        -LiteralPath ([string]$Plan.DesktopExecutablePath) `
        -Context 'desktop executable final check' `
        -RequirePortableExecutable
    $coreEvidence = Get-FileEvidence `
        -LiteralPath ([string]$Plan.CoreRunnerConfigPath) `
        -Context 'installed Core runner config final check'
    $manifestEvidence = Get-FileEvidence `
        -LiteralPath ([string]$Plan.ReleaseManifestPath) `
        -Context 'release manifest final check'
    if (
        $desktopEvidence.Sha256 -cne [string]$Plan.DesktopExecutableSha256 -or
        $coreEvidence.Sha256 -cne [string]$Plan.CoreRunnerConfigSha256 -or
        $manifestEvidence.Sha256 -cne [string]$Plan.ReleaseManifestSha256
    ) {
        throw 'desktop launch inputs changed after verification.'
    }

    $startCommand = Get-Command `
        -Name 'Microsoft.PowerShell.Management\Start-Process' `
        -CommandType Cmdlet `
        -ErrorAction Stop
    if (
        -not $startCommand.Parameters.ContainsKey('Environment') -or
        -not $startCommand.Parameters.ContainsKey('UseNewEnvironment')
    ) {
        throw 'Start-Process does not support the required child-only environment boundary.'
    }

    return Microsoft.PowerShell.Management\Start-Process `
        -FilePath ([string]$Plan.DesktopExecutablePath) `
        -WorkingDirectory ([string]$Plan.WorkingDirectory) `
        -Environment $environment `
        -UseNewEnvironment `
        -PassThru `
        -ErrorAction Stop
}

function Invoke-DumbMoneyDesktopLaunch {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$ReleaseManifestPath,

        [Parameter(Mandatory)]
        [string]$DeploymentReceiptPath,

        [Parameter(Mandatory)]
        [string]$ExpectedReleasesRoot,

        [Parameter(Mandatory)]
        [string]$ExpectedCoreConfigPath,

        [switch]$Start
    )

    $plan = Resolve-DumbMoneyDesktopLaunchPlan `
        -ReleaseManifestPath $ReleaseManifestPath `
        -DeploymentReceiptPath $DeploymentReceiptPath `
        -ExpectedReleasesRoot $ExpectedReleasesRoot `
        -ExpectedCoreConfigPath $ExpectedCoreConfigPath
    if (-not $Start) {
        return $plan
    }

    # Repeat the complete verification immediately before the sole effect.
    $finalPlan = Resolve-DumbMoneyDesktopLaunchPlan `
        -ReleaseManifestPath $ReleaseManifestPath `
        -DeploymentReceiptPath $DeploymentReceiptPath `
        -ExpectedReleasesRoot $ExpectedReleasesRoot `
        -ExpectedCoreConfigPath $ExpectedCoreConfigPath
    $process = Start-DumbMoneyDesktopProcess -Plan $finalPlan
    return [pscustomobject][ordered]@{
        Schema = 'dumbmoney.desktop-launch-result.v1'
        Status = 'STARTED'
        ProcessId = $process.Id
        ReleaseId = $finalPlan.ReleaseId
        DesktopExecutableSha256 = $finalPlan.DesktopExecutableSha256
        CoreRunnerConfigSha256 = $finalPlan.CoreRunnerConfigSha256
    }
}

Export-ModuleMember -Function @(
    'Invoke-DumbMoneyDesktopLaunch',
    'Resolve-DumbMoneyDesktopLaunchPlan'
)
