# Codex Production Environment Architecture Plan

## Purpose

This plan governs the full Codex production environment lifecycle. Recovery
mirror work is one part of the lifecycle, not the architecture itself. The
goal is a portable, observable, recoverable environment that a new agent can
activate safely on another device without turning Git, Windows, WSL, or one
large owner into a second source of truth.

The plan is incremental. A phase may start only when its authority, owner,
input signature, acceptance predicate, rollback boundary, and evidence are
defined. Later phases reuse earlier receipts and do not repeat unchanged work.

## Evidence Base

| Source | Reusable mechanism | Local decision |
| --- | --- | --- |
| Google SRE book | SLOs, useful monitoring, toil reduction, release engineering, incident learning, and simplicity | Measure service outcomes and automate repeatable work; do not equate process uptime with service readiness |
| NIST SP 800-61 Rev. 3 | Incident response belongs throughout cybersecurity risk management | Preparation, detection, response, recovery, and post-incident improvement are one lifecycle |
| Twelve-Factor App | Strict build, release, and run separation; config is attached at release time | Immutable source/build outputs, explicit environment binding, and no reverse write from runtime into source |
| restic | Content-addressed deduplication, repository checking, and restore verification | Reuse unchanged bytes by content identity and treat verification as a recovery gate |
| Home Manager | Declarative target state and generations | Keep desired state separate from activation; retain a bounded rollback generation |
| Dotbot | Small deterministic bootstrap, ordered directives, and idempotent reruns | Natural language selects the route; deterministic owners execute repeatable setup |
| codex-environment-backup | Agent-independent CLI, health checks, and repeatable recovery | Every critical recovery action needs a testable CLI path that survives loss of the agent session |
| opencode-config-backup | Dry-run, idempotent SAME skipping, relative paths, secrets placeholders, and copy/reinstall separation | Stage first, classify every action, and never treat reinstallable or secret material as ordinary copied state |
| GitOps disaster-recovery guidance | Git restores declarative objects, not persistent application data | Source recovery and state/data recovery remain separate owners with an explicit convergence gate |

Research was performed on 2026-07-22 through the workspace resource layer and
Hub search. External projects are design references unless they pass the
dependency admission gate below.

The second research pass adds OpenHands (sandboxed runtime and replayable event
stream), SWE-agent (purpose-built agent-computer interfaces), Temporal
(durable execution, task queues, signals and timers), OpenTelemetry (shared
trace/metric/log semantics), Kubernetes controllers (desired-state
reconciliation), and LangGraph (checkpointed execution with human approval).
These patterns justify the runtime contracts and the routing change introduced
in Phase 1 below.

Primary references:

- https://sre.google/sre-book/table-of-contents/
- https://csrc.nist.gov/pubs/sp/800/61/r3/final
- https://12factor.net/build-release-run
- https://www.sqlite.org/backup.html
- https://restic.readthedocs.io/en/stable/045_working_with_repos.html
- https://github.com/nix-community/home-manager
- https://github.com/anishathalye/dotbot
- https://github.com/gaoguobin/codex-environment-backup
- https://github.com/QThinkerJR/opencode-config-backup
- https://argo-cd.readthedocs.io/en/stable/operator-manual/disaster_recovery/
- https://github.com/All-Hands-AI/OpenHands
- https://github.com/SWE-agent/SWE-agent
- https://docs.temporal.io/workflow-execution
- https://opentelemetry.io/docs/concepts/semantic-conventions/
- https://docs.langchain.com/oss/python/langgraph/persistence

## External Dependency Admission

External projects have three possible outcomes:

1. **Reference only**: borrow a design idea or test case; no runtime coupling.
2. **Encapsulated adoption**: use a mature tool behind an existing owner, with
   pinned version, license record, health check, offline fallback, and exit
   path. This is the default for high-value utilities.
3. **Core dependency**: permit only when the tool is security-reviewed,
   actively maintained, reproducibly installable, cross-platform compatible,
   independently verifiable, and cheaper to operate than the existing owner.

Admission evidence must include upstream identity and commit/version, license,
maintenance signal, dependency and supply-chain review, supported platforms,
offline or degraded behavior, data/secret handling, performance measurements,
restore or rollback proof, and an owner contract. A tool is not admitted merely
because it is popular or feature-rich. Removal must be possible without
rewriting the source authority.

## Unified Lifecycle

```text
declare -> acquire -> build -> release -> stage -> activate -> operate
   ^                                                     |          |
   |                                                     v          v
learn <- post-incident <- recover <- restore <- protect <- observe <- incident
```

Each transition consumes a hashed input identity and emits one machine-readable
receipt. A downstream transition stores only the smallest projection needed by
its caller and references the owner receipt for detail.

## Architecture Layers

### 1. Authority And Inventory

- WSL Work Git owns declarative source, owner code, policies, skills, and
  portable configuration.
