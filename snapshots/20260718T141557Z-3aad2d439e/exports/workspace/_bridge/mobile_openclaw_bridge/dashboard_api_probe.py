#!/usr/bin/env python3
"""Non-production API probe for the mobile dashboard.

The probe starts the dashboard against a temporary database so POST actions do
not touch the real Weixin bridge queue.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
CONFIG = ROOT / "config.local.json"
PYTHON = "python"


def note(message: str) -> None:
    print(message, flush=True)


def request_json(url: str, payload: dict | None = None) -> tuple[int, dict]:
    if payload is None:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def upload_file(url: str, path: Path) -> tuple[int, dict]:
    boundary = f"----codex-dashboard-probe-{int(time.time() * 1000)}"
    data = path.read_bytes()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        "Content-Type: text/plain\r\n\r\n"
    ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="dashboard-api-probe-") as tmp:
        tmp_path = Path(tmp)
        db_path = tmp_path / "probe.db"
        attachments_dir = tmp_path / "attachments"
        config = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
        allowed_users = (config.get("security") or {}).get("allowed_users") or []
        external_user = str(allowed_users[0] if allowed_users else "probe-user@im.wechat")
        port = 18818
        server = subprocess.Popen(
            [
                PYTHON,
                str(ROOT / "mobile_dashboard.py"),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--db",
                str(db_path),
                "--config",
                str(CONFIG),
                "--attachments-dir",
                str(attachments_dir),
            ],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        try:
            base = f"http://127.0.0.1:{port}"
            for _ in range(40):
                try:
                    status, state = request_json(f"{base}/api/state")
                    if status in {200, 500} and isinstance(state, dict):
                        break
                except Exception:
                    time.sleep(0.25)
            else:
                stderr = ""
                raise RuntimeError(f"dashboard server did not start: {stderr}")

            upload_source = tmp_path / "probe.txt"
            upload_source.write_text("dashboard api probe", encoding="utf-8")
            note("upload")
            upload_status, upload = upload_file(f"{base}/api/upload-attachment", upload_source)

            note("send")
            send_status, sent = request_json(
                f"{base}/api/send",
                {
                    "text": "dashboard api probe",
                    "external_user": external_user,
                    "receiver_account_id": "primary",
                    "attachments": [upload.get("attachment")],
                },
            )
            task_id = str(sent.get("id") or "")
            note(f"retry {task_id}")
            retry_status, retry = request_json(
                f"{base}/api/retry",
                {"task_id": task_id, "notify_weixin": False},
            )
            note(f"cancel {task_id}")
            cancel_status, cancel = request_json(
                f"{base}/api/cancel",
                {"task_id": task_id, "notify_weixin": False},
            )
            note("send-to-weixin invalid")
            direct_status, direct = request_json(
                f"{base}/api/send-to-weixin",
                {"text": "dashboard api probe", "external_user": "unknown"},
            )
            note(f"detail {task_id}")
            _, detail = request_json(f"{base}/api/task?id={task_id}")

            ok = (
                upload_status == 200
                and upload.get("ok")
                and send_status == 200
                and sent.get("ok")
                and sent.get("status") == "pending"
                and retry_status == 200
                and retry.get("ok")
                and "notify" not in retry
                and cancel_status == 200
                and cancel.get("ok")
                and "notify" not in cancel
                and direct_status == 400
                and not direct.get("ok")
                and detail.get("ok")
                and (detail.get("tasks") or [{}])[0].get("status") == "cancelled"
            )
            print(
                json.dumps(
                    {
                        "ok": bool(ok),
                        "upload_status": upload_status,
                        "send_status": send_status,
                        "retry_status": retry_status,
                        "cancel_status": cancel_status,
                        "send_to_weixin_invalid_status": direct_status,
                        "task_id": task_id,
                        "final_status": (detail.get("tasks") or [{}])[0].get("status"),
                        "event_count": len(detail.get("events") or []),
                        "db_path": str(db_path),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0 if ok else 1
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    raise SystemExit(main())
