v1

## User Profile

The user runs substantial Windows/PowerShell workflows around `mcsmanager`, a mobile OpenClaw/Weixin bridge, Codex environment governance, and Minecraft Fabric. They value durable artifacts, conservative operational changes, and proof from the actual runtime rather than inferred success. They also use Codex global skills and expect project-local guidance to be saved where later tasks can find it.

## User preferences

- For research, create a Markdown artifact with major-content citation links; when extending a list, analyze items individually and group them by category.
- Keep command output compact and decision-focused. Default output and `--full-output` must remain meaningfully distinct; full should be richer but bounded, not a raw dump.
- Before consequential edits, provide a read-only diagnosis/plan when requested, then make the smallest approved change and verify it.
- For risky cleanup, "判断幽灵配置一定需要谨慎，防止误删有用的配置": inventory/report first and do not silently remove live configuration.
- Prefer backup-before-edit, minimal regression-driven fixes, and real validation output; after "继续", carry the verification chain forward without making the user restate context.
- Do not ask for repeated trial-and-error during recovery. Preserve the user's selected runtime and distinguish smoke tests from actual end-to-end success.
- Store verified stable access patterns and reusable templates in durable/project-local Markdown when the user asks to record them.

## General Tips

- This environment uses Windows PowerShell: use `@' ... '@ | python -`, not Bash `python - <<'PY'` heredocs.
- In `_bridge`, discover ownership with `code_maintainability.py module-context`; after helper additions rebuild the module index, lint changed owners, and preserve facades.
- Treat active mirror freshness, live bridge state, and old-session locks as time-sensitive. Wait for closeout helpers to exit before final claims.
- For state/rollout edits, back up first, use transactional or atomic replacement, and verify the actual recovery workflow.

## What's in Memory

### Codex Desktop session recovery

#### 2026-07-18

- Old-thread resume repair: state_5.sqlite, node_repl.exe, malformed cwd, rollout JSONL
  - desc: Partial repair of a legacy Codex thread under `cwd=\\?\UNC\wsl.localhost\Codex-Wsl-Lab\`; search before changing persisted thread metadata.
  - learnings: Repair both SQLite and historical context; a real completed resume is the only success criterion.

### mcsmanager Windows release

#### 2026-07-17

- Research artifacts, FreeDomain, mirror milestone: awesome-selfhosted, FreeDomain-Cloudflare-DNS-初始化模板.md, seed-v2.3.1, system_membership
  - desc: Cited report creation, disposable Cloudflare public-entrypoint boundaries, and a published but not fully closed mirror milestone under `cwd=C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager`.
  - learnings: Recheck closeout for `main_task_complete: true`; never directly expose bridge, Codex, databases, or admin surfaces.
- CC Switch logging crash mitigation: cc-switch.db, log_config, forwarder.rs, 127.0.0.1:15721
  - desc: DB-backed mitigation for CC Switch logging-path exits; consult before changing proxy logging.
  - learnings: `proxy_config.enable_logging` is separate from global log level; validate DB integrity, unchanged routing, and listening port.

#### 2026-07-16

- Worker idle-backoff repair: worker_loop_has_activity, pending_reply_retries.skipped, STOP_REQUEST
  - desc: Narrow mobile bridge worker fix and paused-state validation.
  - learnings: Remove only skipped historical retries from activity; retain scheduled, processed, and busy-route signals.
- Bounded closeout output: bounded_output.py, full_bounded, --full-output, post_closeout_mirror
  - desc: Shared closeout projection contract and mirror verification path.
  - learnings: Preserve reason/action/finalization fields; raw payloads belong behind `record_path` or `raw_result_ref`.

### Older Memory Topics

#### C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager

- Validation-first bridge modularization: code_maintainability.py, build-module-index, workflow_plan_build_steps.py, supplement-fallback
  - desc: Safe `_bridge` helper extraction, facades, owner-focused linting, and validation; rebuild index after module additions.
- Mobile OpenClaw reply protocol and dashboard: protocol_violation_no_owned_result, backup1, 127.0.0.1:18808, login-on-demand
  - desc: Primary visible-CDP follow-up recovery, backup1 boundary, and verified dashboard/login entrypoints; live bridge state is checkout-sensitive.

#### C:\Users\45543\Documents\mc

- Minecraft Fabric 26.1.2 global skill: fabric-mc-26-1-2, Java 25, Fabric Loom 1.15, Mojang official mappings
  - desc: Current Fabric client/server/mod/shader guidance and global skill location `C:\Users\45543\.codex\skills\fabric-mc-26-1-2\SKILL.md`; recheck versions before use.