- Windows compatibility projection owns only generated Windows execution
  bindings and host-specific mutable state.
- Runtime databases, secrets, credentials, hardware bindings, and external
  service state stay with their domain owners.
- Every recoverable asset declares `asset_id`, platform, authority, owner,
  disposition, dependencies, sensitivity, capture method, restore method, and
  validation method.
- Git repository identity, not path spelling, controls concurrent work.

### 2. Acquisition And Supply Chain

- External resources enter through structured resource jobs with source,
  version, hash, license, and consumption receipts.
- Search relevance is evaluated only from candidate evidence. Echoed query
  text cannot prove relevance.
- Installable dependencies are locked or archived when redistribution and
  platform boundaries allow it. Reinstallable dependencies retain exact
  acquisition instructions when bundling is wasteful or prohibited.
- Generated indexes and caches are rebuildable products, not source assets.

### 3. Build And Release

- Build produces immutable artifacts from a declared source identity.
- Release binds artifacts to platform configuration and dependency versions.
- Run consumes a release and may emit runtime state, but cannot rewrite Work
  Git or the release contract.
- Mirror publication and semantic milestone release remain separate actions.

### 4. Stage And Activate

- Restore and deployment begin with a complete dry-run classified as
  `COPY`, `REINSTALL`, `REGENERATE`, `PROMPT`, `SAME`, `REMOVE`, or `BLOCK`.
- Writes first target an isolated staging root. Hash, schema, dependency,
  platform, secret-placeholder, and membership checks run before activation.
- Activation is explicit, bounded, and reversible. It records the previous
  generation and never silently replaces a live environment.

### 5. Runtime Control Plane

- Linux systemd manages WSL-native core services. Windows-only GUI, hardware,
  Credential Manager, Office, and host database actions use the constrained
  Windows execution plane.
- One owner controls each lifecycle and mutable state fact. The scheduler may
  wake owners but does not absorb their business logic or permissions.
- Long-running work uses durable task state, leases, bounded retries, progress
  receipts, and crash reconciliation instead of terminal attachment.

### 6. Observability And Service Objectives

- Readiness is defined by caller-visible capability, not process existence.
- Each critical service declares a small set of service indicators: request
  success, latency, freshness, queue age, recovery readiness, and dependency
  health where applicable.
- Alerts identify an actionable owner and stable evidence reference. Advisory
  output that requires Codex to repeat the same investigation is incomplete.
- Phase timings, cache decisions, retry budgets, and fallback reasons are
  retained for expensive flows.

### 7. Protection And Recovery

- Declarative source, runtime state, secrets, credentials, and reinstallable
  dependencies use distinct capture and restore contracts.
- Live SQLite databases use their owner-supported online backup or quiescent
  snapshot; WAL and SHM files are never copied as a substitute.
- Snapshot storage moves toward content-addressed reuse. A valid hash result is
  reused until its source signature changes.
- Restore readiness requires a staged restore and owner validation, not only a
  successful capture or remote push.

### 8. Incident And Learning Loop

- Incidents move through preparation, detection, containment, recovery, and
  verified closeout without bypassing domain permissions.
- Recovery first restores safe service, then reconciles desired state and
  persistent data, then proves user-visible capability.
- Post-incident output records the smallest durable rule, owner, skill, test,
  or runbook change that prevents recurrence. Raw incident noise is not copied
  into every layer.

### 9. Publication And Audit

- Publication remains one-way:
  `WSL Work Git -> Windows bare Git -> validated recovery mirror -> GitHub`.
- One stable source state produces at most one final mirror refresh. An
  unchanged validated snapshot is reused.
- Remote success requires head/tag/asset readback as appropriate. Transport
  success alone is not completion.
- Retention is bounded by recoverability, not by accumulating unlimited
  backups, archives, or release assets.

## Cross-System Contracts

| Concern | Authority | Execution owner | Acceptance evidence |
| --- | --- | --- | --- |
| Task routing | workflow contracts | workflow orchestrator/facade | required gates and consumed owner result |
| Repository changes | WSL Work Git | Git change-set owner | declared paths, validation, integrated HEAD |
| Windows execution | Work Git declaration plus host projection | Windows execution agent/domain owner | command identity and readback |
| External resources | resource request/strategy | resource broker and selected owner | source quality and consumption receipt |
| Runtime services | domain declaration | systemd/scheduler plus domain owner | capability readiness and SLO evidence |
| Persistent data | domain owner | database/state backup owner | consistent snapshot and staged restore |
| Secrets | secret backend | credential/secret owner | placeholder resolution without disclosure |
| Recovery mirror | mirror contract | mirror adapter and external mirror CLI | manifest, hash, restore plan, remote verification |
| Release milestone | release plan and explicit approval | mirror release owner | tag, Release, asset and manifest readback |

## Execution Economy Rules

