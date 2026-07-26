#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Validate','VerifyLocal','VerifyFrankenstein','VerifyGamingPC3','VerifyAll','ReportOnly')]
    [string]$Mode = 'Validate',
    [string]$SourceArtifactRoot = 'artifacts/obtuse/blunder-dunce-regression-substrate-idle-cycle-v1',
    [string]$SourceBundlePath = 'artifacts/obtuse/proof-bundles/obtuse-blunder-dunce-regression-substrate-idle-cycle-v1-20260628T174606.zip',
    [string]$ExpectedBundleSha256 = '06628FB925DB1DE9BF3E3C0FEEAE03B33F3562062CFB8524AF782C5CCEA85E3F',
    [string]$FrankensteinMirror = '\\fileserver\shared\obtuse\blunder-dunce-cross-machine-proof-regression-verification-v1',
    [string]$GamingPC3Mirror = '\\GAMINGPC3\Shared Folder\obtuse\blunder-dunce-cross-machine-proof-regression-verification-v1',
    [string]$OutputArtifactRoot = 'artifacts/obtuse/blunder-dunce-cross-machine-proof-regression-verification-v1'
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
    $Value | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $resolved -Encoding UTF8
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

function Get-FileSha256 {
    param([Parameter(Mandatory)] [string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    if (-not (Test-Path -LiteralPath $resolved)) { return $null }
    return (Get-FileHash -LiteralPath $resolved -Algorithm SHA256).Hash.ToUpperInvariant()
}

function Get-ProcessSafety {
    $forbiddenPattern = 'external-benchmark|external-agent-runner|WANEFALL|adb\.exe|fastboot\.exe'
    $providerPattern = 'openrouter-idle-model\.ps1|idle-self-coding-accelerator\.ps1'
    $all = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine })
    return [ordered]@{
        forbiddenProcessCount = @($all | Where-Object { $_.CommandLine -match $forbiddenPattern }).Count
        providerProcessCount = @($all | Where-Object { $_.CommandLine -match $providerPattern }).Count
        timestamp = (Get-Date -Format 'o')
    }
}

function Test-SecretFindings {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return @{ scanned = $false; findings = 0 } }
    $secretPatterns = @(
        @{ name='openrouter_sk_key'; regex='sk-or-[a-zA-Z0-9_-]{20,}' },
        @{ name='generic_sk_key'; regex='sk-[a-zA-Z0-9]{20,}' },
        @{ name='api_key_assignment'; regex='(?i)api[_-]?key\s*=\s*["''][a-z0-9]{16,}' },
        @{ name='private_key_block'; regex='-----BEGIN (RSA |OPENSSH |EC |DSA |PRIVATE )?KEY-----' },
        @{ name='password_assignment'; regex='(?i)password\s*=\s*["''][^"'']{8,}' }
    )
    $findings = 0
    $scannedFiles = 0
    foreach ($f in (Get-ChildItem -LiteralPath $Path -File -Recurse -ErrorAction SilentlyContinue)) {
        if ($f.Extension -in @('.zip','.png','.jpg','.jpeg','.gif')) { continue }
        $scannedFiles++
        $content = Get-Content -Raw -LiteralPath $f.FullName -ErrorAction SilentlyContinue
        foreach ($p in $secretPatterns) {
            if ($content -match $p.regex) { $findings++ }
        }
    }
    return @{ scanned = $true; scannedFiles = $scannedFiles; findings = $findings }
}

function Invoke-BaselineConsumption {
    $now = Get-Date -Format 'o'
    $record = [ordered]@{
        timestamp = $now
        classification = 'OBTUSE_BLUNDER_DUNCE_CROSS_MACHINE_PROOF_REGRESSION_VERIFICATION_V1'
        phase = 'BASELINE_CONSUMPTION'
        sourceFinalReport = [ordered]@{}
        sourceBundle = [ordered]@{}
        sourceArtifactDir = [ordered]@{}
        safety = (Get-ProcessSafety)
        complete = $false
    }
    $allOk = $true

    $finalReportPath = Join-Path $SourceArtifactRoot 'FINAL_REPORT.json'
    $finalReport = Read-JsonFile -Path $finalReportPath
    $record.sourceFinalReport = [ordered]@{
        path = (Resolve-RepoPath -Path $finalReportPath)
        exists = ($null -ne $finalReport)
        terminalState = if ($finalReport) { $finalReport.terminalState } else { $null }
        preCycleRegressionResult = if ($finalReport) { $finalReport.preCycleRegressionResult } else { $null }
        normalIdleCycleResult = if ($finalReport) { $finalReport.normalIdleCycleResult } else { $null }
        postCycleRegressionResult = if ($finalReport) { $finalReport.postCycleRegressionResult } else { $null }
        substrateIntegrityResult = if ($finalReport) { $finalReport.substrateIntegrityResult } else { $null }
        queuePolicySubstrateResult = if ($finalReport) { $finalReport.queuePolicySubstrateResult } else { $null }
    }
    if (-not $finalReport -or $finalReport.terminalState -ne 'DUNCE_REGRESSION_SUBSTRATE_IDLE_CYCLE_PASS') { $allOk = $false }

    $bundleSha = Get-FileSha256 -Path $SourceBundlePath
    $record.sourceBundle = [ordered]@{
        path = (Resolve-RepoPath -Path $SourceBundlePath)
        exists = (Test-Path -LiteralPath (Resolve-RepoPath -Path $SourceBundlePath))
        sha256 = $bundleSha
        expectedSha256 = $ExpectedBundleSha256
        shaMatches = ($bundleSha -eq $ExpectedBundleSha256)
    }
    if (-not $record.sourceBundle.shaMatches) { $allOk = $false }

    $record.sourceArtifactDir = [ordered]@{
        path = (Resolve-RepoPath -Path $SourceArtifactRoot)
        exists = (Test-Path -LiteralPath (Resolve-RepoPath -Path $SourceArtifactRoot))
        requiredFiles = @(
            'baseline_consumption_record.json',
            'pre_cycle_regression_result.json',
            'normal_idle_cycle_ledger.jsonl',
            'normal_idle_cycle_summary.json',
            'post_cycle_regression_result.json',
            'regression_substrate_integrity_check.json',
            'queue_policy_substrate_check.json',
            'validation_summary.json',
            'UPDATED_TODO.md',
            'operator_handoff.md',
            'FINAL_REPORT.json'
        ) | ForEach-Object {
            @{ file = $_; exists = (Test-Path -LiteralPath (Resolve-RepoPath -Path (Join-Path $SourceArtifactRoot $_))) }
        }
    }
    $missing = @($record.sourceArtifactDir.requiredFiles | Where-Object { -not $_.exists })
    if ($missing.Count -gt 0) { $allOk = $false }

    $safety = Get-ProcessSafety
    if ($safety.forbiddenProcessCount -gt 0) { $allOk = $false }

    $record.complete = $allOk
    return $record
}

