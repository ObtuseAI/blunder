#requires -Version 5.1
<#
.SYNOPSIS
    Native Blunder launcher.
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$ErrorActionPreference = 'Stop'

function Resolve-RepoRoot {
    return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
}

function Join-Args {
    param([Parameter(Mandatory)] [AllowEmptyCollection()] [string[]]$Items)
    return ($Items -join ' ').Trim()
}

function Show-Help {
    @'
Blunder

Usage:
  blundercode                       Open Blunder Focus Shell
  blundercode shell                 Open Blunder Focus Shell
  blundercode shell --no-browser    Start the shell without opening a browser
  blundercode stop                  Stop the owned shell process
  blundercode smoke                 Run the dependency-free shell smoke
  blundercode status                Print live Blunder status
  blundercode plan                  Run the planning and coding gate
  blundercode evolve                Refresh compound-evolution guidance
  blundercode arena                 Run the provider-free agent evaluation arena
  blundercode research <question>   Build a provenance-backed discovery brief
  blundercode reliability           Run a three-cycle reliability and recovery campaign
  blundercode product audit <lane>  Inspect mobile, web, or game project readiness
  blundercode product device        Build the first-party device matrix
  blundercode product polyglot      Index product languages and diagnostics
  blundercode product arena         Run the product arena without fabricated candidates
  blundercode product blueprint <json>  Compile an app blueprint request
  blundercode product design <json>  Compile and contrast-test a design system
  blundercode product visual <json>  Evaluate PNG and layout evidence
  blundercode product assets        Audit asset provenance, licenses, and budgets
  blundercode kernel snapshot       Read the Quorum Graph Kernel control plane
  blundercode kernel graph          Build the content-addressed semantic graph
  blundercode kernel impact <json>  Propagate a bounded source-change frontier
  blundercode kernel shards <json>  Run allowlisted isolated validation shards
  blundercode kernel quorum <json>  Evaluate proposer, skeptic, and judge evidence
  blundercode kernel metabolism <json>  Compile a resource-gated idle plan
  blundercode ccos state            Verify the durable CCOS evidence plane
  blundercode ccos records [kind]   Read verified immutable records
  blundercode ccos append <kind> <json>  Append governed evidence without authority
  blundercode ccos canary           Run provider-free activation and rollback gates
  blundercode ccos retention        Report projection-retention candidates
  blundercode ccos recovery-inspect Inspect interrupted-write evidence
  blundercode ccos recover <phrase> Restore the exact last-known-good index
  blundercode validate              Run the native Blunder test suite
  blundercode run <task>            Run one explicitly approved native task
  blundercode help                  Show this help
'@
}

$repo = Resolve-RepoRoot
$command = if ($Arguments.Count -gt 0) { $Arguments[0].ToLowerInvariant() } else { 'shell' }
[string[]]$tail = @()
if ($Arguments.Count -gt 1) {
    $tail = [string[]]$Arguments[1..($Arguments.Count - 1)]
}

