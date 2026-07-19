# General Candidates

Reusable GUI patterns abstracted from app-specific experience but not yet trusted across multiple apps.

Use these only as exploration routes in unfamiliar apps. A candidate must include:

- Pattern: the app-independent action or selector strategy.
- Source app: where it was discovered.
- Preconditions: when it might apply.
- Verification: what proof is required.
- Missing proof: what prevents promotion.
- Known failures: links or summaries when available.

Promote only after successful verification in multiple distinct apps or one app family with matching UI architecture.

## Activate-Rehydrate-Recapture Before Coordinate Input

- Pattern: for desktop apps with multiple windows, occlusion, or stale handles,
  activate the intended window, rehydrate the window object, and take a fresh
  screenshot before coordinate-based input.
- Source app: Windows Weixin.
- Preconditions: the app exposes a targetable window but the first screenshot,
  focus state, or visible surface may be wrong or stale.
- Verification: screenshot must show app-specific UI anchors before any
  coordinate click or typing.
- Missing proof: currently verified in Weixin and consistent with prior
  Notepad/Codex focus lessons, but not yet tested across enough unrelated apps
  to promote to trusted.
- Known failures: without activation, a Weixin window handle produced a capture
  of Codex Desktop content.

## Self-Drawn Chat Surface Fallback

- Pattern: when UI Automation exposes only a top-level window for a chat-style
  app, use UIA as a probe, then fall back to screenshot/OCR and fresh
  window-relative coordinates.
- Source app: Windows Weixin.
- Preconditions: screenshot shows the desired app surface and UIA tree lacks
  useful controls.
- Verification: each action must have visual proof of expected state change,
  such as row highlight, header title, draft text, or button color.
- Missing proof: only verified in Weixin so far.
- Known failures: blindly relying on UIA text produced no usable candidates.

## Rebind After Native File Dialog Completion

- Pattern: after a native Windows file picker accepts a path or closes, refresh
  window state before the next action. If the dialog disappeared, verify the
  parent app result; if the dialog reappeared with a new handle, bind a new
  session by title/process.
- Source app: Windows Weixin.
- Preconditions: the app opens a native file picker and the automation entered
  a file path, clicked Open, pressed Enter, or otherwise triggered file
  validation/completion.
- Verification: parent app screenshot must show the selected file, completed
  attachment, or another expected outcome before any send/submit action.
- Missing proof: verified in Weixin only; needs testing in at least one
  unrelated app that uses the standard Windows file picker.
- Known failures: continuing to act on the old file-picker session after path
  completion produced `bound window no longer exists`.

## Rebind When A Main Window Spawns A Task-Specific Child

- Pattern: when an app action opens or reveals a task-specific child window,
  rebind automation to the child before continuing workflow steps that need to
  target controls inside that child. Treat the previous parent session as stale
  for child-local controls until a fresh screenshot proves otherwise.
- Source app: Windows Weixin.
- Preconditions: the original app window remains visible or partially visible,
  but the actionable surface has moved to a child window whose title, size, or
  process/window handle differs from the original parent.
- Verification: the new bound window screenshot must show the task identity
  such as recipient, document, dialog title, or file target plus the controls
  required for the next action.
- Missing proof: verified in Weixin detached chat only; needs testing in
  another desktop app that spawns document, compose, or transfer child windows.
- Known failures: using the old Weixin main-window session after opening a
  detached chat caused a high-level attach-file route to fail at
  `refresh_parent` with `bound window is no longer targetable`.

## High-Level Tool For Mature File Picker Segments

- Pattern: once a file-picker segment is mature and low-risk, wrap the stable
  intermediate actions into one bounded tool call: activate verified parent,
  open picker, enter absolute file path, confirm picker, return to parent, and
  capture final evidence. Keep irreversible submit/send actions outside the
  fast tool unless separately verified and approved.
- Source app: Windows Weixin.
- Preconditions: parent window identity and target state were freshly verified;
  the app uses a native Windows file picker; the file path is absolute and
  already approved for the current task; current-layout coordinates or stronger
  selectors are available.
- Verification: tool must expose the failed stage and final screenshot; the
  parent app must show the selected file or expected durable state before any
  send/submit action.
- Missing proof: MCP registration and error path were verified, but the new
  high-level attach tool has not yet been live-verified across multiple apps.
