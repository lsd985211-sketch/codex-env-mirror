---
name: ffmpeg-usage
description: Inspect, convert, edit, concatenate, resize, compress, subtitle, extract, and validate audio or video with ffmpeg and ffprobe. Use for deterministic local media processing and platform-specific delivery formats.
metadata: {"codex":{"compatibility":"Requires ffmpeg; ffprobe is strongly recommended. Available encoders, filters, and hardware acceleration vary by build."}}
---

# FFmpeg Media Operations

## Workflow

1. Inspect the source with `ffprobe` before choosing codecs, filters, or stream-copy operations.
2. Confirm output container, codec, dimensions, frame rate, audio layout, subtitles, duration, and size/quality target.
3. Prefer stream copy only when source streams are compatible with the requested edit and container.
4. Use an explicit filter graph for scaling, padding, speed, overlays, subtitles, or audio transforms.
5. Write to a new output path, then verify duration, streams, codecs, dimensions, and nonzero size.

## Progressive Reference

Read `references/full-guide.md` for detailed conversion, concatenation, GIF, subtitle, social-platform, compression, batch, transcription, and troubleshooting examples. Adapt shell-specific examples to the current Windows/PowerShell environment.

## Output Contract

- State the exact command, source/output paths, and whether streams were copied or re-encoded.
- Report ffprobe verification and any quality, sync, subtitle, or compatibility risks.
- Never overwrite the source unless explicitly requested.
