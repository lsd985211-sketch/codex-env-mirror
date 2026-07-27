---
name: gui-automation
description: Windows GUI automation operating discipline for Codex. Use when Codex needs to control desktop apps, inspect windows, click/type/select controls, recover from GUI failures, use OCR or screenshots, tune GUI speed and recognition precision, or decide between UI Automation, DOM, OCR, image matching, and coordinate-based actions.
---

# GUI Automation

## Role Boundaries

- Use this skill for native Windows desktop GUI automation and for choosing the safest desktop automation layer.
- Use it when the target is not well-served by a browser DOM route or when the task involves native windows, file pickers, dialogs, OCR, UIA, or coordinate fallbacks.
- Do not use it as the first choice for ordinary browser DOM automation or local webapp testing when Playwright-style tooling can see the target directly.

## Handoff Rules

- **Native desktop windows, dialogs, file pickers, OCR/UIA flows**: stay in this skill.
- **Ordinary browser DOM automation against a known page**: hand off to `playwright`.
- **Local webapp testing workflow with app-server coordination**: hand off to `webapp-testing`.
- **Existing Chrome session/tab/cookie/extension state**: hand off to `agent-browser`.
- **Image-file editing, OCR, annotation, or object detection on already-captured files**:
  prefer direct image-processing tools or scripts over desktop GUI control. Use
  GUI automation only to acquire the image, operate an app that has no file/API
  route, or verify a visible UI result.

## Operating Model

Use the smallest reliable automation layer:

1. Direct API, CLI, file format, or app protocol when available.
2. Browser DOM or app-specific automation when the target is web-based.
3. Playwright/DOM-style automation for browser-backed or Electron surfaces
   when it can inspect elements directly.
4. Windows UI Automation controls and patterns, including WinUI control
   patterns when exposed.
5. OCR or image matching for visual-only surfaces.
6. Coordinates only after screenshot evidence confirms target geometry.

For Windows desktop work, prefer `mcp__gui_automation` tools. Use `gui_list_windows`, then bind a session with `gui_ensure_window` or `gui_inspect_window`, then operate through one observe-plan-act-verify step at a time.

For browser, Electron, or webview-like windows, first decide whether a DOM or
Playwright route can see and act on the relevant elements. Use desktop GUI
tools only for the native shell, login prompts, file pickers, permission
dialogs, or browser surfaces that do not expose a usable DOM path.

For already-captured image files, separate image processing from GUI control.
Use direct file/image operations for metadata, crop, resize, rotate, blur,
fill, overlay, watermark, draw shapes/text, OCR, and model-based detection
when a safe local tool exists. Treat image edits as file transformations with
input and output paths, not as GUI clicks. Use absolute paths, preserve the
original file unless the user explicitly asked to overwrite it, and verify the
output by reading metadata, dimensions, OCR text, or a rendered preview.
Represent rectangular regions as `[x1, y1, x2, y2]` in image pixel coordinates
and state whether the coordinates are image-relative, window-relative, or
screen-relative before using them.

When UI Automation controls are incomplete, use OCR as the next fallback: `gui_find_text_ocr` to locate visible text, then `gui_click_text` only after screenshot evidence confirms that the matched text is the intended target. Prefer OCR over coordinates when text is visible and the region can be bounded. If an action fails, call `gui_failure_report` before retrying so the next attempt uses the last screenshot, UIA candidates, and checkpoint rather than repeating the same failed click.

For speed and precision, prefer cached UIA selectors within a short TTL, keep OCR on the persistent worker path, and scope OCR to the smallest safe region. Use full-window OCR only for initial discovery, unknown layouts, or when no safe region can be inferred from screenshot/UIA evidence. Once a toolbar, dialog, list, canvas, or content band is located, pass a `region` for follow-up OCR and clicks. OCR worker failures should fall back to one-shot OCR or CPU fallback rather than failing the whole GUI action.

For mature, verified, low-risk workflows, use a bounded fixed flow instead of
round-tripping every tiny action through the main reasoning loop. Keep
decision points visible: capture before the flow starts when identity or target
matters, capture before irreversible submit/send actions, and capture after the
flow to verify the durable result. If any step in the fixed flow fails,
immediately stop the batch and downgrade to single-step observe-plan-act-verify
diagnosis. Do not continue a fixed flow after an unexpected screen, stale
window, disabled control, modal, or failed verification.

