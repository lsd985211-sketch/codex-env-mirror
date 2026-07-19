Set-StrictMode -Version Latest

$script:CodexCdpDefaultPort = 9229

function Get-CodexCdpPortStatePath {
    $stateDir = Join-Path $env:USERPROFILE ".codex\state"
    if (-not (Test-Path -LiteralPath $stateDir)) {
        New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
    }
    return (Join-Path $stateDir "codex-cdp-port.txt")
}

function ConvertTo-CodexCdpPort {
    param([object]$Value)
    if ($null -eq $Value) {
        return $null
    }
    $text = ([string]$Value).Trim()
    if ([string]::IsNullOrWhiteSpace($text)) {
        return $null
    }
    try {
        $port = [int]$text
        if ($port -ge 1 -and $port -le 65535) {
            return $port
        }
    } catch {
        return $null
    }
    return $null
}

function Resolve-CodexCdpPort {
    param(
        [object]$RequestedPort = $null,
        [switch]$Persist,
        [switch]$SetProcessEnv
    )

    $statePath = Get-CodexCdpPortStatePath
    $port = ConvertTo-CodexCdpPort -Value $RequestedPort
    $source = "argument"

    if ($null -eq $port) {
        $port = ConvertTo-CodexCdpPort -Value $env:CODEX_CDP_PORT
        $source = "process_env"
    }

    if ($null -eq $port -and (Test-Path -LiteralPath $statePath)) {
        try {
            $stateText = Get-Content -LiteralPath $statePath -Encoding UTF8 -TotalCount 1
            $port = ConvertTo-CodexCdpPort -Value $stateText
            $source = "state_file"
        } catch {
            $port = $null
        }
    }

    if ($null -eq $port) {
        $port = $script:CodexCdpDefaultPort
        $source = "default"
    }

    if ($Persist) {
        Set-Content -LiteralPath $statePath -Value ([string]$port) -Encoding UTF8
    }
    if ($SetProcessEnv) {
        $env:CODEX_CDP_PORT = [string]$port
    }

    [pscustomobject]@{
        Port = [int]$port
        Source = [string]$source
        StatePath = [string]$statePath
        DefaultPort = [int]$script:CodexCdpDefaultPort
    }
}