switch ($command) {
    'shell' {
        & (Join-Path $PSScriptRoot 'blunder-shell.ps1') -NoBrowser:($tail -contains '--no-browser')
        exit $LASTEXITCODE
    }
    'stop' {
        & (Join-Path $PSScriptRoot 'blunder-shell.ps1') -Stop -NoBrowser
        exit $LASTEXITCODE
    }
    'smoke' {
        & (Join-Path $PSScriptRoot 'blunder-shell.ps1') -Smoke -NoBrowser
        exit $LASTEXITCODE
    }
    'status' {
        & (Join-Path $PSScriptRoot 'status-idle-ops.ps1')
        exit $LASTEXITCODE
    }
    'plan' {
        & (Join-Path $PSScriptRoot 'blunder.ps1') -Command PlanningCodingGate
        exit $LASTEXITCODE
    }
    'evolve' {
        $ledgerRoot = Join-Path $repo 'artifacts\obtuse\blunder-flash-full-autonomy-compounding-bundle-v1\self-coding'
        $planPath = Join-Path $ledgerRoot 'compound_evolution_plan.json'
        & python (Join-Path $PSScriptRoot 'blunder-compound-evolution-planner.py') --ledger-root $ledgerRoot --plan-path $planPath
        exit $LASTEXITCODE
    }
    'arena' {
        $artifactRoot = Join-Path $repo 'artifacts\obtuse\blunder-evaluation-arena-v1'
        New-Item -ItemType Directory -Force -Path $artifactRoot | Out-Null
        & python (Join-Path $PSScriptRoot 'blunder_evaluation_arena.py') `
            --suite (Join-Path $repo 'configs\obtuse\blunder-evaluation-suite.json') `
            --measurements (Join-Path $artifactRoot 'measurements.json') `
            --baselines (Join-Path $artifactRoot 'baselines.json') `
            --report (Join-Path $artifactRoot 'latest-report.json')
        exit $LASTEXITCODE
    }
    'research' {
        $question = Join-Args -Items $tail
        if ([string]::IsNullOrWhiteSpace($question)) { throw 'A concrete research question is required.' }
        $artifact = Join-Path $repo 'artifacts\obtuse\blunder-discovery-v1\latest-brief.json'
        & python (Join-Path $PSScriptRoot 'blunder_discovery.py') --root $repo --question $question --report $artifact --evidence-limit 12
        exit $LASTEXITCODE
    }
    'reliability' {
        $artifact = Join-Path $repo 'artifacts\obtuse\blunder-reliability-campaign-v1\latest-report.json'
        & python (Join-Path $PSScriptRoot 'blunder_reliability_campaign.py') --root $repo --artifact $artifact --cycles 3 --interval-seconds 1
        exit $LASTEXITCODE
    }
    'product' {
        if ($tail.Count -lt 1) { throw 'Product Studio requires audit, device, polyglot, arena, or blueprint.' }
        $productAction = ([string]$tail[0]).ToLowerInvariant()
        $artifactRoot = Join-Path $repo 'artifacts\obtuse\blunder-product-studio-v1'
        New-Item -ItemType Directory -Force -Path $artifactRoot | Out-Null
        $arguments = @((Join-Path $PSScriptRoot 'blunder_product_studio.py'), $productAction, '--root', $repo, '--report', (Join-Path $artifactRoot "$productAction-report.json"))
        if ($productAction -eq 'audit') {
            if ($tail.Count -lt 2) { throw 'Product audit requires mobile, web, or game.' }
            $arguments += @('--domain', ([string]$tail[1]).ToLowerInvariant())
        }
        if ($productAction -in @('blueprint','design','visual')) {
            if ($tail.Count -lt 2) { throw "Product $productAction requires an input JSON path." }
            $arguments += @('--input', $tail[1])
        }
        & python @arguments
        exit $LASTEXITCODE
    }
    'kernel' {
        if ($tail.Count -lt 1) { throw 'Quorum Graph Kernel requires snapshot, graph, impact, shards, tournament, quorum, timeline, or metabolism.' }
        $kernelAction = ([string]$tail[0]).ToLowerInvariant()
        $allowedKernelActions = @('snapshot','graph','impact','shards','tournament','quorum','timeline','metabolism')
        if ($kernelAction -notin $allowedKernelActions) { throw "Unsupported Quorum Graph Kernel action: $kernelAction" }
        $artifactRoot = Join-Path $repo 'artifacts\obtuse\blunder-quorum-graph-kernel-v1'
        New-Item -ItemType Directory -Force -Path $artifactRoot | Out-Null
        $kernelArguments = @((Join-Path $PSScriptRoot 'blunder_quorum_kernel.py'), $kernelAction, '--root', $repo, '--report', (Join-Path $artifactRoot "$kernelAction-report.json"))
        if ($kernelAction -notin @('snapshot','graph')) {
            if ($tail.Count -lt 2) { throw "Kernel $kernelAction requires an input JSON path." }
            $kernelArguments += @('--input', $tail[1])
        }
        & python @kernelArguments
        exit $LASTEXITCODE
    }
    'ccos' {
        if ($tail.Count -lt 1) { throw 'CCOS requires state, records, append, canary, retention, recovery-inspect, or recover.' }
        $ccosAction = ([string]$tail[0]).ToLowerInvariant()
        $allowedCcosActions = @('state','records','append','canary','retention','recovery-inspect','recover')
        if ($ccosAction -notin $allowedCcosActions) { throw "Unsupported CCOS action: $ccosAction" }
        $ccosArguments = @((Join-Path $PSScriptRoot 'blunder_ccos_runtime.py'), $ccosAction, '--root', $repo)
        if ($ccosAction -eq 'records' -and $tail.Count -gt 1) {
            $ccosArguments += @('--kind', $tail[1])
        }
        if ($ccosAction -eq 'append') {
            if ($tail.Count -lt 3) { throw 'CCOS append requires a record kind and JSON input path.' }
            $ccosArguments += @('--kind', $tail[1], '--input', $tail[2])
        }
        if ($ccosAction -eq 'recover') {
            if ($tail.Count -lt 2) { throw 'CCOS recover requires the exact confirmation phrase.' }
            $ccosArguments += @('--confirmation', $tail[1])
        }
        & python @ccosArguments
        exit $LASTEXITCODE
    }
    'validate' {
        Push-Location $repo
        try {
            & python -m unittest discover -s tests -v
            exit $LASTEXITCODE
        }
        finally {
            Pop-Location
        }
    }
    'run' {
        $task = Join-Args -Items $tail
        if ([string]::IsNullOrWhiteSpace($task)) { throw 'A concrete task is required after `blundercode run`.' }
        & python (Join-Path $PSScriptRoot 'blunder_native_agent.py') --root $repo --task $task --provider-approved
        exit $LASTEXITCODE
    }
    'help' { Show-Help; return }
    '--help' { Show-Help; return }
    '-h' { Show-Help; return }
    default { throw "Unknown native Blunder command: $command. Run `blundercode help`." }
}
