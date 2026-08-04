# Video Source Selection

Use for video discovery, metadata, subtitles, transcripts, comments, or audio extraction.

## Request Types

| Need | Intent | Output |
|---|---|---|
| Recent configured YouTube channels | `video_discovery` with owner `youtube-feed` | candidates |
| Search for videos | `video_discovery` | candidates |
| Read metadata for a known URL | `video_metadata` | metadata |
| Obtain subtitles/transcript | `transcript_acquisition` | content or file |
| Download media | `video_materialize` | approval-aware artifact |
| Transcribe local media | local audio/video owner | transcript file |

## Structured Request

```yaml
intent: transcript_acquisition
target: known video URL
source_classes: [video]
language: zh-CN
need_materialization: true
output: content
acceptance:
  transcript_scope_required: true
  provenance_required: true
```

Use `preferred_owner_tools: [youtube-feed]` only for the configured channel feed. Let the resource layer choose YouTube transcript, Bilibili, browser, media, or generic search owners for other tasks.

## Rules

- Distinguish title/description metadata from a real transcript.
- State whether captions are manual, automatic, translated, partial, or unavailable.
- Do not call `yt-dlp`, `bili`, curl, or OpenCLI directly while the resource request owns the need.
- Downloading media or installing a backend requires the resource-layer materialization/approval path.
- Respect platform access, authentication, rate limits, and copyright constraints.

After acquisition, hand content to `content-digest`, translation, transcription, or the calling workflow. Return source URL, platform, scope, language, dates, and limitations.
