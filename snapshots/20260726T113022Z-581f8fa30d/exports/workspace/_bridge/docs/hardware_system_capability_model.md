# Codex Cross-Platform Hardware Capability Model

## Scope

The global `hardware` system separates broad read-only hardware understanding
from narrowly governed state changes:

- `hardware_system_owner.py`: the stable read-only system facade. It routes
  requests to a platform authority and can combine receipts, but it owns no
  discovery implementation and inherits no control permission.
- `wsl_hardware_owner.py`: WSL-visible kernel, block, forwarded USB, exposed
  PCI, and NVIDIA compute projections. It explicitly declares that this is not
  complete Windows host truth.
- `windows_hardware_owner.py`: fast all-device Windows PnP inventory, exact
  device detail, problem evidence, drivers/services, fixed hardware events,
  and snapshot diff. Parent/children topology and identity-heavy properties are
  deliberately exact-device-only so routine snapshots do not perform hundreds
  of property queries.
- `usb_device_owner.py`: USB-specific topology, storage mapping, optional
  pyserial/PyUSB/HID/FIDO2/pyscard backends, Android status, and read-only
  USB/IP transport state.
- `usb_device_control.py`: the only current device-state mutation owner, limited
  to explicitly confirmed rescan/restart/disable/receipt rollback operations.

No hardware owner installs or removes drivers, removes devices, changes
firmware, writes storage, formats/ejects media, changes system policy, exposes
arbitrary commands, or runs as a resident service.

## Platform Authority

Windows is authoritative for physical devices, PnP and drivers, host disks and
reliability counters, battery, Bluetooth, displays, sensors, firmware, and
host event logs. WSL is authoritative only for what the Linux kernel can
actually see: virtual block devices, USB/IP-attached devices, exposed PCI
functions, and the GPU compute projection supplied by the Windows driver.

`hardware_system_owner.py snapshot --platform all` returns one receipt per
platform. A deferred receipt is explicit incomplete work, not a successful
cross-platform snapshot. Owners never infer that an executable visible through
WSL interop ran on its authoritative platform.

WSL requires `lsblk` and `udevadm`. `usbutils` and `pciutils` are optional,
request-driven tools. This host uses a non-root Ubuntu package extraction under
`~/.local/share/codex/hardware-tools` because interactive sudo was unavailable;
fixed wrappers in `~/.local/bin` expose only `lsusb` and `lspci`. Host-only
SMART, Bluetooth, battery, firmware, and display tools are deliberately not
duplicated into WSL.

## Storage Consumer Handoff

`usb_device_owner.py storage --drive-letter <letter>` is the reusable boundary
for content systems working on removable storage. It returns disk, partition,
volume, stable identity, and a `safe_for_content_mutation` health decision. A
content owner must bind its plan to the fingerprint and refresh it immediately
before mutation.

The handoff transfers facts, not permissions. Audio, document, photo, backup,
or other content owners remain responsible for their own path, overwrite,
transaction, rollback, and acceptance rules and do not acquire format, eject,
partition, driver, firmware, or generic device-control authority. This allows
systems to cooperate without duplicating hardware discovery or weakening the
hardware boundary.

## Windows MTP And WSL USB/IP Handoff

`usb_device_owner.py transport [--busid <bus-port>]` is the read-only source
of truth for usbipd-win transport ownership. It normalizes the device state as
`windows_available`, `shared_waiting_for_wsl`, or `wsl_attached`; it never
binds, attaches, detaches, or changes a driver.

Windows MTP and WSL USB/IP are mutually exclusive transport consumers for the
same physical device. Before any transfer, the caller must inspect the exact
bus ID, identify any active Windows content workflow, and coordinate with
active tasks that may own MTP enumeration or WSL media access. `Attached`
means WSL owns the device and Windows MTP is unavailable. `Shared` only means
usbipd can transfer it; a live Windows MTP/Explorer handle can still cause an
attach attempt to fail with `Device busy`.

The handoff order is therefore: diagnose with the read-only owner, obtain the
user's requested target platform and active-task agreement, perform any
explicit usbipd control outside the read-only owner, then verify the target
platform enumerates the same VID/PID and serial. Returning the device follows
the same coordination rule. A device descriptor failure (for example Code 43)
is a Windows PnP enumeration fault and must be repaired and verified in
Windows before considering USB/IP transfer.

## Evidence-Based Design

Microsoft documents that WSL 2 runs a managed virtual machine and that USB
devices require explicit USB/IP attachment. GPU compute in WSL uses a Windows
host driver projection, so Linux GPU visibility is a consumer view rather than
driver ownership.

