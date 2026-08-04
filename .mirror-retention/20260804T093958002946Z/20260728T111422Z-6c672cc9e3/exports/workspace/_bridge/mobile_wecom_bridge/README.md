# Enterprise WeChat Mobile Bridge

This directory contains the local core for using Enterprise WeChat as a phone
entry point for Codex/Reasonix tasks.

The bridge does not include an intranet tunnel. It expects your tunnel software
or cloud relay to expose the local callback endpoint when you are ready.

## Scope

Implemented here:

- local HTTP service using Python stdlib;
- SQLite task queue;
- risk classification, allowlist checks, dedupe fingerprints, and confirmation
  secret checks;
- dry-run task ingestion without WeCom secrets;
- outbound WeCom text-message sender skeleton;
- clear failure when real callback decryption needs an AES package.

Not implemented here:

- tunnel setup;
- automatic high-risk execution from the phone;
- storing real secrets in the repository;
- forcing the current Codex desktop session to wake up.

## Files

- `wecom_bridge_server.py`: HTTP service.
- `mobile_queue.py`: SQLite queue and risk classifier.
- `config.example.json`: template. Copy to `config.local.json`.
- `run-wecom-bridge.ps1`: foreground start script.
- `start-hidden-wecom-bridge.ps1`: hidden background start script.
- `mobile_bridge_cli.py`: local queue inspection and completion CLI.
- `tests_dry_run.py`: local queue/classifier validation.

## Local Dry Run

```powershell
cd C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager\_bridge\mobile_wecom_bridge
python tests_dry_run.py
.\run-wecom-bridge.ps1
```

In another terminal:

```powershell
$body = @{ text = "/ask 分析服务器日志"; user = "phone-test" } | ConvertTo-Json -Compress
Invoke-WebRequest -Uri "http://127.0.0.1:8787/dry-run/enqueue" -Method POST -Body $body -ContentType "application/json; charset=utf-8"
Invoke-WebRequest -Uri "http://127.0.0.1:8787/tasks" -UseBasicParsing
```

Inspect or complete queued tasks locally:

```powershell
python .\mobile_bridge_cli.py pending
python .\mobile_bridge_cli.py claim <task_id> --agent codex
python .\mobile_bridge_cli.py done <task_id> "处理结果"
python .\mobile_bridge_cli.py health
```

## WeCom Setup Outline

1. In Enterprise WeChat, create a self-built app and record `AgentId` and
   `Secret`.
2. Configure these environment variables on the Windows user account that runs
   the bridge:
   - `WECOM_CORP_ID`
   - `WECOM_CORP_SECRET`
   - `WECOM_CALLBACK_TOKEN`
   - `WECOM_ENCODING_AES_KEY`
   - `MOBILE_BRIDGE_CONFIRM_SECRET`
3. Copy `config.example.json` to `config.local.json` and set `agent_id`.
   Also set `security.allowed_users` to the Enterprise WeChat UserID values
   allowed to trigger Codex.
4. Start the local bridge on `127.0.0.1:8787`.
5. Use your intranet tunnel software to expose:
   - `https://your-domain/wecom/callback` -> `http://127.0.0.1:8787/wecom/callback`
6. In Enterprise WeChat callback settings, set:
   - URL: `https://your-domain/wecom/callback`
   - Token: value of `WECOM_CALLBACK_TOKEN`
   - EncodingAESKey: value of `WECOM_ENCODING_AES_KEY`

## Important Dependency Note

Real Enterprise WeChat callbacks use AES-CBC encrypted XML. The Python standard
library has no AES implementation. The dry-run path works without extra
packages, but real callback decrypt/verify needs the `cryptography` package or
another reviewed AES implementation.

Do not install packages or change the runtime silently. Approve that environment
change first, then install:

```powershell
python -m pip install cryptography
```

## Phone Command Policy

- `L0`: `/status`, `/tasks`, `/result`, `/help` - read-only.
- `L1`: `/ask`, `/report`, `/analyze`, `/memory` - queue for agent processing.
- `L2`: server start/stop, config edits, scripts, deploys - require
  confirmation secret.
- `L3`: deletes, bulk moves, permission changes, database clearing, ban/kick -
  rejected from phone-only execution.

This bridge records requests. It does not by itself execute risky operations.

Natural-language messages are allowed. You do not have to use command prefixes.
The bridge classifies risk from the message content before treating `/ask` or
plain text as an analysis task.

## v0.2 Stability Guards

The bridge adds these guards before any real Codex worker should be enabled:

- Allowlist: only configured Enterprise WeChat `UserID` values can trigger
  Codex. Unknown users are recorded and rejected.
- Deduplication: each message has a fingerprint based on WeCom message metadata
  or content hash, so retries do not create duplicate tasks.
- Cooldown: Codex trigger attempts are rate-limited by
  `trigger.cooldown_seconds`.
- Running lock: only one mobile-triggered Codex batch may be active at a time.
- State machine: tasks move through states such as `pending`,
  `waiting_confirmation`, `queued_for_codex`, `sent_to_codex`, `processing`,
  `done`, `pushed_to_wecom`, `push_failed`, historical `codex_timeout`,
  `rejected`, and `cancelled`.
- Timeout recovery is deprecated. Active Codex tasks should be recovered from
  current Codex/CDP health instead of closed by wall-clock time; the old
  `expire-stale` command is retained only as a compatibility no-op.
- Input length limit: `safety.max_input_chars` defaults to 2000.
- Log/event redaction: secrets, tokens, access tokens, and encrypted payloads
  are redacted from stored event payloads.
- Pause switch: create a file named `PAUSE` in this directory to stop bridge
  triggering while still allowing message records.
- Shadow mode: `safety.shadow_mode` defaults to `true`. Keep it enabled until
  real Enterprise WeChat callback, tunnel, and task routing have been verified.

## Confirmation Secret

For `L2` tasks, the phone reply should provide the agreed confirmation secret
for the specific task. The bridge stores only the SHA-256 hash, not the secret.

Set either:

```powershell
[Environment]::SetEnvironmentVariable("MOBILE_BRIDGE_CONFIRM_SECRET", "your-secret", "User")
```

or put a precomputed hash in `security.confirmation_secret_hash`.

`L3` tasks remain rejected from phone-only execution even if the secret is
provided.
