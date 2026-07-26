# Search Source Selection

Use this reference when the task needs general discovery or multiple independent sources. This file describes source choices; the workspace resource layer executes acquisition.

## Selection

| Need | Preferred source class | Notes |
|---|---|---|
| Current general information | reputable web/search owners | Require dates and provenance |
| Technical implementation evidence | official docs, primary repositories | Prefer primary sources |
| Chinese-language ecosystem evidence | Chinese search/platform owners | Keep region and language explicit |
| Community experience | Reddit, V2EX, social owners | Treat as experience, not authority |
| Cross-source research | two or more distinct classes | Add sources only when they reduce uncertainty |

## Structured Fields

Use precise `query`, `keywords`, `language`, `region`, `freshness`, `preferred_domains`, `preferred_owner_tools`, and `result_count` fields. Request `candidates` before full materialization when source quality is uncertain.

## Refinement

- Low relevance: add required terms or restrict domains.
- No results: simplify terms, translate once, or broaden one constraint.
- Duplicate results: request domain diversity.
- Stale results: set an explicit freshness window.
- Owner handoff: attach the owner result to the same resource request.

Do not call Exa, generic web, curl, or another search backend directly as the default path. Those are implementation choices owned by the resource and network layers unless the resource request has terminally released control.
