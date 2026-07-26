"""Non-blocking Codex admission and tool-fact observer.

Ownership: workflow governance owns hook admission hints, bounded tool facts,
and closeout conformance observations.
Non-goals: intercept tools, deny actions, execute owners, persist prompts, or
store complete tool inputs/outputs.
State behavior: append-only event files under _bridge/runtime; corrupt or
missing state degrades to an empty observation and never blocks Codex.
Caller context: Codex UserPromptSubmit/PostToolUse/Stop hooks and
codex_workflow_entry closeout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from task_route_contract import derive_task_facts


SCHEMA = "codex_rule_observer.event.v1"
FACTS_SCHEMA = "codex_rule_observer.closeout_facts.v1"
ROOT = Path(__file__).resolve().parent
DEFAULT_RUNTIME_ROOT = ROOT / "runtime" / "codex_rule_observer"
ALLOWED_EVENTS = {"UserPromptSubmit", "PostToolUse", "Stop"}
MAX_EVENT_FILES = 600
MAX_TURN_EVENT_FILES = 256
MAX_REFS = 24
RESOURCE_ID_RE = re.compile(r"\bres_[0-9a-f]{8,64}\b", re.IGNORECASE)
WEB_REF_RE = re.compile(r"\bturn\d+(?:search|fetch|view|news|forecast|finance|sports|time)\d+\b", re.IGNORECASE)
PATH_KEYS = {"path", "file", "filepath", "file_path", "workdir", "target_dir", "artifact_path", "manifest_path"}
RESULT_KEYS = {
    "ok", "status", "request_id", "result_kind", "resource_layer_terminal",
    "end_to_end_terminal", "consumed", "source_tool", "next_action",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: Any, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "")).strip("-.")
    return (text or fallback)[:160]


def _event_root(runtime_root: Path | None = None) -> Path:
    root = Path(runtime_root or os.environ.get("CODEX_RULE_OBSERVER_ROOT") or DEFAULT_RUNTIME_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _session_id(payload: dict[str, Any] | None = None) -> str:
    payload = payload or {}
    return _safe_id(
        payload.get("session_id")
        or payload.get("conversation_id")
        or os.environ.get("CODEX_THREAD_ID"),
        "unknown-session",
    )


def _turn_id(payload: dict[str, Any] | None = None) -> str:
    payload = payload or {}
    return _safe_id(payload.get("turn_id") or payload.get("turnId"), "unknown-turn")


def _walk_known_values(value: Any, *, keys: set[str], depth: int = 0) -> Iterable[str]:
    if depth > 4:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in keys and isinstance(item, (str, int, float, bool)):
                yield str(item)
            elif isinstance(item, (dict, list, tuple)):
                yield from _walk_known_values(item, keys=keys, depth=depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value[:50]:
            yield from _walk_known_values(item, keys=keys, depth=depth + 1)


def _bounded_serialized(value: Any, limit: int = 131072) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return ""
    return text[:limit]


def stable_refs(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Extract stable identifiers without retaining full hook inputs or outputs."""

    refs: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, value: Any) -> None:
        text = " ".join(str(value or "").split())[:500]
        key = (kind, text)
        if not text or key in seen or len(refs) >= MAX_REFS:
            return
        seen.add(key)
        refs.append({"kind": kind, "value": text})

    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), (dict, list)) else {}
    tool_response = payload.get("tool_response") if isinstance(payload.get("tool_response"), (dict, list)) else {}
    for path in _walk_known_values(tool_input, keys=PATH_KEYS):
        add("path", path)
    serialized = _bounded_serialized({"input": tool_input, "response": tool_response})
    for value in RESOURCE_ID_RE.findall(serialized):
        add("resource_request_id", value)
    for value in WEB_REF_RE.findall(serialized):
        add("web_reference", value)
    tool_name = str(payload.get("tool_name") or "")
    if tool_name:
        add("tool", tool_name)
    return refs


