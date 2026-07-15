param(
  [switch]$Apply
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Root = Split-Path -Parent $PSScriptRoot
$Cli = Join-Path $PSScriptRoot 'mirror_cli.py'
if ($Apply) {
  & python $Cli snapshot --apply
} else {
  & python $Cli snapshot
}
exit $LASTEXITCODE

