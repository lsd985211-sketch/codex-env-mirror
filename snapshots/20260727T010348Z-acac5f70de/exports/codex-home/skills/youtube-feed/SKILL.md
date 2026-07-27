---
name: youtube-feed
description: >
  Discover recent videos from the configured YouTube channel set. Use when the
  user asks for recent podcast or YouTube updates, new videos within a time
  window, or candidate episodes to process next. Route acquisition through the
  resource layer; use the bundled script only as the selected owner adapter.
---

# YouTube Feed

Discover recent videos from the channel list maintained by the bundled script. This skill owns the channel-set semantics, not the global network route.

## Role Boundaries

The resource layer owns acquisition state and network execution. This skill defines the monitored channel set, local adapter invocation, candidate presentation, and transcript handoff.

## Resource Request

Submit:

```yaml
intent: video_discovery
query: recent updates from configured YouTube channels
source_classes: [video]
preferred_owner_tools: [youtube-feed]
freshness: recent
result_count: 30
need_materialization: false
output: candidates
constraints:
  days: 2
  include_views: false
```

Keep the request in progress until its terminal receipt is consumed. Refine `days`, channel category, language, or result count when needed.

## Owner Adapter

When the resource layer selects the local `youtube-feed` adapter, resolve the global skill path in PowerShell:

```powershell
$skill = Join-Path $env:USERPROFILE '.codex\skills\youtube-feed'
python (Join-Path $skill 'scripts\get_updates.py') --days 2 --json
```

Options:

```powershell
python (Join-Path $skill 'scripts\get_updates.py') --days 7 --json
python (Join-Path $skill 'scripts\get_updates.py') --days 2 --markdown
python (Join-Path $skill 'scripts\get_updates.py') --days 2 --json --views
```

Do not run the script independently while an equivalent resource request is still owned by the resource layer.

## Presentation

Return candidates with:

- channel;
- title, preserving the original title when translation is uncertain;
- publish time;
- URL;
- description or view count only when actually returned.

Group by category only when it improves selection. Do not fabricate summaries from titles alone.

## Handoff

After the user selects a video:

1. submit a transcript/content acquisition request;
2. consume the returned transcript or artifact;
3. hand the content to `content-digest` or `podcast-workflow` for transformation.

Edit the `CHANNELS` list in `scripts\get_updates.py` only when the user asks to change the monitored set. Back up and validate the script before modification.

## Output Contract

Return the resource request status, applied time window, candidate count, selected videos, and the next transcript/content owner.