def result_summary(payload: dict[str, Any]) -> dict[str, Any]:
    response = payload.get("tool_response")
    if not isinstance(response, dict):
        return {"observed": bool(response is not None)}
    values: dict[str, Any] = {}
    for key in RESULT_KEYS:
        value = response.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            if value not in (None, ""):
                values[key] = value[:300] if isinstance(value, str) else value
    nested = response.get("receipt")
    if isinstance(nested, dict):
        for key in RESULT_KEYS:
            value = nested.get(key)
            if key not in values and isinstance(value, (str, int, float, bool)) and value not in (None, ""):
                values[key] = value[:300] if isinstance(value, str) else value
    return values


def tool_category(payload: dict[str, Any]) -> str:
    name = str(payload.get("tool_name") or "").lower()
    command_text = " ".join(_walk_known_values(payload.get("tool_input") or {}, keys={"command", "tool", "path"})).lower()
    if name.startswith("web.") or name in {"web", "web.run", "search_query", "open", "image_query"}:
        return "generic_web"
    if "resource_request" in name or "resource_search" in name or "resource_request" in command_text or "resource_cli" in command_text:
        return "resource_layer"
    if name.startswith("mcp__") or "mcp" in name:
        return "owner_mcp"
    if "backup_router" in command_text:
        return "backup"
    if any(token in command_text for token in (" validate", "unittest", "pytest", "py_compile", "doctor")):
        return "validation"
    if name in {"apply_patch", "write_file", "edit_file"} or any(
        token in command_text
        for token in ("apply_patch", "set-content", "add-content", "remove-item", "move-item", "copy-item")
    ):
        return "local_write"
    if "shell" in name or "command" in name:
        return "shell"
    return "other"


def build_tool_event(payload: dict[str, Any]) -> dict[str, Any]:
    category = tool_category(payload)
    call_identity = str(payload.get("tool_use_id") or payload.get("tool_call_id") or os.urandom(8).hex())
    identity_seed = "|".join(
        [
            _session_id(payload),
            _turn_id(payload),
            call_identity,
            str(payload.get("tool_name") or ""),
        ]
    )
    return {
        "schema": SCHEMA,
        "event": "PostToolUse",
        "recorded_at": now_iso(),
        "session_id": _session_id(payload),
        "turn_id": _turn_id(payload),
        "event_id": hashlib.sha256(identity_seed.encode("utf-8")).hexdigest()[:24],
        "tool_name": str(payload.get("tool_name") or "")[:200],
        "category": category,
        "stable_refs": stable_refs(payload),
        "result": result_summary(payload),
        "stores_full_input": False,
        "stores_full_output": False,
    }


