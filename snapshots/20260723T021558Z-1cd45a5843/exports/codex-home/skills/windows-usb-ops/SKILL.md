---
name: windows-usb-ops
description: Diagnose Windows USB, Android MTP, device descriptor, and WSL USB/IP handoff problems. Use when a physical USB device fails to enumerate, has a PnP error, Windows MTP conflicts with WSL access, or an exact device must be safely transferred between Windows and WSL.
---

# Windows USB Operations

## Scope

Use `$hardware-ops` when the request is not specifically USB or when Windows
host truth must be compared with a WSL-visible projection.

Route USB inventory and transport facts through
`workspace/_bridge/usb_device_owner.py`. It is read-only. Route PnP rescan or
an eligible leaf restart through `usb_device_control.py` only after an exact
owner plan and user confirmation.

## Diagnose

1. Check active tasks in the same repository before any USB state operation.
   Name the exact VID/PID, serial or bus ID, target platform, and whether a
   task owns Windows MTP or WSL media access.
2. Run `usb_device_owner.py snapshot --full` and `events` on Windows. Treat a
   PnP problem such as Code 43 as a Windows enumeration fault; repair and
   verify it in Windows before considering USB/IP.
3. Run `usb_device_owner.py transport --busid <bus-port>` on Windows before a
   platform transfer. Use its normalized state rather than inferring ownership
   from a device name or a successful driver installation.
4. Interpret `windows_available` as Windows-owned, `shared_waiting_for_wsl` as
   transferable but potentially occupied by MTP, and `wsl_attached` as WSL-owned.
   Windows MTP and WSL USB/IP are mutually exclusive for the same device.

## Controlled Handoff

Only after the user requests a transfer and active tasks agree, use the
existing approved control path for the exact bus ID. Do not expose, script, or
generalize `usbipd` bind, attach, or detach from this skill. After a handoff,
verify the target runtime sees the same VID/PID and serial; a transport command
returning successfully is not sufficient acceptance evidence.

When WSL owns an attached device, do not claim that Windows Explorer can still
read its MTP storage. Before returning it to Windows, coordinate with every
active WSL consumer and verify the detach target explicitly.

## Boundaries

Do not install drivers, change firmware, format/eject media, use arbitrary
vendor commands, start an ADB server, or transfer device data under this skill.
For MTP content work, hand the read-only device identity and transport result to
the dedicated content owner; it retains its own path, copy, integrity, and
rollback rules.

Do not use `Shell.Application.CopyHere` as a hidden or scheduled MTP transfer
backend. It can block without a consumable result, leave zero-byte temporary
files, and orphan Explorer progress UI after the caller exits. A headless MTP
copy owner must use a bounded Windows Portable Devices API backend with an
explicit cancellation path, per-file progress, atomic temporary-file commit,
source/destination byte receipts, and cleanup on timeout. Until that backend is
validated, MTP owners may snapshot and plan only; they must report
`apply_available=false` instead of exposing a scheduled-task or shell fallback.
