#!/usr/bin/env python3
"""Read-only contract checks for Codex skill framework conventions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


EXPECTED = {
    "routing": {
        "required_any": ["## Role Boundaries", "## 角色边界", "## Operating Rules"],
        "require_handoff": True,
        "require_reference_gate_if_refs": True,
    },
    "execution": {
        "required_any": ["## Role Boundaries", "## 角色边界", "## Operating Rules"],
        "require_handoff": False,
        "require_reference_gate_if_refs": True,
    },
    "constraint-method": {
        "required_any": ["## Scope", "## Operating Rules", "## Scope and"],
        "require_handoff": False,
        "require_reference_gate_if_refs": False,
    },
    "evolution-governance": {
        "required_any": ["## Scope", "## Operating Rules", "## Change Triggers"],
        "require_handoff": False,
        "require_reference_gate_if_refs": True,
    },
    "domain-project": {
        "required_any": ["## Scope", "## Operating Rules", "## Project Context"],
        "require_handoff": False,
        "require_reference_gate_if_refs": True,
    },
}


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def has_any(text: str, needles: list[str]) -> bool:
    return any(needle in text for needle in needles)


def inspect_skill(skill_dir: Path, layer: str) -> dict[str, object]:
    skill_md = skill_dir / "SKILL.md"
    text = read_text(skill_md)
    refs_dir = skill_dir / "references"
    refs_present = refs_dir.exists() and refs_dir.is_dir()

    rules = EXPECTED[layer]
    issues: list[str] = []

    if not has_any(text, rules["required_any"]):  # type: ignore[index]
        issues.append("missing boundary/scope section")

    if rules["require_handoff"]:  # type: ignore[index]
        if "handoff" not in text.lower() and "交接规则" not in text:
            issues.append("missing handoff/exit guidance")

    if rules["require_reference_gate_if_refs"] and refs_present:  # type: ignore[index]
        if "When to Load References" not in text and "按需阅读" not in text:
            issues.append("references present but no load-gating section")

    return {
        "skill": skill_dir.name,
        "layer": layer,
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check skill framework contracts for a selected batch.")
    parser.add_argument("--root", required=True, help="Skills root")
    parser.add_argument(
        "--spec",
        action="append",
        default=[],
        help="Skill-layer mapping in the form skill_name=layer",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args()

    root = Path(args.root)
    results = []
    for item in args.spec:
        skill_name, layer = item.split("=", 1)
        if layer not in EXPECTED:
            raise SystemExit(f"Unsupported layer: {layer}")
        results.append(inspect_skill(root / skill_name, layer))

    problems = [r for r in results if r["issues"]]

    if args.json:
        print(json.dumps({"checked": len(results), "issues": problems}, ensure_ascii=False, indent=2))
    else:
        print(f"Checked={len(results)} Issues={len(problems)}")
        for row in problems:
            print(f"- {row['skill']} [{row['layer']}]: {', '.join(row['issues'])}")

    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
