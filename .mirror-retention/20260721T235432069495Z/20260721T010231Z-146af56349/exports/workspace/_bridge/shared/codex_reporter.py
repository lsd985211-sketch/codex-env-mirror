#!/usr/bin/env python3
"""Generate maintenance analysis reports through Codex.

The caller supplies facts and evidence. This helper writes an evidence bundle,
asks Codex to produce a human-readable report, and stores the final Markdown in
the resource library. It never mutates the subsystem being analyzed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .codex_executable import discover_codex_executable
    from .incident_index import metrics as incident_index_metrics
    from .incident_index import rebuild as rebuild_incident_index
except ImportError:
    from codex_executable import discover_codex_executable
    from incident_index import metrics as incident_index_metrics
    from incident_index import rebuild as rebuild_incident_index


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESOURCE_ROOT = Path(r"C:\Users\45543\Desktop\Codex资源库")
MAINTENANCE_ROOT = RESOURCE_ROOT / "文档" / "系统维护"
REPORT_ROOT = MAINTENANCE_ROOT / "异常报告"
EVIDENCE_ROOT = MAINTENANCE_ROOT / "证据包"
REQUEST_ROOT = MAINTENANCE_ROOT / "报告请求"
RECORD_ROOT = MAINTENANCE_ROOT / "执行记录"
RAW_ROOT = MAINTENANCE_ROOT / "原始载荷" / "codex-reporter"
RECORD_STORE_DB = MAINTENANCE_ROOT / "索引" / "record_store.sqlite"
LOCK_PATH = MAINTENANCE_ROOT / "运行态" / "codex-reporter.lock"
STARTUP_BASELINE_PATH = PROJECT_ROOT / "_bridge" / "codex_startup_baseline.json"
CODEX_REPORTER_MCP_PROFILE = "maintenance_report"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_slug(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in text)[:80].strip("-") or "report"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def stable_digest(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def compact_value(value: Any, limit: int = 300) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def evidence_summary(evidence: Any) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        return {"type": type(evidence).__name__, "preview": compact_value(evidence)}
    keys = sorted(str(key) for key in evidence.keys())[:30]
    return {
        "type": "dict",
        "top_level_keys": keys,
        "item_count": len(evidence),
        "schema": compact_value(evidence.get("schema", ""), 120),
        "ok": evidence.get("ok") if isinstance(evidence.get("ok"), bool) else None,
        "summary": compact_value(evidence.get("summary") or evidence.get("title") or evidence.get("message") or "", 500),
    }


def evidence_issue_codes(value: Any, *, limit: int = 100) -> list[str]:
    codes: set[str] = set()

    def visit(item: Any) -> None:
        if len(codes) >= limit:
            return
        if isinstance(item, dict):
            code = item.get("code")
            if isinstance(code, str) and code.strip():
                codes.add(code.strip())
            for key, child in item.items():
                if key in {"generated_at", "timestamp", "updated_at", "created_at", "pid"}:
                    continue
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return sorted(codes)


def report_semantic_digest(kind: str, title: str, policy: str, evidence: dict[str, Any]) -> str:
    summary = evidence_summary(evidence)
    semantic = {
        "kind": kind,
        "title": title,
        "policy": policy,
        "schema": summary.get("schema"),
        "ok": summary.get("ok"),
        "summary": summary.get("summary"),
        "issue_codes": evidence_issue_codes(evidence),
        "status": evidence.get("status"),
        "severity": evidence.get("severity"),
        "reason": evidence.get("reason"),
    }
    return stable_digest(semantic)


def write_raw_payload(kind: str, request_id: str, evidence: dict[str, Any]) -> dict[str, Any]:
    digest = stable_digest(evidence)
    month = datetime.now().strftime("%Y-%m")
    path = RAW_ROOT / safe_slug(kind) / month / f"sha256-{digest}.json"
    payload = {
        "schema": "codex-maintenance-report-raw-evidence.v1",
        "created_at": now_iso(),
        "first_request_id": request_id,
        "kind": kind,
        "sha256": digest,
        "evidence": evidence,
    }
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        write_json(temporary, payload)
        try:
            if path.exists():
                temporary.unlink(missing_ok=True)
            else:
                temporary.replace(path)
        except OSError:
            if path.exists():
                temporary.unlink(missing_ok=True)
            else:
                raise
    return {
        "schema": "record-store.raw_ref.v1",
        "created_at": now_iso(),
        "request_id": request_id,
        "kind": kind,
        "raw_path": str(path),
        "sha256": digest,
        "content_addressed": True,
        "summary": evidence_summary(evidence),
        "rollback": "The compact record can be rehydrated from raw_path.evidence.",
    }


def resolve_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    evidence = payload.get("evidence")
    if isinstance(evidence, dict) and evidence:
        return evidence
    raw_ref = payload.get("evidence_raw_ref") if isinstance(payload.get("evidence_raw_ref"), dict) else {}
    raw_path = Path(str(raw_ref.get("raw_path") or ""))
    if raw_path.exists():
        raw_payload = read_json(raw_path)
        raw_evidence = raw_payload.get("evidence")
        if isinstance(raw_evidence, dict):
            return raw_evidence
    return {}


def iter_request_paths() -> list[Path]:
    if not REQUEST_ROOT.exists():
        return []
    return sorted(REQUEST_ROOT.glob("*.json"), key=lambda p: p.stat().st_mtime)


def load_request(path: Path) -> dict[str, Any]:
    try:
        payload = read_json(path)
        payload["_path"] = str(path)
        payload["_mtime"] = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
        return payload
    except Exception as exc:
        return {
            "_path": str(path),
            "_mtime": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat() if path.exists() else "",
            "status": "invalid",
            "error": f"{type(exc).__name__}: {exc}",
        }


def queue_snapshot() -> dict[str, Any]:
    requests = [load_request(path) for path in iter_request_paths()]
    counts: dict[str, int] = {}
    for item in requests:
        status = str(item.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    queued = [item for item in requests if item.get("status") in {"queued", "retry", "running"}]
    return {
        "schema": "codex-maintenance-reporter/snapshot/v1",
        "ok": True,
        "generated_at": now_iso(),
        "request_root": str(REQUEST_ROOT),
        "report_root": str(REPORT_ROOT),
        "evidence_root": str(EVIDENCE_ROOT),
        "record_root": str(RECORD_ROOT),
        "raw_root": str(RAW_ROOT),
        "counts": counts,
        "total": len(requests),
        "active_or_waiting": len(queued),
        "oldest_active_or_waiting": min((str(item.get("created_at") or item.get("_mtime") or "") for item in queued), default=""),
        "newest_requests": requests[-10:],
    }


def queue_doctor() -> dict[str, Any]:
    snap = queue_snapshot()
    counts = snap.get("counts") if isinstance(snap.get("counts"), dict) else {}
    issues: list[dict[str, Any]] = []
    if int(counts.get("invalid") or 0) > 0:
        issues.append({"code": "report_request_invalid_json", "severity": "high", "summary": "报告请求队列里存在无法解析的 JSON。"})
    if int(counts.get("failed") or 0) > 0:
        issues.append({"code": "report_request_failed", "severity": "medium", "summary": "存在失败的 Codex 维护报告请求，需要查看失败原因。"})
    if int(counts.get("queued") or 0) + int(counts.get("retry") or 0) > 20:
        issues.append({"code": "report_request_backlog_high", "severity": "medium", "summary": "维护报告请求积压偏高。"})
    return {
        "schema": "codex-maintenance-reporter/doctor/v1",
        "ok": not any(item.get("severity") == "high" for item in issues),
        "generated_at": now_iso(),
        "issues": issues,
        "snapshot": snap,
    }


def queue_metrics() -> dict[str, Any]:
    snap = queue_snapshot()
    counts = snap.get("counts") if isinstance(snap.get("counts"), dict) else {}
    incident_summary = incident_metrics(kind="codex_main_process")
    return {
        "schema": "codex-maintenance-reporter/metrics/v1",
        "ok": True,
        "generated_at": now_iso(),
        "queued": int(counts.get("queued") or 0),
        "retry": int(counts.get("retry") or 0),
        "running": int(counts.get("running") or 0),
        "done": int(counts.get("done") or 0),
        "failed": int(counts.get("failed") or 0),
        "invalid": int(counts.get("invalid") or 0),
        "total": int(snap.get("total") or 0),
        "codex_main_process_incidents": incident_summary,
    }


def queue_repair_plan() -> dict[str, Any]:
    doc = queue_doctor()
    actions: list[dict[str, Any]] = []
    for issue in doc.get("issues", []) if isinstance(doc.get("issues"), list) else []:
        code = str(issue.get("code") or "")
        if code == "report_request_failed":
            actions.append({"code": "inspect_failed_report_requests", "apply": False, "reason": "失败请求不自动重试，避免重复生成或错误覆盖"})
        elif code == "report_request_invalid_json":
            actions.append({"code": "quarantine_invalid_report_requests", "apply": False, "reason": "需要人工确认后隔离坏文件"})
        elif code == "report_request_backlog_high":
            actions.append({"code": "run_report_worker_batch", "apply": False, "command": "python _bridge\\shared\\codex_reporter.py worker --max-jobs 2"})
    return {
        "schema": "codex-maintenance-reporter/repair-plan/v1",
        "ok": True,
        "generated_at": now_iso(),
        "dry_run": True,
        "actions": actions,
        "doctor": doc,
    }


def queue_validate() -> dict[str, Any]:
    snap = queue_snapshot()
    counts = snap.get("counts") if isinstance(snap.get("counts"), dict) else {}
    return {
        "schema": "codex-maintenance-reporter/validate/v1",
        "ok": int(counts.get("invalid") or 0) == 0,
        "generated_at": now_iso(),
        "request_root_exists": REQUEST_ROOT.exists(),
        "invalid_count": int(counts.get("invalid") or 0),
        "request_count": int(snap.get("total") or 0),
    }


def incident_rebuild(*, apply: bool) -> dict[str, Any]:
    if not RECORD_STORE_DB.exists():
        return {"schema": "incident-index.rebuild.v1", "ok": False, "reason": "record_store_index_missing", "db_path": str(RECORD_STORE_DB)}
    conn = sqlite3.connect(str(RECORD_STORE_DB), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        return rebuild_incident_index(conn, apply=apply)
    finally:
        conn.close()


def incident_metrics(*, kind: str = "") -> dict[str, Any]:
    if not RECORD_STORE_DB.exists():
        return {"schema": "incident-index.metrics.v1", "ok": False, "reason": "record_store_index_missing", "db_path": str(RECORD_STORE_DB)}
    conn = sqlite3.connect(str(RECORD_STORE_DB), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        return incident_index_metrics(conn, kind=kind)
    finally:
        conn.close()


class SingleInstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: Any | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, "a+", encoding="utf-8")
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            return False
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(json.dumps({"pid": os.getpid(), "started_at": now_iso()}, ensure_ascii=False))
        self.handle.flush()
        return True

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


def build_prompt(title: str, kind: str, evidence: dict[str, Any], evidence_path: Path, output_path: Path, policy: str) -> str:
    return f"""
