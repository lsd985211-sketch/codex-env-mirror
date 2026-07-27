# GUI Automation Source Notes

Use these sources when refreshing GUI automation strategy:

- Microsoft UI Automation overview: https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-uiautomationoverview
- Microsoft UI Automation control patterns: https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-controlpatternsoverview
- pywinauto documentation: https://pywinauto.readthedocs.io/
- PyAutoGUI documentation: https://pyautogui.readthedocs.io/
- PaddleOCR documentation: https://paddlepaddle.github.io/PaddleOCR/main/en/index.html

Operational interpretation:

- UI Automation is the preferred desktop layer when controls expose names, AutomationIds, control types, or patterns.
- pywinauto is useful for Windows GUI control access and keyboard/mouse execution.
- PyAutoGUI is best kept as a low-level keyboard/mouse/screenshot/image fallback.
- OCR is a fallback for visual-only controls and should not replace available accessibility data.
- Coordinates are the last resort and must be backed by screenshot evidence.
