#requires -Version 5.1
param(
    [string]$Mode = 'Validate',
    [string]$OperatorApproved = 'false',
    [string]$Model = '',
    [string]$ArtifactRoot = 'artifacts/obtuse/blunder-flash-full-autonomy-compounding-bundle-v1/self-coding',
    [int]$MaxProviderCalls = 6,
    [int]$TimeoutSeconds = 90
)

$ErrorActionPreference = 'Stop'
$validModes = @('Validate','SmokeCall','GenerateImprovement','ReviewPatch','SummarizeFailure')
if ($Mode -notin $validModes) { throw "Unsupported mode: $Mode" }
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$endpoint = 'https://openrouter.ai/api/v1/chat/completions'
. (Join-Path $PSScriptRoot 'blunder-compound-evolution-common.ps1')

function Convert-ToBoolean {
    param([object]$Value)
    if ($Value -is [bool]) { return [bool]$Value }
    if ($Value -is [int]) { return ([int]$Value -ne 0) }
    return (([string]$Value).Trim().ToLowerInvariant() -in @('true','1','yes','y','approved'))
}

$OperatorApproved = (Convert-ToBoolean -Value $OperatorApproved) -or (Convert-ToBoolean -Value $env:OBTUSE_OPENROUTER_OPERATOR_APPROVED)

function Resolve-RepoPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $repoRoot $Path
}

