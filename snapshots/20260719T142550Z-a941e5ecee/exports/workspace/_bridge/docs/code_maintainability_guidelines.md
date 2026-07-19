# Code Maintainability Guidelines

This document is the local coding standard for Codex-facing project code in this
workspace. It converts general cleanup advice into rules that fit the current
Python-heavy `_bridge` codebase.

## Scope

- Apply these rules to new code, touched code, and planned refactors.
- Do not churn stable historical code only to satisfy style targets.
- Use `_bridge/code_maintainability.py snapshot|plan|validate` before broad cleanup.
- Use backups and the smallest relevant validator before and after edits.

## Naming

- Python functions, variables, files, and modules use `snake_case`.
- Classes use `PascalCase`.
- Constants use `UPPER_SNAKE_CASE`.
- Boolean names should carry meaning: prefer `is_ready`, `has_data`,
  `should_retry`, `can_write`, `needs_probe`, or `*_ok`.
- Avoid throwaway names such as `a`, `b`, `tmp1`, `obj1`, or `data2` unless the
  scope is tiny and the name is conventional, such as loop indexes.
- Module names must be tightly coupled to their function. Prefer names such as
  `memory_note_analysis`, `memory_work_notes`, `local_mcp_hub_specs`, or
  `record_store_maintenance` over generic `utils`, `helpers`, `common`, or
  `misc` modules.
- Use suffixes consistently to make module purpose obvious:
  - `_analysis`: read-only classification, scoring, extraction, or planning.
  - `_specs`: schema, tool specs, static route/spec tables.
  - `_governance`: owning policy and workflow CLI for a domain.
  - `_doctor`: diagnostic snapshot/doctor/validate entry point.
  - `_maintenance`: record, cleanup, index, or scheduled maintenance entry.
  - `_adapter` or domain-specific service names: external API/process/UI
    boundaries.

## Structure

- Keep functions single-purpose. New or touched functions should usually stay
  under 80 lines; the scanner treats 160 lines or high decision count as a risk
  for legacy code.
- Prefer early returns and guard clauses over deeply nested `if`/`for` blocks.
- Keep nesting to three levels or less when practical.
- Split repeated logic into shared helpers only after real duplication exists.
- Preserve stable callers while extracting internals; use small helper
  extraction before module extraction.
- Prefer purpose-based modules over size-based modules. A new module should
  have a clear operational owner such as tool specs, JSON CLI helpers,
  work-note storage, process lifecycle planning, memory review, or route
  indexing.
- Before extracting a module, classify the target by purpose and expected reuse:
  shared utility, domain service, adapter, schema/spec table, CLI wrapper,
  validator/doctor, repair planner, or persistence boundary.
- Do not create one-off "misc" modules just to shrink a file. If the extracted
  code will not make future work easier, keep it as a local helper.
- Keep modules concentrated by category. Cross-domain primitives belong under
  `_bridge/shared/`; domain-specific helpers stay beside the domain owner until
  there is real cross-domain reuse.
- When a new module becomes a stable entry point, record or expose it through the
  relevant maintenance map, capability table, workflow, or local guide instead
  of leaving it discoverable only by filename.

## Module Reuse Gate

- Before non-simple code edits, query module ownership with
  `python _bridge\code_maintainability.py module-context --term <domain>` and
  query reusable capability candidates with
  `python _bridge\code_maintainability.py lookup-module --term <capability>`.
- Maintenance-only diagnostics may skip the module gate. Maintenance repair,
  optimization, refactor, code cleanup, or module governance work must use the
  module gate before editing.
- If `_bridge/runtime/module_capability_index.json` is missing or stale, rebuild
  the filtered full-source index with
  `python _bridge\code_maintainability.py build-module-index --all-bridge --limit 1000`.
  Use the no-`--all-bridge` form only for fast governance-core checks. This file
  is a derived runtime cache, not a source of truth.
- Prefer reusing or extending an existing module when boundary, state behavior,
  validation owner, and non-goals match the change.
- Create a new module only when extending an existing module would pollute its
  boundary, mix incompatible state-write semantics, or make validation ownership
  ambiguous.
- At closeout, record whether the existing module was reused, extended, or
  deliberately rejected with a boundary reason.

## Boundaries

- Keep data access, business rules, CLI parsing, output formatting, and
  maintenance diagnostics separate where the local module shape allows it.
- For system modules, business behavior and maintenance surfaces must evolve
  together: snapshot, doctor, repair-plan, validate, metrics, or equivalent.
- Do not move responsibility to a weaker layer merely to reduce local code size.
- Keep module boundaries aligned with use, not implementation accident:
  repeated cross-module primitives belong in `_bridge/shared/`; feature-specific
  helpers stay beside the feature until a second real caller appears.
