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
| Codex home | `C:\Users\45543\.codex` | active user skills with required dependencies, rules, scripts, tools, templates, and runtime compatibility evidence; platform/plugin caches and secrets excluded |
| Workspace governance | `C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager` | `AGENTS.md` plus `_bridge` source, policies, docs, tests, scripts, and a manifest-selected current checkpoint export |
| CC Switch | `C:\Users\45543\.cc-switch` | settings, skills, and a recursively redacted semantic database export; raw database remains an encrypted external archive |
| Codex plugins | enabled entries in `config.toml` plus plugin cache manifests | identity, marketplace, version/revision, and manifest hash only; plugin payloads are reacquired |
| Resource library | `C:\Users\45543\Desktop\Codex资源库` | separate asset repository/archive; only a recovery pointer is stored here |
| Windows integration | scheduled tasks and desktop shortcuts | structured specifications, not raw runtime state |

## Commands

Use the workspace workflow facade as the normal entrypoint:

```powershell
python C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager\_bridge\codex_workflow_entry.py mirror status
python C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager\_bridge\codex_workflow_entry.py mirror plan
python C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager\_bridge\codex_workflow_entry.py mirror refresh --confirm REFRESH-CODEX-MIRROR
python C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager\_bridge\codex_workflow_entry.py mirror doctor
python C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager\_bridge\codex_workflow_entry.py mirror restore-plan --target-root C:\CodexRestoreStage
python C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager\_bridge\codex_workflow_entry.py mirror stage --target-root C:\CodexRestoreStage --confirm STAGE-RESTORE
```

`refresh` is the standard capture path: plan, create, validate, prune the
superseded local snapshot, and commit the verified result. `stage` remains
isolated and never activates recovered state.

The underlying owner commands remain available for bootstrap recovery when the
workspace facade has not yet been restored:

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

- `mirror_valid`: manifests, text/binary hashes, text secret scans,
  source-coverage checks, generated semantic exports, references, and snapshot
  content are internally valid.
- `capability_restore_ready`: rules, owner source, configuration templates,
  skills, and bootstrap evidence can be staged and validated.
- `full_state_restore_ready`: all required encrypted external archives and
  secret re-acquisition requirements have verified receipts.

The repository may be mirror-valid before full-state readiness is achieved. A
missing archive must be reported explicitly and can never be represented as a
successful full restore.
