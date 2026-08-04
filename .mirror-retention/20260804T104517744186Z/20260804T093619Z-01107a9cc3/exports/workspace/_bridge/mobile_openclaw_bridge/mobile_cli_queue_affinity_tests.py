from __future__ import annotations

import unittest
from unittest.mock import patch

import mobile_openclaw_cli as cli
from maintenance_command_cli import run_resource_process_command


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

    def test_resource_process_cleanup_parser_and_adapter_preserve_authorization_refs(self) -> None:
        args = cli.build_parser().parse_args(
            [
                "resource-process",
                "cleanup",
                "--safe-apply",
                "--apply",
                "--authorization-grant-ref",
                "scoped-authorization:grant:test",
                "--authorization-thread-id",
                "thread-test",
                "--authorization-operation-id",
                "operation-test",
                "--authorization-state-root",
                "/tmp/authorization-test",
            ]
        )
        with (
            patch("resource_process_doctor.process_snapshot", return_value={}),
            patch("resource_process_doctor.cleanup_orphan_candidates", return_value={"ok": True}) as cleanup,
        ):
            result = run_resource_process_command(args)
        self.assertTrue(result["ok"])
        cleanup.assert_called_once_with(
            apply=True,
            safe_apply=True,
            include_protected=False,
            groups=[],
            min_age_minutes=15.0,
            authorization_grant_ref="scoped-authorization:grant:test",
            authorization_thread_id="thread-test",
            authorization_operation_id="operation-test",
            authorization_state_root="/tmp/authorization-test",
        )

    def test_resource_layer_smoke_does_not_open_mobile_queue(self) -> None:
        self.run_queue_independent(["resource-layer-smoke-check"], "resource_layer_smoke_check")

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
