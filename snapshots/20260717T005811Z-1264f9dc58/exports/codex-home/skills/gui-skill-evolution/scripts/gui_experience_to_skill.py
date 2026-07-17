#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path


SAFE_CATEGORIES = {
    "window-routing",
    "control-selection",
    "input-flow",
    "modal-recovery",
    "output-verification",
    "stability",
    "app-specific",
}

SAFE_LEDGERS = {
    "verified-success",
    "candidate-unverified",
    "failed-or-avoid",
    "lessons",
    "general-candidates",
    "general-trusted",
    "general-conditional",
    "general-failed-or-avoid",
}


def slugify(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return text or "unknown-app"


def build_note(args: argparse.Namespace) -> str:
    now = datetime.now(timezone.utc).isoformat()
    category = args.category if args.category in SAFE_CATEGORIES else "app-specific"
    app_slug = slugify(args.app)
    target = "gui-automation" if args.scope == "general" else f"gui-app-{app_slug}"
    lines = [
        f"# GUI Experience Draft: {args.task}",
        "",
        f"- Captured at: {now}",
        f"- App: {args.app}",
        f"- Target skill: {target}",
        f"- Category: {category}",
        f"- Outcome: {args.outcome}",
        f"- Evidence: {args.evidence or 'not provided'}",
        "",
        "## Lesson",
        "",
        args.lesson.strip(),
        "",
        "## Promotion Gate",
        "",
        "- Confirm scope before applying.",
        "- Verify no secrets, private file contents, account identifiers, or raw transcripts are included.",
        "- Back up any target skill before applying.",
        "- Apply only if the lesson changes future GUI behavior.",
        "",
    ]
    return "\n".join(lines)


def build_ledger_entry(args: argparse.Namespace) -> str:
    now = datetime.now(timezone.utc).isoformat()
    category = args.category if args.category in SAFE_CATEGORIES else "app-specific"
    title = args.title.strip() if args.title else args.task.strip()
    evidence = args.evidence.strip() or "not provided"
    verification = args.verification.strip() or "not provided"
    missing_proof = args.missing_proof.strip()
    avoid_reason = args.avoid_reason.strip()
    lines = [
        f"## {title}",
        "",
        f"- Captured at: {now}",
        f"- Category: {category}",
        f"- Outcome: {args.outcome}",
        f"- Evidence: {evidence}",
        f"- Verification: {verification}",
    ]
    if missing_proof:
        lines.append(f"- Missing proof: {missing_proof}")
    if avoid_reason:
        lines.append(f"- Avoid reason: {avoid_reason}")
    lines.extend(
        [
            "",
            "### Lesson",
            "",
            args.lesson.strip(),
            "",
        ]
    )
    return "\n".join(lines)


def default_app_skill_dir(app: str) -> Path:
    return Path.home() / ".codex" / "skills" / f"gui-app-{slugify(app)}"


def default_general_skill_dir() -> Path:
    return Path.home() / ".codex" / "skills" / "gui-skill-evolution"


def is_general_ledger(ledger: str) -> bool:
    return str(ledger or "").startswith("general-")


def default_skill_dir_for_ledger(app: str, ledger: str) -> Path:
    return default_general_skill_dir() if is_general_ledger(ledger) else default_app_skill_dir(app)


def infer_general_ledger(args: argparse.Namespace) -> str:
    if args.general_ledger:
        return args.general_ledger
    if args.outcome == "failure":
        return "general-failed-or-avoid"
    if args.outcome == "partial":
        return "general-conditional"
    return "general-candidates"


def build_general_title(args: argparse.Namespace) -> str:
    if args.general_title.strip():
        return args.general_title.strip()
    if args.general_pattern.strip():
        return args.general_pattern.strip()
    return args.task.strip()


def write_ledger_entry(args: argparse.Namespace) -> Path:
    ledger = args.ledger.strip()
    if ledger not in SAFE_LEDGERS:
        raise ValueError(f"unsupported ledger: {ledger}")
    skill_dir = Path(args.skill_dir).expanduser() if args.skill_dir else default_skill_dir_for_ledger(args.app, ledger)
    if not skill_dir.exists() and not args.allow_create_skill_dir:
        raise FileNotFoundError(f"target skill directory does not exist: {skill_dir}")
    refs = skill_dir / "references"
    refs.mkdir(parents=True, exist_ok=True)
    target = refs / f"{ledger}.md"
    heading = f"# {ledger.replace('-', ' ').title()}\n\n"
    entry = build_ledger_entry(args)
    if not target.exists():
        target.write_text(heading + entry, encoding="utf-8")
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = target.with_name(f"{target.name}.bak-{stamp}-gui-ledger-append")
        shutil.copy2(target, backup)
        existing = target.read_text(encoding="utf-8")
        separator = "\n" if existing.endswith("\n") else "\n\n"
        target.write_text(existing + separator + entry, encoding="utf-8")
    return target


def build_general_entry(args: argparse.Namespace, general_ledger: str) -> str:
    now = datetime.now(timezone.utc).isoformat()
    pattern = args.general_pattern.strip() or args.lesson.strip()
    title = build_general_title(args)
    evidence = args.evidence.strip() or "not provided"
    verification = args.verification.strip() or "not provided"
    conditions = args.general_conditions.strip() or "not provided"
    missing_proof = args.general_missing_proof.strip() or args.missing_proof.strip()
    failed_scene = args.general_failed_scene.strip()
    lines = [
        f"## {title}",
        "",
        f"- Captured at: {now}",
        f"- Pattern: {pattern}",
        f"- Source app: {args.app}",
        f"- Outcome: {args.outcome}",
        f"- Evidence: {evidence}",
        f"- Verification: {verification}",
        f"- Conditions: {conditions}",
        f"- Confidence status: {general_ledger}",
    ]
    if missing_proof:
        lines.append(f"- Missing proof: {missing_proof}")
    if failed_scene:
        lines.append(f"- Failed scene: {failed_scene}")
    if args.avoid_reason.strip():
        lines.append(f"- Avoid reason: {args.avoid_reason.strip()}")
    lines.extend(
        [
            "",
            "### Transferable Lesson",
            "",
            args.lesson.strip(),
            "",
        ]
    )
    return "\n".join(lines)


def write_general_entry(args: argparse.Namespace) -> Path:
    ledger = infer_general_ledger(args)
    if ledger not in SAFE_LEDGERS:
        raise ValueError(f"unsupported general ledger: {ledger}")
    skill_dir = Path(args.general_skill_dir).expanduser() if args.general_skill_dir else default_general_skill_dir()
    refs = skill_dir / "references"
    refs.mkdir(parents=True, exist_ok=True)
    target = refs / f"{ledger}.md"
    heading = f"# {ledger.replace('-', ' ').title()}\n\n"
    entry = build_general_entry(args, ledger)
    if not target.exists():
        target.write_text(heading + entry, encoding="utf-8")
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = target.with_name(f"{target.name}.bak-{stamp}-gui-ledger-append")
        shutil.copy2(target, backup)
        existing = target.read_text(encoding="utf-8")
        separator = "\n" if existing.endswith("\n") else "\n\n"
        target.write_text(existing + separator + entry, encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a privacy-safe GUI skill evolution draft.")
    parser.add_argument("--app", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--outcome", required=True, choices=["success", "failure", "partial", "unknown"])
    parser.add_argument("--category", required=True)
    parser.add_argument("--lesson", required=True)
    parser.add_argument("--evidence", default="")
    parser.add_argument("--scope", choices=["general", "app"], default="app")
    parser.add_argument("--out-dir", default=str(Path.home() / ".codex" / "skills" / "gui-skill-evolution" / "drafts"))
    parser.add_argument("--ledger", choices=sorted(SAFE_LEDGERS), default="", help="Append directly to an app skill ledger reference file.")
    parser.add_argument("--skill-dir", default="", help="Target gui-app-* skill directory for --ledger. Defaults to ~/.codex/skills/gui-app-<app>.")
    parser.add_argument("--title", default="", help="Ledger entry title. Defaults to --task.")
    parser.add_argument("--verification", default="", help="Short durable verification proof, such as UIA/OCR state or disk readback.")
    parser.add_argument("--missing-proof", default="", help="For candidate-unverified entries, state what proof is missing.")
    parser.add_argument("--avoid-reason", default="", help="For failed-or-avoid entries, state why the path should be avoided.")
    parser.add_argument("--auto-abstract-general", action="store_true", help="When writing app-specific experience, also create a conservative general pool entry.")
    parser.add_argument("--general-pattern", default="", help="Transferable general pattern abstracted from the app-specific result.")
    parser.add_argument("--general-title", default="", help="General pool entry title. Defaults to --general-pattern or --task.")
    parser.add_argument("--general-conditions", default="", help="Conditions required before the general pattern should be tried.")
    parser.add_argument("--general-missing-proof", default="", help="Missing proof that blocks promotion of the general pattern.")
    parser.add_argument("--general-failed-scene", default="", help="Scene summary when a general pattern failed in this app.")
    parser.add_argument("--general-ledger", choices=["", "general-candidates", "general-trusted", "general-conditional", "general-failed-or-avoid"], default="", help="Override the inferred general pool ledger.")
    parser.add_argument("--general-skill-dir", default="", help="Override the gui-skill-evolution skill directory used for general pool entries.")
    parser.add_argument("--allow-create-skill-dir", action="store_true", help="Allow creating the target app skill directory when --ledger is used.")
    parser.add_argument("--apply", action="store_true", help="Write the draft file. Without this flag, print only.")
    args = parser.parse_args()

    if args.ledger:
        entry = build_ledger_entry(args)
        if not args.apply:
            print(entry)
            if args.auto_abstract_general and not is_general_ledger(args.ledger):
                print()
                print(build_general_entry(args, infer_general_ledger(args)))
            return 0
        path = write_ledger_entry(args)
        print(path)
        if args.auto_abstract_general and not is_general_ledger(args.ledger):
            general_path = write_general_entry(args)
            print(general_path)
        return 0

    note = build_note(args)
    if not args.apply:
        print(note)
        return 0

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"{stamp}-{slugify(args.app)}-{slugify(args.task)[:48]}.md"
    path.write_text(note, encoding="utf-8")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
