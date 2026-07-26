#requires -Version 5.1

function Get-BlunderOperatorAuthorization {
    param([Parameter(Mandatory)] [string]$RepoRoot)
    $path = Join-Path $RepoRoot 'configs\obtuse\blunder-operator-autonomy-authorization.json'
    $disabled = [ordered]@{
        enabled = $false
        authorizationPath = $path
        approvedScopes = @()
        allowReliabilityBudgetOverride = $false
        allowHistoricalV5QuarantineOnlyRetirement = $false
        preserveHardDenials = @()
        requiresSeparateApproval = @()
    }
    if (-not (Test-Path -LiteralPath $path)) { return $disabled }
    try {
        $authorization = Get-Content -Raw -LiteralPath $path | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        throw "Invalid Blunder operator authorization JSON at ${path}: $($_.Exception.Message)"
    }
    if ($authorization.PSObject.Properties.Name -notcontains 'enabled') {
        throw "Invalid Blunder operator authorization JSON at ${path}: missing enabled field."
    }
    $authorization | Add-Member -NotePropertyName authorizationPath -NotePropertyValue $path -Force
    return $authorization
}

function Test-BlunderAuthorizationScope {
    param(
        [Parameter(Mandatory)] [object]$Authorization,
        [Parameter(Mandatory)] [string]$Scope
    )
    if (-not $Authorization -or $Authorization.enabled -ne $true) { return $false }
    return @($Authorization.approvedScopes) -contains $Scope
}
