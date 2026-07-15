# Mirror Policy

## Purpose

The mirror preserves recoverable capabilities while keeping volatile state,
credentials, and large artifacts outside Git. It is a derived snapshot and
must not redefine active owners, rules, routes, or permissions.

## Snapshot Contract

- Capture all source exports under one `snapshot_id`.
- Record source authority, source path, restore template, hash, size, mode, and
  `content_kind` for every asset.
- Publish only after staging, text secret scanning, text/binary hash generation,
  source-coverage verification, size-budget enforcement, generated-source
  checks, and required-source checks pass.
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

## Source-Specific Asset Policy

Global extensions are only a conservative default. A source may explicitly add
text extensions, approved binary extensions, exact filenames, or extensionless
files when those assets are required to restore that source's capability.
Approved binary assets are copied byte-for-byte and verified by size and SHA-256;
they are never text-decoded, secret-scanned as text, or rewritten by membership
sanitization. Coverage-required sources must contain every currently eligible
asset and no stale asset.

The active user-skill source excludes `.disabled`, `.system`, backups, caches,
compiled files, and junk. Fonts, OOXML schemas, packaged skill resources,
reference images, templates, shell scripts, licenses, and other declared skill
dependencies remain recoverable. Platform, bundled, and plugin skills are
reacquired from the recorded Codex/plugin inventory rather than copied from
platform-managed directories.

## Prohibited Git Content

Plaintext credentials, tokens, cookies, authentication files, browser profiles,
raw sessions, logs, SQLite/DB files, downloaded resources, package caches,
runtime dependencies, executable programs, unapproved archives or binaries,
and files above the source policy size limit are prohibited. The raw CC Switch
database, full checkpoint history, plugin cache payloads, and runtime databases
remain external; only sanitized semantic or version evidence enters Git.

## Restore Boundary

The bootstrap tools may plan and stage. Live activation requires owner-specific
validation, a target backup, explicit authorization, and a final closeout
receipt. Lower-level copy operations cannot bypass that boundary.
