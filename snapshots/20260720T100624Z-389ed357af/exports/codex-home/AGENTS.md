# Global AGENTS.md

## 0. Scope And Precedence

Machine-wide Codex baseline. Platform/system/developer instructions and the
user's explicit request outrank local rules. Global AGENTS sets universal
boundaries; the nearest workspace AGENTS specializes local entrypoints and
invariants; workflow and owner contracts implement them. Lower layers may add
constraints but must not weaken a higher hard boundary.

## 1. Hard Boundaries

- Ask before modifying local files unless the user already authorized the specific change.
- Route pre-edit recovery through the owning backup router. Clean tracked bytes may use a validated Git HEAD/blob reference; dirty, staged, untracked, runtime, and non-Git bytes require an external copy. Never place backup payloads or manifests inside an active Git worktree, and do not scatter adjacent `.bak-*` files.
- Use UTF-8 for Chinese paths, JSON, Markdown, configuration, resources, and script output; diagnose mojibake before repair.
- Never weaken permission, safety, legal, privacy, secret, or destructive-action boundaries through fallback tools.
- Prefer hidden/no-window Windows helpers; explain beforehand when a visible window is necessary unless explicitly requested.

## 2. Task Admission

- For non-simple work, consume the workspace workflow route before execution. Skip only self-contained translation, wording, trivial formatting, current time/date, or a direct command with no file, state, tool, or routing decision.
- Before repository-scoped file, state, or tool work, query Codex App thread state for other active tasks in the same canonical Git repository. Exclude the current task and tasks that are idle, not loaded, completed, archived, or outside that repository; identify linked worktrees by Git common-dir/repository identity rather than cwd spelling alone.
- When another task is active in that repository, send it a bounded work-start message naming the goal, declared paths or surfaces, expected state writes, branch/worktree, and requested ownership or overlap coordination. A send receipt proves notice, not agreement: resolve reported or plausible overlap before editing, while clearly disjoint work may proceed without waiting indefinitely. If thread discovery is unavailable, shared-worktree mutation is blocked; an isolated declared-path worktree may proceed only when available Git evidence shows no known overlap.
- Explicit structured fields are authoritative; planned tool actions and changed-file evidence are mandatory backstops; natural language only fills absent non-safety facts.
- Treat `required_gates`, `stop_if`, validation, result-consumption, reload boundaries, and closeout evidence as obligations. A triggered unresolved gate blocks completion.
- If the workflow entry is unavailable, apply the smallest equivalent route explicitly: task facts, domain, memory/skill, owner, validation, and closeout.
- For every non-simple task, consume the route pack's proactive asset guidance before broad exploration or execution. Select the smallest combination of rules, skills, owners, and tools that materially improves the work; follow the suggested order and apply each selected asset to the actual analysis or action. Do not wait for the user to name a known asset, do not call assets merely to prove usage, and skip an asset when its stated boundary or a more specific owner makes it irrelevant.

## 3. Ownership And Execution

- Codex owns judgment, design, tradeoffs, exceptions, and safety decisions; delegate complete, low-risk, repeatable, verifiable execution to the owning tool.
- Workflow contracts decide routing and gates; owner contracts hold commands, permissions, retries, fallback, evidence, and maintenance; skills/templates/memory provide scoped guidance and cannot create permissions or override gates.
- For work spanning multiple systems, resolve the primary owner and each dependency owner before execution, preserve every system's permission boundary, and define the handoff evidence and acceptance predicate explicitly.
- Query declared capability, maintenance, and module indexes before adding tools or owners. Prefer composing and extending existing capabilities; add a new owner only when no existing owner can hold the required lifecycle, state, safety, and validation contract without boundary drift.
- Choose tools by comparative advantage: graph tools for source structure and impact, structured queries for indexed state, domain owners for runtime truth, resource owners for external acquisition, and browser/GUI tools only for live interface state. A supporting asset must not displace the primary business or runtime owner.
- When designing or extending a repeatable machine flow, declare one authority for each contract or state fact. Downstream layers consume that authority by stable reference and emit only the smallest derived projection needed by their caller; do not copy full contracts into route, validation, UI, receipt, or closeout layers. Reuse one valid result by signature, invalidate only changed dependents, and add no second audit when the existing owner result or route guidance answers the question.
- Submit machine-readable external-resource needs to the resource layer first. Direct generic web requires an unavailable or terminally exhausted configured route, or an explicit user instruction.
- Resolve the capability matrix before MCP work. Start at the configured stage, move only forward after failure, preserve the permission boundary, and use diagnostic complete-route only for unknown or ambiguous mappings.

## 4. Evidence And Efficiency

- Owner or transport success is not task completion until the caller's acceptance predicate is met and the result is consumed or explicitly waived.
- Start from declared files, owners, indexes, structured state, and stable identifiers. Expand scope only when bounded evidence is insufficient; retain an explicit deep-scan path for tasks that require it.
- Routine success returns a bounded traceable summary. Failures, blockers, and decisions retain decision-complete evidence plus a stable reference; aggregates supplement rather than replace actionable rows.
- Information compression removes redundant representation, not required function. Preserve every decision, gate, owner/action, permission boundary, failure cause, and next step needed by the consumer; when that functional payload cannot fit inline, return a stable reference or an explicit expansion/blocking state instead of silently truncating it or increasing budgets without bound.

## 5. System And Rule Evolution

- System-member changes require membership admission before activation and changed-file reconciliation before closeout; changed files are the fallback when task classification misses the change.
- Rule-bearing changes require rule-governance impact, lifecycle/authority validation, and a machine-readable closeout receipt. Do not duplicate owner catalogs in AGENTS files.
- Keep business and maintenance behavior aligned. Preserve useful protections before replacing them; retirement requires replacement or explicit no-replacement evidence, removal from active surfaces, validation, and a negative guard against reintroduction.

## 6. Delegation And Closeout

- Follow explicit task envelopes exactly. Mobile delegation must acknowledge as required, continue real work, consume required supplements, and return only the specified final markers.
- Persist only durable verified changes, lessons, configuration/state changes, exposed drift, or required proposals through the owning closeout surface; store reusable rules rather than raw incident noise.
