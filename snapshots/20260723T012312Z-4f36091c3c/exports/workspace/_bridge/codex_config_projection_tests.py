#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import tomllib
from pathlib import Path
from unittest import mock


BRIDGE = Path(__file__).resolve().parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

import codex_config_projection as projection  # noqa: E402


PROVIDER_CONFIG = (
    'model_provider = "custom"\n'
    'model = "gpt-test"\n'
    '[model_providers.custom]\n'
    'name = "Custom"\n'
    'base_url = "https://example.invalid/v1"\n'
    'experimental_bearer_token = "<SECRET:OPENAI_API_KEY>"\n'
)
PROVIDER = json.dumps({"auth": {"OPENAI_API_KEY": "<SECRET:OPENAI_API_KEY>"}, "config": PROVIDER_CONFIG})


def make_fixture(root: Path, *, live: str, provider_record: str = PROVIDER) -> tuple[Path, Path]:
    config = root / "config.toml"
    database = root / "cc-switch.db"
    config.write_text(live, encoding="utf-8")
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "CREATE TABLE providers(id TEXT, app_type TEXT, name TEXT, settings_config TEXT, "
            "created_at INTEGER, sort_index INTEGER, is_current BOOLEAN, meta TEXT)"
        )
        connection.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT)")
        connection.execute(
            "INSERT INTO providers VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            ("provider-1", "codex", "Provider", provider_record, 1, 1, 1, '{"commonConfigEnabled":false,"keep":"yes"}'),
        )
        connection.execute(
            "INSERT INTO providers VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            ("provider-2", "codex", "Provider 2", PROVIDER, 0, 2, 0, '{"commonConfigEnabled":true}'),
        )
        connection.execute(
            "INSERT INTO settings VALUES(?, ?)",
            (projection.LEGACY_COMMON_KEY, '[legacy]\nunsafe_authority = true\n'),
        )
        connection.execute(
            "INSERT INTO settings VALUES(?, ?)",
            (projection.LEGACY_MANAGED_DB_KEY, '{"legacy":true}'),
        )
        connection.commit()
    finally:
        connection.close()
    return config, database


def setting_exists(database: Path, key: str) -> bool:
    connection = sqlite3.connect(database)
    try:
        return connection.execute("SELECT 1 FROM settings WHERE key = ?", (key,)).fetchone() is not None
    finally:
        connection.close()


def provider_meta(database: Path) -> list[dict]:
    connection = sqlite3.connect(database)
    try:
        return [json.loads(row[0] or "{}") for row in connection.execute("SELECT meta FROM providers ORDER BY id")]
    finally:
        connection.close()


def write_environment_selection_state(config: Path, desired: bool, *, schema: str | None = None) -> Path:
    path = projection.environment_selection_state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": schema or projection.ENVIRONMENT_SELECTION_STATE_SCHEMA,
                "desired_value": desired,
                "effective_value": desired,
            }
        ),
        encoding="utf-8",
    )
    return path


