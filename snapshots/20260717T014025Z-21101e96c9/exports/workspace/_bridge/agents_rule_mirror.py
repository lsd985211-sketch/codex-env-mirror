#!/usr/bin/env python3
"""Mirror Codex AGENTS rules into resource-library txt docs.

The AGENTS.md files remain the executable source of truth for Codex. This
module creates review mirrors from the current source files directly; it must
not carry a second embedded copy of the rules.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CODEX_HOME = Path.home() / ".codex"
RESOURCE_DOC_ROOT = Path.home() / "Desktop" / "Codex资源库" / "文档" / "系统维护" / "Codex准则"

MIRRORS: tuple[dict[str, Any], ...] = (
    {
        "id": "global",
        "title": "Codex 全局准则镜像",
        "source": CODEX_HOME / "AGENTS.md",
        "target": RESOURCE_DOC_ROOT / "全局准则镜像.txt",
        "scope": r"全机器通用规则。实际生效源文件仍为 C:\Users\45543\.codex\AGENTS.md。",
    },
    {
        "id": "workspace_mcsmanager",
        "title": "mcsmanager 工作区准则镜像",
        "source": ROOT / "AGENTS.md",
        "target": RESOURCE_DOC_ROOT / "工作区准则-mcsmanager-镜像.txt",
        "scope": r"当前工作区规则。实际生效源文件仍为 mcsmanager\AGENTS.md。",
    },
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def mirror_text(item: dict[str, Any], source_text: str) -> str:
    source_sha = sha256_text(source_text)
    return (
        f"{item['title']}\n"
        f"{'=' * len(str(item['title']))}\n\n"
        "镜像说明\n"
        "- 本文件是人工阅读/草拟编辑镜像，不是 Codex 实际读取的权威源。\n"
        "- 权威源仍是对应 AGENTS.md；修改镜像后需要同步回源文件才会生效。\n"
        "- 若源文件变化，请运行 `python _bridge\\agents_rule_mirror.py sync` 刷新镜像。\n\n"
        f"范围: {item['scope']}\n"
        f"源文件: {item['source']}\n"
        f"源文件 SHA256: {source_sha}\n"
        f"镜像生成时间 UTC: {now_iso()}\n\n"
        "源文件内容\n"
        "----------\n\n"
        f"{source_text.rstrip()}\n"
    )


def mirror_status(item: dict[str, Any]) -> dict[str, Any]:
    source = Path(item["source"])
    target = Path(item["target"])
    source_exists = source.exists()
    target_exists = target.exists()
    source_text = read_text(source) if source_exists else ""
    target_text = read_text(target) if target_exists else ""
    source_sha = sha256_text(source_text) if source_exists else ""
    embedded_ok = bool(source_sha and f"源文件 SHA256: {source_sha}" in target_text)
    return {
        "id": item["id"],
        "source": str(source),
        "target": str(target),
        "source_exists": source_exists,
        "target_exists": target_exists,
        "source_sha256": source_sha,
        "target_sha256": sha256_text(target_text) if target_exists else "",
        "in_sync": bool(source_exists and target_exists and embedded_ok),
        "embedded_source_sha_match": embedded_ok,
    }


def sync(_args: argparse.Namespace | None = None) -> dict[str, Any]:
    RESOURCE_DOC_ROOT.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    for item in MIRRORS:
        source = Path(item["source"])
        target = Path(item["target"])
        if not source.exists():
            items.append({"id": item["id"], "ok": False, "reason": "source_missing", "source": str(source)})
            continue
        source_text = read_text(source)
        target.write_text(mirror_text(item, source_text), encoding="utf-8")
        items.append(
            {
                "id": item["id"],
                "ok": True,
                "source": str(source),
                "target": str(target),
                "source_sha256": sha256_text(source_text),
            }
        )
    index = RESOURCE_DOC_ROOT / "README.txt"
    index.write_text(
        "Codex 准则镜像\n"
        "================\n\n"
        "这里存放全局和工作区 AGENTS.md 的 txt 镜像，方便人工阅读和草拟编辑。\n"
        "权威源仍是对应 AGENTS.md；修改镜像后需要同步回源文件才会真正生效。\n\n"
        "文件:\n"
        "- 全局准则镜像.txt\n"
        "- 工作区准则-mcsmanager-镜像.txt\n\n"
        "维护命令:\n"
        "- 刷新镜像: python _bridge\\agents_rule_mirror.py sync\n"
        "- 检查漂移: python _bridge\\agents_rule_mirror.py doctor\n"
        "- 验证: python _bridge\\agents_rule_mirror.py validate\n",
        encoding="utf-8",
    )
    return {
        "schema": "agents_rule_mirror.sync.v1",
        "ok": all(item.get("ok") for item in items),
        "generated_at": now_iso(),
        "root": str(RESOURCE_DOC_ROOT),
        "items": items,
    }


def snapshot(_args: argparse.Namespace | None = None) -> dict[str, Any]:
    statuses = [mirror_status(item) for item in MIRRORS]
    return {
        "schema": "agents_rule_mirror.snapshot.v1",
        "ok": True,
        "generated_at": now_iso(),
        "root": str(RESOURCE_DOC_ROOT),
        "items": statuses,
    }


def doctor(_args: argparse.Namespace | None = None) -> dict[str, Any]:
    snap = snapshot()
    issues: list[dict[str, Any]] = []
    for item in snap["items"]:
        if not item["source_exists"]:
            issues.append({"severity": "blocker", "code": "source_missing", "id": item["id"], "path": item["source"]})
        elif not item["target_exists"]:
            issues.append({"severity": "risk", "code": "mirror_missing", "id": item["id"], "path": item["target"]})
        elif not item["in_sync"]:
            issues.append({"severity": "risk", "code": "mirror_drifted", "id": item["id"], "path": item["target"]})
    severities = {issue["severity"] for issue in issues}
    status = "blocker" if "blocker" in severities else "risk" if "risk" in severities else "ok"
    return {
        "schema": "agents_rule_mirror.doctor.v1",
        "ok": status == "ok",
        "generated_at": now_iso(),
        "status": status,
        "issues": issues,
        "snapshot": snap,
    }


def validate(_args: argparse.Namespace | None = None) -> dict[str, Any]:
    doc = doctor()
    checks = [
        {"name": "mirror_root_exists", "ok": RESOURCE_DOC_ROOT.exists(), "detail": str(RESOURCE_DOC_ROOT)},
        {"name": "doctor_ok", "ok": bool(doc.get("ok")), "detail": doc.get("status")},
        {
            "name": "all_mirrors_in_sync",
            "ok": all(item.get("in_sync") for item in doc.get("snapshot", {}).get("items", [])),
            "detail": "embedded source sha match",
        },
    ]
    return {
        "schema": "agents_rule_mirror.validate.v1",
        "ok": all(check["ok"] for check in checks),
        "generated_at": now_iso(),
        "checks": checks,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mirror AGENTS.md rules to resource-library txt files")
    parser.add_argument("command", choices=["sync", "snapshot", "doctor", "validate"])
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    if args.command == "sync":
        payload = sync(args)
    elif args.command == "snapshot":
        payload = snapshot(args)
    elif args.command == "doctor":
        payload = doctor(args)
    else:
        payload = validate(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
