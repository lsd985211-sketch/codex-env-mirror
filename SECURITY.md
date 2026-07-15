# Security Model

This repository must remain private, but privacy is not treated as a substitute
for secret removal.

- Secret values never enter Git, snapshot manifests, test fixtures, or command
  output committed to the repository.
- Configuration templates use logical placeholders such as
  `<SECRET:OPENAI_API_KEY>`.
- DPAPI-protected data is considered machine/user bound. Cross-machine recovery
  uses secret re-acquisition or an owner-supported export, not blind file copy.
- External bulk state must be encrypted before leaving this machine and linked
  by archive receipt, content hash, snapshot ID, and restore owner.
- Repository validation scans known token formats and sensitive assignments.
- Git hosting should enable secret scanning/push protection and protected
  default-branch rules when a remote is configured.
- Local Git hooks are advisory; remote CI/rules are the enforcement boundary.

If a secret is detected, snapshot publication stops. Removing it from the
latest working tree is insufficient after a commit; rotate the credential and
rewrite affected history through an explicitly approved incident procedure.
