---
name: open-code-review-delegate
description: Use Alibaba OpenCodeReview in LLM-free delegation mode for code reviews. Trigger when Codex reviews workspace changes, a commit, or a branch range and should use OCR for deterministic file selection and rule resolution while keeping review reasoning inside Codex.
---

# Open Code Review Delegation

Use the managed `ocr` binary only for deterministic review preparation. Codex
owns issue analysis, severity, false-positive filtering, fixes, and the final
report.

## Preflight

Run `ocr version`. If it is missing or does not match the governed developer
toolchain, use `developer_toolchain_owner.py`; do not install an npm package or
write `~/.opencodereview/config.json` as a fallback.

Delegation mode does not need an OCR LLM endpoint, model, token, or telemetry
exporter. Do not configure them for this workflow.

## Workflow

1. From the target Git repository, select the review scope:
   - Workspace: `ocr delegate preview`
   - Commit: `ocr delegate preview --commit <sha>`
   - Range: `ocr delegate preview --from <base> --to <head>`
   - Use `--repo <path>` when the process cwd is not the repository root.
2. Consume the preview's mode, refs, merge base, reviewable paths, and excluded
   paths. Do not broaden the review beyond its accepted file set without a
   concrete reason.
3. Run `ocr delegate rule <path...>` for the accepted paths. Batch paths when
   useful; preserve rule groups instead of repeating identical instructions.
4. Read the actual changes with Git:
   - Workspace tracked files: `git diff HEAD -- <path>`
   - Workspace untracked files: read the complete file.
   - Commit: `git show <sha> -- <path>`
   - Range: `git diff <merge-base>..<head> -- <path>`
5. Inspect surrounding source and tests only where the diff and rule evidence
   require context. Report bugs, regressions, security risks, data loss,
   performance faults, and material maintainability gaps; silently discard
   unsupported or low-value speculation.
6. Put findings first, ordered by severity. Each finding names the file and
   line, explains the behavior and impact, and states a concrete correction.
   If there are no findings, say so and identify any remaining test gap.
7. Apply fixes only when the user requested review-and-fix or separately
   approved mutation. Re-run the smallest relevant tests after a fix.

## Boundaries

- OCR preview/rule success is preparation evidence, not review completion.
- Do not send code to an OCR-managed LLM in delegation mode.
- Do not let OCR rules override repository safety, ownership, or approval
  boundaries.
- Preserve untracked-file handling: preview can include them, but Git diff
  cannot show their content.

Upstream basis: `alibaba/open-code-review` v1.7.17,
`plugins/open-code-review/skills/open-code-review-delegate/SKILL.md`
(Apache-2.0).