function Write-JsonFile {
    param([object]$Value, [string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    $dir = Split-Path $resolved -Parent
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $json = $Value | ConvertTo-Json -Depth 40
    [System.IO.File]::WriteAllText($resolved, ($json -replace "`r?`n", "`r`n"), [System.Text.UTF8Encoding]::new($false))
}

function Add-JsonLine {
    param([string]$Path, [object]$Record)
    $resolved = Resolve-RepoPath -Path $Path
    $dir = Split-Path $resolved -Parent
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $line = ($Record | ConvertTo-Json -Depth 30 -Compress) + [Environment]::NewLine
    [System.IO.File]::AppendAllText($resolved, $line, [System.Text.UTF8Encoding]::new($false))
}

function Read-JsonFile {
    param([string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    if (-not (Test-Path -LiteralPath $resolved)) { return $null }
    try { return Get-Content -Raw -LiteralPath $resolved | ConvertFrom-Json -ErrorAction Stop } catch { return $null }
}

function Test-ContainsSecret {
    param([string]$Text)
    if ([string]::IsNullOrEmpty($Text)) { return $false }
    return ($Text -match 'sk-or-[A-Za-z0-9_-]{20,}|sk-proj-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9_-]{20,}')
}

function Get-OpenRouterKey {
    $key = $env:OPENROUTER_API_KEY
    if (-not $key) { $key = [Environment]::GetEnvironmentVariable('OPENROUTER_API_KEY', 'User') }
    if (-not $key) { $key = [Environment]::GetEnvironmentVariable('OPENROUTER_API_KEY', 'Machine') }
    return $key
}

function Resolve-ConfiguredModel {
    if (-not [string]::IsNullOrWhiteSpace($Model)) {
        if ($Model -ieq 'deepseek/deepseek-v4-flash') { return 'deepseek-v4-flash' }
        return $Model
    }
    $fromSpecificEnv = [Environment]::GetEnvironmentVariable('OPENROUTER_DEEPSEEK_V4_FLASH_MODEL', 'Process')
    if (-not $fromSpecificEnv) { $fromSpecificEnv = [Environment]::GetEnvironmentVariable('OPENROUTER_DEEPSEEK_V4_FLASH_MODEL', 'User') }
    if (-not [string]::IsNullOrWhiteSpace($fromSpecificEnv)) { return $fromSpecificEnv }
    $fromProEnv = [Environment]::GetEnvironmentVariable('OPENROUTER_DEEPSEEK_V4_PRO_MODEL', 'Process')
    if (-not $fromProEnv) { $fromProEnv = [Environment]::GetEnvironmentVariable('OPENROUTER_DEEPSEEK_V4_PRO_MODEL', 'User') }
    if (-not [string]::IsNullOrWhiteSpace($fromProEnv)) { return $fromProEnv }
    $fromEnv = [Environment]::GetEnvironmentVariable('OPENROUTER_MODEL', 'Process')
    if (-not $fromEnv) { $fromEnv = [Environment]::GetEnvironmentVariable('OPENROUTER_MODEL', 'User') }
    if (-not [string]::IsNullOrWhiteSpace($fromEnv)) { return $fromEnv }
    $config = Read-JsonFile -Path (Join-Path $ArtifactRoot 'openrouter_provider_lane_config.json')
    if ($config -and -not [string]::IsNullOrWhiteSpace([string]$config.modelConfigured)) {
        $configured = [string]$config.modelConfigured
        # Migrate any stale Pro default to Flash; explicit operator Pro overrides are handled via -Model.
        if ($configured -ieq 'deepseek-v4-pro') { return 'deepseek-v4-flash' }
        if ($configured -ieq 'deepseek/deepseek-v4-flash') { return 'deepseek-v4-flash' }
        return $configured
    }
    return 'deepseek-v4-flash'
}

function Resolve-OpenRouterModelId {
    param([string]$Alias)
    switch ($Alias.ToLowerInvariant()) {
        'deepseek-v4-flash' { return 'deepseek/deepseek-v4-flash' }
        'deepseek-v4-pro' { return 'deepseek-v4-pro' }
        default { return $Alias }
    }
}

function Get-CallLedgerEntries {
    $path = Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'openrouter_call_ledger.jsonl')
    if (-not (Test-Path -LiteralPath $path)) { return @() }
    $rows = @()
    foreach ($line in Get-Content -LiteralPath $path) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        try { $rows += ($line | ConvertFrom-Json -ErrorAction Stop) } catch {}
    }
    return $rows
}

function Get-CallStats {
    $rows = @(Get-CallLedgerEntries)
    return [ordered]@{
        attempted = @($rows | Where-Object { $_.callAttempted -eq $true }).Count
        succeeded = @($rows | Where-Object { $_.success -eq $true }).Count
        failed = @($rows | Where-Object { $_.callAttempted -eq $true -and $_.success -ne $true }).Count
    }
}

function Write-ProviderConfig {
    param([string]$ConfiguredModel, [string]$LaneState, [bool]$KeyPresent)
    $stats = Get-CallStats
    $existing = Read-JsonFile -Path (Join-Path $ArtifactRoot 'openrouter_provider_lane_config.json')
    $existingApproved = ($existing -and $existing.operatorApproved -eq $true)
    $existingEnabled = ($existing -and $existing.openRouterIdleEnabled -eq $true)
    $enableLane = ($LaneState -in @('READY','ACTIVE'))
    if ($LaneState -eq 'BLOCKED' -and $existingApproved -and $existingEnabled -and $KeyPresent) {
        $enableLane = $true
    }
    $modelId = Resolve-OpenRouterModelId -Alias $ConfiguredModel
    $config = [ordered]@{
        classification = 'OBTUSE_BLUNDER_IDLE_SWITCH_TO_DEEPSEEK_V4_FLASH_V1'
        timestamp = (Get-Date -Format 'o')
        provider = 'OpenRouter'
        approvedInitialModel = 'DeepSeek V4 Flash'
        modelConfigured = $ConfiguredModel
        modelUsed = $modelId
        openRouterModelAlias = $ConfiguredModel
        openRouterModelResolved = $modelId
        openRouterKeyPresent = $KeyPresent
        openRouterIdleEnabled = $enableLane
        modelNeedsOperatorReview = ($LaneState -eq 'MODEL_ID_NEEDS_OPERATOR_REVIEW')
        operatorApproved = ($OperatorApproved -or $existingApproved)
        providerBudgetGateEnabled = $false
        providerBudgetGateStatus = 'DISABLED_BY_OPERATOR_CHEAP_MODEL_POLICY'
        providerCallBudget = $MaxProviderCalls
        providerCallsAttempted = $stats.attempted
        providerCallsSucceeded = $stats.succeeded
        providerCallsFailed = $stats.failed
        endpoint = $endpoint
        secretsStored = $false
    }
    Write-JsonFile -Value $config -Path (Join-Path $ArtifactRoot 'openrouter_provider_lane_config.json')
    return $config
}

function Get-CompactStateSummary {
    $statusPath = Join-Path $PSScriptRoot 'status-idle-ops.ps1'
    $status = $null
    if (Test-Path -LiteralPath $statusPath) {
        try { $status = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $statusPath | ConvertFrom-Json } catch {}
    }
    $todoPath = Resolve-RepoPath -Path 'artifacts/obtuse/idle-next-014-active-idle-proof-audit-v1/UPDATED_TODO.md'
    $todo = if (Test-Path -LiteralPath $todoPath) { (Get-Content -Raw -LiteralPath $todoPath) } else { '' }
    if ($todo.Length -gt 600) { $todo = $todo.Substring(0, 600) }
    return [ordered]@{
        timestamp = (Get-Date -Format 'o')
        idle = [ordered]@{
            active = $(if ($status) { $status.active } else { $null })
            status = $(if ($status) { $status.status } else { $null })
            activePidValid = $(if ($status) { $status.pidValid } else { $null })
            heartbeatAgeSeconds = $(if ($status) { $status.heartbeatAgeSeconds } else { $null })
            orphanProcess = $(if ($status) { $status.orphanProcess } else { $null })
            queueState = $(if ($status -and $status.queueReplenishmentStatus) { $status.queueReplenishmentStatus.queueHealthState } else { $null })
            safeExecutableTaskCount = $(if ($status -and $status.queueReplenishmentStatus) { $status.queueReplenishmentStatus.safeExecutableTaskCount } else { $null })
            cooldownRemainingSeconds = $(if ($status -and $status.queueReplenishmentStatus) { $status.queueReplenishmentStatus.cooldownRemainingSeconds } else { $null })
            finalRecommendedOperatorAction = $(if ($status) { $status.finalRecommendedOperatorAction } else { $null })
        }
        todo = $todo
        allowedTargetFiles = @(
            'scripts/obtuse/*.ps1',
            'artifacts/obtuse/blunder-flash-full-autonomy-compounding-bundle-v1/*'
        )
        forbiddenScope = @('WANEFALL','ShowMeAI source','mobile/phone/ADB','benchmark launch','provider spend escalation','secrets','destructive cleanup','production deployment')
    }
}

function Save-RawResponseDebug {
    param([string]$Text)
    try {
        $debugPath = Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'model_raw_response_debug.json')
        $redacted = $Text
        if (Test-ContainsSecret -Text $redacted) { $redacted = '[redacted-secret-bearing-response]' }
        [ordered]@{
            timestamp = (Get-Date -Format 'o')
            length = $Text.Length
            redactedPreview = $(if ($redacted.Length -gt 2000) { $redacted.Substring(0, 2000) } else { $redacted })
        } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $debugPath -Encoding UTF8
    } catch {}
}

function ConvertFrom-ModelJson {
    param([string]$Text)
    if ([string]::IsNullOrWhiteSpace($Text)) { throw 'Model returned empty content.' }
    $trimmed = $Text.Trim()
    # Strip markdown code fences and any surrounding explanation text
    $trimmed = $trimmed -replace '^\s*```(?:json)?\s*', ''
    $trimmed = $trimmed -replace '\s*```\s*$', ''
    $trimmed = $trimmed.Trim()
    # Find the outermost JSON object
    $first = $trimmed.IndexOf('{')
    $last = $trimmed.LastIndexOf('}')
    if ($first -lt 0 -or $last -lt $first) {
        Save-RawResponseDebug -Text $Text
        throw 'Model response did not contain a JSON object.'
    }
    $json = $trimmed.Substring($first, $last - $first + 1)
    try {
        return $json | ConvertFrom-Json -ErrorAction Stop
    } catch {
        Save-RawResponseDebug -Text $Text
        throw "Model response JSON parse failed: $($_.Exception.Message)"
    }
}

function Get-ErrorBody {
    param([object]$Exception)
    try {
        if ($Exception.Response) {
            $reader = New-Object System.IO.StreamReader($Exception.Response.GetResponseStream())
            return $reader.ReadToEnd()
        }
    } catch {}
    return $Exception.Message
}

function Get-ErrorClass {
    param([int]$StatusCode, [string]$Body)
    $text = [string]$Body
    if ($StatusCode -eq 400 -and $text -match '(?i)model|endpoint|not found|invalid') { return 'MODEL_ID_NEEDS_OPERATOR_REVIEW' }
    if ($StatusCode -eq 401 -or $StatusCode -eq 403) { return 'OPENROUTER_AUTH_FAILED' }
    if ($StatusCode -eq 429) { return 'OPENROUTER_RATE_LIMITED' }
    if ($StatusCode -ge 500) { return 'OPENROUTER_SERVER_ERROR' }
    return 'OPENROUTER_CALL_FAILED'
}

function Invoke-OpenRouterHttpPost {
    param([string]$Key, [string]$BodyJson)
    $request = [System.Net.HttpWebRequest][System.Net.WebRequest]::Create($endpoint)
    $request.Method = 'POST'
    $request.ContentType = 'application/json'
    $request.Timeout = [math]::Max(15, $TimeoutSeconds) * 1000
    $request.ReadWriteTimeout = [math]::Max(15, $TimeoutSeconds) * 1000
    $request.Headers.Add('Authorization', "Bearer $Key")
    $request.Headers.Add('HTTP-Referer', 'http://localhost/obtuse')
    $request.Headers.Add('X-Title', 'Obtuse Blunder Idle Self-Coding')
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($BodyJson)
    $request.ContentLength = $bytes.Length
    $requestStream = $request.GetRequestStream()
    try {
        $requestStream.Write($bytes, 0, $bytes.Length)
    } finally {
        $requestStream.Dispose()
    }
    $response = $request.GetResponse()
    try {
        $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
        $content = $reader.ReadToEnd()
        return [ordered]@{
            statusCode = [int]([System.Net.HttpWebResponse]$response).StatusCode
            content = $content
        }
    } finally {
        $response.Dispose()
    }
}

function Invoke-OpenRouterChat {
    param(
        [string]$CallMode,
        [string]$ConfiguredModel,
        [object[]]$Messages,
        [int]$MaxTokens
    )
    $modelId = Resolve-OpenRouterModelId -Alias $ConfiguredModel
    $key = Get-OpenRouterKey
    $keyPresent = -not [string]::IsNullOrWhiteSpace($key)
    $stats = Get-CallStats
    if (-not $OperatorApproved) {
        return [ordered]@{ result = 'OPERATOR_APPROVAL_REQUIRED'; success = $false; callAttempted = $false; model = $ConfiguredModel; modelResolved = $modelId; keyPresent = $keyPresent }
    }
    if (-not $keyPresent) {
        return [ordered]@{ result = 'OPENROUTER_KEY_MISSING'; success = $false; callAttempted = $false; model = $ConfiguredModel; modelResolved = $modelId; keyPresent = $false }
    }
    # Monetary/provider budget gate removed per operator cheap-model policy; telemetry still logged.

    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $body = [ordered]@{
        model = $modelId
        messages = $Messages
        temperature = 0.2
        max_tokens = $MaxTokens
    }
    $bodyJson = $body | ConvertTo-Json -Depth 20
    $promptChars = 0
    foreach ($message in $Messages) {
        $promptChars += ([string]$message.content).Length
    }
    $ledgerBase = [ordered]@{
        timestamp = (Get-Date -Format 'o')
        mode = $CallMode
        provider = 'OpenRouter'
        model = $ConfiguredModel
        modelResolved = $modelId
        endpoint = $endpoint
        callAttempted = $true
        requestMessageCount = $Messages.Count
        requestPromptChars = $promptChars
        requestBodyChars = $bodyJson.Length
        secretsSent = $false
    }
    if (Test-ContainsSecret -Text $bodyJson) {
        $record = [ordered]@{} + $ledgerBase
        $record.success = $false
        $record.result = 'BLOCKED_SECRET_IN_REQUEST'
        Add-JsonLine -Path (Join-Path $ArtifactRoot 'openrouter_call_ledger.jsonl') -Record $record
        return $record
    }

    try {
        $response = Invoke-OpenRouterHttpPost -Key $key -BodyJson $bodyJson
        $text = [string]$response.content
        $parsed = $text | ConvertFrom-Json -ErrorAction Stop
        $content = [string]$parsed.choices[0].message.content
        $record = [ordered]@{} + $ledgerBase
        $record.success = $true
        $record.result = 'PASS'
        $record.statusCode = [int]$response.statusCode
        $record.responseChars = $text.Length
        $record.contentChars = $content.Length
        $record.finishReason = $parsed.choices[0].finish_reason
        $record.usage = $parsed.usage
        Add-JsonLine -Path (Join-Path $ArtifactRoot 'openrouter_call_ledger.jsonl') -Record $record
        return [ordered]@{
            result = 'PASS'
            success = $true
            callAttempted = $true
            statusCode = [int]$response.statusCode
            model = $ConfiguredModel
            modelResolved = $modelId
            content = $content
            parsed = $parsed
            ledger = $record
        }
    }
    catch {
        $bodyText = Get-ErrorBody -Exception $_.Exception
        if (Test-ContainsSecret -Text $bodyText) { $bodyText = '[redacted-secret-bearing-error-body]' }
        $statusCode = 0
        try { $statusCode = [int]$_.Exception.Response.StatusCode } catch {}
        $class = Get-ErrorClass -StatusCode $statusCode -Body $bodyText
        $record = [ordered]@{} + $ledgerBase
        $record.success = $false
        $record.result = $class
        $record.statusCode = $statusCode
        $record.errorSummary = if ($bodyText.Length -gt 800) { $bodyText.Substring(0, 800) } else { $bodyText }
        Add-JsonLine -Path (Join-Path $ArtifactRoot 'openrouter_call_ledger.jsonl') -Record $record
        return [ordered]@{
            result = $class
            success = $false
            callAttempted = $true
            statusCode = $statusCode
            model = $ConfiguredModel
            modelResolved = $modelId
            errorSummary = $record.errorSummary
            ledger = $record
        }
    }
}

function Invoke-SmokeCall {
    param([string]$ConfiguredModel)
    $messages = @(
        @{ role = 'system'; content = 'You are a bounded local maintenance assistant. Return only compact JSON. Do not request secrets.' },
        @{ role = 'user'; content = 'Return exactly {"ok":true,"lane":"openrouter-idle-self-coding","risk":"LOW"} and no extra text.' }
    )
    $result = Invoke-OpenRouterChat -CallMode 'SmokeCall' -ConfiguredModel $ConfiguredModel -Messages $messages -MaxTokens 80
    $summary = [ordered]@{
        classification = 'OBTUSE_BLUNDER_IDLE_SWITCH_TO_DEEPSEEK_V4_FLASH_V1'
        timestamp = (Get-Date -Format 'o')
        mode = 'SmokeCall'
        model = $ConfiguredModel
        modelResolved = $result.modelResolved
        provider = 'OpenRouter'
        providerCallAttempted = $result.callAttempted
        providerCallSucceeded = $result.success
        result = $result.result
        statusCode = $result.statusCode
        responseSummary = $(if ($result.success) { 'OpenRouter returned a completion.' } else { $result.errorSummary })
        secretsStored = $false
    }
    Write-JsonFile -Value $summary -Path (Join-Path $ArtifactRoot 'openrouter_smoke_call_summary.json')
    $laneState = if ($result.result -eq 'MODEL_ID_NEEDS_OPERATOR_REVIEW') { 'MODEL_ID_NEEDS_OPERATOR_REVIEW' } elseif ($result.success) { 'READY' } else { 'BLOCKED' }
    Write-ProviderConfig -ConfiguredModel $ConfiguredModel -LaneState $laneState -KeyPresent ([bool](Get-OpenRouterKey)) | Out-Null
    return $summary
}

function Read-JsonLinesFile {
    param([string]$Path)
    $resolved = Resolve-RepoPath -Path $Path
    $rows = @()
    if (-not (Test-Path -LiteralPath $resolved)) { return $rows }
    foreach ($line in Get-Content -LiteralPath $resolved) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        try { $rows += ($line | ConvertFrom-Json -ErrorAction Stop) } catch {}
    }
    return $rows
}

