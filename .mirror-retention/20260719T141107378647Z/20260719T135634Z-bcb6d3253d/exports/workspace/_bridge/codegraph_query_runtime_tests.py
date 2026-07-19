from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


BRIDGE_DIR = Path(__file__).resolve().parent
if str(BRIDGE_DIR) not in sys.path:
    sys.path.insert(0, str(BRIDGE_DIR))

import codegraph_fresh_mcp_server
import codegraph_query_runtime as runtime
import local_mcp_hub
import local_mcp_hub_specs
import mcp_launch_guard


def create_index(project: Path, *, complete: bool = True, populated: bool = True) -> Path:
    db_path = project / ".codegraph" / "codegraph.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        tables = ["files", "nodes", "edges"]
        if complete:
            tables.extend(["project_metadata", "schema_versions"])
        for table in tables:
            conn.execute(f"create table {table} (id integer primary key, value text)")
        if populated:
            for table in ("files", "nodes", "edges"):
                conn.execute(f"insert into {table}(value) values (?)", (table,))
        conn.commit()
    finally:
        conn.close()
    return db_path


class CodeGraphQueryRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project = self.base / "project"
        self.project.mkdir()
        self.runtime_dir = self.base / "runtime"
        self.runtime_patch = mock.patch.object(runtime, "RUNTIME_DIR", self.runtime_dir)
        self.runtime_patch.start()
        self.addCleanup(self.runtime_patch.stop)

    def test_valid_index_and_fresh_target_return_analysis(self) -> None:
        db_path = create_index(self.project)
        target = self.project / "src.py"
        target.write_text("value = 1\n", encoding="utf-8")
        db_time = db_path.stat().st_mtime
        os.utime(target, (db_time - 10, db_time - 10))
        with mock.patch.object(runtime, "request_background_refresh", return_value={"ok": True, "state": "scheduled"}), mock.patch.object(
            runtime,
            "run_codegraph_explore",
            return_value={"ok": True, "stdout": "src.py analysis", "stderr": ""},
        ):
            result = runtime.query_codegraph("inspect src.py", project_path=self.project, freshness_targets=["src.py"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["analysis"], "src.py analysis")
        self.assertEqual(result["freshness"]["state"], "fresh")
        self.assertFalse(result["degraded"])
        self.assertTrue(result["scope"]["ok"])

    def test_freshness_uncertainty_returns_degraded_analysis(self) -> None:
        create_index(self.project)
        with mock.patch.object(runtime, "request_background_refresh", return_value={"ok": True, "state": "scheduled"}), mock.patch.object(
            runtime,
            "run_codegraph_explore",
            return_value={"ok": True, "stdout": "usable stale analysis", "stderr": ""},
        ):
            result = runtime.query_codegraph("architecture overview", project_path=self.project)
        self.assertTrue(result["ok"])
        self.assertEqual(result["freshness"]["state"], "unknown")
        self.assertTrue(result["degraded"])
        self.assertEqual(result["analysis"], "usable stale analysis")

    def test_stale_target_returns_analysis_and_forces_background_refresh(self) -> None:
        db_path = create_index(self.project)
        target = self.project / "changed.py"
        target.write_text("changed = True\n", encoding="utf-8")
        db_time = db_path.stat().st_mtime
        os.utime(target, (db_time + 10, db_time + 10))
        with mock.patch.object(runtime, "request_background_refresh", return_value={"ok": True, "state": "scheduled"}) as refresh, mock.patch.object(
            runtime,
            "run_codegraph_explore",
            return_value={"ok": True, "stdout": "changed.py stale index analysis", "stderr": ""},
        ):
            result = runtime.query_codegraph("inspect changed.py", project_path=self.project, freshness_targets=["changed.py"])
        self.assertTrue(result["ok"])
        self.assertTrue(result["degraded"])
        self.assertEqual(result["freshness"]["state"], "stale")
        refresh.assert_called_once_with(self.project.resolve(), reason="query_stale", force_sync=True)

    def test_scope_contamination_is_tightened_and_retried(self) -> None:
        create_index(self.project)
        target = self.project / "src.py"
        target.write_text("value = 1\n", encoding="utf-8")
        responses = [
            {"ok": True, "stdout": "_bridge/resources/vendor/tool.py unrelated", "stderr": ""},
            {"ok": True, "stdout": "src.py requested implementation", "stderr": ""},
        ]
        with mock.patch.object(runtime, "request_background_refresh", return_value={"ok": True, "state": "scheduled"}), mock.patch.object(
            runtime,
            "run_codegraph_explore",
            side_effect=responses,
        ) as explore:
            result = runtime.query_codegraph(
                "inspect src.py",
                project_path=self.project,
                freshness_targets=["src.py"],
                exclude_paths=["_bridge/resources"],
            )
        self.assertTrue(result["ok"])
        self.assertEqual(explore.call_count, 2)
        self.assertEqual(len(result["scope_attempts"]), 2)
        self.assertEqual(result["scope_attempts"][0]["scope"]["reason"], "excluded_path_contamination")
        self.assertEqual(result["scope"]["reason"], "scope_accepted")

    def test_explicit_file_target_uses_symbol_map_before_explore(self) -> None:
        create_index(self.project)
        target = self.project / "src.py"
        target.write_text("def run():\n    return 1\n", encoding="utf-8")
        with mock.patch.object(runtime, "request_background_refresh", return_value={"ok": True, "state": "scheduled"}), mock.patch.object(
            runtime,
            "run_target_symbol_map",
            return_value={
                "ok": True,
                "schema": "codegraph_query_runtime.target_symbol_map.v1",
                "attempted_count": 1,
                "accepted_count": 1,
                "analysis": "## Target: src.py\nsrc.py::run dependents=[]",
            },
        ), mock.patch.object(runtime, "run_codegraph_explore") as explore:
            result = runtime.query_codegraph(
                "inspect the symbols",
                project_path=self.project,
                freshness_targets=["src.py"],
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["query_mode"], "target_symbol_map")
        explore.assert_not_called()

    def test_analysis_hygiene_removes_excluded_and_duplicate_blocks(self) -> None:
        analysis = (
            "## src.py\nrequested symbol\n\n"
            "## src.py\nrequested symbol\n\n"
            "## _bridge/resources/vendor.py\nunrelated source"
        )
        result = runtime.sanitize_analysis(analysis, ["_bridge/resources"])
        self.assertEqual(result["removed_duplicate_blocks"], 1)
        self.assertEqual(result["removed_excluded_blocks"], 1)
        self.assertEqual(result["analysis"].count("requested symbol"), 1)
        self.assertNotIn("vendor.py", result["analysis"])

    def test_explicit_targets_do_not_absorb_paths_from_query_instructions(self) -> None:
        targets = runtime.extract_freshness_targets(
            "Inspect src.py and exclude _bridge/resources, backups, logs, and runtime.",
            self.project,
            ["src.py"],
        )
        self.assertEqual(targets, ["src.py"])

    def test_scope_ignores_excludes_echoed_only_in_exploration_header(self) -> None:
        analysis = (
            "**Exploration: inspect src.py. Exclude results from: _bridge/resources, node_modules.**\n"
            "**Source Code**\n"
            "`src.py` requested implementation"
        )
        scope = runtime.assess_analysis_scope(analysis, ["src.py"], ["_bridge/resources", "node_modules"])
        self.assertTrue(scope["ok"])
        self.assertEqual(scope["contamination"], [])

    def test_scope_failure_is_structured_after_bounded_retry(self) -> None:
        create_index(self.project)
        target = self.project / "src.py"
        target.write_text("value = 1\n", encoding="utf-8")
        with mock.patch.object(runtime, "request_background_refresh", return_value={"ok": True, "state": "scheduled"}), mock.patch.object(
            runtime,
            "run_codegraph_explore",
            return_value={"ok": True, "stdout": "_bridge/resources/vendor/tool.py only", "stderr": ""},
        ) as explore, mock.patch.object(runtime, "run_target_file_fallback", return_value={"ok": False, "reason": "fallback_failed"}):
            result = runtime.query_codegraph(
                "inspect src.py",
                project_path=self.project,
                freshness_targets=["src.py"],
                exclude_paths=["_bridge/resources"],
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "codegraph_scope_insufficient")
        self.assertEqual(explore.call_count, 2)
        self.assertEqual(result["next_action"], "refine_targets_or_exclusions_then_retry")
        self.assertIn("full_analysis_ref", result)
        self.assertLessEqual(len(result["analysis"]), runtime.DEFAULT_INLINE_ANALYSIS_CHARS + 100)

    def test_scope_failure_uses_bounded_target_file_fallback(self) -> None:
        create_index(self.project)
        target = self.project / "src.py"
        target.write_text("value = 1\n", encoding="utf-8")
        with mock.patch.object(runtime, "request_background_refresh", return_value={"ok": True, "state": "scheduled"}), mock.patch.object(
            runtime,
            "run_codegraph_explore",
            return_value={"ok": True, "stdout": "_bridge/resources/vendor/tool.py only", "stderr": ""},
        ), mock.patch.object(
            runtime,
            "run_target_file_fallback",
            return_value={"ok": True, "analysis": "**src.py**\n1 value = 1", "rows": []},
        ):
            result = runtime.query_codegraph(
                "inspect src.py",
                project_path=self.project,
                freshness_targets=["src.py"],
                exclude_paths=["_bridge/resources"],
            )
        self.assertTrue(result["ok"])
        self.assertTrue(result["degraded"])
        self.assertEqual(result["degraded_reason"], "scope_fallback_target_files")
        self.assertEqual(result["scope"]["reason"], "scope_accepted")

    def test_missing_corrupt_incomplete_and_empty_indexes_fail_structurally(self) -> None:
        cases: list[tuple[str, callable, str]] = [
            ("missing", lambda project: None, "codegraph_index_missing"),
            (
                "corrupt",
                lambda project: ((project / ".codegraph").mkdir(), (project / ".codegraph" / "codegraph.db").write_bytes(b"not sqlite")),
                "codegraph_index_unreadable",
            ),
            ("incomplete", lambda project: create_index(project, complete=False), "codegraph_index_schema_incomplete"),
            ("empty", lambda project: create_index(project, populated=False), "codegraph_index_empty"),
        ]
        for name, setup, expected_reason in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory(dir=self.base) as case_dir:
                project = Path(case_dir)
                setup(project)
                result = runtime.query_codegraph("anything", project_path=project)
                self.assertFalse(result["ok"])
                self.assertEqual(result["reason"], "codegraph_index_unusable")
                self.assertEqual(result["index"]["reason"], expected_reason)

    def test_refresh_requests_are_coalesced_during_cooldown(self) -> None:
        fake_process = SimpleNamespace(pid=43210)
        with mock.patch.object(runtime.subprocess, "Popen", return_value=fake_process) as popen:
            first = runtime.request_background_refresh(self.project, reason="first", cooldown_seconds=300)
            second = runtime.request_background_refresh(self.project, reason="second", cooldown_seconds=300)
        self.assertEqual(first["state"], "scheduled")
        self.assertEqual(second["state"], "coalesced")
        self.assertEqual(second["reason"], "refresh_cooldown_active")
        self.assertEqual(popen.call_count, 1)

    def test_repeated_pending_signature_does_not_create_sync_storm(self) -> None:
        create_index(self.project)
        calls: list[str] = []
        status_payload = {
            "pendingChanges": {"added": 0, "modified": 2, "removed": 0},
            "index": {"reindexRecommended": False},
        }

        def fake_run(command: list[str], **_: object) -> dict[str, object]:
            action = command[1]
            calls.append(action)
            if action == "status":
                return {"ok": True, "stdout": json.dumps(status_payload), "stderr": ""}
            if action == "sync":
                return {"ok": True, "stdout": "synced", "stderr": ""}
            raise AssertionError(command)

        with mock.patch.object(runtime, "run_command", side_effect=fake_run):
            self.assertEqual(runtime.refresh_worker(self.project, reason="first", force_sync=False), 0)
            self.assertEqual(runtime.refresh_worker(self.project, reason="second", force_sync=False), 0)
        self.assertEqual(calls.count("sync"), 1)
        state_path, _ = runtime.project_runtime_paths(self.project.resolve())
        state = runtime.read_json(state_path)
        self.assertEqual(state["sync"]["reason"], "same_pending_recently_synced")

    def test_status_timeout_is_degraded_maintenance_not_service_failure(self) -> None:
        create_index(self.project)
        timeout = {"ok": False, "reason": "timeout", "stdout": "", "stderr": ""}
        with mock.patch.object(runtime, "run_command", return_value=timeout):
            exit_code = runtime.refresh_worker(self.project, reason="timeout", force_sync=False)
        self.assertEqual(exit_code, 0)
        state_path, lock_path = runtime.project_runtime_paths(self.project.resolve())
        state = runtime.read_json(state_path)
        self.assertTrue(state["ok"])
        self.assertFalse(state["maintenance_ok"])
        self.assertEqual(state["state"], "degraded")
        self.assertFalse(lock_path.exists())

    def test_worker_exception_always_cleans_lock_and_records_failure(self) -> None:
        _, lock_path = runtime.project_runtime_paths(self.project.resolve())
        runtime.atomic_write_json(lock_path, {"pid": 0, "started_at": runtime.now_iso()})
        with mock.patch.object(runtime, "run_command", side_effect=RuntimeError("boom")):
            exit_code = runtime.refresh_worker(self.project, reason="exception", force_sync=False)
        state_path, _ = runtime.project_runtime_paths(self.project.resolve())
        state = runtime.read_json(state_path)
        self.assertEqual(exit_code, 1)
        self.assertEqual(state["reason"], "refresh_worker_exception")
        self.assertFalse(lock_path.exists())

    def test_status_json_parser_tolerates_surrounding_logs(self) -> None:
        payload = runtime.parse_json_output('warning before\n{"pendingChanges":{"added":1}}\nwarning after')
        self.assertEqual(payload, {"pendingChanges": {"added": 1}})

    def test_prelaunch_blocks_only_on_unusable_index(self) -> None:
        create_index(self.project)
        with mock.patch.object(runtime, "request_background_refresh", return_value={"ok": True, "state": "scheduled"}):
            valid = runtime.prelaunch_check(self.project)
        self.assertTrue(valid["ok"])
        missing = runtime.prelaunch_check(self.base / "missing-project")
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["reason"], "codegraph_index_unusable")

    def test_windows_hub_uses_wsl_native_index_for_wsl_unc_project(self) -> None:
        project = Path(r"\\wsl.localhost\Codex-Wsl-Lab\home\codexlab\work\codex-workspace\workspace")
        self.assertEqual(runtime.codegraph_dir_name(project, platform_name="nt"), ".codegraph")
        self.assertEqual(
            runtime.codegraph_execution_path(project, platform_name="nt"),
            "/home/codexlab/work/codex-workspace/workspace",
        )
        prefix = runtime.codegraph_command_prefix(project, platform_name="nt")
        self.assertEqual(prefix[:6], ["wsl.exe", "-d", "Codex-Wsl-Lab", "-u", "codexlab", "--"])
        self.assertEqual(prefix[-3:-1], ["CODEGRAPH_DIR=.codegraph", "CODEGRAPH_NO_DAEMON=1"])
        self.assertTrue(prefix[-1].endswith("/_bridge/tools/codegraph/node_modules/.bin/codegraph"))
        self.assertEqual(
            runtime.codegraph_file_argument(r"_bridge\wsl_workspace_owner.py", project, platform_name="nt"),
            "_bridge/wsl_workspace_owner.py",
        )

    def test_windows_index_inspection_delegates_sqlite_open_to_wsl(self) -> None:
        project = Path(r"\\wsl.localhost\Codex-Wsl-Lab\home\codexlab\work\codex-workspace\workspace")
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"schema": "codegraph_query_runtime.index.v1", "ok": True, "reason": "usable_index"}),
            stderr="",
        )
        with mock.patch.object(runtime.subprocess, "run", return_value=completed) as runner:
            result = runtime.inspect_index_usability(project, platform_name="nt")

        self.assertTrue(result["ok"])
        self.assertEqual(result["inspection_transport"], "wsl_native")
        command = runner.call_args.args[0]
        self.assertEqual(command[:6], ["wsl.exe", "-d", "Codex-Wsl-Lab", "-u", "codexlab", "--"])
        self.assertIn("--inspect-index-json", command)
        self.assertEqual(command[-1], "/home/codexlab/work/codex-workspace/workspace")

    def test_explicit_codegraph_dir_override_is_preserved(self) -> None:
        with mock.patch.dict(runtime.os.environ, {"CODEGRAPH_DIR": ".codegraph-custom"}):
            self.assertEqual(runtime.codegraph_dir_name(self.project), ".codegraph-custom")

    def test_hub_schema_is_hub_first_without_legacy_ack(self) -> None:
        spec = local_mcp_hub_specs.codegraph_tool_specs()[0]
        schema = spec["inputSchema"]
        self.assertEqual(spec["name"], "codegraph.explore")
        self.assertEqual(schema["required"], ["query"])
        self.assertNotIn("fallback_ack", schema["properties"])

    def test_hub_and_native_facades_delegate_to_shared_runtime(self) -> None:
        shared = {
            "ok": True,
            "analysis": "shared analysis",
            "stderr": "",
            "index": {"ok": True},
            "freshness": {"state": "fresh"},
            "refresh": {"state": "coalesced"},
            "degraded": False,
        }
        with mock.patch.object(local_mcp_hub, "query_codegraph", return_value=dict(shared)) as hub_query:
            hub_result = local_mcp_hub.LocalMcpHub.codegraph_explore(None, {"query": "flow"})
        self.assertTrue(hub_result["ok"])
        hub_query.assert_called_once()
        self.assertEqual(hub_query.call_args.kwargs["project_path"], str(local_mcp_hub.CODEGRAPH_PROJECT_ROOT))
        with mock.patch.object(codegraph_fresh_mcp_server, "query_codegraph", return_value=dict(shared)) as native_query:
            native_result = codegraph_fresh_mcp_server.CodeGraphFreshService().codegraph_explore({"query": "flow"})
        self.assertFalse(native_result["isError"])
        self.assertIn("shared analysis", native_result["content"][0]["text"])
        native_query.assert_called_once()

    def test_launch_guard_delegates_to_nonblocking_runtime_prelaunch(self) -> None:
        expected = {"ok": True, "phase": "usable_index_background_refresh"}
        with mock.patch.object(mcp_launch_guard, "codegraph_runtime_prelaunch_check", return_value=expected) as check:
            result = mcp_launch_guard.codegraph_prelaunch([sys.executable, "codegraph_fresh_mcp_server.py"])
        self.assertEqual(result, expected)
        check.assert_called_once_with(mcp_launch_guard.ROOT)


if __name__ == "__main__":
    unittest.main()
