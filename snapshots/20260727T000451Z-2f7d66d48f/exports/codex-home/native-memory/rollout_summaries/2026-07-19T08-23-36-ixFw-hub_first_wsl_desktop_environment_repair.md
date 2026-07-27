thread_id: 019f7979-3c05-72b0-b3b7-8c1c6e5b0ed2
updated_at: 2026-07-25T02:26:39+00:00
rollout_path: /home/codexlab/.codex-app/sessions/2026/07/19/rollout-2026-07-19T16-23-36-019f7979-3c05-72b0-b3b7-8c1c6e5b0ed2.jsonl
cwd: /home/codexlab/work/codex-workspace
git_branch: main

# Hub-first environment audit exposed WSL/Desktop state drift and a partial repair

Rollout context: The user first requested a read-only workspace/dependency assessment, then challenged an incorrect diagnosis that treated local fallback gaps as primary failures, and finally approved repair of PMB/CodeGraph workspace integration and Desktop state. The work occurred in `/home/codexlab/work/codex-workspace` under WSL2, with concurrent ownership of several launcher/startup source files.

## Task 1: Workspace health and dependency audit

Outcome: success

Preference signals:

- The user asked for read-only assessment and no installation. Similar audits should classify core blockers versus optional/task-specific dependencies and avoid writes.
- The initial dependency report found missing local `uv`, `uvx`, `ruff`, `fd`, CodeGraph CLI, and optional media/document tools, while core Git/Python/Node/rg and Windows bridges were available. FFmpeg/FFprobe were later proven available through the Windows WinGet links and should not be reported missing merely because they are absent from WSL PATH.

Key steps:

- Verified WSL2, Python 3.14.4, Node 22.23.1, Git 2.53.0, clean `main` branch, synchronized upstream, healthy WSL Work Git/bare-repo relationship, registered Desktop project, and ample disk/memory.
- Used `code_maintainability.py toolchain`, runtime cache checks, `pip check`, package inspection, audio toolkit validation, CodeGraph health, and MCP session validation to separate local PATH gaps from bundled/Hub capabilities.
- Closeout for the audit returned `outcome=ok`, with no pending review cards and no source changes from that audit.

Failures and how to do differently:

- The first report incorrectly said PMB and CodeGraph were unavailable because it inspected local fallback state before trying Hub. The user explicitly corrected this with "你不是应该通过hub调用吗". Future reports must validate direct Hub owner tools first.
- Avoid broad recursive scans over generated/resource trees; start at owner files and exclude runtime/cache/browser/profile/generated paths.

Reusable knowledge:

- Workspace lifecycle owner: `workspace/_bridge/wsl_workspace_owner.py`; PMB owner: `_bridge/local_pmb_memory.py`; CodeGraph runtime: `_bridge/codegraph_query_runtime.py`.
- Hub capability validation returned `ok=true`, `tool_count=81`.

References:

- `python3 workspace/_bridge/wsl_workspace_owner.py validate`
- `python3 workspace/_bridge/code_maintainability.py toolchain`
- `python3 workspace/_bridge/audio_toolkit/audio_toolkit.py validate`
- `python3 workspace/_bridge/codex_workflow_entry.py closeout --task-kind environment_dependency_audit --selected global-framework,mcp-builder --used global-framework,mcp-builder --outcome ok`

## Task 2: Hub-first PMB and CodeGraph diagnosis

Outcome: partial

Preference signals:

- After the user correction, the agent directly called Hub `hub.validate`, `codegraph.explore`, `pmb.prepare`, and `pmb.stats`. This is the expected route for future Hub-managed capabilities.
- The user later asked to repair the exposed problems, but also required stopping writes on files concurrently owned by another thread. Preserve this concurrency boundary.

Key steps:

- Hub itself validated successfully.
- PMB calls succeeded, proving PMB capability was available through Hub. PMB was bound to the old Windows `mcsmanager` workspace, not the current WSL Work Git workspace.
- CodeGraph via Hub failed for the explicit WSL path because the path was projected to `C:\home\...` and had no current index. The default Hub CodeGraph path could query a valid old Windows index, but that was the wrong scope.
- Maintenance mapping located existing owners rather than creating a parallel module. The planned repair correctly focused on PMB workspace identity and CodeGraph WSL path/index integration, not immediate installation of duplicate local stacks.

Failures and how to do differently:

