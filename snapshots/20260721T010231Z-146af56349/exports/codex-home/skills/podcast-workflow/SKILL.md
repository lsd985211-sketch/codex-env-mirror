---
name: podcast-workflow
description: >
  Orchestrate podcast and YouTube episode discovery, transcript acquisition,
  content transformation, optional local saving, and optional Feishu publishing.
  Use when the user asks to process a podcast/video end to end or choose a recent
  episode and turn it into a digest. Delegate online acquisition through the
  resource layer and use specialist skills for each processing stage.
---

# Podcast Workflow

Coordinate existing owners. Do not embed user-specific paths or duplicate transcript, digest, storage, and publishing implementations.

## Role Boundaries

This skill sequences discovery, acquisition, transformation, storage, and publishing owners. Each specialist remains responsible for its own execution and validation.

## Entry Modes

### Discover Recent Episodes

Use `youtube-feed` to prepare and submit a structured video-discovery request. Present candidates and wait for the user's selection before acquiring transcripts or generating long content.

### Process A Known Episode

For a supplied URL, skip source discovery and submit the URL directly for transcript or source-content acquisition.

## Workflow

1. **Discover or accept the episode**
   - Record title, channel, URL, publish date, and language when known.
2. **Acquire content**
   - Submit a transcript/content resource request.
   - Prefer an existing transcript owner such as `youtube-transcript-cn` when selected by the resource route.
   - Treat the request as active until its terminal receipt is consumed.
3. **Confirm scope**
   - State whether the source is a full transcript, captions, summary, or partial extract.
4. **Transform**
   - Hand acquired content to `content-digest`.
   - Generate only the requested short form, long form, notes, or script.
5. **Save when requested**
   - Resolve the destination through the current Obsidian, filesystem, or document owner.
   - Do not default to a historical user-specific vault path.
6. **Publish when requested**
   - Use `feishu-wiki` or another publishing owner only after the local content is complete and the user has requested publishing.
7. **Optional visuals**
   - Ask for or generate images only when they are part of the requested deliverable.

## Resource Request Example

```yaml
intent: transcript_acquisition
target: supplied episode URL
source_classes: [video]
preferred_owner_tools: [youtube-transcript-cn]
language: zh-CN
need_materialization: true
output: content
acceptance:
  transcript_scope_required: true
  provenance_required: true
```

If the transcript owner fails, refine or continue the configured resource route. Do not independently start a second generic search while the resource request still owns the need.

## Handoff

Exit discovery after candidate selection, exit acquisition after consuming the transcript receipt, and exit transformation after delivering or saving the requested content. Do not keep ownership of publishing or image generation after handing those stages to their specialist owners.

## Delivery

Return:

- episode metadata;
- transcript/source scope;
- digest or requested derivative;
- saved path only when a file was created;
- publishing URL only after a successful publish action;
- blockers such as missing transcript, authentication, or incomplete source.

Never present a temporary transcript, scratch digest, or project-internal file as the final deliverable.
