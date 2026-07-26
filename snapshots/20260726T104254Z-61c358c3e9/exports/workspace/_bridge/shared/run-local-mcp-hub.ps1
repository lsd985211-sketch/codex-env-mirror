param(
  [string]$HostAddress = "127.0.0.1",
  [int]$Port = 18881,
  [switch]$Restart,
  [int]$MaxAttempts = 6,
  [int]$RetryDelaySeconds = 2,
  [int]$HealthWaitSeconds = 12
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$Script = Join-Path $Root "local_mcp_hub.py"
if (-not (Test-Path -LiteralPath $Script)) {
  throw "Missing local MCP hub script: $Script"
}

function Get-HubListener {
  Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalAddress -in @($HostAddress, "0.0.0.0", "::") } |
    Select-Object -First 1
}

function Test-HubHealth {
  try {
    $Health = Invoke-RestMethod -Uri "http://$HostAddress`:$Port/health" -Method Get -TimeoutSec 3
    return [bool]$Health.ok
  } catch {
    return $false
  }
}

function Wait-HubHealth {
  $deadline = (Get-Date).AddSeconds([Math]::Max(1, $HealthWaitSeconds))
  do {
    if (Test-HubHealth) {
      return $true
    }
    Start-Sleep -Milliseconds 500
  } while ((Get-Date) -lt $deadline)
  return $false
}

function Resolve-HubPython {
  $BundledPythonw = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe"
  $BundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
  function Get-ProbePython {
    param([string]$Candidate)
    if ($Candidate -match "pythonw\.exe$") {
      $ConsolePeer = Join-Path (Split-Path -Parent $Candidate) "python.exe"
      if (Test-Path -LiteralPath $ConsolePeer) {
        return $ConsolePeer
      }
    }
    return $Candidate
  }
  function Test-HubPython {
    param([string]$Candidate)
    if (-not (Test-Path -LiteralPath $Candidate)) {
      return $false
    }
    $ProbePython = Get-ProbePython -Candidate $Candidate
    if (-not (Test-Path -LiteralPath $ProbePython)) {
      return $false
    }
    $Probe = "import sys; sys.path.insert(0, r'$Root'); import local_mcp_hub"
    Push-Location -LiteralPath $Root
    try {
      & $ProbePython -c $Probe 1>$null 2>$null
      return $LASTEXITCODE -eq 0
    } catch {
      return $false
    } finally {
      Pop-Location
    }
  }
  if ($env:LOCAL_MCP_HUB_PYTHON -and (Test-Path -LiteralPath $env:LOCAL_MCP_HUB_PYTHON)) {
    if (Test-HubPython -Candidate $env:LOCAL_MCP_HUB_PYTHON) {
      return $env:LOCAL_MCP_HUB_PYTHON
    }
    throw "LOCAL_MCP_HUB_PYTHON cannot import local_mcp_hub dependencies: $env:LOCAL_MCP_HUB_PYTHON"
  }
  $Candidates = @($BundledPythonw, $BundledPython)
  $Pythonw = Get-Command pythonw.exe -ErrorAction SilentlyContinue
  if ($Pythonw) {
    $Candidates += $Pythonw.Source
  }
  $Python = Get-Command python -ErrorAction SilentlyContinue
  if ($Python) {
    $Candidates += $Python.Source
  }
  foreach ($Candidate in $Candidates) {
    if ($Candidate -and (Test-HubPython -Candidate $Candidate)) {
      return $Candidate
    }
  }
  throw "No Python runtime can import local_mcp_hub dependencies"
}

function Stop-HubListener {
  param($Listener)
  if (-not $Listener) {
    return
  }
  $Process = Get-CimInstance Win32_Process -Filter "ProcessId=$($Listener.OwningProcess)" -ErrorAction SilentlyContinue
  if (-not ($Process -and $Process.CommandLine -match [regex]::Escape("local_mcp_hub.py"))) {
    throw "Port $Port is already used by a non-local-mcp-hub process: PID $($Listener.OwningProcess)"
  }
  Stop-Process -Id $Listener.OwningProcess -ErrorAction Stop
  $deadline = (Get-Date).AddSeconds(8)
  do {
    Start-Sleep -Milliseconds 250
    $Current = Get-HubListener
  } while ($Current -and (Get-Date) -lt $deadline)
  if ($Current) {
    throw "Timed out waiting for local MCP hub PID $($Process.ProcessId) to release port $Port"
  }
}

$Errors = @()
for ($Attempt = 1; $Attempt -le [Math]::Max(1, $MaxAttempts); $Attempt++) {
  try {
    $Listener = Get-HubListener
    if ($Listener) {
      $Process = Get-CimInstance Win32_Process -Filter "ProcessId=$($Listener.OwningProcess)" -ErrorAction SilentlyContinue
      if ($Process -and $Process.CommandLine -match [regex]::Escape("local_mcp_hub.py")) {
        if ((-not $Restart) -and (Test-HubHealth)) {
          [pscustomobject]@{
            ok = $true
            already_running = $true
            attempt = $Attempt
            url = "http://$HostAddress`:$Port/mcp"
            health = "http://$HostAddress`:$Port/health"
            script = $Script
          } | ConvertTo-Json -Depth 4
          return
        }
        Stop-HubListener -Listener $Listener
      } else {
        throw "Port $Port is already used by a non-local-mcp-hub process: PID $($Listener.OwningProcess)"
      }
    }

    $Python = Resolve-HubPython
    $Started = Start-Process -FilePath $Python `
      -ArgumentList @($Script, "serve", "--host", $HostAddress, "--port", "$Port") `
      -WorkingDirectory $Root `
      -WindowStyle Hidden `
      -PassThru

    if (Wait-HubHealth) {
      [pscustomobject]@{
        ok = $true
        restarted = [bool]$Restart
        attempt = $Attempt
        pid = $Started.Id
        url = "http://$HostAddress`:$Port/mcp"
        health = "http://$HostAddress`:$Port/health"
        script = $Script
      } | ConvertTo-Json -Depth 4
      return
    }

    $Errors += "attempt $Attempt health timeout after start PID $($Started.Id)"
    $ListenerAfterTimeout = Get-HubListener
    if ($ListenerAfterTimeout) {
      Stop-HubListener -Listener $ListenerAfterTimeout
    }
  } catch {
    $Errors += "attempt $Attempt $($_.Exception.Message)"
    if ($Attempt -ge [Math]::Max(1, $MaxAttempts)) {
      break
    }
  }
  Start-Sleep -Seconds ([Math]::Max(1, $RetryDelaySeconds))
}

throw "Failed to start local MCP hub on $HostAddress`:$Port after $MaxAttempts attempt(s): $($Errors -join '; ')"
