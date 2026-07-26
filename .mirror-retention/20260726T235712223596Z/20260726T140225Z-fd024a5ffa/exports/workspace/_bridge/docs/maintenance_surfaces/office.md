# office Maintenance Surfaces

Authority: maintenance contracts for this system. Load this shard only
after the maintenance registry selects the system. The compact index at
`../maintenance_surface_map.md` is navigation only and does not duplicate
the contracts below.

| Surface | Owns | Does Not Own | Usual Entry |
| --- | --- | --- | --- |
| `cli_anything_microsoft_office/agent-harness` | Bounded native Word, Excel, and PowerPoint create/inspect/edit/export operations, versioned operation allowlists, transactional output, per-operation receipts, and real Office E2E | Arbitrary COM, VBA/macros, visible-session reuse, OOXML-only content planning, central queues, or document state duplication | `cli-anything-microsoft-office --json system status`; app `operations|inspect|edit`; focused pytest and opt-in real Office E2E |