function New-SafeSlug {
    param([string]$Text)
    $slug = ([string]$Text).ToLowerInvariant() -replace '[^a-z0-9]+', '-'
    $slug = $slug.Trim('-')
    if ([string]::IsNullOrWhiteSpace($slug)) { return 'candidate' }
    if ($slug.Length -gt 42) { return $slug.Substring(0, 42).Trim('-') }
    return $slug
}

function Test-PatchTypeAlreadyPromoted {
    param([object[]]$Rows, [string]$PatchType)
    return (@($Rows | Where-Object {
        $_.patchType -eq $PatchType -and $_.patchApplied -eq $true -and [int]$_.sourceLinesChanged -gt 0 -and $_.result -in @('PASS','PROMOTED')
    }).Count -gt 0)
}

function Get-InflowCapabilitySeed {
    param([int]$Offset)
    $paths = @(
        (Resolve-RepoPath -Path 'artifacts/blunder/inflow/external_capability_candidates.jsonl'),
        (Resolve-RepoPath -Path 'artifacts/blunder/inflow/capability_candidates.jsonl')
    )
    $rows = @()
    $seenCandidateIds = @{}
    foreach ($path in $paths) {
        foreach ($row in @(Read-JsonLinesFile -Path $path)) {
            $candidateId = if ($row.candidate_id) { [string]$row.candidate_id } elseif ($row.evidence_record_id) { [string]$row.evidence_record_id } else { '' }
            if (-not [string]::IsNullOrWhiteSpace($candidateId)) {
                if ($seenCandidateIds.ContainsKey($candidateId)) { continue }
                $seenCandidateIds[$candidateId] = $true
            }
            $rows += $row
        }
    }
    if ($rows.Count -eq 0) {
        return [ordered]@{
            title = 'Idle compounding proof-yield monitor'
            targetType = 'CompoundingMonitor'
            candidateId = 'fallback-compounding-monitor'
        }
    }
    $selected = $rows[$Offset % $rows.Count]
    return [ordered]@{
        title = $(if ($selected.title) { [string]$selected.title } else { 'Inflow capability replay candidate' })
        targetType = $(if ($selected.target_type) { [string]$selected.target_type } else { 'InflowCapability' })
        candidateId = $(if ($selected.candidate_id) { [string]$selected.candidate_id } elseif ($selected.evidence_record_id) { [string]$selected.evidence_record_id } else { "inflow-$Offset" })
    }
}