def write_event(event: dict[str, Any], runtime_root: Path | None = None) -> Path:
    root = _event_root(runtime_root)
    session = _safe_id(event.get("session_id"), "unknown-session")
    turn = _safe_id(event.get("turn_id"), "unknown-turn")
    event_id = _safe_id(event.get("event_id"), hashlib.sha256(os.urandom(16)).hexdigest()[:24])
    target_dir = root / session / turn
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{event_id}.json"
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, target)
    try:
        files = sorted(target_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        for stale in files[MAX_TURN_EVENT_FILES:]:
            stale.unlink(missing_ok=True)
    except OSError:
        pass
    return target


def read_events(
    *,
    session_id: str | None = None,
    turn_id: str | None = None,
    runtime_root: Path | None = None,
    max_age_hours: int = 24,
) -> list[dict[str, Any]]:
    root = _event_root(runtime_root)
    session = _safe_id(session_id or os.environ.get("CODEX_THREAD_ID"), "unknown-session")
    base = root / session
    if turn_id:
        base = base / _safe_id(turn_id, "unknown-turn")
    if not base.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, max_age_hours))
    rows: list[dict[str, Any]] = []
    files = sorted(base.glob("**/*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:MAX_EVENT_FILES]
    for path in files:
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            recorded = datetime.fromisoformat(str(item.get("recorded_at") or "").replace("Z", "+00:00"))
            if recorded.tzinfo is None:
                recorded = recorded.replace(tzinfo=timezone.utc)
            if recorded < cutoff:
                continue
            if item.get("schema") == SCHEMA:
                rows.append(item)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    rows.sort(key=lambda item: str(item.get("recorded_at") or ""))
    return rows


def latest_turn_id(*, session_id: str | None = None, runtime_root: Path | None = None) -> str:
    root = _event_root(runtime_root)
    session = _safe_id(session_id or os.environ.get("CODEX_THREAD_ID"), "unknown-session")
    base = root / session
    if not base.exists():
        return ""
    candidates = [item for item in base.iterdir() if item.is_dir()]
    if not candidates:
        return ""
    return max(candidates, key=lambda item: item.stat().st_mtime).name


def closeout_facts(
    *,
    session_id: str | None = None,
    turn_id: str | None = None,
    runtime_root: Path | None = None,
    max_age_hours: int = 24,
) -> dict[str, Any]:
    selected_turn = turn_id or latest_turn_id(session_id=session_id, runtime_root=runtime_root)
    events = read_events(
        session_id=session_id,
        turn_id=selected_turn or None,
        runtime_root=runtime_root,
        max_age_hours=max_age_hours,
    )
    categories = [str(item.get("category") or "") for item in events if item.get("category")]
    tools = sorted({str(item.get("tool_name") or "") for item in events if item.get("tool_name")})
    resource_ids: list[str] = []
    statuses: list[str] = []
    for item in events:
        for ref in item.get("stable_refs", []):
            if isinstance(ref, dict) and ref.get("kind") == "resource_request_id":
                resource_ids.append(str(ref.get("value") or ""))
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        if result.get("request_id"):
            resource_ids.append(str(result["request_id"]))
        if result.get("status"):
            statuses.append(str(result["status"]))
    violations: list[dict[str, str]] = []
    if "generic_web" in categories and "resource_layer" not in categories:
        violations.append({"code": "generic_web_without_observed_resource_layer", "severity": "warning"})
    if "local_write" in categories and "backup" not in categories:
        violations.append({"code": "local_write_without_observed_backup", "severity": "warning"})
    if "local_write" in categories and "validation" not in categories:
        violations.append({"code": "local_write_without_observed_validation", "severity": "warning"})
    return {
        "schema": FACTS_SCHEMA,
        "ok": not violations,
        "session_id": _safe_id(session_id or os.environ.get("CODEX_THREAD_ID"), "unknown-session"),
        "turn_id": selected_turn,
        "event_count": len(events),
        "categories": sorted(set(categories)),
        "tools": tools,
        "web_search_used": "generic_web" in categories,
        "resource_layer_used": "resource_layer" in categories,
        "owner_mcp_used": [tool for tool in tools if tool.lower().startswith("mcp__")],
        "resource_request_id": next((item for item in reversed(resource_ids) if item), ""),
        "resource_status": next((item for item in reversed(statuses) if item), ""),
        "violations": violations,
        "blocking": False,
        "rule": "observations supplement explicit closeout facts; they never intercept tools or inherit authorization",
    }


def admission_output(payload: dict[str, Any]) -> dict[str, Any]:
    prompt = str(payload.get("prompt") or "")
    facts, provenance = derive_task_facts(prompt, [])
    true_facts = [key for key, value in facts.items() if value]
    observed = [
        key
        for key in ("external_knowledge_candidate", "external_network_read", "local_write", "config_change", "system_member_change")
        if facts.get(key)
    ]
    if not observed:
        return {}
    signals = {key: provenance.get(key, {}).get("matched", []) for key in observed}
    context = (
        "[Non-blocking workflow admission] Observed task facts: "
        + ", ".join(observed)
        + ". Treat external_knowledge_candidate as a soft hint only; use the configured resource layer first when external lookup is actually needed. "
        + "This hook does not authorize, deny, or execute any action."
    )
    event = {
        "schema": SCHEMA,
        "event": "UserPromptSubmit",
        "recorded_at": now_iso(),
        "session_id": _session_id(payload),
        "turn_id": _turn_id(payload),
        "event_id": hashlib.sha256(("admission|" + _session_id(payload) + "|" + _turn_id(payload)).encode("utf-8")).hexdigest()[:24],
        "task_facts": true_facts,
        "signals": signals,
        "stores_prompt": False,
    }
    try:
        write_event(event)
    except Exception:
        pass
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context}}


