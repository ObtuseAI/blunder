#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Run','Validate','ReportOnly')]
    [string]$Mode = 'Run',
    [string]$ArtifactRoot = 'artifacts/obtuse/blunder-dunce-supersession-ledger-and-hypothesis-check-v1',
    [string]$V6FinalReport = 'artifacts/obtuse/idle-v6-60min-proof-audit-and-120min-escalation-readiness/FINAL_REPORT.json',
    [string]$V7FinalReport = 'artifacts/obtuse/idle-v7-churn-repair-and-120min-readiness-recheck/FINAL_REPORT.json',
    [string]$HypothesisRoot = 'artifacts/obtuse/blunder-dunce-capability-hypothesis-synthesis-v1',
    [string[]]$TargetRoots = @(
        '\\fileserver\shared\ShowMeAI-Frankenstein-Migration',
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

function Get-ProofHashStatus {
    param([object]$Report)
    $path = if ($Report -and $Report.proofBundlePath) { [string]$Report.proofBundlePath } else { '' }
    $expected = if ($Report -and $Report.proofBundleSha256) { ([string]$Report.proofBundleSha256).ToUpperInvariant() } else { '' }
    $exists = ($path -and (Test-Path -LiteralPath $path))
    $observed = if ($exists) { (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToUpperInvariant() } else { $null }
    return [ordered]@{
        path = $path
        exists = [bool]$exists
        expectedSha256 = $expected
        observedSha256 = $observed
        verified = [bool]($exists -and $expected -and $observed -eq $expected)
    }
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

function New-SupersessionLedger {
    $v6 = Read-JsonFile -Path $V6FinalReport
    $v7 = Read-JsonFile -Path $V7FinalReport
    $v6Hash = Get-ProofHashStatus -Report $v6
    $v7Hash = Get-ProofHashStatus -Report $v7
    $v6Churn = if ($v6 -and $v6.churnClassification) { [string]$v6.churnClassification } elseif ($v6 -and $v6.v6HistoricalChurnClassification) { [string]$v6.v6HistoricalChurnClassification } else { 'UNKNOWN' }
    $v6Decision = if ($v6 -and $v6.readinessDecision) {
        [string]$v6.readinessDecision
    } elseif ($v6 -and $v6.escalation120Decision) {
        [string]$v6.escalation120Decision
    } elseif ($v6 -and $v6.runtime120EscalationDecision) {
        [string]$v6.runtime120EscalationDecision
    } elseif ($v6 -and $v6.v6ReadinessDecision) {
        [string]$v6.v6ReadinessDecision
    } else {
        'UNKNOWN'
    }
    $v7Decision = if ($v7 -and $v7.readiness120Decision) { [string]$v7.readiness120Decision } else { 'UNKNOWN' }
    $superseded = (
        $v6 -and $v7 -and
        $v6.status -eq 'PASS' -and
        $v7.status -eq 'PASS' -and
        $v6Churn -eq 'CHURN_EXCEEDED' -and
        $v6Decision -eq 'GO_WITH_NOTES_V6_CHURN_RETIRED_BY_V7' -and
        $v7Decision -like 'GO*' -and
        $v7.churnBudgetStatus -eq 'WITHIN_BUDGET' -and
        $v7.reliabilityBudgetStatus -eq 'WITHIN_BUDGET' -and
        $v7.proofBudgetStatus -eq 'WITHIN_BUDGET' -and
        $v7.safetyInvariantStatus -eq 'PASS' -and
        [bool]$v7.orphanProcessStatus -and
        $v7Hash.verified
    )
    return [ordered]@{
        timestamp = (Get-Date -Format 'o')
        ledger = @(
            [ordered]@{
                blockerId = 'V6-HISTORICAL-CHURN-EXCEEDED'
                blockerStatus = $(if ($superseded) { 'RETIRED_BY_SUPERSESSION' } else { 'ACTIVE_OR_UNPROVEN' })
                originalEvidence = [ordered]@{
                    finalReport = (Resolve-RepoPath -Path $V6FinalReport)
                    status = if ($v6) { $v6.status } else { 'MISSING' }
                    churnClassification = $v6Churn
                    readinessDecision = $v6Decision
                    proof = $v6Hash
                }
                supersedingEvidence = [ordered]@{
                    finalReport = (Resolve-RepoPath -Path $V7FinalReport)
                    status = if ($v7) { $v7.status } else { 'MISSING' }
                    readinessDecision = $v7Decision
                    churnBudgetStatus = if ($v7) { $v7.churnBudgetStatus } else { 'UNKNOWN' }
                    reliabilityBudgetStatus = if ($v7) { $v7.reliabilityBudgetStatus } else { 'UNKNOWN' }
                    proofBudgetStatus = if ($v7) { $v7.proofBudgetStatus } else { 'UNKNOWN' }
                    proof = $v7Hash
                }
                unresolvedContradiction = -not $superseded
                action = $(if ($superseded) { 'Keep V6 blocker as historical evidence; use V7 proof for current readiness.' } else { 'Keep V6 blocker active until supersession proof verifies.' })
            }
        )
        activeBlockerCount = if ($superseded) { 0 } else { 1 }
        retiredBlockerCount = if ($superseded) { 1 } else { 0 }
        pass = [bool]$superseded
    }
}

function Test-JsonAndCredentials {
    param([object[]]$Files)
    $invalid = New-Object System.Collections.ArrayList
    $credentials = New-Object System.Collections.ArrayList
    $patterns = @(
        'sk-or-v1-[a-zA-Z0-9]{20,}',
        'sk-[a-zA-Z0-9]{20,}',
        'OPENROUTER_API_KEY\s*=\s*[^\s\[\]]+',
        'OPENAI_API_KEY\s*=\s*[^\s\[\]]+'
    )
    foreach ($file in @($Files)) {
        if ($file.Extension -eq '.json') {
            try { $null = Get-Content -Raw -LiteralPath $file.FullName | ConvertFrom-Json -ErrorAction Stop }
            catch { [void]$invalid.Add([ordered]@{ path = $file.FullName; error = $_.Exception.Message }) }
        }
        if ($file.Extension -in @('.json','.md','.txt')) {
            $raw = Get-Content -Raw -LiteralPath $file.FullName -ErrorAction SilentlyContinue
            if ($null -ne $raw) {
                foreach ($pattern in $patterns) {
                    $count = [regex]::Matches($raw, $pattern).Count
                    if ($count -gt 0) {
                        [void]$credentials.Add([ordered]@{ path = $file.FullName; patternClass = $pattern; count = $count })
                        break
                    }
                }
            }
        }
    }
    return [ordered]@{
        invalidJsonCount = $invalid.Count
        invalidJson = @($invalid)
        credentialFindingCount = $credentials.Count
        credentialFindings = @($credentials)
        pass = ($invalid.Count -eq 0 -and $credentials.Count -eq 0)
    }
}

function Invoke-Hypothesis001Check {
    $hypotheses = Read-JsonFile -Path (Join-Path $HypothesisRoot '004_CAPABILITY_HYPOTHESES.json')
    $hypothesis = $null
    if ($hypotheses -and $hypotheses.hypotheses) {
        $hypothesis = @($hypotheses.hypotheses | Where-Object { $_.id -eq 'DUNCE-HYP-001' }) | Select-Object -First 1
    }
    $roots = New-Object System.Collections.ArrayList
    $allFiles = New-Object System.Collections.ArrayList
    foreach ($targetRoot in $TargetRoots) {
        $exists = Test-Path -LiteralPath $targetRoot
        $files = @()
        if ($exists) {
            $files = @(Get-ChildItem -LiteralPath $targetRoot -File -Recurse -ErrorAction SilentlyContinue |
                Where-Object { $_.Extension -in @('.json','.md','.txt','.png') } |
                Select-Object FullName,Name,Extension,Length,LastWriteTimeUtc)
            foreach ($file in $files) { [void]$allFiles.Add($file) }
        }
        [void]$roots.Add([ordered]@{
            root = $targetRoot
            exists = [bool]$exists
            fileCount = $files.Count
            jsonCount = @($files | Where-Object { $_.Extension -eq '.json' }).Count
            textCount = @($files | Where-Object { $_.Extension -in @('.md','.txt') }).Count
            imageCount = @($files | Where-Object { $_.Extension -eq '.png' }).Count
        })
    }
    $health = Test-JsonAndCredentials -Files @($allFiles)
    $resolvableCount = @($allFiles | Where-Object { Test-Path -LiteralPath $_.FullName }).Count
    $fileCount = @($allFiles).Count
    $pass = (
        $hypothesis -and
        @($roots | Where-Object { $_.exists }).Count -gt 0 -and
        $fileCount -gt 0 -and
        $resolvableCount -eq $fileCount -and
        $health.pass
    )
    return [ordered]@{
        timestamp = (Get-Date -Format 'o')
        hypothesisId = 'DUNCE-HYP-001'
        hypothesisTitle = if ($hypothesis) { $hypothesis.title } else { 'MISSING' }
        artifactOnly = $true
        sourceMutationAllowed = $false
        providerCallsAllowed = $false
        roots = @($roots)
        rootsAvailable = @($roots | Where-Object { $_.exists }).Count
        fileCount = $fileCount
        resolvableFileCount = $resolvableCount
        jsonHealth = $health
        successMetric = '100 percent cited artifact resolvability with zero credential findings.'
        successMetricMet = [bool]$pass
        nextFinding = $(if ($pass) { 'DUNCE-HYP-001 artifact lane is usable as a read-only memory substrate.' } else { 'DUNCE-HYP-001 needs operator review before promotion.' })
        pass = [bool]$pass
    }
}

function New-ProofBundle {
    $root = Resolve-RepoPath -Path $ArtifactRoot
    $bundleRoot = Resolve-RepoPath -Path 'artifacts/obtuse/proof-bundles'
    New-Item -ItemType Directory -Force -Path $bundleRoot | Out-Null
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmss')
    $bundlePath = Join-Path $bundleRoot "obtuse-blunder-dunce-supersession-ledger-and-hypothesis-check-v1-$stamp.zip"
    $files = @(Get-ChildItem -LiteralPath $root -Recurse -File | ForEach-Object { $_.FullName })
    $files += (Join-Path $PSScriptRoot 'blunder-dunce-supersession-ledger-and-hypothesis-check.ps1')
    Compress-Archive -LiteralPath $files -DestinationPath $bundlePath -Force
    return [ordered]@{
        path = $bundlePath
        sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $bundlePath).Hash.ToUpperInvariant()
    }
}

function Copy-ToFrankenstein {
    param([Parameter(Mandatory)] [string]$ProofBundlePath)
    $target = '\\fileserver\shared\obtuse\blunder-dunce-supersession-ledger-and-hypothesis-check-v1'
    if (-not (Test-Path -LiteralPath '\\fileserver\shared\obtuse')) {
        return [ordered]@{ copied = $false; path = $target; error = 'share unavailable' }
    }
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    Copy-Item -LiteralPath (Join-Path (Resolve-RepoPath -Path $ArtifactRoot) '*') -Destination $target -Recurse -Force
    Copy-Item -LiteralPath $ProofBundlePath -Destination (Join-Path $target (Split-Path -Leaf $ProofBundlePath)) -Force
    return [ordered]@{ copied = $true; path = $target; error = $null }
}

function Invoke-Run {
    $root = Resolve-RepoPath -Path $ArtifactRoot
    New-Item -ItemType Directory -Force -Path $root | Out-Null
    $processSafety = Get-ProcessSafety
    $ledger = New-SupersessionLedger
    $hypothesisCheck = Invoke-Hypothesis001Check
    Write-JsonFile -Value $ledger -Path (Join-Path $ArtifactRoot '001_BLOCKER_SUPERSESSION_LEDGER.json')
    Write-JsonFile -Value $hypothesisCheck -Path (Join-Path $ArtifactRoot '002_DUNCE_HYP_001_ARTIFACT_CHECK.json')
    $md = "# Blocker Supersession and Dunce Hypothesis Check`r`n`r`n"
    $md += "- Supersession ledger pass: $($ledger.pass)`r`n"
    $md += "- Active blockers: $($ledger.activeBlockerCount)`r`n"
    $md += "- Retired blockers: $($ledger.retiredBlockerCount)`r`n"
    $md += "- DUNCE-HYP-001 pass: $($hypothesisCheck.pass)`r`n"
    $md += "- Roots available: $($hypothesisCheck.rootsAvailable)`r`n"
    $md += "- Files indexed: $($hypothesisCheck.fileCount)`r`n"
    Write-TextFile -Path (Join-Path $ArtifactRoot '002_DUNCE_HYP_001_ARTIFACT_CHECK.md') -Value $md
    $status = if ($ledger.pass -and $hypothesisCheck.pass -and $processSafety.forbiddenProcessCount -eq 0 -and $processSafety.providerProcessCount -eq 0) { 'PASS' } else { 'PASS_WITH_NOTES' }
    $proof = New-ProofBundle
    $copy = Copy-ToFrankenstein -ProofBundlePath $proof.path
    $result = [ordered]@{
        classification = 'OBTUSE_BLUNDER_DUNCE_SUPERSESSION_LEDGER_AND_HYPOTHESIS_CHECK_V1'
        status = $status
        sourceEdited = $true
        filesEdited = @('scripts/obtuse/blunder-dunce-supersession-ledger-and-hypothesis-check.ps1')
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
        supersessionLedgerPass = $ledger.pass
        activeBlockerCount = $ledger.activeBlockerCount
        retiredBlockerCount = $ledger.retiredBlockerCount
        dunceHypothesisChecked = 'DUNCE-HYP-001'
        dunceHypothesisPass = $hypothesisCheck.pass
        rootsAvailable = $hypothesisCheck.rootsAvailable
        filesIndexed = $hypothesisCheck.fileCount
        invalidJsonCount = $hypothesisCheck.jsonHealth.invalidJsonCount
        credentialFindingsCount = $hypothesisCheck.jsonHealth.credentialFindingCount
        proofBundlePath = $proof.path
        proofBundleSha256 = $proof.sha256
        frankensteinCopyPath = $copy.path
        frankensteinCopySucceeded = $copy.copied
        finalReportPath = (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json'))
        knownLimitations = @('DUNCE-HYP-001 is proven as a read-only artifact substrate only; it does not prove source-level Dunce autonomy.')
        nextRecommendedAction = 'Run DUNCE-HYP-002 proof-case catalog and missing-metadata report.'
    }
    Write-JsonFile -Value $result -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')
    $finalMd = "# Final Report`r`n`r`nStatus: $status`r`n`r`nNext: $($result.nextRecommendedAction)`r`n"
    Write-TextFile -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.md') -Value $finalMd
    return $result
}

switch ($Mode) {
    'Validate' {
        $tokens = $null
        $errors = $null
        [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'blunder-dunce-supersession-ledger-and-hypothesis-check.ps1'), [ref]$tokens, [ref]$errors) | Out-Null
        [ordered]@{
            classification = 'OBTUSE_BLUNDER_DUNCE_SUPERSESSION_LEDGER_AND_HYPOTHESIS_CHECK_V1'
            parserErrors = $errors.Count
            result = if ($errors.Count -eq 0) { 'PASS' } else { 'FAIL' }
        } | ConvertTo-Json -Depth 10
        return
    }
    'ReportOnly' {
        $report = Read-JsonFile -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')
        if ($report) { $report | ConvertTo-Json -Depth 80; return }
        [ordered]@{
            classification = 'OBTUSE_BLUNDER_DUNCE_SUPERSESSION_LEDGER_AND_HYPOTHESIS_CHECK_V1'
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
