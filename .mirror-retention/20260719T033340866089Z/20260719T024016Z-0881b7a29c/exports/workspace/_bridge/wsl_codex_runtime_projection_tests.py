from __future__ import annotations

import json
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

import wsl_codex_runtime as runtime  # noqa: E402


THREAD_COLUMNS = (
    "id",
    "rollout_path",
    "created_at",
    "updated_at",
    "source",
    "model_provider",
    "cwd",
    "title",
    "sandbox_policy",
    "approval_mode",
    "tokens_used",
    "has_user_event",
    "archived",
    "archived_at",
    "git_sha",
    "git_branch",
    "git_origin_url",
    "cli_version",
    "first_user_message",
    "agent_nickname",
    "agent_role",
    "memory_mode",
    "model",
    "reasoning_effort",
    "agent_path",
    "created_at_ms",
    "updated_at_ms",
    "thread_source",
    "preview",
    "recency_at",
    "recency_at_ms",
    "history_mode",
)


def create_state_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE threads (
            id TEXT PRIMARY KEY,
            rollout_path TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            source TEXT NOT NULL,
            model_provider TEXT NOT NULL,
            cwd TEXT NOT NULL,
            title TEXT NOT NULL,
            sandbox_policy TEXT NOT NULL,
            approval_mode TEXT NOT NULL,
            tokens_used INTEGER NOT NULL DEFAULT 0,
            has_user_event INTEGER NOT NULL DEFAULT 0,
            archived INTEGER NOT NULL DEFAULT 0,
            archived_at INTEGER,
            git_sha TEXT,
            git_branch TEXT,
            git_origin_url TEXT,
            cli_version TEXT NOT NULL DEFAULT '',
            first_user_message TEXT NOT NULL DEFAULT '',
            agent_nickname TEXT,
            agent_role TEXT,
            memory_mode TEXT NOT NULL DEFAULT 'enabled',
            model TEXT,
            reasoning_effort TEXT,
            agent_path TEXT,
            created_at_ms INTEGER,
            updated_at_ms INTEGER,
            thread_source TEXT,
            preview TEXT NOT NULL DEFAULT '',
            recency_at INTEGER NOT NULL DEFAULT 0,
            recency_at_ms INTEGER NOT NULL DEFAULT 0,
            history_mode TEXT NOT NULL DEFAULT 'legacy'
        )
        """
    )
    connection.commit()
    connection.close()


def thread_row(
    thread_id: str,
    rollout_path: str,
    *,
    cwd: str = "W:\\",
    title: str = "",
    sandbox_policy: str = '{"type":"read-only"}',
    approval_mode: str = "on-request",
    agent_path: str | None = None,
    has_user_event: int = 0,
    archived: int = 0,
    first_user_message: str = "",
    preview: str = "",
    created_at: int = 10,
    updated_at: int = 20,
    recency_at: int = 20,
) -> tuple[object, ...]:
    values = {
        "id": thread_id,
        "rollout_path": rollout_path,
        "created_at": created_at,
        "updated_at": updated_at,
        "source": "vscode",
        "model_provider": "custom",
        "cwd": cwd,
        "title": title,
        "sandbox_policy": sandbox_policy,
        "approval_mode": approval_mode,
        "tokens_used": 0,
        "has_user_event": has_user_event,
        "archived": archived,
        "archived_at": None,
        "git_sha": None,
        "git_branch": None,
        "git_origin_url": None,
        "cli_version": "0.1.0",
        "first_user_message": first_user_message,
        "agent_nickname": None,
        "agent_role": None,
        "memory_mode": "enabled",
        "model": None,
        "reasoning_effort": None,
        "agent_path": agent_path,
        "created_at_ms": created_at * 1000,
        "updated_at_ms": updated_at * 1000,
        "thread_source": "desktop",
        "preview": preview,
        "recency_at": recency_at,
        "recency_at_ms": recency_at * 1000,
        "history_mode": "legacy",
    }
    return tuple(values[column] for column in THREAD_COLUMNS)


def insert_thread(path: Path, row: tuple[object, ...]) -> None:
    connection = sqlite3.connect(path)
    placeholders = ", ".join("?" for _ in THREAD_COLUMNS)
    connection.execute(
        f"INSERT INTO threads ({', '.join(THREAD_COLUMNS)}) VALUES ({placeholders})",
        row,
    )
    connection.commit()
    connection.close()


def create_session(sessions: Path, thread_id: str, *, cwd: str = "W:\\") -> tuple[Path, Path]:
    relative = Path("2026/07/18") / f"rollout-2026-07-18T00-00-00-{thread_id}.jsonl"
    source = sessions / relative
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        json.dumps({"type": "session_meta", "payload": {"id": thread_id, "cwd": cwd}}) + "\n",
        encoding="utf-8",
    )
    return source, relative


class WslStateProjectionTests(unittest.TestCase):
    def fixture(self, root: Path) -> tuple[Path, Path, Path, Path]:
        windows_sessions = root / "windows-sessions"
        windows_state = root / "windows-state.sqlite"
        wsl_home = root / "wsl-home"
        wsl_state = wsl_home / "state_5.sqlite"
        windows_sessions.mkdir()
        wsl_home.mkdir()
        create_state_db(windows_state)
        create_state_db(wsl_state)
        return windows_sessions, windows_state, wsl_home, wsl_state

    def project(self, fixture: tuple[Path, Path, Path, Path], *, write: bool) -> dict[str, object]:
        windows_sessions, windows_state, wsl_home, wsl_state = fixture
        with (
            mock.patch.object(runtime, "WINDOWS_SESSIONS", windows_sessions),
            mock.patch.object(runtime, "WINDOWS_STATE_DB", windows_state, create=True),
            mock.patch.object(runtime, "CODEX_HOME", wsl_home),
            mock.patch.object(runtime, "STATE_DB", wsl_state),
        ):
            return runtime.project_state_db(write=write)

    def test_plugin_projection_uses_enabled_windows_plugins_and_excludes_install_backups(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            windows_home = root / "windows-home"
            wsl_home = root / "wsl-home"
            source = windows_home / "plugins" / "cache" / "openai-bundled" / "browser" / "1.2.3"
            source.mkdir(parents=True)
            (source / ".codex-plugin").mkdir()
            (source / ".codex-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
            (windows_home / "plugins" / "cache" / "openai-bundled" / "plugin-install-old" / "browser" / "1.0" / ".codex-plugin").mkdir(parents=True)
            (windows_home / ".tmp" / "bundled-marketplaces" / "openai-bundled" / ".agents" / "plugins").mkdir(parents=True)
            (windows_home / ".tmp" / "bundled-marketplaces" / "openai-bundled" / ".agents" / "plugins" / "marketplace.json").write_text("{}", encoding="utf-8")
            (windows_home / "config.toml").write_text(
                '[plugins."browser@openai-bundled"]\nenabled = true\n[plugins."disabled@openai-bundled"]\nenabled = false\n',
                encoding="utf-8",
            )
            with (
                mock.patch.object(runtime, "WINDOWS_CODEX_HOME", windows_home),
                mock.patch.object(runtime, "CODEX_HOME", wsl_home),
                mock.patch.object(runtime, "PLUGIN_MANIFEST", wsl_home / "plugin-projection-manifest.json"),
            ):
                plan = runtime.project_plugins(write=False)
                result = runtime.project_plugins(write=True)
            self.assertEqual(1, plan["enabled_count"])
            self.assertEqual(1, result["projected_count"])
            self.assertEqual([], result["missing"])
            projected = wsl_home / "plugins" / "cache" / "openai-bundled" / "browser" / "1.2.3"
            self.assertTrue(projected.is_symlink())
            self.assertFalse((wsl_home / "plugins" / "cache" / "openai-bundled" / "plugin-install-old").exists())

    def test_render_config_includes_enabled_plugin_tables(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            windows_home = root / "windows-home"
            wsl_home = root / "wsl-home"
            windows_home.mkdir()
            (windows_home / "config.toml").write_text(
                '[plugins."browser@openai-bundled"]\nenabled = true\n', encoding="utf-8"
            )
            with (
                mock.patch.object(runtime, "WINDOWS_CODEX_HOME", windows_home),
                mock.patch.object(runtime, "CODEX_HOME", wsl_home),
                mock.patch.object(runtime, "TEMPLATE", runtime.ROOT / "codex-home" / "config.wsl.template.toml"),
                mock.patch.object(runtime, "NODE_WRAPPER", runtime.ROOT / "workspace" / "_bridge" / "codex_node_repl_wsl.sh"),
            ):
                rendered = runtime.render_config()
            parsed = tomllib.loads(rendered)
            self.assertTrue(parsed["plugins"]["browser@openai-bundled"]["enabled"])
            self.assertEqual("local", parsed["marketplaces"]["openai-bundled"]["source_type"])

    def test_projects_display_metadata_without_overwriting_wsl_security_fields(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = self.fixture(Path(raw))
            source, relative = create_session(fixture[0], "thread-visible")
            insert_thread(
                fixture[1],
                thread_row(
                    "thread-visible",
                    str(source),
                    title="Native title",
                    sandbox_policy='{"type":"danger-full-access"}',
                    approval_mode="never",
                    has_user_event=1,
                    first_user_message="hello",
                    preview="hello preview",
                    recency_at=50,
                ),
            )
            insert_thread(
                fixture[3],
                thread_row(
                    "thread-visible",
                    "/stale/rollout.jsonl",
                    sandbox_policy='{"type":"read-only"}',
                    approval_mode="on-request",
                ),
            )

            plan = self.project(fixture, write=False)
            self.assertEqual("would_update", plan["status"])
            self.assertEqual(1, plan["metadata_update_count"])

            result = self.project(fixture, write=True)
            self.assertEqual("updated", result["status"])
            connection = sqlite3.connect(fixture[3])
            row = connection.execute(
                "SELECT rollout_path, cwd, title, sandbox_policy, approval_mode, "
                "has_user_event, first_user_message, preview, recency_at FROM threads WHERE id = ?",
                ("thread-visible",),
            ).fetchone()
            connection.close()
            self.assertEqual(str(fixture[2] / "sessions" / relative), row[0])
            self.assertEqual(str(runtime.ROOT), row[1])
            self.assertEqual("Native title", row[2])
            self.assertEqual('{"type":"read-only"}', row[3])
            self.assertEqual("on-request", row[4])
            self.assertEqual((1, "hello", "hello preview", 50), row[5:])

    def test_inserts_only_threads_backed_by_active_projected_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = self.fixture(Path(raw))
            source, relative = create_session(fixture[0], "thread-active")
            insert_thread(
                fixture[1],
                thread_row(
                    "thread-active",
                    str(source),
                    title="Active",
                    sandbox_policy='{"type":"danger-full-access"}',
                    approval_mode="never",
                    agent_path=r"C:\unsafe\agent.toml",
                    has_user_event=1,
                ),
            )
            insert_thread(
                fixture[1],
                thread_row(
                    "thread-archived",
                    str(Path(raw) / "archived_sessions" / "thread-archived.jsonl"),
                    title="Archived",
                    has_user_event=1,
                    archived=1,
                ),
            )

            result = self.project(fixture, write=True)

            self.assertEqual(1, result["inserted_count"])
            connection = sqlite3.connect(fixture[3])
            rows = connection.execute(
                "SELECT id, rollout_path, has_user_event, archived, sandbox_policy, approval_mode, agent_path "
                "FROM threads ORDER BY id"
            ).fetchall()
            connection.close()
            self.assertEqual(
                [
                    (
                        "thread-active",
                        str(fixture[2] / "sessions" / relative),
                        1,
                        0,
                        '{"type":"danger-full-access"}',
                        "never",
                        None,
                    )
                ],
                rows,
            )

    def test_rejects_archived_or_rollout_path_mismatched_source_rows(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = self.fixture(Path(raw))
            archived_source, _ = create_session(fixture[0], "thread-archived")
            mismatched_source, _ = create_session(fixture[0], "thread-mismatched")
            insert_thread(
                fixture[1],
                thread_row(
                    "thread-archived",
                    str(archived_source),
                    title="Archived",
                    archived=1,
                    has_user_event=1,
                ),
            )
            insert_thread(
                fixture[1],
                thread_row(
                    "thread-mismatched",
                    str(mismatched_source.with_name("different.jsonl")),
                    title="Mismatched",
                    has_user_event=1,
                ),
            )

            result = self.project(fixture, write=True)

            self.assertTrue(result["ok"])
            self.assertEqual("ready_with_source_gaps", result["status"])
            self.assertEqual(2, result["source_rejected_row_count"])
            connection = sqlite3.connect(fixture[3])
            count = connection.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
            connection.close()
            self.assertEqual(0, count)

    def test_merge_preserves_newer_nonempty_wsl_display_state_and_local_rows(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = self.fixture(Path(raw))
            source, _ = create_session(fixture[0], "thread-shared")
            insert_thread(
                fixture[1],
                thread_row(
                    "thread-shared",
                    str(source),
                    title="Older native title",
                    has_user_event=1,
                    preview="native preview",
                    updated_at=20,
                    recency_at=20,
                ),
            )
            insert_thread(
                fixture[3],
                thread_row(
                    "thread-shared",
                    "/stale/shared.jsonl",
                    title="Newer WSL title",
                    has_user_event=0,
                    preview="newer WSL preview",
                    updated_at=60,
                    recency_at=60,
                ),
            )
            insert_thread(
                fixture[3],
                thread_row("thread-local", "/wsl/local.jsonl", title="Local only", has_user_event=1),
            )

            self.project(fixture, write=True)

            connection = sqlite3.connect(fixture[3])
            shared = connection.execute(
                "SELECT title, has_user_event, preview, updated_at, recency_at FROM threads WHERE id = ?",
                ("thread-shared",),
            ).fetchone()
            local = connection.execute(
                "SELECT title, has_user_event FROM threads WHERE id = ?",
                ("thread-local",),
            ).fetchone()
            connection.close()
            self.assertEqual(("Newer WSL title", 1, "newer WSL preview", 60, 60), shared)
            self.assertEqual(("Local only", 1), local)

    def test_missing_windows_state_still_translates_existing_wsl_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = self.fixture(Path(raw))
            fixture[1].unlink()
            insert_thread(
                fixture[3],
                thread_row("thread-local", "/wsl/local.jsonl", cwd="W:\\", title="Local"),
            )

            result = self.project(fixture, write=True)

            self.assertEqual("updated", result["status"])
            self.assertEqual(1, result["translated_count"])
            connection = sqlite3.connect(fixture[3])
            cwd = connection.execute(
                "SELECT cwd FROM threads WHERE id = ?",
                ("thread-local",),
            ).fetchone()[0]
            connection.close()
            self.assertEqual(str(runtime.ROOT), cwd)

    def test_materialize_reports_degraded_state_without_blocking_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            codex_home = root / "codex-home"
            profile = root / ".profile"
            codex_home.mkdir()
            with (
                mock.patch.object(runtime, "CODEX_HOME", codex_home),
                mock.patch.object(runtime, "PROFILE_PATH", profile),
                mock.patch.object(runtime, "render_config", return_value="model = 'test'\n"),
                mock.patch.object(runtime, "project_sessions", return_value={"status": "projected", "changed": False}),
                mock.patch.object(
                    runtime,
                    "project_state_db",
                    return_value={"status": "locked_or_unreadable", "changed": False, "ok": False},
                ),
            ):
                result = runtime.materialize(write=False)

            self.assertTrue(result["ok"])
            self.assertTrue(result["degraded"])
            self.assertFalse(result["session_state_imported"])

    def test_render_config_preserves_projected_desktop_table(self) -> None:
        desktop_table = (
            "[desktop]\n"
            "runCodexInWindowsSubsystemForLinux = true\n"
            "codeFontSize = 13\n"
        )

        rendered = runtime.render_config(desktop_table=desktop_table)
        parsed = tomllib.loads(rendered)

        self.assertTrue(parsed["desktop"]["runCodexInWindowsSubsystemForLinux"])
        self.assertEqual(13, parsed["desktop"]["codeFontSize"])


class WslSessionProjectionSecurityTests(unittest.TestCase):
    def project(self, root: Path, *, write: bool) -> dict[str, object]:
        windows_sessions = root / "windows-sessions"
        codex_home = root / "wsl-home"
        manifest = codex_home / "session-projection-manifest.json"
        transition = codex_home / ".session-projection-transition"
        windows_sessions.mkdir(exist_ok=True)
        codex_home.mkdir(exist_ok=True)
        with (
            mock.patch.object(runtime, "WINDOWS_SESSIONS", windows_sessions),
            mock.patch.object(runtime, "CODEX_HOME", codex_home),
            mock.patch.object(runtime, "SESSION_MANIFEST", manifest),
            mock.patch.object(runtime, "SESSION_TRANSITION_ROOT", transition),
        ):
            return runtime.project_sessions(write=write)

    def test_manifest_path_traversal_is_rejected_without_deleting_outside_projection(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            codex_home = root / "wsl-home"
            target = codex_home / "sessions"
            target.mkdir(parents=True)
            victim = root / "victim.jsonl"
            victim.write_text("keep\n", encoding="utf-8")
            manifest = codex_home / "session-projection-manifest.json"
            manifest.write_text(
                json.dumps({"files": {"../../victim.jsonl": {"size": 5, "mtime_ns": 1}}}),
                encoding="utf-8",
            )

            result = self.project(root, write=True)

            self.assertFalse(result["ok"])
            self.assertEqual("manifest_invalid", result["status"])
            self.assertTrue(victim.is_file())
            self.assertEqual("keep\n", victim.read_text(encoding="utf-8"))

    def test_v3_manifest_forces_reprojection_after_context_transform_change(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source_root = root / "windows-sessions"
            source_root.mkdir()
            source = source_root / "2026" / "thread.jsonl"
            source.parent.mkdir()
            source.write_text(
                json.dumps(
                    {"type": "session_meta", "payload": {"id": "thread", "cwd": "C:\\Users\\45543\\repo"}},
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            codex_home = root / "wsl-home"
            codex_home.mkdir()
            manifest = codex_home / "session-projection-manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema": "codex-wsl-session-projection.v3",
                        "files": {
                            "2026/thread.jsonl": {
                                "size": source.stat().st_size,
                                "mtime_ns": source.stat().st_mtime_ns,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            result = self.project(root, write=False)

            self.assertTrue(result["changed"])
            self.assertEqual("would_create_projection", result["status"])

    def test_source_session_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source_root = root / "windows-sessions"
            source_root.mkdir()
            outside = root / "outside.jsonl"
            outside.write_text(
                json.dumps({"type": "session_meta", "payload": {"id": "outside", "cwd": "W:\\"}}) + "\n",
                encoding="utf-8",
            )
            linked = source_root / "2026" / "linked.jsonl"
            linked.parent.mkdir()
            linked.symlink_to(outside)

            result = self.project(root, write=True)

            self.assertFalse(result["ok"])
            self.assertEqual("unsafe_source_path", result["status"])
            self.assertFalse((root / "wsl-home" / "sessions" / "2026" / "linked.jsonl").exists())

    def test_target_parent_symlink_is_rejected_without_writing_outside_projection(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source_root = root / "windows-sessions"
            source_root.mkdir()
            source = source_root / "2026" / "07" / "thread.jsonl"
            source.parent.mkdir(parents=True)
            source.write_text(
                json.dumps({"type": "session_meta", "payload": {"id": "thread", "cwd": "W:\\"}}) + "\n",
                encoding="utf-8",
            )
            target = root / "wsl-home" / "sessions"
            outside = root / "outside-target"
            target.mkdir(parents=True)
            outside.mkdir()
            (target / "2026").symlink_to(outside, target_is_directory=True)

            result = self.project(root, write=True)

            self.assertFalse(result["ok"])
            self.assertEqual("unsafe_target_path", result["status"])
            self.assertEqual([], list(outside.rglob("*.jsonl")))

    def test_skill_child_shadowing_managed_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source-skills"
            target = root / "target-skills"
            (source / "managed-skill").mkdir(parents=True)
            (target / "managed-skill").mkdir(parents=True)

            result = runtime.link_skill_tree(source, target, write=False)

            self.assertFalse(result["ok"])
            self.assertEqual("conflicting_children", result["status"])
            self.assertEqual(["managed-skill"], result["conflicts"])

    def test_materialize_reports_optional_link_conflict_without_blocking_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            codex_home = root / "codex-home"
            profile = root / ".profile"
            node_entry = root / "node-entry"
            codex_home.mkdir()
            (codex_home / "AGENTS.md").write_text("shadowed\n", encoding="utf-8")
            with (
                mock.patch.object(runtime, "CODEX_HOME", codex_home),
                mock.patch.object(runtime, "PROFILE_PATH", profile),
                mock.patch.object(runtime, "NODE_ENTRY", node_entry),
                mock.patch.object(runtime, "render_config", return_value="model = 'test'\n"),
                mock.patch.object(runtime, "project_sessions", return_value={"status": "projected", "changed": False}),
                mock.patch.object(runtime, "project_state_db", return_value={"status": "ready", "changed": False}),
            ):
                result = runtime.materialize(write=False)

            self.assertTrue(result["ok"])
            self.assertTrue(result["degraded"])
            conflicts = [row for row in result["links"] if row.get("ok") is False]
            self.assertEqual(["conflicting_existing_path"], [row["status"] for row in conflicts])

    def test_session_projection_streams_without_reading_entire_jsonl_as_text(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source.jsonl"
            target = root / "target" / "session.jsonl"
            target.parent.mkdir()
            metadata = json.dumps(
                {"type": "session_meta", "payload": {"id": "thread", "cwd": "W:\\"}},
                separators=(",", ":"),
            ).encode("utf-8") + b"\n"
            source.write_bytes(metadata + b'{"type":"event","payload":"' + (b"x" * 1024 * 1024) + b'"}\n')

            with mock.patch.object(Path, "read_text", side_effect=AssertionError("full text read is forbidden")):
                translated, projected_cwd = runtime._session_projection_file(source, target)

            self.assertTrue(translated)
            self.assertEqual(str(runtime.ROOT), projected_cwd)
            with source.open("rb") as source_handle, target.open("rb") as target_handle:
                source_handle.readline()
                projected_metadata = json.loads(target_handle.readline())
                self.assertEqual(source_handle.read(), target_handle.read())
            self.assertEqual(str(runtime.ROOT), projected_metadata["payload"]["cwd"])

    def test_session_projection_translates_every_resume_context_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source.jsonl"
            target = root / "target" / "session.jsonl"
            target.parent.mkdir()
            windows_cwd = "C:\\Users\\45543\\Downloads\\mcsmanager_windows_release\\mcsmanager"
            records = [
                {"type": "session_meta", "payload": {"id": "thread", "cwd": windows_cwd}},
                {"type": "session_meta", "payload": {"id": "parent", "cwd": windows_cwd}},
                {
                    "type": "turn_context",
                    "payload": {
                        "cwd": windows_cwd,
                        "workspace_roots": [windows_cwd],
                        "world_state": {"workspaceRoots": [windows_cwd]},
                        "permission_profile": {
                            "file_system": {
                                "entries": [{"path": {"type": "path", "path": windows_cwd}, "access": "write"}]
                            }
                        },
                        "file_system_sandbox_policy": {
                            "entries": [{"path": {"type": "path", "path": windows_cwd + "\\.git"}, "access": "read"}]
                        },
                    },
                },
                {
                    "type": "response_item",
                    "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": windows_cwd}]},
                },
            ]
            source.write_text(
                "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records),
                encoding="utf-8",
            )

            translated, projected_cwd = runtime._session_projection_file(source, target)
            expected_cwd, _ = runtime.windows_cwd_to_wsl(records[0]["payload"]["cwd"])

            self.assertTrue(translated)
            self.assertEqual(expected_cwd, projected_cwd)
            projected = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(expected_cwd, projected[0]["payload"]["cwd"])
            self.assertEqual(expected_cwd, projected[1]["payload"]["cwd"])
            self.assertEqual(expected_cwd, projected[2]["payload"]["cwd"])
            self.assertEqual([expected_cwd], projected[2]["payload"]["workspace_roots"])
            self.assertEqual([expected_cwd], projected[2]["payload"]["world_state"]["workspaceRoots"])
            permission_path = projected[2]["payload"]["permission_profile"]["file_system"]["entries"][0]["path"]["path"]
            sandbox_path = projected[2]["payload"]["file_system_sandbox_policy"]["entries"][0]["path"]["path"]
            self.assertEqual(expected_cwd, permission_path)
            self.assertEqual(expected_cwd + "/.git", sandbox_path)
            self.assertEqual(windows_cwd, projected[3]["payload"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
