# drafts Maintenance Surfaces

Authority: maintenance contracts for the drafts system. Load this shard only
after the maintenance registry selects the system. The compact index at
`../maintenance_surface_map.md` is navigation only and does not duplicate the
contract below.

| Surface | Owns | Does Not Own | Usual Entry |
| --- | --- | --- | --- |
| `draft_governance.py` | Draft artifact metadata, two-axis content/workflow state, draft index consistency, and retained-reference validation | Pending decision ownership, closeout queue creation, approval execution, active incident tracking, or inferring state from file names | `snapshot`, `validate` |
