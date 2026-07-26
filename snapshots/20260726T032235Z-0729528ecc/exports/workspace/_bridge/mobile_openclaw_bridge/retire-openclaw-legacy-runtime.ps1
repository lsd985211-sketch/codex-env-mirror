param(
    [switch]$Apply,
    [string]$Confirm = ""
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$ExpectedConfirmation = "RETIRE-LEGACY-OPENCLAW-RUNTIME"
$LegacyRoot = "C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager\_tools\openclaw-codex"
$ManagedRoot = if ($env:CODEX_OPENCLAW_RUNTIME_ROOT) {
    $env:CODEX_OPENCLAW_RUNTIME_ROOT
} else {
    Join-Path $env:LOCALAPPDATA "Codex\openclaw"
}
$RequiredManagedFiles = @(
    "node24\node-v24.17.0-win-x64\node.exe",
    "clean-install\openclaw-extract\package\openclaw.mjs",
    "clean-install\secrets\gateway-token.txt",
    "weixin_send_reply.mjs"
)
$ManagedCacheRelatives = @(
    "downloads",
    "npm-cache",
    "npm-global",
    "clean-install\backup",
    "clean-install\corepack-home",
    "clean-install\direct-tarballs",
    "clean-install\npm-cache",
    "clean-install\npm-cache-ci",
    "clean-install\npm-cache-localtgz",
    "clean-install\npm-cache-localtgz2",
    "clean-install\npm-cache-test",
    "clean-install\npm-cache-test2",
    "clean-install\npm-global",
    "clean-install\npm-global-localtgz",
    "clean-install\npm-global-localtgz2",
    "clean-install\packs",
    "clean-install\pnpm-extract",
    "clean-install\pnpm-home",
    "clean-install\pnpm-store"
)

function Get-TreeStat([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return [pscustomobject]@{ Path = $Path; Exists = $false; Bytes = 0; Files = 0; Reparse = $false }
    }
    $item = Get-Item -LiteralPath $Path -Force
    $reparse = [bool]($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
    if ($reparse) {
        return [pscustomobject]@{ Path = $Path; Exists = $true; Bytes = 0; Files = 0; Reparse = $true }
    }
    if (-not $item.PSIsContainer) {
        return [pscustomobject]@{ Path = $Path; Exists = $true; Bytes = [int64]$item.Length; Files = 1; Reparse = $false }
    }
    $measure = Get-ChildItem -LiteralPath $Path -Force -File -Recurse -ErrorAction SilentlyContinue |
        Measure-Object Length -Sum
    return [pscustomobject]@{
        Path = $Path
        Exists = $true
        Bytes = [int64]($measure.Sum -as [int64])
        Files = [int]$measure.Count
        Reparse = $false
    }
}

$required = @($RequiredManagedFiles | ForEach-Object {
    $path = Join-Path $ManagedRoot $_
    [pscustomobject]@{ Relative = $_; Path = $path; Exists = (Test-Path -LiteralPath $path -PathType Leaf) }
})
$gatewayProcess = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq "node.exe" -and
    $_.ExecutablePath -and
    $_.ExecutablePath.StartsWith($ManagedRoot, [StringComparison]::OrdinalIgnoreCase) -and
    $_.CommandLine -like "*openclaw.mjs*gateway*"
})
$gatewayListening = [bool](Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 18789 -State Listen -ErrorAction SilentlyContinue)
$legacy = Get-TreeStat $LegacyRoot
$caches = @($ManagedCacheRelatives | ForEach-Object { Get-TreeStat (Join-Path $ManagedRoot $_) })
$blockers = @()
if (@($required | Where-Object { -not $_.Exists }).Count) { $blockers += "managed_runtime_incomplete" }
if (-not $gatewayProcess) { $blockers += "managed_gateway_process_missing" }
if (-not $gatewayListening) { $blockers += "managed_gateway_port_not_listening" }
if ($legacy.Reparse) { $blockers += "legacy_root_is_reparse_point" }
if (@($caches | Where-Object { $_.Reparse }).Count) { $blockers += "managed_cache_candidate_is_reparse_point" }

$plan = [ordered]@{
    Schema = "openclaw.legacy_runtime_retirement.v1"
    Ok = ($blockers.Count -eq 0)
    Applied = $false
    ManagedRoot = $ManagedRoot
    ManagedRuntimeReady = (@($required | Where-Object { -not $_.Exists }).Count -eq 0)
    GatewayProcessReady = [bool]$gatewayProcess
    GatewayListening = $gatewayListening
    RequiredFiles = $required
    Legacy = $legacy
    ManagedCacheCandidates = $caches
    ReclaimableBytes = [int64]$legacy.Bytes + [int64](($caches | Measure-Object Bytes -Sum).Sum -as [int64])
    Blockers = $blockers
    Protected = @(
        "Windows pip/wheel cache",
        "clean-install\state",
        "clean-install\secrets",
        "clean-install\openclaw-extract",
        "node24",
        "weixin_send_reply.mjs",
        "clean-install\logs"
    )
    Confirmation = $ExpectedConfirmation
}

if (-not $Apply) {
    $plan | ConvertTo-Json -Depth 6
    exit $(if ($plan.Ok) { 0 } else { 1 })
}
if ($Confirm -ne $ExpectedConfirmation) {
    $plan.Ok = $false
    $plan.Blockers = @($plan.Blockers) + "confirmation_required"
    $plan | ConvertTo-Json -Depth 6
    exit 2
}
if ($blockers.Count) {
    $plan | ConvertTo-Json -Depth 6
    exit 3
}

if ($legacy.Exists) {
    Remove-Item -LiteralPath $LegacyRoot -Recurse -Force
}
$deletedCaches = @()
foreach ($candidate in $caches) {
    if (-not $candidate.Exists) { continue }
    Remove-Item -LiteralPath $candidate.Path -Recurse -Force
    $deletedCaches += $candidate
}

$plan.Ok = (-not (Test-Path -LiteralPath $LegacyRoot)) -and @($deletedCaches | Where-Object { Test-Path -LiteralPath $_.Path }).Count -eq 0
$plan.Applied = $true
$plan.DeletedManagedCaches = $deletedCaches
$plan.LegacyExistsAfter = Test-Path -LiteralPath $LegacyRoot
$plan | ConvertTo-Json -Depth 6
exit $(if ($plan.Ok) { 0 } else { 4 })
