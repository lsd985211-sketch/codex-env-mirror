---
name: remotion-video
description: Create, modify, preview, and render programmatic videos with Remotion and React. Use for timeline composition, captions, audio synchronization, data-driven animation, 2D/3D scenes, and deterministic video rendering.
metadata: {"codex":{"compatibility":"Requires a Remotion project with Node.js and project dependencies. Project-specific audio or render helper scripts must be generated in the target project; they are not bundled with this skill."}}
---

# Remotion Video

## Scope

Use this skill after a target Remotion project exists or when the user asks to create one. The skill owns video composition guidance, project edits, preview, and rendering. It does not own external media acquisition; route those assets through the resource layer.

## Core Workflow

1. Inspect `package.json`, Remotion version, compositions, dimensions, FPS, and duration.
2. Confirm the requested output, aspect ratio, codec, audio, captions, and asset sources.
3. Keep timing deterministic with `useCurrentFrame()`, `useVideoConfig()`, `Sequence`, and explicit frame calculations.
4. Create project-local audio or render helpers only when the task needs them. Never assume helper files are bundled with this skill.
5. Preview representative frames before a full render.
6. Render with the project's Remotion CLI and verify the produced media with metadata plus sampled frames.

## Progressive References

- Read `references/full-guide.md` only for detailed legacy patterns, 3D scenes, process animations, TTS, or long-form examples.
- Prefer current official Remotion APIs and the target project's installed version when the reference guide differs.
- Treat all script paths in the legacy guide as templates that belong in the target project, not bundled executables.

## Common Commands

```powershell
npx remotion compositions
npx remotion studio
npx remotion render <composition-id> <output-file>
```

## Validation

- Verify composition discovery succeeds.
- Check duration, FPS, dimensions, codec, and audio presence.
- Inspect representative beginning, transition, and ending frames.
- Check that text is not clipped and that audio/captions remain synchronized.

## Output Contract

- State files changed, composition ID, render command, and output path.
- Report preview/render verification and any missing project dependency.
- Do not claim a successful render until the output file exists and has nonzero duration.