Use high-level tools for any mature high-frequency workflow, not just one app.
A workflow is eligible when it is repeated often, has stable intermediate
steps, has explicit preconditions, can expose the failed stage, and can verify
the final state before any irreversible action. Keep high-level tools narrow:
batch only the stable middle segment, leave identity checks and destructive or
representational submit/send actions outside unless those boundaries are also
separately verified and approved.

Before choosing strict mode or fast mode, separate relevant variables from
irrelevant variables. Relevant variables can change correctness, safety,
coordinates, selectors, target identity, input destination, or final result:
window handle/size, foreground focus, target app/account/chat/file, modal
state, enabled/disabled buttons, current field focus, and current output state.
Irrelevant variables are visual or textual details outside the action path that
do not affect the next control or verification. Do not inspect irrelevant
variables in mature fast flows. If a variable's relevance is uncertain, treat
it as relevant until one verified run proves otherwise.

Reuse a verified stable main-window session inside one bounded workflow instead
of repeatedly calling `gui_ensure_window` or `gui_recover_session` before every
minor action. Reuse is allowed only after the window identity and target state
have been verified by fresh evidence. Temporary dialogs, file pickers, popups,
and child windows are short-lived sessions: bind them when needed, discard them
after completion, then verify the parent window. If focus, window handle,
screenshot content, control state, or verification becomes unexpected, abandon
session reuse and rebind or recover before continuing.

## Required Loop

For every non-trivial GUI operation:

1. Precheck process and window state.
2. Capture evidence with screenshot and, when useful, UIA tree.
3. Identify controls in this priority order: UIA name/AutomationId/control type, DOM text when browser-backed, scoped OCR text, full-window OCR for discovery, icon/image match, coordinates.
4. Execute one action only.
5. Verify the expected UI change, file output, submitted state, or generated artifact.
6. Stop after three repeated failures in the same state and preserve screenshots/logs.

Exception: a workflow promoted as mature may execute several non-decision
actions inside one fixed flow, but only when the flow has explicit
preconditions, checkpoints, stop-on-failure behavior, and final verification.
The moment the flow encounters an unverified state, switch back to one action
per observation until the cause is understood and the flow is corrected.

Fast mode uses fresh proof only at decision boundaries: target identity,
layout/selector assumptions, irreversible actions, and final result. Strict
mode uses one observe-plan-act-verify step per action. New workflows,
changed layouts, stale sessions, unexpected modals, failed high-level tools,
or user safety concerns always force strict mode until the workflow is repaired
and reclassified.

Never repeatedly click the same coordinate without new evidence. Never ignore modal dialogs, permission prompts, captcha, or disabled controls.

## Recovery

Create checkpoints before fragile flows or after reaching a stable screen. When a bound window disappears or becomes invalid:

- Use `gui_recover_session` or `gui_act(..., auto_recover=true)` for one safe retry.
- Rebind by remembered `title_pattern` and `process_name` first.
- Re-capture evidence after recovery before continuing.
- Treat captcha, login, UAC, and destructive confirmations as `paused_for_human`.

For stable parent windows in a mature flow, prefer checkpoint-and-reuse over
repeated recovery. For transient windows, prefer rebind-after-transition over
reuse, because native dialogs can rebuild handles while preserving the same
title.

Use `auto_recover=true` only for window/session failures. Do not use it to mask bad selectors, wrong screens, or validation failures.

Codex Desktop may return to the foreground between assistant/tool turns. For foreground-sensitive actions such as hotkeys or typing, keep activation and input in the same GUI/MCP action or the same bounded script. The GUI MCP activation path should handle Windows foreground lock with restore, attached thread input, AppActivate, and a safe title-bar click fallback before sending keys.

## Evidence Rules

Keep evidence short and useful:

- Prefer tool-specific captures before whole-desktop screenshots: browser or
  Playwright screenshots for web content, GUI MCP window screenshots for
  desktop apps, and OS-level screenshots only when no narrower capture path is
  available or the user explicitly asks for a system screenshot.
- Save user-requested screenshots where the user asks. Save internal
  inspection screenshots to temp/evidence paths, and record only concise paths
  or summaries in skills.
- Store screenshot paths and UIA summaries.
- Do not store secrets, full chat transcripts, private attachment contents, or credentials in skills.
- When recording a reusable lesson, strip personal data and replace concrete content with role labels such as `input_file`, `target_user`, or `output_path`.
- Prefer compact failure reports over dumping full UI trees unless the compact report is insufficient.

