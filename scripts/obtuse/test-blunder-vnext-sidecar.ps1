param(
    [Parameter(Mandatory = $true)]
    [string]$Root,
    [Parameter(Mandatory = $true)]
    [string]$Executable
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Net.Http
$resolvedRoot = (Resolve-Path -LiteralPath $Root).Path
$resolvedExecutable = (Resolve-Path -LiteralPath $Executable).Path
$tokenBytes = New-Object byte[] 48
$random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$random.GetBytes($tokenBytes)
$random.Dispose()
$token = -join ($tokenBytes | ForEach-Object { $_.ToString('x2') })

$start = [System.Diagnostics.ProcessStartInfo]::new()
$start.FileName = $resolvedExecutable
$start.Arguments = "--root `"$resolvedRoot`" --token-stdin --port 0"
$start.WorkingDirectory = $resolvedRoot
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
$start.RedirectStandardInput = $true
$start.RedirectStandardOutput = $true
$start.RedirectStandardError = $false
$process = [System.Diagnostics.Process]::new()
$process.StartInfo = $start
$ready = $null

try {
    if (-not $process.Start()) {
        throw 'The Blunder sidecar process did not start.'
    }
    $tokenPayload = [System.Text.UTF8Encoding]::new($false).GetBytes("$token`n")
    $process.StandardInput.BaseStream.Write($tokenPayload, 0, $tokenPayload.Length)
    $process.StandardInput.BaseStream.Flush()
    $readyLine = $process.StandardOutput.ReadLine()
    if (-not $readyLine) {
        throw 'The Blunder sidecar returned no readiness record. Sidecar stderr was emitted directly above.'
    }
    $ready = $readyLine | ConvertFrom-Json
    if ($ready.schema -ne 'obtuse.blunder.core-ready.v1' -or $ready.status -ne 'READY' -or $ready.host -ne '127.0.0.1') {
        throw 'The Blunder sidecar readiness record has an invalid identity or host boundary.'
    }
    if ($ready.launcherPid -ne $process.Id -or $ready.pid -lt 1) {
        throw 'The Blunder sidecar launcher and runtime process identities do not match the owned process tree.'
    }
    $handler = [System.Net.Http.HttpClientHandler]::new()
    $handler.UseProxy = $false
    $client = [System.Net.Http.HttpClient]::new($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(2)
    $client.DefaultRequestHeaders.Add('X-Blunder-Token', $token)
    $health = $null
    $lastConnectionError = $null
    try {
        foreach ($attempt in 1..8) {
            try {
                $response = $client.GetAsync("http://127.0.0.1:$($ready.port)/api/v1/health").GetAwaiter().GetResult()
                $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
                if (-not $response.IsSuccessStatusCode) {
                    throw "Blunder sidecar health returned HTTP $([int]$response.StatusCode). body=$body"
                }
                $health = $body | ConvertFrom-Json
                break
            }
            catch [System.Exception] {
                $lastConnectionError = $_.Exception
                Write-Warning "Blunder sidecar health connection attempt $attempt failed. type=$($lastConnectionError.GetType().FullName) error=$($lastConnectionError.Message)"
                Start-Sleep -Milliseconds 250
            }
        }
    }
    finally {
        $client.Dispose()
        $handler.Dispose()
    }
    if ($null -eq $health) {
        if ($process.HasExited) {
            $process.WaitForExit()
        }
        $detail = if ($null -eq $lastConnectionError) { 'no connection error was captured' } else { $lastConnectionError.Message }
        throw "The authenticated Blunder sidecar health endpoint was unavailable after eight bounded attempts. error=$detail Sidecar stderr was emitted directly above."
    }
    if ($health.status -ne 'READY' -or $health.authorityGranted -ne $false) {
        throw 'The authenticated Blunder sidecar health contract is not authority closed.'
    }
    [ordered]@{
        schema = 'obtuse.blunder.sidecar-smoke.v1'
        status = 'PASS'
        launcherIdentityMatched = $true
        runtimeIdentityPresent = $true
        testHostPid = $PID
        launcherPid = $process.Id
        runtimePid = $ready.pid
        loopbackOnly = $true
        authenticated = $true
        authorityGranted = $false
    } | ConvertTo-Json -Depth 3
}
finally {
    if ($null -ne $ready -and $ready.pid -gt 0) {
        try {
            $runtimeProcess = [System.Diagnostics.Process]::GetProcessById([int]$ready.pid)
            $runtimeProcess.Kill()
            $runtimeProcess.WaitForExit(10000) | Out-Null
            if (-not $runtimeProcess.HasExited) {
                throw "The Blunder sidecar runtime did not terminate during smoke-test cleanup. runtimePid=$($ready.pid)"
            }
            $runtimeProcess.Dispose()
        }
        catch [System.ArgumentException] {
        }
    }
    if ($process.Id -gt 0 -and -not $process.HasExited) {
        $process.Kill()
        $process.WaitForExit(10000) | Out-Null
        if (-not $process.HasExited) {
            throw "The Blunder sidecar launcher did not terminate during smoke-test cleanup. launcherPid=$($process.Id)"
        }
    }
    $process.Dispose()
}
