---
name: whitepaper-pipeline
description: "Orchestrate the complete evidence-to-publication lifecycle for a whitepaper: scope the request, delegate structured research, govern sources, create canonical content and data snapshots, coordinate diagrams and Office/HTML/PDF production, derive a public-safe website, publish it, verify the live result, and update it incrementally. Use when a task spans information collection through whitepaper creation and website publication or when an existing whitepaper and its public site must stay synchronized. Do not use for a single research lookup, a standalone document edit, or a standalone website deployment."
---

# Whitepaper Pipeline

## Role

Act as a scenario orchestrator. Own the lifecycle contract, stage transitions,
receipts, and acceptance gates. Hand execution to existing domain skills and
owners; do not reimplement their procedures.

## Boundary

- Do not replace the resource layer, `doc-coauthoring`, `office-craft`,
  `cli-anything-microsoft-office`, `mermaid-generator`, `data-analysis`,
  frontend skills, `github-ops`, or `playwright`.
- Do not publish internal evidence, machine paths, credentials, private URLs,
  or machine snapshots unless the publication contract explicitly allows it.
- Keep the internal document source and public-site output separate.
- Treat remote publication as an external write. Publish only when the request
  authorizes it or an existing approved publication policy covers the update.

## Intake Contract

Before collecting material, normalize the request into these fields:

```yaml
subject: ""
mode: create | update | publish-only
audience: ""
scope: []
questions: []
freshness:
  as_of: ""
  max_age: ""
sources:
  preferred: []
  excluded: []
  authority_rules: []
deliverables:
  canonical_markdown: true
  machine_snapshot: true
  html: true
  docx: true
  pdf: true
  public_site: true
publication:
  authorized: false
  target: ""
  public_policy: ""
validation:
  required: []
```

Ask only for a field that cannot be inferred safely. Store this contract with
the task artifacts so updates reuse the same scope and publication policy.

## Pipeline

Read [pipeline-contract.md](references/pipeline-contract.md) for the detailed
stage table. Run the following stages in order, skipping a stage only when its
receipt already satisfies the current request.

1. **Plan evidence.** Convert scope and questions into structured resource
   requests. Use the resource layer first for external acquisition. Choose
   source-specific owners through the capability matrix; natural-language
   search text supplements structured fields rather than replacing them.
2. **Collect and govern evidence.** Require source receipts with origin,
   retrieval time, authority, relevance, freshness, and local artifact or
   content reference. Build an evidence ledger. Refine the delegation when
   results are weak; do not silently replace an active resource job with a
   second acquisition route.
3. **Define the argument.** Use `doc-coauthoring` for outline, audience,
   claims, counterpoints, and section acceptance criteria. Every load-bearing
   claim must map to evidence or be labeled as analysis/inference.
4. **Build canonical artifacts.** Maintain one canonical Markdown source and
   one machine-readable snapshot. Use `data-analysis` for derived metrics and
   `mermaid-generator` for diagrams. Keep source IDs stable across formats.
5. **Render document formats.** Hand Markdown, snapshot, and shared assets to
   `office-craft`. Let it coordinate the document runtime and
   `cli-anything-microsoft-office` for native Office inspection/export. Reuse
   rendered diagram assets across HTML, DOCX, and PDF.
6. **Build the public edition.** Apply an explicit public-safety transform,
   then choose the smallest web execution skill: a static Markdown-to-HTML
   path for article-like output or `frontend-app-builder` for a richer site.
   The public build must consume the canonical source or a declared public
   derivative, never an independently maintained copy.
7. **Validate locally.** Verify structure, headings, tables, image readability,
   asset portability, links, extracted text, sensitive-pattern rejection, and
   public/internal directory separation. A failed gate stops publication.
8. **Publish and read back.** Use `github-ops` for repository and Pages writes,
   with the configured MCP priority chain and network gateway. Skip commits
   when the public-content hash is unchanged. After a changed push, wait for
   the deployment and use `playwright` to verify the live page and assets.
9. **Checkpoint the update.** Persist hashes, source receipts, generated
   artifacts, deployment identity, live URL, verification evidence, and the
   next freshness trigger. Update relevant skills only for reusable verified
   lessons, not one-off publication details.

## Automatic Update Semantics

- A whitepaper content update automatically triggers public-build planning and
  local public validation when `deliverables.public_site` is true.
- Remote publication remains conditional on `publication.authorized` or an
  approved standing policy.
- If generated public content is unchanged, record a no-op receipt and do not
  commit or deploy.
- If collection, rendering, sanitization, or validation fails, preserve the
  currently live site and return the failed stage with recovery guidance.
- If only external facts are stale, refresh affected evidence and dependent
  sections instead of rebuilding unrelated material.

## Handoff Discipline

For every handoff, pass only:

- normalized task fields relevant to the receiving owner;
- paths or IDs of canonical inputs;
- required output schema;
- acceptance checks;
- previous receipt ID when resuming.

Accept completion only from a machine-readable receipt or direct artifact
readback. A message that work was submitted is not a completion receipt.

## Output Contract

Return:

1. lifecycle status by stage;
2. canonical and published artifact locations;
3. evidence and validation summary;
4. live URL and deployment identity when published;
5. deferred or failed stages with concrete recovery actions.

Do not repeat the detailed operating instructions of delegated skills in the
final report.
