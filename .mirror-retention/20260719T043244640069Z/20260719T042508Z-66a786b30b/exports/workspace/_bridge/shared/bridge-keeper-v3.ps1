# Bridge Keeper v3 — WriteConsoleInput 方式注入回车到 Codex 控制台
# 正确的控制台输入注入方式，Codex 的 TUI (ratatui) 通过 stdin 读取
# 需要知道 Codex 的进程 PID

param(
    [int]$CodexPID = 0
)

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class ConsoleAPI {
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool AttachConsole(int dwProcessId);
    
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool FreeConsole();
    
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern IntPtr GetStdHandle(int nStdHandle);
    
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool WriteConsoleInput(IntPtr hConsoleInput, INPUT_RECORD[] lpBuffer, int nLength, out int lpNumberOfEventsWritten);
    
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern int GetConsoleProcessList(int[] lpdwProcessIds, int dwProcessCount);
    
    public const int STD_INPUT_HANDLE = -10;
    public const int KEY_EVENT = 0x0001;
    
    [StructLayout(LayoutKind.Sequential)]
    public struct KEY_EVENT_RECORD {
        public bool bKeyDown;
        public short wRepeatCount;
        public short wVirtualKeyCode;
        public short wVirtualScanCode;
        public char UnicodeChar;
        public int dwControlKeyState;
    }
    
    [StructLayout(LayoutKind.Explicit)]
    public struct INPUT_RECORD {
        [FieldOffset(0)] public int EventType;
        [FieldOffset(4)] public KEY_EVENT_RECORD KeyEvent;
    }
}
"@

# Find Codex PID if not specified
if ($CodexPID -eq 0) {
    $codexProcs = Get-Process -Name "codex" -ErrorAction SilentlyContinue
    if (-not $codexProcs) {
        Write-Host "No codex.exe process found" -ForegroundColor Red
        exit 1
    }
    # Pick the one that has a main window (the CLI/TUI process)
    $main = $codexProcs | Where-Object { $_.MainWindowHandle -ne 0 } | Sort-Object WorkingSet64 -Descending | Select-Object -First 1
    if (-not $main) { $main = $codexProcs | Sort-Object WorkingSet64 -Descending | Select-Object -First 1 }
    $CodexPID = $main.Id
}

Write-Host "Bridge Keeper v3 — Console Input mode" -ForegroundColor Green
Write-Host "Attaching to Codex PID: $CodexPID" -ForegroundColor Cyan

$keyboardHandle = [IntPtr]::Zero

# Attach to Codex's console and get its input handle
[ConsoleAPI]::FreeConsole() | Out-Null
if (-not [ConsoleAPI]::AttachConsole($CodexPID)) {
    Write-Host "ERROR: Cannot attach to Codex console (PID $CodexPID). Error: $([Runtime.InteropServices.Marshal]::GetLastWin32Error())" -ForegroundColor Red
    Write-Host "Make sure Codex is running in a console window." -ForegroundColor Yellow
    exit 1
}

$keyboardHandle = [ConsoleAPI]::GetStdHandle([ConsoleAPI]::STD_INPUT_HANDLE)
if ($keyboardHandle -eq [IntPtr]::Zero -or $keyboardHandle -eq [IntPtr](-1)) {
    Write-Host "ERROR: Cannot get console input handle" -ForegroundColor Red
    [ConsoleAPI]::FreeConsole() | Out-Null
    exit 1
}

Write-Host "Console attached. Sending Enter every 15s. Ctrl+C to stop." -ForegroundColor Green

$enterKeyDown = New-Object ConsoleAPI+INPUT_RECORD
$enterKeyDown.EventType = [ConsoleAPI]::KEY_EVENT
$enterKeyDown.KeyEvent.bKeyDown = $true
$enterKeyDown.KeyEvent.wVirtualKeyCode = 0x0D  # VK_RETURN
$enterKeyDown.KeyEvent.UnicodeChar = [char]0x0D
$enterKeyDown.KeyEvent.dwControlKeyState = 0

$enterKeyUp = New-Object ConsoleAPI+INPUT_RECORD
$enterKeyUp.EventType = [ConsoleAPI]::KEY_EVENT
$enterKeyUp.KeyEvent.bKeyDown = $false
$enterKeyUp.KeyEvent.wVirtualKeyCode = 0x0D
$enterKeyUp.KeyEvent.UnicodeChar = [char]0x0D
$enterKeyUp.KeyEvent.dwControlKeyState = 0

$written = 0
$count = 0

while ($true) {
    # Send Enter down + up
    [ConsoleAPI]::WriteConsoleInput($keyboardHandle, @($enterKeyDown), 1, [ref]$written) | Out-Null
    Start-Sleep -Milliseconds 60
    [ConsoleAPI]::WriteConsoleInput($keyboardHandle, @($enterKeyUp), 1, [ref]$written) | Out-Null
    
    $count++
    Write-Host "[$count] Enter injected at $(Get-Date -Format 'HH:mm:ss')" -ForegroundColor DarkGray
    
    Start-Sleep -Seconds 15
}
