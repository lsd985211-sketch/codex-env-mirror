# GitHub Source Selection

Use this reference for GitHub repository, code, issue, pull-request, release, or project discovery. The resource layer owns the request; GitHub MCP or the Hub GitHub adapter is the preferred owner execution surface.

## Request Types

| Need | Structured intent | Expected result |
|---|---|---|
| Find repositories | `github_search` | ranked repository candidates |
| Find exact symbols or patterns | `github_code_search` | file and repository matches |
| Inspect one repository | `github_repository_read` | README, tree, files, releases |
| Investigate issues or pull requests | `github_issue_research` | matching threads and metadata |
| Download or clone | `github_materialize` | approval-aware local artifact |

## Required Fields

```yaml
intent: github_search
query: exact repository or code need
keywords: []
owner_or_org: optional
language: optional
min_stars: optional
updated_after: optional
include_archived: false
result_count: 10
output: candidates
```

## Owner Priority

1. Submit the structured request to the resource layer.
2. Let it invoke GitHub MCP or the Hub GitHub adapter when supported.
3. If it returns `handoff_required`, perform the requested GitHub owner call and attach the normalized result to the same request.
4. Use local `git` only for an already materialized local repository.
5. Use `gh` only when the configured GitHub owner route selects it or the higher owner surfaces are unavailable at that stage.

Do not make Codex search GitHub first and then pass already discovered results back to the resource layer. Source discovery is part of the resource request.

## Refinement

- Too broad: add language, organization, topic, minimum activity, or exact symbols.
- Low relevance: distinguish repository search from code search.
- Missing repository: verify owner/name before falling back.
- Private target: report authentication/permission requirements; do not substitute unrelated public results.