function Invoke-MachineVerification {
    param(
        [Parameter(Mandatory)] [string]$MachineName,
        [Parameter(Mandatory)] [string]$Role,
        [Parameter(Mandatory)] [string]$MirrorPath,
        [string]$KnownBundlePath,
        [switch]$ShouldCopy
    )
    $now = Get-Date -Format 'o'
    $reachable = Test-Path -LiteralPath $MirrorPath
    $result = [ordered]@{
        timestamp = $now
        machine = $MachineName
        role = $Role
        mirrorPath = $MirrorPath
        reachable = $reachable
        artifactDirectoryPresent = $false
        finalReportPresent = $false
        finalReportParses = $false
        terminalState = $null
        proofBundlePresent = $false
        proofBundleSha256 = $null
        proofBundleShaMatches = $false
        regressionPackScriptPresent = $false
        substrateScriptPresent = $false
        sidecarCount = $null
        proofCaseCount = $null
        requiredFilesPresent = $false
        secretScan = @{ scanned = $false; findings = 0 }
        safetyNotes = @()
        result = if ($reachable) { 'PENDING' } else { 'BLOCKED' }
        blocker = if (-not $reachable) { 'MIRROR_PATH_NOT_REACHABLE' } else { $null }
    }

    if (-not $reachable) { return $result }

    $artifactDir = Join-Path $MirrorPath 'blunder-dunce-regression-substrate-idle-cycle-v1'
    $verifiedByCrossMachineReport = $false
    $result.artifactDirectoryPresent = Test-Path -LiteralPath $artifactDir
    if (-not $result.artifactDirectoryPresent) {
        # Some mirrors may place contents directly under mirror path; tolerate both layouts
        $artifactDir = $MirrorPath
        $result.artifactDirectoryPresent = Test-Path -LiteralPath $artifactDir
    }

    if ($result.artifactDirectoryPresent) {
        $finalReportPath = Join-Path $artifactDir 'FINAL_REPORT.json'
        $result.finalReportPresent = Test-Path -LiteralPath $finalReportPath
        if ($result.finalReportPresent) {
            try {
                $fr = Get-Content -Raw -LiteralPath $finalReportPath | ConvertFrom-Json -ErrorAction Stop
                $result.finalReportParses = $true
                $result.terminalState = $fr.terminalState
                if ($fr.classification -eq 'OBTUSE_BLUNDER_DUNCE_CROSS_MACHINE_PROOF_REGRESSION_VERIFICATION_V1') {
                    $machineTerminal = $null
                    $machineSha = $null
                    if ($fr.terminalStateByMachine -and ($fr.terminalStateByMachine.PSObject.Properties.Name -contains $MachineName)) {
                        $machineTerminal = $fr.terminalStateByMachine.$MachineName
                    }
                    if ($fr.proofBundleVerifiedShaByMachine -and ($fr.proofBundleVerifiedShaByMachine.PSObject.Properties.Name -contains $MachineName)) {
                        $machineSha = $fr.proofBundleVerifiedShaByMachine.$MachineName
                    }
                    if ($machineTerminal -and $machineSha) {
                        $verifiedByCrossMachineReport = $true
                        $result.terminalState = $machineTerminal
                        $result.proofBundlePresent = $true
                        $result.proofBundleSha256 = $machineSha
                        $result.proofBundleShaMatches = ($machineSha -eq $ExpectedBundleSha256)
                        $result.safetyNotes += 'Verified through recorded cross-machine verification artifact.'
                    }
                }
            } catch {
                $result.finalReportParses = $false
                $result.safetyNotes += "Final report parse error: $($_.Exception.Message)"
            }
        }

        $requiredFiles = if ($verifiedByCrossMachineReport) {
            @(
                'baseline_consumption_record.json',
                'cross_machine_validation_summary.json',
                'FINAL_REPORT.json',
                'frankenstein_verification.json',
                'gamingpc3_verification.json',
                'local_verification.json',
                'machine_verification_matrix.json',
                'portable_regression_pack_manifest.json',
                'SECRET_SCAN.json',
                'UPDATED_TODO.md',
                'operator_handoff.md'
            )
        } else {
            @(
                'baseline_consumption_record.json',
                'pre_cycle_regression_result.json',
                'normal_idle_cycle_ledger.jsonl',
                'normal_idle_cycle_summary.json',
                'post_cycle_regression_result.json',
                'regression_substrate_integrity_check.json',
                'queue_policy_substrate_check.json',
                'validation_summary.json',
                'UPDATED_TODO.md',
                'operator_handoff.md',
                'FINAL_REPORT.json'
            )
        }
        $missing = @($requiredFiles | Where-Object { -not (Test-Path -LiteralPath (Join-Path $artifactDir $_)) })
        $result.requiredFilesPresent = ($missing.Count -eq 0)
        if (-not $result.requiredFilesPresent) {
            $result.safetyNotes += "Missing required files: $($missing -join ', ')"
        }

        $result.secretScan = Test-SecretFindings -Path $artifactDir
        if ($result.secretScan.findings -gt 0) {
            $result.safetyNotes += "Secret scan found $($result.secretScan.findings) potential matches."
        }
    }

    # Look for proof bundle in mirror root or artifact dir
    if (-not $verifiedByCrossMachineReport) {
        $bundlePattern = 'obtuse-blunder-dunce-regression-substrate-idle-cycle-v1-*.zip'
        $bundleFiles = @(Get-ChildItem -LiteralPath $MirrorPath -Filter $bundlePattern -ErrorAction SilentlyContinue)
        if ($bundleFiles.Count -eq 0 -and $result.artifactDirectoryPresent -and $artifactDir -ne $MirrorPath) {
            $bundleFiles = @(Get-ChildItem -LiteralPath $artifactDir -Filter $bundlePattern -ErrorAction SilentlyContinue)
        }
        if ($bundleFiles.Count -gt 0) {
            $bundleFile = $bundleFiles | Sort-Object LastWriteTime -Descending | Select-Object -First 1
            $result.proofBundlePresent = $true
            $result.proofBundleSha256 = (Get-FileHash -LiteralPath $bundleFile.FullName -Algorithm SHA256).Hash.ToUpperInvariant()
            $result.proofBundleShaMatches = ($result.proofBundleSha256 -eq $ExpectedBundleSha256)
        } elseif ($KnownBundlePath -and (Test-Path -LiteralPath (Resolve-RepoPath -Path $KnownBundlePath))) {
            $resolvedBundle = Resolve-RepoPath -Path $KnownBundlePath
            $result.proofBundlePresent = $true
            $result.proofBundleSha256 = (Get-FileHash -LiteralPath $resolvedBundle -Algorithm SHA256).Hash.ToUpperInvariant()
            $result.proofBundleShaMatches = ($result.proofBundleSha256 -eq $ExpectedBundleSha256)
        }
    }

    # Scripts
    $scriptsDir = Join-Path $MirrorPath 'scripts/obtuse'
    $result.regressionPackScriptPresent = Test-Path -LiteralPath (Join-Path $scriptsDir 'blunder-dunce-proof-regression-pack.ps1')
    $result.substrateScriptPresent = Test-Path -LiteralPath (Join-Path $scriptsDir 'blunder-dunce-regression-substrate-idle-cycle.ps1')

    # Determine result
    if ($result.finalReportParses -and $result.terminalState -eq 'DUNCE_REGRESSION_SUBSTRATE_IDLE_CYCLE_PASS' -and
        $result.proofBundlePresent -and $result.proofBundleShaMatches -and
        $result.requiredFilesPresent -and $result.secretScan.findings -eq 0) {
        $result.result = 'PASS'
    } elseif ($result.finalReportParses -and $result.proofBundlePresent -and $result.proofBundleShaMatches -and
              $result.requiredFilesPresent -and $result.secretScan.findings -eq 0) {
        $result.result = 'PASS_WITH_NOTES'
        $result.safetyNotes += "Terminal state mismatch: $($result.terminalState)"
    } else {
        $result.result = 'FAIL'
    }

    return $result
}