## OCR Backend

For the local `gui_automation` MCP, keep PaddleOCR isolated from the MCP host runtime. The server runs on Python 3.14, while PaddleOCR/PaddlePaddle currently run from a Python 3.12 venv subprocess. Do not import PaddleOCR directly in the MCP server process.

On Windows CPU, PaddleOCR 3.x may hit a Paddle oneDNN/PIR runtime error during recognition. The OCR runner should set `PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT=0` unless a verified GPU backend is selected. If CUDA is available, test it in a separate OCR venv first and switch via environment/config only after `paddle.device.is_compiled_with_cuda()`, `paddle.utils.run_check()`, and a real screenshot OCR smoke test pass.

For this Windows workspace, GPU OCR has been verified with RTX 3060 Laptop GPU and remains a Windows-only capability. The managed runtimes live under `%LOCALAPPDATA%\Codex\runtimes\ocr`: use `gpu-venv\Scripts\python.exe` with `GUI_OCR_DEVICE=gpu`, and retain `cpu-venv\Scripts\python.exe` as the fallback. `pip-cache` is a junction to the single Windows pip cache authority, so package downloads remain reusable without a second wheel copy in the retired project tree.

## OCR Performance Discipline

Treat OCR as a precision fallback, not the default control discovery layer:

- Try UIA first for buttons, menus, text boxes, list items, tabs, and window state.
- Use OCR when UIA is incomplete, owner-drawn, stale, or missing visible text.
- Start with full-window OCR only when there is no reliable region. Convert successful matches into bounded regions for the next step.
- Reuse the OCR worker for repeated recognition. Avoid repeatedly spawning one-shot OCR in a loop.
- Prefer region OCR for toolbars, side panels, modal bodies, app content panes, and known text clusters.
- Keep coordinates as the last resort and bind them to fresh screenshot evidence.

## Image Processing And Vision Tool Discipline

Use local image-processing or computer-vision tools as optional helpers, not as
always-on GUI infrastructure:

- Default to disabled/on-demand for image MCPs that load models, run OCR, or
  download assets. Do not add them to the default MCP startup set unless they
  are proven lightweight, bounded, and covered by maintenance checks.
- Split capabilities into tiers: light file transforms (`get_metainfo`, crop,
  resize, rotate, blur, fill, draw, overlay) may be script-backed; heavy model
  tools (object detection, segmentation, text-prompt find, background removal)
  require explicit task need, resource checks, timeout limits, and a fallback
  path.
- Keep all file access path-bounded. A vision tool must only read/write inside
  approved workspace, temp, or user-specified directories. Reject implicit
  whole-disk search, relative traversal, and ambiguous overwrite targets.
- Keep telemetry off by default. Do not enable external telemetry, model
  downloads, or post-install scripts without explicit user approval and a
  maintenance rollback note.
- Do not auto-approve broad image-editing or model tools as a class. Non-
  destructive metadata reads can be low-risk; edits, overwrites, background
  removal, masking, and model downloads need explicit intent and verification.
- Validate image tool output with durable evidence: file existence, extension,
  dimensions, checksum or timestamp when useful, preview when visual quality
  matters, and OCR/detection confidence when semantic recognition is used.
- Record tool resource behavior in maintenance outputs when a vision backend is
  installed: model directory size, cache location, startup cost, running
  processes, timeout defaults, telemetry state, and allowed paths.

## Skill Evolution Hook

After each GUI task, use `gui-skill-evolution` when any of these happened:

- A new app-specific window/control pattern was discovered.
- A failure required a recovery strategy.
- A fragile selector was replaced by a stronger UIA/OCR/image method.
- The same app workflow was completed successfully twice.
- The task exposed a rule that should apply to all GUI automation.

General lessons stay in `gui-automation`. App-specific lessons become `gui-app-<app-name>` skills.

## References

## When to Load References

Read `references/gui-automation-sources.md` when refreshing external GUI automation knowledge or deciding between UIA, pywinauto, PyAutoGUI, and OCR.

## Preflight

- Confirm the target is a native Windows GUI surface or a GUI-backed dialog.
- Decide the safest layer first: direct API, DOM, UIA, OCR, or coordinates.
- Capture evidence before fragile or irreversible actions.

## Output Contract

- Return the verified state, action taken, and result observed.
- Mention the control layer used and why.
- State if the workflow is only candidate-level or fully verified.
