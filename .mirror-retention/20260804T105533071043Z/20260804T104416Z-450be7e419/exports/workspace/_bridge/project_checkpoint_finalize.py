#!/usr/bin/env python3
"""Create a project-scoped checkpoint after verified major changes.

This is a local finalizer, not a daemon. It persists project evidence in the
checkpoint store and emits a bounded PMB promotion candidate for deliberate use.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from shared.backup_router import create_backup


ROOT = Path(__file__).resolve().parents[1]
KB_ROOT = ROOT / "_bridge" / "shared"
CHECKPOINT_ROOT = KB_ROOT / "checkpoints"
MANIFEST = CHECKPOINT_ROOT / "MANIFEST.md"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def slugify(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"[^a-z0-9._-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-._")
    return text or "checkpoint"


def split_items(values: list[str] | None) -> list[str]:
    items: list[str] = []
    for value in values or []:
        for part in value.split(";"):
            item = part.strip()
            if item:
                items.append(item)
    return items


def bullet_list(items: list[str]) -> str:
    if not items:
        return "- none recorded"
    return "\n".join(f"- {item}" for item in items)


@dataclass(frozen=True)
class Checkpoint:
    project_id: str
    change_type: str
    title: str
    summary: str
    evidence: list[str]
    verification: list[str]
    backups: list[str]
    changed_files: list[str]
    stable_conclusions: list[str]
    followups: list[str]
    created_at: str
    checkpoint_id: str
    input_signature: str
    logical_ref: str


def checkpoint_input_signature(
    *,
    project_id: str,
    change_type: str,
    title: str,
    summary: str,
    evidence: list[str],
    verification: list[str],
    backups: list[str],
    changed_files: list[str],
    stable_conclusions: list[str],
    followups: list[str],
) -> str:
    payload = {
        "project_id": project_id,
        "change_type": change_type,
        "title": title,
        "summary": summary,
        "evidence": evidence,
        "verification": verification,
        "backups": backups,
        "changed_files": changed_files,
        "stable_conclusions": stable_conclusions,
        "followups": followups,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_checkpoint(args: argparse.Namespace) -> Checkpoint:
    created = now_utc()
    project_id = slugify(args.project_id)
    title = args.title.strip()
    change_type = args.change_type.strip()
    summary = args.summary.strip()
    evidence = split_items(args.evidence)
    verification = split_items(args.verification)
    backups = split_items(args.backup)
    changed_files = split_items(args.changed_file)
    stable_conclusions = split_items(args.stable_conclusion)
    followups = split_items(args.followup)
    input_signature = checkpoint_input_signature(
        project_id=project_id,
        change_type=change_type,
        title=title,
        summary=summary,
        evidence=evidence,
        verification=verification,
        backups=backups,
        changed_files=changed_files,
        stable_conclusions=stable_conclusions,
        followups=followups,
    )
    short_hash = input_signature[:12]
    checkpoint_id = f"checkpoint-{short_hash}"
    filename = f"{created.strftime('%Y%m%d-%H%M%S')}-{slugify(title)}.md"
    rel_path = Path("checkpoints") / project_id / filename
    return Checkpoint(
        project_id=project_id,
        change_type=change_type,
        title=title,
        summary=summary,
        evidence=evidence,
        verification=verification,
        backups=backups,
        changed_files=changed_files,
        stable_conclusions=stable_conclusions,
        followups=followups,
        created_at=created.isoformat(),
        checkpoint_id=checkpoint_id,
        input_signature=input_signature,
        logical_ref=str(rel_path).replace("\\", "/"),
    )


def render_markdown(checkpoint: Checkpoint) -> str:
    return f"""# {checkpoint.title}

metadata:
- checkpoint_id: {checkpoint.checkpoint_id}
- project_id: {checkpoint.project_id}
- change_type: {checkpoint.change_type}
- created_at: {checkpoint.created_at}
- input_signature: {checkpoint.input_signature}

## Summary
{checkpoint.summary}

## Changed Files
{bullet_list(checkpoint.changed_files)}

## Evidence
{bullet_list(checkpoint.evidence)}

## Verification
{bullet_list(checkpoint.verification)}

## Backups
{bullet_list(checkpoint.backups)}

## Stable Conclusions
{bullet_list(checkpoint.stable_conclusions)}