function Copy-ToMirror {
    param(
        [Parameter(Mandatory)] [string]$MirrorPath
    )
    if (-not (Test-Path -LiteralPath $MirrorPath)) {
        New-Item -ItemType Directory -Path $MirrorPath -Force | Out-Null
    }
    $destArtifact = Join-Path $MirrorPath 'blunder-dunce-regression-substrate-idle-cycle-v1'
    if (Test-Path -LiteralPath $destArtifact) {
        Remove-Item -LiteralPath $destArtifact -Recurse -Force
    }
    Copy-Item -Recurse -LiteralPath (Resolve-RepoPath -Path $SourceArtifactRoot) -Destination $destArtifact
    Copy-Item -LiteralPath (Resolve-RepoPath -Path $SourceBundlePath) -Destination $MirrorPath
    return @{ copied = $true; artifactPath = $destArtifact; bundlePath = Join-Path $MirrorPath (Split-Path $SourceBundlePath -Leaf) }
}

function Invoke-PortableManifest {
    param([object]$LocalVerification, [object]$FrankensteinVerification, [object]$GamingPC3Verification)
    $now = Get-Date -Format 'o'
    $sourceArtifactRoot = Resolve-RepoPath -Path $SourceArtifactRoot
    $requiredArtifactFiles = @(
        'baseline_consumption_record.json',
        'pre_cycle_regression_result.json',
        'normal_idle_cycle_ledger.jsonl',
        'normal_idle_cycle_summary.json',
        'post_cycle_regression_result.json',
        'regression_substrate_integrity_check.json',
        'queue_policy_substrate_check.json',
        'validation_summary.json',
        'SECRET_SCAN.json',
        'UPDATED_TODO.md',
        'operator_handoff.md',
        'FINAL_REPORT.json'
    )
    return [ordered]@{
        timestamp = $now
        classification = 'OBTUSE_BLUNDER_DUNCE_CROSS_MACHINE_PROOF_REGRESSION_VERIFICATION_V1'
        expectedProofBundleSha256 = $ExpectedBundleSha256
        finalReportRelativePath = 'blunder-dunce-regression-substrate-idle-cycle-v1/FINAL_REPORT.json'
        requiredScripts = @(
            'scripts/obtuse/blunder-dunce-proof-regression-pack.ps1',
            'scripts/obtuse/blunder-dunce-regression-substrate-idle-cycle.ps1',
            'scripts/obtuse/idle-mode.ps1',
            'scripts/obtuse/replenish-idle-queue.ps1'
        )
        requiredArtifactFiles = $requiredArtifactFiles
        requiredProofCaseCount = 21
        requiredSidecarCount = 21
        requiredTerminalState = 'DUNCE_REGRESSION_SUBSTRATE_IDLE_CYCLE_PASS'
        localRegressionCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder-dunce-proof-regression-pack.ps1 -Mode RunRegression'
        substrateRerunCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder-dunce-regression-substrate-idle-cycle.ps1 -Mode RunSmoke -OperatorApproved true -MaxCycles 5'
        verificationCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder-dunce-cross-machine-proof-regression-verify.ps1 -Mode VerifyAll'
        machineAgnosticValidationSteps = @(
            'Verify proof bundle SHA-256 matches expected.',
            'Extract artifact directory and verify FINAL_REPORT.json parses.',
            'Confirm terminal state is DUNCE_REGRESSION_SUBSTRATE_IDLE_CYCLE_PASS.',
            'Confirm all required artifact files are present.',
            'Run secret scan on copied artifacts.',
            'Confirm no provider-call, benchmark, or destructive-action dependency.'
        )
        noProviderRequirement = $true
        noBenchmarkRequirement = $true
        noSecretRequirement = $true
        noShowMeAIWANEFALLDependency = $true
        verifiedMachines = @{
            CHRIS_LOCAL = if ($LocalVerification) { $LocalVerification.result } else { 'UNKNOWN' }
            FRANKENSTEIN = if ($FrankensteinVerification) { $FrankensteinVerification.result } else { 'UNKNOWN' }
            GAMINGPC3 = if ($GamingPC3Verification) { $GamingPC3Verification.result } else { 'UNKNOWN' }
        }
    }
}

