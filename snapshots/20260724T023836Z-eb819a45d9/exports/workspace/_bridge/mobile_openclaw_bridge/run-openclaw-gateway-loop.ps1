param(
  [int]$Port = 18789,
  [int]$RestartDelaySeconds = 5
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$OpenClawRuntimeRoot = if ($env:CODEX_OPENCLAW_RUNTIME_ROOT) { $env:CODEX_OPENCLAW_RUNTIME_ROOT } else { Join-Path $env:LOCALAPPDATA "Codex\openclaw" }
$OpenClawBase = Join-Path $OpenClawRuntimeRoot "clean-install"
$Node = if ($env:CODEX_OPENCLAW_NODE) { $env:CODEX_OPENCLAW_NODE } else { Join-Path $OpenClawRuntimeRoot "node24\node-v24.17.0-win-x64\node.exe" }
$OpenClaw = Join-Path $OpenClawBase "openclaw-extract\package\openclaw.mjs"
$StateDir = Join-Path $OpenClawBase "state"
$HomeDir = Join-Path $OpenClawBase "home"
$LogDir = Join-Path $OpenClawBase "logs"
$SecretsDir = Join-Path $OpenClawBase "secrets"
$GatewayTokenFile = Join-Path $SecretsDir "gateway-token.txt"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
New-Item -ItemType Directory -Force -Path $SecretsDir | Out-Null

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$StdoutLog = Join-Path $LogDir "openclaw-gateway-loop-$stamp.stdout.log"
$StderrLog = Join-Path $LogDir "openclaw-gateway-loop-$stamp.stderr.log"
$LifecycleLog = Join-Path $LogDir "openclaw-gateway-loop-$stamp.lifecycle.log"

function Write-Life([string]$Message) {
  Add-Content -LiteralPath $LifecycleLog -Encoding UTF8 -Value ((Get-Date -Format o) + " " + $Message)
}

function Read-OrCreateGatewayToken {
  if (Test-Path -LiteralPath $GatewayTokenFile) {
    $existing = (Get-Content -Raw -Encoding UTF8 -LiteralPath $GatewayTokenFile).Trim()
    if ($existing) {
      return $existing
    }
  }

  $bytes = New-Object byte[] 32
  $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  try {
    $rng.GetBytes($bytes)
  } finally {
    $rng.Dispose()
  }
  $token = [Convert]::ToBase64String($bytes).TrimEnd("=") -replace "\+", "-" -replace "/", "_"
  Set-Content -LiteralPath $GatewayTokenFile -Encoding ASCII -NoNewline -Value $token
  return $token
}

try {
  if (-not (Test-Path -LiteralPath $Node)) {
    throw "Missing OpenClaw Node runtime: $Node"
  }
  if (-not (Test-Path -LiteralPath $OpenClaw)) {
    throw "Missing OpenClaw launcher: $OpenClaw"
  }

  $env:OPENCLAW_HOME = $HomeDir
  $env:OPENCLAW_STATE_DIR = $StateDir
  $env:OPENCLAW_GATEWAY_TOKEN = Read-OrCreateGatewayToken

  Write-Life "starting gateway supervisor port=$Port restartDelay=$RestartDelaySeconds node=$Node"
  Write-Life "stdout=$StdoutLog"
  Write-Life "stderr=$StderrLog"
  Write-Life "gateway auth token source=$GatewayTokenFile"

  $run = 0
  while ($true) {
    $run += 1
    $existingPort = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($existingPort) {
      Write-Life "run=$run port=$Port already listening; rechecking after ${RestartDelaySeconds}s"
      Start-Sleep -Seconds ([Math]::Max(1, $RestartDelaySeconds))
      continue
    }

    Write-Life "gateway run=$run starting"
    $gatewayArgs = @(
      $OpenClaw,
      "gateway",
      "--port",
      [string]$Port,
      "--verbose"
    )
    $process = Start-Process -FilePath $Node `
      -ArgumentList $gatewayArgs `
      -WorkingDirectory $OpenClawBase `
      -WindowStyle Hidden `
      -RedirectStandardOutput $StdoutLog `
      -RedirectStandardError $StderrLog `
      -PassThru
    $process.WaitForExit()
    $exitCode = $process.ExitCode
    Write-Life "gateway run=$run exited code=$exitCode; restarting after ${RestartDelaySeconds}s"
    Start-Sleep -Seconds ([Math]::Max(1, $RestartDelaySeconds))
  }
} catch {
  Write-Life ("gateway supervisor failed: " + $_.Exception.Message)
  Add-Content -LiteralPath $StderrLog -Encoding UTF8 -Value $_.ScriptStackTrace
  exit 1
}
