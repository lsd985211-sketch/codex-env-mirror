Set-StrictMode -Version Latest

function Get-CodexDesktopPackage {
    @(Get-AppxPackage -Name "OpenAI.Codex" -ErrorAction SilentlyContinue |
        Sort-Object -Property @{ Expression = { [version]$_.Version }; Descending = $true } |
        Select-Object -First 1)[0]
}

function Resolve-CodexDesktopEntrypointFromInstallLocation {
    param([Parameter(Mandatory = $true)][string]$InstallLocation)

    if ([string]::IsNullOrWhiteSpace($InstallLocation)) {
        return $null
    }

    $manifestPath = Join-Path $InstallLocation "AppxManifest.xml"
    if (Test-Path -LiteralPath $manifestPath) {
        try {
            [xml]$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8
            $applications = @($manifest.Package.Applications.Application)
            $application = $applications | Where-Object { $_.Id -eq "App" } | Select-Object -First 1
            if ($null -eq $application) {
                $application = $applications | Select-Object -First 1
            }
            $relativeExecutable = [string]$application.Executable
            if (-not [string]::IsNullOrWhiteSpace($relativeExecutable)) {
                $candidate = Join-Path $InstallLocation ($relativeExecutable -replace "/", "\")
                if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                    return $candidate
                }
            }
        } catch {
            Write-Verbose "Codex AppxManifest entrypoint resolution failed: $($_.Exception.Message)"
        }
    }

    foreach ($relativePath in @("app\ChatGPT.exe", "app\Codex.exe")) {
        $candidate = Join-Path $InstallLocation $relativePath
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }
    return $null
}

function Resolve-CurrentCodexDesktopExe {
    $package = Get-CodexDesktopPackage
    if ($null -ne $package -and -not [string]::IsNullOrWhiteSpace([string]$package.InstallLocation)) {
        $resolved = Resolve-CodexDesktopEntrypointFromInstallLocation -InstallLocation ([string]$package.InstallLocation)
        if (-not [string]::IsNullOrWhiteSpace($resolved)) {
            return $resolved
        }
    }

    $fallback = Get-ChildItem -LiteralPath "C:\Program Files\WindowsApps" -Directory -Filter "OpenAI.Codex_*" -ErrorAction SilentlyContinue |
        Sort-Object -Property Name -Descending |
        ForEach-Object { Resolve-CodexDesktopEntrypointFromInstallLocation -InstallLocation $_.FullName } |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        Select-Object -First 1
    if (-not [string]::IsNullOrWhiteSpace($fallback)) {
        return $fallback
    }
    return $null
}

function Resolve-CodexDesktopUserDataDir {
    $package = Get-CodexDesktopPackage
    if ($null -eq $package -or [string]::IsNullOrWhiteSpace([string]$package.PackageFamilyName)) {
        throw "OpenAI Codex package identity was not found; refusing to use an unbound Desktop profile."
    }

    $packageRoot = Join-Path $env:LOCALAPPDATA ("Packages\" + [string]$package.PackageFamilyName)
    Join-Path $packageRoot "LocalCache\Roaming\Codex\web\Codex"
}

function Test-CodexDesktopHostProcess {
    param(
        [Parameter(Mandatory = $true)][object]$Process,
        [switch]$MainOnly
    )

    $name = [string]$Process.Name
    if ($name -notin @("ChatGPT.exe", "Codex.exe")) {
        return $false
    }
    $path = [string]$Process.ExecutablePath
    $commandLine = [string]$Process.CommandLine
    $identityText = if (-not [string]::IsNullOrWhiteSpace($path)) { $path } else { $commandLine }
    if ($identityText -notlike "*\OpenAI.Codex_*\app\*") {
        return $false
    }
    if ($identityText -like "*\app\resources\codex.exe*") {
        return $false
    }
    if ($MainOnly -and $commandLine -match "--type=") {
        return $false
    }
    return $true
}

function Get-CodexDesktopHostProcesses {
    param([switch]$MainOnly)

    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { Test-CodexDesktopHostProcess -Process $_ -MainOnly:$MainOnly })
}
