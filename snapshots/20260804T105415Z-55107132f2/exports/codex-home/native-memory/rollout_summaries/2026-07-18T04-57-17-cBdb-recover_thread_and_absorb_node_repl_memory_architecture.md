thread_id: 019f7395-ff2b-7cc3-99dc-4ca80576a2c5
updated_at: 2026-07-24T08:27:25+00:00
rollout_path: /home/codexlab/.codex-app/sessions/2026/07/18/rollout-2026-07-18T12-57-17-019f7395-ff2b-7cc3-99dc-4ca80576a2c5.jsonl
cwd: /home/codexlab/work/codex-workspace
git_branch: main

# Recovered historical Codex context and extracted durable lessons

Rollout context: The user asked to query thread `019f1c72-03c3-7032-aa56-dff625d7c720` and absorb valuable content. The normal thread index returned no match and the thread API initially rejected arguments, but direct read-only inspection of the local session JSONL recovered the history. No secrets are retained.

## Task 1: Recover historical thread and diagnose resume failure

Outcome: success

Preference signals:
- The recovered thread shows the user wants careful validation and explicitly asked not to be made to repeat trial-and-error: "仔细检查，不要让我频繁试错". Future troubleshooting should validate the real startup contract before asking for another restart or environment switch.
- The user prefers concise Chinese updates, exact evidence, and clear separation of confirmed root cause versus hypothesis.

Key steps:
- `codex_app__list_threads({query:<thread-id>})` returned `threads: []`.
- `codex_app__read_thread` with extra arguments returned `invalid arguments`; later direct local JSONL inspection recovered the full historical rollout.
- The historical `node_repl` failure was traced to shared configuration being consumed by a WSL app-server while containing a Windows absolute executable path.
- The implemented stabilization changed the command to `node_repl.exe`, installed a stable user-level entry at `C:\Users\45543\.local\bin\node_repl.exe`, and marked the command runtime-derived so ordinary configuration projection would not repeatedly rewrite it.
- Validation included Windows and WSL command execution, shared `CODEX_HOME` configuration parsing, MCP listing, an actual stdio `initialize` handshake returning `serverInfo: rmcp 1.5.0`, projection validation, and 20 regression tests.

Failures and how to do differently:
- The historical final claim was stronger than the evidence: the handshake simulation passed, but the user later reported the error after a real switch/restart. Treat it as startup-contract validation, not end-to-end desktop-switch proof.
- Fix the projection/source owner, not only the generated `config.toml`.

Reusable knowledge:
- Runtime identity must be checked independently from desktop UI identity and filesystem mount paths.
- A command-help test does not prove MCP compatibility; use the real stdio handshake.

## Task 2: Memory architecture and Codex/Reasonix separation

Outcome: success

The recovered thread established a durable architecture: `project-kb` stores long evidence, `vector-memory` stores short stable conclusions, and `memory-graph` stores structured entities/relations/observations. Codex and Reasonix are isolated at the storage layer with separate vector directories and graph SQLite databases; cross-agent reads must be explicit. The old shared Chroma store remains read-only. The user prefers stdio-only operation and asked to stop unsolicited bridge-status interaction.

## Task 3: Windows/CC Switch and GPU diagnostic findings

Outcome: partial

The recovered diagnostics confirmed that CC Switch had a WebView2 policy injection of `--disable-gpu`, which caused excess WebView CPU. Removing the policy, rebuilding the corrupted SQLite database with equal row counts and `quick_check=ok`, stopping the full process tree, and restarting one instance reduced CC Switch CPU and preserved proxy port `15721`.

WPS, GameViewer service, and the GameViewer virtual display were separately excluded as the main RTX cause. RTX remained around `P0 / 1425 / 7001 MHz / 28 W`, even after those A/B changes, leaving the NVIDIA 610.62 graphics stack or another host-level driver/display interaction as the unresolved likely cause. Device-level virtual-display mutation interrupted the active Codex GUI, so future tests must use an external receipt and avoid modifying display devices during the active session.

