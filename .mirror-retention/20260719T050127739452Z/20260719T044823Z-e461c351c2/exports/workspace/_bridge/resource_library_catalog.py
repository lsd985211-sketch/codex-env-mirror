#!/usr/bin/env python3
"""Generate the human and machine directory for the desktop resource library.

Ownership: resource-library navigation metadata and owner pointers.
Non-goals: owning mail, scheduler, memory, backup, record, download, or website business state.
State behavior: read-only by default; build writes only the generated catalog and README after routed backup.
Caller context: Codex maintenance, resource discovery, and human workspace navigation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from shared.backup_router import create_backup
from shared.json_cli import configure_utf8_stdio, now_iso, print_json


ROOT = Path(r"C:\Users\45543\Desktop\Codex资源库")
README_PATH = ROOT / "README.md"
CATALOG_PATH = ROOT / "文档" / "系统架构" / "resource-library-catalog.json"
GENERATED_MARKER = "<!-- generated-by: resource_library_catalog.py -->"

MODULES: tuple[dict[str, str], ...] = (
    {"id": "mail", "name": "邮箱区", "path": "文档/邮箱区", "owner": "_bridge/shared/email_scheduler.py", "purpose": "身份、入站、回信、草稿、发件与发送记录", "authority": "邮件文件与 email_scheduler_state；SQLite 仅为派生查询面", "maintenance": "python _bridge\\shared\\email_scheduler.py validate"},
    {"id": "scheduler", "name": "定时模块", "path": "文档/定时模块", "owner": "_bridge/shared/codex_scheduler_runner.py", "purpose": "统一计划任务、到期执行、错过窗口补跑与幂等记录", "authority": "统一调度任务表与运行态", "maintenance": "python _bridge\\shared\\codex_scheduler_runner.py validate"},
    {"id": "memory", "name": "记忆", "path": "memory", "owner": "_bridge/memory_governance.py + PMB", "purpose": "可复用记忆、候选、审查与持久化治理", "authority": "记忆 owner/PMB；目录镜像不是独立权威源", "maintenance": "python _bridge\\memory_governance.py doctor"},
    {"id": "backups", "name": "备份", "path": "_backup", "owner": "_bridge/shared/backup_router.py", "purpose": "资源库统一备份集与清单", "authority": "manifest.json 与哈希", "maintenance": "python _bridge\\shared\\backup_router.py validate C:\\Users\\45543\\Desktop\\Codex资源库\\_backup"},
    {"id": "records", "name": "记录与审计", "path": "文档/系统维护", "owner": "_bridge/shared/record_store_maintenance.py", "purpose": "维护记录、证据、原始载荷、异常报告与 SQLite 索引", "authority": "原始记录文件；SQLite 为派生索引", "maintenance": "python _bridge\\shared\\record_store_maintenance.py doctor"},
    {"id": "resources", "name": "用户资源", "path": "图片", "owner": "_bridge/resource_cli.py + resource layer", "purpose": "图片、论文、音视频、表格、安装包与其他用户交付资源", "authority": "资源文件、请求 manifest 与 receipt", "maintenance": "python _bridge\\resource_cli.py status --help"},
    {"id": "websites", "name": "网站", "path": "网站", "owner": "whitepaper-pipeline / site publisher", "purpose": "白皮书与其他网页发布产物", "authority": "站点源文件、发布回执与托管平台状态", "maintenance": "按站点发布回执验证"},
)


def module_row(spec: dict[str, str]) -> dict[str, Any]:
    path = ROOT / Path(spec["path"])
    immediate_count = 0
    if path.exists() and path.is_dir():
        try:
            immediate_count = sum(1 for _ in path.iterdir())
        except OSError:
            immediate_count = 0
    return {**spec, "absolute_path": str(path), "exists": path.exists(), "immediate_entry_count": immediate_count}


def snapshot() -> dict[str, Any]:
    modules = [module_row(spec) for spec in MODULES]
    return {
        "schema": "resource_library_catalog.snapshot.v1",
        "ok": ROOT.exists(),
        "generated_at": now_iso(),
        "resource_root": str(ROOT),
        "catalog_path": str(CATALOG_PATH),
        "readme_path": str(README_PATH),
        "modules": modules,
        "summary": {"module_count": len(modules), "existing_count": sum(1 for item in modules if item["exists"]), "missing_count": sum(1 for item in modules if not item["exists"])},
    }


def render_readme(snap: dict[str, Any]) -> str:
    lines = [
        "# Codex资源库", "", GENERATED_MARKER, "",
        "桌面统一资源库的人类目录。业务状态由各 owner 管理；本页只提供导航、权威边界和维护入口。", "",
        "## 模块目录", "",
        "| 模块 | 路径 | 职责 | Owner | 权威状态 | 维护入口 |",
        "|---|---|---|---|---|---|",
    ]
    for item in snap["modules"]:
        status = "存在" if item["exists"] else "缺失"
        lines.append(f"| {item['name']} ({status}) | {item['path']} | {item['purpose']} | {item['owner']} | {item['authority']} | {item['maintenance']} |")
    lines.extend([
        "", "## 使用原则", "",
        "- 给用户交付的资源默认进入本资源库；Codex 内部临时资源不强制迁入。",
        "- 邮件、调度、记忆和记录的文件是业务 owner 的状态，不由目录生成器改写。",
        "- 大型记录和队列优先通过 SQLite/owner 查询面检索，不做无边界全盘扫描。",
        "- 备份统一进入 _backup 并保留 manifest；归档与备份语义分开。",
        "- 新增顶层资源类别时，应同步更新本目录 owner，而不是手写第二份冲突清单。",
        "", f"生成时间：{snap['generated_at']}", "",
    ])
    return "\n".join(lines)


def doctor() -> dict[str, Any]:
    snap = snapshot()
    issues: list[dict[str, Any]] = []
    for item in snap["modules"]:
        if not item["exists"]:
            issues.append({"severity": "risk", "code": "catalog_path_missing", "module": item["id"], "path": item["absolute_path"]})
    if not CATALOG_PATH.exists():
        issues.append({"severity": "advisory", "code": "machine_catalog_missing"})
    readme = README_PATH.read_text(encoding="utf-8-sig") if README_PATH.exists() else ""
    if GENERATED_MARKER not in readme:
        issues.append({"severity": "advisory", "code": "human_catalog_not_generated"})
    return {"schema": "resource_library_catalog.doctor.v1", "ok": not any(item["severity"] == "risk" for item in issues), "generated_at": now_iso(), "issues": issues, "summary": snap["summary"]}


def repair_plan() -> dict[str, Any]:
    doc = doctor()
    return {
        "schema": "resource_library_catalog.repair_plan.v1", "ok": True, "generated_at": now_iso(), "default_apply": False,
        "actions": [{"id": "generate_machine_catalog", "target": str(CATALOG_PATH)}, {"id": "generate_human_readme", "target": str(README_PATH)}],
        "issues_addressed": doc["issues"], "apply_command": "python _bridge\\resource_library_catalog.py build --apply", "business_state_mutation": False,
    }


def build(*, apply: bool) -> dict[str, Any]:
    snap = snapshot()
    if not apply:
        return {**repair_plan(), "schema": "resource_library_catalog.build.v1", "applied": False, "preview": snap}
    existing = [str(path) for path in (README_PATH, CATALOG_PATH) if path.exists()]
    backup = create_backup(existing, remark="刷新资源库人类与机器目录前备份", purpose="resource-library-catalog-refresh", category="resource-library", trigger="resource_library_catalog") if existing else {"ok": True, "created_count": 0, "manifest_paths": []}
    if not backup.get("ok"):
        return {"schema": "resource_library_catalog.build.v1", "ok": False, "applied": False, "reason": "backup_failed", "backup": backup}
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CATALOG_PATH.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    README_PATH.write_text(render_readme(snap), encoding="utf-8")
    return {"schema": "resource_library_catalog.build.v1", "ok": True, "applied": True, "catalog_path": str(CATALOG_PATH), "readme_path": str(README_PATH), "backup": backup, "summary": snap["summary"]}


def validate() -> dict[str, Any]:
    doc = doctor()
    checks = [
        {"name": "all_required_paths_exist", "ok": not any(item["code"] == "catalog_path_missing" for item in doc["issues"])},
        {"name": "machine_catalog_exists", "ok": CATALOG_PATH.exists()},
        {"name": "human_catalog_generated", "ok": README_PATH.exists() and GENERATED_MARKER in README_PATH.read_text(encoding="utf-8-sig")},
    ]
    return {"schema": "resource_library_catalog.validate.v1", "ok": all(item["ok"] for item in checks), "generated_at": now_iso(), "checks": checks, "summary": snapshot()["summary"]}


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Generate the desktop resource-library catalog.")
    parser.add_argument("command", choices=("snapshot", "doctor", "repair-plan", "build", "validate"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    payload = {"snapshot": snapshot, "doctor": doctor, "repair-plan": repair_plan, "validate": validate}.get(args.command, lambda: build(apply=args.apply))()
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
