param(
    [string[]]$ProcessName = @("codex", "codex-plus-plus", "python", "node", "java", "powershell"),
    [int]$MaxProcesses = 40,
    [int]$MaxCommandLineChars = 500,
    [switch]$Json
)

$ErrorActionPreference = "Continue"

function Test-IsAdmin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Resolve-CommandPath {
    param([string]$Name)
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $cmd) { return $null }
    return $cmd.Source
}

$processes = @()
foreach ($name in $ProcessName) {
    $escaped = $name.Replace("'", "''")
    $items = Get-CimInstance Win32_Process -Filter "Name LIKE '%$escaped%'" -ErrorAction SilentlyContinue
    foreach ($item in $items) {
        $processes += [pscustomobject]@{
            ProcessId = $item.ProcessId
            ParentProcessId = $item.ParentProcessId
            Name = $item.Name
            ExecutablePath = $item.ExecutablePath
            CommandLine = if ($item.CommandLine -and $item.CommandLine.Length -gt $MaxCommandLineChars) {
                $item.CommandLine.Substring(0, $MaxCommandLineChars) + "...<truncated>"
            } else {
                $item.CommandLine
            }
        }
    }
}

$processes = $processes |
    Sort-Object Name, ProcessId -Unique |
    Select-Object -First $MaxProcesses

$result = [pscustomobject]@{
    Timestamp = (Get-Date).ToString("o")
    User = [Environment]::UserName
    ComputerName = [Environment]::MachineName
    IsAdmin = Test-IsAdmin
    PowerShell = [pscustomobject]@{
        Version = $PSVersionTable.PSVersion.ToString()
        Edition = $PSVersionTable.PSEdition
        Host = $Host.Name
        OutputEncoding = [Console]::OutputEncoding.WebName
        CodePage = (chcp 2>$null)
    }
    Paths = [pscustomobject]@{
        powershell = Resolve-CommandPath "powershell"
        pwsh = Resolve-CommandPath "pwsh"
        python = Resolve-CommandPath "python"
        node = Resolve-CommandPath "node"
        npm = Resolve-CommandPath "npm"
        git = Resolve-CommandPath "git"
        java = Resolve-CommandPath "java"
        gradle = Resolve-CommandPath "gradle"
    }
    Environment = [pscustomobject]@{
        PathHead = (($env:Path -split ';') | Select-Object -First 12)
        JAVA_HOME = $env:JAVA_HOME
        PYTHONUTF8 = $env:PYTHONUTF8
        TEMP = $env:TEMP
    }
    Processes = $processes
}

if ($Json) {
    $result | ConvertTo-Json -Depth 6
} else {
    $result | Format-List
}
