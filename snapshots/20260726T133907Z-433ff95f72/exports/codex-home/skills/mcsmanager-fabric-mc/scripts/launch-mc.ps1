<#
.SYNOPSIS
  通用 Minecraft Fabric 客户端启动器 — 自动处理存档名特殊字符、重名检测、UUID 自动解析
.DESCRIPTION
  参数化启动任意 HMCL 管理的 Fabric 实例，支持单人存档、多人服务器、主菜单三种模式。
  存档名包含括号等特殊字符时自动重命名为临时名→启动→退出后还原。
  存档名冲突时自动检测并生成不重名的临时名。
  无需 GUI 操作，全程命令行自动化。

.PARAMETER instanceDir
  HMCL 实例目录（如 versions/3c3u），必填。
.PARAMETER javaPath
  javaw.exe 或 java.exe 完整路径，默认自动查找 LibericaJDK-25。
.PARAMETER minecraftDir
  .minecraft 根目录，默认从 instanceDir 向上两级推导。
.PARAMETER saveName
  单人存档文件夹名。省略则进入主菜单。
.PARAMETER username
  玩家名，默认从 instanceDir 的 usercache.json 取第一个有效用户。
.PARAMETER server
  多人服务器地址（host:port 格式）。与 -saveName 互斥。
.PARAMETER ram
  分配内存，默认 4G。
.PARAMETER noCleanup
  调试用：退出后不还原存档原名。

.EXAMPLE
  # 单人存档（自动处理括号）
  .\launch-mc.ps1 -instanceDir "C:\...\versions\3c3u" -saveName "新的世界 (2)"

  # 多人服务器
  .\launch-mc.ps1 -instanceDir "C:\...\versions\3c3u" -server "localhost:25565" -username lsd985211

  # 只启动到主菜单
  .\launch-mc.ps1 -instanceDir "C:\...\versions\3c3u"
#>

param(
    [Parameter(Mandatory=$true)]
    [string]$instanceDir,

    [string]$javaPath = "C:\Program Files\BellSoft\LibericaJDK-25\bin\javaw.exe",

    [string]$minecraftDir,

    [string]$saveName,

    [string]$username,

    [string]$server,

    [string]$ram = "4G",

    [switch]$noCleanup
)

$ErrorActionPreference = "Continue"

# === 路径推断 ===
$instanceDir = (Resolve-Path $instanceDir).Path
$versionName = Split-Path $instanceDir -Leaf

if (-not $minecraftDir) {
    $minecraftDir = Split-Path (Split-Path $instanceDir -Parent) -Parent
}

# Java 路径检查
if (-not (Test-Path $javaPath)) {
    # 尝试查找任何可用的 Java
    $candidates = @(
        "C:\Program Files\BellSoft\LibericaJDK-25\bin\javaw.exe",
        "C:\Program Files\BellSoft\LibericaJDK-21\bin\javaw.exe"
    )
    $javaPath = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $javaPath) {
        Write-Error "No Java found. Set -javaPath manually."
        exit 1
    }
}

# === UUID 自动解析 ===
if (-not $username) {
    $cacheFile = "$minecraftDir\usercache.json"
    if (Test-Path $cacheFile) {
        try {
            $cache = Get-Content $cacheFile -Raw -Encoding UTF8 | ConvertFrom-Json
            $firstValid = $cache | Where-Object { $_.expiresOn } | Sort-Object { [DateTime]$_.expiresOn } -Descending | Select-Object -First 1
            if ($firstValid) {
                $username = $firstValid.name
                $uuid = $firstValid.uuid
            }
        } catch {
            Write-Warning "Failed to parse usercache.json, using defaults"
        }
    }
    if (-not $username) {
        $username = "Player"
        $uuid = "00000000-0000-0000-0000-000000000000"
    }
}

# 如果用户指定了 username 但没指定 uuid，从缓存中匹配
if (-not $uuid) {
    if (Test-Path $cacheFile) {
        try {
            $cache = Get-Content $cacheFile -Raw -Encoding UTF8 | ConvertFrom-Json
            $match = $cache | Where-Object { $_.name -eq $username } | Select-Object -First 1
            if ($match) { $uuid = $match.uuid }
        } catch {}
    }
    if (-not $uuid) { $uuid = "00000000-0000-0000-0000-000000000000" }
}

# === 读取实例 JSON 获取版本元数据 ===
$versionJsonPath = "$instanceDir\$versionName.json"
if (-not (Test-Path $versionJsonPath)) {
    Write-Error "Version JSON not found: $versionJsonPath"
    exit 1
}
try {
    $versionJson = Get-Content $versionJsonPath -Raw -Encoding UTF8 | ConvertFrom-Json
} catch {
    Write-Error "Failed to parse version JSON: $versionJsonPath"
    exit 1
}