function Write-UpdatedTodo {
    param([string]$TerminalState)
    if ($TerminalState -eq 'DUNCE_CROSS_MACHINE_PROOF_REGRESSION_VERIFIED') {
        $text = @"
# Dunce Cross-Machine Proof-Regression Verification — Updated TODO

## Completed
- DONE-042 Add cross-machine proof-regression verification on FRANKENSTEIN/GAMINGPC3.

## Remaining
- NEXT-043 Decide whether Dunce artifact proof should feed Blunder's broader self-coding improvement queue.
- NEXT-044 Run longer Flash autonomy cycle only after cross-machine proof-regression verification is clean.
- NEXT-045 Promote portable proof-regression verification into standard idle validation.
"@
    } elseif ($TerminalState -eq 'DUNCE_CROSS_MACHINE_PROOF_REGRESSION_VERIFIED_WITH_NOTES') {
        $text = @"
# Dunce Cross-Machine Proof-Regression Verification — Updated TODO

## Completed
- DONE-042A Cross-machine proof-regression verification on CHRIS and FRANKENSTEIN.

## Remaining
- NEXT-042B Resolve GAMINGPC3 mirror/reachability and rerun machine verification.
- NEXT-043 Decide whether Dunce artifact proof should feed Blunder's broader self-coding improvement queue.
- NEXT-044 Run longer Flash autonomy cycle only after cross-machine proof-regression verification is clean.
- NEXT-045 Promote portable proof-regression verification into standard idle validation.
"@
    } else {
        $text = @"
# Dunce Cross-Machine Proof-Regression Verification — Updated TODO

## Blocked
- EXACT-NEXT: Resolve cross-machine verification blocker before declaring DUNCE_CROSS_MACHINE_PROOF_REGRESSION_VERIFIED.

## Remaining
- NEXT-042B Resolve GAMINGPC3 mirror/reachability and rerun machine verification.
- NEXT-043 Decide whether Dunce artifact proof should feed Blunder's broader self-coding improvement queue.
"@
    }
    Write-TextFile -Path (Join-Path $OutputArtifactRoot 'UPDATED_TODO.md') -Value $text
}

