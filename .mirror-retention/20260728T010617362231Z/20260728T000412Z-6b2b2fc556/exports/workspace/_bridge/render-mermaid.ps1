param(
    [Parameter(Mandatory = $true)]
    [string]$InputMd,

    [Parameter(Mandatory = $true)]
    [string]$OutputDir,

    [string]$Prefix = "graph"
)

$nodeHome = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node'
$nodeExe = Join-Path $nodeHome 'bin\node.exe'
$script = Join-Path $PSScriptRoot 'render_mermaid_diagrams.js'

if (-not (Test-Path $nodeExe)) {
    throw "Bundled node.exe not found: $nodeExe"
}
if (-not (Test-Path $script)) {
    throw "Renderer script not found: $script"
}

$env:NODE_PATH = @(
    (Join-Path $nodeHome 'node_modules')
    (Join-Path $nodeHome 'node_modules\.pnpm\playwright@1.61.0\node_modules')
    (Join-Path $nodeHome 'node_modules\.pnpm\playwright-core@1.61.0\node_modules')
) -join [IO.Path]::PathSeparator

& $nodeExe $script $InputMd $OutputDir --prefix $Prefix
