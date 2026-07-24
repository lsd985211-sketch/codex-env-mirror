from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock


BRIDGE = Path(__file__).resolve().parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

import codex_state_repair as repairer  # noqa: E402


GLOBAL_TEXT = 'model = "gpt-test"\n'
PROJECT_TEXT = 'sandbox_mode = "danger-full-access"\n'
STATE_TEXT = '{"keep":true}'


class CodexStateRepairWriteTests(unittest.TestCase):
    def test_codex_desktop_running_accepts_main_process_evidence(self) -> None:
        with mock.patch.object(
            repairer,
            "query_desktop_host_processes",
            return_value=[{"pid": 1}],
        ) as query:
            self.assertTrue(repairer.codex_desktop_running())

        query.assert_called_once_with(main_only=True)

    def test_codex_desktop_running_falls_back_to_process_family(self) -> None:
        with mock.patch.object(
            repairer,
            "query_desktop_host_processes",
            side_effect=[[], [{"pid": 2}]],
        ) as query:
            self.assertTrue(repairer.codex_desktop_running())

        self.assertEqual(
            [mock.call(main_only=True), mock.call(main_only=False)],
            query.call_args_list,
        )

    def test_codex_desktop_running_returns_false_only_after_two_empty_queries(self) -> None:
        with mock.patch.object(
            repairer,
            "query_desktop_host_processes",
            side_effect=[[], []],
        ):
            self.assertFalse(repairer.codex_desktop_running())

    def test_codex_desktop_running_fails_closed_when_query_is_unavailable(self) -> None:
        with mock.patch.object(
            repairer,
            "query_desktop_host_processes",
            side_effect=RuntimeError("cim unavailable"),
        ):
            self.assertTrue(repairer.codex_desktop_running())

    def test_baseline_host_path_translates_windows_path_for_wsl_repair(self) -> None:
        self.assertEqual(
            Path("/mnt/c/Users/45543/.codex/config.toml"),
            repairer.baseline_host_path(r"C:\Users\45543\.codex\config.toml"),
        )

    def test_repair_global_state_uses_platform_resolved_baseline_path(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            state_path = Path(raw) / "global-state.json"
            state_path.write_text('{"keep":true}', encoding="utf-8")
            baseline = {
                "global_state": r"C:\Users\45543\.codex\.codex-global-state.json",
                "global_state_required": {"desktop.ready": True},
            }
            with mock.patch.object(repairer, "baseline_host_path", return_value=state_path) as resolve:
                state, changed = repairer.repair_global_state(baseline)

        resolve.assert_called_once_with(baseline["global_state"])
        self.assertTrue(state["desktop"]["ready"])
        self.assertEqual(["global_state_value_set_desktop.ready"], changed)

    def test_repair_skips_optional_host_project_config(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            files = self.make_fixture(Path(raw))
            baseline = json.loads(files[3].read_text(encoding="utf-8"))
            baseline["project_config_required"] = False
            files[1].unlink()
            files[3].write_text(
                json.dumps(baseline, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            result = self.run_repair(files[3], files[4])

            self.assertFalse(result["write_decisions"]["project_config"])

    def make_fixture(self, root: Path) -> tuple[Path, Path, Path, Path, Path]:
        global_config = root / "global-config.toml"
        project_config = root / "project-config.toml"
        global_state = root / "global-state.json"
        baseline_path = root / "baseline.json"
        backup_root = root / "backups"
        global_config.write_text(GLOBAL_TEXT, encoding="utf-8", newline="\n")
        project_config.write_text(PROJECT_TEXT, encoding="utf-8", newline="\n")
        global_state.write_text(STATE_TEXT, encoding="utf-8", newline="\n")
        baseline = {
            "global_config": str(global_config),
            "project_config": str(project_config),
            "global_state": str(global_state),
            "expected_mcp": {},
            "decommissioned_mcp": {},
            "expected_plugins": [],
            "expected_marketplaces": {},
            "global_required_values": {},
            "project_required_values": {},
            "global_state_required": {},
        }
        baseline_path.write_text(
            json.dumps(baseline, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return global_config, project_config, global_state, baseline_path, backup_root

    def run_repair(self, baseline_path: Path, backup_root: Path) -> dict:
        with (
            mock.patch.object(repairer, "BASELINE_PATH", baseline_path),
            mock.patch.object(repairer, "BACKUP_ROOT", backup_root),
            mock.patch.object(repairer, "HUB_MANAGED_MCP_NAMES", frozenset()),
            mock.patch.object(repairer, "refresh_runtime_artifacts", return_value=[]),
        ):
            return repairer.repair(dry_run=False, runtime_validation=False)

    @staticmethod
    def set_old_timestamp(*paths: Path) -> dict[Path, int]:
        old_ns = 1_700_000_000_000_000_000
        for path in paths:
            os.utime(path, ns=(old_ns, old_ns))
        return {path: path.stat().st_mtime_ns for path in paths}

    def test_no_drift_creates_no_backup_and_touches_no_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            files = self.make_fixture(Path(raw))
            tracked = files[:4]
            before = self.set_old_timestamp(*tracked)

            result = self.run_repair(files[3], files[4])

            self.assertEqual([], result["written"])
            self.assertIsNone(result["backup_dir"])
            self.assertFalse(files[4].exists())
            self.assertEqual(before, {path: path.stat().st_mtime_ns for path in tracked})

    def test_wsl_active_config_defers_runtime_roots_and_uses_host_selection_config(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            files = self.make_fixture(Path(raw))
            host_config = Path(raw) / "host-config.toml"
            host_config.write_text(
                "[desktop]\nrunCodexInWindowsSubsystemForLinux = false\n",
                encoding="utf-8",
            )
            wsl_text = (
                'model = "gpt-test"\n'
                '[mcp_servers.node_repl]\ncommand = "/home/codexlab/.local/bin/codex-node-repl"\n'
                '[mcp_servers.custom-slash-commands]\ncommand = "python3"\n'
                '[plugins."documents@openai-primary-runtime"]\nenabled = true\n'
                '[marketplaces.openai-primary-runtime]\nsource = "/home/codexlab/.codex-app/.tmp/marketplaces/openai-primary-runtime"\n'
            )
            files[0].write_text(wsl_text, encoding="utf-8")
            baseline = json.loads(files[3].read_text(encoding="utf-8"))
            baseline.update({
                "configuration_authority": "wsl_active",
                "desktop_host_config": str(host_config),
                "expected_mcp": {
                    "node_repl": {"required": True, "command": "cmd.exe", "args": ["/d", "/c", "wrong.exe"]},
                },
                "expected_plugins": ["browser@openai-bundled"],
                "expected_marketplaces": {"openai-bundled": {"source": "C:\\\\wrong"}},
            })
            files[3].write_text(json.dumps(baseline), encoding="utf-8")
            selection = {
                "ok": True,
                "changed": False,
                "selected_value": False,
                "effective_value": False,
            }
            with (
                mock.patch.object(repairer, "BASELINE_PATH", files[3]),
                mock.patch.object(repairer, "BACKUP_ROOT", files[4]),
                mock.patch.object(repairer, "refresh_runtime_artifacts", return_value=[]),
                mock.patch.object(repairer, "ensure_desktop_environment_selection", return_value=selection) as select,
                mock.patch.object(
                    repairer,
                    "ensure_wsl_runtime_projection",
                    return_value={"ok": True, "changed": False, "status": "not_required"},
                ),
            ):
                result = repairer.repair(dry_run=False, runtime_validation=False)

            self.assertEqual(wsl_text, files[0].read_text(encoding="utf-8"))
            self.assertFalse(result["write_decisions"]["global_config"])
            self.assertNotIn("global_config", result["written"])
            self.assertEqual("wsl_runtime", result["global_config_owner"])
            self.assertEqual(str(host_config), result["desktop_host_config"])
            self.assertEqual(host_config, select.call_args.kwargs["host_config"])

    def test_explicit_mcp_registration_reconciliation_repairs_only_mcp_tables_for_wsl_config(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            files = self.make_fixture(Path(raw))
            files[0].write_text(
                '[plugins."keep@plugin"]\nenabled = true\n\n'
                '[mcp_servers.node_repl]\ncommand = "old-node"\nargs = []\n\n'
                '[mcp_servers."custom-slash-commands"]\ncommand = "python3"\nrequired = true\n\n'
                '[mcp_servers."sqlite-scratch"]\ncommand = "python3"\nrequired = true\n\n'
                '[mcp_servers."sqlite-bridge-ro"]\ncommand = "python3"\nrequired = false\n',
                encoding="utf-8",
            )
            baseline = json.loads(files[3].read_text(encoding="utf-8"))
            baseline.update({
                "configuration_authority": "wsl_active",
                "expected_mcp": {
                    "node_repl": {"required": True, "command": "node", "args": []},
                    "local-mcp-hub": {"required": False, "url": "http://127.0.0.1:18881/mcp"},
                },
            })
            files[3].write_text(json.dumps(baseline) + "\n", encoding="utf-8")
            with (
                mock.patch.object(repairer, "BASELINE_PATH", files[3]),
                mock.patch.object(repairer, "BACKUP_ROOT", files[4]),
                mock.patch.object(repairer, "refresh_runtime_artifacts", return_value=["runtime_artifact_refresh"]),
                mock.patch.object(repairer, "repair_global_state", return_value=({"unrelated": True}, ["global_state_unrelated"])),
                mock.patch.object(repairer, "ensure_desktop_environment_selection", return_value={"ok": True, "changed": False, "effective_value": False}),
            ):
                result = repairer.repair(
                    dry_run=False,
                    runtime_validation=False,
                    reconcile_mcp_registration=True,
                )
            config = tomllib.loads(files[0].read_text(encoding="utf-8"))
            servers = config["mcp_servers"]
            self.assertTrue(result["write_decisions"]["global_config"])
            self.assertFalse(result["write_decisions"]["baseline"])
            self.assertFalse(result["write_decisions"]["global_state"])
            self.assertEqual("explicit_mcp_registration", result["global_config_owner"])
            self.assertTrue(result["mcp_registration_reconciled"])
            self.assertNotIn("runtime_artifact_refresh", result["changed"])
            self.assertNotIn("global_state_unrelated", result["changed"])
            self.assertTrue(servers["node_repl"]["required"])
            self.assertEqual("http://127.0.0.1:18881/mcp", servers["local-mcp-hub"]["url"])
            self.assertNotIn("custom-slash-commands", servers)
            self.assertNotIn("sqlite-scratch", servers)
            self.assertNotIn("sqlite-bridge-ro", servers)
            self.assertTrue(config["plugins"]["keep@plugin"]["enabled"])

    def test_global_config_drift_writes_only_global_config(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            files = self.make_fixture(Path(raw))
            baseline = json.loads(files[3].read_text(encoding="utf-8"))
            baseline["global_required_values"] = {"sandbox_mode": "danger-full-access"}
            files[3].write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8", newline="\n")
            before = self.set_old_timestamp(files[1], files[2], files[3])

            result = self.run_repair(files[3], files[4])

            self.assertEqual(["global_config"], result["written"])
            self.assertEqual(before, {path: path.stat().st_mtime_ns for path in (files[1], files[2], files[3])})

            manifest = json.loads((Path(result["backup_dir"]) / "manifest.json").read_text(encoding="utf-8"))
            backup_sources = {Path(item["source_path"]).name for item in manifest["items"]}
            self.assertIn("global-config.toml", backup_sources)
            self.assertIn(files[3].name, backup_sources)
            self.assertNotIn("project-config.toml", backup_sources)
            self.assertNotIn("global-state.json", backup_sources)

    def test_explicit_plugin_registration_reconciliation_preserves_mcp_tables(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            files = self.make_fixture(Path(raw))
            baseline = json.loads(files[3].read_text(encoding="utf-8"))
            baseline.update({
                "configuration_authority": "wsl_active",
                "expected_plugins": ["browser@openai-bundled"],
            })
            files[3].write_text(json.dumps(baseline) + "\n", encoding="utf-8")
            with (
                mock.patch.object(repairer, "BASELINE_PATH", files[3]),
                mock.patch.object(repairer, "BACKUP_ROOT", files[4]),
                mock.patch.object(repairer, "refresh_runtime_artifacts", return_value=["runtime_artifact_refresh"]),
                mock.patch.object(repairer, "repair_global_state", return_value=({"unrelated": True}, ["global_state_unrelated"])),
                mock.patch.object(repairer, "ensure_desktop_environment_selection", return_value={"ok": True, "changed": False, "effective_value": False}),
            ):
                result = repairer.repair(
                    dry_run=False,
                    runtime_validation=False,
                    reconcile_plugin_registration=True,
                )
            config = tomllib.loads(files[0].read_text(encoding="utf-8"))
            self.assertTrue(config["plugins"]["browser@openai-bundled"]["enabled"])
            self.assertNotIn("mcp_servers", config)
            self.assertFalse(result["write_decisions"]["baseline"])
            self.assertFalse(result["write_decisions"]["global_state"])
            self.assertEqual("explicit_plugin_registration", result["global_config_owner"])
            self.assertTrue(result["plugin_registration_reconciled"])
            self.assertFalse(result["mcp_registration_reconciled"])
            self.assertNotIn("runtime_artifact_refresh", result["changed"])
            self.assertNotIn("global_state_unrelated", result["changed"])

    def test_wsl_runtime_projection_is_skipped_when_desktop_wsl_is_disabled(self) -> None:
        result = repairer.ensure_wsl_runtime_projection(enabled=False, dry_run=False)

        self.assertTrue(result["ok"])
        self.assertEqual("not_required", result["status"])

    def test_wsl_runtime_projection_consumes_owner_receipt(self) -> None:
        completed = mock.Mock(
            returncode=0,
            stdout=json.dumps({
                "ok": True,
                "changed": True,
                "session_projection": {
                    "ok": True,
                    "status": "projected",
                    "source_count": 3,
                    "projected_count": 3,
                },
                "state_projection": {
                    "ok": True,
                    "status": "ready",
                    "source_rejected_row_count": 0,
                    "source_missing_row_count": 0,
                },
            }),
            stderr="",
        )
        with (
            mock.patch.object(repairer, "codex_desktop_running", return_value=False),
            mock.patch.object(repairer.shutil, "which", return_value="wsl.exe"),
            mock.patch.object(
                repairer,
                "prepare_wsl_state_snapshot",
                return_value={"ok": True, "status": "prepared", "path": r"C:\snapshot\state_5.sqlite"},
            ),
            mock.patch.object(repairer.subprocess, "run", return_value=completed) as run,
        ):
            result = repairer.ensure_wsl_runtime_projection(enabled=True, dry_run=False)

        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertEqual("applied", result["status"])
        command = run.call_args.args[0]
        self.assertIn("apply", command)
        self.assertEqual("/mnt/c/snapshot/state_5.sqlite", command[-1])

    def test_wsl_runtime_projection_accepts_preserved_session_conflicts(self) -> None:
        completed = mock.Mock(
            returncode=0,
            stdout=json.dumps({
                "ok": True,
                "changed": True,
                "session_projection": {
                    "ok": True,
                    "status": "projected_with_conflicts",
                    "source_count": 3,
                    "projected_count": 2,
                    "conflict_count": 1,
                },
                "state_projection": {
                    "ok": True,
                    "status": "ready",
                    "source_rejected_row_count": 0,
                    "source_missing_row_count": 0,
                },
            }),
            stderr="",
        )
        with (
            mock.patch.object(repairer, "codex_desktop_running", return_value=False),
            mock.patch.object(repairer.shutil, "which", return_value="wsl.exe"),
            mock.patch.object(
                repairer,
                "prepare_wsl_state_snapshot",
                return_value={"ok": True, "status": "prepared", "path": r"C:\snapshot\state_5.sqlite"},
            ),
            mock.patch.object(repairer.subprocess, "run", return_value=completed),
        ):
            result = repairer.ensure_wsl_runtime_projection(enabled=True, dry_run=False)

        self.assertTrue(result["ok"])
        self.assertTrue(result["ready"])
        self.assertEqual("applied", result["status"])

    def test_desktop_environment_selection_uses_no_window_creation_flag(self) -> None:
        completed = mock.Mock(
            returncode=0,
            stdout=json.dumps({
                "ok": True,
                "status": "ready",
                "changed": False,
                "selected_value": True,
            }),
            stderr="",
        )
        with tempfile.TemporaryDirectory() as raw:
            host_config = Path(raw) / "config.toml"
            host_config.write_text(
                "[desktop]\nrunCodexInWindowsSubsystemForLinux = true\n",
                encoding="utf-8",
            )
            with (
                mock.patch.object(repairer.os, "name", "nt"),
                mock.patch.object(repairer, "codex_desktop_running", return_value=False),
                mock.patch.object(repairer.shutil, "which", return_value="wsl.exe"),
                mock.patch.object(repairer, "RUNTIME_REPAIR_NO_WINDOW_FLAG", 0x08000000),
                mock.patch.object(repairer.subprocess, "run", return_value=completed) as run,
            ):
                result = repairer.ensure_desktop_environment_selection(
                    host_config=host_config,
                    dry_run=False,
                )

        self.assertTrue(result["ok"])
        self.assertEqual(0x08000000, run.call_args.kwargs["creationflags"])

    def test_wsl_runtime_projection_uses_no_window_creation_flag(self) -> None:
        completed = mock.Mock(
            returncode=0,
            stdout=json.dumps({
                "ok": True,
                "changed": False,
                "session_projection": {"ok": True, "status": "projected", "source_count": 1, "projected_count": 1},
                "state_projection": {"ok": True, "status": "ready", "source_rejected_row_count": 0, "source_missing_row_count": 0},
            }),
            stderr="",
        )
        with (
            mock.patch.object(repairer, "codex_desktop_running", return_value=False),
            mock.patch.object(repairer.shutil, "which", return_value="wsl.exe"),
            mock.patch.object(repairer, "RUNTIME_REPAIR_NO_WINDOW_FLAG", 0x08000000),
            mock.patch.object(
                repairer,
                "prepare_wsl_state_snapshot",
                return_value={"ok": True, "status": "prepared", "path": r"C:\snapshot\state_5.sqlite"},
            ),
            mock.patch.object(repairer.subprocess, "run", return_value=completed) as run,
        ):
            result = repairer.ensure_wsl_runtime_projection(enabled=True, dry_run=False)

        self.assertTrue(result["ok"])
        self.assertEqual(0x08000000, run.call_args.kwargs["creationflags"])

    def test_wsl_runtime_projection_rejects_incomplete_session_receipt(self) -> None:
        completed = mock.Mock(
            returncode=0,
            stdout=json.dumps({
                "ok": True,
                "changed": True,
                "session_projection": {
                    "ok": True,
                    "status": "projected",
                    "source_count": 3,
                    "projected_count": 2,
                },
                "state_projection": {
                    "ok": True,
                    "status": "ready",
                    "source_rejected_row_count": 0,
                    "source_missing_row_count": 0,
                },
            }),
            stderr="",
        )
        with (
            mock.patch.object(repairer, "codex_desktop_running", return_value=False),
            mock.patch.object(repairer.shutil, "which", return_value="wsl.exe"),
            mock.patch.object(
                repairer,
                "prepare_wsl_state_snapshot",
                return_value={"ok": True, "status": "prepared", "path": r"C:\snapshot\state_5.sqlite"},
            ),
            mock.patch.object(repairer.subprocess, "run", return_value=completed),
        ):
            result = repairer.ensure_wsl_runtime_projection(enabled=True, dry_run=False)

        self.assertFalse(result["ok"])
        self.assertFalse(result["ready"])
        self.assertEqual("owner_incomplete", result["status"])

    def test_wsl_state_snapshot_uses_sqlite_online_backup(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source.sqlite"
            target = root / "projection" / "state_5.sqlite"
            connection = sqlite3.connect(source)
            connection.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, cwd TEXT)")
            connection.execute("INSERT INTO threads VALUES (?, ?)", ("thread-1", r"C:\work"))
            connection.commit()
            connection.close()

            result = repairer.prepare_wsl_state_snapshot(source=source, target=target)

            self.assertTrue(result["ok"], result)
            self.assertEqual("ok", result["integrity"])
            snapshot = sqlite3.connect(target)
            row = snapshot.execute("SELECT id, cwd FROM threads").fetchone()
            snapshot.close()
            self.assertEqual(("thread-1", r"C:\work"), row)

    def test_project_config_drift_writes_only_project_config(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            files = self.make_fixture(Path(raw))
            baseline = json.loads(files[3].read_text(encoding="utf-8"))
            baseline["project_required_values"] = {"approval_policy": "never"}
            files[3].write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8", newline="\n")
            before = self.set_old_timestamp(files[0], files[2], files[3])

            result = self.run_repair(files[3], files[4])

            self.assertEqual(["project_config"], result["written"])
            self.assertEqual(before, {path: path.stat().st_mtime_ns for path in (files[0], files[2], files[3])})

    def test_global_state_drift_writes_only_global_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            files = self.make_fixture(Path(raw))
            baseline = json.loads(files[3].read_text(encoding="utf-8"))
            baseline["global_state_required"] = {"desktop.show-context-window-usage": True}
            files[3].write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8", newline="\n")
            before = self.set_old_timestamp(files[0], files[1], files[3])

            result = self.run_repair(files[3], files[4])

            self.assertEqual(["global_state"], result["written"])
            self.assertEqual(before, {path: path.stat().st_mtime_ns for path in (files[0], files[1], files[3])})
            state = json.loads(files[2].read_text(encoding="utf-8"))
            self.assertTrue(state["desktop"]["show-context-window-usage"])

    def test_runtime_artifact_refresh_never_writes_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            files = self.make_fixture(Path(raw))
            before = self.set_old_timestamp(files[0], files[1], files[2], files[3])

            with (
                mock.patch.object(repairer, "BASELINE_PATH", files[3]),
                mock.patch.object(repairer, "BACKUP_ROOT", files[4]),
                mock.patch.object(repairer, "HUB_MANAGED_MCP_NAMES", frozenset()),
                mock.patch.object(repairer, "refresh_runtime_artifacts", return_value=["stable_node_repl_entry_updated"]),
            ):
                result = repairer.repair(dry_run=False, runtime_validation=False)

            self.assertEqual([], result["written"])
            self.assertFalse(result["write_decisions"]["baseline"])
            self.assertEqual(before, {path: path.stat().st_mtime_ns for path in files[:4]})
            self.assertIn("stable_node_repl_entry_updated", result["changed"])

    def test_node_repl_runtime_values_are_resolved_without_baseline_mutation(self) -> None:
        baseline = {
            "expected_mcp": {
                "node_repl": {
                    "required": True,
                    "command": "cmd.exe",
                    "args": ["old"],
                    "env": {
                        "CODEX_HOME": r"C:\Users\45543\.codex",
                        "CODEX_CLI_PATH": r"C:\stale\codex.exe",
                        "NODE_REPL_NODE_PATH": r"C:\stale\node.exe",
                    },
                }
            }
        }
        original = json.loads(json.dumps(baseline))
        runtime = {
            "node_path": r"C:\current\node.exe",
            "node_modules": r"C:\current\node_modules",
        }
        with (
            mock.patch.object(repairer, "DESKTOP_NATIVE_MCP_NAMES", frozenset({"node_repl"})),
            mock.patch.object(repairer, "discover_latest_codex_cli", return_value=r"C:\current\codex.exe"),
            mock.patch.object(repairer, "discover_latest_node_repl_runtime", return_value=runtime),
        ):
            spec = repairer.expected_mcp_specs(baseline)["node_repl"]

        self.assertEqual(original, baseline)
        self.assertEqual(r"C:\current\codex.exe", spec["env"]["CODEX_CLI_PATH"])
        self.assertEqual(r"C:\current\node.exe", spec["env"]["NODE_REPL_NODE_PATH"])
        self.assertEqual(r"C:\current\node_modules", spec["env"]["NODE_REPL_NODE_MODULE_DIRS"])
        self.assertEqual(r"C:\Users\45543\.codex", spec["env"]["CODEX_HOME"])

    def test_tracked_baseline_excludes_volatile_node_repl_runtime_values(self) -> None:
        baseline = json.loads(repairer.BASELINE_PATH.read_text(encoding="utf-8"))
        env = baseline["expected_mcp"]["node_repl"].get("env", {})
        self.assertTrue(repairer.VOLATILE_NODE_REPL_ENV_KEYS.isdisjoint(env))

    def test_bom_removal_is_a_real_global_config_write(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            files = self.make_fixture(Path(raw))
            files[0].write_bytes(b"\xef\xbb\xbf" + GLOBAL_TEXT.encode("utf-8"))
            before = self.set_old_timestamp(files[1], files[2], files[3])

            result = self.run_repair(files[3], files[4])

            self.assertEqual(["global_config"], result["written"])
            self.assertIn("global_config_remove_bom", result["changed"])
            self.assertFalse(files[0].read_bytes().startswith(b"\xef\xbb\xbf"))
            self.assertEqual(before, {path: path.stat().st_mtime_ns for path in (files[1], files[2], files[3])})

    def test_wsl_resume_context_projection_defers_while_desktop_is_running(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            state_path = Path(raw) / "global-state.json"
            state_path.write_text('{"electron-persisted-atom-state":{"queued-follow-ups":{}}}', encoding="utf-8")
            with (
                mock.patch.object(repairer, "codex_desktop_running", return_value=True),
                mock.patch.object(repairer, "backup_files") as backup,
            ):
                result = repairer.ensure_wsl_resume_context_projection(
                    enabled=True,
                    dry_run=False,
                    global_state_path=state_path,
                )

            self.assertTrue(result["ok"])
            self.assertFalse(result["ready"])
            self.assertEqual("deferred_desktop_running", result["status"])
            backup.assert_not_called()

    def test_environment_owner_failure_preserves_desired_wsl_but_falls_back_effectively(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = root / "config.toml"
            state = root / "desktop-environment-selection.json"
            config.write_text(
                "[desktop]\nrunCodexInWindowsSubsystemForLinux = true\n",
                encoding="utf-8",
            )
            state.write_text(
                json.dumps({"last_synced_value": True}),
                encoding="utf-8",
            )
            windows_os = mock.Mock(wraps=os)
            windows_os.name = "nt"
            with (
                mock.patch.object(repairer, "HOST_ENVIRONMENT_SELECTION_STATE", state),
                mock.patch.object(repairer, "os", windows_os),
                mock.patch.object(repairer, "codex_desktop_running", return_value=False),
                mock.patch.object(repairer.shutil, "which", return_value="wsl.exe"),
                mock.patch.object(repairer, "create_backup", return_value={"ok": True}),
                mock.patch.object(
                    repairer.subprocess,
                    "run",
                    return_value=mock.Mock(returncode=0xFFFFFFFF, stdout="", stderr=""),
                ),
            ):
                result = repairer.ensure_desktop_environment_selection(
                    host_config=config,
                    dry_run=False,
                )

            self.assertFalse(result["ok"])
            self.assertTrue(result["desired_value"])
            self.assertFalse(result["effective_value"])
            self.assertTrue(result["fallback_preserved"])
            self.assertEqual("environment_owner_failed", result["fallback_reason"])
            persisted = json.loads(state.read_text(encoding="utf-8"))
            self.assertTrue(persisted["desired_value"])
            self.assertFalse(persisted["effective_value"])
            self.assertTrue(persisted["fallback_pending"])

    def test_wsl_resume_context_projection_repairs_after_restart_boundary(self) -> None:
        broken = "/mnt/c/Program Files/WindowsApps/OpenAI.Codex/app/resources/C:\\Users\\45543\\Downloads\\mcsmanager_windows_release\\mcsmanager"
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            state_path = root / "global-state.json"
            state_path.write_text(
                json.dumps({
                    "queued-follow-ups": {
                        "thread": [{"cwd": broken, "context": {"cwd": broken, "workspaceRoots": [broken]}}]
                    }
                }),
                encoding="utf-8",
            )
            with (
                mock.patch.object(repairer, "codex_desktop_running", return_value=False),
                mock.patch.object(
                    repairer,
                    "load_wsl_workspace_thread_index",
                    return_value={
                        "ok": True,
                        "status": "ready",
                        "source": "wsl_runtime_owner",
                        "thread_ids": [],
                        "thread_cwds": {},
                        "thread_count": 0,
                    },
                ),
                mock.patch.object(repairer, "backup_files", return_value=root / "backup") as backup,
            ):
                result = repairer.ensure_wsl_resume_context_projection(
                    enabled=True,
                    dry_run=False,
                    global_state_path=state_path,
                )

            self.assertTrue(result["ok"])
            self.assertTrue(result["ready"])
            self.assertTrue(result["changed"])
            self.assertEqual("applied", result["status"])
            backup.assert_called_once()
            entry = json.loads(state_path.read_text(encoding="utf-8"))["queued-follow-ups"]["thread"][0]
            expected = "/mnt/c/Users/45543/Downloads/mcsmanager_windows_release/mcsmanager"
            self.assertEqual(expected, entry["cwd"])
            self.assertEqual(expected, entry["context"]["cwd"])
            self.assertEqual([expected], entry["context"]["workspaceRoots"])

    def test_wsl_thread_index_uses_runtime_owner_without_visible_window(self) -> None:
        payload = {
            "ok": True,
            "status": "ready",
            "thread_ids": ["wsl-thread"],
            "thread_cwds": {"wsl-thread": "/home/codexlab/work/codex-workspace/workspace"},
            "thread_count": 1,
        }
        with (
            mock.patch.object(repairer.shutil, "which", return_value="wsl.exe"),
            mock.patch.object(
                repairer.subprocess,
                "run",
                return_value=mock.Mock(returncode=0, stdout=json.dumps(payload), stderr=""),
            ) as run,
        ):
            result = repairer.load_wsl_workspace_thread_index()

        self.assertTrue(result["ok"])
        self.assertEqual("wsl_runtime_owner", result["source"])
        self.assertEqual(["wsl-thread"], result["thread_ids"])
        self.assertEqual(repairer.RUNTIME_REPAIR_NO_WINDOW_FLAG, run.call_args.kwargs["creationflags"])
        self.assertEqual("thread-index", run.call_args.args[0][-1])

    def test_default_wsl_resume_projection_consumes_wsl_owner_index(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            state_path = root / "global-state.json"
            state_path.write_text(
                json.dumps({
                    "local-projects": {
                        "wsl-project": {
                            "id": "wsl-project",
                            "name": "WSL Codex 工作区",
                            "rootPaths": [r"\\wsl.localhost\Codex-Wsl-Lab\home\codexlab\work\codex-workspace"],
                        }
                    },
                    "project-order": ["wsl-project"],
                    "projectless-thread-ids": ["wsl-thread"],
                    "thread-project-assignments": {},
                }),
                encoding="utf-8",
            )
            index = {
                "ok": True,
                "status": "ready",
                "source": "wsl_runtime_owner",
                "thread_ids": ["wsl-thread"],
                "thread_cwds": {"wsl-thread": "/home/codexlab/work/codex-workspace/workspace"},
                "thread_count": 1,
            }
            with (
                mock.patch.object(repairer, "codex_desktop_running", return_value=False),
                mock.patch.object(repairer, "load_wsl_workspace_thread_index", return_value=index) as owner_index,
                mock.patch.object(repairer, "backup_files", return_value=root / "backup"),
            ):
                result = repairer.ensure_wsl_resume_context_projection(
                    enabled=True,
                    dry_run=False,
                    global_state_path=state_path,
                )

            self.assertTrue(result["ok"])
            self.assertEqual("wsl_runtime_owner", result["task_index_source"])
            self.assertEqual(1, result["assigned_task_count"])
            owner_index.assert_called_once_with()
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual("wsl-project", state["thread-project-assignments"]["wsl-thread"]["projectId"])

    def test_wsl_resume_projection_indexes_top_level_tasks_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            state_path = root / "global-state.json"
            state_path.write_text(
                json.dumps({
                    "projectless-thread-ids": ["existing"],
                    "thread-project-assignments": {"assigned": {"projectId": "project"}},
                }),
                encoding="utf-8",
            )
            thread_state = root / "state_5.sqlite"
            visible_session = root / "visible.jsonl"
            assigned_session = root / "assigned.jsonl"
            subagent_session = root / "subagent.jsonl"
            archived_session = root / "archived.jsonl"
            no_user_session = root / "no-user.jsonl"
            for path in (visible_session, assigned_session, subagent_session, archived_session, no_user_session):
                path.write_text("{}\n", encoding="utf-8")
            connection = sqlite3.connect(thread_state)
            connection.execute(
                "CREATE TABLE threads (id TEXT, source TEXT, rollout_path TEXT, archived INTEGER, has_user_event INTEGER, updated_at_ms INTEGER)"
            )
            connection.executemany(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?)",
                [
                    ("visible", "vscode", str(visible_session), 0, 1, 6),
                    ("assigned", "vscode", str(assigned_session), 0, 1, 5),
                    ("subagent", '{"subagent":{"thread_spawn":{}}}', str(subagent_session), 0, 1, 4),
                    ("archived", "vscode", str(archived_session), 1, 1, 3),
                    ("no-user-event", "vscode", str(no_user_session), 0, 0, 2),
                    ("missing-session", "vscode", str(root / "missing.jsonl"), 0, 0, 1),
                ],
            )
            connection.commit()
            connection.close()
            with (
                mock.patch.object(repairer, "codex_desktop_running", return_value=False),
                mock.patch.object(repairer, "backup_files", return_value=root / "backup") as backup,
            ):
                result = repairer.ensure_wsl_resume_context_projection(
                    enabled=True,
                    dry_run=False,
                    global_state_path=state_path,
                    thread_state_path=thread_state,
                )

            self.assertTrue(result["ok"])
            self.assertTrue(result["task_index_ok"])
            self.assertEqual(3, result["eligible_task_count"])
            self.assertEqual(2, result["indexed_task_count"])
            backup.assert_called_once()
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(["existing", "visible", "no-user-event"], state["projectless-thread-ids"])
            self.assertEqual({"assigned": {"projectId": "project"}}, state["thread-project-assignments"])

    def test_wsl_resume_projection_reconstructs_project_assignment_from_thread_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project_root = r"C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager"
            state_path = root / "global-state.json"
            state_path.write_text(
                json.dumps({
                    "local-projects": {
                        "main-project": {
                            "id": "main-project",
                            "name": "主项目",
                            "rootPaths": [project_root],
                        }
                    },
                    "projectless-thread-ids": ["imported"],
                    "thread-project-assignments": {},
                }),
                encoding="utf-8",
            )
            session = root / "imported.jsonl"
            session.write_text("{}\n", encoding="utf-8")
            thread_state = root / "state_5.sqlite"
            connection = sqlite3.connect(thread_state)
            connection.execute(
                "CREATE TABLE threads (id TEXT, source TEXT, rollout_path TEXT, cwd TEXT, archived INTEGER, updated_at_ms INTEGER)"
            )
            connection.execute(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?)",
                ("imported", "vscode", str(session), project_root + r"\_bridge", 0, 1),
            )
            connection.commit()
            connection.close()
            with (
                mock.patch.object(repairer, "codex_desktop_running", return_value=False),
                mock.patch.object(repairer, "backup_files", return_value=root / "backup") as backup,
            ):
                result = repairer.ensure_wsl_resume_context_projection(
                    enabled=True,
                    dry_run=False,
                    global_state_path=state_path,
                    thread_state_path=thread_state,
                )

            self.assertTrue(result["ok"])
            self.assertEqual(1, result["assigned_task_count"])
            self.assertEqual(1, result["removed_projectless_task_count"])
            backup.assert_called_once()
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual([], state["projectless-thread-ids"])
            assignment = state["thread-project-assignments"]["imported"]
            self.assertEqual("main-project", assignment["projectId"])
            self.assertEqual(project_root + r"\_bridge", assignment["cwd"])

    def test_windows_resume_cwd_candidate_handles_wsl_and_malformed_mount_paths(self) -> None:
        expected = r"C:\Users\45543\Documents\Codex\thread"
        self.assertEqual(
            expected,
            repairer.windows_resume_cwd_candidate(
                "/mnt/c/Users/45543/Documents/Codex/thread"
            ),
        )
        self.assertEqual(
            expected,
            repairer.windows_resume_cwd_candidate(
                r"C:\mnt\c\Users\45543\Documents\Codex\thread"
            ),
        )
        self.assertEqual("", repairer.windows_resume_cwd_candidate(expected))

    def test_windows_resume_projection_updates_only_existing_top_level_mappings(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "valid-cwd"
            target.mkdir()
            mount_cwd = "/mnt/c/fixture/valid-cwd"
            malformed_cwd = r"C:\mnt\c\fixture\valid-cwd"
            missing_cwd = "/mnt/c/fixture/definitely-missing-codex-cwd"

            def projected_cwd(value: str) -> str:
                if value in {mount_cwd, malformed_cwd}:
                    return str(target)
                if value == missing_cwd:
                    return str(root / "definitely-missing-codex-cwd")
                return ""
            thread_state = root / "state_5.sqlite"
            rollout = root / "rollout.jsonl"
            rollout.write_text(
                "\n".join([
                    json.dumps({
                        "type": "event_msg",
                        "payload": {"thread_settings": {"cwd": mount_cwd}},
                    }, ensure_ascii=False),
                    json.dumps({
                        "type": "world_state",
                        "payload": {"state": {"environments": {"environments": {"local": {"cwd": mount_cwd}}}}},
                    }, ensure_ascii=False),
                    json.dumps({
                        "type": "turn_context",
                        "payload": {"cwd": mount_cwd, "workspace_roots": [mount_cwd]},
                    }, ensure_ascii=False),
                ]) + "\n",
                encoding="utf-8",
            )
            (root / ".codex-global-state.json").write_text(
                json.dumps({
                    "thread-workspace-root-hints": {"mount": mount_cwd},
                    "thread-projectless-output-directories": {"mount": mount_cwd},
                }),
                encoding="utf-8",
            )
            connection = sqlite3.connect(thread_state)
            connection.execute(
                "CREATE TABLE threads (id TEXT PRIMARY KEY, source TEXT, cwd TEXT, rollout_path TEXT, archived INTEGER)"
            )
            connection.executemany(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?)",
                [
                    ("mount", "vscode", mount_cwd, str(rollout), 0),
                    ("malformed", "vscode", malformed_cwd, str(rollout), 0),
                    ("missing", "vscode", missing_cwd, str(rollout), 0),
                    ("subagent", '{"subagent":{}}', mount_cwd, str(rollout), 0),
                    ("archived", "vscode", mount_cwd, str(rollout), 1),
                ],
            )
            connection.commit()
            connection.close()
            with (
                mock.patch.object(repairer, "codex_desktop_running", return_value=False),
                mock.patch.object(repairer, "WINDOWS_SESSION_BACKUP_ROOT", root / "backups"),
                mock.patch.object(repairer, "windows_resume_cwd_candidate", side_effect=projected_cwd),
            ):
                result = repairer.ensure_windows_resume_cwd_projection(
                    enabled=True,
                    dry_run=False,
                    thread_state_path=thread_state,
                )

            self.assertTrue(result["ok"])
            self.assertEqual("applied", result["status"])
            self.assertEqual(2, result["changed_row_count"])
            self.assertEqual(1, result["rejected_count"])
            self.assertEqual("ok", result["sqlite_integrity"])
            self.assertTrue(Path(result["backup"]["manifest_path"]).is_file())
            connection = sqlite3.connect(thread_state)
            rows = dict(connection.execute("SELECT id, cwd FROM threads"))
            connection.close()
            self.assertEqual(str(target), rows["mount"])
            self.assertEqual(str(target), rows["malformed"])
            self.assertEqual(missing_cwd, rows["missing"])
            self.assertEqual(mount_cwd, rows["subagent"])
            self.assertEqual(mount_cwd, rows["archived"])
            records = [json.loads(line) for line in rollout.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(str(target), records[0]["payload"]["thread_settings"]["cwd"])
            self.assertEqual(str(target), records[1]["payload"]["state"]["environments"]["environments"]["local"]["cwd"])
            self.assertEqual(str(target), records[2]["payload"]["cwd"])
            self.assertEqual(str(target), records[2]["payload"]["workspace_roots"][0])
            state = json.loads((root / ".codex-global-state.json").read_text(encoding="utf-8"))
            self.assertEqual(str(target), state["thread-workspace-root-hints"]["mount"])
            self.assertEqual(str(target), state["thread-projectless-output-directories"]["mount"])


if __name__ == "__main__":
    unittest.main()
