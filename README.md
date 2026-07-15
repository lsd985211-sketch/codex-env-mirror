# Codex Environment Recovery Kit

This private repository is a derived recovery product for the active Codex
working environment on this machine. It is not a live configuration authority.

## Authority Boundary

- Active rules, members, routes, configuration, skills, and owner state remain
  authoritative in their current owner-managed locations.
- This repository stores immutable, hashed exports and restore instructions.
- A snapshot must never be copied directly over the live environment.
- Restore follows `plan -> stage -> validate -> owner activation`.

## Current Local Sources

| Area | Active source | Recovery treatment |
| --- | --- | --- |
| Codex home | `C:\Users\45543\.codex` | selected source/config exports; secrets and runtime DBs excluded |
| Workspace governance | `C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager` | `AGENTS.md` plus `_bridge` source, policies, docs, tests, and scripts |
| CC Switch | `C:\Users\45543\.cc-switch` | settings and skills mirrored; database requires encrypted archive |
| Resource library | `C:\Users\45543\Desktop\Codex资源库` | separate asset repository/archive; only a recovery pointer is stored here |
| Windows integration | scheduled tasks and desktop shortcuts | structured specifications, not raw runtime state |

## Commands

```powershell
python scripts\mirror_cli.py plan
python scripts\mirror_cli.py snapshot --apply
python scripts\mirror_cli.py validate
python scripts\mirror_cli.py restore-plan --target-root C:\CodexRestoreStage
python scripts\mirror_cli.py stage --target-root C:\CodexRestoreStage --confirm STAGE-RESTORE
```

`snapshot` is dry-run unless `--apply` is provided. `stage` writes only to an
isolated target and refuses known active source roots. Activation is deliberately
not automated by this bootstrap repository; it must be performed by the target
environment's owners after backup and validation.

## Readiness States

- `mirror_valid`: manifests, hashes, secret scan, references, and snapshot
  content are internally valid.
- `capability_restore_ready`: rules, owner source, configuration templates,
  skills, and bootstrap evidence can be staged and validated.
- `full_state_restore_ready`: all required encrypted external archives and
  secret re-acquisition requirements have verified receipts.

The repository may be mirror-valid before full-state readiness is achieved. A
missing archive must be reported explicitly and can never be represented as a
successful full restore.

