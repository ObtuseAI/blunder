#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Validate','Run','ReportOnly')]
    [string]$Mode = 'Run',
    [string]$ArtifactRoot = 'artifacts/obtuse/blunder-dunce-capability-hypothesis-synthesis-v1',
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
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $repoRoot $Path
}

function Write-JsonFile {
    param([Parameter(Mandatory)] [object]$Value, [Parameter(Mandatory)] [string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    $dir = Split-Path $resolved -Parent
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $Value | ConvertTo-Json -Depth 60 | Set-Content -LiteralPath $resolved -Encoding UTF8
}

function Write-TextFile {
    param([Parameter(Mandatory)] [string]$Path, [Parameter(Mandatory)] [string]$Value)
    $resolved = Resolve-RepoPath -Path $Path
    $dir = Split-Path $resolved -Parent
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    [System.IO.File]::WriteAllText($resolved, ($Value -replace "`r?`n", "`r`n"), [System.Text.UTF8Encoding]::new($false))
}

function Read-JsonFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try { return Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json -ErrorAction Stop } catch { return $null }
}

function Get-ForbiddenProcessSummary {
    $patterns = 'external-benchmark|external-agent-runner|WANEFALL|adb\.exe|fastboot\.exe'
    $providerPatterns = 'openrouter-idle-model\.ps1|idle-self-coding-accelerator\.ps1'
    $all = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine })
    return [ordered]@{
        forbiddenProcessCount = @($all | Where-Object { $_.CommandLine -match $patterns }).Count
        providerProcessCount = @($all | Where-Object { $_.CommandLine -match $providerPatterns }).Count
    }
}

function Get-TargetInventory {
    $rows = @()
    foreach ($root in $TargetRoots) {
        $exists = Test-Path -LiteralPath $root
        $files = @()
        if ($exists) {
            $files = @(Get-ChildItem -LiteralPath $root -File -ErrorAction SilentlyContinue |
                Where-Object { $_.Extension -in @('.json','.md','.txt','.png') } |
                Select-Object Name,FullName,Extension,Length,LastWriteTimeUtc)
        }
        $rows += [ordered]@{
            root = $root
            exists = [bool]$exists
            files = $files
            fileCount = $files.Count
        }
    }
    return $rows
}

function Test-JsonAndCredentialHealth {
    param([object[]]$Inventory)
    $invalid = @()
    $credentialFindings = @()
    $jsonChecked = 0
    $credentialFilesChecked = 0
    $patterns = @(
        'sk-or-v1-[a-zA-Z0-9]{20,}',
        'sk-[a-zA-Z0-9]{20,}',
        'OPENROUTER_API_KEY\s*=\s*[^\s\[\]]+',
        'OPENAI_API_KEY\s*=\s*[^\s\[\]]+'
    )
    foreach ($target in $Inventory) {
        foreach ($file in @($target.files)) {
            if ($file.Extension -eq '.json') {
                $jsonChecked++
                try { $null = Get-Content -Raw -LiteralPath $file.FullName | ConvertFrom-Json -ErrorAction Stop }
                catch { $invalid += [ordered]@{ path = $file.FullName; error = $_.Exception.Message } }
            }
            if ($file.Extension -in @('.json','.md','.txt')) {
                $credentialFilesChecked++
                try {
                    $raw = Get-Content -Raw -LiteralPath $file.FullName -ErrorAction Stop
                    foreach ($pattern in $patterns) {
                        $count = [regex]::Matches($raw, $pattern).Count
                        if ($count -gt 0) {
                            $credentialFindings += [ordered]@{ path = $file.FullName; patternClass = $pattern; count = $count }
                            break
                        }
                    }
                } catch {}
            }
        }
    }
    return [ordered]@{
        jsonChecked = $jsonChecked
        invalidJsonCount = $invalid.Count
        invalidJson = $invalid
        credentialFilesChecked = $credentialFilesChecked
        credentialFindingsCount = $credentialFindings.Count
        credentialFindings = $credentialFindings
        pass = ($invalid.Count -eq 0 -and $credentialFindings.Count -eq 0)
    }
}

