# Hardware Platform Routing Reference

## Authority Matrix

| Need | Primary owner | Supporting evidence |
| --- | --- | --- |
| Physical device, driver, PnP problem, display, battery, Bluetooth | `windows_hardware_owner.py` | Windows PnP/CIM and fixed event sources |
| Physical disk identity and reliability | Windows storage owner path | `Get-PhysicalDisk`, `Get-Disk`, `Get-StorageReliabilityCounter` |
| WSL block devices and mounts | `wsl_hardware_owner.py` | `lsblk` |
| USB device attached through USB/IP | `usb_device_owner.py` then `wsl_hardware_owner.py` | `usbipd` state, then `lsusb` |
| WSL-visible PCI projection | `wsl_hardware_owner.py` | `lspci` |
| NVIDIA compute projection in WSL | `wsl_hardware_owner.py` | `/usr/lib/wsl/lib/nvidia-smi` |
| USB state change | `usb_device_control.py` | exact owner plan and post-state receipt |
| MTP public media | `mtp_media_archive_owner.py` | exact device/storage snapshot and manifest |

## Tool Policy

`lsblk` and `udevadm` are required WSL baseline tools. `usbutils` and
`pciutils` are optional but should be installed when USB/IP or PCI projection
inspection is needed. WSL uses the host NVIDIA driver projection; do not
install a Linux display driver inside WSL. SMART, battery, Bluetooth, firmware,
and host display tools remain Windows-side unless WSL has direct device
ownership and a declared consumer.

The user-local fallback installation root is
`~/.local/share/codex/hardware-tools`; fixed `~/.local/bin/lsusb` and `lspci`
wrappers may load libraries only from that root. This is a non-root fallback,
not a second package database authority. A recovered host may instead install
Ubuntu `usbutils` and `pciutils` system packages and then remove the fallback.

## Official Basis

- [Connect USB devices to WSL](https://learn.microsoft.com/en-us/windows/wsl/connect-usb)
- [GPU accelerated ML training in WSL](https://learn.microsoft.com/en-us/windows/wsl/tutorials/gpu-compute)
- [Enable NVIDIA CUDA on WSL](https://learn.microsoft.com/en-us/windows/ai/directml/gpu-cuda-in-wsl)
- [Comparing WSL versions](https://learn.microsoft.com/en-us/windows/wsl/compare-versions)
- [PnPUtil command syntax](https://learn.microsoft.com/en-us/windows-hardware/drivers/devtest/pnputil-command-syntax)
- [Get-PhysicalDisk](https://learn.microsoft.com/en-us/powershell/module/storage/get-physicaldisk)
- [Get-StorageReliabilityCounter](https://learn.microsoft.com/en-us/powershell/module/storage/get-storagereliabilitycounter)