你是本机系统维护报告分析员。请只依据下面嵌入的证据包，生成一份中文 Markdown 异常分析报告。

报告标题：{title}
异常类型：{kind}
证据包路径：{evidence_path}
输出报告路径：{output_path}
处理边界：{policy}

要求：
1. 先判断这是不是需要关注的真实异常；如果只是历史噪声或无需处理，也要明确说明。
2. 给出根因判断、证据、影响范围、建议动作。
3. 对微信桥接队列异常和邮件发送异常：只能分析并写报告，不能修改队列、不能重发邮件、不能改任务状态。
4. 不要编造证据；所有结论必须能从证据包或本机只读检查推导。
5. 只输出 Markdown 报告正文；不要写文件，不要调用外部工具，不要输出 REPORT_WRITTEN。

嵌入证据包：
{json.dumps(evidence, ensure_ascii=False, indent=2)}
""".strip()


def run_codex_report(prompt: str, timeout_seconds: int) -> tuple[bool, str]:
    codex_exe = discover_codex_executable(startup_baseline=STARTUP_BASELINE_PATH)
    if not codex_exe:
        return False, "codex.exe not found"
    tmp = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", suffix=".txt")
    tmp_path = Path(tmp.name)
    tmp.close()
    try:
        cmd = [
            codex_exe,
            "exec",
            "-C",
            str(PROJECT_ROOT),
            "--output-last-message",
            str(tmp_path),
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "-",
        ]
        env = os.environ.copy()
        env["CODEX_BACKGROUND_JOB"] = "codex_reporter"
        env["CODEX_MCP_PROFILE"] = CODEX_REPORTER_MCP_PROFILE
        proc = subprocess.run(
            cmd,
            input=prompt.encode("utf-8"),
            text=False,
            capture_output=True,
            cwd=str(PROJECT_ROOT),
            timeout=timeout_seconds,
            env=env,
        )
        last_message = tmp_path.read_text(encoding="utf-8-sig").strip() if tmp_path.exists() else ""
        if proc.returncode != 0 and not last_message:
            stderr = (proc.stderr or b"").decode("utf-8", errors="replace")
            return False, stderr.strip()[:2000]
        return True, last_message
    except subprocess.TimeoutExpired:
        return False, "codex report generation timed out"
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def write_fallback_report(output_path: Path, title: str, kind: str, evidence_path: Path, error: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# {title}",
        "",
        f"- generated_at: {now_iso()}",
        f"- kind: {kind}",
        "- report_generation: fallback",
        f"- codex_error: {error}",
        f"- evidence_bundle: {evidence_path}",
        "",
        "Codex 报告生成未完成。本文件只保留证据包索引，不执行任何自动修复。",
        "",
    ]
    output_path.write_text("\n".join(lines), encoding="utf-8")


def generate_report(
    *,
    kind: str,
    title: str,
    evidence: dict[str, Any],
    policy: str,
    timeout_seconds: int = 900,
) -> dict[str, Any]:
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = safe_slug(kind)
    evidence_path = EVIDENCE_ROOT / f"{stamp}-{slug}.json"
    output_path = REPORT_ROOT / f"{stamp}-{slug}-codex-report.md"
    evidence_ref = write_raw_payload(kind, f"{stamp}-{slug}", evidence)
    bundle = {
        "schema": "codex-maintenance-report-evidence.v1",
        "generated_at": now_iso(),
        "kind": kind,
        "title": title,
        "policy": policy,
        "evidence_summary": evidence_ref.get("summary", {}),
        "evidence_raw_ref": evidence_ref,
        "contract": {
            "subsystem_mutation_allowed": False,
            "report_writer": "codex",
            "output_path": str(output_path),
        },
    }
    write_json(evidence_path, bundle)
    prompt_bundle = dict(bundle)
    prompt_bundle["evidence"] = evidence
    prompt = build_prompt(title, kind, prompt_bundle, evidence_path, output_path, policy)
    ok, message = run_codex_report(prompt, timeout_seconds=timeout_seconds)
    if ok and message.strip():
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(message.strip() + "\n", encoding="utf-8")
    else:
        write_fallback_report(output_path, title, kind, evidence_path, message)
        ok = False
    return {
        "ok": bool(ok),
        "kind": kind,
        "report_path": str(output_path),
        "evidence_path": str(evidence_path),
        "codex_message": message[-1000:],
    }


def enqueue_report(
    *,
    kind: str,
    title: str,
    evidence: dict[str, Any],
    policy: str,
    priority: int = 50,
) -> dict[str, Any]:
    REQUEST_ROOT.mkdir(parents=True, exist_ok=True)
    day = datetime.now().strftime("%Y%m%d")
    semantic_digest = report_semantic_digest(kind, title, policy, evidence)
    request_id = f"{day}-{safe_slug(kind)}-{semantic_digest[:12]}"
    path = REQUEST_ROOT / f"{request_id}.json"
    if path.exists():
        existing = read_json(path)
        return {
            "ok": True,
            "queued": str(existing.get("status") or "") in {"queued", "retry", "running"},
            "deduplicated": True,
            "request_id": request_id,
            "request_path": str(path),
            "status": existing.get("status"),
        }
    evidence_ref = write_raw_payload(kind, request_id, evidence)
    payload = {
        "schema": "codex-maintenance-report-request.v1",
        "request_id": request_id,
        "created_at": now_iso(),
        "status": "queued",
        "priority": int(priority),
        "kind": kind,
        "title": title,
        "policy": policy,
        "evidence_summary": evidence_ref.get("summary", {}),
        "evidence_raw_ref": evidence_ref,
        "semantic_digest": semantic_digest,
        "attempt_count": 0,
    }
    write_json(path, payload)
    return {"ok": True, "queued": True, "request_id": request_id, "request_path": str(path)}


def worker(max_jobs: int = 2, timeout_seconds: int = 900) -> dict[str, Any]:
    lock = SingleInstanceLock(LOCK_PATH)
    if not lock.acquire():
        return {"ok": True, "skipped": True, "reason": "reporter_already_running"}
    results: list[dict[str, Any]] = []
    try:
        REQUEST_ROOT.mkdir(parents=True, exist_ok=True)
        paths = sorted(
            REQUEST_ROOT.glob("*.json"),
            key=lambda p: (int(read_json(p).get("priority") or 50), p.stat().st_mtime),
        )
        for path in paths:
            if len(results) >= max_jobs:
                break
            payload = read_json(path)
            if payload.get("status") not in {"queued", "retry"}:
                continue
            payload["status"] = "running"
            payload["started_at"] = now_iso()
            payload["attempt_count"] = int(payload.get("attempt_count") or 0) + 1
            write_json(path, payload)
            try:
                result = generate_report(
                    kind=str(payload.get("kind") or "maintenance"),
                    title=str(payload.get("title") or "系统维护报告"),
                    evidence=resolve_evidence(payload),
                    policy=str(payload.get("policy") or "report_only"),
                    timeout_seconds=timeout_seconds,
                )
                payload["status"] = "done" if result.get("ok") else "failed"
                payload["finished_at"] = now_iso()
                payload["result"] = result
                write_json(path, payload)
                results.append({"request_id": payload.get("request_id"), **result})
            except Exception as exc:
                payload["status"] = "failed"
                payload["finished_at"] = now_iso()
                payload["error"] = f"{type(exc).__name__}: {exc}"
                write_json(path, payload)
                results.append({"request_id": payload.get("request_id"), "ok": False, "error": payload["error"]})
    finally:
        lock.release()
    record = {
        "schema": "codex-maintenance-reporter-worker.v1",
        "ok": not any(item.get("ok") is False for item in results),
        "generated_at": now_iso(),
        "results": results,
    }
    if results:
        RECORD_ROOT.mkdir(parents=True, exist_ok=True)
        record_path = RECORD_ROOT / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-codex-reporter-worker.json"
        write_json(record_path, record)
        record["record_path"] = str(record_path)
    else:
        record["record_suppressed"] = True
        record["record_suppression_reason"] = "empty_worker_poll_is_heartbeat_not_execution_evidence"
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a maintenance report through Codex")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("snapshot")
    sub.add_parser("doctor")
    sub.add_parser("repair-plan")
    sub.add_parser("validate")
    sub.add_parser("metrics")
    p_generate = sub.add_parser("generate")
    p_generate.add_argument("--kind", required=True)
    p_generate.add_argument("--title", required=True)
    p_generate.add_argument("--evidence-json", required=True)
    p_generate.add_argument("--policy", default="report_only")
    p_generate.add_argument("--timeout-seconds", type=int, default=900)
    p_enqueue = sub.add_parser("enqueue")
    p_enqueue.add_argument("--kind", required=True)
    p_enqueue.add_argument("--title", required=True)
    p_enqueue.add_argument("--evidence-json", required=True)
    p_enqueue.add_argument("--policy", default="report_only")
    p_enqueue.add_argument("--priority", type=int, default=50)
    p_worker = sub.add_parser("worker")
    p_worker.add_argument("--max-jobs", type=int, default=2)
    p_worker.add_argument("--timeout-seconds", type=int, default=900)
    p_incident_rebuild = sub.add_parser("incident-rebuild")
    p_incident_rebuild.add_argument("--apply", action="store_true")
    p_incident_metrics = sub.add_parser("incident-metrics")
    p_incident_metrics.add_argument("--kind", default="")
    args = parser.parse_args(argv)
    if args.command == "snapshot":
        payload = queue_snapshot()
    elif args.command == "doctor":
        payload = queue_doctor()
    elif args.command == "repair-plan":
        payload = queue_repair_plan()
    elif args.command == "validate":
        payload = queue_validate()
    elif args.command == "metrics":
        payload = queue_metrics()
    elif args.command == "worker":
        payload = worker(max_jobs=args.max_jobs, timeout_seconds=args.timeout_seconds)
    elif args.command == "incident-rebuild":
        payload = incident_rebuild(apply=bool(args.apply))
    elif args.command == "incident-metrics":
        payload = incident_metrics(kind=args.kind)
    else:
        evidence_path = Path(args.evidence_json)
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        if args.command == "enqueue":
            payload = enqueue_report(kind=args.kind, title=args.title, evidence=evidence, policy=args.policy, priority=args.priority)
        else:
            payload = generate_report(
                kind=args.kind,
                title=args.title,
                evidence=evidence,
                policy=args.policy,
                timeout_seconds=args.timeout_seconds,
            )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") is not False else 1


if __name__ == "__main__":
    raise SystemExit(main())
