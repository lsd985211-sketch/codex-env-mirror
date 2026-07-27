# audio Maintenance Surfaces

Authority: maintenance contracts for this system. Load this shard only
after the maintenance registry selects the system. The compact index at
`../maintenance_surface_map.md` is navigation only and does not duplicate
the contracts below.

| Surface | Owns | Does Not Own | Usual Entry |
| --- | --- | --- | --- |
| `music_library_owner.py` with `music_library_planner.py` and `music_library_transaction.py` | Reusable local music-library inventory, reviewed correction consumption, artist/album/track layout planning, lyrics and artwork association, duplicate/conflict quarantine, USB storage identity binding, exact-plan confirmation, same-volume non-overwriting moves, durable journals, interruption recovery, rollback, and full SHA-256 post-state validation. It reuses `usb_device_owner.py storage` for hardware admission and `ffprobe`/the existing audio toolkit stack for media inspection. | Network research, arbitrary correction fields or target paths, device control, format/eject/partition operations, deletion, overwrite, cross-volume copy, transcoding, tag rewriting, or accepting a transport/move result without hash validation | Read-only `inventory --root <path>`, `doctor [--root <path>]`, `validate [--plan <json> --expected source|applied]`; plan `plan --root <path> --corrections <json>`; explicit `apply --plan <json> --confirm-plan-id <id>` and `rollback --plan <json> --confirm-plan-id <id>`; regression via `python -m unittest _bridge\music_library_owner_tests.py` |