function Get-CompoundEvolutionPlan {
    $plannerPath = Join-Path $PSScriptRoot 'blunder-compound-evolution-planner.py'
    return Invoke-BlunderCompoundEvolutionPlan -RepoRoot $repoRoot -ArtifactRoot $ArtifactRoot -PlannerPath $plannerPath
}

function Get-CandidateTemplateFamily {
    param([string]$Title, [string[]]$TargetFiles)
    $signal = ((@($TargetFiles) -join ' ') + ' ' + $Title).ToLowerInvariant()
    if ($signal -match 'rollback|restore|demotion') { return 'rollback' }
    if ($signal -match 'validation|threshold|proof|gate|accelerator|timeout') { return 'validation' }
    if ($signal -match 'inflow|capability|replay|fixture') { return 'inflow' }
    if ($signal -match 'queue|replenish|scheduler') { return 'queue' }
    if ($signal -match 'novelty|diversity|candidate-catalog|compounding-metrics') { return 'novelty' }
    if ($signal -match 'status|health|report|summary|metric|comment|ledger') { return 'observability' }
    return 'general'
}

function New-CandidateTemplate {
    param(
        [string[]]$TargetFiles,
        [string]$Title,
        [string]$Problem,
        [string[]]$PatchPlan,
        [string[]]$ValidationPlan,
        [string[]]$ExpectedProof,
        [string]$ExpectedPatchType
    )
    return [ordered]@{
        family = Get-CandidateTemplateFamily -Title $Title -TargetFiles $TargetFiles
        targetFiles = $TargetFiles
        title = $Title
        problem = $Problem
        patchPlan = $PatchPlan
        validationPlan = $ValidationPlan
        expectedProof = $ExpectedProof
        expectedPatchType = $ExpectedPatchType
        qualityContract = [ordered]@{
            rootCause = $Problem
            evidenceSource = ($ExpectedProof -join '; ')
            validationGate = ($ValidationPlan -join '; ')
            rollbackGate = 'Restore target from rollback snapshot if validation fails.'
            promotionStop = 'Do not promote if parser, target validation, status, safety, or planning-quality thresholds fail.'
        }
    }
}

