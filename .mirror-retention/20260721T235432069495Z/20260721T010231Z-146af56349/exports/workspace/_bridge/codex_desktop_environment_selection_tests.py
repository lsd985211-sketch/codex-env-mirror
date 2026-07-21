from __future__ import annotations

import json
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path


BRIDGE = Path(__file__).resolve().parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

import codex_desktop_environment_selection as selection  # noqa: E402


def successful_backup(paths: list[str], **_: object) -> dict[str, object]:
    return {"ok": True, "created_count": len(paths), "manifest_paths": ["test-manifest.json"]}


class CodexDesktopEnvironmentSelectionTests(unittest.TestCase):
    def fixture(self, root: Path, *, host_value: bool, wsl_value: bool | None) -> tuple[Path, Path, Path]:
        host = root / "host.toml"
        wsl = root / "wsl.toml"
        state = root / "selection-state.json"
        host.write_text(
            "[desktop]\n"
            f"runCodexInWindowsSubsystemForLinux = {'true' if host_value else 'false'}\n"
            "codeFontSize = 13\n",
            encoding="utf-8",
        )
        wsl_text = 'model = "test"\n'
        if wsl_value is not None:
            wsl_text += (
                "\n[desktop]\n"
                f"runCodexInWindowsSubsystemForLinux = {'true' if wsl_value else 'false'}\n"
                "sansFontSize = 14\n"
            )
        wsl.write_text(wsl_text, encoding="utf-8")
        return host, wsl, state

    def reconcile(
        self,
        host: Path,
        wsl: Path,
        state: Path,
        **kwargs: object,
    ) -> dict[str, object]:
        return selection.reconcile_environment_selection(
            host_config=host,
            wsl_config=wsl,
            state_path=state,
            write=True,
            backup_creator=successful_backup,
            **kwargs,
        )

    def test_initial_projection_uses_host_selection_and_seeds_desktop_settings(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            host, wsl, state = self.fixture(Path(raw), host_value=True, wsl_value=None)

            result = self.reconcile(host, wsl, state)

            self.assertTrue(result["ok"])
            self.assertTrue(result["selected_value"])
            self.assertEqual("host_initial", result["selection_source"])
            projected = tomllib.loads(wsl.read_text(encoding="utf-8"))
            self.assertTrue(projected["desktop"][selection.ENVIRONMENT_KEY])
            self.assertEqual(13, projected["desktop"]["codeFontSize"])

    def test_plan_does_not_create_state_directory_or_lock(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            host, wsl, _ = self.fixture(Path(raw), host_value=True, wsl_value=None)
            state = Path(raw) / "missing-state-dir" / "selection-state.json"

            result = selection.reconcile_environment_selection(
                host_config=host,
                wsl_config=wsl,
                state_path=state,
                write=False,
            )

            self.assertTrue(result["ok"])
            self.assertTrue(result["changed"])
            self.assertFalse(state.parent.exists())

    def test_wsl_change_to_windows_wins_when_host_still_has_last_synced_value(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            host, wsl, state = self.fixture(Path(raw), host_value=True, wsl_value=False)
            state.write_text(
                json.dumps({"schema": selection.STATE_SCHEMA, "last_synced_value": True}) + "\n",
                encoding="utf-8",
            )

            result = self.reconcile(host, wsl, state)

            self.assertFalse(result["selected_value"])
            self.assertEqual("wsl_changed", result["selection_source"])
            self.assertFalse(tomllib.loads(host.read_text(encoding="utf-8"))["desktop"][selection.ENVIRONMENT_KEY])
            self.assertFalse(tomllib.loads(wsl.read_text(encoding="utf-8"))["desktop"][selection.ENVIRONMENT_KEY])

    def test_explicit_wsl_selection_restores_both_sides_after_unintended_native_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            host, wsl, state = self.fixture(Path(raw), host_value=False, wsl_value=False)
            state.write_text(
                json.dumps({"schema": selection.STATE_SCHEMA, "last_synced_value": False}) + "\n",
                encoding="utf-8",
            )
            result = selection.reconcile_environment_selection(
                host_config=host, wsl_config=wsl, state_path=state, write=True,
                requested_value=True, backup_creator=successful_backup,
            )
            self.assertTrue(result["ok"], result)
            self.assertTrue(result["selected_value"])
            self.assertEqual("explicit_selection", result["selection_source"])
            self.assertTrue(tomllib.loads(host.read_text(encoding="utf-8"))["desktop"][selection.ENVIRONMENT_KEY])
            self.assertTrue(tomllib.loads(wsl.read_text(encoding="utf-8"))["desktop"][selection.ENVIRONMENT_KEY])

    def test_explicit_selection_persists_a_host_readable_desired_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            host, wsl, state = self.fixture(root, host_value=False, wsl_value=False)
            host_state = root / "host-state" / "desktop-environment-selection.json"

            result = selection.reconcile_environment_selection(
                host_config=host,
                wsl_config=wsl,
                state_path=state,
                host_state_path=host_state,
                write=True,
                requested_value=True,
                backup_creator=successful_backup,
            )

            self.assertTrue(result["ok"])
            self.assertIn("host_state", result["written"])
            self.assertTrue(json.loads(host_state.read_text(encoding="utf-8"))["last_synced_value"])

    def test_host_state_is_authoritative_when_wsl_state_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            host, wsl, state = self.fixture(root, host_value=True, wsl_value=False)
            state.write_text(
                json.dumps({"schema": selection.STATE_SCHEMA, "last_synced_value": False}) + "\n",
                encoding="utf-8",
            )
            host_state = root / "host-state.json"
            host_state.write_text(
                json.dumps({"schema": selection.STATE_SCHEMA, "last_synced_value": True}) + "\n",
                encoding="utf-8",
            )

            result = selection.reconcile_environment_selection(
                host_config=host,
                wsl_config=wsl,
                state_path=state,
                host_state_path=host_state,
                write=False,
            )

            self.assertFalse(result["selected_value"])
            self.assertEqual("wsl_changed", result["selection_source"])

    def test_runtime_fallback_marker_restores_desired_wsl_selection(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            host, wsl, state = self.fixture(root, host_value=False, wsl_value=False)
            host_state = root / "host-state.json"
            host_state.write_text(
                json.dumps({
                    "schema": selection.STATE_SCHEMA,
                    "last_synced_value": True,
                    "desired_value": True,
                    "effective_value": False,
                    "fallback_pending": True,
                }) + "\n",
                encoding="utf-8",
            )

            result = selection.reconcile_environment_selection(
                host_config=host,
                wsl_config=wsl,
                state_path=state,
                host_state_path=host_state,
                write=True,
                backup_creator=successful_backup,
            )

            self.assertTrue(result["ok"], result)
            self.assertTrue(result["selected_value"])
            self.assertEqual("fallback_recovery", result["selection_source"])
            self.assertFalse(json.loads(host_state.read_text(encoding="utf-8"))["fallback_pending"])
            self.assertTrue(tomllib.loads(host.read_text(encoding="utf-8"))["desktop"][selection.ENVIRONMENT_KEY])

    def test_explicit_windows_selection_overrides_pending_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            host, wsl, state = self.fixture(root, host_value=False, wsl_value=False)
            host_state = root / "host-state.json"
            host_state.write_text(
                json.dumps({
                    "schema": selection.STATE_SCHEMA,
                    "last_synced_value": True,
                    "desired_value": True,
                    "effective_value": False,
                    "fallback_pending": True,
                }) + "\n",
                encoding="utf-8",
            )

            result = selection.reconcile_environment_selection(
                host_config=host,
                wsl_config=wsl,
                state_path=state,
                host_state_path=host_state,
                write=True,
                requested_value=False,
                backup_creator=successful_backup,
            )

            self.assertTrue(result["ok"], result)
            self.assertFalse(result["selected_value"])
            self.assertEqual("explicit_selection", result["selection_source"])
            self.assertFalse(json.loads(host_state.read_text(encoding="utf-8"))["fallback_pending"])

    def test_host_change_to_wsl_wins_when_wsl_still_has_last_synced_value(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            host, wsl, state = self.fixture(Path(raw), host_value=True, wsl_value=False)
            state.write_text(
                json.dumps({"schema": selection.STATE_SCHEMA, "last_synced_value": False}) + "\n",
                encoding="utf-8",
            )

            result = self.reconcile(host, wsl, state)

            self.assertTrue(result["selected_value"])
            self.assertEqual("host_changed", result["selection_source"])
            projected = tomllib.loads(wsl.read_text(encoding="utf-8"))
            self.assertTrue(projected["desktop"][selection.ENVIRONMENT_KEY])
            self.assertEqual(14, projected["desktop"]["sansFontSize"])

    def test_backup_failure_prevents_both_config_writes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            host, wsl, state = self.fixture(Path(raw), host_value=True, wsl_value=None)
            before_host = host.read_bytes()
            before_wsl = wsl.read_bytes()

            result = selection.reconcile_environment_selection(
                host_config=host,
                wsl_config=wsl,
                state_path=state,
                write=True,
                backup_creator=lambda *_args, **_kwargs: {"ok": False, "reason": "test"},
            )

            self.assertFalse(result["ok"])
            self.assertEqual("backup_failed", result["status"])
            self.assertEqual(before_host, host.read_bytes())
            self.assertEqual(before_wsl, wsl.read_bytes())
            self.assertFalse(state.exists())

    def test_host_write_failure_rolls_back_wsl_config(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            host, wsl, state = self.fixture(Path(raw), host_value=True, wsl_value=False)
            state.write_text(
                json.dumps({"schema": selection.STATE_SCHEMA, "last_synced_value": True}) + "\n",
                encoding="utf-8",
            )
            before_wsl = wsl.read_bytes()

            def fail_host_write(path: Path, text: str) -> None:
                if path == host:
                    raise OSError("host write failed")
                selection.atomic_write_text(path, text)

            result = self.reconcile(host, wsl, state, writer=fail_host_write)

            self.assertFalse(result["ok"])
            self.assertEqual("write_failed_rolled_back", result["status"])
            self.assertEqual(before_wsl, wsl.read_bytes())
            self.assertTrue(tomllib.loads(host.read_text(encoding="utf-8"))["desktop"][selection.ENVIRONMENT_KEY])

    def test_source_change_during_backup_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            host, wsl, state = self.fixture(Path(raw), host_value=True, wsl_value=False)
            state.write_text(
                json.dumps({"schema": selection.STATE_SCHEMA, "last_synced_value": True}) + "\n",
                encoding="utf-8",
            )

            def changing_backup(_paths: list[str], **_: object) -> dict[str, object]:
                host.write_text(
                    "[desktop]\nrunCodexInWindowsSubsystemForLinux = true\ncodeFontSize = 17\n",
                    encoding="utf-8",
                )
                return {"ok": True, "created_count": 2, "manifest_paths": ["test-manifest.json"]}

            result = selection.reconcile_environment_selection(
                host_config=host,
                wsl_config=wsl,
                state_path=state,
                write=True,
                backup_creator=changing_backup,
            )

            self.assertFalse(result["ok"])
            self.assertEqual("source_changed_during_reconcile", result["status"])
            self.assertEqual(17, tomllib.loads(host.read_text(encoding="utf-8"))["desktop"]["codeFontSize"])
            self.assertFalse(tomllib.loads(wsl.read_text(encoding="utf-8"))["desktop"][selection.ENVIRONMENT_KEY])

    def test_state_write_failure_rolls_back_both_configs(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            host, wsl, state = self.fixture(Path(raw), host_value=True, wsl_value=False)
            state.write_text(
                json.dumps({"schema": selection.STATE_SCHEMA, "last_synced_value": True}) + "\n",
                encoding="utf-8",
            )
            before_host = host.read_bytes()
            before_wsl = wsl.read_bytes()
            before_state = state.read_bytes()

            def fail_state_write(path: Path, text: str) -> None:
                if path == state:
                    raise OSError("state write failed")
                selection.atomic_write_text(path, text)

            result = self.reconcile(host, wsl, state, writer=fail_state_write)

            self.assertFalse(result["ok"])
            self.assertEqual("write_failed_rolled_back", result["status"])
            self.assertEqual(before_host, host.read_bytes())
            self.assertEqual(before_wsl, wsl.read_bytes())
            self.assertEqual(before_state, state.read_bytes())


if __name__ == "__main__":
    unittest.main()
