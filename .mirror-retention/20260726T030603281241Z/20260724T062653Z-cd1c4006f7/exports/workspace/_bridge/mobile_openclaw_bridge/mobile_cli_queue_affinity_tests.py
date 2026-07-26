from __future__ import annotations

import unittest
from unittest.mock import patch

import mobile_openclaw_cli as cli


class QueueAffinityTests(unittest.TestCase):
    def run_queue_independent(self, argv: list[str], handler_name: str) -> None:
        with (
            patch.object(cli, "load_config", return_value={}),
            patch.object(cli, "queue_from_config", side_effect=AssertionError("queue must not open")),
            patch.object(cli, handler_name, return_value={"ok": True}) as handler,
            patch.object(cli, "print_json"),
        ):
            self.assertEqual(cli.main(argv), 0)
        handler.assert_called_once()

    def test_performance_does_not_open_mobile_queue(self) -> None:
        self.run_queue_independent(["performance", "metrics"], "run_performance_command")

    def test_mcp_session_does_not_open_mobile_queue(self) -> None:
        self.run_queue_independent(["mcp-session", "doctor"], "run_mcp_session_command")

    def test_resource_process_does_not_open_mobile_queue(self) -> None:
        self.run_queue_independent(["resource-process", "metrics"], "run_resource_process_command")

    def test_backup_hygiene_does_not_open_mobile_queue(self) -> None:
        self.run_queue_independent(["backup-hygiene", "doctor"], "run_backup_hygiene_command")

    def test_backup_router_does_not_open_mobile_queue(self) -> None:
        self.run_queue_independent(["backup-router", "validate"], "run_backup_router_command")

    def test_mobile_maintenance_metrics_remains_queue_dependent(self) -> None:
        with (
            patch.object(cli, "load_config", return_value={}),
            patch.object(cli, "queue_from_config", side_effect=RuntimeError("queue opened")),
        ):
            with self.assertRaisesRegex(RuntimeError, "queue opened"):
                cli.main(["maintenance", "metrics"])


if __name__ == "__main__":
    unittest.main()