function Get-RotatingCandidateTemplate {
    $ledger = Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'model_candidate_ledger.jsonl')
    $count = 0
    if (Test-Path -LiteralPath $ledger) { $count = @((Get-Content -LiteralPath $ledger) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }).Count }
    $patchRows = @(Read-JsonLinesFile -Path (Join-Path $ArtifactRoot 'self_coding_patch_ledger.jsonl'))
    $templates = @(
        (New-CandidateTemplate -TargetFiles @('scripts/obtuse/idle-self-coding-ledger-summary.ps1') -Title 'Add self-coding ledger summary helper' -Problem 'Promotions are counted without a compact deduplicated summary of patch types, provider calls, and rollback activity.' -PatchPlan @('Create a read-only PowerShell helper that summarizes self-coding ledgers and duplicate pressure as JSON.') -ValidationPlan @('PowerShell parser check','Run ledger summary helper Validate mode','Run idle status command') -ExpectedProof @('self_coding_patch_ledger.jsonl','promotion_ledger.jsonl','openrouter_call_ledger.jsonl') -ExpectedPatchType 'SELF_CODING_LEDGER_SUMMARY_HELPER'),
        (New-CandidateTemplate -TargetFiles @('scripts/obtuse/idle-self-coding-candidate-catalog.ps1') -Title 'Add self-coding candidate catalog helper' -Problem 'The self-coding lane needs an inspectable catalog of safe local improvement families to avoid repeating the same helper patch.' -PatchPlan @('Create a read-only PowerShell helper that lists approved safe candidate families and forbidden scopes as JSON.') -ValidationPlan @('PowerShell parser check','Run candidate catalog helper Validate mode','Run idle status command') -ExpectedProof @('model_candidate_ledger.jsonl','threshold_decision_ledger.jsonl','promotion_ledger.jsonl') -ExpectedPatchType 'SELF_CODING_CANDIDATE_CATALOG_HELPER'),
        (New-CandidateTemplate -TargetFiles @('scripts/obtuse/idle-self-coding-throttle-report.ps1') -Title 'Add self-coding throttle report helper' -Problem 'The self-coding lane needs a compact report of cooldown, provider call counts, and failure guard state before increasing compounding.' -PatchPlan @('Create a read-only PowerShell helper that reports throttle, cooldown, and provider failure telemetry as JSON.') -ValidationPlan @('PowerShell parser check','Run throttle report helper Validate mode','Run idle status command') -ExpectedProof @('openrouter_call_ledger.jsonl','self_coding_run_summary.json','compound_loop_summary.json') -ExpectedPatchType 'SELF_CODING_THROTTLE_REPORT_HELPER'),
        (New-CandidateTemplate -TargetFiles @('scripts/obtuse/idle-openrouter-self-coding-health.ps1') -Title 'Add OpenRouter self-coding health helper' -Problem 'No compact local health helper exists for the OpenRouter self-coding lane.' -PatchPlan @('Create a read-only PowerShell helper that reports lane artifact/config/ledger health as JSON.') -ValidationPlan @('PowerShell parser check','Run helper Validate mode','Run idle status command') -ExpectedProof @('self_coding_patch_ledger.jsonl','threshold_decision_ledger.jsonl','promotion_ledger.jsonl') -ExpectedPatchType 'OPENROUTER_SELF_CODING_HEALTH_HELPER'),
        (New-CandidateTemplate -TargetFiles @('scripts/obtuse/idle-self-coding-compounding-metrics.ps1') -Title 'Add self-coding compounding metrics helper' -Problem 'The lane needs a compact metric for applied-patch yield versus provider calls so repeated low-yield cycles can force new mutation families.' -PatchPlan @('Create a read-only PowerShell helper that reports provider calls, patch yield, promotion yield, and duplicate pressure.') -ValidationPlan @('PowerShell parser check','Run compounding metrics helper Validate mode','Run idle status command') -ExpectedProof @('self_coding_patch_ledger.jsonl','openrouter_call_ledger.jsonl','promotion_ledger.jsonl') -ExpectedPatchType 'GENERATED_SAFE_REPORT_HELPER'),
        (New-CandidateTemplate -TargetFiles @('scripts/obtuse/idle-replenishment-yield-report.ps1') -Title 'Add replenishment yield report helper' -Problem 'Queue replenishment needs visible admit-versus-duplicate yield so history exhaustion can be repaired before idle goes telemetry-only.' -PatchPlan @('Create a read-only PowerShell helper that summarizes replenishment admits, duplicate skips, cooldowns, and pending work.') -ValidationPlan @('PowerShell parser check','Run replenishment yield helper Validate mode','Run idle status command') -ExpectedProof @('idle-replenishment-ledger.jsonl','idle-task-queue.json','idle-cycle-ledger.jsonl') -ExpectedPatchType 'GENERATED_SAFE_REPORT_HELPER'),
        (New-CandidateTemplate -TargetFiles @('scripts/obtuse/idle-inflow-capability-bridge-report.ps1') -Title 'Add inflow capability bridge report helper' -Problem 'Inflow capability candidates are being mined into artifacts but not made visible as fresh idle queue work.' -PatchPlan @('Create a read-only PowerShell helper that counts inflow candidates, replay fixtures, synthetic drills, and active tokens.') -ValidationPlan @('PowerShell parser check','Run inflow capability bridge helper Validate mode','Run idle status command') -ExpectedProof @('capability_candidates.jsonl','replay_fixtures.jsonl','promotion_bridge_ledger.jsonl') -ExpectedPatchType 'GENERATED_SAFE_REPORT_HELPER'),
        (New-CandidateTemplate -TargetFiles @('scripts/obtuse/idle-self-coding-novelty-pressure.ps1') -Title 'Add self-coding novelty pressure helper' -Problem 'The self-coding lane accepts safe candidates even when they repeatedly resolve to already-present helper patches.' -PatchPlan @('Create a read-only helper that reports repeated patch families and novelty pressure so dynamic templates are preferred.') -ValidationPlan @('PowerShell parser check','Run novelty pressure helper Validate mode','Run idle status command') -ExpectedProof @('model_candidate_ledger.jsonl','self_coding_patch_ledger.jsonl','promotion_ledger.jsonl') -ExpectedPatchType 'GENERATED_SAFE_REPORT_HELPER')
    )
    try {
        $plan = Get-CompoundEvolutionPlan
    }
    catch {
        return [ordered]@{
            noCandidate = $true
            reason = 'COMPOUND_EVOLUTION_PLAN_UNAVAILABLE'
            title = 'Compound evolution plan unavailable'
            problem = $_.Exception.Message
            targetFiles = @()
            expectedPatchType = 'NONE'
        }
    }
    $familyOrder = @($plan.rankedFamilies | ForEach-Object { [string]$_.family })
    foreach ($family in $familyOrder) {
        foreach ($template in @($templates | Where-Object { $_.family -eq $family })) {
            $firstTarget = if ($template.targetFiles -and @($template.targetFiles).Count -gt 0) { [string]@($template.targetFiles)[0] } else { '' }
            $targetExists = (-not [string]::IsNullOrWhiteSpace($firstTarget) -and (Test-Path -LiteralPath (Resolve-RepoPath -Path $firstTarget)))
            $available = if ($template.expectedPatchType -eq 'GENERATED_SAFE_REPORT_HELPER') {
                -not $targetExists
            } else {
                -not (Test-PatchTypeAlreadyPromoted -Rows $patchRows -PatchType $template.expectedPatchType)
            }
            if (-not $available) { continue }
            $template['compoundEvolution'] = [ordered]@{
                selectedFamily = [string]$plan.selectedFamily
                assignedFamily = [string]$template.family
                pressureLevel = [double]$plan.pressure.level
                candidateBudgetNext = [int]$plan.pressure.candidateBudgetNext
                championFitness = [double]$plan.championFloor.fitness
                cooledDownFamilies = @($plan.cooledDownFamilies)
                thresholdReductionAuthorized = $false
            }
            return $template
        }
    }

    return [ordered]@{
        noCandidate = $true
        reason = 'SAFE_TEMPLATE_POOL_EXHAUSTED'
        title = 'No safe self-coding candidate'
        problem = 'All bounded templates are exhausted after adaptive family ranking; generated telemetry helper creation is disabled because it does not improve capability.'
        targetFiles = @()
        expectedPatchType = 'NONE'
        compoundEvolutionPlanPath = (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'compound_evolution_plan.json'))
        selectedFamily = [string]$plan.selectedFamily
        pressureLevel = [double]$plan.pressure.level
        candidateBudgetNext = [int]$plan.pressure.candidateBudgetNext
    }
}

