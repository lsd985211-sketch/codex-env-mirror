<#
.SYNOPSIS
    Minecraft 服务端 MOD 分类管理脚本（通用版本 v2）
    按 fabric.mod.json 的 environment 字段自动分类处理 MOD 和配置，
    配合 AutoModpack 使用。不依赖硬编码的 MOD 列表。

.DESCRIPTION
    environment=client → 移入 client-mods/（服务端不加载）
    environment=*      → 复制到 client-mods/（服务端保留 + 客户端分发）
    environment=server → 保留在 mods/，不分发

    配置自动匹配：根据 MOD id 在 host-modpack/config/ 中查找对应文件/文件夹。
    幽灵配置自动清理。

    使用方法：在服务端实例根目录执行 .\organize-mods.ps1
    前置条件：服务端已关闭，java 在 PATH 中
#>

$ErrorActionPreference = "Continue"
$BaseDir = $PSScriptRoot
if (-not $BaseDir) { $BaseDir = Get-Location }

# ============================================================
# 路径
# ============================================================
$ModsDir        = Join-Path $BaseDir "mods"
$ConfigDir      = Join-Path $BaseDir "config"
$ClientModsDir  = Join-Path $BaseDir "client-mods"
$ClientCfgDir   = Join-Path $BaseDir "client-config"
$HostModpackDir = Join-Path $BaseDir "automodpack\host-modpack\main"
$HostConfigDir  = Join-Path $HostModpackDir "config"
$AmServerJson   = Join-Path $BaseDir "automodpack\automodpack-server.json"
$TempDir        = Join-Path $env:TEMP "mc_organize_mods"

# ============================================================
# 辅助函数
# ============================================================
function Backup-File {
    param([string]$Path)
    if (Test-Path $Path) {
        $ts = Get-Date -Format "yyyyMMdd_HHmmss"
        $backup = $Path -replace "\.json$", "_backup_$ts.json"
        Copy-Item -LiteralPath $Path -Destination $backup
        Write-Host "[BACKUP] $backup"
    }
}

function Test-RobocopySuccess {
    param([int]$ExitCode)
    # robocopy exit codes 0-7 indicate success (0=no change, 1=files copied, etc.)
    return ($ExitCode -ge 0 -and $ExitCode -le 7)
}

function Move-ConfigItem {
    param([string]$Src, [string]$Dst, [bool]$IsCopy = $false)
    if (-not (Test-Path $Src)) { return $false }
    if (Test-Path $Dst) { return $false }
    $parent = Split-Path $Dst -Parent
    if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    if (Test-Path $Src -PathType Container) {
        if ($IsCopy) {
            $result = robocopy $Src $Dst /E /NFL /NDL /NJH /NJS 2>&1
            if (-not (Test-RobocopySuccess $LASTEXITCODE)) {
                Write-Host "  WARN: robocopy exit code $LASTEXITCODE for $Src"
            }
        } else {
            $result = robocopy $Src $Dst /E /MOVE /NFL /NDL /NJH /NJS 2>&1
            if (-not (Test-RobocopySuccess $LASTEXITCODE)) {
                Write-Host "  WARN: robocopy exit code $LASTEXITCODE for $Src"
            }
            if (Test-Path $Src) { Remove-Item -LiteralPath $Src -Recurse -Force -ErrorAction SilentlyContinue }
        }
    } else {
        if ($IsCopy) { Copy-Item -LiteralPath $Src -Destination $Dst -Force }
        else         { Move-Item -LiteralPath $Src -Destination $Dst -Force }
    }
    return $true
}