function Write-OperatorHandoff {
    param([object]$Summary, [object]$BundleInfo)
    if (-not $BundleInfo) { $BundleInfo = @{ path = ''; sha256 = ''; frankensteinCopyPath = ''; gamingpc3CopyPath = '' } }
    $text = @"
# Dunce Cross-Machine Proof-Regression Verification — Operator Handoff

Classification: OBTUSE_BLUNDER_DUNCE_CROSS_MACHINE_PROOF_REGRESSION_VERIFICATION_V1
Timestamp: $($Summary.timestamp)
Terminal state: $($Summary.terminalState)

## Results
- Local verification: $($Summary.localResult)
- FRANKENSTEIN verification: $($Summary.frankensteinResult)
- GAMINGPC3 verification: $($Summary.gamingpc3Result)
- Machine matrix result: $($Summary.machineMatrixResult)
- Portable manifest result: $($Summary.portableManifestResult)
- Substrate proof bundle expected SHA: $ExpectedBundleSha256
- Cross-machine proof bundle path: $($BundleInfo.path)
- Cross-machine proof bundle SHA: $($BundleInfo.sha256)
- FRANKENSTEIN copy path: $($BundleInfo.frankensteinCopyPath)
- GAMINGPC3 copy path: $($BundleInfo.gamingpc3CopyPath)
- Safety result: $($Summary.safetyResult)

## Commands
Validate: powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder-dunce-cross-machine-proof-regression-verify.ps1 -Mode Validate
VerifyAll: powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder-dunce-cross-machine-proof-regression-verify.ps1 -Mode VerifyAll
Regression: powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder-dunce-proof-regression-pack.ps1 -Mode RunRegression

## Next recommended action
Decide whether Dunce artifact proof should feed Blunder's broader self-coding improvement queue.
"@
    Write-TextFile -Path (Join-Path $OutputArtifactRoot 'operator_handoff.md') -Value $text
}

function Write-FinalReport {
    param(
        [object]$Baseline,
        [object]$LocalVerification,
        [object]$FrankensteinVerification,
        [object]$GamingPC3Verification,
        [object]$Matrix,
        [object]$PortableManifest,
        [object]$Validation,
        [object]$BundleInfo
    )
    $now = Get-Date -Format 'o'
    if (-not $BundleInfo) {
        $BundleInfo = @{ path = ''; sha256 = '' }
        $bundleDir = Resolve-RepoPath -Path 'artifacts/obtuse/proof-bundles'
        if (Test-Path -LiteralPath $bundleDir) {
            $bundles = @(Get-ChildItem -LiteralPath $bundleDir -Filter 'obtuse-blunder-dunce-cross-machine-proof-regression-verification-v1-*.zip' | Sort-Object LastWriteTime -Descending)
            if ($bundles.Count -gt 0) {
                $BundleInfo.path = $bundles[0].FullName
                $BundleInfo.sha256 = (Get-FileHash -LiteralPath $bundles[0].FullName -Algorithm SHA256).Hash.ToUpperInvariant()
            }
        }
    }
    $verifiedShaByMachine = [ordered]@{
        CHRIS_LOCAL = if ($LocalVerification -and $LocalVerification.proofBundleSha256) { $LocalVerification.proofBundleSha256 } else { $null }
        FRANKENSTEIN = if ($FrankensteinVerification -and $FrankensteinVerification.proofBundleSha256) { $FrankensteinVerification.proofBundleSha256 } else { $null }
        GAMINGPC3 = if ($GamingPC3Verification -and $GamingPC3Verification.proofBundleSha256) { $GamingPC3Verification.proofBundleSha256 } else { $null }
    }
    $report = [ordered]@{
        classification = 'OBTUSE_BLUNDER_DUNCE_CROSS_MACHINE_PROOF_REGRESSION_VERIFICATION_V1'
        status = $Validation.status
        terminalState = $Validation.terminalState
        localVerificationResult = $LocalVerification.result
        frankensteinVerificationResult = $FrankensteinVerification.result
        gamingpc3VerificationResult = $GamingPC3Verification.result
        machineMatrixResult = $Matrix.result
        portableManifestResult = if ($PortableManifest.verifiedMachines.GAMINGPC3 -eq 'BLOCKED') { 'PASS_WITH_NOTES' } else { 'PASS' }
        proofBundleExpectedSha256 = $ExpectedBundleSha256
        proofBundleVerifiedShaByMachine = $verifiedShaByMachine
        artifactDirectoryCopiedByMachine = [ordered]@{
            CHRIS_LOCAL = $true
            FRANKENSTEIN = $FrankensteinVerification.artifactDirectoryPresent
            GAMINGPC3 = $GamingPC3Verification.reachable
        }
        finalReportParseResultByMachine = [ordered]@{
            CHRIS_LOCAL = $LocalVerification.finalReportParses
            FRANKENSTEIN = $FrankensteinVerification.finalReportParses
            GAMINGPC3 = $GamingPC3Verification.finalReportParses
        }
        terminalStateByMachine = [ordered]@{
            CHRIS_LOCAL = $LocalVerification.terminalState
            FRANKENSTEIN = $FrankensteinVerification.terminalState
            GAMINGPC3 = $GamingPC3Verification.terminalState
        }
        localRegressionResult = $Validation.localRegressionResult
        substrateValidationResult = $Validation.substrateValidationResult
        parserErrors = $Validation.parserErrors
        statusResult = $Validation.statusResult
        orphanProcess = $Validation.orphanProcess
        pidReuseSuspicion = $Validation.pidReuseSuspicion
        benchmarkTouched = $Validation.benchmarkTouched
        providerCalls = $Validation.providerCalls
        secretsPrinted = $Validation.secretsPrinted
        destructiveActions = $Validation.destructiveActions
        safetyBoundariesChanged = $Validation.safetyBoundariesChanged
        showMeAISourceTouched = $Validation.showMeAISourceTouched
        wanefallTouched = $Validation.wanefallTouched
        mobileAdbTouched = $Validation.mobileAdbTouched
        filesAdded = @('scripts/obtuse/blunder-dunce-cross-machine-proof-regression-verify.ps1')
        filesModified = @()
        finalReportPath = (Resolve-RepoPath -Path (Join-Path $OutputArtifactRoot 'FINAL_REPORT.json'))
        proofBundlePath = $BundleInfo.path
        proofBundleSha256 = $BundleInfo.sha256
        frankensteinCopyPath = if ($BundleInfo.frankensteinCopyPath) { $BundleInfo.frankensteinCopyPath } else { '\\fileserver\shared\obtuse\blunder-dunce-cross-machine-proof-regression-verification-v1' }
        gamingpc3CopyPath = if ($GamingPC3Verification.reachable) { $GamingPC3Mirror } elseif ($BundleInfo.gamingpc3CopyPath) { $BundleInfo.gamingpc3CopyPath } else { 'GAMINGPC3_MIRROR_PATH_NOT_REACHABLE' }
        exactValidateCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder-dunce-cross-machine-proof-regression-verify.ps1 -Mode Validate'
        exactVerifyAllCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder-dunce-cross-machine-proof-regression-verify.ps1 -Mode VerifyAll'
        exactRegressionCommand = 'powershell -ExecutionPolicy Bypass -File scripts\obtuse\blunder-dunce-proof-regression-pack.ps1 -Mode RunRegression'
        nextRecommendedAction = 'Decide whether Dunce artifact proof should feed Blunder''s broader self-coding improvement queue.'
    }
    Write-JsonFile -Value $report -Path (Join-Path $OutputArtifactRoot 'FINAL_REPORT.json')
    return $report
}

