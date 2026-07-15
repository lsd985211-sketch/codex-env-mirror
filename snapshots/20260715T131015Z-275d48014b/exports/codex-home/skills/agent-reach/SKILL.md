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

1. Classify the information need and decide whether source selection is actually required.
2. Choose the smallest source set that can answer the question. Add multiple platforms only when they contribute distinct evidence.
3. Build a structured acquisition brief with the fields below.
4. Submit the brief to the resource layer and treat the request as in progress until its receipt is terminal and consumed.
5. If results are weak, refine the same request first: tighten keywords, language, domains, time range, result type, or owner preference.
6. Return acquired evidence and source coverage to the calling task.

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
```

For a known URL, omit source discovery and hand the URL directly to the generic acquisition route.

## Refinement Rules

- `no_results`: simplify or translate keywords, then retry the same request.
- `low_relevance`: narrow terms, domains, resource type, or owner tool.
- `blocked_or_paywalled`: request metadata or an alternative lawful source.
- `owner_handoff_required`: call the requested owner tool and attach the result; do not open an independent replacement search.
- `terminal_resource_failure`: follow the configured Codex direct-network priority chain from the released stage.

## Output Contract

Return:

- selected source class and why it fits;
- the structured acquisition brief or refinement applied;
- request/receipt status and source coverage;
- the next content-processing owner after acquisition.
