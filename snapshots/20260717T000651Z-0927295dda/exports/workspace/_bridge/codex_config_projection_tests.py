#!/usr/bin/env python3

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path


BRIDGE = Path(__file__).resolve().parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

import codex_config_projection as projection


PROVIDER = json.dumps(
    {
        "auth": {"OPENAI_API_KEY": "<SECRET:OPENAI_API_KEY>"},
        "config": (
            'model_provider = "custom"\n'
            'model = "gpt-test"\n'
            '[model_providers.custom]\n'
            'name = "Custom"\n'
            'base_url = "https://example.invalid/v1"\n'
            'experimental_bearer_token = "<SECRET:OPENAI_API_KEY>"\n'
        ),
    }
)


def make_fixture(root: Path, *, common: str, live: str) -> tuple[Path, Path]:
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
            ("provider-1", "codex", "Provider", PROVIDER, 1, 1, 1, '{"commonConfigEnabled":false,"keep":"yes"}'),
        )
        connection.execute(
            "INSERT INTO providers VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            ("provider-2", "codex", "Provider 2", PROVIDER, 0, 2, 0, '{}'),
        )
        connection.execute("INSERT INTO settings VALUES(?, ?)", (projection.COMMON_KEY, common))
        connection.commit()
    finally:
        connection.close()
    return config, database


def read_common(database: Path) -> str:
    connection = sqlite3.connect(database)
    try:
        return str(connection.execute("SELECT value FROM settings WHERE key = ?", (projection.COMMON_KEY,)).fetchone()[0])
    finally:
        connection.close()


def run() -> None:
    live = (
        'model_provider = "custom"\n'
        'model = "gpt-test"\n'
        'sandbox_mode = "danger-full-access"\n'
        'novel_setting = "preserve-me"\n'
        '[model_providers.custom]\n'
        'name = "Custom"\n'
        'base_url = "https://example.invalid/v1"\n'
        'experimental_bearer_token = "<SECRET:OPENAI_API_KEY>"\n'
        '[desktop]\n'
        'show-context-window-usage = true\n'
        '[plugins."documents@openai-primary-runtime"]\n'
        'enabled = true\n'
    )
    common = '[desktop]\nshow-context-window-usage = false\n\n[legacy]\nkeep = true\n'
    with tempfile.TemporaryDirectory() as raw:
        config, database = make_fixture(Path(raw), common=common, live=live)
        snap = projection.snapshot(config, database)
        encoded = json.dumps(snap, ensure_ascii=False)
        assert "<SECRET:OPENAI_API_KEY>" not in encoded
        assert snap["classification_counts"]["provider_owned"] >= 2
        assert snap["unowned_count"] == 1

        additions = projection.apply_projection(
            config,
            database,
            additions_only=True,
            sync_desktop=False,
            backup=False,
        )
        assert additions["ok"]
        text = read_common(database)
        assert 'novel_setting = "preserve-me"' in text
        assert 'show-context-window-usage = false' in text
        assert "experimental_bearer_token" not in text
        assert 'model = "gpt-test"' not in text
        connection = sqlite3.connect(database)
        try:
            provider_meta = [json.loads(row[0]) for row in connection.execute("SELECT meta FROM providers ORDER BY id")]
        finally:
            connection.close()
        assert all(item.get("commonConfigEnabled") is True for item in provider_meta)
        assert provider_meta[0].get("keep") == "yes"

        full = projection.apply_projection(config, database, sync_desktop=False, backup=False)
        assert full["ok"]
        text = read_common(database)
        assert 'show-context-window-usage = true' in text
        assert "[legacy]" in text and "keep = true" in text
        assert projection.validate(config, database)["ok"]

        connection = sqlite3.connect(database)
        try:
            meta = json.loads(connection.execute("SELECT meta FROM providers WHERE id = 'provider-1'").fetchone()[0])
            meta["commonConfigEnabled"] = False
            connection.execute("UPDATE providers SET meta = ? WHERE id = 'provider-1'", (json.dumps(meta),))
            connection.commit()
        finally:
            connection.close()
        independent_validate = projection.validate(config, database)
        independent_doctor = projection.doctor(config, database)
        assert independent_validate["ok"]
        assert independent_doctor["ok"] and independent_doctor["advisories"]

        config.write_text('[desktop]\nshow-context-window-usage = true\n', encoding="utf-8")
        no_delete = projection.apply_projection(config, database, sync_desktop=False, backup=False)
        assert no_delete["ok"]
        assert "[legacy]" in read_common(database)

        remove = projection.apply_projection(
            config,
            database,
            removals=(("legacy", "keep"),),
            sync_desktop=False,
            backup=False,
        )
        assert remove["ok"]
        assert "keep = true" not in read_common(database)

        recovery_state = projection.load_state(config, database)
        recovered_text, recovered_paths = projection.recover_desktop_settings(
            {**recovery_state, "live": {}, "common": {}, "common_text": ""},
            {
                "rows": [
                    {"key": "show-context-window-usage", "found": True, "value": True},
                    {"key": "show-ultra-in-model-picker-slider", "found": True, "value": False},
                ]
            },
        )
        assert "show-context-window-usage = true" in recovered_text
        assert "show-ultra-in-model-picker-slider = false" in recovered_text
        assert len(recovered_paths) == 2

        authoritative_text, authoritative_paths = projection.recover_desktop_settings(
            {
                **recovery_state,
                "live": {"desktop": {"show-context-window-usage": False}},
                "common": {"desktop": {"show-ultra-in-model-picker-slider": True}},
                "common_text": "[desktop]\nshow-ultra-in-model-picker-slider = true\n",
            },
            {
                "rows": [
                    {"key": "show-context-window-usage", "found": True, "value": True},
                    {"key": "show-ultra-in-model-picker-slider", "found": True, "value": False},
                ]
            },
        )
        assert authoritative_paths == []
        assert "show-ultra-in-model-picker-slider = true" in authoritative_text

        active_text, active_paths = projection.render_active_config(
            {
                **recovery_state,
                "live": {},
                "live_text": "",
                "common": {"desktop": {"show-context-window-usage": True}},
                "common_text": "[desktop]\nshow-context-window-usage = true\n",
                "managed": {"desktop": {"show-context-window-usage": True}},
            }
        )
        assert "show-context-window-usage = true" in active_text
        assert active_paths == ["active-config:/desktop/show-context-window-usage"]

        explicit_active_text, explicit_active_paths = projection.render_active_config(
            {
                **recovery_state,
                "live": {"desktop": {"show-context-window-usage": False}},
                "live_text": "[desktop]\nshow-context-window-usage = false\n",
                "common": {"desktop": {"show-context-window-usage": True}},
                "common_text": "[desktop]\nshow-context-window-usage = true\n",
                "managed": {"desktop": {"show-context-window-usage": True}},
            }
        )
        assert "show-context-window-usage = false" in explicit_active_text
        assert explicit_active_paths == []

    print(json.dumps({"ok": True, "tests": 14}, ensure_ascii=False))


if __name__ == "__main__":
    run()
