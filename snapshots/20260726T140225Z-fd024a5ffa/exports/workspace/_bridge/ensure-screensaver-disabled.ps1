$ErrorActionPreference = 'Stop'

function Set-RegValue {
    param(
        [string]$Path,
        [string]$Name,
        [string]$Value
    )
    if (-not (Test-Path $Path)) {
        New-Item -Path $Path -Force | Out-Null
    }
    Set-ItemProperty -Path $Path -Name $Name -Value $Value
}

function Clear-LockscreenSection {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return }
    $content = Get-Content -LiteralPath $Path -Raw
    if ($content -notmatch '(?mi)^\[lockscreen\]') { return }
    $newContent = [regex]::Replace($content, '(?ms)^\[lockscreen\]\s*(?:[^\[]*\r?\n?)*', '')
    if ($newContent -ne $content) {
        Set-Content -LiteralPath $Path -Value $newContent -Encoding UTF8
    }
}

$reg = 'HKCU:\Control Panel\Desktop'
Set-RegValue $reg ScreenSaveActive '0'
Set-RegValue $reg ScreenSaveTimeOut '0'
Set-RegValue $reg ScreenSaverIsSecure '0'
Set-RegValue $reg SCRNSAVE.EXE ''

Clear-LockscreenSection 'C:\Users\45543\AppData\Roaming\cdheheyoudun\config.ini'
Clear-LockscreenSection 'C:\Users\45543\AppData\Roaming\tjch\config.ini'
