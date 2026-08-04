# records Maintenance Surfaces

Authority: maintenance contracts for this system. Load this shard only
after the maintenance registry selects the system. The compact index at
`../maintenance_surface_map.md` is navigation only and does not duplicate
the contracts below.

| Surface | Owns | Does Not Own | Usual Entry |
| --- | --- | --- | --- |
| `shared/record_store_maintenance.py` | Record/index growth, queryability, and persistence of derived governance tables across index rebuilds | Business task completion semantics or direct domain repair | `snapshot`, `doctor`, `repair-plan`, `index --apply`, `validate`, `metrics` |
| `shared/migration_ledger.py` | Immutable migration plans plus append-only applied/verified/rollback events in the existing record-store database | Moving files, changing domain state, or replacing owner-specific apply commands | `snapshot`, `validate`; domain owners call `create_operation` and `append_event` |
| `shared/incident_index.py` + `shared/codex_reporter.py` | Cross-day incident families, report occurrences, unique maintenance-run denominator, and publishable failure-rate metrics | Report generation, retries, process repair, or treating unresolved historical rows as denominator evidence | `codex_reporter.py incident-rebuild [--apply]`, `incident-metrics [--kind ...]`, `metrics` |
