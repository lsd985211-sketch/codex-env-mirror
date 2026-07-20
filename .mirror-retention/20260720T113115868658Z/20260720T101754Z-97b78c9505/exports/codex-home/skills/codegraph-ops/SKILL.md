---
name: codegraph-ops
description: "Use when Codex needs code-structure discovery, symbol lookup, call graph analysis, impact/blast-radius analysis, or efficient source context from an indexed repository. Prefer for questions like how a module works, where a class/function is defined, who calls what, what changes could affect, or before editing shared code."
---

# CodeGraph Ops

## Framework Layer

- Primary layer: execution
- Purpose: execute code-graph queries and use the returned source/relationships as already-read context.

## Role Boundaries

- Own CodeGraph query execution through MCP or the project-local CLI.
- Own interpreting returned symbols, source blocks, call paths, callers, callees, and impact results.
- Hand runtime-state questions back to the relevant diagnostic workflow after CodeGraph identifies candidate code paths.
- Hand file modifications back to the normal project edit workflow, including approval and backups.

## Operating Rules

- Resolve execution affinity before calling CodeGraph. In this workspace CodeGraph is `hub_first`: start at Hub `codegraph.explore`, then continue forward through the generated CodeGraph fallback chain after a real failure.
- Use CodeGraph before broad grep/read exploration when the question is about structure, calls, or impact.
- Use runtime evidence after CodeGraph for stateful behavior; static graph output is not proof of runtime behavior.
- Keep re-indexing and MCP/global install changes behind explicit approval unless the user has already approved that specific operation.
- If Hub `codegraph.explore` fails, keep Hub availability, native current-turn callability, and project CLI health separate. Continue only forward through the generated fallback chain; do not jump backward to native or repeat an exhausted stage.
- If an earlier CodeGraph response reported auto-sync disabled or query drift,
  do not carry that diagnosis forward blindly. Re-check current backend state
  with `mcp-session smoke --profile codegraph` or the project-local CLI. A
  smoke result that lists `codegraph_explore` and reports watcher active
  supersedes the old auto-sync-disabled observation for the current task. Still
  prefer tight file-path and symbol anchors, and keep correctness validation in
  the owning test/doctor path.
- For this workspace's mobile bridge refactors, do not treat generic CodeGraph
  relevance as enough. Run `python _bridge\codegraph_health.py validate --json`
  and require the bridge worker relevance smokes to pass, or use local
  AST/targeted reads for that edit while keeping CodeGraph drift as a separate
  maintenance item.

## Graph Tool Selection

- Use Hub `codegraph.explore` for narrow, source-only questions: exact symbols,
  callers, callees, source blocks, and bounded local blast radius.
- Hand semantic or hybrid code-flow search, 360-degree symbol context, traces,
  diff impact, and cross-repository analysis to Hub `gitnexus.list_tools` then
  `gitnexus.call` through `gitnexus-ops`.
- Hand managed mixed-artifact graphs, graph-guided review, `review_delta`, and
  `review_analysis` to Hub `graphify.list_tools` then `graphify.call` through
  `graphify-ops`.
- All three routes are MCP-first. A Hub `hub.call` wrapper is still Hub-first
  when the active Codex turn has stale tool metadata; it does not grant a
  fallback permission or skip the read-only adapter boundary.
- Reuse the current task's tool catalog, graph health, and successful query
  slice instead of repeating discovery, sync, or the same graph query.
  Re-index only after a source/ignore signature change or a graph validator
  proves the existing index stale.

## Query Sequence

When the question is structural, prefer this order:

1. Find the symbol or module.
2. Read the source blocks returned by the graph.
3. Expand callers, callees, and impact only as needed.
4. Cross-check runtime logs or state if the answer depends on execution.

Use the smallest useful graph slice first. Expand only when the answer is still ambiguous.

## Preflight
- Confirm the skill matches the task before using it.
- Keep the scope tight and avoid unrelated changes.

