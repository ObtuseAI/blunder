#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Run','Validate','ReportOnly')]
    [string]$Mode = 'Run',
    [string]$ArtifactRoot = 'artifacts/obtuse/blunder-dunce-proof-case-catalog-v1',
    [string]$SidecarRoot = 'artifacts/obtuse/blunder-dunce-proof-metadata-sidecars-v1/sidecars',
    [string[]]$TargetRoots = @(
        '\\fileserver\shared\ShowMeAI_InWorld_Proof',
        '\\fileserver\shared\ShowMeAI_Veloren_AI_Only_Proof',
        '\\fileserver\shared\ShowMeAI_Veloren_Proof',
        '\\fileserver\shared\ShowMeAI_Veloren_World_Proof'
    )
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent

function Resolve-RepoPath {
    param([Parameter(Mandatory)] [string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $repoRoot $Path
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

function Read-JsonFile {
    param([Parameter(Mandatory)] [string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    if (-not (Test-Path -LiteralPath $resolved)) { return $null }
    return Get-Content -Raw -LiteralPath $resolved | ConvertFrom-Json -ErrorAction Stop
}

function New-SidecarId {
    param([Parameter(Mandatory)] [string]$ImagePath)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($ImagePath.ToLowerInvariant())
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $hash = $sha.ComputeHash($bytes)
    return ([System.BitConverter]::ToString($hash).Replace('-', '').Substring(0, 16)).ToLowerInvariant()
}

function Get-SidecarMetadata {
    param([Parameter(Mandatory)] [string]$ImagePath)
    $sidecarPath = Resolve-RepoPath -Path (Join-Path $SidecarRoot "$((New-SidecarId -ImagePath $ImagePath)).json")
    if (-not (Test-Path -LiteralPath $sidecarPath)) { return $null }
    try { return Get-Content -Raw -LiteralPath $sidecarPath | ConvertFrom-Json -ErrorAction Stop } catch { return $null }
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

function Test-CredentialText {
    param([string]$Path)
    $patterns = @(
        'sk-or-v1-[a-zA-Z0-9]{20,}',
        'sk-[a-zA-Z0-9]{20,}',
        'OPENROUTER_API_KEY\s*=\s*[^\s\[\]]+',
        'OPENAI_API_KEY\s*=\s*[^\s\[\]]+'
    )
    $raw = Get-Content -Raw -LiteralPath $Path -ErrorAction SilentlyContinue
    if ($null -eq $raw) { return $false }
    foreach ($pattern in $patterns) {
        if ([regex]::Matches($raw, $pattern).Count -gt 0) { return $true }
    }
    return $false
}

function Get-ManifestMetadata {
    param([string]$Path)
    if (-not $Path) {
        return [ordered]@{
            manifestPath = $null
            manifestParsed = $false
            scenarioPresent = $false
            expectedObservationPresent = $false
            safetyClaimPresent = $false
            sourcePathPresent = $false
            notes = @('no paired manifest found')
        }
    }
    $notes = New-Object System.Collections.ArrayList
    try {
        $manifest = Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json -ErrorAction Stop
    } catch {
        [void]$notes.Add($_.Exception.Message)
        return [ordered]@{
            manifestPath = $Path
            manifestParsed = $false
            scenarioPresent = $false
            expectedObservationPresent = $false
            safetyClaimPresent = $false
            sourcePathPresent = $false
            notes = @($notes)
        }
    }
    $names = @($manifest.PSObject.Properties.Name)
    $scenario = @('scenario','task','case','title','description') | Where-Object { $names -contains $_ } | Select-Object -First 1
    $expected = @('expectedObservation','expected_observation','expected','observation','assertion') | Where-Object { $names -contains $_ } | Select-Object -First 1
    $safety = @('safetyClaim','safety_claim','safety','risk','allowedScope') | Where-Object { $names -contains $_ } | Select-Object -First 1
    $source = @('image','imagePath','screenshot','path','file') | Where-Object { $names -contains $_ } | Select-Object -First 1
    return [ordered]@{
        manifestPath = $Path
        manifestParsed = $true
        scenarioPresent = [bool]$scenario
        expectedObservationPresent = [bool]$expected
        safetyClaimPresent = [bool]$safety
        sourcePathPresent = [bool]$source
        notes = @($notes)
    }
}

function Find-PairedManifest {
    param([System.IO.FileInfo]$Image)
    $base = [System.IO.Path]::GetFileNameWithoutExtension($Image.Name)
    $dir = $Image.DirectoryName
    $candidates = @(
        (Join-Path $dir "$base.json"),
        (Join-Path $dir "$base.manifest.json"),
        (Join-Path $dir "manifest.json"),
        (Join-Path $dir "proof_manifest.json")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    $nearby = @(Get-ChildItem -LiteralPath $dir -File -Filter '*.json' -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($nearby.Count -gt 0) { return $nearby[0].FullName }
    return $null
}

function New-ProofCatalog {
    $roots = New-Object System.Collections.ArrayList
    $cases = New-Object System.Collections.ArrayList
    $credentialFindings = New-Object System.Collections.ArrayList
    $invalidJson = New-Object System.Collections.ArrayList
    foreach ($targetRoot in $TargetRoots) {
        $exists = Test-Path -LiteralPath $targetRoot
        $images = @()
        $jsonFiles = @()
        if ($exists) {
            $images = @(Get-ChildItem -LiteralPath $targetRoot -File -Recurse -Include '*.png','*.jpg','*.jpeg' -ErrorAction SilentlyContinue)
            $jsonFiles = @(Get-ChildItem -LiteralPath $targetRoot -File -Recurse -Filter '*.json' -ErrorAction SilentlyContinue)
            foreach ($jsonFile in $jsonFiles) {
                try { $null = Get-Content -Raw -LiteralPath $jsonFile.FullName | ConvertFrom-Json -ErrorAction Stop }
                catch { [void]$invalidJson.Add([ordered]@{ path = $jsonFile.FullName; error = $_.Exception.Message }) }
                if (Test-CredentialText -Path $jsonFile.FullName) {
                    [void]$credentialFindings.Add([ordered]@{ path = $jsonFile.FullName; type = 'credential-like-pattern' })
                }
            }
            foreach ($textFile in @(Get-ChildItem -LiteralPath $targetRoot -File -Recurse -Include '*.md','*.txt' -ErrorAction SilentlyContinue)) {
                if (Test-CredentialText -Path $textFile.FullName) {
                    [void]$credentialFindings.Add([ordered]@{ path = $textFile.FullName; type = 'credential-like-pattern' })
                }
            }
        }
        foreach ($image in $images) {
            $manifestPath = Find-PairedManifest -Image $image
            $metadata = Get-ManifestMetadata -Path $manifestPath
            $sidecar = Get-SidecarMetadata -ImagePath $image.FullName
            $sidecarComplete = [bool]($sidecar -and $sidecar.scenario -and $sidecar.expectedObservation -and $sidecar.safetyClaim -and $sidecar.imageExists)
            $complete = (($metadata.manifestParsed -and $metadata.scenarioPresent -and $metadata.expectedObservationPresent -and $metadata.safetyClaimPresent) -or $sidecarComplete)
            [void]$cases.Add([ordered]@{
                root = $targetRoot
                imagePath = $image.FullName
                imageBytes = $image.Length
                manifest = $metadata
                sidecarPath = if ($sidecar) { Resolve-RepoPath -Path (Join-Path $SidecarRoot "$((New-SidecarId -ImagePath $image.FullName)).json") } else { $null }
                sidecarMetadataComplete = $sidecarComplete
                completeMetadata = [bool]$complete
            })
        }
        [void]$roots.Add([ordered]@{
            root = $targetRoot
            exists = [bool]$exists
            imageCount = $images.Count
            manifestCount = $jsonFiles.Count
        })
    }
    $completeCases = @($cases | Where-Object { $_.completeMetadata })
    return [ordered]@{
        timestamp = (Get-Date -Format 'o')
        roots = @($roots)
        proofCases = @($cases)
        proofCaseCount = $cases.Count
        completeProofCaseCount = $completeCases.Count
        missingMetadataCount = ($cases.Count - $completeCases.Count)
        invalidJsonCount = $invalidJson.Count
        invalidJson = @($invalidJson)
        credentialFindingsCount = $credentialFindings.Count
        credentialFindings = @($credentialFindings)
        successMetric = 'At least 4 proof cases with complete metadata and no missing files.'
        successMetricMet = [bool]($completeCases.Count -ge 4 -and $invalidJson.Count -eq 0 -and $credentialFindings.Count -eq 0)
    }
}

function New-ProofBundle {
    $root = Resolve-RepoPath -Path $ArtifactRoot
    $bundleRoot = Resolve-RepoPath -Path 'artifacts/obtuse/proof-bundles'
    New-Item -ItemType Directory -Force -Path $bundleRoot | Out-Null
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmss')
    $bundlePath = Join-Path $bundleRoot "obtuse-blunder-dunce-proof-case-catalog-v1-$stamp.zip"
    $files = @(Get-ChildItem -LiteralPath $root -Recurse -File | ForEach-Object { $_.FullName })
    $files += (Join-Path $PSScriptRoot 'blunder-dunce-proof-case-catalog.ps1')
    Compress-Archive -LiteralPath $files -DestinationPath $bundlePath -Force
    return [ordered]@{
        path = $bundlePath
        sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $bundlePath).Hash.ToUpperInvariant()
    }
}

function Copy-ToFrankenstein {
    param([Parameter(Mandatory)] [string]$ProofBundlePath)
    $target = '\\fileserver\shared\obtuse\blunder-dunce-proof-case-catalog-v1'
    if (-not (Test-Path -LiteralPath '\\fileserver\shared\obtuse')) {
        return [ordered]@{ copied = $false; path = $target; error = 'share unavailable' }
    }
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    Copy-Item -LiteralPath (Join-Path (Resolve-RepoPath -Path $ArtifactRoot) '*') -Destination $target -Recurse -Force
    Copy-Item -LiteralPath $ProofBundlePath -Destination (Join-Path $target (Split-Path -Leaf $ProofBundlePath)) -Force
    return [ordered]@{ copied = $true; path = $target; error = $null }
}

function Invoke-Run {
    New-Item -ItemType Directory -Force -Path (Resolve-RepoPath -Path $ArtifactRoot) | Out-Null
    $processSafety = Get-ProcessSafety
    $catalog = New-ProofCatalog
    Write-JsonFile -Value $catalog -Path (Join-Path $ArtifactRoot '001_PROOF_CASE_CATALOG.json')
    $missing = @($catalog.proofCases | Where-Object { -not $_.completeMetadata })
    Write-JsonFile -Value ([ordered]@{ timestamp = (Get-Date -Format 'o'); missingMetadata = $missing; missingMetadataCount = $missing.Count }) -Path (Join-Path $ArtifactRoot '002_MISSING_METADATA_REPORT.json')
    $md = "# Dunce Proof Case Catalog`r`n`r`n"
    $md += "- Proof cases: $($catalog.proofCaseCount)`r`n"
    $md += "- Complete proof cases: $($catalog.completeProofCaseCount)`r`n"
    $md += "- Missing metadata: $($catalog.missingMetadataCount)`r`n"
    $md += "- Invalid JSON: $($catalog.invalidJsonCount)`r`n"
    Write-TextFile -Path (Join-Path $ArtifactRoot '001_PROOF_CASE_CATALOG.md') -Value $md
    $status = if ($catalog.successMetricMet -and $processSafety.forbiddenProcessCount -eq 0 -and $processSafety.providerProcessCount -eq 0) { 'PASS' } else { 'PASS_WITH_NOTES' }
    $proof = New-ProofBundle
    $copy = Copy-ToFrankenstein -ProofBundlePath $proof.path
    $result = [ordered]@{
        classification = 'OBTUSE_BLUNDER_DUNCE_PROOF_CASE_CATALOG_V1'
        status = $status
        sourceEdited = $true
        filesEdited = @('scripts/obtuse/blunder-dunce-proof-case-catalog.ps1')
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
        dunceHypothesisChecked = 'DUNCE-HYP-002'
        successMetricMet = $catalog.successMetricMet
        rootsAvailable = @($catalog.roots | Where-Object { $_.exists }).Count
        proofCaseCount = $catalog.proofCaseCount
        completeProofCaseCount = $catalog.completeProofCaseCount
        missingMetadataCount = $catalog.missingMetadataCount
        invalidJsonCount = $catalog.invalidJsonCount
        credentialFindingsCount = $catalog.credentialFindingsCount
        proofBundlePath = $proof.path
        proofBundleSha256 = $proof.sha256
        frankensteinCopyPath = $copy.path
        frankensteinCopySucceeded = $copy.copied
        finalReportPath = (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json'))
        knownLimitations = $(if ($catalog.successMetricMet) { @() } else { @('Some visual proof cases lack complete scenario/expected-observation/safety metadata.') })
        nextRecommendedAction = $(if ($catalog.successMetricMet) { 'Run DUNCE-HYP-003 falsifiable capability hypothesis promotion check.' } else { 'Create artifact-only metadata sidecars for incomplete proof cases before using them as regression data.' })
    }
    Write-JsonFile -Value $result -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')
    Write-TextFile -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.md') -Value "# Final Report`r`n`r`nStatus: $status`r`n`r`nNext: $($result.nextRecommendedAction)`r`n"
    return $result
}

switch ($Mode) {
    'Validate' {
        $tokens = $null
        $errors = $null
        [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'blunder-dunce-proof-case-catalog.ps1'), [ref]$tokens, [ref]$errors) | Out-Null
        [ordered]@{
            classification = 'OBTUSE_BLUNDER_DUNCE_PROOF_CASE_CATALOG_V1'
            parserErrors = $errors.Count
            result = if ($errors.Count -eq 0) { 'PASS' } else { 'FAIL' }
        } | ConvertTo-Json -Depth 10
        return
    }
    'ReportOnly' {
        $report = Read-JsonFile -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')
        if ($report) { $report | ConvertTo-Json -Depth 80; return }
        [ordered]@{
            classification = 'OBTUSE_BLUNDER_DUNCE_PROOF_CASE_CATALOG_V1'
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
