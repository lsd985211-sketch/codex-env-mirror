# Codex Environment Recovery Kit

This public repository is a derived recovery product for the active Codex
working environment on this machine. It is not a live configuration authority.
Agents entering this repository automatically receive the recovery boundary and
entry sequence from `AGENTS.md`; detailed capability and lifecycle facts remain
in machine-readable manifests rather than being duplicated in prose.

`CURRENT.md` is the human-readable current-state surface. Its machine authority
is `manifests/control-plane-state.json`, which binds the current snapshot,
readiness, source freshness, latest milestone, and hashes of every declared
static control-plane file. Older modification dates on static contracts are not
staleness when their hashes remain compatible with the current snapshot.

## Authority Boundary

- Active rules, members, routes, configuration, skills, and owner state remain
  authoritative in their current owner-managed locations.
- This repository stores immutable, hashed exports and restore instructions.
- A snapshot must never be copied directly over the live environment.
- Restore follows `plan -> stage -> validate -> owner activation`.

## Current Local Sources

| Area | Active source | Recovery treatment |
| --- | --- | --- |
| Codex home | `C:\Users\45543\.codex` | active user skills with required dependencies, current native memory text, rules, scripts, tools, and runtime compatibility evidence; active provider/model configuration, model catalogs, platform/plugin caches, and secrets excluded |
| Agent compatibility home | `C:\Users\45543\.agents` | active compatibility skills only; `.disabled` and caches excluded |
| Workspace governance | `/home/codexlab/work/codex-workspace` (`\\wsl.localhost\Codex-Wsl-Lab\home\codexlab\work\codex-workspace`) | authoritative Work Git `AGENTS.md` plus `_bridge` source, policies, docs, tests, scripts, and a manifest-selected current checkpoint export; the Windows native workspace is a generated compatibility projection only |
| CC Switch | `C:\Users\45543\.cc-switch` | stable user-owned skills only; mutable settings and database semantics are excluded, while an optional private legacy database archive remains owner-managed |
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

Use the WSL Work Git workflow facade as the normal entrypoint:

```bash
cd /home/codexlab/work/codex-workspace/workspace
python3 _bridge/codex_workflow_entry.py mirror status
python3 _bridge/codex_workflow_entry.py mirror plan
python3 _bridge/codex_workflow_entry.py mirror refresh --confirm REFRESH-CODEX-MIRROR
python3 _bridge/codex_workflow_entry.py mirror publish --confirm PUBLISH-CODEX-MIRROR
python3 _bridge/codex_workflow_entry.py mirror release-plan
python3 _bridge/codex_workflow_entry.py mirror contract-review-plan
python3 _bridge/codex_workflow_entry.py mirror release --tag seed-v2.4.0 --confirm RELEASE-CODEX-MIRROR
python3 _bridge/codex_workflow_entry.py mirror doctor
python3 _bridge/codex_workflow_entry.py mirror restore-plan --target-root /mnt/c/CodexRestoreStage
python3 _bridge/codex_workflow_entry.py mirror stage --target-root /mnt/c/CodexRestoreStage --confirm STAGE-RESTORE
```

`refresh` is the standard transactional capture path: reconcile an invalid
uncommitted candidate, plan, create, validate, retry bounded source drift,
commit the verified candidate, then retire superseded snapshots in a separate
commit. A failed candidate is removed and `latest.json` is restored atomically.
`stage` remains isolated and never activates recovered state.

`publish` is the completed remote recovery-seed path. It runs the refresh or
reuse transaction, validates the exact snapshot against live sources, commits
any remaining mirror repository metadata, pushes `HEAD` to the configured
remote branch, and verifies that the remote branch resolves to the local
`HEAD`. A local-only refresh is useful for inspection, but it is not considered
published for off-machine recovery.

MCP release archives are content-addressed. A new milestone uploads an archive
only when its name, SHA-256, or size changed; unchanged public archives are
referenced in the new `mcp-bundle-index.json` by their verified prior release
tag and asset URL. Restore follows those references and verifies the archive
hash after download, so this optimization never substitutes an unverified
asset or weakens fresh-device recovery.

Every successful refresh or publish also refreshes the generated current-state
surfaces before the final full validation and commit. Routine snapshot
publication remains tag-free. `release-plan` classifies changes since the last
milestone, while explicit `release` creates and verifies an annotated semantic
tag, a public GitHub Release, and a `snapshot-manifest.json` attachment. A
retry continues from already verified branch, tag, or Release state.

Stable contracts are maintained by Codex, not rewritten by the snapshot
generator. `contract-review-plan` maps material control-plane changes to the
documents that require semantic review. Codex reads the actual diff, updates a
document when meaning changed, records `compatible` when it did not, runs the
relevant validators, and writes `contract-review-state.json` through the
explicit review command. A milestone is blocked when that receipt does not
cover the current static-file fingerprint. The same Codex review records the
semantic release impact (`patch`, `minor`, or `major`); filenames alone do not
decide compatibility impact.

After a successful production-environment finalization, the workspace closeout
hook uses this same publish path when the changed files belong to active mirror
source roots. The hook keeps the historical `post_closeout_mirror` receipt name
for compatibility, but its completed ordering is Work Git handoff, finalization
and owner checks, then mirror publish. When another same-Work-Git task remains
active, the completed task delegates that final publish and milestone instead of
starting a competing snapshot.

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
- `capability_restore_ready`: rules, owner source, stable configuration
  requirements, skills, and bootstrap evidence can be staged and validated;
  target-local provider/model selection remains an explicit reacquisition step.
- `source_freshness_checked` / `source_freshness_ok`: whether the optional live
  source comparison ran and whether the active source still matches the snapshot.
- `full_state_restore_ready`: all required encrypted external archives and
  secret re-acquisition requirements have verified receipts.
- `push.remote_verification.ok`: for `publish`, the remote branch was read back
  and matched the pushed local `HEAD`.
- `control_plane`: generated root state references `latest.json` and all static
  control-plane hashes match their declared files.

The repository may be mirror-valid before full-state readiness is achieved. A
missing archive must be reported explicitly and can never be represented as a
successful full restore.