- Prefer stable facade wrappers during extraction. Existing CLI commands,
  public function names, schemas, tool names, and permission boundaries should
  remain stable while internals move behind a purpose module.
- A module extraction is successful only if it improves one of these practical
  outcomes: fewer repeated edits, clearer validation ownership, smaller blast
  radius, easier tool routing, reusable capability, or simpler future upgrade.
  Lower line count or lower issue count alone is not enough.

## Comments And Docs

- Add comments for non-obvious protocol rules, state transitions, safety
  boundaries, and compatibility behavior.
- Do not comment obvious assignments or repeat the code in prose.
- Update comments and docs when behavior changes.
- Prefer concise module docstrings that state ownership and non-goals.
- Every new purpose module needs a top-level docstring that states:
  - what it owns;
  - what it explicitly does not own;
  - whether it reads or writes state;
  - the normal caller or operating context.
- Feature modules should explain their boundary once at module level instead of
  scattering repeated comments through every helper.

## Complexity

- Reduce branch-heavy logic with dictionaries, tables, strategy helpers, or
  small pure functions when that makes the state model clearer.
- Define named constants for repeated thresholds, protocol names, file paths, or
  state strings that have domain meaning.
- Avoid hard-coded credentials, tokens, machine-specific secrets, and hidden
  external endpoints.

## Errors And Safety

- Wrap filesystem, subprocess, network, UI, and MCP protocol boundaries with
  explicit error handling.
- Error output should include enough context to diagnose the failing boundary,
  but must not leak secrets or sensitive raw content.
- Validate inputs at the boundary before entering core logic.
- Keep fallback behavior inside the same permission boundary as the owning tool.

## Dependencies

- Prefer standard library and existing local helpers before adding dependencies.
- Pin or vendor dependency versions when a workflow depends on exact behavior.
- Wrap third-party tools behind local adapters so they can be replaced without
  editing every caller.

## Developer Toolchain Use

- Use `python _bridge\code_maintainability.py toolchain` or
  `python _bridge\code_maintainability.py validate` to confirm the local
  developer toolchain before relying on it.
- Use `rg` for broad text/code discovery with generated-tree exclusions before
  slower recursive scans.
- Use `fd` for fast file discovery when a file list is the primary need and it
  is clearer than `rg --files`.
- Use `uv` when a Python task needs stable package/tool execution, dependency
  resolution, or an isolated environment. Do not use it to silently upgrade
  unrelated project dependencies.
- Use `uvx` for one-shot Python CLI tools that are useful for a task but should
  not become permanent global installs.
- Use `ruff check <changed-python-files>` as fast targeted feedback after
  Python edits. Use `ruff format` only for explicitly approved formatting
  scopes; never run whole-repo formatting as incidental cleanup.
- Toolchain results support the owning module's validation. They do not replace
  `py_compile`, domain doctors, queue/state readback, or permission-specific
  validators.

## Tests And Validation

- Every non-trivial change needs the smallest meaningful validation loop:
  compile check, module validate, doctor, smoke, state query, or read-back.
- For core bridge, MCP, mail, scheduler, memory, and backup paths, validate both
  business behavior and maintenance output.
- New failure fixes should add or improve a deterministic repro/validation path
  when a correct seam exists.

## Refactor Policy

- Prefer metric-guided, behavior-preserving, incremental refactors.
- Extract pure helpers first; only split modules after the helper boundary is
  clear and validators are green.
- Avoid big-bang rewrites, broad formatting churn, and unrelated cleanup.
- Stop at stable intermediate points when validators pass and the next target is
  a different risk surface.
- Do not optimize for shorter functions alone. A refactor is only progress if
  module ownership, validation ownership, or future edit routing becomes clearer.
- If helper extraction makes the original file larger or less navigable, switch
  to a purpose-owned module instead of continuing to add local helpers.
- Preserve public or semi-public entry points as facades during extraction.
  Move implementation behind the facade first, validate, then decide whether any
  caller should change.
- For cleanup, MCP, process, permission, scheduler, bridge, or mail paths,
  separate pure planning logic from execution safety gates. Never move an
  execution gate into a weaker or less visible layer just to reduce line count.
- Treat each refactor pass as one bounded risk surface:
  - inspect the highest current risk function;
  - state the old mechanism's useful protection;
  - move only pure logic or a single responsibility;
  - run the owner validator plus any behavior-specific dry-run;
  - stop when the next target belongs to a different semantic surface.
- For the mobile OpenClaw bridge, treat `mobile_openclaw_cli.py` as a stable
  facade while extracting internals. Prefer purpose-owned peer modules for
  command parsing, control reply text, read-only repair evidence, account file
  lookup, CLI formatting, and worker observability. Do not move queue mutation,
  permission decisions, delivery/retry behavior, supplement ack/drop behavior,
  or repair execution gates until the old protection and a targeted regression
  matrix are explicit.
