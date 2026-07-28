# WSL Declarative Workspace

This repository is the platform-neutral declarative working authority rebuilt
from a validated `codex-env-mirror` snapshot. It is not a runtime backup and it
does not replace Windows host capabilities.

## Authority and direction

Initial bootstrap and normal operation have different directions. A validated
mirror may seed a new Work Git repository once, but it is never the daily edit
authority after cutover.

```text
one-time bootstrap or explicit restore:
codex-env-mirror -> Work Git

normal operation:
WSL worktree -> commit -> Windows bare Git -> owner validation -> closeout -> codex-env-mirror
```

The WSL worktree is the daily executable authority and the Windows bare Git
repository is its durable object/history store. The mirror is a derived,
validated recovery and release product. It never automatically overwrites the
Work Git repository, and a refresh must not recursively publish itself.
The worktree and `.git` storage are owned by the normal `codexlab` account;
system-level operations use an explicit root owner instead of running daily
Codex work as root.

Codex Desktop stores the project as the Windows-visible UNC Git root
`\\wsl.localhost\Codex-Wsl-Lab\home\codexlab\work\codex-workspace` while
Linux owners use `/home/codexlab/work/codex-workspace`. Registration must go
through the Desktop IPC owner; editing `.codex-global-state.json` while Desktop
is running is not a durable registration path.

## Platform projections

The repository stores semantics and templates, not host runtime state. Each
platform supplies:

- `WORKTREE_ROOT`
- `WORKSPACE_ROOT`
- `CODEX_HOME`
- `AGENT_HOME`
- `CC_SWITCH_HOME`

Windows and WSL generate separate Codex/MCP projections. Windows-only GUI,
Office, browser, Weixin, CC Switch, and desktop-session owners remain on the
Windows host. WSL uses Linux-native commands and the Hub/Windows bridge for
host-owned capabilities.

The former Windows workspace is therefore a host compatibility projection,
not a source authority. Existing Windows paths remain valid where a Windows
executable, mutable runtime database, device session, or GUI owner requires
them. The projection owner may update those files from committed Work Git and
host-specific templates; ambient Windows state must not be reverse-imported
into Work Git.

## Bootstrap contract

Run `workspace/_bridge/bootstrap_wsl_workspace.py --json --write-receipt` after
cloning. Bootstrap is validation-first and records a receipt under ignored
runtime state. It must report:

- required source files and hashes;
- platform paths and tool versions;
- Git branch and cleanliness;
- whether activation or host runtime import occurred.

Bootstrap does not activate host configuration, import secrets, share writable
SQLite/session/cache state, or change the default WSL distribution.

## Runtime exclusion

Generated indexes, `__pycache__`, locks, logs, sessions, caches, SQLite files,
and platform credentials stay outside the declarative Git authority. They are
recreated by their owner during bootstrap or maintenance.

## Work Git change sets

`main` is the integration worktree, not a shared scratch directory. A simple
single task may edit it directly while clean. When `main` is already dirty or
multiple tasks run concurrently, create `codex/task/<task-id>` through
`workspace/_bridge/work_git_change_owner.py start`; the owner places the task
worktree under external Codex runtime state and leaves existing main changes
untouched.

Commits declare their exact changed paths. Foreign staged changes block the
commit; foreign unstaged changes remain untouched and are listed in the
receipt. Task integration is fast-forward only, refuses overlap with dirty
main paths, preserves the task branch/worktree for rollback, and synchronizes
only to the Windows local bare Git repository. Conflict resolution, branch
cleanup, mirror publication, and GitHub publication remain explicit separate
operations.

Repository configuration enables pruning, fast-forward-only pulls, explicit
push semantics, rerere without autoupdate, untracked-cache acceleration, and
commit-graph generation. The Windows bare repository rejects deletes and
non-fast-forward pushes and keeps reflogs. Built-in fsmonitor stays disabled
because the current WSL Git reports it unsupported.

## Acceptance gates

Before considering a WSL worktree active, run the bootstrap validator, the
platform projection tests, the maintenance capability snapshot, the MCP route
snapshot, and the smallest applicable owner validators. Only after validation
does the owner produce a closeout receipt or a mirror candidate.

## WSL Codex app-server (optional)

The WSL-native app-server is a separate, user-scoped execution route for
Linux-side clients. Its sole owner is
`workspace/_bridge/wsl_codex_app_server.py`; it does not replace the Windows
Desktop/CDP route or share its sessions, credentials, databases, or caches.

- The service runs as the normal WSL user through `systemctl --user`.
- The unit prefers `/usr/bin/codex`, uses an isolated
  `CODEX_HOME=/home/codexlab/.codex-app`, and binds only the user-runtime Unix
  socket `unix:///run/user/1000/codex-app-server.sock`.
- Inspect with `python3 workspace/_bridge/wsl_codex_app_server.py plan|status|validate`.
- Install only with explicit confirmation:
  `python3 workspace/_bridge/wsl_codex_app_server.py install --confirm INSTALL-CODEX-APP-SERVER`.
- Stop with `python3 workspace/_bridge/wsl_codex_app_server.py stop`.

The service remains subject to WSL lifecycle and can stop when the WSL VM is
terminated. A healthy unit is not evidence that a remote MCP, Desktop session,
or Windows-only owner is available.

## Windows execution plane

WSL is the declarative control plane; Windows is a host-owned execution plane
for GUI, credentials, hardware and other Windows-only capabilities. This does
not make Linux `root` a Windows administrator, and it cannot keep running after
Windows terminates the WSL VM. It does keep core code, policy, indexes and
Linux services independent of Codex Desktop and its user-interface lifecycle.

`workspace/_bridge/windows_execution_agent.py` is the narrow bridge between
those planes. It is deliberately a one-shot typed dispatcher rather than a
resident generic elevated service:

- the default lane runs under the normal Windows user with `RunLevel=Limited`;
- elevated tasks must be fixed, separately owned and carry a concrete reason;
- callers provide only a catalogued operation ID, never a command string or
  free-form arguments;
- task-start acceptance is not business completion; the named business owner
  must still provide and consume its result receipt;
- periodic validation and retries use the existing `CodexSchedulerRunner`;
  the agent creates no second timer, loop or Windows scheduled task;
- Windows tasks may use the generated compatibility projection but cannot
  reverse-write Work Git or WSL configuration.

Inspect with `python3 workspace/_bridge/windows_execution_agent.py
status|validate|capabilities`. Plan an action with `invoke-plan --operation
<id>` and execute only with the exact confirmation returned by that plan. If a
future latency requirement justifies a resident IPC service, it must preserve
this same typed catalog and use an explicit Windows DACL plus client identity,
message bounds and replay protection; a default named-pipe ACL or TCP listener
is not an acceptable shortcut.

The boundary follows Microsoft's platform contracts: WSL systemd services do
not keep the WSL instance alive, WSL accesses Windows resources through the
launching Windows user's permissions, Task Scheduler distinguishes least and
highest run levels, and a default named-pipe security descriptor grants broad
read access. See [WSL systemd](https://learn.microsoft.com/windows/wsl/systemd),
[WSL enterprise security](https://learn.microsoft.com/windows/wsl/enterprise#windows-file-system-access),
[Task Scheduler run levels](https://learn.microsoft.com/windows/win32/taskschd/principal-runlevel),
and [named-pipe security](https://learn.microsoft.com/windows/win32/ipc/named-pipe-security-and-access-rights).