$assetsIndex = $versionJson.assetIndex.id ?? "30"
$assetsDir = "$minecraftDir\assets"
$mainClass = $versionJson.mainClass
$nativesDir = "$instanceDir\natives-windows-x86_64"
$type = $versionJson.type ?? "release"

# === 构建 classpath（从 cp-xxx.txt 或从 libraries 目录动态构建）===
$versionCpFile = "$PSScriptRoot\cp-$versionName.txt"
if (Test-Path $versionCpFile) {
    $classpath = (Get-Content $versionCpFile | Where-Object { $_ -ne "" } | ForEach-Object { $_.Trim() }) -join ";"
    "Using pre-built classpath from cp-$versionName.txt"
}
else {
    # 从 libraries 目录动态收集
    $libsDir = "$minecraftDir\libraries"
    $allJars = Get-ChildItem -Path $libsDir -Recurse -Filter "*.jar" -ErrorAction SilentlyContinue
    $classpath = ($allJars.FullName + "$instanceDir\$versionName.jar") -join ";"
    "Dynamically built classpath: $($allJars.Count) libraries + version JAR"
}

# === 杀死旧 Java 进程 ===
Get-Process -Name "java","javaw" -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 1

# === JVM 参数 ===
$jvmArgs = [System.Collections.ArrayList]@(
    "-Xmx$ram",
    "-XX:+UseZGC",
    "-XX:+ZGenerational",
    "-Djava.library.path=$nativesDir",
    "-Djna.tmpdir=$nativesDir",
    "-Dorg.lwjgl.system.SharedLibraryExtractPath=$nativesDir",
    "-Dio.netty.native.workdir=$nativesDir",
    "-Dminecraft.launcher.brand=HMCL",
    "-Dminecraft.launcher.version=3.14.1",
    "-Dlog4j.configurationFile=$instanceDir\log4j2.xml",
    "-cp", $classpath,
    $mainClass,
    "--username", $username,
    "--version", $versionName,
    "--gameDir", $instanceDir,
    "--assetsDir", $assetsDir,
    "--assetIndex", $assetsIndex,
    "--uuid", $uuid,
    "--accessToken", "0",
    "--versionType", $type
)

# === 存档名特殊字符处理 ===
$savesDir = "$instanceDir\saves"
$tempName = $null
$origName = $null
$launchMode = "menu"

if ($saveName -and $server) {
    Write-Warning "Both -saveName and -server specified. Using -server."
    $saveName = $null
}

if ($server) {
    $launchMode = "multiplayer"
    $jvmArgs.Add("--server") | Out-Null
    $jvmArgs.Add($server) | Out-Null
    "Launching multiplayer → $server"
}
elseif ($saveName) {
    $launchMode = "singleplayer"
    $origName = $saveName
    $targetSave = "$savesDir\$saveName"

    if (-not (Test-Path $targetSave)) {
        Write-Error "Save not found: $targetSave"
        exit 1
    }

    # 检查存档名是否包含特殊字符
    $hasSpecialChars = $saveName -match '[()（）]'

    if ($hasSpecialChars) {
        # 去括号，清理空格
        $sanitized = $saveName -replace '[()（）]', '' -replace '\s+', ' ' -replace ' $', '' -replace '^ ', ''

        # 检测与已有存档重名
        if (Test-Path "$savesDir\$sanitized") {
            # 追加数字后缀直到不重名
            $counter = 1
            while (Test-Path "$savesDir\${sanitized}_$counter") { $counter++ }
            $tempName = "${sanitized}_$counter"
        }
        else {
            $tempName = $sanitized
        }

        Rename-Item $targetSave "$savesDir\$tempName"
        "Renamed '$saveName' → '$tempName' (due to special chars in save name)"
        $jvmArgs.Add("--quickPlaySingleplayer") | Out-Null
        $jvmArgs.Add($tempName) | Out-Null
    }
    else {
        # 无特殊字符，直接使用
        $jvmArgs.Add("--quickPlaySingleplayer") | Out-Null
        $jvmArgs.Add($saveName) | Out-Null
        "Launching singleplayer → $saveName"
    }
}
else {
    "Launching to main menu (no save/server specified)"
}

# === 启动 ===
$proc = Start-Process -FilePath $javaPath -ArgumentList $jvmArgs -WorkingDirectory $instanceDir -PassThru
"Launched $versionName [PID $($proc.Id)] [UUID $uuid] [User $username]"

# === 等待游戏退出 ===
$proc.WaitForExit()
"Game exited (exit code: $($proc.ExitCode))."

# === 还原存档名 ===
if ($tempName -and (Test-Path "$savesDir\$tempName") -and !$noCleanup) {
    Rename-Item "$savesDir\$tempName" "$savesDir\$origName"
    "Restored '$tempName' → '$origName'"
}
