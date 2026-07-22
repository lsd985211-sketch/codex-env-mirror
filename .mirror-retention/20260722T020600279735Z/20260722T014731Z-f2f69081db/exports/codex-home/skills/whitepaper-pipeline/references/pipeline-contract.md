# Whitepaper Pipeline Contract

Use this reference when planning or resuming a full whitepaper lifecycle. The
pipeline is a state machine, not a checklist of unrelated tools.

## Stage Contract

| Stage | Required input | Execution owner | Required receipt | Acceptance gate |
|---|---|---|---|---|
| `intake` | User request and existing publication policy | `whitepaper-pipeline` | Normalized intake contract | Scope, audience, deliverables, freshness, and publication authority are explicit |
| `evidence_plan` | Intake questions and source rules | Resource layer plus capability matrix | Structured request IDs and search plan | Every question has an acquisition route and authority rule |
| `evidence_collect` | Structured requests | Resource owner adapters | Source receipts and evidence ledger | Sources are relevant, readable, current enough, and traceable |
| `argument` | Evidence ledger and audience | `doc-coauthoring` | Outline and claim-to-source map | Major claims, caveats, and section criteria are covered |
| `canonical_build` | Approved outline and evidence | Writing task plus `data-analysis` and `mermaid-generator` as needed | Canonical Markdown, snapshot, shared assets | Source IDs resolve and generated numbers are reproducible |
| `document_render` | Canonical artifacts | `office-craft` and Office owner | HTML, DOCX, PDF receipts | Structure, images, links, pagination, and extracted text pass |
| `public_transform` | Canonical source and publication policy | Whitepaper task plus selected frontend skill | Public source tree and sanitization report | No blocked path, secret, private endpoint, or internal-only artifact remains |
| `public_validate` | Public source tree | Frontend validator and `playwright` | Local validation receipt | Page and assets load; links, layout, console, and sensitive-pattern checks pass |
| `publish` | Authorized validated tree | `github-ops` | Commit/deployment receipt or no-op hash receipt | Remote SHA/build status and public URL are read back |
| `live_verify` | Published URL | `playwright` | Live verification receipt | Expected title/content/assets are visible and no blocking errors occur |
| `checkpoint` | All stage receipts | Workflow/closeout owner | Lifecycle checkpoint | Hashes, evidence IDs, deployment ID, freshness trigger, and unresolved items persist |

## Evidence Ledger

Each source entry should contain:

```json
{
  "source_id": "src-001",
  "question_ids": ["q-01"],
  "title": "",
  "origin": "",
  "owner_tool": "",
  "retrieved_at": "",
  "published_at": "",
  "authority": "primary|official|peer-reviewed|secondary|community",
  "relevance": 0.0,
  "freshness_status": "current|stale|unknown",
  "content_ref": "",
  "limitations": []
}
```

Do not score authority or relevance from file type alone. A PDF is a container,
not evidence that the content is a paper or authoritative source.

## Lifecycle Checkpoint

Persist one compact checkpoint beside the canonical artifacts:

```json
{
  "schema": "whitepaper.pipeline.checkpoint.v1",
  "subject": "",
  "status": "planned|running|blocked|validated|published",
  "current_stage": "",
  "canonical": {"markdown": "", "snapshot": "", "hash": ""},
  "public": {"directory": "", "hash": "", "site_url": ""},
  "receipts": {},
  "deployment": {"repository": "", "commit": "", "build": ""},
  "freshness": {"as_of": "", "next_review_at": ""},
  "updated_at": ""
}
```

## Composition Rules

- Load `whitepaper-pipeline` for lifecycle orchestration.
- Load only the execution skill needed by the current stage.
- Let each execution skill retain its native validation and fallback rules.
- Do not copy execution instructions into this contract.
- Resume from the last valid receipt; do not repeat completed stages unless an
  input hash, scope, freshness rule, or publication policy changed.
