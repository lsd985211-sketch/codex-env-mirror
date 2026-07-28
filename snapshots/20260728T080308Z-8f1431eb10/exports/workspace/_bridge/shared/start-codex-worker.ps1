$worker = Join-Path $PSScriptRoot 'codex_worker.py'
if (-not (Test-Path $worker)) {
    Write-Error "Worker not found: $worker"
    exit 1
}

Start-Process -FilePath python -ArgumentList $worker -WindowStyle Hidden
Write-Output "codex worker started"