function Get-EvidenceSignals {
    param([object[]]$Inventory)
    $signals = New-Object System.Collections.ArrayList
    foreach ($target in $Inventory) {
        foreach ($file in @($target.files)) {
            $name = [string]$file.Name
            $signalType = if ($name -match '(?i)manifest') { 'manifest' } elseif ($name -match '(?i)proof') { 'proof' } elseif ($file.Extension -eq '.png') { 'visual-proof' } elseif ($name -match '(?i)handoff|task|plan') { 'handoff' } else { 'artifact' }
            [void]$signals.Add([ordered]@{
                root = $target.root
                path = $file.FullName
                name = $name
                extension = $file.Extension
                signalType = $signalType
                bytes = $file.Length
            })
        }
    }
    return @($signals)
}

function New-Hypotheses {
    param([object[]]$Signals)
    $hasMigration = @($Signals | Where-Object { $_.root -like '*ShowMeAI-Frankenstein-Migration*' }).Count -gt 0
    $hasVisualProof = @($Signals | Where-Object { $_.signalType -eq 'visual-proof' }).Count -gt 0
    $hasManifest = @($Signals | Where-Object { $_.signalType -eq 'manifest' }).Count -gt 0
    $hypotheses = @(
        [ordered]@{
            id = 'DUNCE-HYP-001'
            title = 'ShowMeAI artifact lane can act as a durable external memory substrate.'
            evidence = @('migration task packets', 'role lock records', 'handoff plans')
            measurableTest = 'Create a read-only index and verify every cited file still exists and parses if JSON.'
            successMetric = '100 percent cited artifact resolvability with zero credential findings.'
            nextSafeAction = 'Build artifact index and contradiction ledger under Obtuse artifacts only.'
            operatorGate = 'source-edit approval required before touching ShowMeAI source'
            priority = $(if ($hasMigration) { 'P0' } else { 'P1' })
        },
        [ordered]@{
            id = 'DUNCE-HYP-002'
            title = 'Visual proof manifests can become a capability-regression dataset.'
            evidence = @('Veloren proof screenshots', 'visual proof manifests')
            measurableTest = 'Catalog proof images and manifests; check each proof has a scenario, expected observation, and safety claim.'
            successMetric = 'At least 4 proof cases with complete metadata and no missing files.'
            nextSafeAction = 'Generate a proof-case catalog and missing-metadata report.'
            operatorGate = 'no computer vision/provider calls until separately approved'
            priority = $(if ($hasVisualProof -and $hasManifest) { 'P0' } else { 'P2' })
        },
        [ordered]@{
            id = 'DUNCE-HYP-003'
            title = 'Blunder can improve AGI-adjacent behavior by turning artifacts into falsifiable capability hypotheses.'
            evidence = @('Dunce control lane passed v2 tasks', 'AGI roadmap artifact', 'bounded supervisor proof')
            measurableTest = 'For each hypothesis, require evidence, a measurable test, a stop condition, and a rollback-free artifact output.'
            successMetric = 'Five hypotheses created with explicit pass/fail criteria and no source mutation.'
            nextSafeAction = 'Promote highest-priority hypotheses into idle queue as inspect-artifacts tasks only.'
            operatorGate = 'operator review before runtime or source mutation'
            priority = 'P0'
        },
        [ordered]@{
            id = 'DUNCE-HYP-004'
            title = 'Long-window idle autonomy needs contradiction detection before stronger claims.'
            evidence = @('V6 historical blocker', 'V7 supersession proof', 'PASS_WITH_NOTES audit trail')
            measurableTest = 'Create a ledger that links old blockers to superseding repairs and marks unresolved contradictions.'
            successMetric = 'Every active blocker is either repaired, retired with supersession proof, or still marked blocking.'
            nextSafeAction = 'Add blocker-supersession ledger to the next audit pass.'
            operatorGate = 'none for artifact-only ledger'
            priority = 'P0'
        },
        [ordered]@{
            id = 'DUNCE-HYP-005'
            title = 'Recursive improvement quality increases when candidate generation is constrained by novelty and proof depth.'
            evidence = @('V7 anti-churn throttle', 'Dunce v2 duplicate suppression', 'queue persisted with zero failed tasks')
            measurableTest = 'Score future generated tasks for novelty, utility, proof depth, and reversibility before admission.'
            successMetric = 'Generated/executed ratio remains within budget while at least one novel proof artifact appears per window.'
            nextSafeAction = 'Queue a read-only utility scoring pass for Dunce artifacts.'
            operatorGate = 'operator approval before any provider-backed generation'
            priority = 'P1'
        }
    )
    return $hypotheses
}

