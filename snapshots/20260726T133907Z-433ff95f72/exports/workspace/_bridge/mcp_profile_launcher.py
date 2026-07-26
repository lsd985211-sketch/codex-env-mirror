#!/usr/bin/env python3
"""Console-free-friendly MCP profile launcher.

Codex starts stdio MCP servers directly.  On Windows, using .cmd wrappers adds
an extra cmd.exe/conhost.exe layer and can flash visible console windows.  This
launcher keeps the existing mcp_launch_guard behavior while replacing those
batch wrappers with one Python entry point.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from mcp_profile_launcher_process import run_profile_process
from platform_paths import memory_root  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "_bridge"
PYTHON = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "python" / "python.exe"
SYSTEM_PYTHON = Path("C:/Python314/python.exe")
NODE = Path("C:/Program Files/nodejs/node.exe")
NODE_DIR = NODE.parent
NPX_CLI = Path("C:/Program Files/nodejs/node_modules/npm/bin/npx-cli.js")
GUARD = BRIDGE / "mcp_launch_guard.py"
LAZY_PROXY = BRIDGE / "mcp_lazy_stdio_proxy.py"
LAZY_CACHE_DIR = BRIDGE / "runtime" / "mcp_lazy_stdio_proxy"
LAZY_PROFILES = {"cdev", "drawio", "gui", "pw"}
NO_WINDOW_KW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
MCP_NODE_MODULE_ROOT = BRIDGE / "tools" / "mcp-node"
FILESYSTEM_SERVER_JS = MCP_NODE_MODULE_ROOT / "node_modules" / "@modelcontextprotocol" / "server-filesystem" / "dist" / "index.js"
NEXT_AI_DRAWIO_SERVER_JS = BRIDGE / "runtime_dependencies" / "next-ai-drawio-mcp" / "node_modules" / "@next-ai-drawio" / "mcp-server" / "dist" / "index.js"


def q(path: Path) -> str:
    return str(path)


def codex_library_root() -> Path:
    short_root = Path.home() / "Desktop" / "CODEX~1"
    return short_root if short_root.exists() else Path.home() / "Desktop" / "Codex资源库"


def prefer_system_node(env: dict[str, str]) -> dict[str, str]:
    """Keep Node-based MCPs off older project-local Node binaries in PATH."""
    current = env.get("PATH") or os.environ.get("PATH") or ""
    node_dir = str(NODE_DIR)
    parts = [item for item in current.split(os.pathsep) if item]
    filtered = [item for item in parts if os.path.normcase(item) != os.path.normcase(node_dir)]
    return {**env, "PATH": os.pathsep.join([node_dir, *filtered])}


def network_env_for_target(*, target_kind: str, target: str, runtime: str) -> dict[str, str]:
    """Resolve a governed per-process network environment without global mutation."""

    try:
        proc = subprocess.run(
            [
                q(PYTHON),
                q(BRIDGE / "codex_network_gateway.py"),
                "env",
                "--target-kind",
                target_kind,
                "--target",
                target,
                "--runtime",
                runtime,
            ],
            cwd=q(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            **NO_WINDOW_KW,
        )
        payload = json.loads(proc.stdout or "{}")
        values = payload.get("env") if proc.returncode == 0 and isinstance(payload, dict) else {}
        return {str(key): str(value) for key, value in values.items()} if isinstance(values, dict) else {}
    except Exception:
        return {}


def filesystem_server_command(roots: list[str]) -> list[str]:
    if FILESYSTEM_SERVER_JS.exists():
        return [q(NODE), q(FILESYSTEM_SERVER_JS), *roots]
    return [q(NODE), q(NPX_CLI), "-y", "@modelcontextprotocol/server-filesystem@2026.1.14", *roots]


def guarded_profile_command(profile: str) -> tuple[dict[str, str], list[str]]:
    env: dict[str, str] = {}
    wrappers = BRIDGE / "tools" / "mcp-wrappers"
    if profile == "gui":
        env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
        return env, [q(PYTHON), q(GUARD), "--profile", "gui", "--min-age-minutes", "15", "--", "C:/Python314/python.exe", q(BRIDGE / "gui_automation_mcp.py")]
    if profile == "cdev":
        return prefer_system_node(env), [q(PYTHON), q(GUARD), "--profile", "cdev", "--min-age-minutes", "15", "--", q(NODE), q(NPX_CLI), "--registry", "https://registry.npmjs.org", "chrome-devtools-mcp@1.4.0"]
    if profile == "ctx7":
        return prefer_system_node(env), [q(PYTHON), q(GUARD), "--profile", "ctx7", "--min-age-minutes", "30", "--", q(NODE), q(wrappers / "context7_stdio_proxy.js")]
    if profile == "mid":
        return env, [q(PYTHON), q(GUARD), "--profile", "mid", "--min-age-minutes", "30", "--", "C:/Python314/python.exe", "-m", "uv", "tool", "run", "markitdown-mcp@0.0.1a4"]
    if profile == "msdocs":
        return prefer_system_node(env), [q(PYTHON), q(GUARD), "--profile", "msdocs", "--min-age-minutes", "30", "--", q(NODE), q(wrappers / "microsoftdocs_stdio_proxy.js")]
    if profile == "oadocs":
        env.update(network_env_for_target(target_kind="docs", target="https://developers.openai.com/mcp", runtime="node"))
        return prefer_system_node(env), [q(PYTHON), q(GUARD), "--profile", "oadocs", "--min-age-minutes", "30", "--", q(NODE), q(wrappers / "openai_docs_stdio_proxy.js")]
    if profile == "skills":
        return env, [q(PYTHON), q(GUARD), "--profile", "skills", "--min-age-minutes", "15", "--", str(Path.home() / "AppData" / "Local" / "MySkills" / "myskills-mcp.exe")]
    if profile == "pw":
        return prefer_system_node(env), [q(PYTHON), q(GUARD), "--profile", "pw", "--min-age-minutes", "30", "--", q(NODE), q(NPX_CLI), "--registry", "https://registry.npmjs.org", "@playwright/mcp@latest"]
    if profile == "drawio":
        if not NEXT_AI_DRAWIO_SERVER_JS.exists():
            raise SystemExit(f"Next AI Draw.io MCP package missing: {NEXT_AI_DRAWIO_SERVER_JS}")
        return prefer_system_node(env), [q(PYTHON), q(GUARD), "--profile", "drawio", "--min-age-minutes", "30", "--", q(NODE), q(NEXT_AI_DRAWIO_SERVER_JS)]
    if profile == "cg":
        env.update({"CODEGRAPH_NO_DAEMON": "1", "CODEGRAPH_WATCH_DEBOUNCE_MS": "2000"})
        return env, [q(PYTHON), q(GUARD), "--profile", "cg", "--min-age-minutes", "15", "--", q(PYTHON), q(BRIDGE / "codegraph_fresh_mcp_server.py")]
    if profile == "pmb":
        env.update({
            "PMB_HOME": str(memory_root() / "pmb" / "data"),
            "PMB_WORKSPACE": "mcsmanager",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        })
        ensure = subprocess.run(
            [q(PYTHON), q(BRIDGE / "local_pmb_memory.py"), "daemon-ensure"],
            cwd=q(ROOT),
            env={**os.environ, **env},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            **NO_WINDOW_KW,
        )
        if ensure.returncode != 0:
            if ensure.stdout:
                sys.stderr.write(ensure.stdout)
            if ensure.stderr:
                sys.stderr.write(ensure.stderr)
            sys.stderr.flush()
            raise SystemExit(ensure.returncode)
        pmb = (
            BRIDGE / "venvs" / "pmb-memory" / "Scripts" / "pmb.exe"
            if sys.platform == "win32"
            else Path.home() / ".local" / "share" / "codex-runtimes" / "pmb-memory" / "bin" / "pmb"
        )
        return env, [q(PYTHON), q(GUARD), "--profile", "pmb", "--min-age-minutes", "15", "--", q(pmb), "mcp", "proxy", "--no-autostart", "--no-fallback"]
    if profile == "fs":
        roots = [
            q(ROOT),
            str(Path.home() / "Desktop" / "Codex资源库"),
            str(Path.home() / ".codex" / "skills"),
            str(Path.home() / ".codex" / "memories"),
            str(Path.home() / ".codex" / "plugins"),
        ]
        return prefer_system_node(env), [q(PYTHON), q(GUARD), "--profile", "fs", "--min-age-minutes", "15", "--", *filesystem_server_command(roots)]
    if profile == "fs-admin":
        return prefer_system_node(env), [q(PYTHON), q(GUARD), "--profile", "fs-admin", "--min-age-minutes", "15", "--", *filesystem_server_command(["C:/"])]
    if profile == "weixin":
        env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
        python = SYSTEM_PYTHON if SYSTEM_PYTHON.exists() else PYTHON
        return env, [q(PYTHON), q(GUARD), "--profile", "weixin", "--min-age-minutes", "15", "--", q(python), q(BRIDGE / "desktop_weixin_mcp_server.py")]
    raise SystemExit(f"unknown profile: {profile}")


def profile_command(profile: str, *, lazy: bool = True, warm_cache: bool = False) -> tuple[dict[str, str], list[str]]:
    env, guarded_command = guarded_profile_command(profile)
    if warm_cache and profile not in LAZY_PROFILES:
        raise SystemExit(f"profile does not use lazy catalog caching: {profile}")
    if not lazy or profile not in LAZY_PROFILES:
        return env, guarded_command
    command = [
        q(PYTHON),
        q(LAZY_PROXY),
        "--profile",
        profile,
        "--cache-dir",
        q(LAZY_CACHE_DIR),
        "--child-cwd",
        q(ROOT),
    ]
    if warm_cache:
        command.append("--warm-cache")
    command.extend(["--", *guarded_command])
    return env, command


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("profile")
    parser.add_argument("--eager", action="store_true", help="Bypass the lazy proxy for protocol/runtime validation.")
    parser.add_argument("--warm-cache", action="store_true", help="Refresh the lazy proxy initialization and tools/list cache.")
    args, extra = parser.parse_known_args()
    if args.eager and args.warm_cache:
        parser.error("--eager and --warm-cache are mutually exclusive")
    extra_env, cmd = profile_command(args.profile, lazy=not args.eager, warm_cache=args.warm_cache)
    cmd.extend(extra)
    extra_env.setdefault("PYTHONUTF8", "1")
    extra_env.setdefault("PYTHONIOENCODING", "utf-8")
    return run_profile_process(
        cmd,
        extra_env=extra_env,
        cwd=ROOT,
        lazy_proxy=LAZY_PROXY,
    )


if __name__ == "__main__":
    raise SystemExit(main())
