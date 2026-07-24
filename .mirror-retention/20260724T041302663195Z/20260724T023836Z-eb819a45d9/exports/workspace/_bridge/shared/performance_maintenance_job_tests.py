from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import performance_maintenance_job as job


class WindowsBridgeProbeTests(unittest.TestCase):
    def test_probe_uses_windows_host_owner_once(self) -> None:
        with patch.object(job, "run_json", return_value={"ok": True}) as run_json:
            self.assertEqual(job.run_windows_bridge_probe(), {"ok": True})

        command = run_json.call_args.args[0]
        self.assertEqual(command[0], "powershell.exe")
        self.assertIn("-NonInteractive", command)
        self.assertEqual(command[-2:], ["-Mode", "dry-run"])
        self.assertIn("wsl.localhost", command[command.index("-File") + 1].lower())

    def test_queue_probe_failure_is_a_failed_required_step(self) -> None:
        probe = {"ok": False, "reason": "host_probe_failed"}
        step = job.Step("bridge_queue_pre_appserver_restart_guard", ok=bool(probe.get("ok")), payload=probe)

        self.assertFalse(step.ok)
        self.assertFalse(not any(item.ok is False and not item.name.endswith("_doctor") for item in [step]))

    def test_maintenance_reuses_one_host_probe_receipt(self) -> None:
        host_receipt = {
            "ok": True,
            "execution_affinity": "windows_host",
            "queue": {},
            "owners_before": [],
        }
        with TemporaryDirectory() as temp:
            with (
                patch.object(job, "RECORD_ROOT", Path(temp) / "records"),
                patch.object(job, "STATE_PATH", Path(temp) / "state.json"),
                patch.object(job, "run_json", return_value={"ok": True, "severity": "ok"}),
                patch.object(job, "run_windows_bridge_probe", return_value=host_receipt) as probe,
                patch.object(job, "read_json", return_value={}),
                patch.object(job, "write_json"),
                patch.object(job, "write_raw_payload", return_value={}),
                patch.object(job, "stable_digest", return_value="digest"),
                patch.object(job, "apply_defender_repairs", return_value=job.Step("defender_safe_repair", ok=True)),
            ):
                result = job.run_maintenance(apply_safe=False)

        self.assertTrue(result["ok"])
        probe.assert_called_once_with()
        bridge_payloads = [
            step["payload"]
            for step in result["steps"]
            if step["name"] in {"bridge_queue_pre_appserver_restart_guard", "bridge_appserver_idle_restart_dry_run"}
        ]
        self.assertEqual(bridge_payloads, [host_receipt, host_receipt])


if __name__ == "__main__":
    unittest.main()
