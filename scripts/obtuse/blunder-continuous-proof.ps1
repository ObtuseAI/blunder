#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Root,
    [Parameter(Mandatory = $true)]
    [string]$OutputRoot
)

$ErrorActionPreference = 'Stop'
$resolvedRoot = (Resolve-Path -LiteralPath $Root).Path
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputRoot)
New-Item -ItemType Directory -Force -Path $resolvedOutput | Out-Null

function Invoke-ProofCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [string]$Executable,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )
    $log = Join-Path $resolvedOutput "$Name.log"
    $started = [DateTimeOffset]::UtcNow
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $Executable @Arguments 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $log | ForEach-Object { Write-Host $_ }
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    $finished = [DateTimeOffset]::UtcNow
    if ($code -ne 0) {
        throw "$Name failed with exit code $code. Review $log"
    }
    return [ordered]@{
        name = $Name
        exitCode = $code
        startedAt = $started.ToString('o')
        finishedAt = $finished.ToString('o')
        log = $log
        sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $log).Hash
    }
}

Push-Location $resolvedRoot
try {
    $proofs = @()
    $proofs += Invoke-ProofCommand -Name 'tests' -Executable 'python' -Arguments @('-m','unittest','discover','-s','tests','-v')
    $effectCoverageReport = Join-Path $resolvedOutput 'effect-surface-coverage.json'
    $proofs += Invoke-ProofCommand -Name 'effect-surface-coverage' -Executable 'python' -Arguments @(
        'scripts/obtuse/blunder_effect_surface_audit.py','--root',$resolvedRoot,'--output',$effectCoverageReport
    )
    $proofs += Invoke-ProofCommand -Name 'constitutional-epoch-one' -Executable 'python' -Arguments @(
        'scripts/obtuse/blunder_constitutional_epoch_one_proof.py','--root',$resolvedRoot,
        '--output-root',(Join-Path $resolvedOutput 'constitutional-epoch-one')
    )
    $proofs += Invoke-ProofCommand -Name 'physiology-epoch-two-a' -Executable 'python' -Arguments @(
        'scripts/obtuse/blunder_physiology_epoch_two_a_proof.py','--root',$resolvedRoot,
        '--output-root',(Join-Path $resolvedOutput 'physiology-epoch-two-a')
    )
    $proofs += Invoke-ProofCommand -Name 'physiology-epoch-two-b' -Executable 'python' -Arguments @(
        'scripts/obtuse/blunder_physiology_epoch_two_b_proof.py','--root',$resolvedRoot,
        '--output-root',(Join-Path $resolvedOutput 'physiology-epoch-two-b')
    )
    $proofs += Invoke-ProofCommand -Name 'morphogenesis-epoch-three' -Executable 'python' -Arguments @(
        'scripts/obtuse/blunder_morphogenesis_epoch_three_proof.py','--root',$resolvedRoot,
        '--output-root',(Join-Path $resolvedOutput 'morphogenesis-epoch-three')
    )
    $proofs += Invoke-ProofCommand -Name 'sealed-evolution-epoch-four' -Executable 'python' -Arguments @(
        'scripts/obtuse/blunder_sealed_evolution_epoch_four_proof.py','--root',$resolvedRoot,
        '--output-root',(Join-Path $resolvedOutput 'sealed-evolution-epoch-four')
    )
    $proofs += Invoke-ProofCommand -Name 'cognitive-ecology-epoch-five' -Executable 'python' -Arguments @(
        'scripts/obtuse/blunder_cognitive_ecology_epoch_five_proof.py','--root',$resolvedRoot,
        '--output-root',(Join-Path $resolvedOutput 'cognitive-ecology-epoch-five')
    )
    $proofs += Invoke-ProofCommand -Name 'sovereign-observatory-epoch-six' -Executable 'python' -Arguments @(
        'scripts/obtuse/blunder_sovereign_observatory_epoch_six_proof.py','--root',$resolvedRoot,
        '--output-root',(Join-Path $resolvedOutput 'sovereign-observatory-epoch-six')
    )
    $proofs += Invoke-ProofCommand -Name 'self-expansion-epoch-seven' -Executable 'python' -Arguments @(
        'scripts/obtuse/blunder_self_expansion_epoch_seven_proof.py','--root',$resolvedRoot,
        '--output-root',(Join-Path $resolvedOutput 'self-expansion-epoch-seven')
    )
    $proofs += Invoke-ProofCommand -Name 'ccos-operationalization' -Executable 'python' -Arguments @(
        'scripts/obtuse/blunder_ccos_operationalization_proof.py','--root',$resolvedRoot,
        '--output-root',(Join-Path $resolvedOutput 'ccos-operationalization')
    )
    $secretRoot = Join-Path $resolvedOutput 'secret-hygiene'
    $proofs += Invoke-ProofCommand -Name 'secret-hygiene' -Executable 'python' -Arguments @(
        'security/obtuse_secret_hygiene.py','--root',$resolvedRoot,'--artifact-root',$secretRoot
    )
    $proofs += Invoke-ProofCommand -Name 'capability-audit-report' -Executable 'powershell.exe' -Arguments @(
        '-NoProfile','-ExecutionPolicy','Bypass','-File','scripts/obtuse/blunder-s-tier-capability-audit.ps1','-Mode','ReportOnly',
        '-ArtifactRoot',(Join-Path $resolvedOutput 'capability-audit')
    )
    $materializedCommit = (& git -C $resolvedRoot rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $materializedCommit) { throw 'Cannot resolve the materialized proof commit SHA.' }
    $statusLines = @(& git -C $resolvedRoot status --porcelain)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve the proof worktree state.' }
    $worktreeClean = $statusLines.Count -eq 0
    $sourceCommit = if ($env:BLUNDER_SOURCE_SHA) { $env:BLUNDER_SOURCE_SHA } else { $materializedCommit }
    $candidateFiles = @(& git -C $resolvedRoot ls-files --cached --others --exclude-standard) | Sort-Object -Unique
    if ($LASTEXITCODE -ne 0 -or $candidateFiles.Count -eq 0) { throw 'Cannot resolve the proof source candidate set.' }
    $sourceRows = foreach ($relative in $candidateFiles) {
        $candidate = Join-Path $resolvedRoot $relative
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            "$relative $((Get-FileHash -Algorithm SHA256 -LiteralPath $candidate).Hash)"
        }
    }
    $sourceBytes = [System.Text.Encoding]::UTF8.GetBytes(($sourceRows -join "`n"))
    $sourceTreeSha256 = [System.BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash($sourceBytes)).Replace('-', '')
    $proofSubjectSummary = if ($worktreeClean) { "commit $sourceCommit" } else { "review worktree over $sourceCommit" }
    $manifest = [ordered]@{
        schema = 'obtuse.blunder.continuous-proof.v2'
        status = 'PASS'
        generatedAt = [DateTimeOffset]::UtcNow.ToString('o')
        proofSubject = if ($worktreeClean) { 'exact-commit' } else { 'review-worktree' }
        commit = if ($worktreeClean) { $sourceCommit } else { $null }
        baseCommit = $sourceCommit
        materializedCommit = $materializedCommit
        worktreeClean = $worktreeClean
        sourceTreeSha256 = $sourceTreeSha256
        repository = $env:GITHUB_REPOSITORY
        distribution = 'PRIVATE_LOCAL_ONLY'
        publicSourcePreview = $false
        providerCalls = 0
        secretValuesPrinted = $false
        proofs = $proofs
    }
    $manifestPath = Join-Path $resolvedOutput 'continuous-proof.json'
    $manifest | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
    (Get-FileHash -Algorithm SHA256 -LiteralPath $manifestPath).Hash | Set-Content -LiteralPath (Join-Path $resolvedOutput 'continuous-proof.sha256') -Encoding ASCII
    if ($env:GITHUB_STEP_SUMMARY) {
        @(
            '# Blunder release proof'
            ''
            "- Proof subject: $proofSubjectSummary"
            "- Source tree SHA-256: ``$sourceTreeSha256``"
            '- Source proof: PASS'
            '- Distribution posture: private local repository'
            '- Runtime-dependent capability audit: report-only evidence retained'
            '- Provider calls: 0'
            '- Permission expansion: none'
        ) | Add-Content -LiteralPath $env:GITHUB_STEP_SUMMARY -Encoding UTF8
    }
} finally {
    Pop-Location
}
