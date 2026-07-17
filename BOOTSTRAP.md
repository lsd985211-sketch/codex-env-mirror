# Bootstrap

Use this document when the original Codex environment is unavailable.

1. Verify this repository came from the expected public remote or a trusted
   offline copy and inspect the latest tagged GitHub Release when available.
2. Run `python scripts/mirror_cli.py validate` using Python 3.11 or newer. It
   validates the fixed snapshot without requiring the publisher's live paths.
3. Read `AGENTS.md`, `manifests/source-authorities.json`,
   `manifests/asset-dispositions.json`, `manifests/control-plane-state.json`,
   `manifests/restore-order.json`, `manifests/secret-requirements.json`, and
   `manifests/external-archives.json`.
4. Run `restore-plan` against an empty isolated target directory.
5. Resolve required secrets and encrypted archive receipts without placing
   secret values in this repository.
6. Run `stage` with the exact confirmation token.
7. Validate staged hashes and generated receipts.
8. Activate each domain through its owner, with a fresh target backup and the
   target environment's current workflow gates.

Do not restore cookies, sessions, browser profiles, caches, logs, or internal
Codex databases as a default action. Recreate or import them only when their
owner explicitly supports the target Codex version.
