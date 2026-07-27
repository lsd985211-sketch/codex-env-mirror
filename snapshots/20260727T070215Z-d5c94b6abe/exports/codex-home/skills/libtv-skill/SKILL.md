---
name: libtv-skill
description: >
  Use the agent-im OpenAPI to create or continue sessions, request image or
  video generation, upload image/video inputs, query progress, and return final
  media plus the project URL. Use only when LIBTV_ACCESS_KEY is configured and
  the user requests this agent-im or Liblib workflow.
metadata:
  openclaw:
    emoji: "💬"
    requires:
      bins: [python]
      env: [LIBTV_ACCESS_KEY]
    primaryEnv: LIBTV_ACCESS_KEY
---

# agent-im Session Operations

Use the bundled standard-library Python scripts. Resolve paths from the skill directory and use the current Windows Python launcher.

## Role Boundaries

This skill owns the local agent-im client workflow. It does not own credential storage, generic media downloads, or unrelated image/video generation providers.

## Preflight

In PowerShell:

```powershell
$skill = Join-Path $env:USERPROFILE '.codex\skills\libtv-skill'
$env:LIBTV_ACCESS_KEY = '<access-key>'
```

Optional endpoint overrides:

```powershell
$env:OPENAPI_IM_BASE = 'https://im.liblib.tv'
```

Never print or persist the access key. A help command must work without credentials; network operations require the key.

## Commands

Create or continue a session:

```powershell
python (Join-Path $skill 'scripts\create_session.py') '生一个动漫视频'
python (Join-Path $skill 'scripts\create_session.py') '再生成一张风景图' --session-id SESSION_ID
python (Join-Path $skill 'scripts\create_session.py')
```

Query progress:

```powershell
python (Join-Path $skill 'scripts\query_session.py') SESSION_ID
python (Join-Path $skill 'scripts\query_session.py') SESSION_ID --after-seq 5 --project-id PROJECT_UUID
```

Change the project bound to the access key:

```powershell
python (Join-Path $skill 'scripts\change_project.py')
```

Upload an image or video under 200 MB:

```powershell
python (Join-Path $skill 'scripts\upload_file.py') 'C:\path\to\image.png'
python (Join-Path $skill 'scripts\upload_file.py') 'C:\path\to\video.mp4'
```

## Execution

1. Validate the local input type and size before upload.
2. Create or continue a session and retain `sessionId` and `projectUuid`.
3. Poll with `--after-seq` rather than repeatedly downloading the complete history.
4. Stop on a terminal API error and report its status; do not retry authentication or invalid requests.
5. Return generated media as soon as it becomes available.

## Output Contract

Return:

- `sessionId`;
- generated image/video URLs found in assistant messages;
- `projectUrl` when the task completes;
- a concise blocker when credentials, permissions, input type, or API state prevents completion.

Do not expose `projectUrl` as a substitute for unfinished media. Do not claim generation completed until a generated result is present.
