---
name: skill-analyzer
description: >
  Analyze current Codex skill selection and usage evidence. Use for skill usage
  reports, high- and low-frequency skill review, outcome trends, routing drift,
  or deciding which skills need consolidation, revision, testing, or retirement.
---

# Skill Analyzer

Use the workspace skill owner as the source of truth. Do not read the retired Obsidian `skill_usage_log.jsonl` path or maintain a second analytics implementation inside this skill.

## Role Boundaries

This skill interprets current usage evidence. The skill orchestrator owns logging and aggregation; lifecycle governance and MySkills own approved changes.

## Source Of Truth

Run from the active workspace:

```powershell
python _bridge\skill_orchestrator.py usage-summary
```

The command reads:

```text
_bridge\runtime\skill_orchestrator\skill_usage.jsonl
```

Treat the owner output as machine-readable evidence. Do not infer satisfaction scores that are not present in the current schema.

## Workflow

1. Run `usage-summary` and read `selected_counts`, `used_counts`, `outcomes`, and `record_count`.
2. Compare selected versus used counts to identify skills that trigger but are not actually applied.
3. Treat low frequency as a review signal, not automatic evidence that a skill should be deleted.
4. Combine usage evidence with lifecycle doctor, overlap audit, validation status, and recent task failures before recommending changes.
5. Route any approved change through the skill lifecycle owner and MySkills interfaces.

## Interpretation

- High `selected_counts`, lower `used_counts`: possible trigger overlap or premature selection.
- High usage with partial outcomes: inspect content quality and forward-test realistic tasks.
- Low usage with a unique owner capability: keep unless replacement coverage is proven.
- No usage plus duplicated responsibility: candidate for consolidation or disablement.
- Invalid frontmatter, missing assets, or stale owner paths: correctness defect, independent of frequency.

## Report

Return a compact report containing:

- observation window represented by the available log;
- total records and outcome counts;
- most selected and most used skills;
- material selected-versus-used gaps;
- lifecycle candidates with evidence and recommended next action;
- limitations of the current data.

Do not write reports into an Obsidian path unless the user explicitly asks and the active Obsidian owner resolves the destination.
