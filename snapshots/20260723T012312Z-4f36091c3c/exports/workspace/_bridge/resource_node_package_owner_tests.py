#!/usr/bin/env python3
"""Focused regression coverage for platform-aware managed npm installation."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import resource_node_package_owner as owner


def assert_ok(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    assert_ok(
        owner._npm_command(platform_name="nt") == [str(owner.WINDOWS_NODE), str(owner.WINDOWS_NPM_CLI)],
        "Windows must retain the existing node-plus-npm-cli invocation",
    )
    with mock.patch.object(owner.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}"):
        assert_ok(owner._npm_command(platform_name="posix") == ["/usr/bin/npm"], "WSL must invoke native npm")

    with tempfile.TemporaryDirectory() as temp_dir:
        target_dir = Path(temp_dir) / "graphify"

        def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
            assert_ok(command[0] == "npm", "WSL managed install must not invoke a Windows executable")
            prefix = Path(command[command.index("--prefix") + 1])
            package_path = prefix / "node_modules" / "@scope" / "example"
            package_path.mkdir(parents=True)
            (package_path / "package.json").write_text(json.dumps({"version": "1.2.3", "bin": {"example": "cli.js"}}), encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="installed", stderr="")

        request = {
            "target": "@scope/example@1.2.3",
            "target_dir": str(target_dir),
            "allow_filesystem_write": True,
            "metadata": {
                "package_action": "install",
                "package_ecosystem": "npm",
                "package_target_dir_explicit": True,
                "install_approved": True,
            },
        }
        with (
            mock.patch.object(owner, "_npm_runtime_available", return_value=True),
            mock.patch.object(owner, "_npm_command", return_value=["npm"]),
            mock.patch.object(owner.subprocess, "run", side_effect=fake_run),
        ):
            result = owner.execute_node_package_request(request, {"env": {}, "unset_env": []}, 10, lambda **payload: payload)

        assert_ok(result.get("ok") is True, f"approved isolated npm install should verify: {result}")
        assert_ok(result.get("metadata", {}).get("installed_version") == "1.2.3", "install must verify package metadata")
        assert_ok(result.get("metadata", {}).get("target_dir") == str(target_dir), "explicit isolated target must be retained")

    print(json.dumps({"ok": True, "checks": 6}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
