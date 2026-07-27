#!/usr/bin/env python3
"""Bridge Keeper — 通过 WriteConsoleInput 向 Codex 控制台注入回车。
每 15 秒触发一次，让 Codex 自动检查 Bridge 任务。
"""
import ctypes
import ctypes.wintypes as w
import subprocess
import time
import os
import sys

kernel32 = ctypes.windll.kernel32

# Win32 types
STD_INPUT_HANDLE = -10
KEY_EVENT = 0x0001
VK_RETURN = 0x0D

class KEY_EVENT_RECORD(ctypes.Structure):
    _fields_ = [
        ("bKeyDown", w.BOOL),
        ("wRepeatCount", w.WORD),
        ("wVirtualKeyCode", w.WORD),
        ("wVirtualScanCode", w.WORD),
        ("uChar", w.WCHAR),
        ("dwControlKeyState", w.DWORD),
    ]

class INPUT_RECORD(ctypes.Structure):
    _fields_ = [
        ("EventType", w.WORD),
        ("KeyEvent", KEY_EVENT_RECORD),
    ]

def find_codex_pid():
    """Find the main Codex TUI process PID."""
    try:
        result = subprocess.run(
            ['powershell', '-Command',
             '(Get-Process codex | Where-Object {$_.MainWindowHandle -ne 0} | Sort-Object WorkingSet64 -Descending | Select-Object -First 1).Id'],
            capture_output=True, text=True, timeout=10
        )
        pid = result.stdout.strip()
        if pid:
            return int(pid)
    except:
        pass
    return 0

def inject_enter(pid):
    """Inject Enter key into a console process using WriteConsoleInput."""
    # Step 1: Free our own console
    kernel32.FreeConsole()
    
    # Step 2: Attach to Codex's console
    if not kernel32.AttachConsole(pid):
        err = kernel32.GetLastError()
        kernel32.AllocConsole()  # Re-attach to our own
        return False, f"AttachConsole failed (error {err})"
    
    # Step 3: Get Codex's stdin handle
    stdin = kernel32.GetStdHandle(STD_INPUT_HANDLE)
    if stdin == -1:
        kernel32.FreeConsole()
        kernel32.AllocConsole()
        return False, "GetStdHandle failed"
    
    # Step 4: Create and send Enter key event
    enter_down = INPUT_RECORD()
    enter_down.EventType = KEY_EVENT
    enter_down.KeyEvent.bKeyDown = True
    enter_down.KeyEvent.wVirtualKeyCode = VK_RETURN
    enter_down.KeyEvent.uChar = '\r'
    
    enter_up = INPUT_RECORD()
    enter_up.EventType = KEY_EVENT
    enter_up.KeyEvent.bKeyDown = False
    enter_up.KeyEvent.wVirtualKeyCode = VK_RETURN
    enter_up.KeyEvent.uChar = '\r'
    
    records = (INPUT_RECORD * 2)(enter_down, enter_up)
    written = w.DWORD(0)
    
    result = kernel32.WriteConsoleInputW(stdin, records, 2, ctypes.byref(written))
    if not result:
        err = kernel32.GetLastError()
        kernel32.FreeConsole()
        kernel32.AllocConsole()
        return False, f"WriteConsoleInput failed (error {err})"
    
    # Step 5: Detach and restore
    kernel32.FreeConsole()
    kernel32.AllocConsole()
    return True, f"sent ({written.value} events)"

def main():
    print(f"[{time.strftime('%H:%M:%S')}] Bridge Keeper — Python WriteConsoleInput mode")
    print(f"Looking for Codex process...")
    
    pid = 0
    for attempt in range(30):
        pid = find_codex_pid()
        if pid:
            break
        print(f"  Waiting... ({attempt+1})")
        time.sleep(2)
    
    if not pid:
        print("ERROR: Cannot find Codex process. Make sure Codex is running.")
        sys.exit(1)
    
    print(f"Codex PID: {pid}")
    print(f"Sending Enter every 15s. Ctrl+C to stop.\n")
    
    count = 0
    while True:
        ok, msg = inject_enter(pid)
        count += 1
        status = "OK" if ok else "FAIL"
        print(f"[{time.strftime('%H:%M:%S')}] [{count}] {status}: {msg}")
        time.sleep(15)

if __name__ == "__main__":
    main()
