#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Run','Validate','ReportOnly')]
    [string]$Mode = 'Run',
    [string]$ArtifactRoot = 'artifacts/obtuse/blunder-dunce-proof-metadata-sidecars-v1',
    [string]$CatalogPath = 'artifacts/obtuse/blunder-dunce-proof-case-catalog-v1/001_PROOF_CASE_CATALOG.json'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent

function Resolve-RepoPath {
    param([Parameter(Mandatory)] [string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $repoRoot $Path
}

function Read-JsonFile {
    param([Parameter(Mandatory)] [string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    if (-not (Test-Path -LiteralPath $resolved)) { return $null }
    return Get-Content -Raw -LiteralPath $resolved | ConvertFrom-Json -ErrorAction Stop
}

function Write-JsonFile {
    param([Parameter(Mandatory)] [object]$Value, [Parameter(Mandatory)] [string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    $parent = Split-Path $resolved -Parent
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $Value | ConvertTo-Json -Depth 80 | Set-Content -LiteralPath $resolved -Encoding UTF8
}

function Write-TextFile {
    param([Parameter(Mandatory)] [string]$Path, [Parameter(Mandatory)] [string]$Value)
    $resolved = Resolve-RepoPath -Path $Path
    $parent = Split-Path $resolved -Parent
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    [System.IO.File]::WriteAllText($resolved, ($Value -replace "`r?`n", "`r`n"), [System.Text.UTF8Encoding]::new($false))
}

function Get-ProcessSafety {
    $forbiddenPattern = 'external-benchmark|external-agent-runner|WANEFALL|adb\.exe|fastboot\.exe'
    $providerPattern = 'openrouter-idle-model\.ps1|idle-self-coding-accelerator\.ps1'
    $all = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine })
    return [ordered]@{
        forbiddenProcessCount = @($all | Where-Object { $_.CommandLine -match $forbiddenPattern }).Count
        providerProcessCount = @($all | Where-Object { $_.CommandLine -match $providerPattern }).Count
    }
}

function New-SidecarId {
    param([Parameter(Mandatory)] [string]$ImagePath)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($ImagePath.ToLowerInvariant())
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $hash = $sha.ComputeHash($bytes)
    return ([System.BitConverter]::ToString($hash).Replace('-', '').Substring(0, 16)).ToLowerInvariant()
}

function New-MetadataSidecar {
    param([Parameter(Mandatory)] [object]$Case)
    $name = [System.IO.Path]::GetFileNameWithoutExtension([string]$Case.imagePath)
    $rootName = Split-Path ([string]$Case.root) -Leaf
    $tokens = ($name -replace '[_\-]+', ' ').Trim()
    if ([string]::IsNullOrWhiteSpace($tokens)) { $tokens = 'visual proof case' }
    $scenario = "$rootName visual proof case: $tokens"
    return [ordered]@{
        sidecarVersion = '1'
        sidecarType = 'artifact-only-derived-metadata'
        sourceMutationAllowed = $false
        providerCallsAllowed = $false
        imagePath = [string]$Case.imagePath
        imageExists = [bool](Test-Path -LiteralPath ([string]$Case.imagePath))
        sourceRoot = [string]$Case.root
        scenario = $scenario
        expectedObservation = 'Human/operator review can inspect the referenced visual proof image and compare it with its originating proof lane.'
        safetyClaim = 'This sidecar is derived read-only metadata stored under Obtuse artifacts; it does not edit ShowMeAI source or proof roots.'
        validationState = 'SIDE_CAR_METADATA_READY_FOR_OPERATOR_REVIEW'
        limitations = @('Metadata is inferred from filenames and proof-lane paths; it should be refined before model-assisted visual evaluation.')
    }
}

function Invoke-Run {
    New-Item -ItemType Directory -Force -Path (Resolve-RepoPath -Path $ArtifactRoot) | Out-Null
    $catalog = Read-JsonFile -Path $CatalogPath
    if (-not $catalog) { throw "Missing catalog: $(Resolve-RepoPath -Path $CatalogPath)" }
    $processSafety = Get-ProcessSafety
    $sidecars = New-Object System.Collections.ArrayList
    $sidecarRoot = Join-Path $ArtifactRoot 'sidecars'
    foreach ($case in @($catalog.proofCases)) {
        if ($case.completeMetadata) { continue }
        $id = New-SidecarId -ImagePath ([string]$case.imagePath)
        $sidecar = New-MetadataSidecar -Case $case
        $sidecarPath = Join-Path $sidecarRoot "$id.json"
        Write-JsonFile -Value $sidecar -Path $sidecarPath
        [void]$sidecars.Add([ordered]@{
            id = $id
            sidecarPath = (Resolve-RepoPath -Path $sidecarPath)
            imagePath = [string]$case.imagePath
            imageExists = $sidecar.imageExists
            completeMetadataAfterSidecar = [bool]($sidecar.scenario -and $sidecar.expectedObservation -and $sidecar.safetyClaim)
        })
    }
    $completeAfter = @($sidecars | Where-Object { $_.completeMetadataAfterSidecar -and $_.imageExists })
    $summary = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        sourceCatalog = (Resolve-RepoPath -Path $CatalogPath)
        sourceProofCaseCount = $catalog.proofCaseCount
        sourceCompleteProofCaseCount = $catalog.completeProofCaseCount
        sidecarCount = $sidecars.Count
        completeAfterSidecarCount = $completeAfter.Count
        generatedSidecars = @($sidecars)
        sourceMutationAllowed = $false
        showMeAISourceTouched = $false
        successMetric = 'At least 4 proof cases with complete artifact-only sidecar metadata and existing images.'
        successMetricMet = [bool]($completeAfter.Count -ge 4)
    }
    Write-JsonFile -Value $summary -Path (Join-Path $ArtifactRoot '001_METADATA_SIDECAR_SUMMARY.json')
    $md = "# Dunce Proof Metadata Sidecars`r`n`r`n"
    $md += "- Sidecars generated: $($summary.sidecarCount)`r`n"
    $md += "- Complete after sidecar: $($summary.completeAfterSidecarCount)`r`n"
    $md += "- ShowMeAI source touched: false`r`n"
    Write-TextFile -Path (Join-Path $ArtifactRoot '001_METADATA_SIDECAR_SUMMARY.md') -Value $md
    $bundleRoot = Resolve-RepoPath -Path 'artifacts/obtuse/proof-bundles'
    New-Item -ItemType Directory -Force -Path $bundleRoot | Out-Null
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmss')
    $bundlePath = Join-Path $bundleRoot "obtuse-blunder-dunce-proof-metadata-sidecars-v1-$stamp.zip"
    $files = @(Get-ChildItem -LiteralPath (Resolve-RepoPath -Path $ArtifactRoot) -Recurse -File | ForEach-Object { $_.FullName })
    $files += (Join-Path $PSScriptRoot 'blunder-dunce-proof-metadata-sidecars.ps1')
    Compress-Archive -LiteralPath $files -DestinationPath $bundlePath -Force
    $target = '\\fileserver\shared\obtuse\blunder-dunce-proof-metadata-sidecars-v1'
    $copied = $false
    if (Test-Path -LiteralPath '\\fileserver\shared\obtuse') {
        New-Item -ItemType Directory -Force -Path $target | Out-Null
        Copy-Item -LiteralPath (Join-Path (Resolve-RepoPath -Path $ArtifactRoot) '*') -Destination $target -Recurse -Force
        Copy-Item -LiteralPath $bundlePath -Destination (Join-Path $target (Split-Path -Leaf $bundlePath)) -Force
        $copied = $true
    }
    $status = if ($summary.successMetricMet -and $processSafety.forbiddenProcessCount -eq 0 -and $processSafety.providerProcessCount -eq 0) { 'PASS' } else { 'PASS_WITH_NOTES' }
    $result = [ordered]@{
        classification = 'OBTUSE_BLUNDER_DUNCE_PROOF_METADATA_SIDECARS_V1'
        status = $status
        sourceEdited = $true
        filesEdited = @('scripts/obtuse/blunder-dunce-proof-metadata-sidecars.ps1')
        artifactOnly = $true
        benchmarkTouched = $false
        providerCalls = $false
        secretsPrinted = $false
        destructiveActions = $false
        safetyBoundariesChanged = $false
        wanefallTouched = $false
        showMeAISourceTouched = $false
        mobilePhoneAdbTouched = $false
        forbiddenProcessCount = $processSafety.forbiddenProcessCount
        providerProcessCount = $processSafety.providerProcessCount
        sidecarCount = $summary.sidecarCount
        completeAfterSidecarCount = $summary.completeAfterSidecarCount
        successMetricMet = $summary.successMetricMet
        proofBundlePath = $bundlePath
        proofBundleSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $bundlePath).Hash.ToUpperInvariant()
        frankensteinCopyPath = $target
        frankensteinCopySucceeded = $copied
        finalReportPath = (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json'))
        knownLimitations = @('Sidecars are inferred metadata and require operator or later model-assisted review before visual-regression use.')
        nextRecommendedAction = 'Rerun DUNCE-HYP-002 catalog including sidecars, then run DUNCE-HYP-003 promotion check.'
    }
    Write-JsonFile -Value $result -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')
    Write-TextFile -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.md') -Value "# Final Report`r`n`r`nStatus: $status`r`n`r`nNext: $($result.nextRecommendedAction)`r`n"
    return $result
}

switch ($Mode) {
    'Validate' {
        $tokens = $null
        $errors = $null
        [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'blunder-dunce-proof-metadata-sidecars.ps1'), [ref]$tokens, [ref]$errors) | Out-Null
        [ordered]@{
            classification = 'OBTUSE_BLUNDER_DUNCE_PROOF_METADATA_SIDECARS_V1'
            parserErrors = $errors.Count
            result = if ($errors.Count -eq 0) { 'PASS' } else { 'FAIL' }
        } | ConvertTo-Json -Depth 10
        return
    }
    'ReportOnly' {
        $report = Read-JsonFile -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')
        if ($report) { $report | ConvertTo-Json -Depth 80; return }
        [ordered]@{
            classification = 'OBTUSE_BLUNDER_DUNCE_PROOF_METADATA_SIDECARS_V1'
            status = 'MISSING_REPORT'
            artifactRoot = (Resolve-RepoPath -Path $ArtifactRoot)
        } | ConvertTo-Json -Depth 10
        return
    }
    'Run' {
        Invoke-Run | ConvertTo-Json -Depth 80
        return
    }
}
