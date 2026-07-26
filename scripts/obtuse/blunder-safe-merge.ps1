#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Repository,
    [Parameter(Mandatory = $true)]
    [int]$PullRequest,
    [Parameter(Mandatory = $true)]
    [string]$ExpectedHead,
    [Parameter(Mandatory = $true)]
    [string]$Confirmation,
    [switch]$Execute
)

$ErrorActionPreference = 'Stop'
$repo = & gh repo view $Repository --json visibility,isPrivate,nameWithOwner | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Cannot read GitHub repository state.' }
$pr = & gh pr view $PullRequest --repo $Repository --json state,isDraft,mergeable,headRefOid,statusCheckRollup,url | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Cannot read GitHub pull request state.' }

$checks = @($pr.statusCheckRollup)
$failed = @($checks | Where-Object { $_.status -ne 'COMPLETED' -or $_.conclusion -ne 'SUCCESS' })
$expectedConfirmation = "MERGE $PullRequest $ExpectedHead"
$gates = [ordered]@{
    repositoryPrivate = $repo.isPrivate -eq $true -and $repo.visibility -eq 'PRIVATE'
    pullRequestOpen = $pr.state -eq 'OPEN'
    pullRequestReady = $pr.isDraft -eq $false
    headMatches = $pr.headRefOid -eq $ExpectedHead
    checksPresent = $checks.Count -gt 0
    checksPassed = $failed.Count -eq 0
    mergeable = $pr.mergeable -eq 'MERGEABLE'
    exactConfirmation = $Confirmation -ceq $expectedConfirmation
}
$pass = @($gates.Values | Where-Object { $_ -ne $true }).Count -eq 0
$report = [ordered]@{
    schema = 'obtuse.blunder.safe-private-merge.v1'
    status = if ($pass) { 'READY_FOR_OPERATOR_MERGE' } else { 'BLOCKED' }
    repository = $repo.nameWithOwner
    pullRequest = $PullRequest
    url = $pr.url
    expectedHead = $ExpectedHead
    expectedConfirmation = $expectedConfirmation
    gates = $gates
    executed = $false
}
if ($Execute) {
    if (-not $pass) { throw "Safe merge is blocked: $($report | ConvertTo-Json -Depth 6 -Compress)" }
    & gh pr merge $PullRequest --repo $Repository --merge --match-head-commit $ExpectedHead
    if ($LASTEXITCODE -ne 0) { throw 'GitHub merge failed after all local gates passed.' }
    $merged = & gh pr view $PullRequest --repo $Repository --json state,headRefOid | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $merged.state -ne 'MERGED' -or $merged.headRefOid -ne $ExpectedHead) {
        throw 'GitHub merge readback did not prove the exact expected head was merged.'
    }
    $report.status = 'MERGED'
    $report.executed = $true
}
$report | ConvertTo-Json -Depth 6