function Read-ModEnvironment {
    param([string]$JarPath)
    $env = "?"
    $id  = "?"
    $err = $null
    try {
        $subDir = Join-Path $TempDir ([System.IO.Path]::GetRandomFileName())
        New-Item -ItemType Directory -Path $subDir -Force | Out-Null
        Push-Location $subDir
        # Quote the jar path to handle spaces
        $quotedPath = """$JarPath"""
        $jarResult = cmd /c "jar xf $quotedPath fabric.mod.json 2>nul"
        if (Test-Path "fabric.mod.json") {
            try {
                # Use -Encoding UTF8 to handle non-ASCII characters in JSON
                $json = Get-Content "fabric.mod.json" -Raw -Encoding UTF8 | ConvertFrom-Json
                $env = if ($json.environment) { $json.environment } else { "*" }
                $id  = $json.id
            } catch {
                $env = "parse_err"
                $err = "JSON parse failed: $($_.Exception.Message)"
            }
        } else {
            # No fabric.mod.json — treat as unknown (likely dual)
            $env = "no_meta"
        }
        Pop-Location
        Remove-Item -LiteralPath $subDir -Recurse -Force -ErrorAction SilentlyContinue
    } catch {
        $env = "jar_err"
        $err = "jar extract failed: $($_.Exception.Message)"
    }
    return @{ Env = $env; Id = $id; Error = $err }
}

# 自动匹配：在 host-config 中查找与 mod_id 相关的配置（文件或文件夹）
# 使用 mod_id 全名 + 拆分片段做关键词匹配，最短片段 5 字符，避免过度泛化
function Find-ConfigMatches {
    param([string]$ModId)
    $result = @()
    if (-not $ModId -or $ModId -eq "?") { return $result }

    $MIN_FRAGMENT_LEN = 5
    $BLACKLIST = @("config", "server", "client", "fabric", "common", "library", "loader", "script")

    # 构建去重的关键词列表
    $seen = @{}
    $kwlist = @()
    $str = [string]$ModId
    if (-not $seen.ContainsKey($str)) { $seen[$str] = $true; $kwlist += $str }
    $raw = [string]$ModId -split '[-_]'
    foreach ($r in $raw) {
        $s = [string]$r
        if ($s.Length -lt $MIN_FRAGMENT_LEN) { continue }
        if ($s -in $BLACKLIST) { continue }
        if (-not $seen.ContainsKey($s)) { $seen[$s] = $true; $kwlist += $s }
    }

    $hostItems = @(Get-ChildItem $HostConfigDir -Name -ErrorAction SilentlyContinue | ForEach-Object { [string]$_ })

    foreach ($kw in $kwlist) {
        $kl = [string]$kw.ToLower()
        foreach ($it in $hostItems) {
            $iname = [string]$it
            if ($iname.ToLower() -match [regex]::Escape($kl)) {
                if ($iname -notin $result) { $result += $iname }
            }
        }
    }
    return $result
}
# ============================================================
# 前置检查
# ============================================================
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Minecraft MOD 分类管理脚本 v2" -ForegroundColor Cyan
Write-Host "  实例: $BaseDir" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

# Check java
$javaCheck = Get-Command java -ErrorAction SilentlyContinue
if (-not $javaCheck) {
    Write-Host "ERROR: java 不在 PATH 中，需要 JDK 来读取 fabric.mod.json。" -ForegroundColor Red
    exit 1
}

# Check mods directory
if (-not (Test-Path $ModsDir)) {
    Write-Host "ERROR: 未找到 mods/ 目录。" -ForegroundColor Red
    exit 1
}

# Check automodpack-server.json
if (-not (Test-Path $AmServerJson)) {
    Write-Host "WARN: 未找到 automodpack-server.json，将跳过 AutoModpack 配置更新。" -ForegroundColor Yellow
}

Remove-Item $TempDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $TempDir -Force | Out-Null
New-Item -ItemType Directory -Path $ClientModsDir -Force | Out-Null
New-Item -ItemType Directory -Path $ClientCfgDir  -Force | Out-Null
New-Item -ItemType Directory -Path $HostConfigDir -Force | Out-Null

if (Test-Path $AmServerJson) {
    Backup-File $AmServerJson
}

# ============================================================
# 主流程
# ============================================================