function Invoke-GenerateImprovement {
    param([string]$ConfiguredModel)
    $state = Get-CompactStateSummary
    $template = Get-RotatingCandidateTemplate
    if ($template.noCandidate -eq $true) {
        $summary = [ordered]@{
            classification = 'OBTUSE_BLUNDER_IDLE_SWITCH_TO_DEEPSEEK_V4_FLASH_V1'
            timestamp = (Get-Date -Format 'o')
            mode = 'GenerateImprovement'
            model = $ConfiguredModel
            modelResolved = (Resolve-OpenRouterModelId -Alias $ConfiguredModel)
            providerCallAttempted = $false
            providerCallSucceeded = $false
            result = 'NO_CAPABILITY_CANDIDATE'
            candidateGenerated = $false
            parseResult = 'NOT_RUN'
            candidatePath = $null
            errorSummary = $template.problem
        }
        Write-JsonFile -Value $summary -Path (Join-Path $ArtifactRoot 'model_generation_summary.json')
        Add-JsonLine -Path (Join-Path $ArtifactRoot 'model_candidate_ledger.jsonl') -Record $summary
        return $summary
    }
    $stateJson = ([ordered]@{
        active = $state.idle.active
        queueState = $state.idle.queueState
        safeExecutableTaskCount = $state.idle.safeExecutableTaskCount
        cooldownRemainingSeconds = $state.idle.cooldownRemainingSeconds
    }) | ConvertTo-Json -Compress
    $targetFilesJson = $template.targetFiles | ConvertTo-Json -Compress
    $compoundEvolutionJson = $template.compoundEvolution | ConvertTo-Json -Depth 10 -Compress
    $prompt = @"
Return JSON only. Generate exactly one LOW-risk Blunder idle self-coding improvement candidate.
The targetFiles array must be exactly $targetFilesJson.

Schema:
{
  "title": "$($template.title)",
  "problem": "$($template.problem)",
  "targetFiles": $targetFilesJson,
  "patchPlan": $(($template.patchPlan | ConvertTo-Json -Compress)),
  "validationPlan": $(($template.validationPlan | ConvertTo-Json -Compress)),
  "riskLevel": "LOW",
  "rollbackPlan": ["Restore target from rollback snapshot if validation fails."],
  "expectedProof": $(($template.expectedProof | ConvertTo-Json -Compress)),
  "expectedPatchType": "$($template.expectedPatchType)",
  "mutationFamily": "$($template.family)",
  "compoundEvolution": $compoundEvolutionJson,
  "qualityContract": $(($template.qualityContract | ConvertTo-Json -Compress))
}

Constraints:
- do not propose benchmark launch work
- do not request or print secrets
- do not touch WANEFALL, ShowMeAI source, mobile/phone/ADB, production deployment, git, or destructive cleanup
- keep the qualityContract fields concrete; do not use generic phrases like improve things, make better, or optimize everything
- the problem must name the observed artifact, ledger, status, queue, proof, inflow, or validation signal that justifies the patch

State:
$stateJson
"@
    $messages = @(
        @{ role = 'system'; content = 'You produce one safe local maintenance candidate as strict JSON. No markdown. No explanation. Return only the JSON object. Do not wrap in code fences.' },
        @{ role = 'user'; content = $prompt }
    )
    $result = Invoke-OpenRouterChat -CallMode 'GenerateImprovement' -ConfiguredModel $ConfiguredModel -Messages $messages -MaxTokens 1200
    $summary = [ordered]@{
        classification = 'OBTUSE_BLUNDER_IDLE_SWITCH_TO_DEEPSEEK_V4_FLASH_V1'
        timestamp = (Get-Date -Format 'o')
        mode = 'GenerateImprovement'
        model = $ConfiguredModel
        modelResolved = $result.modelResolved
        providerCallAttempted = $result.callAttempted
        providerCallSucceeded = $result.success
        result = $result.result
        candidateGenerated = $false
        parseResult = 'NOT_RUN'
        candidatePath = $null
        errorSummary = $(if ($result.success) { $null } else { $result.errorSummary })
    }
    if ($result.success) {
        try {
            $candidate = ConvertFrom-ModelJson -Text $result.content
            # Normalize array fields: model sometimes returns strings instead of arrays
            foreach ($field in @('targetFiles','patchPlan','validationPlan','rollbackPlan','expectedProof')) {
                if ($candidate.PSObject.Properties.Name -contains $field) {
                    $value = $candidate.$field
                    if ($value -is [string]) {
                        $candidate.$field = @($value)
                    } elseif ($value -is [array] -or $value -is [System.Collections.ArrayList]) {
                        $candidate.$field = @($value)
                    } elseif ($null -eq $value) {
                        $candidate.$field = @()
                    } else {
                        $candidate.$field = @($value)
                    }
                }
            }
            # Ensure riskLevel is uppercase
            if ($candidate.PSObject.Properties.Name -contains 'riskLevel') {
                $candidate.riskLevel = [string]$candidate.riskLevel.ToUpperInvariant()
            }
            $candidate | Add-Member -NotePropertyName mutationFamily -NotePropertyValue ([string]$template.family) -Force
            $candidate | Add-Member -NotePropertyName compoundEvolution -NotePropertyValue $template.compoundEvolution -Force
            $candidateRecord = [ordered]@{
                classification = 'OBTUSE_BLUNDER_IDLE_SWITCH_TO_DEEPSEEK_V4_FLASH_V1'
                timestamp = (Get-Date -Format 'o')
                source = 'OpenRouter'
                model = $ConfiguredModel
                modelResolved = (Resolve-OpenRouterModelId -Alias $ConfiguredModel)
                candidate = $candidate
                compoundEvolutionPlanPath = (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'compound_evolution_plan.json'))
                rawContentStored = $false
                secretsStored = $false
            }
            Write-JsonFile -Value $candidateRecord -Path (Join-Path $ArtifactRoot 'model_generated_candidate_task.json')
            Add-JsonLine -Path (Join-Path $ArtifactRoot 'model_candidate_ledger.jsonl') -Record $candidateRecord
            $summary.candidateGenerated = $true
            $summary.parseResult = 'PASS'
            $summary.candidatePath = (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'model_generated_candidate_task.json'))
        } catch {
            $summary.parseResult = 'FAIL'
            $summary.errorSummary = $_.Exception.Message
        }
    }
    Write-JsonFile -Value $summary -Path (Join-Path $ArtifactRoot 'model_generation_summary.json')
    return $summary
}

