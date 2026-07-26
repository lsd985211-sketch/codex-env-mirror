#!/usr/bin/env python3
"""Isolated MCP gateway candidate lab.

Ownership: local test environment for MetaMCP and lightweight MCP gateway
candidates.
Non-goals: no Codex config edits, no MCP registration, no system proxy/DNS
mutation, no Windows startup tasks, no secret migration, and no production
process control.
State behavior: writes only under `_bridge/runtime/gateway-lab` unless a caller
passes an explicit lab root.
Caller context: used by Codex to test gateway candidates before any runtime
integration proposal.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from network_policy import env_for_runtime
from shared.json_cli import configure_utf8_stdio, now_iso, print_json
from gateway_lab_metamcp import bootstrap_lab as metamcp_bootstrap_lab
from gateway_lab_metamcp import install_echo_server as metamcp_install_echo
from gateway_lab_metamcp import smoke as metamcp_smoke
from gateway_lab_metamcp import status as metamcp_status
from gateway_lab_context7 import install as context7_install
from gateway_lab_context7 import smoke as context7_smoke


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "_bridge"
DEFAULT_LAB_ROOT = BRIDGE / "runtime" / "gateway-lab"

CANDIDATES: dict[str, dict[str, str]] = {
    "metamcp": {
        "name": "MetaMCP",
        "repo": "https://github.com/metatool-ai/metamcp.git",
        "homepage": "https://github.com/metatool-ai/metamcp",
        "role": "primary_control_plane_candidate",
    },
    "acehoss-mcp-gateway": {
        "name": "acehoss/mcp-gateway",
        "repo": "https://github.com/acehoss/mcp-gateway.git",
        "homepage": "https://github.com/acehoss/mcp-gateway",
        "role": "lightweight_transport_bridge_comparison",
    },
}


def lab_root(value: str | None = None) -> Path:
    return Path(value).expanduser().resolve() if value else DEFAULT_LAB_ROOT


def tool_available(name: str) -> dict[str, Any]:
    path = shutil.which(name)
    if not path and name == "docker":
        user_docker = Path.home() / "AppData" / "Local" / "Programs" / "DockerDesktop" / "resources" / "bin" / "docker.exe"
        if user_docker.exists():
            path = str(user_docker)
    if not path:
        return {"available": False, "path": "", "version": "", "returncode": None}
    command = [path, "--version"]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
    except Exception as exc:  # noqa: BLE001 - diagnostic payload should remain bounded.
        return {"available": True, "path": path, "version": "", "returncode": None, "error": str(exc)}
    return {
        "available": completed.returncode == 0,
        "path": path,
        "version": (completed.stdout or completed.stderr).strip().splitlines()[0] if (completed.stdout or completed.stderr).strip() else "",
        "returncode": completed.returncode,
    }


def docker_daemon_status(docker_path: str) -> dict[str, Any]:
    if not docker_path:
        return {"available": False, "ok": False, "reason": "docker_cli_missing"}
    result = run_command([docker_path, "version", "--format", "{{json .Server}}"], timeout=20)
    return {
        "available": True,
        "ok": bool(result.get("ok")),
        "result": result,
    }


def windows_feature_state(feature_name: str) -> dict[str, Any]:
    result = run_command(
        ["dism.exe", "/English", "/online", "/Get-FeatureInfo", f"/FeatureName:{feature_name}"],
        timeout=30,
    )
    state = ""
    restart_required = ""
    text = f"{result.get('stdout', '')}\n{result.get('stderr', '')}"
    for line in text.splitlines():
        if line.startswith("State :"):
            state = line.split(":", 1)[1].strip()
        elif line.startswith("Restart Required :"):
            restart_required = line.split(":", 1)[1].strip()
    return {
        "ok": bool(result.get("ok")),
        "feature": feature_name,
        "state": state,
        "restart_required": restart_required,
    }


def run_command(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None, timeout: int = 60) -> dict[str, Any]:
    started = time.time()
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "elapsed_ms": int((time.time() - started) * 1000),
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
            "command": command,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "returncode": None,
            "elapsed_ms": int((time.time() - started) * 1000),
            "stdout": (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "")[-4000:] if isinstance(exc.stderr, str) else "",
            "error": "timeout",
            "command": command,
        }


def powershell_single_quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def run_limited_token_powershell(root: Path, *, label: str, script_body: str, timeout: int = 180) -> dict[str, Any]:
    task_id = f"CodexGatewayLab-{label}-{uuid.uuid4().hex[:8]}"
    tmp_dir = root / "tmp" / "limited-token"
    log_dir = root / "logs" / "limited-token"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    script_path = tmp_dir / f"{task_id}.ps1"
    output_path = log_dir / f"{task_id}.json"
    script_path.write_text(script_body.replace("__OUTPUT_PATH__", str(output_path)), encoding="utf-8")

    create_script = "\n".join(
        [
            f"$taskName = {powershell_single_quoted(task_id)}",
            f"$scriptPath = {powershell_single_quoted(str(script_path))}",
            "$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"{0}\"' -f $scriptPath)",
            "$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1)",
            "$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited",
            "Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Force | Out-Null",
            "Start-ScheduledTask -TaskName $taskName",
        ]
    )
    create_result = run_command(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", create_script], timeout=30)
    if not create_result.get("ok"):
        return {
            "ok": False,
            "error": "task_create_failed",
            "task_name": task_id,
            "script_path": str(script_path),
            "output_path": str(output_path),
            "create_result": create_result,
        }

    deadline = time.time() + timeout
    while time.time() < deadline:
        if output_path.exists():
            break
        time.sleep(2)

    cleanup_script = "\n".join(
        [
            f"$taskName = {powershell_single_quoted(task_id)}",
            "$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue",
            "$info = if ($task) { Get-ScheduledTaskInfo -TaskName $taskName } else { $null }",
            "if ($task) { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false }",
            "[pscustomobject]@{",
            "  taskName = $taskName",
            "  taskExisted = [bool]$task",
            "  lastRunTime = if ($info) { $info.LastRunTime.ToString('o') } else { '' }",
            "  lastTaskResult = if ($info) { $info.LastTaskResult } else { $null }",
            "} | ConvertTo-Json -Depth 4",
        ]
    )
    cleanup_result = run_command(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cleanup_script], timeout=30)
    task_output: dict[str, Any] | str = ""
    if output_path.exists():
        raw_output = output_path.read_text(encoding="utf-8-sig", errors="replace")
        try:
            task_output = json.loads(raw_output)
        except json.JSONDecodeError:
            task_output = raw_output[-4000:]
    return {
        "ok": output_path.exists() and isinstance(task_output, dict) and task_output.get("exitCode") == 0,
        "task_name": task_id,
        "script_path": str(script_path),
        "output_path": str(output_path),
        "timed_out": not output_path.exists(),
        "task_output": task_output,
        "cleanup_result": cleanup_result,
    }


def limited_docker_script(args: list[str], cwd: Path | None = None) -> str:
    ps_args = "@(" + ",".join(powershell_single_quoted(arg) for arg in args) + ")"
    cwd_line = f"Set-Location -LiteralPath {powershell_single_quoted(str(cwd))}" if cwd else ""
    return "\n".join(
        [
            "$id = [Security.Principal.WindowsIdentity]::GetCurrent()",
            "$principal = [Security.Principal.WindowsPrincipal]::new($id)",
            "$docker = Join-Path $env:LOCALAPPDATA 'Programs\\DockerDesktop\\resources\\bin\\docker.exe'",
            cwd_line,
            f"$dockerArgs = {ps_args}",
            "$result = & $docker @dockerArgs 2>&1",
            "[pscustomobject]@{",
            "  user = $id.Name",
            "  isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)",
            "  sessionId = (Get-Process -Id $PID).SessionId",
            "  command = @($docker) + $dockerArgs",
            "  cwd = (Get-Location).Path",
            "  exitCode = $LASTEXITCODE",
            "  output = ($result -join \"`n\")",
            "  generatedAt = (Get-Date).ToString('o')",
            "} | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 -LiteralPath '__OUTPUT_PATH__'",
        ]
    )


def docker_limited_pull(root: Path, image: str, *, timeout: int = 300) -> dict[str, Any]:
    result = run_limited_token_powershell(
        root,
        label="DockerPull",
        script_body=limited_docker_script(["pull", image]),
        timeout=timeout,
    )
    payload = {
        "schema": "gateway_lab.docker_limited_pull.v1",
        "ok": bool(result.get("ok")),
        "generated_at": now_iso(),
        "image": image,
        "runner": result,
        "boundary": "runs Docker CLI in hidden limited user token because elevated Codex token cannot access Windows credential vault",
    }
    write_json(root / "logs" / f"docker-limited-pull-{image.replace('/', '_').replace(':', '_')}.json", payload)
    return payload


def metamcp_compose(root: Path, action: str, *, timeout: int = 600) -> dict[str, Any]:
    repo = root / "repos" / "metamcp"
    if not repo.exists():
        return {"schema": "gateway_lab.metamcp_compose.v1", "ok": False, "error": "repo_not_fetched", "repo": str(repo)}
    action_args = {
        "pull": ["compose", "pull"],
        "up": ["compose", "up", "-d"],
        "ps": ["compose", "ps"],
        "logs": ["compose", "logs", "--tail", "120"],
        "down": ["compose", "down"],
    }
    if action not in action_args:
        return {"schema": "gateway_lab.metamcp_compose.v1", "ok": False, "error": "unsupported_action", "action": action}
    result = run_limited_token_powershell(
        root,
        label=f"MetaMCP{action.title()}",
        script_body=limited_docker_script(action_args[action], cwd=repo),
        timeout=timeout,
    )
    payload = {
        "schema": "gateway_lab.metamcp_compose.v1",
        "ok": bool(result.get("ok")),
        "generated_at": now_iso(),
        "action": action,
        "repo": str(repo),
        "runner": result,
        "boundary": "isolated MetaMCP lab compose action; no Codex MCP registration or startup integration",
    }
    write_json(root / "logs" / f"metamcp-compose-{action}.json", payload)
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json_file(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def docker_config_path() -> Path:
    return Path.home() / ".docker" / "config.json"


def docker_config_target_state(config: dict[str, Any]) -> dict[str, Any]:
    proposed = dict(config)
    proposed.pop("credsStore", None)
    proposed.pop("credHelpers", None)
    features = dict(proposed.get("features") or {})
    features["hooks"] = "false"
    proposed["features"] = features
    return proposed


def docker_config_diff(current: dict[str, Any], proposed: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for key in sorted(set(current) | set(proposed)):
        if current.get(key) != proposed.get(key):
            changes.append({"key": key, "from": current.get(key), "to": proposed.get(key)})
    return changes


def docker_config_plan(root: Path) -> dict[str, Any]:
    path = docker_config_path()
    current = read_json_file(path)
    proposed = docker_config_target_state(current)
    changes = docker_config_diff(current, proposed)
    return {
        "schema": "gateway_lab.docker_config_plan.v1",
        "ok": True,
        "generated_at": now_iso(),
        "config_path": str(path),
        "exists": path.exists(),
        "changes": changes,
        "would_write": bool(changes),
        "boundary": "temporary Docker Desktop lab mitigation; not Codex config, not system proxy, not startup task",
        "apply": "python _bridge\\gateway_lab.py docker-config-apply --confirm",
        "restore": "python _bridge\\gateway_lab.py docker-config-restore --backup <backup_path> --confirm",
    }


def docker_config_apply(root: Path, *, confirm: bool = False) -> dict[str, Any]:
    plan = docker_config_plan(root)
    if not confirm:
        plan["ok"] = False
        plan["error"] = "confirm_required"
        return plan
    path = docker_config_path()
    current = read_json_file(path)
    proposed = docker_config_target_state(current)
    changes = docker_config_diff(current, proposed)
    backup_dir = root / "logs" / "docker-config-backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = now_iso().replace(":", "").replace("-", "").replace(".", "")
    backup_path = backup_dir / f"config-{stamp}.json"
    if path.exists():
        shutil.copy2(path, backup_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(proposed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    payload = {
        "schema": "gateway_lab.docker_config_apply.v1",
        "ok": True,
        "generated_at": now_iso(),
        "config_path": str(path),
        "backup_path": str(backup_path),
        "changes": changes,
        "restore": f"python _bridge\\gateway_lab.py docker-config-restore --backup {backup_path} --confirm",
    }
    write_json(root / "logs" / "docker-config-last-apply.json", payload)
    return payload


def docker_config_restore(root: Path, *, backup: str, confirm: bool = False) -> dict[str, Any]:
    backup_path = Path(backup).expanduser().resolve()
    path = docker_config_path()
    if not confirm:
        return {
            "schema": "gateway_lab.docker_config_restore.v1",
            "ok": False,
            "error": "confirm_required",
            "config_path": str(path),
            "backup_path": str(backup_path),
            "backup_exists": backup_path.exists(),
        }
    if not backup_path.exists():
        return {
            "schema": "gateway_lab.docker_config_restore.v1",
            "ok": False,
            "error": "backup_missing",
            "backup_path": str(backup_path),
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup_path, path)
    payload = {
        "schema": "gateway_lab.docker_config_restore.v1",
        "ok": True,
        "generated_at": now_iso(),
        "config_path": str(path),
        "backup_path": str(backup_path),
    }
    write_json(root / "logs" / "docker-config-last-restore.json", payload)
    return payload


def url_json(url: str, *, method: str = "GET", body: dict[str, Any] | None = None, timeout: int = 10) -> dict[str, Any]:
    data = json.dumps(body or {}).encode("utf-8") if body is not None else None
    request = Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - localhost-only lab request.
            text = response.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = text
            return {"ok": True, "status": response.status, "body": parsed}
    except URLError as exc:
        return {"ok": False, "error": str(exc)}


def snapshot(root: Path) -> dict[str, Any]:
    tools = {name: tool_available(name) for name in ("git", "node", "npm", "pnpm", "docker", "wsl")}
    docker_status = docker_daemon_status(str(tools.get("docker", {}).get("path") or ""))
    windows_features = {
        "wsl": windows_feature_state("Microsoft-Windows-Subsystem-Linux"),
        "virtual_machine_platform": windows_feature_state("VirtualMachinePlatform"),
    }
    repos = {}
    for key in CANDIDATES:
        repo_dir = root / "repos" / key
        repos[key] = {
            "path": str(repo_dir),
            "exists": repo_dir.exists(),
            "git_dir_exists": (repo_dir / ".git").exists(),
        }
    return {
        "schema": "gateway_lab.snapshot.v1",
        "ok": True,
        "generated_at": now_iso(),
        "lab_root": str(root),
        "contracts": {
            "isolated_runtime_only": True,
            "codex_config_mutation": False,
            "system_proxy_dns_mutation": False,
            "startup_task_mutation": False,
            "secret_migration": False,
        },
        "tools": tools,
        "docker_daemon": docker_status,
        "windows_features": windows_features,
        "candidates": CANDIDATES,
        "repos": repos,
    }


def init(root: Path) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    for child in ("repos", "logs", "tmp"):
        (root / child).mkdir(exist_ok=True)
    payload = snapshot(root)
    write_json(root / "snapshot.json", payload)
    return {
        "schema": "gateway_lab.init.v1",
        "ok": True,
        "generated_at": now_iso(),
        "lab_root": str(root),
        "snapshot_path": str(root / "snapshot.json"),
        "next": [
            "python _bridge\\gateway_lab.py fetch --candidate metamcp",
            "python _bridge\\gateway_lab.py fetch --candidate acehoss-mcp-gateway",
            "python _bridge\\gateway_lab.py doctor",
        ],
    }


def git_env_for(url: str) -> dict[str, str]:
    payload = env_for_runtime(url, runtime="generic", context="gateway_lab")
    env = os.environ.copy()
    env.update({str(key): str(value) for key, value in payload.get("env", {}).items() if value})
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def fetch(root: Path, candidate: str, *, timeout: int = 180) -> dict[str, Any]:
    if candidate not in CANDIDATES:
        return {"schema": "gateway_lab.fetch.v1", "ok": False, "error": "unknown_candidate", "candidate": candidate}
    root.mkdir(parents=True, exist_ok=True)
    repo_url = CANDIDATES[candidate]["repo"]
    target = root / "repos" / candidate
    if (target / ".git").exists():
        result = run_command(["git", "-C", str(target), "pull", "--ff-only"], env=git_env_for(repo_url), timeout=timeout)
        action = "pull"
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        result = run_command(["git", "clone", "--depth", "1", repo_url, str(target)], env=git_env_for(repo_url), timeout=timeout)
        action = "clone"
    payload = {
        "schema": "gateway_lab.fetch.v1",
        "ok": bool(result.get("ok")),
        "generated_at": now_iso(),
        "candidate": candidate,
        "action": action,
        "repo": repo_url,
        "target": str(target),
        "result": result,
    }
    write_json(root / "logs" / f"fetch-{candidate}.json", payload)
    return payload


def package_summary(repo_dir: Path) -> dict[str, Any]:
    package_json = repo_dir / "package.json"
    compose_files = [path.name for path in repo_dir.glob("docker-compose*.y*ml")]
    docker_files = [str(path.relative_to(repo_dir)) for path in repo_dir.rglob("Dockerfile")][:20] if repo_dir.exists() else []
    payload: dict[str, Any] = {
        "package_json_exists": package_json.exists(),
        "compose_files": compose_files,
        "docker_files": docker_files,
    }
    if package_json.exists():
        try:
            package = json.loads(package_json.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            payload["package_error"] = str(exc)
        else:
            payload["package_name"] = package.get("name")
            payload["scripts"] = sorted((package.get("scripts") or {}).keys()) if isinstance(package.get("scripts"), dict) else []
            payload["package_manager"] = package.get("packageManager", "")
    return payload


def doctor(root: Path) -> dict[str, Any]:
    snap = snapshot(root)
    issues: list[dict[str, Any]] = []
    tools = snap["tools"]
    if not tools["git"]["available"]:
        issues.append({"severity": "blocker", "code": "git_missing", "message": "git is required to fetch gateway candidates"})
    if not tools["docker"]["available"]:
        issues.append({"severity": "risk", "code": "docker_missing", "message": "MetaMCP official quickstart requires Docker/Docker Compose"})
    elif not snap.get("docker_daemon", {}).get("ok"):
        issues.append({"severity": "risk", "code": "docker_daemon_unavailable", "message": "Docker CLI is installed, but Docker Desktop daemon is not reachable"})
    if snap.get("windows_features", {}).get("wsl", {}).get("state") != "Enabled":
        issues.append({"severity": "risk", "code": "wsl_feature_not_enabled", "message": "WSL Windows feature is not enabled"})
    if not tools["node"]["available"]:
        issues.append({"severity": "risk", "code": "node_missing", "message": "Node is needed for Node-based MCP gateway experiments"})
    if not tools["pnpm"]["available"] and not tools["npm"]["available"]:
        issues.append({"severity": "risk", "code": "node_package_manager_missing", "message": "npm or pnpm is needed for Node-based experiments"})

    summaries = {}
    for key in CANDIDATES:
        repo_dir = root / "repos" / key
        summaries[key] = package_summary(repo_dir)
        if not repo_dir.exists():
            issues.append({"severity": "advisory", "code": f"{key}_not_fetched", "message": f"candidate repo not fetched: {key}"})

    status = "blocked" if any(item["severity"] == "blocker" for item in issues) else ("needs_setup" if issues else "ready")
    payload = {
        "schema": "gateway_lab.doctor.v1",
        "ok": status != "blocked",
        "generated_at": now_iso(),
        "status": status,
        "lab_root": str(root),
        "issues": issues,
        "snapshot": snap,
        "repo_summaries": summaries,
        "docker_setup": {
            "approved_by_user": "current_task",
            "preferred_command": "winget install --id Docker.DockerDesktop -e --accept-source-agreements --accept-package-agreements",
            "notes": [
                "Docker Desktop may require WSL2, admin approval, sign-in choices, and reboot.",
                "Do not place Docker/MetaMCP on Codex startup path during lab.",
            ],
        },
    }
    write_json(root / "doctor.json", payload)
    return payload


def clean(root: Path, *, confirm: bool = False) -> dict[str, Any]:
    if not confirm:
        return {
            "schema": "gateway_lab.clean.v1",
            "ok": False,
            "error": "confirm_required",
            "would_remove": str(root),
            "rerun": "python _bridge\\gateway_lab.py clean --confirm",
        }
    if root.exists():
        shutil.rmtree(root)
    return {"schema": "gateway_lab.clean.v1", "ok": True, "removed": str(root), "generated_at": now_iso()}


def write_acehoss_echo_config(root: Path) -> Path:
    config = root / "acehoss-localhost-echo.yaml"
    python_path = shutil.which("python") or "python"
    echo_server = BRIDGE / "gateway_lab_echo_mcp.py"
    config.write_text(
        "\n".join(
            [
                'hostname: "127.0.0.1"',
                "port: 33000",
                "",
                "debug:",
                '  level: "warn"',
                "",
                "servers:",
                "  echo:",
                f'    command: "{python_path.replace("\\", "/")}"',
                "    args:",
                f'      - "{str(echo_server).replace("\\", "/")}"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return config


def smoke_acehoss(root: Path, *, timeout: int = 20) -> dict[str, Any]:
    repo = root / "repos" / "acehoss-mcp-gateway"
    if not repo.exists():
        return {"schema": "gateway_lab.smoke_acehoss.v1", "ok": False, "error": "repo_not_fetched", "repo": str(repo)}
    if not (repo / "node_modules").exists():
        return {
            "schema": "gateway_lab.smoke_acehoss.v1",
            "ok": False,
            "error": "dependencies_missing",
            "next": f"cd {repo} && npm install",
        }
    config = write_acehoss_echo_config(root)
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        return {"schema": "gateway_lab.smoke_acehoss.v1", "ok": False, "error": "npm_missing"}
    env = os.environ.copy()
    env["CONFIG_PATH"] = str(config)
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / "acehoss-smoke.out.log"
    stderr_path = log_dir / "acehoss-smoke.err.log"
    with stdout_path.open("w", encoding="utf-8") as stdout_file, stderr_path.open("w", encoding="utf-8") as stderr_file:
        process = subprocess.Popen(
            [npm, "start"],
            cwd=str(repo),
            env=env,
            stdout=stdout_file,
            stderr=stderr_file,
            text=True,
        )
    session: dict[str, Any] = {"ok": False, "error": "not_attempted"}
    call: dict[str, Any] = {"ok": False, "error": "not_attempted"}
    try:
        deadline = time.time() + timeout
        while time.time() < deadline:
            session = url_json("http://127.0.0.1:33000/api/sessionid", timeout=3)
            if session.get("ok"):
                break
            time.sleep(1)
        session_id = ""
        if isinstance(session.get("body"), dict):
            session_id = str(session["body"].get("sessionId") or "")
        if session_id:
            call = url_json(
                f"http://127.0.0.1:33000/api/echo/echo?sessionId={session_id}",
                method="POST",
                body={"text": "gateway-lab-ok"},
                timeout=5,
            )
    finally:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, text=True)
        else:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
    schema_result = run_command(
        [shutil.which("npx.cmd") or "npx", "tsx", "src/gateway.ts", "--schemaDump", "--schemaFormat", "json"],
        cwd=repo,
        env=env,
        timeout=20,
    )
    payload = {
        "schema": "gateway_lab.smoke_acehoss.v1",
        "ok": bool(call.get("ok") and isinstance(call.get("body"), dict) and "gateway-lab-ok" in json.dumps(call.get("body"))),
        "generated_at": now_iso(),
        "config": str(config),
        "repo": str(repo),
        "session": session,
        "call": call,
        "schema_dump_ok": bool(schema_result.get("ok")),
        "schema_dump_preview": str(schema_result.get("stdout") or schema_result.get("stderr") or "")[:1200],
        "logs": {"stdout": str(stdout_path), "stderr": str(stderr_path)},
        "security_note": "lab binds to 127.0.0.1 only; npm audit findings must be resolved before any durable integration",
    }
    write_json(root / "logs" / "acehoss-smoke.json", payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Isolated MCP gateway candidate lab")
    parser.add_argument("--root", default="", help="Optional lab root; defaults to _bridge/runtime/gateway-lab")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("snapshot")
    sub.add_parser("init")
    fetch_parser = sub.add_parser("fetch")
    fetch_parser.add_argument("--candidate", choices=sorted(CANDIDATES), required=True)
    fetch_parser.add_argument("--timeout", type=int, default=180)
    sub.add_parser("doctor")
    smoke_parser = sub.add_parser("smoke-acehoss")
    smoke_parser.add_argument("--timeout", type=int, default=20)
    sub.add_parser("docker-config-plan")
    docker_apply_parser = sub.add_parser("docker-config-apply")
    docker_apply_parser.add_argument("--confirm", action="store_true")
    docker_restore_parser = sub.add_parser("docker-config-restore")
    docker_restore_parser.add_argument("--backup", required=True)
    docker_restore_parser.add_argument("--confirm", action="store_true")
    docker_pull_parser = sub.add_parser("docker-limited-pull")
    docker_pull_parser.add_argument("--image", required=True)
    docker_pull_parser.add_argument("--timeout", type=int, default=300)
    metamcp_parser = sub.add_parser("metamcp-compose")
    metamcp_parser.add_argument("--action", choices=["pull", "up", "ps", "logs", "down"], required=True)
    metamcp_parser.add_argument("--timeout", type=int, default=600)
    sub.add_parser("metamcp-status")
    metamcp_bootstrap_parser = sub.add_parser("metamcp-bootstrap-lab")
    metamcp_bootstrap_parser.add_argument("--confirm", action="store_true")
    metamcp_echo_parser = sub.add_parser("metamcp-install-echo")
    metamcp_echo_parser.add_argument("--confirm", action="store_true")
    sub.add_parser("metamcp-smoke")
    context7_parser = sub.add_parser("metamcp-install-context7")
    context7_parser.add_argument("--confirm", action="store_true")
    sub.add_parser("metamcp-smoke-context7")
    clean_parser = sub.add_parser("clean")
    clean_parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)
    root = lab_root(args.root)
    if args.command == "snapshot":
        payload = snapshot(root)
    elif args.command == "init":
        payload = init(root)
    elif args.command == "fetch":
        payload = fetch(root, args.candidate, timeout=args.timeout)
    elif args.command == "doctor":
        payload = doctor(root)
    elif args.command == "smoke-acehoss":
        payload = smoke_acehoss(root, timeout=args.timeout)
    elif args.command == "docker-config-plan":
        payload = docker_config_plan(root)
    elif args.command == "docker-config-apply":
        payload = docker_config_apply(root, confirm=args.confirm)
    elif args.command == "docker-config-restore":
        payload = docker_config_restore(root, backup=args.backup, confirm=args.confirm)
    elif args.command == "docker-limited-pull":
        payload = docker_limited_pull(root, args.image, timeout=args.timeout)
    elif args.command == "metamcp-compose":
        payload = metamcp_compose(root, args.action, timeout=args.timeout)
    elif args.command == "metamcp-status":
        payload = metamcp_status()
    elif args.command == "metamcp-bootstrap-lab":
        payload = metamcp_bootstrap_lab(confirm=args.confirm)
    elif args.command == "metamcp-install-echo":
        payload = metamcp_install_echo(confirm=args.confirm)
    elif args.command == "metamcp-smoke":
        payload = metamcp_smoke()
    elif args.command == "metamcp-install-context7":
        payload = context7_install(confirm=args.confirm)
    elif args.command == "metamcp-smoke-context7":
        payload = context7_smoke()
    elif args.command == "clean":
        payload = clean(root, confirm=args.confirm)
    else:  # pragma: no cover
        parser.error(f"unsupported command: {args.command}")
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
