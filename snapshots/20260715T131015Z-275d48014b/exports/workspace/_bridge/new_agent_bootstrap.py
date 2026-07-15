#!/usr/bin/env python3
"""Read-only context pack generator for a new Codex/agent session.

The tool summarizes current project rules, checkpoints, MCP configuration, and
health signals without modifying local state. It is intentionally conservative:
facts that can drift are reported as "verify live" rather than treated as
permanent truth.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "_bridge"
SHARED = BRIDGE / "shared"
MANIFEST = SHARED / "checkpoints" / "MANIFEST.md"
CODEX_CONFIG = Path.home() / ".codex" / "config.toml"
PROJECT_CONFIG = ROOT / ".codex" / "config.toml"
AGENTS = ROOT / "AGENTS.md"
MAX_SNIPPET_CHARS = 1200
TOOL_VERSION = "1.1.0"
BOOTSTRAP_DIR = SHARED / "bootstrap"


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    command: list[str]
    returncode: int | None
    stdout: str
    stderr: str
    error: str = ""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_text(path: Path, limit: int | None = None) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if limit is not None and len(text) > limit:
        return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"
    return text


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def run_command(args: list[str], timeout: int = 30) -> CommandResult:
    try:
        proc = subprocess.run(
            args,
            cwd=str(ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
        )
    except Exception as exc:
        return CommandResult(False, args, None, "", "", repr(exc))
    return CommandResult(proc.returncode == 0, args, proc.returncode, proc.stdout.strip(), proc.stderr.strip())


def load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def config_summary() -> dict[str, Any]:
    global_config = load_toml(CODEX_CONFIG)
    project_config = load_toml(PROJECT_CONFIG)
    mcp_servers = sorted((global_config.get("mcp_servers") or {}).keys())
    return {
        "global_config": str(CODEX_CONFIG),
        "global_config_exists": CODEX_CONFIG.exists(),
        "project_config": str(PROJECT_CONFIG),
        "project_config_exists": PROJECT_CONFIG.exists(),
        "model": global_config.get("model"),
        "model_provider": global_config.get("model_provider"),
        "reasoning_effort": global_config.get("model_reasoning_effort"),
        "windows_sandbox": (global_config.get("windows") or {}).get("sandbox"),
        "mcp_servers": mcp_servers,
        "project_config_keys": sorted(project_config.keys()),
    }


def parse_mcp_list_names(output: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("Name ") or stripped.startswith("---"):
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue
        name = parts[0]
        if name in {"Name", "-"}:
            continue
        status = "unknown"
        for token in reversed(parts):
            lowered = token.lower()
            if lowered in {"enabled", "disabled", "running", "failed"}:
                status = lowered
                break
        rows.append({"name": name, "status": status})
    return rows


def codex_mcp_list() -> dict[str, Any]:
    result = run_command(["codex", "mcp", "list"], timeout=30)
    output = "\n".join(part for part in [result.stdout, result.stderr] if part)
    return {
        "ok": result.ok,
        "returncode": result.returncode,
        "servers": parse_mcp_list_names(output),
        "output": output[:4000],
        "error": result.error,
    }


def startup_audit() -> dict[str, Any]:
    script = BRIDGE / "codex_state_audit.py"
    if not script.exists():
        return {"ok": False, "error": "codex_state_audit.py not found"}
    result = run_command([sys.executable, str(script)], timeout=60)
    payload: dict[str, Any] = {
        "ok": result.ok,
        "returncode": result.returncode,
        "error": result.error,
        "note": "Read-only live audit signal. Failures mean current Codex state differs from saved baseline; do not treat bootstrap generation itself as failed.",
    }
    try:
        data = json.loads(result.stdout)
        checks = data.get("checks", [])
        payload.update(
            {
                "audit_ok": data.get("ok"),
                "failed_checks": [c for c in checks if not c.get("ok")],
                "check_count": len(checks),
            }
        )
    except Exception:
        payload["raw"] = (result.stdout or result.stderr)[:3000]
    return payload


def script_inventory() -> dict[str, Any]:
    script = BRIDGE / "script_inventory.py"
    if not script.exists():
        return {"ok": False, "error": "script_inventory.py not found"}
    result = run_command([sys.executable, str(script), "--json"], timeout=30)
    if not result.ok:
        return {"ok": False, "error": result.error or result.stderr or result.stdout[:1000]}
    try:
        data = json.loads(result.stdout)
    except Exception as exc:
        return {"ok": False, "error": f"invalid json: {exc}"}
    active = [item for item in data.get("items", []) if item.get("category") == "active"]
    return {
        "ok": True,
        "counts": data.get("counts", {}),
        "active": active[:20],
        "excluded_dirs": data.get("excluded_dirs", []),
    }


def checkpoint_manifest() -> dict[str, Any]:
    text = read_text(MANIFEST)
    return {
        "path": str(MANIFEST),
        "exists": MANIFEST.exists(),
        "text": text,
    }


def checkpoint_files(project_id: str | None, limit: int) -> list[dict[str, Any]]:
    root = SHARED / "checkpoints"
    if not root.exists():
        return []
    if project_id:
        files = sorted((root / project_id).glob("*.md"), reverse=True)
    else:
        files = sorted(root.glob("*/*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    result = []
    for path in files[:limit]:
        result.append(
            {
                "path": str(path.relative_to(SHARED)).replace("/", "\\"),
                "mtime": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                "snippet": read_text(path, MAX_SNIPPET_CHARS),
            }
        )
    return result


def keyword_hits(query: str, roots: Iterable[Path], limit: int = 12) -> list[dict[str, str]]:
    if not query:
        return []
    terms = [term.casefold() for term in query.split() if len(term) >= 2]
    if not terms:
        return []
    hits: list[dict[str, str]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.md")):
            if any(part.lower() in {"backups", "backup", "node_modules"} for part in path.parts):
                continue
            text = read_text(path)
            folded = text.casefold()
            score = sum(folded.count(term) for term in terms)
            if score <= 0:
                continue
            idx = min((folded.find(term) for term in terms if folded.find(term) >= 0), default=0)
            snippet = text[max(0, idx - 200) : idx + 700].replace("\r\n", "\n")
            hits.append({"path": str(path.relative_to(ROOT)).replace("/", "\\"), "score": str(score), "snippet": snippet})
    return sorted(hits, key=lambda item: int(item["score"]), reverse=True)[:limit]


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    depth = args.depth
    task = args.task or ""
    if args.task_file:
        task_file = Path(args.task_file)
        if not task_file.is_absolute():
            task_file = (ROOT / task_file).resolve()
        task = read_text(task_file).strip()
    payload: dict[str, Any] = {
        "ok": True,
        "tool": {
            "name": "new_agent_bootstrap",
            "version": TOOL_VERSION,
            "mode": "read-only unless --save is used",
            "schema": "bootstrap-pack-v1",
        },
        "generated_at": now_iso(),
        "workspace": str(ROOT),
        "project_id": args.project_id or "",
        "task": task,
        "depth": depth,
        "format": args.format,
        "data_sources": [
            "AGENTS.md",
            "Codex config.toml",
            "project .codex/config.toml",
            "checkpoints/MANIFEST.md",
            "recent project checkpoints",
            "codex mcp list (normal/deep)",
            "codex_state_audit.py (normal/deep)",
            "script_inventory.py (deep)",
        ],
        "evolution": {
            "stable_contract": [
                "default run is read-only",
                "compact output stays token-light",
                "live drift is reported as warning, not silently repaired",
                "project_id groups checkpoint retrieval",
            ],
            "extension_points": [
                "register as MCP tool bootstrap_context_pack",
                "attach PMB recall adapter",
                "attach indexed checkpoint query adapter",
                "add project-specific profiles",
                "add schema-versioned consumers for mobile bridge or Reasonix",
            ],
        },
        "rules": {
            "agents_md": str(AGENTS),
            "agents_md_exists": AGENTS.exists(),
            "must_check_bridge_on_turn_start": True,
            "must_ask_before_file_edits": True,
            "must_backup_before_file_edits": True,
            "fact_first": "Verify drift-prone facts such as ports, permissions, processes, services, and MCP tools live before acting.",
        },
        "config": config_summary(),
        "checkpoint_manifest": checkpoint_manifest(),
        "recent_checkpoints": checkpoint_files(args.project_id, args.checkpoint_limit),
    }
    if depth in {"normal", "deep"}:
        payload["codex_mcp_list"] = codex_mcp_list()
        payload["startup_audit"] = startup_audit()
        payload["keyword_hits"] = keyword_hits(task or args.project_id or "", [SHARED], args.hit_limit)
    if depth == "deep":
        payload["script_inventory"] = script_inventory()
        payload["agents_md_snippet"] = read_text(AGENTS, 3000)
    payload["risk_sections"] = classify_risks(payload)
    return payload


def classify_risks(payload: dict[str, Any]) -> dict[str, Any]:
    stable = [
        "AGENTS.md project rules",
        "checkpoint manifest path and recorded project baselines",
        "new_agent_bootstrap schema/version",
    ]
    live = [
        "codex mcp list",
        "codex_state_audit.py results",
        "ports, processes, services, permissions, MCP tools",
    ]
    warnings: list[str] = []
    audit = payload.get("startup_audit") or {}
    if audit and not audit.get("audit_ok", audit.get("ok", True)):
        warnings.append("Codex startup audit reports drift from saved baseline; verify live state before repair.")
    mcp = payload.get("codex_mcp_list") or {}
    if mcp and not mcp.get("ok", True):
        warnings.append("codex mcp list failed; tool availability must be verified before relying on MCP.")
    if payload.get("task") and any(ord(ch) > 127 for ch in payload["task"]):
        warnings.append("Non-ASCII task text may display as mojibake in Windows PowerShell; use --task-file for exact text.")
    return {
        "stable": stable,
        "live_verify": live,
        "warnings": warnings,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    compact = payload.get("format") == "compact"
    lines = [
        "# New Agent Bootstrap Pack",
        "",
        f"- tool: `{payload['tool']['name']}` v`{payload['tool']['version']}`",
        f"- generated_at: {payload['generated_at']}",
        f"- workspace: `{payload['workspace']}`",
        f"- project_id: `{payload.get('project_id') or 'unspecified'}`",
        f"- task: {payload.get('task') or 'unspecified'}",
        f"- depth: `{payload['depth']}`",
        f"- format: `{payload['format']}`",
        "",
        "## Required First Moves",
        "- Read `AGENTS.md` before modifying files.",
        "- Check `agent_bridge_receive(agent=\"codex\")` and `knowledge_get(key=\"reasonix-notify\")` at turn start.",
        "- Default memory quick-pass: PMB prepare/recall -> indexed checkpoints or rollout summary only when the answer still needs evidence.",
        "- If a memory layer is skipped, say why briefly; do not silently skip it.",
        "- Query memory/checkpoints on demand; do not load all history by default.",
        "- Verify live state for ports, services, permissions, processes, and MCP tool lists.",
        "- Before file edits: ask the user, create a marked backup, then edit.",
        "",
        "## Config Summary",
    ]
    config = payload["config"]
    lines.extend(
        [
            f"- global_config_exists: `{config['global_config_exists']}`",
            f"- project_config_exists: `{config['project_config_exists']}`",
            f"- model: `{config.get('model')}`",
            f"- model_provider: `{config.get('model_provider')}`",
            f"- reasoning_effort: `{config.get('reasoning_effort')}`",
            f"- windows_sandbox: `{config.get('windows_sandbox')}`",
            f"- mcp_servers: {', '.join(config.get('mcp_servers') or [])}",
            "",
            "## Checkpoint Manifest",
        ]
    )
    manifest = payload["checkpoint_manifest"]
    if manifest["exists"]:
        lines.append(f"- path: `{manifest['path']}`")
        if compact:
            for line in manifest["text"].splitlines():
                if line.startswith("| ") and "`checkpoints/" in line:
                    lines.append(f"- {line}")
        else:
            lines.append("")
            lines.append("```markdown")
            lines.append(manifest["text"][:3500])
            lines.append("```")
    else:
        lines.append("- missing")
    recent = payload.get("recent_checkpoints") or []
    lines.extend(["", "## Recent Project Checkpoints"])
    if recent:
        for item in recent:
            lines.append(f"- `{item['path']}` ({item['mtime']})")
    else:
        lines.append("- none")
    if "codex_mcp_list" in payload:
        mcp = payload["codex_mcp_list"]
        lines.extend(["", "## Live MCP List"])
        lines.append(f"- ok: `{mcp.get('ok')}` returncode: `{mcp.get('returncode')}`")
        if compact and mcp.get("servers"):
            server_summary = ", ".join(f"{server['name']}:{server['status']}" for server in mcp["servers"])
            lines.append(f"- servers: {server_summary}")
        elif mcp.get("output"):
            lines.append("```text")
            lines.append(mcp["output"][:3000])
            lines.append("```")
    if "startup_audit" in payload:
        audit = payload["startup_audit"]
        lines.extend(["", "## Startup Audit"])
        lines.append(f"- ok: `{audit.get('ok')}` audit_ok: `{audit.get('audit_ok')}` checks: `{audit.get('check_count')}`")
        failures = audit.get("failed_checks") or []
        if failures:
            lines.append("- failed_checks:")
            for item in failures[:10]:
                lines.append(f"  - {item.get('name')}: {item.get('detail')}")
    if "keyword_hits" in payload:
        hits = payload["keyword_hits"]
        lines.extend(["", "## Task-Relevant Indexed Checkpoint Hits"])
        if hits:
            for hit in hits:
                lines.append(f"- `{hit['path']}` score={hit['score']}")
        else:
            lines.append("- none")
    if "script_inventory" in payload:
        inv = payload["script_inventory"]
        lines.extend(["", "## Script Inventory"])
        lines.append(f"- ok: `{inv.get('ok')}` counts: `{inv.get('counts')}`")
        for item in inv.get("active", [])[:12]:
            lines.append(f"- `{item['path']}` - {item['role']}")
    risks = payload.get("risk_sections") or {}
    lines.extend(["", "## Risk Sections"])
    for label, values in (
        ("stable", risks.get("stable", [])),
        ("live_verify", risks.get("live_verify", [])),
        ("warnings", risks.get("warnings", [])),
    ):
        lines.append(f"- {label}:")
        if values:
            for value in values:
                lines.append(f"  - {value}")
        else:
            lines.append("  - none")
    lines.extend(["", "## Evolution"])
    for key, values in payload.get("evolution", {}).items():
        lines.append(f"- {key}:")
        for value in values:
            lines.append(f"  - {value}")
    lines.extend(
        [
            "",
        "## Hand-Off Notes",
        "- Use `checkpoints/MANIFEST.md` as the long-evidence index.",
        "- Use PMB for reusable facts and indexed checkpoints for long-form evidence; treat migration stores as read-only historical sources.",
        "- Persist new memory only through the current memory owner and its approval/governance flow.",
        "- For new sessions, prefer the bootstrap memory quick-pass before fresh reasoning so reused facts enter context early.",
        "- Treat old memories as hints, not truth, when current evidence disagrees.",
    ]
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a read-only new-agent bootstrap context pack")
    parser.add_argument("--project-id", default="", help="Engineering project id, e.g. memory-system or mobile-openclaw-bridge")
    parser.add_argument("--task", default="", help="Current task or keywords for indexed memory/checkpoint selection")
    parser.add_argument("--task-file", default="", help="UTF-8 file containing task text; avoids PowerShell argument encoding issues")
    parser.add_argument("--depth", choices=["quick", "normal", "deep"], default="normal")
    parser.add_argument("--format", choices=["compact", "full"], default="compact", help="Markdown verbosity. JSON always contains full structured fields.")
    parser.add_argument("--checkpoint-limit", type=int, default=6)
    parser.add_argument("--hit-limit", type=int, default=8)
    parser.add_argument("--json", action="store_true", help="Emit structured JSON instead of Markdown")
    parser.add_argument("--save", action="store_true", help="Save output to _bridge/shared/bootstrap/latest.* and a timestamped copy")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = build_payload(args)
    if args.json:
        output = json.dumps(payload, ensure_ascii=False, indent=2)
        ext = "json"
    else:
        output = render_markdown(payload)
        ext = "md"
    if args.save:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        latest = BOOTSTRAP_DIR / f"latest.{ext}"
        stamped = BOOTSTRAP_DIR / f"{timestamp}-{payload.get('project_id') or 'workspace'}-bootstrap.{ext}"
        write_text(latest, output)
        write_text(stamped, output)
        if not args.json:
            output += f"\n\nSaved:\n- {latest}\n- {stamped}\n"
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