- Bridge CLI extractions must be validated against the old failure class. For
  example, repair parsing/text changes need repair command and control receipt
  checks; supplement changes need supplement fallback checks; final reply
  delivery changes need text/media split and idempotency checks; capability
  changes need passphrase/token state-machine checks.
- Do not let the scanner alone drive the whole session. Use it to choose the
  next candidate, then classify the candidate by semantic surface before
  editing. Good batch boundaries include resource cleanup planning, stdio MCP
  protocol calls, HTTP MCP protocol smoke, current-turn evidence handling,
  scheduler queue transitions, bridge delivery, and permission/capability
  checks.
- Stop after a validation-green pass when the next hotspot crosses to another
  semantic surface. Record that target as the next focused pass instead of
  mixing unrelated protocol paths in one edit window.
- Prefer facade-preserving local helper extraction for the first pass on a
  risky owner file. Create a new purpose-owned module only when the extracted
  logic has a clear owner and will reduce repeated future edits.
- Avoid "helper inflation": if extracting helpers increases line count but the
  original hotspot is now easy to review, pause and move to a higher-value
  owner file rather than continuing cosmetic splits.
- Use a module-first decision gate before edits:
  1. Identify the current module's purpose and the proposed module's purpose.
  2. Choose a function-coupled module name and category before writing code.
  3. Add a module docstring with ownership, non-goals, state behavior, and caller
     context.
  4. Check whether the extracted code is reused now or will clearly reduce
     repeated work in the next similar task.
  5. Keep old entry points as facades until all validation paths pass.
  6. Move only one responsibility per patch.
  7. Record any new module in the relevant maintenance map or capability table
     when it becomes a stable entry point.

## Review Discipline

- Treat external review feedback as evidence to evaluate, not an instruction to
  apply blindly. This applies to human review, reviewer subagents, CodeRabbit,
  and plugin-derived review workflows.
- Before implementing review feedback, restate or identify the technical
  requirement, check the current code path, find the old mechanism's useful
  protection, and decide whether the suggestion is correct for this workspace.
- Push back or ask for clarification when feedback is ambiguous, conflicts with
  local requirements, breaks compatibility, violates YAGNI, or moves safety into
  a weaker layer.
- For multi-item review feedback, clarify unclear items first, then implement
  one bounded item at a time: blockers/security/data-loss risks, simple fixes,
  then larger refactors. Validate after each meaningful batch.
- Do not claim a manual review came from an external tool. If CodeRabbit or a
  similar tool is used, verify the CLI/auth/network prerequisites first, parse
  its real output, and report failures as tool failures rather than replacing it
  with an unnamed manual review.
- Request an additional reviewer only when it has value: major feature, merge
  readiness, complex bug fix, high-risk refactor, stuck diagnosis, or explicit
  user request. Do not turn review into a ritual for every small edit.
- Platform-specific refactor skills, such as SwiftUI/macOS view refactor
  plugins, are reference material for those platforms. In this Python-heavy
  workspace, only their general principles carry over: clear ownership, stable
  facades, small focused modules, narrow platform/API escape hatches, and
  behavior-preserving validation.

## System Refactor Validation Matrix

- Generic Python/module change:
  - `python -m py_compile <changed-files>`
  - `ruff check <changed-python-files>` when `ruff` is available and the files
    are Python.
  - owner validate or targeted read-back.
- Resource/MCP process lifecycle change:
  - `python -m py_compile _bridge/resource_process_doctor.py _bridge/resource_process_lifecycle.py`
  - `python _bridge/resource_process_doctor.py repair-plan`
  - `python _bridge/resource_process_doctor.py cleanup --min-age-minutes 999999`
  - `python _bridge/resource_process_doctor.py validate`
  - `python _bridge/resource_process_doctor.py metrics`
  - `python _bridge/code_maintainability.py validate`
- Workflow/routing change:
  - `python _bridge/workflow_orchestrator.py validate`
  - targeted `plan --message "<realistic task>"`.
- Developer toolchain check:
  - `python _bridge/code_maintainability.py toolchain`
  - `uv --version`
  - `uvx --version`
  - `ruff --version`
- Rule or guide change:
  - targeted read-back;
  - `python _bridge/agents_rule_mirror.py sync`;
  - `python _bridge/agents_rule_mirror.py validate` when AGENTS changed.

## External Design Basis

- Separation of concerns: split by kinds of work, not by arbitrary file size.
- High cohesion and low coupling: a module should have one focused reason to
  change and minimal knowledge of unrelated modules.
- Incremental modernization: use strangler/branch-by-abstraction style
  transitions so old and new paths can coexist during validation.
- Refactoring discipline: preserve external behavior while improving internal
  structure.
