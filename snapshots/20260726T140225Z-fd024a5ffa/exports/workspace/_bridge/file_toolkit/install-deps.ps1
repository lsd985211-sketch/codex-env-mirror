param(
  [string]$Python = "C:\Users\45543\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Requirements = Join-Path $Root "requirements.txt"

if (-not (Test-Path -LiteralPath $Python)) {
  $cmd = Get-Command python -ErrorAction Stop
  $Python = $cmd.Source
}

& $Python -m pip install -r $Requirements