function New-ProofBundle {
    $proofRoot = Resolve-RepoPath -Path 'artifacts/obtuse/proof-bundles'
    New-Item -ItemType Directory -Force -Path $proofRoot | Out-Null
    $stamp = Get-Date -Format 'yyyyMMddTHHmmss'
    $zipPath = Join-Path $proofRoot "obtuse-blunder-dunce-capability-hypothesis-synthesis-v1-$stamp.zip"
    $root = Resolve-RepoPath -Path $ArtifactRoot
    $files = @(Get-ChildItem -LiteralPath $root -Recurse -File | ForEach-Object { $_.FullName })
    $files += (Join-Path $PSScriptRoot 'blunder-dunce-capability-hypothesis-synthesis.ps1')
    Compress-Archive -LiteralPath $files -DestinationPath $zipPath -Force
    return [ordered]@{ path = $zipPath; sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath).Hash.ToUpperInvariant() }
}

function Copy-ToFrankenstein {
    param([string]$ProofBundlePath)
    $target = '\\fileserver\shared\obtuse\blunder-dunce-capability-hypothesis-synthesis-v1'
    if (-not (Test-Path -LiteralPath '\\fileserver\shared\obtuse')) {
        return [ordered]@{ copied = $false; path = $target; error = 'share unavailable' }
    }
    try {
        New-Item -ItemType Directory -Force -Path $target | Out-Null
        Copy-Item -LiteralPath (Resolve-RepoPath -Path $ArtifactRoot) -Destination (Split-Path $target -Parent) -Recurse -Force
        Copy-Item -LiteralPath $ProofBundlePath -Destination (Join-Path $target (Split-Path -Leaf $ProofBundlePath)) -Force
        return [ordered]@{ copied = $true; path = $target; error = $null }
    } catch {
        return [ordered]@{ copied = $false; path = $target; error = $_.Exception.Message }
    }
}

