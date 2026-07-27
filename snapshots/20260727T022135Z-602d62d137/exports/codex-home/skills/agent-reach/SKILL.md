---
name: agent-reach
description: >
  Route internet and platform discovery tasks to the smallest useful source set.
  Use for multi-source research, source selection, platform-specific discovery,
  GitHub search, social platforms, YouTube, RSS, jobs, or questions about where
  online evidence should come from. Do not use for fetching one known webpage,
  content analysis after acquisition, or online write actions.
metadata:
  openclaw:
    homepage: https://github.com/Panniantong/Agent-Reach
---

# Agent Reach

Select sources and prepare an acquisition brief. Do not bypass the workspace resource layer by running platform commands merely because this skill knows they exist.

## Role Boundaries

- Own source classification, source priority, query refinement, and multi-source coverage.
- Submit external acquisition as a machine-readable resource request through the workspace resource layer.
- Let the resource layer choose and execute the configured owner tool, network route, cache, retry, and materialization path.
- When the resource layer requests an owner handoff, call that owner tool for the same request and attach the result to the same receipt.
- Stop after evidence collection. Leave interpretation, writing, translation, and analysis to the owning task or another skill.

Do not claim these tasks:

- Fetching the body of one known normal webpage or documentation URL.
- Posting, commenting, liking, following, or other remote write actions.
- Analyzing already acquired content.
- Installing or downloading resources without the resource-layer approval path.

## When to Load References

Read only the reference for the selected source class. Do not load all platform instructions into context.

## Source Classes

| Class | Typical targets | Read when needed |
|---|---|---|
| `search` | General discovery, multi-source research | [references/search.md](references/search.md) |
| `social` | Xiaohongshu, X, Reddit, Bilibili, V2EX | [references/social.md](references/social.md) |
| `career` | Jobs, LinkedIn, remote-work sources | [references/career.md](references/career.md) |
| `dev` | GitHub repositories, code, issues, pull requests | [references/dev.md](references/dev.md) |
| `web` | Websites, articles, RSS source selection | [references/web.md](references/web.md) |
| `video` | YouTube, Bilibili, podcasts, transcripts | [references/video.md](references/video.md) |

Read only the reference for the selected class.

## Workflow

1. Classify the information need and list the material claims that the acquisition must establish.
2. Assign each claim the smallest suitable source class. Add platforms only when they contribute distinct evidence.
3. Build a structured acquisition brief with the fields below, including claim coverage in acceptance when it matters.
4. Submit the brief to the resource layer and treat the request as in progress until its receipt is terminal and consumed.
5. If a lane returns no usable evidence, refine once when useful and then degrade forward through the configured route. A later successful owner result must be attached to and consumed by the same request.
6. Return acquired evidence, uncovered claims, source coverage, and bounded uncertainty to the calling task.

## Research Completeness

- Judge completion by material-claim coverage, not candidate count or transport success.
- Keep these outcomes distinct: `no_results`, `not_publicly_documented`, `unsupported`, and `unavailable_in_current_environment`.
- Do not infer product support or lack of support from one documentation index. When the claim concerns current installed behavior, combine public documentation with the current implementation/schema and verified runtime evidence.
- For current software behavior, expand in a bounded order when official docs are insufficient: official docs, current official repository/release/issues, installed implementation or schema, then narrowly scoped community evidence.
- Empty content, metadata-only output, inaccessible snippets, or uncited summaries cannot satisfy a content request.
- Stop expanding when every material claim is supported or explicitly marked as bounded uncertainty with the exhausted source lanes recorded.

## Acquisition Brief

Provide fields that are known; do not invent missing constraints.

```yaml
intent: documentation_lookup | github_search | web_research | social_discovery | video_discovery | job_search
query: precise search question
keywords: [required terms]
source_classes: [search, dev]
preferred_domains: []
preferred_owner_tools: []
language: zh-CN | en | any
region: CN | global | any
freshness: current | recent | historical | any
result_count: 10
need_materialization: false
output: candidates | metadata | content | files
acceptance:
  relevance: high
  diversity: source-appropriate
  provenance_required: true
  material_claims: []
  claim_coverage_required: true
```

For a known URL, omit source discovery and hand the URL directly to the generic acquisition route.

## Refinement Rules

- `no_results`: simplify or translate keywords, then retry the same request.
- `not_publicly_documented`: inspect the current official implementation/repository when the claim is implementation-sensitive; otherwise return bounded uncertainty.
- `low_relevance`: narrow terms, domains, resource type, or owner tool.
- `blocked_or_paywalled`: request metadata or an alternative lawful source.
- `owner_handoff_required`: call the requested owner tool and attach the result; do not open an independent replacement search.
- `terminal_resource_failure`: follow the configured Codex direct-network priority chain from the released stage.

## Output Contract

Return:

- selected source class and why it fits;
- the structured acquisition brief or refinement applied;
- request/receipt status and source coverage;
- material claims covered and still unresolved;
- the next content-processing owner after acquisition.
