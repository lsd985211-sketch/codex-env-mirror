# Lessons

Reusable Notepad GUI automation causes and patterns from verified successes and failures.

## Root Causes Behind Success

- Foreground-sensitive actions succeed when activation, focus, and typing/clicking happen in one bounded action.
- Disk readback is the strongest proof for Notepad save and Markdown formatting workflows.
- Clipboard operations are reliable when verified through both clipboard content and document UIA text.
- Exact UIA names are safer than AutomationIds when a single AutomationId is reused by multiple controls.
- Launching Notepad with a file path is more stable than driving the in-app Open dialog.

## Root Causes Behind Failure

- Codex Desktop can steal foreground between turns, invalidating split activate-then-type flows.
- Global UIA searches can match stale Notepad tabs, other apps, or previous test controls; constrain selectors to the bound window rectangle when possible.
- A clickable control is not proof of success. Require a changed UI state, clipboard state, or file output.
- Some Notepad menu items expose no stable UIA toggle state, so visual-only toggles should remain unverified.
- Some Markdown toolbar buttons are clickable but did not modify saved file content in the tested environment.

## Promotion Rule For Future Notepad Work

Move a workflow from `candidate-unverified.md` to `verified-success.md` only after:
1. Running it on a temporary or explicitly approved file/window.
2. Capturing the exact selector path or UIA name used.
3. Verifying the result through UIA/OCR/clipboard/disk.
4. Recording the failure boundary and safer fallback.

Move a workflow to `failed-or-avoid.md` when:
1. It repeatedly clicks the intended control but produces no result proof.
2. It risks closing, overwriting, or changing the wrong document.
3. It has a safer verified replacement.