1. Compute one stable signature from authoritative inputs.
2. Reuse a valid owner receipt when the signature and acceptance predicate are
   unchanged.
3. Invalidate only dependent phases touched by changed inputs.
4. Batch independent deterministic operations under one total deadline.
5. Persist decision-complete failure evidence so diagnosis is not repeated.
6. Escalate ambiguity, permission decisions, architecture changes, and failed
   acceptance to Codex; machines handle low-risk repeatable execution.
7. Compression removes duplicate representation only. It must preserve gates,
   permissions, decisions, causes, owners, and next actions.

## Phased Implementation

### Phase 0 - Measurement And False-Success Removal

Status: implemented in the current change set, pending final closeout.

- Reject resource results whose only relevance evidence is the echoed query.
- Restore non-zero relevance thresholds and require stronger multi-source
  evidence.
- Add mirror owner phase timings for plan, capture/live validation,
  control-plane validation, and total publication.

Acceptance: resource regression tests reject irrelevant results; mirror tests
preserve bounded timing receipts; rule impact is fully mapped.

### Phase 1 - Canonical Asset And Release Model

Status: routing entry implemented; canonical asset schema remains next.

- Treat requests for an AI operating system, productionization, durable agent
  runtime, observability, or declarative reconciliation as workflow-governance
  work instead of `general` work.
- Automatically expose governance, external-research, memory, MCP, and
  execution-economy guidance from the selected route.

- Extend the existing mirror source/manifest contract with platform,
  authority, disposition, dependency, sensitivity, and restore action fields.
- Generate restore classification from that single contract instead of
  duplicating action logic in docs or release metadata.
- Add schema migration and compatibility tests before changing live manifests.

Acceptance: every required capability has an authoritative asset row and a
complete restore disposition; existing snapshots remain readable.

### Phase 2 - Incremental Capture And Storage

- Use changed-source signatures to skip unaffected owners.
- Reuse file hashes by stable metadata only where the owner can prove safety.
- Measure whether NTFS hardlink creation, hashing, validation, or Git is the
  dominant cost before choosing CAS, packfiles, or another storage change.
- Prototype storage changes outside the production mirror and compare capture,
  validation, restore, and cleanup costs.

Acceptance: unchanged publication avoids recapture; changed publication runs
only invalidated phases; restore integrity is unchanged or stronger.

### Phase 3 - Portable Stage And Activation

- Produce a machine-readable dry-run with the required action classes.
- Stage into an isolated root and validate dependencies, secrets placeholders,
  platform mapping, MCP readiness, and membership.
- Add an explicit activation owner with previous-generation rollback.

Acceptance: a clean target can stage without source mutation; activation and
rollback both have readback receipts.

### Phase 4 - Production Operations Baseline

- Declare readiness and small SLO sets for critical WSL and Windows services.
- Route scheduler wakeups to domain owners and persist bounded run receipts.
- Add incident classification and owner-specific recovery entrypoints without
  creating a universal repair command.

Acceptance: every critical capability reports owner, readiness, freshness,
latency or queue age where relevant, and a tested recovery action.

### Phase 5 - Disaster-Recovery Exercise

- Restore source, dependencies, persistent state, and secrets bindings into a
  disposable environment.
- Reconcile platform-specific projections and run capability smoke tests.
- Record recovery point, recovery time, gaps, and exact failing owner.

Acceptance: a new agent can perform the documented restore without access to
the original live worktree; all required capabilities are usable or have an
explicit external dependency blocker.

### Phase 6 - Adaptive Automation

- Convert repeated successful recovery and maintenance paths into deterministic
  owner actions with stable signatures.
- Use historical timings to set phase budgets and detect regressions.
- Keep architecture and permission decisions with Codex.

Acceptance: routine work becomes shorter without hiding failures, weakening
permissions, duplicating authority, or sacrificing required function.

## Guardrails

- Do not create a universal production owner. Compose domain owners through
  stable receipts.
- Do not treat Git as a backup for runtime data or secrets.
- Do not copy mutable Windows state back into Work Git.
- Do not introduce a content store before measurement proves its benefit and a
  restore test proves portability.
- Do not activate a staged environment automatically after capture or restore.
- Do not rerun capture, hashing, validation, upload, or release steps when a
  current signature-bound receipt already satisfies the caller.
- Do not compress evidence at the cost of a gate, permission boundary,
  decision, failure cause, owner, or next action.

## Next Implementation Slice

After Phase 0 closeout, use the new mirror phase timings from exactly one final
publication to select the next bottleneck. If capture dominates, implement the
Phase 2 changed-source/hash reuse slice. If validation dominates, cache only
signature-bound owner results. If Git or remote transfer dominates, optimize
repository packing or transfer separately. Phase 1 schema work proceeds in an
isolated compatibility-tested change set and must not be mixed with storage
experiments.