New-Item -ItemType Directory -Force -Path (Resolve-RepoPath -Path $ArtifactRoot) | Out-Null
foreach ($ledger in @('openrouter_call_ledger.jsonl','model_candidate_ledger.jsonl')) {
    $path = Resolve-RepoPath -Path (Join-Path $ArtifactRoot $ledger)
    if (-not (Test-Path -LiteralPath $path)) { New-Item -ItemType File -Path $path -Force | Out-Null }
}

$configuredModel = Resolve-ConfiguredModel
$keyPresent = [bool](Get-OpenRouterKey)

switch ($Mode) {
    'Validate' {
        $config = Write-ProviderConfig -ConfiguredModel $configuredModel -LaneState $(if ($keyPresent -and $OperatorApproved) { 'READY' } else { 'BLOCKED' }) -KeyPresent $keyPresent
        $compoundPlan = Get-CompoundEvolutionPlan
        [ordered]@{
            classification = 'OBTUSE_BLUNDER_IDLE_SWITCH_TO_DEEPSEEK_V4_FLASH_V1'
            mode = 'Validate'
            result = $(if ($keyPresent) { 'PASS' } else { 'OPENROUTER_KEY_MISSING' })
            provider = 'OpenRouter'
            modelConfigured = $configuredModel
            modelResolved = (Resolve-OpenRouterModelId -Alias $configuredModel)
            openRouterKeyPresent = $keyPresent
            providerBudgetGateEnabled = $false
            providerBudgetGateStatus = 'DISABLED_BY_OPERATOR_CHEAP_MODEL_POLICY'
            providerCallBudget = $MaxProviderCalls
            providerCallsAttempted = (Get-CallStats).attempted
            compoundEvolutionPlanStatus = $compoundPlan.status
            compoundEvolutionSelectedFamily = $compoundPlan.selectedFamily
            compoundEvolutionPressureLevel = $compoundPlan.pressure.level
            compoundEvolutionCandidateBudgetNext = $compoundPlan.pressure.candidateBudgetNext
            compoundEvolutionCooledDownFamilies = @($compoundPlan.cooledDownFamilies)
            compoundEvolutionChampionFitness = $compoundPlan.championFloor.fitness
            compoundEvolutionAdvisoryOnly = $compoundPlan.safety.advisoryOnly
            secretsPrinted = $false
            configPath = (Resolve-RepoPath -Path (Join-Path $ArtifactRoot 'openrouter_provider_lane_config.json'))
        } | ConvertTo-Json -Depth 20
        return
    }
    'SmokeCall' {
        Invoke-SmokeCall -ConfiguredModel $configuredModel | ConvertTo-Json -Depth 20
        return
    }
    'GenerateImprovement' {
        Invoke-GenerateImprovement -ConfiguredModel $configuredModel | ConvertTo-Json -Depth 20
        return
    }
    'ReviewPatch' {
        [ordered]@{ classification = 'OBTUSE_BLUNDER_IDLE_SWITCH_TO_DEEPSEEK_V4_FLASH_V1'; mode = 'ReviewPatch'; result = 'PASS'; providerCallAttempted = $false; note = 'Patch review is handled locally by idle-improvement-thresholds.ps1.' } | ConvertTo-Json -Depth 10
        return
    }
    'SummarizeFailure' {
        [ordered]@{ classification = 'OBTUSE_BLUNDER_IDLE_SWITCH_TO_DEEPSEEK_V4_FLASH_V1'; mode = 'SummarizeFailure'; result = 'PASS'; providerCallAttempted = $false; note = 'Failure summaries are redacted and local by default.' } | ConvertTo-Json -Depth 10
        return
    }
}


# MODEL-TIMEOUT-POLICY-MARKER-v1: HTTP timeout is configured via TimeoutSeconds and capped at 15 seconds minimum to avoid hang.
