# Bridge Keeper v2 — 使用 Win32 PostMessage 直接向 Codex 窗口发送回车
# 不需要窗口焦点，后台可靠运行

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32 {
    [DllImport("user32.dll")]
    public static extern IntPtr FindWindow(string lpClassName, string lpWindowName);
    
    [DllImport("user32.dll")]
    public static extern IntPtr PostMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
    
    [DllImport("user32.dll")]
    public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
    
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    
    [DllImport("user32.dll")]
    public static extern int GetWindowText(IntPtr hWnd, System.Text.StringBuilder text, int count);
    
    [DllImport("user32.dll")]
    public static extern int GetWindowThreadProcessId(IntPtr hWnd, out int processId);
    
    public const uint WM_KEYDOWN = 0x0100;
    public const uint WM_KEYUP = 0x0101;
}
"@

$KEY_ENTER = 0x0D

function Find-CodexWindow {
    # Find all codex.exe processes and their console windows
    $codexProcs = Get-Process -Name "codex" -ErrorAction SilentlyContinue | 
        Where-Object { $_.MainWindowHandle -ne 0 }
    
    if ($codexProcs) {
        # Return the one with the largest working set (likely the main TUI)
        return ($codexProcs | Sort-Object WorkingSet64 -Descending | Select-Object -First 1).MainWindowHandle
    }
    
    # Fallback: scan all top-level windows for Codex
    $result = 0
    $codexPids = (Get-Process -Name "codex" -ErrorAction SilentlyContinue).Id
    $found = $false
    
    $callback = [Win32+EnumWindowsProc]{
        param($hwnd, $lparam)
        $pid = 0
        [Win32]::GetWindowThreadProcessId($hwnd, [ref]$pid)
        if ($pid -in $codexPids) {
            $sb = New-Object System.Text.StringBuilder(256)
            [Win32]::GetWindowText($hwnd, $sb, 256)
            if ($sb.ToString() -match "codex|Codex|mcsmanager") {
                $script:result_hwnd = $hwnd
                return $false
            }
        }
        return $true
    }
    [Win32]::EnumWindows($callback, [IntPtr]::Zero)
    return $script:result_hwnd
}

Write-Host "Bridge Keeper v2 — PostMessage mode" -ForegroundColor Green
Write-Host "Searching for Codex window..." -ForegroundColor Cyan

$hwnd = Find-CodexWindow

if ($hwnd -eq 0) {
    # Wait for Codex to start
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        $hwnd = Find-CodexWindow
        if ($hwnd -ne 0) { break }
        Write-Host "  Waiting for Codex window... ($i)" -ForegroundColor Yellow
    }
}

if ($hwnd -eq 0) {
    Write-Host "ERROR: Cannot find Codex window. Make sure Codex is running." -ForegroundColor Red
    exit 1
}

Write-Host "Codex window found: 0x$($hwnd.ToString('X'))" -ForegroundColor Green
Write-Host "Sending Enter every 15 seconds. Press Ctrl+C to stop." -ForegroundColor Cyan
Write-Host ""

$count = 0
while ($true) {
    # Send Enter keydown + keyup
    [Win32]::PostMessage($hwnd, [Win32]::WM_KEYDOWN, [IntPtr]$KEY_ENTER, [IntPtr]0x001C0001)
    Start-Sleep -Milliseconds 50
    [Win32]::PostMessage($hwnd, [Win32]::WM_KEYUP, [IntPtr]$KEY_ENTER, [IntPtr]0xC01C0001)
    
    $count++
    Write-Host "[$count] Enter sent at $(Get-Date -Format 'HH:mm:ss')" -ForegroundColor DarkGray
    
    Start-Sleep -Seconds 15
}