def run() -> None:
    assert projection.config_runtime_scope(Path("/home/codexlab/.codex/config.toml")) == "wsl"
    assert projection.config_runtime_scope(Path("/home/codexlab/.codex-app/config.toml")) == "wsl"
    assert projection.config_runtime_scope(Path("/mnt/c/Users/45543/.codex/config.toml")) == "windows_host"
    for config in (
        Path("/mnt/c/Users/45543/.codex/config.toml"),
        Path("/home/codexlab/.codex-app/config.toml"),
    ):
        assert projection.projection_lock_path(config) == (
            config.parent / "state" / "runtime" / "codex_config_projection" / "projection.lock"
        )
    live = (
        'model_provider = "custom"\n'
        + 'model = "gpt-test"\n'
        + 'model_catalog_json = "stale-catalog.json"\n'
        + 'sandbox_mode = "danger-full-access"\n'
        + 'personality = "pragmatic"\n'
        + 'notify = ["cmd.exe", "turn-ended"]\n'
        + 'novel_setting = "preserve-me"\n'
        + '[model_providers.custom]\n'
        + 'name = "Custom"\n'
        + 'base_url = "https://example.invalid/v1"\n'
        + 'experimental_bearer_token = "<SECRET:OPENAI_API_KEY>"\n'
        + '[desktop]\nshow-context-window-usage = true\nrunCodexInWindowsSubsystemForLinux = true\n'
        + '[plugins."documents@openai-primary-runtime"]\nenabled = true\n'
        + '[mcp_servers.playwright]\ncommand = "playwright"\n'
        + '[mcp_servers.context7]\ncommand = "context7"\n'
        + '[projects."c:\\\\users\\\\example"]\ntrust_level = "trusted"\n'
        + '[shell_environment_policy.set]\nNODE_REPL_TRUSTED_CODE_PATHS = "C:\\\\Users\\\\example\\\\.codex"\n'
        + '[windows]\nsandbox = "elevated"\n'
    )
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        config, database = make_fixture(root, live=PROVIDER_CONFIG)
        lease = root / "capture-lease.json"
        lease.write_text(json.dumps({"expires_at_epoch": projection.datetime.now(projection.timezone.utc).timestamp() + 60}), encoding="utf-8")
        previous_lease = os.environ.get(projection.CAPTURE_LEASE_ENV)
        os.environ[projection.CAPTURE_LEASE_ENV] = str(lease)
        try:
            deferred = projection.apply_projection(config, database, sync_desktop=False, backup=False)
        finally:
            if previous_lease is None:
                os.environ.pop(projection.CAPTURE_LEASE_ENV, None)
            else:
                os.environ[projection.CAPTURE_LEASE_ENV] = previous_lease
        assert deferred["ok"] and deferred["deferred"] and config.read_text(encoding="utf-8") == PROVIDER_CONFIG

    with tempfile.TemporaryDirectory() as raw:
        config, database = make_fixture(Path(raw), live=PROVIDER_CONFIG)
        write_environment_selection_state(config, True)

        applied = projection.apply_projection(config, database, sync_desktop=False, backup=False)

        assert applied["ok"], applied
        active = tomllib.loads(config.read_text(encoding="utf-8"))
        assert active["desktop"]["runCodexInWindowsSubsystemForLinux"] is True
        managed = json.loads(projection.managed_projection_path(config).read_text(encoding="utf-8"))["values"]
        assert "runCodexInWindowsSubsystemForLinux" not in managed.get("desktop", {})

    with tempfile.TemporaryDirectory() as raw:
        config, database = make_fixture(Path(raw), live=PROVIDER_CONFIG)
        write_environment_selection_state(config, False)

        applied = projection.apply_projection(config, database, sync_desktop=False, backup=False)

        assert applied["ok"], applied
        active = tomllib.loads(config.read_text(encoding="utf-8"))
        assert active["desktop"]["runCodexInWindowsSubsystemForLinux"] is False

    with tempfile.TemporaryDirectory() as raw:
        config, _ = make_fixture(Path(raw), live=PROVIDER_CONFIG)
        state_path = projection.environment_selection_state_path(config)
        assert projection.runtime_local_projection_values(config) == {}
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text("{", encoding="utf-8")
        assert projection.runtime_local_projection_values(config) == {}
        write_environment_selection_state(config, True, schema="unsupported")
        assert projection.runtime_local_projection_values(config) == {}
        state_path.write_text(
            json.dumps(
                {
                    "schema": projection.ENVIRONMENT_SELECTION_STATE_SCHEMA,
                    "desired_value": "true",
                }
            ),
            encoding="utf-8",
        )
        assert projection.runtime_local_projection_values(config) == {}

    runtime_path = ("desktop", "runCodexInWindowsSubsystemForLinux")
    assert projection.DESKTOP_RUNTIME_SETTING_KEYS[runtime_path] == "runCodexInWindowsSubsystemForLinux"
    fallback = projection._desktop_fallback_values(
        {"rows": [{"key": "runCodexInWindowsSubsystemForLinux", "found": True, "value": False}]}
    )
    assert runtime_path not in fallback
    client = mock.Mock()
    client.evaluate.return_value = {"ok": True, "rows": []}
    with (
        mock.patch.object(
            projection.codex_desktop_model_runtime,
            "_find_codex_page",
            return_value=(9222, "ws://desktop", [], ""),
        ),
        mock.patch.object(
            projection.codex_desktop_model_runtime,
            "_CdpClient",
            return_value=client,
        ),
    ):
        desktop_state = projection.desktop_projection_state(
            {"desktop": {"runCodexInWindowsSubsystemForLinux": False}},
            apply=True,
        )
    assert desktop_state["ok"] and desktop_state["entry_count"] == 1
    assert "runCodexInWindowsSubsystemForLinux" in client.evaluate.call_args.args[0]

    with tempfile.TemporaryDirectory() as raw:
        config, database = make_fixture(Path(raw), live=live)
        managed_path = projection.managed_projection_path(config)
        managed_path.parent.mkdir(parents=True, exist_ok=True)
        managed_path.write_text(
            json.dumps({
                "schema": "codex-managed-config-projection/v1",
                "values": {
                    "personality": "legacy",
                    "mcp_servers": {"node_repl": {"command": "/wrong/runtime"}},
                    "plugins": {"legacy@runtime": {"enabled": True}},
                    "shell_environment_policy": {"set": {"CODEX_HOME": "C:\\\\wrong"}},
                    "desktop": {"runCodexInWindowsSubsystemForLinux": False},
                },
            }),
            encoding="utf-8",
        )

        before = projection.snapshot(config, database)
        assert before["legacy_surface_count"] == 4
        assert not before["projection_current"]
        assert "<SECRET:OPENAI_API_KEY>" not in json.dumps(before, ensure_ascii=False)

        applied = projection.apply_projection(config, database, sync_desktop=False, backup=False)
        assert applied["ok"], applied
        assert not setting_exists(database, projection.LEGACY_COMMON_KEY)
        assert not setting_exists(database, projection.LEGACY_MANAGED_DB_KEY)
        metas = provider_meta(database)
        assert all("commonConfigEnabled" not in item for item in metas)
        assert metas[0]["keep"] == "yes"

        managed = json.loads(managed_path.read_text(encoding="utf-8"))["values"]
        assert managed["sandbox_mode"] == "danger-full-access"
        assert managed["personality"] == "pragmatic"
        assert managed["novel_setting"] == "preserve-me"
        assert managed["desktop"]["show-context-window-usage"] is True
        assert "runCodexInWindowsSubsystemForLinux" not in managed["desktop"]
        assert managed["mcp_servers"]["playwright"]["command"] == "playwright"
        assert "context7" not in managed.get("mcp_servers", {})
        assert managed["plugins"]["documents@openai-primary-runtime"]["enabled"] is True
        assert managed["notify"] == ["cmd.exe", "turn-ended"]
        assert managed["shell_environment_policy"]["set"]["NODE_REPL_TRUSTED_CODE_PATHS"].startswith("C:\\")
        assert managed["windows"]["sandbox"] == "elevated"
        assert "projects" not in managed
        assert "model" not in managed and "model_providers" not in managed
        assert "model_catalog_json" not in managed
        active_after_apply = tomllib.loads(config.read_text(encoding="utf-8"))
        assert "model_catalog_json" not in active_after_apply
        assert projection.validate(config, database)["ok"]

        config.write_text(PROVIDER_CONFIG, encoding="utf-8")
        restored = projection.apply_projection(config, database, additions_only=True, sync_desktop=False, backup=False)
        assert restored["ok"], restored
        restored_config = tomllib.loads(config.read_text(encoding="utf-8"))
        assert restored_config["model"] == "gpt-test"
        assert restored_config["sandbox_mode"] == "danger-full-access"
        assert restored_config["personality"] == "pragmatic"
        assert restored_config["novel_setting"] == "preserve-me"
        assert restored_config["desktop"]["show-context-window-usage"] is True
        assert "runCodexInWindowsSubsystemForLinux" not in restored_config["desktop"]
        assert restored_config["mcp_servers"]["playwright"]["command"] == "playwright"
        assert "context7" not in restored_config.get("mcp_servers", {})
        assert restored_config["plugins"]["documents@openai-primary-runtime"]["enabled"] is True
        assert restored_config["notify"] == ["cmd.exe", "turn-ended"]
        assert restored_config["shell_environment_policy"]["set"]["NODE_REPL_TRUSTED_CODE_PATHS"].startswith("C:\\")
        assert restored_config["windows"]["sandbox"] == "elevated"
        assert "projects" not in restored_config

        config.write_text(PROVIDER_CONFIG + '[desktop]\nshow-context-window-usage = false\n', encoding="utf-8")
        explicit = projection.apply_projection(config, database, sync_desktop=False, backup=False)
        assert explicit["ok"], explicit
        managed = json.loads(managed_path.read_text(encoding="utf-8"))["values"]
        assert managed["desktop"]["show-context-window-usage"] is False

        config.write_text(PROVIDER_CONFIG, encoding="utf-8")
        readback = {
            "rows": [
                {"key": "show-context-window-usage", "found": True, "value": True},
                {"key": "show-ultra-in-model-picker-slider", "found": True, "value": False},
            ]
        }
        state = projection.load_state(config, database)
        values = projection.managed_projection_values(state, desktop_readback=readback)
        assert values["desktop"]["show-context-window-usage"] is False
        assert values["desktop"]["show-ultra-in-model-picker-slider"] is False

        connection = sqlite3.connect(database)
        try:
            connection.execute("INSERT INTO settings VALUES(?, ?)", (projection.LEGACY_COMMON_KEY, "stale"))
            meta = provider_meta(database)[0]
            meta["commonConfigEnabled"] = True
            connection.execute("UPDATE providers SET meta = ? WHERE id = 'provider-1'", (json.dumps(meta),))
            connection.commit()
        finally:
            connection.close()
        drift = projection.validate(config, database)
        assert not drift["ok"]
        cleaned = projection.apply_projection(config, database, sync_desktop=False, backup=False)
        assert cleaned["ok"]
        assert not setting_exists(database, projection.LEGACY_COMMON_KEY)
        assert all("commonConfigEnabled" not in item for item in provider_meta(database))

        quoted = '[projects."c:\\\\users\\\\example"]\ntrust_level = "trusted"\n'
        unchanged, changed = projection.codex_state_repair.ensure_project_trusted(
            quoted,
            r"projects.c:\users\example.trust_level",
            "trusted",
        )
        assert not changed and unchanged.count("[projects.") == 1
        duplicate = quoted + "\n[projects.'c:\\users\\example']\ntrust_level = \"trusted\"\n"
        normalized, changed = projection.codex_state_repair.normalize_duplicate_project_tables(duplicate)
        assert changed and normalized.count("[projects.") == 1

    with tempfile.TemporaryDirectory() as raw:
        wsl_root = Path(raw) / ".codex-app"
        wsl_root.mkdir()
        config, database = make_fixture(wsl_root, live=live)
        managed_path = projection.managed_projection_path(config)
        managed_path.parent.mkdir(parents=True, exist_ok=True)
        managed_path.write_text(
            json.dumps({
                "schema": "codex-managed-config-projection/v1",
                "runtime_scope": "windows_host",
                "values": {
                    "personality": "legacy",
                    "mcp_servers": {"node_repl": {"command": "cmd.exe"}},
                    "plugins": {"legacy@runtime": {"enabled": True}},
                    "shell_environment_policy": {"set": {"CODEX_HOME": "C:\\\\wrong"}},
                    "desktop": {"runCodexInWindowsSubsystemForLinux": False},
                },
            }),
            encoding="utf-8",
        )

        applied = projection.apply_projection(config, database, sync_desktop=False, backup=False)
        assert applied["ok"], applied
        managed = json.loads(managed_path.read_text(encoding="utf-8"))
        assert managed["runtime_scope"] == "wsl"
        values = managed["values"]
        assert values["personality"] == "pragmatic"
        assert values["desktop"]["show-context-window-usage"] is True
        assert "runCodexInWindowsSubsystemForLinux" not in values["desktop"]
        for platform_root in (
            "marketplaces",
            "mcp_servers",
            "notify",
            "plugins",
            "projects",
            "shell_environment_policy",
            "windows",
        ):
            assert platform_root not in values

        config.write_text(PROVIDER_CONFIG, encoding="utf-8")
        restored = projection.apply_projection(config, database, additions_only=True, sync_desktop=False, backup=False)
        assert restored["ok"], restored
        restored_config = tomllib.loads(config.read_text(encoding="utf-8"))
        assert restored_config["personality"] == "pragmatic"
        assert restored_config["desktop"]["show-context-window-usage"] is True
        assert "mcp_servers" not in restored_config
        assert "plugins" not in restored_config
        assert "shell_environment_policy" not in restored_config

    with tempfile.TemporaryDirectory() as raw:
        catalog_provider = json.dumps(
            {
                "auth": {"OPENAI_API_KEY": "<SECRET:OPENAI_API_KEY>"},
                "config": PROVIDER_CONFIG,
                "modelCatalog": [{"slug": "gpt-test", "displayName": "GPT Test"}],
            }
        )
        live_with_catalog = 'model_catalog_json = "cc-switch-model-catalog.json"\n' + PROVIDER_CONFIG
        config, database = make_fixture(Path(raw), live=live_with_catalog, provider_record=catalog_provider)
        applied = projection.apply_projection(config, database, sync_desktop=False, backup=False)
        assert applied["ok"], applied
        active = tomllib.loads(config.read_text(encoding="utf-8"))
        managed = json.loads(projection.managed_projection_path(config).read_text(encoding="utf-8"))["values"]
        assert active["model_catalog_json"] == "cc-switch-model-catalog.json"
        assert "model_catalog_json" not in managed
        provider = projection.snapshot(config, database)["provider"]
        assert provider["model_catalog_active"] is True
        assert provider["model_catalog_sha256"]

    print(json.dumps({"ok": True, "tests": 40}, ensure_ascii=False))


if __name__ == "__main__":
    run()