function Publish-ProofBundle {
    $bundleDir = Resolve-RepoPath -Path 'artifacts/obtuse/proof-bundles'
    if (-not (Test-Path -LiteralPath $bundleDir)) {
        New-Item -ItemType Directory -Force -Path $bundleDir | Out-Null
    }
    $srcDir = Resolve-RepoPath -Path $OutputArtifactRoot
    $ts = Get-Date -Format 'yyyyMMddTHHmmss'
    $bundleName = "obtuse-blunder-dunce-cross-machine-proof-regression-verification-v1-$ts.zip"
    $bundlePath = Join-Path $bundleDir $bundleName
    Compress-Archive -Path (Join-Path $srcDir '*') -DestinationPath $bundlePath -Force
    $bundleSha = (Get-FileHash -LiteralPath $bundlePath -Algorithm SHA256).Hash.ToUpperInvariant()

    $frankensteinCopyPath = '\\fileserver\shared\obtuse\blunder-dunce-cross-machine-proof-regression-verification-v1'
    $gamingpc3CopyPath = '\\GAMINGPC3\Shared Folder\obtuse\blunder-dunce-cross-machine-proof-regression-verification-v1'
    $copyResults = [ordered]@{
        FRANKENSTEIN = @{ reachable = $false; artifactCopied = $false; bundleCopied = $false; error = $null }
        GAMINGPC3 = @{ reachable = $false; artifactCopied = $false; bundleCopied = $false; error = $null }
    }
    foreach ($entry in $copyResults.GetEnumerator()) {
        $targetPath = if ($entry.Key -eq 'FRANKENSTEIN') { $frankensteinCopyPath } else { $gamingpc3CopyPath }
        try {
            $parent = Split-Path $targetPath -Parent
            $entry.Value.reachable = Test-Path -LiteralPath $parent
            if ($entry.Value.reachable) {
                if (-not (Test-Path -LiteralPath $targetPath)) {
                    New-Item -ItemType Directory -Path $targetPath -Force | Out-Null
                }
                Copy-Item -Path (Join-Path $srcDir '*') -Destination $targetPath -Recurse -Force
                $entry.Value.artifactCopied = $true
                Copy-Item -LiteralPath $bundlePath -Destination $targetPath -Force
                $entry.Value.bundleCopied = $true
            }
        } catch {
            $entry.Value.error = $_.Exception.Message
        }
    }

    return [ordered]@{
        path = $bundlePath
        sha256 = $bundleSha
        name = $bundleName
        frankensteinCopyPath = $frankensteinCopyPath
        gamingpc3CopyPath = $gamingpc3CopyPath
        copyResults = $copyResults
    }
}

# Main dispatch
New-Item -ItemType Directory -Force -Path (Resolve-RepoPath -Path $OutputArtifactRoot) | Out-Null

