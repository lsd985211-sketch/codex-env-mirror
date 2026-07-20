# Execution Economy

This workspace uses a machine-first contract for repeatable work. The goal is
not maximum automation; it is the smallest reliable execution path.

## Contract

- The environment owns declared, deterministic, low-risk owner operations.
- Codex owns ambiguity, architecture and tradeoffs, approvals, external
  writes, unknown inputs, failed validation, and unconsumable receipts.
- A stable input signature identifies the declared inputs, owner capability,
  and relevant version. A fresh validated receipt for that signature is
  evidence to reuse, not a reason to run the same step again.
- Independent operations are batched under one deadline. Stateful,
  approval-bound, or externally side-effecting operations remain separate.
- A changed input invalidates only the affected step and its dependents. A
  source-affecting closeout performs one final publication; later actions read
  that receipt.

### Single Authority And Derived Projections

Every repeatable flow identifies one authoritative owner for each contract,
state fact, or external result. Other layers may expose a bounded summary or a
stable reference, but must not copy the full payload and then validate or
mutate it independently. Route plans, owner facades, UI projections, receipts,
and closeout packages therefore remain consumers, not competing authorities.

Before adding a routine step, determine whether an existing owner result,
route guidance, or derived index already answers the need. If a projection is
necessary, carry a stable reference plus only the fields the immediate caller
needs, declare the input signature and dependent invalidation boundary, and
keep approval-bound or stateful operations separate from batchable reads.

The machine resolves these checks from declared contracts and indexes. Codex
handles exceptions, architecture tradeoffs, and whether residual uncertainty
justifies a new owner or validation surface.

### Function-Preserving Compression

Compression removes duplicate representation while preserving the consumer's
ability to decide and act. A bounded route, receipt, validator, UI projection,
or closeout summary therefore retains all required gates, policy decisions,
owners and actions, permission boundaries, failure causes, and next steps.
Descriptive context and repeated copies may be shortened or replaced by a
stable reference.

If the required functional payload itself exceeds the inline budget, the
machine must return an explicit `reference_required` or `blocked_no_reference`
state and a safe expansion action. It must not silently omit required fields,
report a success-like partial result, restore duplicated contracts in another
layer, or grow the byte budget indefinitely. The shared executable authority
for this presentation boundary is `bounded_output.py`; callers declare only
their task-specific required fields.

## Cross-Layer Mapping

| Layer | Machine responsibility | Codex responsibility |
| --- | --- | --- |
| Rules | Require least-work, signature reuse, bounded batching, and one final publication | Resolve exceptions and decide when a rule must change |
| Workflow | Emit `auto_execute`, `codex_deferred`, `review_required`, or `blocked` plus signatures and invalidation rules | Consume the decision and handle the escalation boundary |
| Owner/MCP | Execute the typed owner path, cache safe results, and return a receipt | Select the owner when routing is ambiguous and judge result fitness |
| Resource/network | Reuse route/package/source evidence and share one deadline across a batch | Refine a failed or unsuitable request; never start an independent duplicate fetch |
| Skills | Provide the narrowest procedure and handoff conditions | Load only the selected skill and apply judgment beyond its boundary |
| Validation/closeout | Verify the smallest affected surface and reconcile receipts | Decide whether residual risk blocks completion or needs a proposal |

## Evidence From Prior Work

The mirror path already established two reusable optimizations: commit only the
current snapshot/retention pathspecs (`6151e41`), and reuse control-plane
validation rather than running the full validation twice (`d53d8a8`). These are
instances of the same general rule: derive the changed set once, reuse valid
evidence, and reserve expensive work for invalidated inputs.

## External Design References

- Temporal, *What is a Temporal Activity?*: activities are small, well-defined,
  preferably idempotent units; workflows persist their results and larger work
  should be split for recovery and timeout control.
  <https://docs.temporal.io/activities>
- AWS, *Idempotency and retries*: replay/retry may execute an operation more
  than once; choose at-least-once only for idempotent work, use stable
  idempotency keys for external side effects, and combine at-most-once with no
  retry when a single attempt is required.
  <https://docs.aws.amazon.com/durable-execution/patterns/best-practices/idempotency/>
- Microsoft, *AI agent orchestration patterns*: start with the lowest necessary
  complexity, route deterministic pipelines deterministically, parallelize
  only independent work, validate outputs, checkpoint long flows, and scope
  human approval to sensitive tool invocations.
  <https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns>

This document is guidance. The executable policy is
`workflow_automation_delegation.py`; owner permissions and validation remain
authoritative.
