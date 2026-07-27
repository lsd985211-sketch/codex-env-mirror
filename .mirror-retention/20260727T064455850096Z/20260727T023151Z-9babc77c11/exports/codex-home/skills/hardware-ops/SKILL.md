---
name: hardware-ops
description: Route hardware work across Windows host truth and WSL-visible projections. Use for GPU, storage, PCI, battery, Bluetooth, displays, sensors, general device inventory, and cross-platform hardware diagnosis; hand USB-specific work to windows-usb-ops.
---

# Hardware Operations

## Entry

Start with `python workspace/_bridge/hardware_system_owner.py routes`. Use its
platform authority instead of inferring ownership from the current shell.

- Windows owns physical PnP devices, drivers, disks, reliability counters,
  battery, Bluetooth, displays, sensors, and host event evidence.
- WSL owns only Linux-visible virtual or forwarded block, USB, PCI, and GPU
  projections. A WSL result is never proof of complete host hardware state.
- USB transport and device-specific diagnosis use `$windows-usb-ops` and the
  USB owners. Device state changes remain with `usb_device_control.py`.

## Workflow

1. Identify the requested fact and its authoritative platform.
2. Run the smallest owner command for that platform. Use
   `hardware_system_owner.py snapshot --platform all` only when both receipts
   are actually required; a deferred receipt means the result is incomplete.
3. For WSL evidence, consume `wsl_hardware_owner.py snapshot`; optional tool
   absence is advisory unless the requested fact depends on that tool.
4. For Windows evidence, run the projected Windows owner on Windows. Do not
   treat a WSL PowerShell path or Windows executable discovery as execution.
5. Require a separate control-owner plan, exact target, confirmation, and
   post-state receipt for any mutation.

Load [platform-routing.md](references/platform-routing.md) only when choosing
tools, interpreting WSL GPU/USB visibility, or planning recovery on a new host.

## Boundaries

Do not install duplicate WSL tools for host-only facts. Do not expose arbitrary
PowerShell, shell, ADB, Fastboot, firmware, driver, mount, format, eject, or
storage-write operations through this skill.
