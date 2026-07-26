#requires -Version 5.1
<#
.SYNOPSIS
    Standard idle validation integration suite for the
    OBTUSE_BLUNDER_LOCAL_FRANKENSTEIN_OPERATIONAL_CLOSURE_V1 mission.

.DESCRIPTION
    Supported modes:
      Validate   - lightweight existence checks for required prior artifacts.
      Run        - execute the full ordered sub-check suite and write records.
      ReportOnly - read existing integration record and print a report line.
#>
[CmdletBinding()]
param(
    [ValidateSet('Validate','Run','ReportOnly')]
    [string]$Mode = 'Validate'
)

$ErrorActionPreference = 'Stop'

$scriptRoot = $PSScriptRoot
$repoRoot = Split-Path (Split-Path $scriptRoot -Parent) -Parent
$artifactRoot = 'artifacts/obtuse/blunder-local-frankenstein-operational-closure-v1'
$classification = 'OBTUSE_BLUNDER_LOCAL_FRANKENSTEIN_OPERATIONAL_CLOSURE_V1'

function Resolve-RepoPath {
    param([Parameter(Mandatory)] [string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $repoRoot $Path
}

function Write-JsonFile {
    param(
        [Parameter(Mandatory)] [object]$Value,
        [Parameter(Mandatory)] [string]$Path
    )
    $resolved = Resolve-RepoPath -Path $Path
    $parent = Split-Path $resolved -Parent
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $Value | ConvertTo-Json -Depth 40 | Set-Content -LiteralPath $resolved -Encoding UTF8
}

function Read-JsonFile {
    param([Parameter(Mandatory)] [string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    if (-not (Test-Path -LiteralPath $resolved)) { return $null }
    try {
        return Get-Content -Raw -LiteralPath $resolved | ConvertFrom-Json -ErrorAction Stop
    } catch {
        return $null
    }
}

function Invoke-ExternalCommand {
    <#
    Runs a command with & and captures output/exit code.
    PowerShell scripts (.ps1) are executed in a child powershell.exe process
    so that their explicit exit statements do not terminate this session.
    #>
    param(
        [Parameter(Mandatory)] [string]$FilePath,
        [string[]]$ArgumentList = @()
    )
    $commandDisplay = "$FilePath"
    if ($ArgumentList.Count -gt 0) {
        $commandDisplay = "$FilePath $($ArgumentList -join ' ')"
    }

    $output = @()
    $exitCode = 0
    try {
        if ($FilePath -like '*.ps1') {
            $psi = New-Object System.Diagnostics.ProcessStartInfo
            $psi.FileName = 'powershell.exe'
            $psi.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$FilePath`" $($ArgumentList -join ' ')"
            $psi.UseShellExecute = $false
            $psi.RedirectStandardOutput = $true
            $psi.RedirectStandardError = $true
            $psi.CreateNoWindow = $true
            $proc = [System.Diagnostics.Process]::Start($psi)
            $stdout = $proc.StandardOutput.ReadToEnd()
            $stderr = $proc.StandardError.ReadToEnd()
            $proc.WaitForExit()
            $exitCode = $proc.ExitCode
            $output = @($stdout, $stderr) | ForEach-Object { if ($_) { $_ -split "`r?`n" } }
        } else {
            $output = & $FilePath @ArgumentList 2>&1
            $exitCode = $LASTEXITCODE
        }
    } catch {
        $output = @($_.Exception.Message)
        $exitCode = 1
    }
    if ($null -eq $exitCode) { $exitCode = 0 }
    $raw = ($output | ForEach-Object { [string]$_ }) -join "`n"
    $parsed = $null
    if (-not [string]::IsNullOrWhiteSpace($raw)) {
        try { $parsed = $raw | ConvertFrom-Json -ErrorAction Stop } catch {}
    }
    return [ordered]@{
        command = $commandDisplay
        exitCode = $exitCode
        raw = $raw
        parsed = $parsed
    }
}

function Test-SecretFindings {
    <#
    Simple regex scan for forbidden secret-like patterns.
    Returns an object with scannedPaths, scannedFiles, findings, and status.
    #>
    param([Parameter(Mandatory)] [string[]]$Paths)
    $secretPatterns = @(
        @{ name = 'openrouter_sk_key'; regex = 'sk-or-[a-zA-Z0-9_-]{20,}' },
        @{ name = 'generic_sk_key'; regex = 'sk-[a-zA-Z0-9]{20,}' },
        @{ name = 'api_key_assignment'; regex = '(?i)api[_-]?key\s*=\s*["''][a-z0-9]{16,}' },
        @{ name = 'private_key_block'; regex = '-----BEGIN (RSA |OPENSSH |EC |DSA |PRIVATE )?KEY-----' },
        @{ name = 'password_assignment'; regex = '(?i)password\s*=\s*["''][^"'']{8,}' },
        @{ name = 'secret_token_assignment'; regex = '(?i)(secret|token)\s*=\s*["''][^"'']{8,}' }
    )
    $findings = 0
    $scannedFiles = 0
    $scannedPaths = @()
    foreach ($path in $Paths) {
        $resolved = Resolve-RepoPath -Path $path
        $scannedPaths += $resolved
        if (-not (Test-Path -LiteralPath $resolved)) { continue }
        $items = if (Test-Path -LiteralPath $resolved -PathType Container) {
            Get-ChildItem -LiteralPath $resolved -File -Recurse -ErrorAction SilentlyContinue
        } else {
            Get-Item -LiteralPath $resolved
        }
        foreach ($item in $items) {
            if ($item.Extension -in @('.zip','.png','.jpg','.jpeg','.gif','.mp4','.avi','.exe','.dll')) { continue }
            $scannedFiles++
            $content = $null
            try { $content = Get-Content -Raw -LiteralPath $item.FullName -ErrorAction SilentlyContinue } catch {}
            if ([string]::IsNullOrWhiteSpace($content)) { continue }
            foreach ($pattern in $secretPatterns) {
                if ($content -match $pattern.regex) { $findings++ }
            }
        }
    }
    $status = if ($findings -eq 0) { 'PASS' } else { 'FAIL' }
    return [ordered]@{
        scannedPaths = $scannedPaths
        scannedFiles = $scannedFiles
        findings = $findings
        status = $status
    }
}

function Get-PropertyValue {
    param([object]$Object, [string]$Name, [object]$DefaultValue = $null)
    if ($null -eq $Object) { return $DefaultValue }
    if ($Object.PSObject.Properties.Name -contains $Name) { return $Object.$Name }
    return $DefaultValue
}

function Get-NestedPropertyValue {
    param([object]$Object, [string]$Path, [object]$DefaultValue = $null)
    $current = $Object
    $segments = $Path -split '\.'
    foreach ($segment in $segments) {
        if ($null -eq $current) { return $DefaultValue }
        if ($current.PSObject.Properties.Name -contains $segment) {
            $current = $current.$segment
        } else {
            return $DefaultValue
        }
    }
    return $current
}

function Truncate-Output {
    param([string]$Output, [int]$Limit = 4096)
    if ($Output.Length -le $Limit) { return $Output }
    return $Output.Substring(0, $Limit) + "`n[...TRUNCATED...]"
}

function New-Subresult {
    param(
        [Parameter(Mandatory)] [string]$Name,
        [Parameter(Mandatory)] [string]$Command,
        [Parameter(Mandatory)] [int]$ExitCode,
        [Parameter(Mandatory)] [string]$RawOutput,
        [Parameter(Mandatory)] [string]$Status,
        [string]$Notes = ''
    )
    return [ordered]@{
        name = $Name
        command = $Command
        exitCode = $ExitCode
        rawOutput = (Truncate-Output -Output $RawOutput)
        status = $Status
        notes = $Notes
    }
}

function Invoke-ValidateMode {
    $required = @(
        @{ path = 'artifacts/obtuse/blunder-dunce-cross-machine-proof-regression-verification-v1/FINAL_REPORT.json'; label = 'cross-machine FINAL_REPORT.json' },
        @{ path = 'artifacts/obtuse/blunder-dunce-cross-machine-proof-regression-verification-v1/portable_regression_pack_manifest.json'; label = 'portable manifest' },
        @{ path = 'artifacts/obtuse/blunder-dunce-cross-machine-proof-regression-verification-v1/machine_verification_matrix.json'; label = 'machine matrix' }
    )
    $proofBundles = @(Get-ChildItem -LiteralPath (Resolve-RepoPath -Path 'artifacts/obtuse/proof-bundles') -Filter 'obtuse-blunder-dunce-cross-machine-proof-regression-verification-v1-*.zip' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)
    $proofBundleExists = ($proofBundles.Count -gt 0)

    $missing = @()
    foreach ($item in $required) {
        if (-not (Test-Path -LiteralPath (Resolve-RepoPath -Path $item.path))) {
            $missing += $item.label
        }
    }
    if (-not $proofBundleExists) { $missing += 'cross-machine proof bundle zip' }

    $summary = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        classification = $classification
        phase = 'Validate'
        result = if ($missing.Count -eq 0) { 'VALIDATE_PASS' } else { 'VALIDATE_FAIL' }
        missingArtifacts = $missing
        proofBundleFound = if ($proofBundleExists) { $proofBundles[0].Name } else { $null }
    }
    Write-JsonFile -Value $summary -Path (Join-Path $artifactRoot 'validation_summary.json')
    Write-Host $summary.result -ForegroundColor $(if ($missing.Count -eq 0) { 'Green' } else { 'Red' })
    if ($missing.Count -gt 0) {
        exit 1
    }
    exit 0
}

function Invoke-ReportOnlyMode {
    $integration = Read-JsonFile -Path (Join-Path $artifactRoot 'standard_idle_validation_integration.json')
    $summary = Read-JsonFile -Path (Join-Path $artifactRoot 'standard_idle_validation_summary.json')
    if (-not $integration -and -not $summary) {
        Write-Host "REPORT_ONLY: no existing records found." -ForegroundColor Yellow
        exit 0
    }
    $overall = if ($summary) { $summary.overallResult } else { 'UNKNOWN' }
    $subresults = if ($summary) { $summary.subresults } else { @() }
    $statusLine = ($subresults | ForEach-Object { "$($_.name)=$($_.status)" }) -join ' '
    Write-Host "REPORT_ONLY: overall=$overall $statusLine"
    exit 0
}

function Invoke-RunMode {
    New-Item -ItemType Directory -Force -Path (Resolve-RepoPath -Path $artifactRoot) | Out-Null

    $subresults = New-Object System.Collections.ArrayList
    $timestamp = Get-Date -Format 'o'

    # 1. StatusCheck
    $statusScript = Join-Path $scriptRoot 'status-idle-ops.ps1'
    $statusResult = Invoke-ExternalCommand -FilePath $statusScript -ArgumentList @()
    $statusParsed = $statusResult.parsed
    $statusPass = ($statusResult.exitCode -eq 0 -and $null -ne $statusParsed)
    $statusNotes = if ($statusPass) { 'status-idle-ops returned parseable JSON.' } else { 'status-idle-ops did not return parseable JSON or exited non-zero.' }
    [void]$subresults.Add((New-Subresult -Name 'StatusCheck' -Command $statusResult.command -ExitCode $statusResult.exitCode -RawOutput $statusResult.raw -Status $(if ($statusPass) { 'PASS' } else { 'FAIL' }) -Notes $statusNotes))

    # 2. V5Validation
    $v5Script = Join-Path $scriptRoot 'idle-reliability-sla-v5.ps1'
    $v5Result = Invoke-ExternalCommand -FilePath $v5Script -ArgumentList @('-Mode','Validate')
    $v5Summary = Read-JsonFile -Path 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle/validation_summary.json'
    $v5Final = Read-JsonFile -Path 'artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle/FINAL_REPORT.json'
    $v5Pass = $false
    $v5Notes = ''
    if ($v5Result.raw -match 'VALIDATE_PASS') {
        $v5Pass = $true
        $v5Notes = 'Console output reported VALIDATE_PASS.'
    } elseif ($v5Summary -and $v5Summary.pass -eq $true) {
        $v5Pass = $true
        $v5Notes = 'validation_summary.json indicates pass.'
    } elseif ($v5Final -and $v5Final.terminalState -like '*PASS*') {
        $v5Pass = $true
        $v5Notes = 'FINAL_REPORT.json terminalState indicates pass.'
    } else {
        $v5Notes = 'No VALIDATE_PASS signal in console, validation_summary, or FINAL_REPORT.'
    }
    [void]$subresults.Add((New-Subresult -Name 'V5Validation' -Command $v5Result.command -ExitCode $v5Result.exitCode -RawOutput $v5Result.raw -Status $(if ($v5Pass) { 'PASS' } else { 'FAIL' }) -Notes $v5Notes))

    # 3. V6ProofAudit
    $v6Script = Join-Path $scriptRoot 'idle-v6-proof-audit-escalation.ps1'
    $v6Result = Invoke-ExternalCommand -FilePath $v6Script -ArgumentList @('-Mode','ReportOnly')
    $v6Pass = $false
    $v6Notes = ''
    if ($v6Result.exitCode -eq 0 -and $null -ne $v6Result.parsed) {
        if ($v6Result.parsed.status -like '*PASS*' -or $v6Result.parsed.available -eq $true -or $v6Result.parsed.pass -eq $true) {
            $v6Pass = $true
            $v6Notes = 'ReportOnly returned available/pass JSON.'
        } else {
            $v6Notes = 'ReportOnly returned JSON but did not indicate pass.'
        }
    } else {
        $v6Notes = 'ReportOnly did not return parseable JSON or exited non-zero.'
    }
    [void]$subresults.Add((New-Subresult -Name 'V6ProofAudit' -Command $v6Result.command -ExitCode $v6Result.exitCode -RawOutput $v6Result.raw -Status $(if ($v6Pass) { 'PASS' } else { 'FAIL' }) -Notes $v6Notes))

    # 4. V7ChurnRepair
    $v7Script = Join-Path $scriptRoot 'idle-v7-churn-repair-readiness.ps1'
    $v7Result = Invoke-ExternalCommand -FilePath $v7Script -ArgumentList @('-Mode','Validate')
    $v7Summary = Read-JsonFile -Path 'artifacts/obtuse/idle-v7-churn-repair-and-120min-readiness-recheck/validation_summary.json'
    $v7Final = Read-JsonFile -Path 'artifacts/obtuse/idle-v7-churn-repair-and-120min-readiness-recheck/FINAL_REPORT.json'
    $v7Pass = $false
    $v7Notes = ''
    if ($v7Result.raw -match 'VALIDATE_PASS') {
        $v7Pass = $true
        $v7Notes = 'Console output reported VALIDATE_PASS.'
    } elseif ($v7Summary -and $v7Summary.pass -eq $true) {
        $v7Pass = $true
        $v7Notes = 'validation_summary.json indicates pass.'
    } elseif ($v7Final -and $v7Final.status -like '*PASS*') {
        $v7Pass = $true
        $v7Notes = 'FINAL_REPORT.json status indicates pass.'
    } else {
        $v7Notes = 'No VALIDATE_PASS signal in console, validation_summary, or FINAL_REPORT.'
    }
    [void]$subresults.Add((New-Subresult -Name 'V7ChurnRepair' -Command $v7Result.command -ExitCode $v7Result.exitCode -RawOutput $v7Result.raw -Status $(if ($v7Pass) { 'PASS' } else { 'FAIL' }) -Notes $v7Notes))

    # 5. Next006Regression
    $next006Present = $false
    $next006Sha = $null
    if ($statusPass) {
        $next006Present = [bool](Get-NestedPropertyValue -Object $statusParsed -Path 'queueReplenishmentStatus.next006FalsePositiveRegressionEvidencePresent')
        $next006Sha = Get-NestedPropertyValue -Object $statusParsed -Path 'queueReplenishmentStatus.next006ProofBundleSha256'
    }
    $next006Pass = ($next006Present -eq $true -and -not [string]::IsNullOrWhiteSpace($next006Sha))
    $next006Notes = "next006FalsePositiveRegressionEvidencePresent=$next006Present; next006ProofBundleSha256=$(if ($next006Sha) { 'present' } else { 'missing' })"
    [void]$subresults.Add((New-Subresult -Name 'Next006Regression' -Command 'status-idle-ops.ps1 (field extraction)' -ExitCode 0 -RawOutput $next006Notes -Status $(if ($next006Pass) { 'PASS' } else { 'FAIL' }) -Notes $next006Notes))

    # 6. Next007QueueSemantics
    $queueHealthState = $null
    $safeExecutableTaskCount = $null
    if ($statusPass) {
        $queueHealthState = [string](Get-NestedPropertyValue -Object $statusParsed -Path 'queueReplenishmentStatus.queueHealthState')
        $safeExecutableTaskCount = Get-NestedPropertyValue -Object $statusParsed -Path 'queueReplenishmentStatus.safeExecutableTaskCount'
    }
    $queueHealthOk = ($queueHealthState -ne 'QUEUE_MALFORMED' -and $queueHealthState -ne 'QUEUE_UNREADABLE')
    $safeCountOk = $false
    if ($safeExecutableTaskCount -is [int] -or $safeExecutableTaskCount -is [double] -or $safeExecutableTaskCount -is [long]) {
        $safeCountOk = ([int]$safeExecutableTaskCount -ge 0)
    } elseif ($null -ne $safeExecutableTaskCount) {
        $parsedCount = 0
        if ([int]::TryParse([string]$safeExecutableTaskCount, [ref]$parsedCount)) {
            $safeCountOk = ($parsedCount -ge 0)
        }
    }
    $next007Pass = ($queueHealthOk -and $safeCountOk)
    $next007Notes = "queueHealthState=$queueHealthState; safeExecutableTaskCount=$safeExecutableTaskCount"
    [void]$subresults.Add((New-Subresult -Name 'Next007QueueSemantics' -Command 'status-idle-ops.ps1 (field extraction)' -ExitCode 0 -RawOutput $next007Notes -Status $(if ($next007Pass) { 'PASS' } else { 'FAIL' }) -Notes $next007Notes))

    # 7. DunceProofRegression
    $dunceProofScript = Join-Path $scriptRoot 'blunder-dunce-proof-regression-pack.ps1'
    $dunceProofResult = Invoke-ExternalCommand -FilePath $dunceProofScript -ArgumentList @('-Mode','RunRegression')
    $dunceProofPass = $false
    $dunceProofNotes = ''
    if ($dunceProofResult.exitCode -eq 0 -and $dunceProofResult.raw -match 'closure:\s*DUNCE_PROOF_REGRESSION_PACK_PHASE_CLOSED') {
        $dunceProofPass = $true
        $dunceProofNotes = 'RunRegression exited 0 with phase-closed terminal state.'
    } elseif ($dunceProofResult.exitCode -eq 0 -and $dunceProofResult.raw -match 'REGRESSION result:\s*PASS') {
        $dunceProofPass = $true
        $dunceProofNotes = 'RunRegression exited 0 and reported PASS.'
    } else {
        $dunceProofNotes = 'RunRegression did not report expected PASS/phase-closed signal.'
    }
    [void]$subresults.Add((New-Subresult -Name 'DunceProofRegression' -Command $dunceProofResult.command -ExitCode $dunceProofResult.exitCode -RawOutput $dunceProofResult.raw -Status $(if ($dunceProofPass) { 'PASS' } else { 'FAIL' }) -Notes $dunceProofNotes))

    # 8. DunceSubstrateValidation
    $dunceSubstrateScript = Join-Path $scriptRoot 'blunder-dunce-regression-substrate-idle-cycle.ps1'
    $dunceSubstrateResult = Invoke-ExternalCommand -FilePath $dunceSubstrateScript -ArgumentList @('-Mode','Validate')
    $dunceSubstratePass = ($dunceSubstrateResult.exitCode -eq 0 -and $dunceSubstrateResult.raw -match 'VALIDATE_PASS')
    $dunceSubstrateNotes = if ($dunceSubstratePass) { 'Substrate validation reported VALIDATE_PASS.' } else { 'Substrate validation did not report VALIDATE_PASS.' }
    [void]$subresults.Add((New-Subresult -Name 'DunceSubstrateValidation' -Command $dunceSubstrateResult.command -ExitCode $dunceSubstrateResult.exitCode -RawOutput $dunceSubstrateResult.raw -Status $(if ($dunceSubstratePass) { 'PASS' } else { 'FAIL' }) -Notes $dunceSubstrateNotes))

    # 9. CrossMachineVerification
    $crossScript = Join-Path $scriptRoot 'blunder-dunce-cross-machine-proof-regression-verify.ps1'
    $crossResult = Invoke-ExternalCommand -FilePath $crossScript -ArgumentList @('-Mode','VerifyAll')
    $crossMatrix = Read-JsonFile -Path 'artifacts/obtuse/blunder-dunce-cross-machine-proof-regression-verification-v1/machine_verification_matrix.json'
    $crossFinal = Read-JsonFile -Path 'artifacts/obtuse/blunder-dunce-cross-machine-proof-regression-verification-v1/FINAL_REPORT.json'
    $gamingpc3Result = $null
    $matrixResult = $null
    if ($crossMatrix) {
        $matrixResult = [string](Get-NestedPropertyValue -Object $crossMatrix -Path 'result')
        if ($crossMatrix.machines -and $crossMatrix.machines -is [array]) {
            foreach ($machine in $crossMatrix.machines) {
                if ((Get-PropertyValue -Object $machine -Name 'machine') -eq 'GAMINGPC3') {
                    $gamingpc3Result = [string](Get-PropertyValue -Object $machine -Name 'result')
                }
            }
        }
    }
    if ([string]::IsNullOrWhiteSpace($gamingpc3Result) -and $crossFinal) {
        $gamingpc3Result = [string](Get-NestedPropertyValue -Object $crossFinal -Path 'gamingpc3VerificationResult')
    }

    $crossPass = $false
    $crossStatus = 'FAIL'
    $crossNotes = ''
    if ($crossResult.exitCode -eq 0 -and $matrixResult -eq 'PASS') {
        $crossPass = $true
        $crossStatus = 'PASS'
        $crossNotes = 'Cross-machine verification matrix result is PASS.'
    } elseif ($matrixResult -eq 'PASS_WITH_NOTES' -and $gamingpc3Result -eq 'BLOCKED') {
        $crossPass = $true
        $crossStatus = 'PASS_WITH_NOTES'
        $crossNotes = 'Cross-machine verification returned PASS_WITH_NOTES because GAMINGPC3 is BLOCKED.'
    } else {
        $crossNotes = "Cross-machine verification did not pass. matrixResult=$matrixResult gamingpc3Result=$gamingpc3Result"
    }
    [void]$subresults.Add((New-Subresult -Name 'CrossMachineVerification' -Command $crossResult.command -ExitCode $crossResult.exitCode -RawOutput $crossResult.raw -Status $crossStatus -Notes $crossNotes))

    # 10. FlashDefaultModel
    $flashScript = Join-Path $scriptRoot 'idle-self-coding-accelerator.ps1'
    $flashResult = Invoke-ExternalCommand -FilePath $flashScript -ArgumentList @('-Mode','Validate','-Model','deepseek-v4-flash')
    $flashPass = $false
    $flashNotes = ''
    $flashFinal = Read-JsonFile -Path 'artifacts/obtuse/blunder-flash-full-autonomy-compounding-bundle-v1/self-coding/FINAL_REPORT.json'
    $resolvedModel = $null
    if ($flashFinal) {
        $resolvedModel = [string](Get-NestedPropertyValue -Object $flashFinal -Path 'openRouterModelResolved')
    }
    if ([string]::IsNullOrWhiteSpace($resolvedModel) -and $flashResult.parsed) {
        $resolvedModel = [string](Get-NestedPropertyValue -Object $flashResult.parsed -Path 'openRouterModelResolved')
    }
    if ($resolvedModel -ieq 'deepseek/deepseek-v4-flash') {
        $flashPass = $true
        $flashNotes = "Resolved model is $resolvedModel."
    } else {
        $flashNotes = "Resolved model is '$resolvedModel'; expected 'deepseek/deepseek-v4-flash'."
    }
    [void]$subresults.Add((New-Subresult -Name 'FlashDefaultModel' -Command $flashResult.command -ExitCode $flashResult.exitCode -RawOutput $flashResult.raw -Status $(if ($flashPass) { 'PASS' } else { 'FAIL' }) -Notes $flashNotes))

    # 11. SecretScan
    $secretScan = Test-SecretFindings -Paths @($artifactRoot, $PSCommandPath)
    $secretPass = ($secretScan.status -eq 'PASS')
    $secretNotes = "scannedFiles=$($secretScan.scannedFiles); findings=$($secretScan.findings)"
    [void]$subresults.Add((New-Subresult -Name 'SecretScan' -Command 'Test-SecretFindings' -ExitCode 0 -RawOutput ($secretScan | ConvertTo-Json -Depth 10) -Status $(if ($secretPass) { 'PASS' } else { 'FAIL' }) -Notes $secretNotes))

    # Overall result
    $passStatuses = @('PASS')
    $overall = 'PASS'
    $nonPass = @($subresults | Where-Object { $_.status -notin $passStatuses })
    $crossMachineSubresult = $subresults | Where-Object { $_.name -eq 'CrossMachineVerification' } | Select-Object -First 1
    if ($nonPass.Count -eq 0) {
        $overall = 'PASS'
    } elseif ($nonPass.Count -eq 1 -and $crossMachineSubresult -and $crossMachineSubresult.status -eq 'PASS_WITH_NOTES') {
        $allOthersPass = $true
        foreach ($sr in $subresults) {
            if ($sr.name -ne 'CrossMachineVerification' -and $sr.status -ne 'PASS') {
                $allOthersPass = $false
                break
            }
        }
        if ($allOthersPass) {
            $overall = 'PASS_WITH_NOTES'
        } else {
            $overall = 'FAIL'
        }
    } else {
        $overall = 'FAIL'
    }

    $integrationRecord = [ordered]@{
        timestamp = $timestamp
        classification = $classification
        mode = 'Run'
        overallResult = $overall
        subresults = @($subresults)
        secretScan = $secretScan
        statusSnapshot = if ($statusPass) { $statusParsed } else { $null }
    }
    $summaryRecord = [ordered]@{
        timestamp = $timestamp
        classification = $classification
        overallResult = $overall
        subresults = @($subresults | ForEach-Object {
            [ordered]@{
                name = $_.name
                status = $_.status
                exitCode = $_.exitCode
                notes = $_.notes
            }
        })
    }

    Write-JsonFile -Value $integrationRecord -Path (Join-Path $artifactRoot 'standard_idle_validation_integration.json')
    Write-JsonFile -Value $summaryRecord -Path (Join-Path $artifactRoot 'standard_idle_validation_summary.json')

    Write-Host "$classification | overall=$overall" -ForegroundColor $(if ($overall -in @('PASS','PASS_WITH_NOTES')) { 'Green' } else { 'Red' })
    foreach ($sr in $subresults) {
        Write-Host "  $($sr.name): $($sr.status)"
    }

    if ($overall -in @('PASS','PASS_WITH_NOTES')) {
        exit 0
    } else {
        exit 1
    }
}

Push-Location $repoRoot
try {
    if ($Mode -eq 'Validate') {
        Invoke-ValidateMode
    } elseif ($Mode -eq 'Run') {
        Invoke-RunMode
    } elseif ($Mode -eq 'ReportOnly') {
        Invoke-ReportOnlyMode
    } else {
        throw "Unsupported mode: $Mode"
    }
} finally {
    Pop-Location
}