def post_tool_output(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        write_event(build_tool_event(payload))
    except Exception:
        pass
    return {}


def stop_output(payload: dict[str, Any]) -> dict[str, Any]:
    facts = closeout_facts(session_id=_session_id(payload), turn_id=_turn_id(payload))
    if not facts.get("violations"):
        return {}
    codes = ", ".join(item.get("code", "") for item in facts["violations"] if isinstance(item, dict))
    return {
        "systemMessage": (
            "Non-blocking governance observation: " + codes
            + ". Review these at closeout if relevant. No tool was denied and completion is not blocked by this hook."
        )
    }


def handle(event: str, payload: dict[str, Any]) -> dict[str, Any]:
    if event == "UserPromptSubmit":
        return admission_output(payload)
    if event == "PostToolUse":
        return post_tool_output(payload)
    if event == "Stop":
        return stop_output(payload)
    return {}


def validate() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    implicit = admission_output({"session_id": "validate", "turn_id": "1", "prompt": "推荐当前可用的 USB 诊断工具"})
    checks.append({"name": "implicit_external_hint", "ok": "additionalContext" in implicit.get("hookSpecificOutput", {})})
    sample = build_tool_event(
        {
            "session_id": "validate",
            "turn_id": "1",
            "tool_use_id": "tool-1",
            "tool_name": "web.run",
            "tool_input": {"query": "secret content", "path": "C:/safe/reference.md"},
            "tool_response": {"ok": True, "content": "large private output", "request_id": "res_0123456789abcdef"},
        }
    )
    serialized = json.dumps(sample, ensure_ascii=False)
    checks.append({"name": "no_full_tool_payload", "ok": "secret content" not in serialized and "large private output" not in serialized})
    checks.append({"name": "stable_refs_only", "ok": any(item.get("kind") == "resource_request_id" for item in sample.get("stable_refs", []))})
    checks.append({"name": "no_pretooluse", "ok": "PreToolUse" not in ALLOWED_EVENTS})
    hooks_path = Path.home() / ".codex" / "hooks.json"
    try:
        configured = json.loads(hooks_path.read_text(encoding="utf-8"))
        configured_events = set((configured.get("hooks") or {}).keys())
    except (OSError, json.JSONDecodeError, TypeError):
        configured_events = set()
    checks.append(
        {
            "name": "configured_hooks_are_observational_only",
            "ok": configured_events == ALLOWED_EVENTS and "PreToolUse" not in configured_events,
            "detail": sorted(configured_events),
        }
    )
    return {"schema": "codex_rule_observer.validate.v1", "ok": all(item["ok"] for item in checks), "checks": checks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Non-blocking Codex rule observer")
    sub = parser.add_subparsers(dest="command", required=True)
    hook = sub.add_parser("hook")
    hook.add_argument("event", choices=sorted(ALLOWED_EVENTS))
    sub.add_parser("facts")
    sub.add_parser("validate")
    args = parser.parse_args(argv)
    try:
        if args.command == "hook":
            try:
                payload = json.load(sys.stdin)
            except Exception:
                payload = {}
            output = handle(args.event, payload if isinstance(payload, dict) else {})
        elif args.command == "facts":
            output = closeout_facts()
        else:
            output = validate()
    except Exception as exc:
        output = {"ok": True, "observer_error": type(exc).__name__, "blocking": False}
    sys.stdout.write(json.dumps(output, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