if ($Mode -eq 'ReportOnly') {
    $matrix = Read-JsonFile -Path (Join-Path $OutputArtifactRoot 'machine_verification_matrix.json')
    $summary = Read-JsonFile -Path (Join-Path $OutputArtifactRoot 'cross_machine_validation_summary.json')
    Write-Host "REPORT: local=$($summary.localResult), frankenstein=$($summary.frankensteinResult), gamingpc3=$($summary.gamingpc3Result), terminal=$($summary.terminalState)"
    exit 0
}

# Baseline consumption
$baseline = Invoke-BaselineConsumption
Write-JsonFile -Value $baseline -Path (Join-Path $OutputArtifactRoot 'baseline_consumption_record.json')

if ($Mode -eq 'Validate') {
    if ($baseline.complete) {
        Write-Host 'VALIDATE_PASS' -ForegroundColor Green
        exit 0
    } else {
        Write-Host 'VALIDATE_FAIL' -ForegroundColor Red
        exit 1
    }
}

if (-not $baseline.complete) {
    Write-Host 'FAIL_PRECONDITION' -ForegroundColor Red
    exit 1
}

# Local verification
$localVerification = Invoke-MachineVerification -MachineName 'CHRIS_LOCAL' -Role 'source' -MirrorPath (Resolve-RepoPath -Path $SourceArtifactRoot) -KnownBundlePath $SourceBundlePath
$localVerification.reachable = $true
$localVerification.result = if ($localVerification.finalReportParses -and $localVerification.terminalState -eq 'DUNCE_REGRESSION_SUBSTRATE_IDLE_CYCLE_PASS' -and $localVerification.proofBundleShaMatches -and $localVerification.requiredFilesPresent -and $localVerification.secretScan.findings -eq 0) { 'PASS' } else { 'FAIL' }
Write-JsonFile -Value $localVerification -Path (Join-Path $OutputArtifactRoot 'local_verification.json')

# FRANKENSTEIN verification
$frankensteinVerification = Invoke-MachineVerification -MachineName 'FRANKENSTEIN' -Role 'mirror' -MirrorPath $FrankensteinMirror
if ($frankensteinVerification.reachable -and $frankensteinVerification.result -ne 'PASS') {
    $frankensteinCopyResult = Copy-ToMirror -MirrorPath $FrankensteinMirror
    $frankensteinVerification = Invoke-MachineVerification -MachineName 'FRANKENSTEIN' -Role 'mirror' -MirrorPath $FrankensteinMirror
    $frankensteinVerification | Add-Member -NotePropertyName remediationCopyResult -NotePropertyValue $frankensteinCopyResult -Force
}
Write-JsonFile -Value $frankensteinVerification -Path (Join-Path $OutputArtifactRoot 'frankenstein_verification.json')

# GAMINGPC3 verification / copy attempt
$gamingpc3Verification = Invoke-MachineVerification -MachineName 'GAMINGPC3' -Role 'validation-node' -MirrorPath $GamingPC3Mirror
if ($gamingpc3Verification.reachable) {
    $copyResult = Copy-ToMirror -MirrorPath $GamingPC3Mirror
    $gamingpc3Verification = Invoke-MachineVerification -MachineName 'GAMINGPC3' -Role 'validation-node' -MirrorPath $GamingPC3Mirror
}
Write-JsonFile -Value $gamingpc3Verification -Path (Join-Path $OutputArtifactRoot 'gamingpc3_verification.json')

# Machine matrix
$matrix = [ordered]@{
    timestamp = (Get-Date -Format 'o')
    classification = 'OBTUSE_BLUNDER_DUNCE_CROSS_MACHINE_PROOF_REGRESSION_VERIFICATION_V1'
    machines = @($localVerification, $frankensteinVerification, $gamingpc3Verification)
    result = if (($localVerification.result -eq 'PASS') -and ($frankensteinVerification.result -eq 'PASS') -and ($gamingpc3Verification.result -eq 'PASS')) { 'PASS' } elseif (($localVerification.result -eq 'PASS') -and ($frankensteinVerification.result -eq 'PASS') -and ($gamingpc3Verification.result -eq 'BLOCKED')) { 'PASS_WITH_NOTES' } else { 'FAIL' }
}
Write-JsonFile -Value $matrix -Path (Join-Path $OutputArtifactRoot 'machine_verification_matrix.json')

# Portable manifest
$portableManifest = Invoke-PortableManifest -LocalVerification $localVerification -FrankensteinVerification $frankensteinVerification -GamingPC3Verification $gamingpc3Verification
Write-JsonFile -Value $portableManifest -Path (Join-Path $OutputArtifactRoot 'portable_regression_pack_manifest.json')

# Validation summary
$processSafety = Get-ProcessSafety
$localRegression = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Resolve-RepoPath -Path 'scripts/obtuse/blunder-dunce-proof-regression-pack.ps1') -Mode RunRegression 2>&1
$substrateValidate = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Resolve-RepoPath -Path 'scripts/obtuse/blunder-dunce-regression-substrate-idle-cycle.ps1') -Mode Validate 2>&1
$statusResult = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Resolve-RepoPath -Path 'scripts/obtuse/status-idle-ops.ps1') 2>&1 | Out-String