- [Connect USB devices to WSL](https://learn.microsoft.com/en-us/windows/wsl/connect-usb)
- [Comparing WSL versions](https://learn.microsoft.com/en-us/windows/wsl/compare-versions)
- [GPU accelerated ML training in WSL](https://learn.microsoft.com/en-us/windows/wsl/tutorials/gpu-compute)
- [Enable NVIDIA CUDA on WSL](https://learn.microsoft.com/en-us/windows/ai/directml/gpu-cuda-in-wsl)
- [Get-PhysicalDisk](https://learn.microsoft.com/en-us/powershell/module/storage/get-physicaldisk)
- [Get-StorageReliabilityCounter](https://learn.microsoft.com/en-us/powershell/module/storage/get-storagereliabilitycounter)

Microsoft documents CfgMgr32 and SetupAPI as the user-mode foundations for
device identity, properties, interfaces, and topology. The unified property
model includes parent and children relationships, so unknown topology must not
be treated as a verified leaf.

- [Porting from SetupAPI to CfgMgr32](https://learn.microsoft.com/windows-hardware/drivers/install/porting-from-setupapi-to-cfgmgr32)
- [CM_Get_Child](https://learn.microsoft.com/windows/win32/api/cfgmgr32/nf-cfgmgr32-cm_get_child)
- [Using a device interface](https://learn.microsoft.com/windows-hardware/drivers/install/using-a-device-interface)

For event-driven applications on Windows 8 and later,
`CM_Register_Notification` avoids requiring a window handle. The current owner
keeps event collection process-bounded; a future event-driven watcher should
use this API rather than adding an always-running polling process.

- [Device interface arrival and removal notifications](https://learn.microsoft.com/windows-hardware/drivers/install/registering-for-notification-of-device-interface-arrival-and-device-removal)
- [RegisterDeviceNotification](https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-registerdevicenotificationw)
- [DeviceWatcher enumeration](https://learn.microsoft.com/windows/apps/develop/devices-sensors/enumerate-devices)

PnPUtil is built into Windows and Microsoft recommends it over DevCon. Its
read-only enumeration can expose relations, drivers, services, stacks,
interfaces, properties, and resources. Its broad selectors and mutation flags
(`/deviceid`, `/class`, `/bus`, `/subtree`, `/force`, `/reboot`, and
`/remove-device`) remain outside the guarded control owner.

- [PnPUtil command syntax](https://learn.microsoft.com/windows-hardware/drivers/devtest/pnputil-command-syntax)
- [PnPUtil examples](https://learn.microsoft.com/windows-hardware/drivers/devtest/pnputil-examples#enum-devicetree)
- [DevCon migration guidance](https://learn.microsoft.com/windows-hardware/drivers/devtest/devcon-migration)

User-mode I/O is device-model specific and is not a generic hardware-control
permission. WinUSB requires `Winusb.sys` as the device function driver; HID,
serial, and smart-card devices use separate APIs and resource owners.

- [WinUSB for developers](https://learn.microsoft.com/windows-hardware/drivers/usbcon/introduction-to-winusb-for-developers)
- [HID API](https://learn.microsoft.com/windows-hardware/drivers/hid/hid-api)
- [USB serial driver](https://learn.microsoft.com/windows-hardware/drivers/usbcon/usb-driver-installation-based-on-compatible-ids)
- [Smart Card Resource Manager](https://learn.microsoft.com/windows/win32/secauthn/smart-card-resource-manager)
- [SCardGetStatusChange](https://learn.microsoft.com/windows/win32/api/winscard/nf-winscard-scardgetstatuschangew)

Device and driver installation failures should use the existing Windows event
channels and `SetupAPI.dev.log` as evidence. These sources are diagnostic and
must not become an excuse for ad hoc driver mutation.

- [SetupAPI logging](https://learn.microsoft.com/windows-hardware/drivers/install/setupapi-logging--windows-vista-and-later-)

## Query Layers

- `snapshot`, `problems`, and `classes` use one `Win32_PnPEntity` query plus one
  `Win32_PnPSignedDriver` query. They retain broad class, state, problem,
  service, manufacturer, and driver visibility without claiming topology.
- `device --instance-id <exact-id>` uses fixed PnP property keys for one exact
  identity and returns parent, children, container, hardware/compatible IDs,
  location paths, bus metadata, removal policy, and a stable fingerprint.
- `events` uses only the fixed Windows event channels declared by the owner.

The snapshot result explicitly reports `detail_level: fast_inventory` and
`topology_scope: exact_device_only`; callers must not infer leaf status from a
global snapshot.

## Future Admission Order

1. Add read-only owner evidence for a hardware domain.
2. Validate stable identity, topology, capability, and problem-state semantics.
3. Add process-bounded event observation when snapshots are insufficient.
4. Admit state changes only through a separate control owner with an allowlist,
   exact target identity, explicit confirmation, post-state acceptance,
   durable receipt, rollback boundary, and protected-class negative tests.
5. Keep firmware, raw storage, driver lifecycle, broad selectors, and generic
   user-mode I/O outside the default Codex hardware permission boundary.
