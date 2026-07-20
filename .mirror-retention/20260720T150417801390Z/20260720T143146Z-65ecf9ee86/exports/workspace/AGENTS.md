# Bridge Implementation Subtree

The Git-root `AGENTS.md` remains the workspace authority. This nested file adds
only bridge-relative entrypoints for work under `workspace/`.

- Run the workflow entry as
  `python3 _bridge/workflow_orchestrator.py plan --message "<task>" --detail micro`.
- Resolve maintenance through `_bridge/docs/maintenance_surface_map.md` and
  membership through `_bridge/system_membership.py`.
- Resolve MCP affinity, session binding, and fallback through the capability
  matrix and generated capability routes before invoking an MCP tool.
- Back up project files through `_bridge/shared/backup_router.py` before an
  authorized edit.
- Treat Windows absolute paths in startup, GUI, Office, mobile, OCR, MCP, or
  runtime database contracts as host compatibility projection dependencies,
  not evidence that this subtree or the old Windows directory is the source
  authority.
- Preserve the one-way publication boundary: WSL Work Git to Windows bare Git
  to validated recovery mirror.