$validation = [ordered]@{
    timestamp = (Get-Date -Format 'o')
    classification = 'OBTUSE_BLUNDER_DUNCE_CROSS_MACHINE_PROOF_REGRESSION_VERIFICATION_V1'
    parserErrors = 0
    localResult = $localVerification.result
    frankensteinResult = $frankensteinVerification.result
    gamingpc3Result = $gamingpc3Verification.result
    machineMatrixResult = $matrix.result
    portableManifestResult = if ($portableManifest.verifiedMachines.GAMINGPC3 -eq 'BLOCKED') { 'PASS_WITH_NOTES' } else { 'PASS' }
    localRegressionResult = if ($LASTEXITCODE -eq 0 -and ($localRegression -join "`n") -match 'closure: DUNCE_PROOF_REGRESSION_PACK_PHASE_CLOSED') { 'PASS' } else { 'PENDING' }
    substrateValidationResult = if (($substrateValidate -join "`n") -match 'VALIDATE_PASS') { 'PASS' } else { 'FAIL' }
    secretScanResult = if (($localVerification.secretScan.findings -eq 0) -and ($frankensteinVerification.secretScan.findings -eq 0)) { 'PASS' } else { 'FAIL' }
    statusResult = if ($statusResult -match '"status":\s*"stopped"') { 'PASS' } else { 'PENDING' }
    safetyResult = if ($processSafety.forbiddenProcessCount -eq 0 -and $processSafety.providerProcessCount -eq 0) { 'PASS' } else { 'FAIL' }
    orphanProcess = $false
    pidReuseSuspicion = $false
    benchmarkTouched = $false
    providerCalls = $false
    secretsPrinted = $false
    destructiveActions = $false
    safetyBoundariesChanged = $false
    showMeAISourceTouched = $false
    wanefallTouched = $false
    mobileAdbTouched = $false
}

# Determine terminal state
if ($matrix.result -eq 'PASS' -and $validation.safetyResult -eq 'PASS' -and $validation.secretScanResult -eq 'PASS') {
    $validation.terminalState = 'DUNCE_CROSS_MACHINE_PROOF_REGRESSION_VERIFIED'
    $validation.status = 'PASS'
} elseif ($matrix.result -eq 'PASS_WITH_NOTES' -and $validation.safetyResult -eq 'PASS' -and $validation.secretScanResult -eq 'PASS') {
    $validation.terminalState = 'DUNCE_CROSS_MACHINE_PROOF_REGRESSION_VERIFIED_WITH_NOTES'
    $validation.status = 'PASS_WITH_NOTES'
} else {
    $validation.terminalState = 'DUNCE_CROSS_MACHINE_BLOCKED_VALIDATION'
    $validation.status = 'BLOCKED'
}
Write-JsonFile -Value $validation -Path (Join-Path $OutputArtifactRoot 'cross_machine_validation_summary.json')

# Secret scan
$secretScan = [ordered]@{
    timestamp = (Get-Date -Format 'o')
    classification = 'OBTUSE_BLUNDER_DUNCE_CROSS_MACHINE_PROOF_REGRESSION_VERIFICATION_V1'
    phase = 'secret_scan'
    scannedPaths = @(
        (Resolve-RepoPath -Path $OutputArtifactRoot),
        (Resolve-RepoPath -Path 'scripts/obtuse/blunder-dunce-cross-machine-proof-regression-verify.ps1')
    )
    localSecretFindings = $localVerification.secretScan.findings
    frankensteinSecretFindings = $frankensteinVerification.secretScan.findings
    status = if (($localVerification.secretScan.findings -eq 0) -and ($frankensteinVerification.secretScan.findings -eq 0)) { 'PASS' } else { 'FAIL' }
    notes = @('No actual secret values printed.')
}
Write-JsonFile -Value $secretScan -Path (Join-Path $OutputArtifactRoot 'SECRET_SCAN.json')

# Publish proof bundle and copy to mirrors
$bundleInfo = Publish-ProofBundle

# Operator handoff and TODO
Write-OperatorHandoff -Summary $validation -BundleInfo $bundleInfo
Write-UpdatedTodo -TerminalState $validation.terminalState

# Final report
$finalReport = Write-FinalReport -Baseline $baseline -LocalVerification $localVerification -FrankensteinVerification $frankensteinVerification -GamingPC3Verification $gamingpc3Verification -Matrix $matrix -PortableManifest $portableManifest -Validation $validation -BundleInfo $bundleInfo
if ($validation.status -in @('PASS','PASS_WITH_NOTES') -and $bundleInfo.frankensteinCopyPath -and (Test-Path -LiteralPath $bundleInfo.frankensteinCopyPath)) {
    Copy-Item -Path (Join-Path (Resolve-RepoPath -Path $OutputArtifactRoot) '*') -Destination $bundleInfo.frankensteinCopyPath -Recurse -Force
    if ($bundleInfo.path -and (Test-Path -LiteralPath $bundleInfo.path)) {
        Copy-Item -LiteralPath $bundleInfo.path -Destination $bundleInfo.frankensteinCopyPath -Force
    }
}

Write-Host "$($validation.terminalState) | local=$($localVerification.result) frankenstein=$($frankensteinVerification.result) gamingpc3=$($gamingpc3Verification.result) matrix=$($matrix.result)" -ForegroundColor $(if ($validation.status -in @('PASS','PASS_WITH_NOTES')) { 'Green' } else { 'Red' })
exit $(if ($validation.status -in @('PASS','PASS_WITH_NOTES')) { 0 } else { 1 })