## Output Contract

- Return the discovered structure, then the practical implication.
- Mark static inferences as inferences.
- Do not treat graph output as proof for queue state, GUI state, or process state.

## Primary Route

Use the classified Hub route first:

```text
mcp__local_mcp_hub.codegraph_explore
```

The native `mcp__codegraph.codegraph_explore` tool is used when the generated
priority selects a native entry stage or the Hub-first stage has failed and the
generated forward chain selects native next.

For this workspace, pass:

```text
projectPath = /home/codexlab/work/codex-workspace/workspace
```

The Hub runs on Windows and translates that Linux path to the matching WSL UNC
path. Omitting `projectPath` is also supported and selects the current WSL Work
Git `workspace/` root. Do not route this workspace through the retired Windows
`mcsmanager` checkout; that directory is only a generated compatibility
projection for Windows-only executables.

Use `codegraph_explore` before grep/read loops for:

- architecture or flow questions
- symbol/file discovery
- call path questions
- impact analysis before edits
- surveying a module with many large files

Treat returned source blocks as already read. Do not reopen the same files unless the returned block is trimmed, stale, or insufficient.

## CLI Fallback

If both Hub and native CodeGraph are unavailable or insufficient, use the
project runtime owner from the WSL Work Git checkout:

```bash
python3 workspace/_bridge/codegraph_query_runtime.py \
  --project-path /home/codexlab/work/codex-workspace/workspace
```

On Windows the Hub owner detects the WSL UNC checkout and invokes the lockfile-
pinned Linux CodeGraph through hidden `wsl.exe`; source, SQLite index, and file
locking therefore stay on the WSL filesystem. The index remains in the Work Git
workspace's ignored `.codegraph/`. Native Windows CodeGraph is retained only
for actual Windows-local projects. Do not put a SQLite CodeGraph index on a WSL
UNC path or invoke a `.cmd` wrapper with a UNC working directory.

The raw CLI examples below apply only when operating an already validated local
CodeGraph installation:

```powershell
& $cg status . --json
& $cg query ClientModLoader --limit 5 --json
& $cg explore "How does mobile message delivery work?"
& $cg node ClientModLoader
& $cg node "_bridge/mobile_openclaw_bridge/mobile_bridge_mcp_server.py" --file --limit 120
& $cg callers task_batch_runtime --json
& $cg callees task_batch_runtime --json
& $cg impact task_batch_runtime --depth 2 --json
```

## Maintenance Checks

Use the project health wrapper for status and validation:

```powershell
python _bridge\codegraph_health.py validate --json
python _bridge\codegraph_health.py metrics --json
python _bridge\codegraph_health.py doctor --json
```

Current expected healthy baseline for the WSL Work Git workspace:

- project-local CodeGraph CLI is available
- Codex config mentions the project-local CodeGraph MCP command
- `.codegraph/codegraph.db` integrity is `ok`
- journal mode is `wal`
- the current workspace index lives at `workspace/.codegraph/codegraph.db` and
  is opened only by WSL-native CodeGraph processes
- `codegraph_query_runtime.py` validates index usability before queries and MCP startup, then coalesces freshness status/sync through a per-project lock, cooldown, pending signature, and hidden background worker. Freshness uncertainty must not suppress an otherwise valid analysis result.

`doctor` may still report drift for retired indexes. Do not treat that as a
blocker when the current WSL workspace index and Hub query both validate.

## Boundaries

- CodeGraph is a static code graph, not runtime truth. For queues, DB state, GUI state, process state, network behavior, or audio issues, verify with logs, SQLite, process checks, GUI evidence, or runtime commands.
- Before modifying files in this workspace, follow project rules: ask for approval and create a marked backup.
- Do not run `codegraph install`, `uninstall`, `uninit`, or global npm changes unless explicitly approved.
- Re-index only when needed after changing ignore boundaries or when `status`/MCP reports stale or mismatched index state.
