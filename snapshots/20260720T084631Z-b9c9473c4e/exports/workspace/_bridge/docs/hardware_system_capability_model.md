# Codex Windows Hardware Capability Model

## Scope

The global `hardware` system separates broad read-only hardware understanding
from narrowly governed state changes:

- `windows_hardware_owner.py`: fast all-device Windows PnP inventory, exact
  device detail, problem evidence, drivers/services, fixed hardware events,
  and snapshot diff. Parent/children topology and identity-heavy properties are
  deliberately exact-device-only so routine snapshots do not perform hundreds
  of property queries.
- `usb_device_owner.py`: USB-specific topology, storage mapping, optional
  pyserial/PyUSB/HID/FIDO2/pyscard backends, and Android status.
- `usb_device_control.py`: the only current device-state mutation owner, limited
  to explicitly confirmed rescan/restart/disable/receipt rollback operations.

No hardware owner installs or removes drivers, removes devices, changes
firmware, writes storage, formats/ejects media, changes system policy, exposes
arbitrary commands, or runs as a resident service.

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

## Evidence-Based Design

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