# ---- 1. 扫描 ----
Write-Host "`n[1/5] 扫描 mods/ ..."
$allMods = Get-ChildItem $ModsDir -Filter "*.jar" -File -ErrorAction SilentlyContinue
if (-not $allMods) { Write-Host "ERROR: 未找到 MOD。"; exit 1 }
Write-Host "  共 $($allMods.Count) 个 JAR"

$mods = @()
$parseErrors = @()
foreach ($m in $allMods) {
    $info = Read-ModEnvironment $m.FullName
    $mods += [PSCustomObject]@{ Name=$m.Name; FullName=$m.FullName; Env=$info.Env; Id=$info.Id }
    if ($info.Error) {
        $parseErrors += "$($m.Name): $($info.Error)"
    }
}

# 分类统计
$clientOnly = $mods | Where-Object { $_.Env -eq "client" }
$dual       = $mods | Where-Object { $_.Env -eq "*" }
$serverOnly = $mods | Where-Object { $_.Env -eq "server" }
# 无法确认的 MOD: parse_err / no_meta / jar_err / ?
$unknown    = $mods | Where-Object { $_.Env -notin @("client","*","server") }

Write-Host "  client-only : $($clientOnly.Count)"
Write-Host "  dual (*)    : $($dual.Count)"
Write-Host "  server-only : $($serverOnly.Count)"
if ($unknown.Count -gt 0) {
    Write-Host "  UNKNOWN     : $($unknown.Count)" -ForegroundColor Yellow
    foreach ($u in $unknown) {
        Write-Host "    $($u.Name) [env=$($u.Env)]" -ForegroundColor Yellow
    }
}

if ($parseErrors.Count -gt 0) {
    Write-Host "`n  解析警告:" -ForegroundColor Yellow
    foreach ($e in $parseErrors) {
        Write-Host "    $e" -ForegroundColor Yellow
    }
}

if ($clientOnly) {
    Write-Host "  client IDs  : $($clientOnly.Id -join ', ')"
}
if ($dual) {
    Write-Host "  dual IDs    : $($dual.Id -join ', ')"
}

# ---- 2. 移动 client-only ----
Write-Host "`n[2/5] 移动纯客户端 MOD -> client-mods/ ..."
$clientMoved = @()
foreach ($m in $clientOnly) {
    $dst = Join-Path $ClientModsDir $m.Name
    if (-not (Test-Path $dst)) {
        Move-Item -LiteralPath $m.FullName -Destination $dst -Force
        Write-Host "  MOVED  $($m.Name)"
        $clientMoved += $m
    } else {
        # Check if existing is the same size (likely same MOD, skip)
        $srcSize = (Get-Item -LiteralPath $m.FullName).Length
        $dstSize = (Get-Item -LiteralPath $dst).Length
        if ($srcSize -eq $dstSize) {
            Write-Host "  SKIP   $($m.Name) (same size, exists)"
        } else {
            Write-Host "  CONFLICT $($m.Name) (dst exists with different size — skip, manual check needed)" -ForegroundColor Yellow
        }
    }
}

# ---- 3. 复制 dual ----
Write-Host "`n[3/5] 复制双端 MOD -> client-mods/ ..."
$dualCopied = @()
foreach ($m in $dual) {
    $dst = Join-Path $ClientModsDir $m.Name
    if (-not (Test-Path $dst)) {
        Copy-Item -LiteralPath $m.FullName -Destination $dst -Force
        Write-Host "  COPIED $($m.Name)"
        $dualCopied += $m
    } else {
        $srcSize = (Get-Item -LiteralPath $m.FullName).Length
        $dstSize = (Get-Item -LiteralPath $dst).Length
        if ($srcSize -eq $dstSize) {
            Write-Host "  SKIP   $($m.Name) (same size, exists)"
        } else {
            Write-Host "  CONFLICT $($m.Name) (dst exists with different size — skip, manual check needed)" -ForegroundColor Yellow
        }
    }
}

# ---- 4. 迁移配置（自动匹配）----
Write-Host "`n[4/5] 迁移配置文件（自动匹配）..."
$allProcessed = $clientMoved + $dualCopied
$cfgMoved = 0; $cfgSkipped = 0