## Followups
{bullet_list(checkpoint.followups)}
"""


def checkpoint_path_contract(checkpoint: Checkpoint) -> dict[str, str]:
    logical_ref = checkpoint.logical_ref
    workspace_path = (KB_ROOT / logical_ref).resolve()
    workspace_relative_path = str(workspace_path.relative_to(ROOT)).replace("\\", "/")
    return {
        "schema": "project_checkpoint.path_contract.v1",
        "logical_ref": logical_ref,
        "workspace_relative_path": workspace_relative_path,
        "workspace_path": str(workspace_path),
    }


def build_suggestions(checkpoint: Checkpoint) -> dict[str, object]:
    paths = checkpoint_path_contract(checkpoint)
    vector_text = checkpoint.summary
    if checkpoint.stable_conclusions:
        vector_text = " ".join(checkpoint.stable_conclusions)
    return {
        "pmb_memory": {
            "owner": "local-pmb-memory",
            "write_when": "A verified stable conclusion should change future recall or execution behavior.",
            "candidate": {
                "text": vector_text,
                "tags": f"{checkpoint.project_id},{checkpoint.change_type},stable-conclusion",
                "metadata": {
                    "project_id": checkpoint.project_id,
                    "checkpoint_id": checkpoint.checkpoint_id,
                    "source": paths["logical_ref"],
                    "source_logical_ref": paths["logical_ref"],
                    "source_workspace_path": paths["workspace_path"],
                    "verified": True,
                },
            },
        },
        "project_checkpoint": {
            "owner": "project_checkpoint_finalize",
            "status": "persisted_by_owner_when_apply_is_enabled",
            "logical_ref": paths["logical_ref"],
            "workspace_relative_path": paths["workspace_relative_path"],
            "workspace_path": paths["workspace_path"],
            "source": paths["logical_ref"],
        },
    }


def write_checkpoint(checkpoint: Checkpoint) -> Path:
    target = KB_ROOT / checkpoint.logical_ref
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"checkpoint already exists: {target}")
    target.write_text(render_markdown(checkpoint), encoding="utf-8", newline="\n")
    return target


def find_existing_checkpoint(checkpoint: Checkpoint) -> Path | None:
    project_root = CHECKPOINT_ROOT / checkpoint.project_id
    marker = f"- input_signature: {checkpoint.input_signature}"
    if not project_root.is_dir():
        return None
    for path in sorted(project_root.glob("*.md"), reverse=True):
        if marker in path.read_text(encoding="utf-8", errors="replace"):
            return path
    return None


def reuse_checkpoint(checkpoint: Checkpoint, path: Path) -> Checkpoint:
    text = path.read_text(encoding="utf-8", errors="replace")

    def metadata_value(name: str, fallback: str) -> str:
        match = re.search(rf"(?m)^- {re.escape(name)}: (.+)$", text)
        return match.group(1).strip() if match else fallback

    logical_ref = str(path.resolve().relative_to(KB_ROOT.resolve())).replace("\\", "/")
    return replace(
        checkpoint,
        checkpoint_id=metadata_value("checkpoint_id", checkpoint.checkpoint_id),
        created_at=metadata_value("created_at", checkpoint.created_at),
        logical_ref=logical_ref,
    )


def backup_manifest(path: Path = MANIFEST) -> dict[str, object]:
    """Back up an existing manifest through the shared owner before mutation."""

    if not path.exists():
        return {"ok": True, "skipped": "manifest_not_created_yet", "path": str(path)}
    return create_backup(
        [str(path)],
        remark="before-project-checkpoint-manifest-update",
        purpose="project checkpoint manifest update",
        category="checkpoint",
        trigger="project_checkpoint_finalize",
    )


def update_manifest(checkpoint: Checkpoint, keep_recent: int = 12) -> dict[str, object]:
    """Update the checkpoint manifest after a verified checkpoint write."""

    rel_path = checkpoint.logical_ref
    today = now_utc().date().isoformat()
    if MANIFEST.exists():
        text = MANIFEST.read_text(encoding="utf-8", errors="replace")
    else:
        text = "# Checkpoint MANIFEST v1.0.0\n\n> Updated: unknown\n\n## Recent checkpoints\n"
    lines = text.splitlines()
    updated_lines: list[str] = []
    updated_seen = False
    for line in lines:
        if line.startswith("> Updated:"):
            updated_lines.append(f"> Updated: {today}")
            updated_seen = True
        else:
            updated_lines.append(line)
    if not updated_seen:
        updated_lines.insert(1, "")
        updated_lines.insert(2, f"> Updated: {today}")

    try:
        recent_index = next(i for i, line in enumerate(updated_lines) if line.strip() == "## Recent checkpoints")
    except StopIteration:
        if updated_lines and updated_lines[-1].strip():
            updated_lines.append("")
        updated_lines.extend(["## Recent checkpoints"])
        recent_index = len(updated_lines) - 1

    prefix = updated_lines[: recent_index + 1]
    tail = updated_lines[recent_index + 1 :]
    existing_recent: list[str] = []
    rest_start = len(tail)
    for idx, line in enumerate(tail):
        stripped = line.strip()
        if stripped.startswith("## ") and idx > 0:
            rest_start = idx
            break
        if stripped.startswith("- "):
            existing_recent.append(stripped[2:].strip())
    rest = tail[rest_start:]
    recent: list[str] = [rel_path]
    for item in existing_recent:
        if item and item != rel_path and item not in recent:
            recent.append(item)
        if len(recent) >= keep_recent:
            break
    new_lines = prefix + [f"- {item}" for item in recent]
    if rest:
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.extend(rest)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8", newline="\n")
    return {"ok": True, "manifest": str(MANIFEST), "updated": today, "recent_count": len(recent), "added": rel_path}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Finalize a verified major-change checkpoint")
    parser.add_argument("--project-id", required=True, help="Stable engineering project id")
    parser.add_argument("--change-type", required=True, help="baseline, feature, fix, migration, root-cause, etc.")
    parser.add_argument("--title", required=True, help="Human checkpoint title")
    parser.add_argument("--summary", required=True, help="Concise verified summary")
    parser.add_argument("--changed-file", action="append", help="Changed file path; repeat or separate with semicolons")
    parser.add_argument("--evidence", action="append", help="Evidence item; repeat or separate with semicolons")
    parser.add_argument("--verification", action="append", help="Verification command/result; repeat or separate with semicolons")
    parser.add_argument("--backup", action="append", help="Backup path; repeat or separate with semicolons")
    parser.add_argument("--stable-conclusion", action="append", help="Reusable conclusion; repeat or separate with semicolons")
    parser.add_argument("--followup", action="append", help="Follow-up item; repeat or separate with semicolons")
    parser.add_argument("--write", action="store_true", help="Write the checkpoint markdown")
    parser.add_argument("--json", action="store_true", help="Emit JSON result")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    checkpoint = build_checkpoint(args)
    existing = find_existing_checkpoint(checkpoint)
    if existing is not None:
        checkpoint = reuse_checkpoint(checkpoint, existing)
    paths = checkpoint_path_contract(checkpoint)
    target = Path(paths["workspace_path"])
    checkpoint_payload = asdict(checkpoint)
    checkpoint_payload.update(paths)
    result = {
        "ok": True,
        "dry_run": not args.write,
        "checkpoint": checkpoint_payload,
        "path_contract": paths,
        "target": paths["workspace_path"],
        "markdown": render_markdown(checkpoint),
        "suggestions": build_suggestions(checkpoint),
    }
    if args.write:
        if existing is not None:
            result["reused"] = True
            result["written"] = str(existing)
            result["manifest"] = {
                "ok": True,
                "skipped": "checkpoint_input_signature_already_current",
                "manifest": str(MANIFEST),
            }
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(f"reused: {existing}")
            return 0
        manifest_backup = backup_manifest()
        result["manifest_backup"] = manifest_backup
        if not manifest_backup.get("ok"):
            result.update({"ok": False, "reason": "checkpoint_manifest_backup_failed"})
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(f"blocked: manifest backup failed for {MANIFEST}")
            return 1
        written = write_checkpoint(checkpoint)
        result["written"] = str(written)
        result["manifest"] = update_manifest(checkpoint)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        status = "wrote" if args.write else "dry-run"
        print(f"{status}: {target}")
        print()
        print(render_markdown(checkpoint))
        print("Memory suggestions:")
        print(json.dumps(build_suggestions(checkpoint), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
