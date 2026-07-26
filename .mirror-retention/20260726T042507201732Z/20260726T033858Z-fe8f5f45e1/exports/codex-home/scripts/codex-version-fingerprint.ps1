param(
    [string]$StateRoot = "",
    [int]$TotalTimeoutSeconds = 12,
    [int]$CommandTimeoutSeconds = 3
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
$mutex = $null
$lockAcquired = $false

function Get-TextSha256 {
    param([string]$Value)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
        return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function ConvertTo-CanonicalJson {
    param($Value)
    if ($null -eq $Value) { return "null" }
    if ($Value -is [System.Collections.IDictionary]) {
        $parts = foreach ($key in @($Value.Keys | ForEach-Object { [string]$_ } | Sort-Object)) {
            (ConvertTo-CanonicalJson -Value $key) + ":" + (ConvertTo-CanonicalJson -Value $Value[$key])
        }
        return "{" + ($parts -join ",") + "}"
    }
    if ($Value -is [System.Collections.IEnumerable] -and $Value -isnot [string]) {
        $parts = foreach ($item in $Value) { ConvertTo-CanonicalJson -Value $item }
        return "[" + ($parts -join ",") + "]"
    }
    if ($Value -is [pscustomobject]) {
        $parts = foreach ($property in @($Value.PSObject.Properties | Sort-Object Name)) {
            (ConvertTo-CanonicalJson -Value ([string]$property.Name)) + ":" + (ConvertTo-CanonicalJson -Value $property.Value)
        }
        return "{" + ($parts -join ",") + "}"
    }
    return ($Value | ConvertTo-Json -Compress)
}

function Write-JsonAtomic {
    param([string]$Path, $Value)
    $directory = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
    $temporary = "$Path.tmp-$PID-$([DateTime]::UtcNow.Ticks)"
    try {
        $json = $Value | ConvertTo-Json -Depth 12 -Compress
        [System.IO.File]::WriteAllText($temporary, $json, $utf8NoBom)
        Move-Item -LiteralPath $temporary -Destination $Path -Force
    } finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}

function Get-PropertyValue {
    param($Object, [string]$Name, $Default = $null)
    if ($null -eq $Object) { return $Default }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $Default }
    return $property.Value
}

function Convert-ToCommandLine {
    param([string[]]$Arguments)
    $quoted = foreach ($argument in $Arguments) {
        $value = [string]$argument
        if ($value -notmatch '[\s"]') {
            $value
        } else {
            '"' + ($value -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"'
        }
    }
    return ($quoted -join " ")
}

function Invoke-BoundedCommand {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [int]$TimeoutSeconds
    )
    $result = [ordered]@{
        status = "unavailable"
        exit_code = $null
        stdout = ""
        executable = $FilePath
    }
    if ([string]::IsNullOrWhiteSpace($FilePath) -or -not (Test-Path -LiteralPath $FilePath -PathType Leaf)) {
        return $result
    }
    if ($stopwatch.Elapsed.TotalSeconds -ge $TotalTimeoutSeconds) {
        $result.status = "total_timeout"
        return $result
    }
    $remaining = [Math]::Max(1, [Math]::Floor($TotalTimeoutSeconds - $stopwatch.Elapsed.TotalSeconds))
    $effectiveTimeout = [Math]::Max(1, [Math]::Min($TimeoutSeconds, $remaining))
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = New-Object System.Diagnostics.ProcessStartInfo
    $process.StartInfo.FileName = $FilePath
    $process.StartInfo.Arguments = Convert-ToCommandLine -Arguments $Arguments
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.CreateNoWindow = $true
    $process.StartInfo.RedirectStandardOutput = $true
    $process.StartInfo.RedirectStandardError = $true
    try {
        if (-not $process.Start()) {
            $result.status = "start_failed"
            return $result
        }
        if (-not $process.WaitForExit($effectiveTimeout * 1000)) {
            try { $process.Kill() } catch { }
            $result.status = "timeout"
            return $result
        }
        $stdout = ($process.StandardOutput.ReadToEnd() + "`n" + $process.StandardError.ReadToEnd()).Trim()
        if ($stdout.Length -gt 2048) { $stdout = $stdout.Substring(0, 2048) }
        $result.exit_code = [int]$process.ExitCode
        $result.stdout = $stdout
        $result.status = if ($process.ExitCode -eq 0) { "supported" } else { "degraded" }
        return $result
    } catch {
        $result.status = "unavailable"
        return $result
    } finally {
        $process.Dispose()
    }
}

function Get-FileIdentity {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return [ordered]@{ status = "unavailable"; path = "" }
    }
    try {
        $item = Get-Item -LiteralPath $Path -ErrorAction Stop
        $version = $item.VersionInfo
        return [ordered]@{
            status = "supported"
            path = $item.FullName
            length = [int64]$item.Length
            file_version = [string]$version.FileVersion
            product_version = [string]$version.ProductVersion
        }
    } catch {
        return [ordered]@{ status = "unavailable"; path = $Path }
    }
}

