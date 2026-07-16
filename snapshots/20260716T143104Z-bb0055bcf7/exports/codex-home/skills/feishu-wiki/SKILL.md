---
name: feishu-wiki
description: Read Feishu documents and Bitable data, inspect wiki trees, and publish Markdown content through the Feishu Open API. Use for Feishu or Lark document URLs, wiki navigation, Bitable records, or approved wiki writes.
metadata: {"codex":{"compatibility":"Requires requests plus FEISHU_APP_ID and FEISHU_APP_SECRET. Wiki writes additionally require FEISHU_WIKI_SPACE_ID and FEISHU_WIKI_ROOT_NODE; fixed account identifiers are intentionally not bundled."}}
---

# Feishu Wiki And Documents

## Boundaries

- Reading documents, wiki metadata, and Bitable records is read-only.
- Creating documents or records is a remote write and requires the user's approval.
- Credentials and workspace identifiers must come from environment variables or the secret owner. Never place them in `SKILL.md`, source code, logs, or command output.

## Environment

Required for API access:

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`

Required only for configured wiki writes/tree defaults:

- `FEISHU_WIKI_SPACE_ID`
- `FEISHU_WIKI_ROOT_NODE`

Required only for the bundled record-import helper:

- `FEISHU_BITABLE_APP_TOKEN`
- `FEISHU_BITABLE_TABLE_ID`

## Commands

```powershell
python scripts/read_document.py <document-url-or-token> --mode raw
python scripts/read_document.py <document-url-or-token> --mode blocks
python scripts/read_bitable.py --url <bitable-url>
python scripts/list_wiki.py --json
python scripts/save_to_wiki.py --file <markdown-file> --parent <node-token>
```

Use `save_to_wiki.py` and `add_records.py` only after remote-write approval.

## Workflow

1. Classify the URL or token as docx, wiki, or Bitable.
2. Check required environment variables without printing their values.
3. Use the narrowest script and request mode.
4. Preserve structured JSON for Codex-facing processing.
5. Report API permission, access, pagination, or authentication errors explicitly.

## References

- `references/legacy-wiki-guide.md` preserves the previous workflow examples.
- The former `feishu-doc-reader` instructions are retained only as migration history under that skill; current execution belongs here.

## Output Contract

- State the operation, token type, item/page count, and whether remote state changed.
- Never include access tokens, app secrets, or full credential-bearing URLs.
- Do not report success when the API returns an empty or partial result without explanation.
