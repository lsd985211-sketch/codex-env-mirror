---
name: gitnexus-ops
description: "Use for GitNexus knowledge-graph analysis of local repositories: semantic or hybrid code-flow search, 360-degree symbol context, graph trace, impact, diff-to-flow analysis, and cross-repository groups. Use Hub MCP first; retain CodeGraph as the default for ordinary source symbols, callers, callees, and narrow impact queries."
---

# GitNexus Ops

Use the managed WSL-local `gitnexus@1.6.9` package through Hub MCP first:

```text
gitnexus.list_tools
gitnexus.call
```

Each Hub call starts and closes a fresh upstream stdio session. It forwards
only tools GitNexus declares read-only, so `rename`, setup, hooks, and other
state-changing operations remain unavailable through this route.

## Select The Graph Tool

- Use Hub `codegraph.explore` first for symbol lookup, callers, callees, and
  narrow source-only change impact in the current repository.
- Use GitNexus MCP for semantic or hybrid code-flow search, `context`, `trace`,
  `detect_changes`, bounded Cypher, code-shape checks, and cross-repository
  group analysis.
- Use Graphify MCP for a managed graph spanning code plus documents, papers,
  or other non-code artifacts.
- Treat all graph results as static evidence and verify runtime claims with the
  owning test, log, state, or diagnostic tool.

## Workflow

1. At first use after an install/update, call `gitnexus.list_tools` to inspect
   the installed upstream catalog. Reuse the bounded catalog result during the
   task instead of starting an extra discovery session for every query.
2. If the target repository has no index, run the pinned CLI explicitly from
   its Git root: `gitnexus analyze --index-only`. This creates only ignored
   `.gitnexus` state and the local registry; it must not run `setup`,
   `--skills`, embeddings, or a PDG build unless separately requested.
3. Call `gitnexus.call` with a read-only tool and a bounded argument payload.
   Pass `repo` when more than one repository is registered; for uncommitted
   worktree review use `detect_changes` with that worktree path.
4. Start with `query`, then narrow with `context`, `trace`, `impact`, or
   `detect_changes`. Use `cypher` only after reading the GitNexus schema and
   keep statements bounded.
5. Reuse the indexed-repository signature and prior graph result while they
   remain current. Do not repeat `list_tools`, `analyze`, or the same query for
   reassurance; invalidate only on source/index-version drift or failed graph
   health. Batch independent read-only questions when the owner supports one
   bounded call.

When Codex has not refreshed the newly exposed dotted Hub aliases, call the
same aliases through Hub `hub.call` with its required acknowledgement. That is
the MCP-preserving dynamic wrapper, not a CLI fallback.

## Boundaries

- Do not run `gitnexus setup -c codex`, `uninstall`, `rename`, `clean`,
  `remove`, `publish`, `serve --http`, or any hook/configuration command
  without explicit user approval.
- Keep embedding generation, external embedding endpoints, `--skills`, and
  `--pdg` opt-in. They materially change local state, compute cost, or source
  context.
- Update the package only through the resource package owner after verifying
  its version, license authorization, postinstall behavior, and rollback path.