function Invoke-Run {
    New-Item -ItemType Directory -Force -Path (Resolve-RepoPath -Path $ArtifactRoot) | Out-Null
    $forbidden = Get-ForbiddenProcessSummary
    $inventory = Get-TargetInventory
    $health = Test-JsonAndCredentialHealth -Inventory $inventory
    $signals = Get-EvidenceSignals -Inventory $inventory
    $hypotheses = New-Hypotheses -Signals $signals
    $queueCandidates = @($hypotheses | ForEach-Object {
        [ordered]@{
            title = "Dunce hypothesis artifact check: $($_.id)"
            actionType = 'inspect-artifacts'
            targetPath = (Resolve-RepoPath -Path $ArtifactRoot)
            sourceProject = 'dunce/showmeai'
            risk = 1
            sourceMutationAllowed = $false
            providerCallsAllowed = $false
            operatorGate = $_.operatorGate
        }
    })
    Write-JsonFile -Value ([ordered]@{ timestamp = (Get-Date -Format 'o'); inventory = $inventory }) -Path (Join-Path $ArtifactRoot '001_TARGET_INVENTORY.json')
    Write-JsonFile -Value ([ordered]@{ timestamp = (Get-Date -Format 'o'); health = $health }) -Path (Join-Path $ArtifactRoot '002_JSON_AND_CREDENTIAL_HEALTH.json')
    Write-JsonFile -Value ([ordered]@{ timestamp = (Get-Date -Format 'o'); signals = $signals; signalCount = $signals.Count }) -Path (Join-Path $ArtifactRoot '003_EVIDENCE_SIGNALS.json')
    Write-JsonFile -Value ([ordered]@{ timestamp = (Get-Date -Format 'o'); hypotheses = $hypotheses; hypothesisCount = $hypotheses.Count }) -Path (Join-Path $ArtifactRoot '004_CAPABILITY_HYPOTHESES.json')
    Write-JsonFile -Value ([ordered]@{ timestamp = (Get-Date -Format 'o'); queueCandidates = $queueCandidates; candidateCount = $queueCandidates.Count }) -Path (Join-Path $ArtifactRoot '005_NEXT_SAFE_QUEUE_CANDIDATES.json')
    $md = "# Dunce Capability Hypotheses`r`n`r`n"
    foreach ($hypothesis in $hypotheses) {
        $md += "## $($hypothesis.id) - $($hypothesis.title)`r`n"
        $md += "- Priority: $($hypothesis.priority)`r`n"
        $md += "- Test: $($hypothesis.measurableTest)`r`n"
        $md += "- Success: $($hypothesis.successMetric)`r`n"
        $md += "- Next safe action: $($hypothesis.nextSafeAction)`r`n"
        $md += "- Gate: $($hypothesis.operatorGate)`r`n`r`n"
    }
    Write-TextFile -Path (Join-Path $ArtifactRoot '004_CAPABILITY_HYPOTHESES.md') -Value $md
    $proof = New-ProofBundle
    $copy = Copy-ToFrankenstein -ProofBundlePath $proof.path
    $result = [ordered]@{
        classification = 'OBTUSE_BLUNDER_DUNCE_CAPABILITY_HYPOTHESIS_SYNTHESIS_V1'
        status = $(if ($health.pass -and $forbidden.forbiddenProcessCount -eq 0) { 'PASS' } else { 'PASS_WITH_NOTES' })
        artifactOnly = $true
        targetRoots = $TargetRoots
        rootsAvailable = @($inventory | Where-Object { $_.exists }).Count
        evidenceSignalCount = $signals.Count
        hypothesisCount = $hypotheses.Count
        queueCandidateCount = $queueCandidates.Count
        invalidJsonCount = $health.invalidJsonCount
        credentialFindingsCount = $health.credentialFindingsCount
        forbiddenProcessCount = $forbidden.forbiddenProcessCount
        providerProcessCount = $forbidden.providerProcessCount
        benchmarkTouched = $false
        providerCalls = $false
        secretsPrinted = $false
        destructiveActions = $false
        safetyBoundariesChanged = $false
        wanefallTouched = $false
        showMeAISourceTouched = $false
        mobilePhoneAdbTouched = $false
        proofBundlePath = $proof.path
        proofBundleSha256 = $proof.sha256
        frankensteinCopyPath = $copy.path
        frankensteinCopySucceeded = $copy.copied
        finalReportPath = (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json'))
        nextRecommendedAction = 'Add blocker-supersession ledger and run the highest-priority Dunce hypothesis artifact check.'
    }
    Write-JsonFile -Value $result -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json')
    $finalMd = "# Final Report`r`n`r`nStatus: $($result.status)`r`n`r`nHypotheses: $($result.hypothesisCount)`r`nEvidence signals: $($result.evidenceSignalCount)`r`nProof bundle: $($result.proofBundlePath)`r`n"
    Write-TextFile -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.md') -Value $finalMd
    return $result
}

New-Item -ItemType Directory -Force -Path (Resolve-RepoPath -Path $ArtifactRoot) | Out-Null

switch ($Mode) {
    'Validate' {
        $tokens = $null; $errors = $null
        [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'blunder-dunce-capability-hypothesis-synthesis.ps1'), [ref]$tokens, [ref]$errors) | Out-Null
        [ordered]@{ classification = 'OBTUSE_BLUNDER_DUNCE_CAPABILITY_HYPOTHESIS_SYNTHESIS_V1'; parserErrors = $errors.Count; result = $(if ($errors.Count -eq 0) { 'PASS' } else { 'FAIL' }) } | ConvertTo-Json -Depth 10
        return
    }
    'Run' {
        Invoke-Run | ConvertTo-Json -Depth 60
        return
    }
    'ReportOnly' {
        $report = Read-JsonFile -Path (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'FINAL_REPORT.json'))
        if ($report) { $report | ConvertTo-Json -Depth 60; return }
        [ordered]@{ classification = 'OBTUSE_BLUNDER_DUNCE_CAPABILITY_HYPOTHESIS_SYNTHESIS_V1'; status = 'MISSING_REPORT'; artifactRoot = (Resolve-RepoPath -Path $ArtifactRoot) } | ConvertTo-Json -Depth 10
        return
    }
}
