# OfficeCLI Evaluation Draft

Content maturity: draft
Workflow status: retained_reference
Pending action: none

Created: 2026-07-11 23:42 +08:00

## Purpose

Retain the OfficeCLI evaluation for possible future reconsideration without
installing it, approving a pilot, or placing it in a pending review queue.

## Evaluation

`iOfficeAI/OfficeCLI` is a C#/.NET 10 self-contained OOXML CLI for Word, Excel,
and PowerPoint with HTML/PNG feedback, SDKs, plugins, and agent skills.

Current recommendation: optional isolated pilot only. It should not replace the
existing `office-craft`, `docx`, `xlsx`, `pptx`, or native Microsoft Office
harness workflows without a separate approved evaluation.

## Possible Pilot

- Use the portable Windows x64 release and verify published checksums.
- Do not run `officecli install` initially; keep automatic updates disabled.
- Test CJK DOCX, formula-heavy XLSX, complex PPTX, tracked changes, and
  screenshot behavior.
- Consider an owner CLI adapter only after the isolated tests pass.

## Risks

- No independent test project was identified during the evaluation.
- CI evidence appeared weighted toward smoke tests.
- Release churn was rapid.
- Screenshot workflows may depend on a browser runtime.
- Maintenance was concentrated and compatibility/performance issues were active.

## Revisit Condition

Revisit only when the user explicitly asks to evaluate, install, pilot, or
compare OfficeCLI again. Keeping this draft does not create a Review Card.
