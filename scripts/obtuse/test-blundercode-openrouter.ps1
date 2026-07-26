#requires -Version 5.1
<#
.SYNOPSIS
    Validates OpenRouter model availability and minimal chat calls for Blunder Code.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string[]]$Models,

    [Parameter(Mandatory = $false)]
    [int]$TimeoutSeconds,

    [Parameter(Mandatory = $false)]
    [switch]$AllowPaidCall
)

$ErrorActionPreference = 'Stop'

function Resolve-RepoRoot {
    return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
}

function New-RunId {
    return (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + ([guid]::NewGuid().ToString('N').Substring(0, 8))
}

function Write-JsonFile {
    param(
        [string]$Path,
        [object]$Value
    )

    $Value | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Get-OpenRouterKey {
    $key = [Environment]::GetEnvironmentVariable('OPENROUTER_API_KEY', 'Process')
    if (-not $key) { $key = [Environment]::GetEnvironmentVariable('OPENROUTER_API_KEY', 'User') }
    if (-not $key) { $key = [Environment]::GetEnvironmentVariable('OPENROUTER_API_KEY', 'Machine') }
    if (-not $key) { $key = [Environment]::GetEnvironmentVariable('OBTUSE_OPENROUTER_API_KEY', 'Process') }
    if (-not $key) { $key = [Environment]::GetEnvironmentVariable('OBTUSE_OPENROUTER_API_KEY', 'User') }
    if (-not $key) { $key = [Environment]::GetEnvironmentVariable('OBTUSE_OPENROUTER_API_KEY', 'Machine') }
    return $key
}

function Resolve-OpenRouterSlug {
    param([string]$Model)

    $value = $Model.Trim()
    if ($value -ieq 'openrouter/glm-5.2') { return 'z-ai/glm-5.2' }
    if ($value -ieq 'glm-5.2') { return 'z-ai/glm-5.2' }
    if ($value -ieq 'deepseek-v4-flash') { return 'deepseek/deepseek-v4-flash' }
    if ($value -like 'openrouter/*') { return $value.Substring('openrouter/'.Length) }
    return $value
}

function Read-ErrorBody {
    param([object]$ErrorRecord)

    $response = $ErrorRecord.Exception.Response
    if (-not $response) {
        return $ErrorRecord.Exception.Message
    }
    $stream = $response.GetResponseStream()
    if (-not $stream) {
        return $ErrorRecord.Exception.Message
    }
    $reader = New-Object System.IO.StreamReader($stream)
    try {
        return $reader.ReadToEnd()
    }
    finally {
        $reader.Dispose()
    }
}

function Get-DefaultModels {
    param([string]$Repo)

    $path = Join-Path $Repo 'configs\obtuse\blundercode-openrouter-models.json'
    $doc = Get-Content -Raw -LiteralPath $path | ConvertFrom-Json
    return @($doc.models | ForEach-Object { [string]$_.configured })
}

function Get-CatalogMap {
    param(
        [hashtable]$Headers,
        [int]$Timeout
    )

    $uri = 'https://openrouter.ai/api/v1/models'
    $catalog = Invoke-RestMethod -Method Get -Uri $uri -Headers $Headers -TimeoutSec $Timeout
    $map = @{}
    foreach ($model in $catalog.data) {
        if ($model.id) { $map[[string]$model.id] = $true }
        if ($model.canonical_slug) { $map[[string]$model.canonical_slug] = $true }
    }
    return $map
}

function Test-Chat {
    param(
        [string]$Slug,
        [hashtable]$Headers,
        [int]$Timeout
    )

    $body = @{
        model = $Slug
        messages = @(
            @{ role = 'user'; content = 'Reply with OK only.' }
        )
        max_tokens = 2
        temperature = 0
    } | ConvertTo-Json -Depth 8

    try {
        $response = Invoke-RestMethod `
            -Method Post `
            -Uri 'https://openrouter.ai/api/v1/chat/completions' `
            -Headers $Headers `
            -ContentType 'application/json' `
            -Body $body `
            -TimeoutSec $Timeout
        $content = ''
        if ($response.choices -and $response.choices.Count -gt 0) {
            $content = [string]$response.choices[0].message.content
        }
        return [ordered]@{
            result = 'CHAT_OK'
            success = $true
            responsePreview = $content
        }
    }
    catch [System.Net.WebException] {
        return [ordered]@{
            result = 'CHAT_HTTP_ERROR'
            success = $false
            errorType = $_.Exception.GetType().FullName
            errorMessage = $_.Exception.Message
            responseBody = Read-ErrorBody -ErrorRecord $_
        }
    }
    catch [System.Exception] {
        return [ordered]@{
            result = 'CHAT_ERROR'
            success = $false
            errorType = $_.Exception.GetType().FullName
            errorMessage = $_.Exception.Message
        }
    }
}

if ($TimeoutSeconds -lt 1) {
    $TimeoutSeconds = 60
}

$repo = Resolve-RepoRoot
$run = New-RunId
$root = Join-Path $repo 'artifacts\obtuse\blundercode-openrouter-api'
New-Item -ItemType Directory -Path $root -Force | Out-Null
$key = Get-OpenRouterKey

if (-not $key) {
    $record = [ordered]@{
        status = 'OPENROUTER_KEY_MISSING'
        keyPresent = $false
        callAttempted = $false
        runId = $run
        checkedAt = (Get-Date).ToString('o')
    }
    $path = Join-Path $root "$run.json"
    Write-JsonFile -Path $path -Value $record
    $record.artifact = $path
    $record | ConvertTo-Json -Depth 12
    exit 2
}

if (-not $AllowPaidCall) {
    throw 'OpenRouter chat validation requires -AllowPaidCall because it may spend a small amount of provider credit.'
}

$headers = @{
    Authorization = "Bearer $key"
    'HTTP-Referer' = 'https://localhost/blundercode'
    'X-OpenRouter-Title' = 'Blunder Code'
}

$targets = if ($Models -and $Models.Count -gt 0) { @($Models) } else { Get-DefaultModels -Repo $repo }
$catalog = Get-CatalogMap -Headers $headers -Timeout $TimeoutSeconds
$results = @()

foreach ($target in $targets) {
    $slug = Resolve-OpenRouterSlug -Model $target
    $listed = [bool]$catalog[$slug]
    if (-not $listed) {
        $results += [ordered]@{
            configured = $target
            openRouterSlug = $slug
            listed = $false
            chatAttempted = $false
            success = $false
            result = 'MODEL_NOT_LISTED'
        }
        continue
    }

    $chat = Test-Chat -Slug $slug -Headers $headers -Timeout $TimeoutSeconds
    $results += [ordered]@{
        configured = $target
        openRouterSlug = $slug
        listed = $true
        chatAttempted = $true
        success = [bool]$chat.success
        result = [string]$chat.result
        responsePreview = $(if ($chat.responsePreview) { [string]$chat.responsePreview } else { $null })
        errorType = $(if ($chat.errorType) { [string]$chat.errorType } else { $null })
        errorMessage = $(if ($chat.errorMessage) { [string]$chat.errorMessage } else { $null })
        responseBody = $(if ($chat.responseBody) { [string]$chat.responseBody } else { $null })
    }
}

$failed = @($results | Where-Object { -not $_.success })
$record = [ordered]@{
    status = if ($failed.Count -eq 0) { 'OPENROUTER_MODELS_VALIDATED' } else { 'OPENROUTER_MODEL_VALIDATION_FAILED' }
    keyPresent = $true
    secretPrinted = $false
    callAttempted = $true
    runId = $run
    endpoint = 'https://openrouter.ai/api/v1/chat/completions'
    modelCount = $results.Count
    failedCount = $failed.Count
    results = $results
    checkedAt = (Get-Date).ToString('o')
}
$path = Join-Path $root "$run.json"
Write-JsonFile -Path $path -Value $record
$record.artifact = $path
$record | ConvertTo-Json -Depth 12

if ($failed.Count -gt 0) {
    exit 1
}
