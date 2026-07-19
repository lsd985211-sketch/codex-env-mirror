# Codex Audio System Capability Model

## Scope

The `audio` system combines existing media operations with a governed music
library owner. It is a content system, not a device-control system.

- `audio_toolkit/audio_toolkit.py`: stream and tag inspection, analysis,
  conversion, trimming, normalization, silence detection, transcription, and
  lyric generation/alignment.
- `music_library_owner.py`: the public music-library CLI and admission facade.
- `music_library_planner.py`: read-only inventory, corrections, classification,
  sidecar association, duplicate handling, and deterministic target planning.
- `music_library_transaction.py`: exact-plan apply, journaled same-volume moves,
  interruption recovery, rollback, and full hash validation.

## Hardware Handoff

Removable-media work uses a narrow dependency on the `hardware` system:

1. `usb_device_owner.py storage --drive-letter <letter>` identifies the disk,
   partition, volume, health state, read-only state, and stable fingerprint.
2. The music plan stores that fingerprint but receives no format, eject,
   partition, driver, firmware, or generic device-control permission.
3. Apply and rollback refresh the storage binding immediately before mutation.
4. A changed fingerprint, non-USB bus, offline/read-only disk, or unhealthy
   disk blocks the content operation.
5. The music owner then validates source size and SHA-256, target absence, and
   same-root paths before any move.

This preserves hardware ownership while allowing audio content operations to
consume verified device facts.

## Resource And Tool Reuse

- External artist, album, year, track-order, or version research belongs to the
  resource layer. It produces a reviewed `music_library_owner.v1.corrections`
  file; it cannot inject target paths or commands.
- The planner reuses `ffprobe` and the existing audio toolchain for local media
  facts. It does not add another transcoder, tag writer, or transcription stack.
- The owner keeps corrections separate from source media. WAV/FLAC content and
  embedded tags are not rewritten during organization.

## Mutation Contract

- No deletion, overwrite, cross-volume copy, transcoding, or tag rewriting.
- Plan files carry a deterministic integrity-bound ID and full file hashes.
- Apply requires the exact plan ID, one shared apply/rollback lock, a durable
  JSONL journal, post-move hash checks, and final full-library hash validation.
- Duplicate files, metadata conflicts, unresolved versions, suspected
  truncation, and orphan sidecars move only to clearly named review areas.
- Rollback restores original relative paths and validates their hashes; plans,
  journals, inventories, and receipts remain for audit.

## Validation

```powershell
python _bridge\music_library_owner.py validate
python -m unittest _bridge\music_library_owner_tests.py
python _bridge\usb_device_owner.py validate
python _bridge\system_membership.py validate
python _bridge\workflow_orchestrator.py validate
```