- Known failures: stale coordinates or a different file picker layout can place
  input in the wrong control, so coordinates must be refreshed per layout until
  stronger UIA selectors are added.

## Tool-Specific Screenshot Evidence Before OS-Level Capture

- Pattern: when screenshot evidence is needed, prefer the narrowest capture
  source that directly matches the target: browser/Playwright screenshot for
  web content, GUI MCP window screenshot for desktop apps, and OS-level full
  screenshot only when no target-specific capture can prove the state.
- Source skill: official OpenAI `screenshot` skill, adapted to this Windows GUI
  framework.
- Preconditions: the task needs visual evidence for inspection, targeting, or
  verification.
- Verification: the captured image path or evidence summary must correspond to
  the intended app/window/region before it is used to justify a click, type, or
  skill update.
- Missing proof: imported from an external skill and aligned with local GUI MCP
  practice, but not yet stress-tested as a formal local pattern across multiple
  apps after this import.
- Known failures: whole-desktop screenshots can include irrelevant/private
  areas and can hide which window is actually actionable.

## Browser-Backed Surface DOM Route

- Pattern: if a desktop-visible surface is browser-backed, Electron-based, or
  reachable through a browser automation channel, inspect and act through DOM or
  Playwright-style selectors before falling back to Windows UIA, OCR, or
  coordinates.
- Source skill: official `playwright-interactive` concept plus local bundled
  browser/chrome skills.
- Preconditions: the relevant content is web-rendered and a browser automation
  route can access the current page, tab, app webview, or local URL.
- Verification: DOM selectors or browser screenshots must prove the same target
  state that the user sees, especially before submit/send actions.
- Missing proof: external skill details were not fully loaded during import, so
  this remains a conservative candidate rather than a trusted rule.
- Known failures: native shell controls, OS dialogs, login popups, and many
  embedded webviews may not expose a usable DOM route.

## Direct Image-File Transform Before GUI Editing

- Pattern: when the target is an image file rather than a live app surface, use
  direct image-processing operations for metadata, crop, resize, rotate, blur,
  fill, draw, overlay, watermark, and OCR before considering GUI control.
- Source project: Imagesorcery MCP repository review.
- Preconditions: the image path is known, the requested output can be expressed
  as a file transformation, and the operation does not require a human-only app
  workflow.
- Verification: output file must be checked by existence, dimensions, format,
  and a visual preview or OCR/readback when content quality matters. Original
  files must remain unchanged unless overwrite was explicitly requested.
- Missing proof: imported from an external MCP design and not yet verified as a
  local high-level tool route in this workspace.
- Known failures: image-file tools do not prove anything about a live GUI state;
  use GUI screenshot/UIA/OCR verification when the task is to operate a visible
  application.

## Pixel Bounding Box Contract For Vision Work

- Pattern: represent image regions as `[x1, y1, x2, y2]` and explicitly state
  the coordinate frame before using a crop, blur, fill, detection, or click
  result.
- Source project: Imagesorcery MCP repository review, aligned with local GUI
  coordinate-freshness rules.
- Preconditions: the image dimensions are known and the region is image-
  relative, window-relative, or screen-relative without ambiguity.
- Verification: any transformed output or click target must be checked against
  the same coordinate frame that produced it.
- Missing proof: needs local testing across GUI screenshots and standalone image
  files before promotion to trusted.
- Known failures: mixing image-relative, window-relative, and screen-relative
  coordinates can target the wrong pixels or controls.

## Tiered Vision Backend Activation

- Pattern: separate light image transforms from heavy model-backed operations.
  Keep OCR, object detection, segmentation, background removal, and text-prompt
  image search disabled/on-demand unless the current task needs them.
- Source project: Imagesorcery MCP repository review.
- Preconditions: a vision backend has explicit allowed paths, timeouts, model
  cache location, telemetry-off default, and maintenance visibility.
- Verification: doctor/metrics should show whether model files, cache size,
  running processes, telemetry state, and allowed paths are under control.
- Missing proof: no Imagesorcery backend is installed locally; this is a design
  candidate for future image tool integration.
- Known failures: default-starting model-heavy MCPs can add startup latency,
  memory pressure, transport failures, unexpected downloads, and noisy config
  drift.
