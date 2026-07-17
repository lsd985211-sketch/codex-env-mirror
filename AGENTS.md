# Codex Environment Mirror Working Guide

## Scope And Authority

This repository is a derived, hashed recovery product. It is not a live Codex
configuration authority. Do not edit snapshot payloads, copy a snapshot over a
live installation, or treat exported owner state as current runtime truth.

The active environment owns rules, members, routes, configuration, skills,
memory, secrets, and activation. This repository owns capture integrity,
explicit asset disposition, isolated staging, and recovery evidence.

## Agent Entry

1. Read `README.md`, then `manifests/source-authorities.json`,
   `manifests/asset-dispositions.json`, `manifests/control-plane-state.json`,
   and `manifests/restore-order.json`. Use `CURRENT.md` for a human summary.
2. Run `python scripts/mirror_cli.py validate` before relying on the latest
   snapshot. This default is portable snapshot validation; only a capture-source
   owner uses `--live-sources` to compare the snapshot with active machine state.
3. Use `snapshot-manifest.json` for the exact asset, owner, classification,
   hash, restore template, membership guard, and external-state gaps.
4. Use `restore-plan` and `stage` only with an empty isolated target. Staging
   never activates recovered state.
5. In the live source environment, treat `mirror publish` as the completed
   release path: refresh or reuse the verified snapshot, commit local mirror
   repository state, push the configured remote branch, and verify the remote
   head. A local-only refresh is not a remote recovery seed.
6. After successful production-environment finalization, the workspace closeout
   hook should publish automatically when changed files match active mirror
   source roots. Do not silently downgrade that hook to local-only refresh.

## Hard Boundaries

- Keep active, generated, historical, private, and reacquired assets distinct.
- Retired members, tombstones, `.disabled`, `.system`, backups, caches, logs,
  secrets, sessions, and runtime databases do not enter capability snapshots.
- Valuable excluded state must have an explicit external-archive, reacquire,
  regenerate, generated-representation, runtime, or historical disposition.
  Unknown top-level source assets block refresh.
- Preserve text and binary assets according to their declared content kind;
  binary assets are byte-for-byte and hash-only.
- Activation remains owner-driven, version-aware, approval-gated, backed up,
  and validated. Never reconstruct permissions or secrets from placeholders.

## Editing And Validation

- Use the workspace facade for normal operations:
  `python C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager\_bridge\codex_workflow_entry.py mirror <action>`.
- Use `scripts/mirror_cli.py` directly only for bootstrap recovery or repository
  development.
- Update manifests and tests with behavior changes. Keep documents as concise
  navigation surfaces; machine-readable manifests remain the detailed source.
- A refresh is complete only after capture validation, Git commit, retention
  commit, clean status, and an isolated stage hash check.
- A publish is complete only after the refresh acceptance predicates pass, the
  local repository is clean before push, the push succeeds, and remote-head
  verification confirms the remote branch equals the local `HEAD`.
- Do not rewrite static contracts merely to make timestamps recent. The control
  plane contract and generated state determine freshness. Do not tag routine
  snapshots; milestone releases require the explicit release owner command and
  remote tag/Release readback.