- A `workflow.route_pack_missing` response was observed; the CLI workflow plan was used as bounded fallback and the route failure should remain explicit.
- Do not equate local `pmb.exe`/npm package absence with Hub capability failure. If Hub succeeds, diagnose workspace binding or index scope instead.
- CodeGraph acceptance requires target coverage and exclusion compliance; valid old-index output contaminated with excluded paths is not acceptable evidence for the WSL workspace.

Reusable knowledge:

- Direct Hub PMB evidence: `pmb.prepare` and `pmb.stats` returned `ok=true`; PMB stats showed 523 active events/search entries.
- Direct Hub CodeGraph evidence: `reason=codegraph_index_unusable` for the projected `C:\\home\\codexlab...` path; default old Windows index was usable but out of scope.

References:

- Hub tools: `mcp__local_mcp_hub__hub_validate`, `mcp__local_mcp_hub__pmb_prepare`, `mcp__local_mcp_hub__pmb_stats`, `mcp__local_mcp_hub__codegraph_explore`.
- Candidate source files: `workspace/_bridge/local_mcp_hub.py`, `local_mcp_hub_pmb_runtime.py`, `local_pmb_memory.py`, `codegraph_query_runtime.py`, `platform_paths.py`.

## Task 3: Desktop active-state and CDP metadata repair

Outcome: partial

Preference signals:

- The user accepted continued investigation and expected current state to be separated from stale historical receipts.
- The user’s delegated concurrency warning explicitly required pausing writes to overlapping startup/WSL owner source files. This was honored after the warning.

Key steps:

- `desktop-project-apply --confirm REGISTER-WSL-DESKTOP-PROJECT` rewrote active Desktop state so `selected-project` pointed to WSL project ID `7729bf8c-a9a4-42e4-b89a-6264ae14dfa8` and `active-workspace-roots` pointed to `\\wsl.localhost\Codex-Wsl-Lab\home\codexlab\work\codex-workspace`.
- Backed up and deleted stale `codex-desktop-restart-receipt.json` (`relaunch_pending`, `desktop_exited_gracefully`); restart status then became `idle`.
- Verified live Desktop on 9229 and no 9231 listener at the time; backed up and rewrote `codex-cdp-port.txt` to `9229` without BOM. The independent helper later rewrote it with BOM, establishing that `codex-cdp-port.ps1` owns persistent writes.
- Desktop project status remained `registered_and_git_ready`; the 74-test WSL owner/startup suite passed.

Failures and how to do differently:

- Registration alone is insufficient: the global active selection had previously reverted to a remote/old Windows project while the owner still reported the WSL project as registered. Always inspect `selected-project` and `active-workspace-roots`.
- The source-level launcher issue remained unresolved. The launcher/helper/runtime ecosystem had a 9229-versus-9231 split, and the stale cleanup path produced `cleanup_requires_explicit_opt_in`. A shortcut-only 9231 override would be a temporary workaround and risks recreating a dual-port world.
- Final workspace validation remained blocked by four dirty source files and stale host compatibility projection. Do not claim full release readiness or final source repair.

Reusable knowledge:

- Runtime-state repair can be done safely with `backup_router` without touching overlapping source files.
- The final converged fix must choose one CDP authority and update helper, launcher, runtime discovery, state persistence, and shortcut/plan-task semantics consistently. The evidence in this rollout favored the live 9229 instance, but the concurrent thread proposed 9231 for the elevated shortcut; this conflict was not resolved.

References:

- Active state after repair: `selected-project` local WSL project ID and WSL UNC active root.
- Stale receipt backup manifest was created under `/mnt/c/Users/45543/.codex/backups/202607/codex-desktop-restart/...`.
- CDP state helper: `/mnt/c/Users/45543/.codex/scripts/codex-cdp-port.ps1`; persistent state: `/mnt/c/Users/45543/.codex/state/codex-cdp-port.txt`.
- Live evidence: `cmd.exe /c "netstat -ano | findstr :9229"` showed PID 11160 `ChatGPT.exe`; `:9231` had no listener.
- Concurrently modified paths: `codex-home/scripts/start-codex-desktop-elevated.ps1`, `workspace/_bridge/codex_startup_chain_tests.py`, `workspace/_bridge/wsl_workspace_owner.py`, `workspace/_bridge/wsl_workspace_owner_tests.py`.