function Complete-Component {
    param($Component)
    $copy = [ordered]@{}
    foreach ($key in $Component.Keys) {
        if ($key -notin @("captured_at", "duration_ms", "error", "digest")) {
            $copy[$key] = $Component[$key]
        }
    }
    $Component.digest = Get-TextSha256 -Value (ConvertTo-CanonicalJson -Value $copy)
    return $Component
}

if ([string]::IsNullOrWhiteSpace($StateRoot)) {
    $StateRoot = Join-Path $env:USERPROFILE ".codex\state\codex-update-intelligence"
}
$TotalTimeoutSeconds = [Math]::Max(4, [Math]::Min($TotalTimeoutSeconds, 30))
$CommandTimeoutSeconds = [Math]::Max(1, [Math]::Min($CommandTimeoutSeconds, 8))
$receiptPath = Join-Path $StateRoot "fingerprint-receipt.json"

try {
    $mutex = New-Object System.Threading.Mutex($false, "Local\OpenAI-Codex-Version-Fingerprint")
    try { $lockAcquired = $mutex.WaitOne(0) } catch { $lockAcquired = $false }
    if (-not $lockAcquired) {
        Write-JsonAtomic -Path $receiptPath -Value ([ordered]@{
            schema = "codex_local_version_fingerprint.v1.receipt"
            ok = $true
            status = "already_running"
            completed_at = [DateTime]::UtcNow.ToString("o")
        })
        exit 0
    }

    $desktop = [ordered]@{
        status = "unavailable"
        product_surface = "desktop"
        host_id = "windows_local"
        package_name = ""
        publisher_id = ""
        package_version = ""
        executable = [ordered]@{ status = "unavailable"; path = "" }
        signature_status = "unknown"
    }
    try {
        $package = Get-AppxPackage -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match "OpenAI|Codex" -or $_.PackageFamilyName -match "OpenAI|Codex" } |
            Sort-Object Version -Descending |
            Select-Object -First 1
        if ($null -ne $package) {
            $executablePath = Join-Path $package.InstallLocation "app\ChatGPT.exe"
            $signature = if (Test-Path -LiteralPath $executablePath) { Get-AuthenticodeSignature -FilePath $executablePath } else { $null }
            $desktop.status = "supported"
            $desktop.package_name = [string]$package.Name
            $desktop.publisher_id = [string]$package.PublisherId
            $desktop.package_version = [string]$package.Version
            $desktop.executable = Get-FileIdentity -Path $executablePath
            $desktop.signature_status = if ($null -ne $signature) { [string]$signature.Status } else { "unavailable" }
        }
    } catch {
        $desktop.status = "degraded"
    }
    $desktop = Complete-Component -Component $desktop

    $windowsCliCommand = Get-Command codex.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    $windowsCliPath = if ($null -ne $windowsCliCommand) { [string]$windowsCliCommand.Source } else { "" }
    $windowsCliVersion = Invoke-BoundedCommand -FilePath $windowsCliPath -Arguments @("--version") -TimeoutSeconds $CommandTimeoutSeconds
    $windowsCli = Complete-Component -Component ([ordered]@{
        status = [string]$windowsCliVersion.status
        product_surface = "windows_cli"
        host_id = "windows_local"
        executable = Get-FileIdentity -Path $windowsCliPath
        version_output = [string]$windowsCliVersion.stdout
    })

    $wslCommand = Get-Command wsl.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    $wslPath = if ($null -ne $wslCommand) { [string]$wslCommand.Source } else { "" }
    $wslStatus = Invoke-BoundedCommand -FilePath ([string]$wslPath) -Arguments @("--status") -TimeoutSeconds $CommandTimeoutSeconds
    $wslVersion = Invoke-BoundedCommand -FilePath ([string]$wslPath) -Arguments @("-e", "sh", "-lc", "command -v codex >/dev/null 2>&1 && { printf 'path='; command -v codex; printf '\nversion='; codex --version; }" ) -TimeoutSeconds $CommandTimeoutSeconds
    $wslCli = Complete-Component -Component ([ordered]@{
        status = if ($wslVersion.status -eq "supported") { "supported" } elseif ($wslStatus.status -eq "supported") { "degraded" } else { [string]$wslVersion.status }
        product_surface = "remote_wsl_app_server"
        host_id = "wsl_default_host"
        host_discovery = [string]$wslStatus.status
        cli_app_server_shared_binary = $true
        version_output = [string]$wslVersion.stdout
    })

    $fingerprintCore = [ordered]@{
        schema = "codex_local_version_fingerprint.v1"
        host = [ordered]@{ identity = "windows_launcher" }
        components = [ordered]@{
            desktop_package = $desktop
            windows_cli = $windowsCli
            wsl_cli_app_server = $wslCli
        }
    }
    $fingerprintDigest = Get-TextSha256 -Value (ConvertTo-CanonicalJson -Value $fingerprintCore)
    $fingerprint = [ordered]@{
        schema = $fingerprintCore.schema
        captured_at = [DateTime]::UtcNow.ToString("o")
        host = $fingerprintCore.host
        components = $fingerprintCore.components
        digest = $fingerprintDigest
    }
    $lastObservedPath = Join-Path $StateRoot "last_observed.json"
    $lastValidatedPath = Join-Path $StateRoot "last_validated.json"
    Write-JsonAtomic -Path $lastObservedPath -Value $fingerprint
    $validatedDigest = ""
    if (Test-Path -LiteralPath $lastValidatedPath -PathType Leaf) {
        try {
            $validated = Get-Content -LiteralPath $lastValidatedPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $validatedDigest = [string](Get-PropertyValue -Object $validated -Name "collector_digest" -Default "")
            if ([string]::IsNullOrWhiteSpace($validatedDigest)) {
                $validatedDigest = [string](Get-PropertyValue -Object $validated -Name "digest" -Default "")
            }
        } catch { $validatedDigest = "" }
    }
    $eventId = "dce_" + (Get-TextSha256 -Value ("codex`n$validatedDigest`n$fingerprintDigest`nlauncher")).Substring(0, 24)
    $pendingPath = Join-Path $StateRoot ("pending\" + $eventId + ".json")
    $completedPath = Join-Path $StateRoot ("completed\" + $eventId + ".json")
    $enqueued = $false
    if ($validatedDigest -ne $fingerprintDigest -and -not (Test-Path -LiteralPath $pendingPath) -and -not (Test-Path -LiteralPath $completedPath)) {
        $trigger = [ordered]@{
            schema = "codex_version_change_trigger.v1"
            event_id = $eventId
            profile_id = "codex"
            trigger_kind = "launcher"
            detected_at = [DateTime]::UtcNow.ToString("o")
            previous_validated_digest = $validatedDigest
            current_observed_digest = $fingerprintDigest
            fingerprint = $fingerprint
            ingestion = [ordered]@{
                owner = "dependency_change_intelligence.py"
                status = "pending"
                retry_on_next_launcher_or_manual_scan = $true
            }
        }
        Write-JsonAtomic -Path $pendingPath -Value $trigger
        Write-JsonAtomic -Path (Join-Path $StateRoot "last_enqueued.json") -Value ([ordered]@{ schema = "codex_version_change_trigger.v1.last_enqueued"; event_id = $eventId; digest = $fingerprintDigest })
        $enqueued = $true
    }
    Write-JsonAtomic -Path $receiptPath -Value ([ordered]@{
        schema = "codex_local_version_fingerprint.v1.receipt"
        ok = $true
        status = if ($validatedDigest -eq $fingerprintDigest) { "unchanged" } elseif ($enqueued) { "enqueued" } else { "duplicate_pending" }
        completed_at = [DateTime]::UtcNow.ToString("o")
        elapsed_ms = [int]$stopwatch.ElapsedMilliseconds
        event_id = $eventId
        fingerprint_digest = $fingerprintDigest
        validated_baseline_advanced = $false
        network_used = $false
    })
} catch {
    try {
        Write-JsonAtomic -Path $receiptPath -Value ([ordered]@{
            schema = "codex_local_version_fingerprint.v1.receipt"
            ok = $false
            status = "advisory_failure"
            completed_at = [DateTime]::UtcNow.ToString("o")
            elapsed_ms = [int]$stopwatch.ElapsedMilliseconds
            error_class = $_.Exception.GetType().Name
            validated_baseline_advanced = $false
            network_used = $false
        })
    } catch { }
} finally {
    if ($lockAcquired -and $null -ne $mutex) {
        try { $mutex.ReleaseMutex() } catch { }
    }
    if ($null -ne $mutex) { $mutex.Dispose() }
}

exit 0
