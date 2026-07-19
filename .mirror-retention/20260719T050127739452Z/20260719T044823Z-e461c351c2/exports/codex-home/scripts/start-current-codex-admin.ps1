$ErrorActionPreference = 'Stop'

$packageHelper = Join-Path $env:USERPROFILE '.codex\scripts\codex-desktop-package.ps1'
if (-not (Test-Path -LiteralPath $packageHelper)) {
  throw "Codex Desktop package helper was not found at $packageHelper"
}
. $packageHelper

$codexExe = Resolve-CurrentCodexDesktopExe
if ([string]::IsNullOrWhiteSpace($codexExe)) {
  throw 'OpenAI Codex Desktop executable was not found.'
}
$appDir = Split-Path -Parent $codexExe

$args = @(
  '--remote-debugging-port=9229',
  '--remote-allow-origins=http://127.0.0.1:9229'
)

Start-Process -FilePath $codexExe -ArgumentList $args -WorkingDirectory $appDir -Verb RunAs
