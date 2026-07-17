# General Failed Or Avoid

GUI patterns that failed across scenes, proved unsafe, or are too brittle to use as general strategies.

Each entry should include:

- Pattern: the tempting general rule or action.
- Failed apps/scenes: where it failed.
- Cause: why it failed.
- Risk: wrong target, destructive action, focus theft, false verification, stale state, or privacy risk.
- Safer alternative: a preferred candidate, conditional, or app-specific route.

If a failed general pattern is still useful in one app, keep that app-specific success in the app skill but do not promote the general pattern.

## Trusting Window Metadata Without Visual Verification

- Pattern: using process name, window title, or handle as sufficient proof that
  coordinates will target the intended surface.
- Failed apps/scenes: Windows Weixin main window capture before activation.
- Cause: the captured visual surface can differ from the discovered window
  metadata in occluded or multi-window desktop states.
- Risk: wrong target, unintended clicks, unintended typing.
- Safer alternative: activate, rehydrate, and verify a fresh screenshot with
  app-specific anchors before input.

## Default-Starting Heavy Vision MCPs

- Pattern: adding a model-backed image/OCR/detection MCP to the default Codex
  startup set just because it exposes many useful tools.
- Failed apps/scenes: Imagesorcery MCP repository review under a Windows Codex
  environment already experiencing MCP and performance pressure.
- Cause: post-install model downloads, OCR/CV dependencies, optional telemetry,
  and broad image-editing tools increase startup cost and fault surface before
  there is a specific task need.
- Risk: high memory/CPU load, transport instability, unexpected downloads,
  excessive tool visibility, accidental writes outside the intended image area,
  and configuration drift.
- Safer alternative: keep heavy vision MCPs disabled/on-demand, path-bounded,
  telemetry-off, timeout-limited, and covered by doctor/validate/metrics before
  any trial use.

## Broad Auto-Approval For Image Editing Tools

- Pattern: approving all image edit, detection, config, OCR, and overlay tools
  as a single class.
- Failed apps/scenes: Imagesorcery MCP configuration examples reviewed as an
  unsafe fit for this local workflow.
- Cause: image tools range from read-only metadata to file mutation, model
  downloads, config edits, masking, and overwrite-capable operations.
- Risk: unintended file modification, privacy exposure through broad path
  access, false confidence in model results, and hidden configuration changes.
- Safer alternative: classify each tool by side effect: metadata reads are low
  risk, new-output transforms require explicit output paths, overwrites and
  model/config operations require explicit approval and verification.
