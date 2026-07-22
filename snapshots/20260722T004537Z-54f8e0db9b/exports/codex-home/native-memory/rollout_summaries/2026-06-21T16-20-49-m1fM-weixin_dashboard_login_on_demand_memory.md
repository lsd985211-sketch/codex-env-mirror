thread_id: 019eeafc-1677-7723-992f-b31590c0fe66
updated_at: 2026-06-22T17:43:15+00:00
rollout_path: C:\Users\45543\.codex\sessions\2026\06\22\rollout-2026-06-22T00-20-50-019eeafc-1677-7723-992f-b31590c0fe66.jsonl
cwd: C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager

# Verified the unified Weixin bridge dashboard/login entry and recorded it for future reuse

Rollout context: the user asked how to access the service and whether two desktop shortcuts were still valid, then explicitly asked to record memory. The work was in the OpenClaw Weixin bridge dashboard/login area under `C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager`.

## Task 1: Record unified dashboard/login entry

Outcome: success

Preference signals:

- When the user asked, “我怎么访问这个服务，现有快捷方式有两个，是否失效”, they wanted a direct, stable access answer rather than a vague explanation. Future answers should clearly name the working primary entry and explicitly label any legacy shortcut.
- When the user later said “记录记忆”, they explicitly wanted the verified access pattern stored durably. This is a strong signal to record stable workflow changes after validation.

Key steps:

- Checked bridge state and current service behavior before writing memory.
- Confirmed the primary dashboard entry is `http://127.0.0.1:18808/` and the QR login page is intended to be `http://127.0.0.1:18808/login/`.
- Verified the desktop shortcut `C:\Users\45543\Desktop\微信桥接面板.lnk` still points to `_bridge\mobile_openclaw_bridge\open-dashboard.ps1` and remains the primary shortcut.
- Confirmed `C:\Users\Public\Desktop\OpenClaw 微信登录二维码.lnk` still points to the legacy standalone `generate-weixin-login-qr.ps1` flow, so it should be treated as legacy unless intentionally updated.
- Wrote a small ad-hoc memory file under `C:\Users\45543\.codex\memories\extensions\ad_hoc\notes\20260623-014254-weixin-dashboard-login-on-demand.md` with the verified facts and operational lesson.

Failures and how to do differently:

- Starting the QR login backend early from the launcher was not reliable because the Node service exits when the browser heartbeat is absent. The durable fix was to start it on-demand at the `/login/` request boundary.
- The old standalone QR shortcut can create confusion once the dashboard becomes the single entry point; future guidance should describe it as legacy instead of presenting two equal options.

Reusable knowledge:

- The on-demand login flow is stable enough to reuse: `18808/` serves the dashboard, `/login/` proxies to the QR login page, and the backend on `18790` is started when needed.
- Verified post-change behavior: `18808/`, `/api/state`, `/login/`, `/login/api/state`, and `/login/qr.png` returned HTTP 200; `18790/api/state` also returned HTTP 200 after on-demand startup.
- The file-level implementation note is at `mobile_dashboard.py:2639`, where `/login/` now checks and starts the login backend if needed.

References:

1. Memory note written: `C:\Users\45543\.codex\memories\extensions\ad_hoc\notes\20260623-014254-weixin-dashboard-login-on-demand.md`
2. Verified shortcuts:
   - `C:\Users\45543\Desktop\微信桥接面板.lnk` -> `_bridge\mobile_openclaw_bridge\open-dashboard.ps1`
   - `C:\Users\Public\Desktop\OpenClaw 微信登录二维码.lnk` -> legacy `generate-weixin-login-qr.ps1`
3. Verified endpoints:
   - `http://127.0.0.1:18808/`
   - `http://127.0.0.1:18808/login/`
   - `http://127.0.0.1:18808/login/api/state`
   - `http://127.0.0.1:18808/login/qr.png`
4. Backup for the change:
   - `_bridge\mobile_openclaw_bridge\backups\20260623-013655-login-on-demand`

