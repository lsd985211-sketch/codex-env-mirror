# Codex Environment Recovery Kit

This public repository is a derived recovery product for the active Codex
working environment on this machine. It is not a live configuration authority.
Agents entering this repository automatically receive the recovery boundary and
entry sequence from `AGENTS.md`; detailed capability and lifecycle facts remain
in machine-readable manifests rather than being duplicated in prose.

## Authority Boundary

- Active rules, members, routes, configuration, skills, and owner state remain
  authoritative in their current owner-managed locations.
- This repository stores immutable, hashed exports and restore instructions.
- A snapshot must never be copied directly over the live environment.
- Restore follows `plan -> stage -> validate -> owner activation`.

## Current Local Sources

| Area | Active source | Recovery treatment |
| --- | --- | --- |
| Codex home | `C:\Users\45543\.codex` | active user skills with required dependencies, current native memory text, rules, scripts, tools, templates, and runtime compatibility evidence; platform/plugin caches and secrets excluded |
| Agent compatibility home | `C:\Users\45543\.agents` | active compatibility skills only; `.disabled` and caches excluded |
| Workspace governance | `C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager` | `AGENTS.md` plus `_bridge` source, policies, docs, tests, scripts, and a manifest-selected current checkpoint export |
| CC Switch | `C:\Users\45543\.cc-switch` | settings, skills, and a recursively redacted semantic database export; raw database remains an encrypted external archive |
| Codex plugins | enabled entries in `config.toml` plus plugin cache manifests | identity, marketplace, version/revision, and manifest hash only; plugin payloads are reacquired |
| Resource library | `C:\Users\45543\Desktop\Codex资源库` | separate asset repository/archive; only a recovery pointer is stored here |
| Windows integration | scheduled tasks and desktop shortcuts | structured specifications, not raw runtime state |

Every top-level asset in the inventoried Codex, Agent compatibility, and CC
Switch homes must also have an explicit disposition in
`manifests/asset-dispositions.json`. An unknown item blocks refresh, so a new
valuable capability cannot be silently omitted. Private or version-sensitive
state remains an explicit external-archive gap instead of being copied into the
capability snapshot.

## Membership-Driven Scope

`_bridge/system_membership.py` is the upstream authority for active capability
membership. Its `mirror-source-projection` output identifies active members,
their mirror source IDs, generated source IDs, and change roots. The mirror
manifest remains the capture authority: it defines redaction, exclusions,
binary handling, restore mappings, generated commands, and external-archive
dispositions. A source or generated source present in the manifest but absent
from the active membership projection blocks the next refresh, preventing a
new module from becoming silently unowned.

When adding a working-environment member, update its membership contract and
mirror source projection first. Then add the corresponding source definition
or generated source to `manifests/source-authorities.json`, run the membership
validator, and refresh through the transactional facade. Retired members are
never projected into the active mirror.

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

`refresh` is the standard transactional capture path: reconcile an invalid
uncommitted candidate, plan, create, validate, retry bounded source drift,
commit the verified candidate, then retire superseded snapshots in a separate
commit. A failed candidate is removed and `latest.json` is restored atomically.
`stage` remains isolated and never activates recovered state.

The underlying owner commands remain available for bootstrap recovery when the
workspace facade has not yet been restored:

```powershell
python scripts\mirror_cli.py plan
python scripts\mirror_cli.py snapshot --apply
python scripts\mirror_cli.py validate
python scripts\mirror_cli.py validate --live-sources
python scripts\mirror_cli.py restore-plan --target-root C:\CodexRestoreStage
python scripts\mirror_cli.py stage --target-root C:\CodexRestoreStage --confirm STAGE-RESTORE
```

The default `validate` checks the committed repository and fixed snapshot, so a
fresh clone does not require the publisher's machine paths. `--live-sources`
adds active-source coverage, generated-source freshness, and top-level asset
disposition checks; it is used by the publisher-side workspace facade and
refresh transaction. `snapshot` is dry-run unless `--apply` is provided. `stage` writes only to an
isolated target and refuses known active source roots. Activation is deliberately
not automated by this bootstrap repository; it must be performed by the target
environment's owners after backup and validation.

## Readiness States

- `mirror_valid`: manifests, text/binary hashes, text secret scans, generated
  snapshot assets, references, membership guards, and repository governance are
  internally valid.
- `capability_restore_ready`: rules, owner source, configuration templates,
  skills, and bootstrap evidence can be staged and validated.
- `source_freshness_checked` / `source_freshness_ok`: whether the optional live
  source comparison ran and whether the active source still matches the snapshot.
- `full_state_restore_ready`: all required encrypted external archives and
  secret re-acquisition requirements have verified receipts.

The repository may be mirror-valid before full-state readiness is achieved. A
missing archive must be reported explicitly and can never be represented as a
successful full restore.
