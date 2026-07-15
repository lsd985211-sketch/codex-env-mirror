# Mirror Policy

## Purpose

The mirror preserves recoverable capabilities while keeping volatile state,
credentials, and large artifacts outside Git. It is a derived snapshot and
must not redefine active owners, rules, routes, or permissions.

## Snapshot Contract

- Capture all source exports under one `snapshot_id`.
- Record source authority, source path, restore template, hash, size, and mode
  for every asset.
- Publish only after staging, secret scanning, hash generation, size-budget
  enforcement, and required-source checks pass.
- Never update an existing snapshot directory.
- Update `snapshots/latest.json` atomically after publication.
- Consult the live membership owner during snapshot creation, exclude inactive
  implementations and registrations, then remove inactive lifecycle records
  from the exported membership snapshot. The mirror retains only irreversible
  block fingerprints and an active-members-only validation receipt.

## Content Classes

- `authority_export`: current owner source or a machine-readable owner snapshot.
- `configuration_template`: structurally preserved configuration with secret
  values replaced by requirement identifiers.
- `bootstrap_source`: scripts and documentation required before owners exist.
- `external_archive_reference`: encrypted bulk state managed outside Git.
- `regenerated`: runtime data that must be rebuilt instead of restored.
- `reacquire`: authentication or session state that must be obtained again.

## Prohibited Git Content

Plaintext credentials, tokens, cookies, authentication files, browser profiles,
raw sessions, logs, SQLite/DB files, downloaded resources, package caches,
runtime dependencies, executable binaries, archives, and files above the policy
size limit are prohibited.

## Restore Boundary

The bootstrap tools may plan and stage. Live activation requires owner-specific
validation, a target backup, explicit authorization, and a final closeout
receipt. Lower-level copy operations cannot bypass that boundary.
