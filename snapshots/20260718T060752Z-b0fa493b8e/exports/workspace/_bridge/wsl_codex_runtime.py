#!/usr/bin/env python3
"""Materialize the isolated, Linux-facing Codex runtime for Codex-Wsl-Lab.

The work Git owns templates and active capability files.  The WSL home owns
credentials, sessions, databases, and other runtime state.  This command is
idempotent and never imports Windows auth or session databases.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CODEX_HOME = Path(os.environ.get("WSL_CODEX_HOME", str(Path.home() / ".codex"))).expanduser().resolve()
TEMPLATE = ROOT / "codex-home" / "config.wsl.template.toml"
NODE_WRAPPER = ROOT / "workspace" / "_bridge" / "codex_node_repl_wsl.sh"
RUNTIME_ROOT = ROOT / "workspace" / "_bridge" / "runtime" / "wsl_codex"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def link_or_verify(source: Path, target: Path) -> dict[str, object]:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        return {"path": str(target), "source": str(source), "status": "linked", "target": os.readlink(target)}
    if target.exists():
        return {"path": str(target), "source": str(source), "status": "preexisting_unmodified"}
    target.symlink_to(source, target_is_directory=source.is_dir())
    return {"path": str(target), "source": str(source), "status": "linked", "target": os.readlink(target)}


def link_skill_tree(source: Path, target: Path, *, write: bool) -> dict[str, object]:
    """Link user skills individually so Codex system skills stay runtime-local."""
    if not write:
        return {"path": str(target), "source": str(source), "status": "would_link_children"}
    if target.is_symlink():
        if target.resolve() == source.resolve():
            generated = source / ".system"
            staged = target.parent / ".system-migration"
            if generated.exists() and not staged.exists():
                shutil.copytree(generated, staged)
            target.unlink()
            target.mkdir(parents=True, exist_ok=True)
            if staged.exists() and not (target / ".system").exists():
                shutil.move(str(staged), str(target / ".system"))
            if generated.exists():
                shutil.rmtree(generated)
        else:
            return {"path": str(target), "source": str(source), "status": "conflicting_symlink"}
    target.mkdir(parents=True, exist_ok=True)
    linked = 0
    for child in sorted(source.iterdir()):
        if child.name == ".system":
            continue
        destination = target / child.name
        if destination.exists() or destination.is_symlink():
            continue
        destination.symlink_to(child, target_is_directory=child.is_dir())
        linked += 1
    return {"path": str(target), "source": str(source), "status": "linked_children", "linked_count": linked}


def render_config() -> str:
    if not TEMPLATE.is_file():
        raise FileNotFoundError(TEMPLATE)
    if not NODE_WRAPPER.is_file():
        raise FileNotFoundError(NODE_WRAPPER)
    replacements = {
        "__WSL_WORKSPACE_ROOT__": str(ROOT),
        "__WSL_CODEX_HOME__": str(CODEX_HOME),
        "__WSL_NODE_REPL_WRAPPER__": str(NODE_WRAPPER),
    }
    rendered = TEMPLATE.read_text(encoding="utf-8")
    for key, value in replacements.items():
        rendered = rendered.replace(key, value)
    if "<SECRET:" in rendered or "C:\\Users\\" in rendered:
        raise ValueError("WSL config contains a secret placeholder or Windows-only path")
    return rendered.rstrip() + "\n"


def materialize(*, write: bool) -> dict[str, object]:
    CODEX_HOME.mkdir(parents=True, exist_ok=True)
    (CODEX_HOME / "sqlite").mkdir(parents=True, exist_ok=True)
    config = CODEX_HOME / "config.toml"
    rendered = render_config()
    current = config.read_text(encoding="utf-8") if config.is_file() else ""
    changed = current != rendered
    links = []
    for name in ("AGENTS.md", "MEMORY.md", "USER_WORKING_PREFERENCES.md", "skills", "scripts", "tools", "automations"):
        source = ROOT / "codex-home" / name
        target = CODEX_HOME / name
        if source.exists():
            if name == "skills":
                links.append(link_skill_tree(source, target, write=write))
            else:
                links.append(link_or_verify(source, target) if write else {"path": str(target), "source": str(source), "status": "would_link"})
    if write and changed:
        config.write_text(rendered, encoding="utf-8", newline="\n")
    return {
        "schema": "codex-wsl-runtime.v1",
        "ok": True,
        "generated_at": now_iso(),
        "write": write,
        "changed": changed,
        "root": str(ROOT),
        "codex_home": str(CODEX_HOME),
        "config": str(config),
        "config_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        "links": links,
        "secrets_imported": False,
        "windows_runtime_imported": False,
        "session_state_imported": False,
    }


def validate() -> dict[str, object]:
    result = materialize(write=False)
    config = CODEX_HOME / "config.toml"
    result["config_exists"] = config.is_file()
    result["config_matches_template"] = bool(config.is_file() and sha256(config) == result["config_sha256"])
    result["node_wrapper_exists"] = NODE_WRAPPER.is_file()
    result["node_repl_exists"] = Path("/mnt/c/Users/45543/.local/bin/node_repl.exe").is_file()
    result["required"] = [
        "config_exists",
        "config_matches_template",
        "node_wrapper_exists",
        "node_repl_exists",
    ]
    result["ok"] = all(bool(result[key]) for key in result["required"])
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize the WSL Codex runtime projection")
    parser.add_argument("command", choices=("plan", "apply", "validate"))
    args = parser.parse_args(argv)
    if args.command == "plan":
        payload = materialize(write=False)
    elif args.command == "apply":
        payload = materialize(write=True)
    else:
        payload = validate()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