# Also include any manually-added MODs in client-mods/ for known-pattern building
$manualMods = @()
if (Test-Path $ClientModsDir) {
    $modsDirNames = $mods | ForEach-Object { $_.Name }
    Get-ChildItem $ClientModsDir -Filter "*.jar" -File -ErrorAction SilentlyContinue | ForEach-Object {
        if ($_.Name -notin $modsDirNames) {
            # This is a manually-added client MOD — add to known patterns
            $info = Read-ModEnvironment $_.FullName
            $manualMods += [PSCustomObject]@{ Name=$_.Name; FullName=$_.FullName; Env=$info.Env; Id=$info.Id }
        }
    }
}
if ($manualMods.Count -gt 0) {
    Write-Host "  手动添加的 MOD: $($manualMods.Count) 个"
    $allProcessed += $manualMods
}

$processedIds = [System.Collections.Generic.HashSet[string]]@()

foreach ($m in $allProcessed) {
    $matched = Find-ConfigMatches $m.Id
    $isCopy = ($m.Env -ne "client")
    foreach ($item in $matched) {
        $src = Join-Path $HostConfigDir $item
        $dst = Join-Path $ClientCfgDir $item
        $result = Move-ConfigItem -Src $src -Dst $dst -IsCopy $isCopy
        if ($result) { $cfgMoved++ } else { $cfgSkipped++ }
    }
    [void]$processedIds.Add($m.Id)
}
# 幽灵配置检测（保守模式 — 只报告可疑项，不自动删除）
# 全局扫描 config/、client-config/、host-modpack/config/ 三个目录，
# 列出可能无对应 MOD 的配置项，由用户手动确认后处理。
# host-modpack/config/ 中如 client-config 已有同名副本，标记为"残留"。
Write-Host "  检查幽灵配置（保守模式 — 仅报告，不删除）..."

# 构建全量 MOD 关键词集合（mods/ 中所有已扫描 MOD + client-mods/ 中手动添加的 MOD）
$knownPatterns = @()
# mods/ 中的所有 MOD（来自步骤1扫描结果）
foreach ($m in $mods) {
    $kw = [string]$m.Id
    if ($kw -and $kw -ne "?") { $knownPatterns += $kw }
    $kw -split '[-_]' | Where-Object { $_.Length -gt 4 } | ForEach-Object {
        $s = [string]$_
        if ($s -notin @("config","server","client","fabric","common","library","loader","script")) {
            $knownPatterns += $s
        }
    }
}
# client-mods/ 中手动添加的 MOD（不在 mods/ 中的）
if (Test-Path $ClientModsDir) {
    $modsDirNames = $mods | ForEach-Object { $_.Name }
    Get-ChildItem $ClientModsDir -Filter "*.jar" -File -ErrorAction SilentlyContinue | ForEach-Object {
        if ($_.Name -notin $modsDirNames) {
            $info = Read-ModEnvironment $_.FullName
            $kw = [string]$info.Id
            if ($kw -and $kw -ne "?") { $knownPatterns += $kw }
            $kw -split '[-_]' | Where-Object { $_.Length -gt 4 } | ForEach-Object {
                $s = [string]$_
                if ($s -notin @("config","server","client","fabric","common","library","loader","script")) {
                    $knownPatterns += $s
                }
            }
        }
    }
}
$knownPatterns = $knownPatterns | Select-Object -Unique | ForEach-Object { [string]$_ }

