---
name: cloakbrowser-ops
description: Plan explicitly authorized CloakBrowser compatibility work in an isolated profile when normal Chrome, DevTools, Playwright, HTTP, or GUI routes cannot meet the requirement. Use for governed wrapper/binary diagnostics and never as the default browser route.
---

# CloakBrowser Ops

## Route

1. Use the normal browser chain first unless the user explicitly requests CloakBrowser or the task has a verified compatibility requirement.
2. Run `python _bridge/cloakbrowser_owner.py plan --task "<task>"`. Add `--authorized` only when the task is explicitly authorized.
3. Treat the Python wrapper and patched browser binary as separate resources. The resource layer may manage the wrapper; binary acquisition requires source, license, size, hash, and save-path evidence.
4. Launch only with an explicit `CLOAKBROWSER_BINARY_PATH`, a dedicated user-data directory, and per-process network settings.
5. Verify visible or machine-readable browser state and close the isolated process when complete.

## Boundaries

- Do not trigger the wrapper's implicit binary download path.
- Do not replace the default Chrome, Playwright, DevTools, in-app browser, or GUI routes.
- Do not reuse the user's normal browser profile, cookies, or global proxy settings.
- Do not represent wrapper installation as binary availability or task authorization.
- Respect the patched Chromium binary's separate license and distribution terms.

## Diagnostics

- Snapshot: `python _bridge/cloakbrowser_owner.py snapshot`
- Doctor: `python _bridge/cloakbrowser_owner.py doctor`
- Validation: `python _bridge/cloakbrowser_owner.py validate`
