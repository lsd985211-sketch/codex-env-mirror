# Project Checkpoints

Project checkpoints record verified major changes by engineering project, not by
chat thread. They are the long-evidence layer for changes that should survive
context resets and future repair work.

Use `_bridge/project_checkpoint_finalize.py` after a verified major change when
one of these is true:

- MCP, Codex startup, plugin, permission, sandbox, service, or port baselines changed.
- Mobile bridge, OpenClaw, worker, scheduled task, CDP delivery, or reply behavior changed.
- ClientModLoader, AutoModpack, or Minecraft instance loading behavior changed.
- Memory architecture, access policy, project rules, or cross-agent coordination changed.
- A root cause was verified or an old memory was superseded by current evidence.
- A rollback baseline, stable prototype, or reusable engineering rule was created.

Write only verified facts. Keep long evidence in indexed checkpoint files and
submit short stable conclusions to the `local-pmb-memory` owner. Keep structured
module and relationship facts in their owning indexed surfaces rather than a
separate graph MCP. Do not store secrets, full logs, private reasoning, or
speculative conclusions.

Default command shape:

```powershell
python _bridge\project_checkpoint_finalize.py `
  --project-id mobile-openclaw-bridge `
  --change-type baseline `
  --title "Short verified title" `
  --summary "What changed and why it matters." `
  --changed-file "_bridge\path\file.py" `
  --evidence "Evidence item" `
  --verification "Command/result" `
  --backup "_bridge\backups\..." `
  --stable-conclusion "Reusable conclusion" `
  --write
```

The finalizer writes to `_bridge/shared/checkpoints/<project_id>/...md`, updates
the checkpoint manifest, and prints a structured PMB memory candidate. The
candidate is not automatically written; Codex must submit it through the
current memory owner and approval/governance flow after validation.
