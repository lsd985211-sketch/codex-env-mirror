---
name: graphify-ops
description: "Use for local Graphify knowledge-graph work over a scoped mix of code, documentation, papers, or media transcripts; graph-guided review, affected-flow analysis, or persistent cross-artifact exploration. Keep Hub-first CodeGraph as the default for ordinary source symbols, callers, callees, and impact analysis."
---

# Graphify Ops

Use the reviewed WSL-local Graphify package through Hub MCP first. Its direct
CLI is reserved for graph creation and maintenance:

```text
/home/codexlab/.local/share/codex-resource-dependencies/node/graphify/0.17.1/node_modules/.bin/graphify
```

It is an on-demand local analysis tool, not a replacement for Hub-first
CodeGraph. The installed package is `@sentropic/graphify@0.17.1` (MIT) in an
isolated user-local prefix; do not use `npx`, global npm installs, or a moving
`latest` version.

## Select The Graph Tool

- Use CodeGraph first for ordinary source structure, symbols, call paths, and
  targeted change impact inside the current repository.
- Use Graphify when the analysis needs a durable graph across mixed code and
  non-code inputs, graph-guided review/flow commands, or a separately managed
  corpus whose generated state is useful beyond one query.
- Treat either graph as static evidence. Verify runtime conclusions through the
  relevant owner, logs, state, or tests.

## Workflow

1. Confirm the corpus and output location. Run `graphify scope inspect <path>`
   before extraction when the input scope is uncertain.
2. Treat `extract` and `build` as state-producing commands. Their default
   output is `.graphify`; direct it outside an active source tree when the
   generated graph is only temporary, or add an explicit ignore/retention plan
   before creating it in a repository.
3. Use `extract <inputPath> --out <output-root> --scope tracked` for a
   code-only initial graph. Keep `--description-mode assistant` and
   `--label-mode assistant` unless an explicit task permits an external model.
4. Query an existing managed graph through Hub `graphify.list_tools` then
   `graphify.call`. The Hub starts a fresh upstream MCP session and forwards
   only the pinned version's query-only allowlist. Use `first_hop_summary` for
   orientation, `query_graph` for bounded traversal, `review_delta` or
   `review_analysis` for changed files, `get_node`/`get_neighbors` for focused
   expansion, and `graph_stats` for graph health. Pass only files relevant to
   the task.
5. Reuse a graph when its corpus signature, Graphify version, and validation
   receipt are unchanged. Do not repeat extraction, build, `list_tools`, or
   graph-wide statistics for reassurance; run the first invalidated phase and
   then only the affected review queries.
6. Validate output with `check-update`, `portable-check`, and the owning task's
   normal test or doctor. A portability finding in `source_file`, `file_path`,
   or exported path data blocks completion. A finding only in a natural-language
   `label` that contains a slash command such as `/ask` is an upstream
   false-positive candidate: verify that the source path fields are relative,
   retain the original label, and record the tool finding rather than rewriting
   source semantics. Do not infer runtime truth from Graphify output.

When Codex has not refreshed the newly exposed dotted Hub aliases, call the
same aliases through Hub `hub.call` with its required acknowledgement. That is
the MCP-preserving dynamic wrapper, not a CLI fallback.

## Safety Boundaries

- Do not run `graphify install`, `graphify codex install`, `uninstall`, `hook`,
  or `watch` unless the user explicitly requests the resulting configuration,
  hooks, or persistent process. The managed Hub adapter owns short-lived
  `serve` MCP sessions.
- Do not use `extract --backend`, `--description-mode direct`, or
  `--label-mode direct` without an explicit external-model and credential
  decision.
- Keep CodeGraph's Hub-first route unchanged for source symbols and call paths.
  Graphify is the Hub-MCP option for managed mixed-artifact graphs.
- Update this package only through the resource package owner after checking
  the official npm version, license, install scripts, and rollback target.
