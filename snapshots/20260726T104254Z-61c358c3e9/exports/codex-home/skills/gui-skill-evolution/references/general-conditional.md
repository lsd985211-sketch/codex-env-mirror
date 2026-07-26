# General Conditional

Reusable GUI patterns that work only under explicit conditions.

Use these when an unfamiliar app matches the listed preconditions. If a condition is absent or uncertain, treat the pattern as a candidate, not trusted.

Each entry should include:

- Pattern: the reusable GUI action or selection strategy.
- Conditions: exact app/window/control conditions required.
- Successful scenes: where it worked.
- Failed scenes: where it failed and why.
- Verification: durable proof required after use.
- Fallback: safer route when conditions do not hold.

## WinUI/UIA Control Pattern Route

- Pattern: when a Windows app exposes WinUI or standard UI Automation controls,
  select controls by UIA name, AutomationId, control type, and supported
  patterns before using OCR or coordinates.
- Conditions: `gui_inspect_window` or `gui_find_element` returns meaningful
  controls or patterns for the target action.
- Successful scenes: broadly consistent with local Notepad/file picker work;
  imported as a conditional pattern after reviewing the official `winui-app`
  skill category, not as a direct app workflow.
- Failed scenes: owner-drawn/self-drawn chat surfaces such as Windows Weixin can
  expose only weak top-level UIA data, making OCR or screenshot-backed
  coordinates necessary.
- Verification: after the UIA action, verify a visible state change, enabled
  state, selected item, text field value, file output, or other durable result.
- Fallback: use app-specific routes, scoped OCR, image matching, then
  screenshot-backed coordinates.
