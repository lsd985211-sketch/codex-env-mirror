#!/usr/bin/env python3
"""Read-only overlap hints for broad skill frontmatter descriptions."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


DOMAIN_PATTERNS = {
    "internet-web": re.compile(
        r"(generic web acquisition|webpage|crawl|scrape|fetch page|internet|platform discovery|multi-source research|网页|网站|链接|URL|research|搜索|look up)",
        re.I,
    ),
    "docs-api": re.compile(
        r"(documentation|library docs|framework docs|api|sdk|库文档|API参考|current docs|code examples)",
        re.I,
    ),
    "browser-gui": re.compile(
        r"(existing browser state|chrome browser|browser automation|playwright|web application testing|gui automation|desktop apps|cookies|logged-in sessions|extensions|浏览器自动化|桌面 GUI|Windows GUI)",
        re.I,
    ),
    "review-security": re.compile(
        r"(security review|secure-by-default|threat model|code review|refactor|security|评审|重构|安全)",
        re.I,
    ),
}


def frontmatter_text(text: str) -> str:
    if not text.startswith("---\n"):
        return ""
    end = text.find("\n---", 4)
    if end == -1:
        return ""
    return text[4:end]


def description_text(text: str) -> str:
    fm = frontmatter_text(text)
    for line in fm.splitlines():
        if line.startswith("description:"):
            return line.split(":", 1)[1].strip()
    return fm


def classify(skill_md: Path) -> list[str]:
    text = skill_md.read_text(encoding="utf-8", errors="ignore")
    desc = description_text(text)
    return [name for name, pattern in DOMAIN_PATTERNS.items() if pattern.search(desc)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Report broad overlap candidates across selected skills.")
    parser.add_argument("skills", nargs="+", help="Skill directories")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args()

    by_domain: dict[str, list[str]] = {name: [] for name in DOMAIN_PATTERNS}
    for skill_path in args.skills:
        skill_dir = Path(skill_path)
        hits = classify(skill_dir / "SKILL.md")
        for hit in hits:
            by_domain[hit].append(skill_dir.name)

    overlaps = {k: sorted(v) for k, v in by_domain.items() if len(v) > 1}

    if args.json:
        print(json.dumps(overlaps, ensure_ascii=False, indent=2))
    else:
        if not overlaps:
            print("No overlap candidates detected.")
        for domain, skills in overlaps.items():
            print(f"[{domain}] {', '.join(skills)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
