---
name: feishu-doc-reader
description: Compatibility entry for older Feishu document-reading requests. Route all current Feishu document, wiki, and Bitable work to feishu-wiki.
metadata: {"codex":{"superseded_by":"feishu-wiki","compatibility":"Retained so older prompts and references continue to resolve without maintaining a second Feishu implementation."}}
---

# Feishu Document Reader Compatibility Entry

This skill no longer owns execution. Use `feishu-wiki` for current Feishu work.

## Handoff

- Document or wiki URL: use the `feishu-wiki` document reader.
- Bitable URL: use the `feishu-wiki` Bitable reader.
- Wiki tree or publishing: use the corresponding `feishu-wiki` scripts.
- Historical commands are preserved in `references/legacy-guide.md` for migration only; do not execute them because their bundled scripts are absent.

## Output Contract

- Name the current owner skill and command used.
- Report authentication, permission, or API errors without exposing credentials.