# 检测函数：扫描指定目录，列出可疑项
function Find-GhostConfigs {
    param([string]$DirPath, [string]$DirLabel, [bool]$CheckResidue = $false)
    if (-not (Test-Path $DirPath)) { return }
    Get-ChildItem $DirPath -ErrorAction SilentlyContinue | ForEach-Object {
        $name = $_.Name
        $isKnown = $false
        foreach ($p in $knownPatterns) {
            if ($p.Length -gt 0 -and $name.ToLower() -match [regex]::Escape($p.ToLower())) {
                $isKnown = $true
                break
            }
        }
        if ($isKnown) { return }
        # host-modpack 中：如 client-config 已有同名副本 → 残留
        if ($CheckResidue) {
            $clientCopy = Join-Path $ClientCfgDir $name
            if (Test-Path $clientCopy) {
                $script:suspicious += "[残留] $DirLabel\$name (host 中可安全删除)"
                return
            }
        }
        # 无 MOD 匹配 → 可疑
        $script:suspicious += "[可疑] $DirLabel\$name (无对应 MOD)"
    }
}

$suspicious = @()
Find-GhostConfigs -DirPath $ConfigDir      -DirLabel "config"
Find-GhostConfigs -DirPath $ClientCfgDir   -DirLabel "client-config"
Find-GhostConfigs -DirPath $HostConfigDir  -DirLabel "host-config" -CheckResidue $true

if ($suspicious.Count -gt 0) {
    Write-Host "  发现 $($suspicious.Count) 个可疑/残留配置项（未删除，请手动确认）：" -ForegroundColor Yellow
    foreach ($s in $suspicious) {
        Write-Host "    $s" -ForegroundColor Yellow
    }
    Write-Host "  如需清理，手动删除后重新运行本脚本。" -ForegroundColor Yellow
} else {
    Write-Host "  未发现可疑配置。" -ForegroundColor Green
}
Write-Host "`n[5/5] 更新 AutoModpack 配置 ..."
if (Test-Path $AmServerJson) {
    try {
        $am = Get-Content $AmServerJson -Raw -Encoding UTF8 | ConvertFrom-Json

        # Only overwrite syncedFiles and allowEditsInFiles — preserve other fields
        $am.syncedFiles = @(
            "!/kubejs/server_scripts/**",
            "/mods/*.jar",
            "/client-mods/*",
            "/config/**",
            "/client-config/**",
            "/kubejs/**",
            "/emotes/*"
        )
        $am.allowEditsInFiles = @(
            "/config/**",
            "/client-config/**",
            "/mods/*",
            "/client-mods/*",
            "/resourcepacks/*",
            "/shaderpacks/*"
        )
        $am | ConvertTo-Json -Depth 4 | Set-Content $AmServerJson -Encoding UTF8
        Write-Host "  已更新"
    } catch {
        Write-Host "  ERROR: 更新 automodpack-server.json 失败: $($_.Exception.Message)" -ForegroundColor Red
    }
} else {
    Write-Host "  跳过（文件不存在）" -ForegroundColor Yellow
}

# ============================================================
# 报告
# ============================================================
Write-Host "`n============================================" -ForegroundColor Green
Write-Host "  完成" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host "  mods/          : $((Get-ChildItem $ModsDir -Filter *.jar -File -ErrorAction SilentlyContinue).Count) JARs"
Write-Host "  client-mods/   : $((Get-ChildItem $ClientModsDir -Filter *.jar -File -ErrorAction SilentlyContinue).Count) JARs"
Write-Host "  config/        : $((Get-ChildItem $ConfigDir -Recurse -File -ErrorAction SilentlyContinue).Count) files"
Write-Host "  client-config/ : $((Get-ChildItem $ClientCfgDir -Recurse -File -ErrorAction SilentlyContinue).Count) files"

if ($unknown.Count -gt 0) {
    Write-Host "`n  WARNING: $($unknown.Count) MOD 无法确定分类（见上方 UNKNOWN 列表）。" -ForegroundColor Yellow
    Write-Host "  这些 MOD 保留在 mods/ 中，未分发。请手动检查。" -ForegroundColor Yellow
}

if ($parseErrors.Count -gt 0) {
    Write-Host "  WARNING: $($parseErrors.Count) MOD 解析出错（见上方警告）。" -ForegroundColor Yellow
}

Write-Host "  AutoModpack content.json 将在服务器下次启动时重新生成。"

Remove-Item $TempDir -Recurse -Force -ErrorAction SilentlyContinue
