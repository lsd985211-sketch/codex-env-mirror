# MCP Capability Matrix

Owner-backed non-simple work is projected through `codex_workflow_entry.py plan`.
Its `owner_facade` contract is the default machine route: strong route evidence
creates a typed action; incomplete or ambiguous MCP/session routes return
`needs_input`; session-bound tools remain `handoff_required` and attach their
result to the same `workflow_run_id`.

This is the compact operating map for every configured MCP in this workspace.
It tells Codex when a tool is worth calling, how to call it for maximum value,
how to validate the result, and when to stop and use a bounded fallback.

## Execution Economy

Repeatable routes use the machine-first delegation contract from
`workflow_automation_delegation.py`. The environment owns declared,
deterministic, low-risk owner steps; it reuses a fresh receipt or derived index
when the stable input signature is unchanged, batches independent requests
under one deadline, and skips unchanged downstream validation. Codex owns
ambiguity, architecture and tradeoffs, approvals, external writes, unknown
inputs, failed validation, and receipts that cannot be consumed. A source-
affecting closeout performs one final publication and later steps consume its
receipt.

Do not treat this as a static availability list. Stateless owner services may
run through Hub/fresh stdio without being exposed as native tools in the active
turn. Session-bound tools still require the active session and a real call.

## Proactive Use Rule

For every nontrivial task, Codex should actively choose the owning tool before
falling back to shell or ad hoc scripts. The default decision order is:

1. Recall relevant memory and load the narrowest applicable skill.
2. If the task matches a repeated workflow, render the relevant
   `custom-slash-commands` template as a checklist.
3. Select the owning MCP and its execution affinity. Stateless and owner-service
   capabilities default to Hub; current browser/GUI/mobile-thread capabilities
   remain native-current-session first.
4. Use shell, direct Python, or CLI only when they are the classified owner
   interface for that action, or after the selected affinity route reaches its
   local fallback stage.
5. Call known Hub tools directly. Use `owner_mcp.call_readonly` for explicit
   allowlisted OpenAI Docs, Context7, Microsoft Docs, filesystem-read, and MarkItDown calls.
   Use `mcp_gateway.complete_route` only for unknown, ambiguous, permission/schema
   uncertainty, or diagnostics. Local Hub/owner CLI remains bounded fallback.

Task-to-tool routing:

- Local known files: `filesystem-admin` is the default MCP profile so one
  governed surface can cover the configured workspace roots. Read calls use
  the Hub read-only allowlist; writes still require task authorization, backup,
  the owning module boundary, and readback. Use `apply_patch` for manual code
  edits. Keep `filesystem` as the restricted compatibility route, not the
  default selector.
- Fixed local workflows: `custom-slash-commands` first, then the rendered
  checklist's owning MCP/CLI.
- Code graphs: `codegraph` is the Hub-first default for narrow indexed source
  symbols, callers, callees, and local impact; `gitnexus` is Hub-first for
  semantic/hybrid execution-flow search, context, trace, and cross-repository
  analysis; `graphify` is Hub-first for managed mixed-artifact graphs and
  graph-guided review. For broad text, Markdown, config, generated assets, and
  rule lookups, use `rg` with standard exclusions first.
- Memory/profile/history: route first through `_bridge/memory_router.py`.
  It decides whether the task should use only current context, the memory
  quick pass, user profile guidance, PMB recall/prepare, external knowledge,
  record-store indexes, or one-shot work notes. PMB is not mandatory for every
  task; it is the long-lived lesson/root-cause layer when selected by the
  route.
- Memory/knowledge absorption candidates: use `memory_governance` and PMB as
  the owning layer. External projects such as `agent-memory-engine`, `ArcRift`,
  and `localmem` are capability references only: absorb local-first,
  evidence-backed, lifecycle-managed patterns into the existing candidate note,
  PMB, work-note, and external-knowledge flow. Do not add another memory
  server unless a concrete missing capability survives this route.
- Bridge/mobile: `mobile-openclaw-bridge` first, preserving owned-result and
  supplement contracts.
- Databases: `sqlite-scratch` for scratch writes; `sqlite-bridge-ro` for bridge
  read-only inspection; `record_store.sqlite` and `email_state.sqlite` for
  derived read-only indexes; production writes must use owning business CLIs.
- Browser/GUI: `playwright`, `chrome-devtools`, or `gui-automation` according
  to the actual surface; avoid GUI when an API/CLI can prove the state.
- Windows generic desktop automation: treat `CursorTouch/Windows-MCP` as a
  capability source, not as a blanket dependency. Absorb its useful pattern as
  `Screenshot/Snapshot/WaitFor` first, then one explicit desktop action, then
  read-back verification. Do not enable broad `PowerShell`, `FileSystem`,
  `Registry`, or process-kill surfaces without the existing permission and
  confirmation boundary.
- Windows desktop Weixin: `desktop-weixin` MCP first for the installed desktop
  harness abilities; use `cli-anything-weixin` only as validation or fallback,
  and keep it separate from the mobile OpenClaw bridge.
- GitHub: Hub `github.api` / `github.gh` is the entry stage for remote repo state and writes;
  failures continue forward through Hub gateway/local Hub and `gh`, without a backward native probe. `github.api` can use a Secret Vault backed GitHub App
  installation token when `github_app.*` aliases are configured. Local git is
  not proof of remote repository changes.
- External docs / online research: submit a resource-layer request first. The
  resource layer owns source discovery, URL selection, retries, and receipts,
  and should choose `openai-docs` for OpenAI API/Codex/ChatGPT developer docs,
  `microsoftdocs` for Microsoft/Windows/Azure, `context7` for
  libraries/SDKs/frameworks, GitHub MCP for repository facts, and
  browser/DevTools/Playwright for page/runtime evidence internally. Deferred or
  insufficient results require a refined resource delegation; failed or blocked
  results require the configured owner/Hub online route chain. Generic web
  search is only a documented last fallback after resource-layer unavailability,
  route-chain exhaustion evidence, or explicit user direct-web instruction.
- Network routing: for proxy, DNS, OpenAI/ChatGPT/GitHub/npm connectivity,
  timeout, or slow-response diagnosis, use `codex_network_gateway.py` as the
  Codex-facing control plane when a caller needs a practical route/env/lease
  answer, or Hub `network_gateway.*` when the Hub route is more convenient.
  Treat `codex_chat` / `codex_model_api` as the current Codex configured model
  baseurl, which may be third-party; treat `openai` as the official OpenAI
  experiment target. Do not transfer official OpenAI probe conclusions onto the
  current Codex route unless the active config shows they are the same.
  Use `network_doctor.py` or Hub `network.*` for lower-level
  diagnostics. `codex_network_gateway.py plan --probe` / Hub
  `network_gateway.plan` may compare direct and
  current proxy for one request and return `probe_selected_direct` or
  `probe_selected_proxy`; direct routes include `unset_env` so callers can
  remove inherited proxy variables. `codex_network_gateway.py batch-plan`
  is the preferred route for multiple resource or package/doc requests in one
  turn. It owns persistent route-decision caching, fresh/stale/expired
  freshness state, and lightweight circuit evidence; callers should consume
  the returned route/env/lease advice instead of duplicating network health
  policy. `network_gateway.lease_start` /
  `lease_status` / `lease_stop` / `lease_cleanup` are the production-safe route
  for short-lived isolated Mihomo proxy leases: they create a localhost proxy
  endpoint for one target kind, return per-process env, enforce TTL/cleanup
  metadata, and do not change the main Clash node. `network_gateway.smoke` is
  the Hub/CLI route for bounded current-proxy, proxy-wrapper, or isolated
  Mihomo smoke tests and report generation. `env` remains per-process only. The
  network layer does not own resource fetching, GitHub writes, browser actions,
  permanent Clash/Mihomo node switching, or system proxy/DNS changes. If the
  Clash/Mihomo HTTP external-controller is unavailable while proxy traffic
  still works, keep the gateway in degraded mode and use the structured
  controller diagnosis; do not silently edit Clash configuration or enable
  controller ports.
  `clash_node_metrics.py` owns Clash node evidence for gateway requests:
  bounded node/site delay cache, lower-frequency isolated real-access evidence,
  freshness-aware `recommend --refresh-if-stale`, and compact recommendation
  payloads. When valid target access evidence already exists it is reused; when
  missing, real-access probes are run only for a small delay-qualified candidate
  set, and access score participates in ranking. It does not switch the main
  Clash node or scan every node with real access probes by default.
- Network gateway component lab: use
  `_bridge/network_gateway_component_lab.py` only for lab-only absorption and
  smoke tests of proxy wrapper components such as proxy-chain, GOST,
  easy_proxies, and Resin. Keep practical Codex route decisions in
  `codex_network_gateway.py`; keep lower-level diagnostics in
  `network_doctor.py` / `network_policy.py`; keep Clash/Mihomo node experiments
  in `_bridge/clash_mihomo_control.py isolated-probe`. The component lab may
  install Node packages under `_bridge/runtime/network_gateway_lab`, start
  localhost-only temporary processes, and must clean them up before returning.
  It must not edit system proxy, DNS, Clash subscriptions/config, firewall,
  Hub startup, or Codex conversation routing.
- Hub on-demand routing: the local Hub should expose stable core tools by
  default and keep low-frequency or experimental tools behind
  `hub.catalog` -> `hub.search` -> `hub.describe` -> `hub.call`. This reduces
  default tool-schema cost without deleting capability. `hub.call` preserves
  the target tool's original permission boundary and requires the Hub
  on-demand acknowledgement. MetaMCP lab tools are experimental hidden tools,
  not practical default routes.
- Docker / gateway lab: use `_bridge/gateway_lab.py` for isolated Docker-backed
  gateway experiments such as MetaMCP. In elevated Codex sessions, direct
  Docker Desktop pulls or compose calls can fail before network work begins
  because `docker-credential-desktop` / `docker-credential-wincred` cannot
  access the user credential vault from the elevated token. Prefer
  `gateway_lab.py docker-limited-pull --image <image>` or
  `gateway_lab.py metamcp-compose --action pull|up|ps|logs|down`, which runs
  Docker through a hidden one-shot limited-user scheduled task and deletes the
  task afterward. Do not delete Docker credentials, disable Desktop features,
  or mutate global Docker config as the first fix. Validate with lab doctor,
  `metamcp-compose --action ps`, localhost HTTP/readback checks, and port
  bindings restricted to `127.0.0.1`.
- Resource acquisition: for nontrivial "get this file/page/artifact/resource"
  work, submit a structured request through `resource_cli.py request` first
  when a local broker can classify the target and produce a receipt. The
  broker may execute local safe `resource_cli` paths and supported read-only
  owner adapters, then persists a manifest, preview, metadata, and artifact
  references when available. GitHub metadata may execute through Hub
  `github.api`; package metadata may execute through bounded package-manager
  read paths. Owner MCPs that are only callable in the active Codex turn, such
  as Context7, Microsoft Docs, Playwright, MarkItDown, or browser tools, return
  a structured `handoff_required` contract with the owner call arguments and
  attach-result route; Codex must call that owner tool and attach the result
  instead of treating local probe metadata as the resource. For batches, the
  scheduler asks `codex_network_gateway.py batch-plan` once, attaches each
  returned plan to the request metadata, and lets the broker consume it. For
  single requests, the broker asks `codex_network_gateway.py plan` as fallback.
  `resource_network_execution.py` translates that gateway plan into URL attempt
  specs and owner-tool network guidance. Target-only owner requests still get
  gateway route evidence by using representative owner targets such as
  `https://api.github.com/`, `https://learn.microsoft.com/`, package
  registries, `https://api.openalex.org/`, `https://commons.wikimedia.org/`,
  `https://huggingface.co/datasets`, or `https://example.com/`; these are
  route/cache probes for GitHub/docs/packages/papers/images/datasets/web, not
  resource-source decisions. Gateway route cache entries carry `owner_tool`
  plus a target-profile version, so Context7/Microsoft Docs/GitHub/browser or
  resource-router requests do not collapse into one host-only cache entry, and
  changed probe/source profiles invalidate stale cached plans instead of
  reusing obsolete node evidence.
  Resource ownership stays in the resource layer, while network route selection
  stays in the gateway. Explicit online research requests already authorize this
  source-owner routing for read-only evidence gathering, but not external
  writes, permission changes, message sending, or secret access.
- Agent-native CLI harnesses: use the installed `cli-anything` skill and
  `cli-hub` when a GUI app, desktop app, repository, or workflow should become
  a structured CLI. Discover through `_bridge/cli_anything_governance.py`
  first; installing a specific harness is a package-manager action and still
  needs explicit task intent plus validation.
- Developer toolchain: use `rg` for broad text search, `fd` for fast file
  discovery when available, `uv` for stable Python tool environments, `uvx`
  for one-shot Python CLI tools, `ruff` for targeted Python lint checks, and
  `playwright` for browser evidence. These tools are utility routes, not new
  governance systems; use them where they reduce ad hoc scripting or make
  validation faster.

## Tool Advantage Rule

The goal is not merely to keep tools callable; it is to let each tool do the
work it is best at while the rest of the system stays quiet and stable.

- Memory and skills are the context layer. Use them before repeating prior
  investigations, changing established systems, or making policy decisions.
- `custom-slash-commands` is the workflow layer. Use it to start repeatable
  flows and produce checklists; never treat rendered text as execution.
- `codegraph`, `rg`, and document/search MCPs are the discovery layer.
  `codegraph` is the first choice for source structure, symbol flow, call
  paths, and blast radius; `rg` answers broad text existence after the target
  is known or when CodeGraph is unavailable/insufficient; docs MCPs answer
  current external APIs.
- `filesystem`, `apply_patch`, backup-router, and module CLIs are the file
  operation layer. Prefer the most structured owner for writes; verify with
  readback, hashes, schema checks, or tests.
- SQLite MCPs are the structured state layer. For queue, task, delivery,
  receipt, scheduler, inbox/outbox, record-store, `.sqlite`/`.db` files,
  index-backed resources, or database-backed status questions, use SQLite MCP /
  Hub read-only queries before broad log scanning, `rg`, file walking, or large
  CLI dumps. Use `sqlite-bridge-ro` for read-only bridge evidence,
  `record_store.sqlite` for indexed record/resource evidence,
  `email_state.sqlite` for derived mail scheduler state, and `sqlite-scratch`
  for task-scoped scratch state. Production repairs still must go through the
  owning business maintenance CLI/API; do not directly mutate production
  databases to "fix" state.
- `mobile-openclaw-bridge`, `agent-bridge`, GitHub, browser, and GUI MCPs are
  domain gateways. Use them when the target state lives outside local files;
  do not substitute local files, git status, or screenshots for authoritative
  domain evidence.
- `local-mcp-hub`, `mcp_session_doctor`, and maintenance tools are the
  stability layer. They should make tool failures bounded and observable, not
  hide or normalize failures.
- `cli-anything` and `cli-hub` are the harness layer. Use them to turn useful
  external software capabilities into repeatable CLI tools instead of writing
  one-off scripts or fragile GUI automation.
- `execution_route_pack.asset_guidance` is the admission-time navigation layer.
  It derives a small ordered set of rules, skills, owners, and tools from the
  existing workflow authorities and explains each asset's advantage and skip
  boundary. Use that guidance to shape real work; do not call tools only to
  generate usage evidence. `_bridge/tool_utilization_audit.py` remains an
  explicit diagnostic/forward-test surface for routing maintainers, not a
  routine task step or closeout gate.

Anti-patterns:

- Do not start with shell when an owning MCP can provide a smaller, more
  structured answer.
- Do not call many MCPs just to appear thorough; choose the smallest set that
  covers the task and validation.
- Do not let a fallback result masquerade as native current-turn callability.
- Do not use diagnostic or write-capable Hub gateway calls without their exact
  acknowledgement and the target tool's original permission contract.
- Do not leave useful tool-learning only in chat context; promote stable
  lessons into this matrix, AGENTS rules, skills, or memory through the normal
  approval and backup process.

## Classified MCP Priority

Stateless and owner-service capabilities are Hub-first. Current Chrome, browser,
GUI, and mobile-thread capabilities are native-current-session first. Stable
owner status and maintenance operations may be owner-CLI-first. A Hub or fresh-
stdio result must not be recorded as native current-turn availability.

Machine source of truth: `_bridge/mcp_execution_priority.py`. Every logical
profile has explicit `execution_affinity`, `session_binding`,
`registration_mode`, `desktop_instance_budget`, `startup_mode`,
`startup_child_budget`, lifecycle, and reason fields.
`hub_managed` profiles must not appear in Codex Desktop `config.toml`; direct
Hub mappings handle common tools and the governed fresh-stdio gateway exposes
the remaining tool surface under the same permission boundary. Unknown tools
never silently default to native-first.

Heavy session-bound profiles use `_bridge/mcp_lazy_stdio_proxy.py` in the
launcher process, avoiding a second waiting Python parent per task: Desktop
starts a small per-task catalog proxy, `initialize` and `tools/list` are served
from an atomic command-fingerprinted cache, and the guarded real child starts
only on the first non-catalog call. The child then remains dedicated to that
task so browser, GUI, and editable-diagram state are preserved. Refresh caches
with `python _bridge\mcp_profile_launcher.py <cdev|pw|gui|drawio> --warm-cache`;
protocol smoke uses `--eager` so validation still exercises the real server.

| Profile | Registration | Default priority | Lifecycle / binding |
| --- | --- | --- | --- |
| `codegraph`, `gitnexus`, `graphify`, `openai-docs`, `context7`, `github`, `markitdown`, `microsoftdocs`, `custom-slash-commands`, `sqlite-bridge-ro`, `local-pmb-memory`, `filesystem-admin`, `filesystem`, `sqlite-scratch`, `myskills` | `hub_managed` | `hub_first` | Desktop budget 0; direct Hub for mapped tools, fresh-stdio gateway for the full tool surface, process exits after each call |
| `local-mcp-hub` | `desktop_native` | `hub_first` | Shared control-plane process; Desktop budget 1 |
| `node_repl` | `desktop_native` | `session_native_first` | Bound to `current_repl_kernel`; Desktop budget 1 |
| `chrome-devtools`, `playwright`, `next-ai-drawio`, `gui-automation` | `desktop_native` | `session_native_first` | Desktop proxy budget 1, heavy-child startup budget 0; guarded child starts on first non-catalog call and remains bound to the current browser, diagram, or GUI session |
| `desktop-weixin` | `desktop_native` | `session_native_first` | Bound to current desktop chat/draft state; Desktop budget 1 |
| `mobile-openclaw-bridge` | `desktop_native` | `owner_cli_first` | Supplement tools override to current mobile thread; Desktop budget 1 |
| `agent-bridge` | `desktop_native` | `native_first` | Claim/send/complete state has no equivalent complete Hub adapter; Desktop budget 1 |

Tool topology:

- Tool tiers:
  - Tier A: high-frequency core tools that should be Hub-first when stateless and have a
    bounded continuity route: memory, filesystem, codegraph, sqlite, slash,
    GitHub, bridge, and Hub.
  - Tier B: useful but heavier or task-specific tools that should start on
    demand and recover once: docs, browser, conversion, and elevated/admin
    helpers.
  - Tier C: heavyweight UI/GUI paths. Use only when the task needs the surface,
    and prefer API/CLI evidence when it can prove the state.
  - Tier R: retired profiles. They may be migration sources, not active routes.
- `local HTTP MCP hub`: native MCP over localhost HTTP. This is the preferred
  stable route for core low-risk tools when current-turn callable.
- `daemon-backed stdio proxy`: durable state lives in a local daemon; stdio is
  a replaceable Codex-facing proxy. PMB uses this model.
- `local stateless stdio`: each server instance is cheap to start and holds no
  durable session state. SQLite and custom slash commands use this model.
- `external stateless stdio`: an external MCP server is disposable from Codex's
  point of view; an allowlisted Hub read route is the read entry stage, followed
  by Hub gateway/local project continuity. Filesystem reads use this model;
  filesystem writes use their explicit native-first override.

## Shared Loop

Use this loop for nontrivial work:

1. Recall relevant memory and load the narrowest relevant skill.
2. Render a `custom-slash-commands` workflow template if a fixed flow exists.
3. Call the owning MCP through its classified affinity: `hub_first`,
   `session_native_first`, or `owner_cli_first`.
4. Verify with the tool's own result plus a targeted read, smoke, or doctor.
5. If the selected route fails, continue within the same permission boundary.
   The profile selects only the entry stage; every later failure moves forward
   through the fixed fallback sequence and never jumps backward to an earlier
   stage. Session-bound failure may require a structured `handoff_required`
   result rather than substituting a fresh browser/GUI/thread.
6. Close out with a memory, skill, baseline, or no-persistence decision.

Evidence levels are separate: `config_ok`, `protocol_ok`,
`current_turn_exposed`, `current_turn_callable`, and `call_completed`.

Routing uses one fixed forward sequence with affinity-specific entry points:

1. Native stage: precise discovery, then current-turn native MCP call.
2. Hub-direct stage: known same-boundary Hub aliases, including
   `codegraph.explore`, `github.api`, PMB/SQLite/resource tools, and
   `owner_mcp.call_readonly` for OpenAI Docs, Context7, Microsoft Docs, filesystem reads,
   and MarkItDown.
3. Hub gateway stage: use `mcp_gateway.complete_route` only when the direct Hub
   mapping is unknown, ambiguous, schema/permission conversion is unclear, or
   compact diagnostic route evidence is required.
4. Local Hub CLI/Python: use only when Hub MCP is unavailable, not exposed,
   transport-closed, insufficient, or cannot preserve the mapping cleanly.
5. Owner CLI fallback: use only after the Hub/local Hub path cannot complete the
   same-boundary route.
6. Terminal local read: use targeted `rg`/file reads only after the
   same-boundary Hub, local Hub, and owner CLI route is unavailable,
   insufficient, or explicitly inapplicable; record that route evidence instead
   of treating native failure alone as permission to jump to local structure.

`native_first` and `session_native_first` enter at stage 1; `hub_first` enters
at stage 2 and therefore does not jump backward to native after Hub failure;
`owner_cli_first` enters at stage 5. `unclassified` enters the Hub gateway stage
for route resolution. Once entered, all profiles move only toward later stages.

If Hub MCP is unavailable, not exposed, or cannot complete the same-boundary
route, use the local CLI fallback before any profile-specific local fallback:

```powershell
python _bridge\mcp_session_doctor.py complete-route --profile <profile> --tool <tool> --status transport_closed --arguments-json "{}"
```

This command is the same-boundary local fallback when Hub is unavailable, not
exposed, or cannot provide a known direct route or Hub MCP `complete_route`
result. It
records the current-turn negative observation, runs the local gateway route,
attempts the fresh stdio/proxy/session call when the profile supports it, and
returns either `route_complete` or a concrete same-boundary blocker plus the
profile fallback commands. It must not call Hub again, repeat the dead native
handle, escalate permissions, or claim restored current-turn callability until a
real native MCP tool call completes and a positive observation is recorded.

## Orchestration MCPs

### `local-mcp-hub`

- Use when: a core low-risk local tool is available through the localhost HTTP
  MCP hub according to the classified affinity, or when a current-session route
  needs controlled same-boundary continuity.
- Optimize calls: call `hub.capabilities` first when the right Hub entry is
  unclear. Prefer hub-native tools for slash templates, read-only SQLite
  inspection, PMB read/prepare, CodeGraph Hub-first explore, Chrome DevTools
  governed aliases after native failure, desktop Weixin same-safety tools,
  GitHub same-credential proxy calls, GitHub App auth diagnostics,
  agent-bridge read-only status, and MCP
  maintenance checks. For
  higher-risk capabilities, still follow their classified affinity rather than
  applying a global native-first rule. Use
  `mcp_gateway.route`/`mcp_gateway.call` for known fresh-stdio gateway routes,
  and `mcp_gateway.complete_route` only when the route needs dynamic resolution
  or diagnostic evidence.
- Topology: local HTTP MCP hub on `127.0.0.1:18881`. It exposes `slash.*`,
  read-only SQLite aliases such as `sqlite_scratch_query`,
  `sqlite_bridge_query`, `record_store_query`, and `email_state_query` for
  Codex/tool-search discovery after tool metadata refresh. In the current Codex MCP mapping, the
  dotted Hub names are exposed as `sqlite_scratch_sqlite_query`,
  `sqlite_bridge_sqlite_query`, and `record_store_sqlite_query`; use those when
  they are visible in the active turn. The dotted `sqlite_scratch.*`,
  `sqlite_bridge.*`, and `record_store.*` names remain internal-compatible
  routes, and the Hub also exposes PMB read/prepare tools, `codegraph.explore`,
  `gitnexus.list_tools`, `gitnexus.call`, `graphify.list_tools`, and
  `graphify.call`. If an existing Codex turn has not refreshed these aliases,
  invoke them through Hub `hub.call` with its required acknowledgement; this is
  still the same Hub MCP route. The Hub also exposes
  `chrome_devtools.*`, `desktop_weixin.*`, `github.api`, `github.gh`,
  `github_app.snapshot`, `github_app.doctor`, `github_app.validate`,
  read-only `resource_search.text`, `resource_search.images`,
  `resource_search.news`, `resource_search.videos`, `resource_search.books`,
  and `resource_search.extract`,
  `resource.request`, `resource.status`, read-only `network.snapshot`,
  `network.recommend`, `network.env`, `network.plan`, `network.probe`,
  `network.probe_suite`, `network.validate`,
  `resource.progress`, `resource.attach_result`, `mobile_bridge.get_pending_batch`,
  `mobile_bridge.ack_message`, `agent_bridge.status`, governed `mcp_gateway.*`, `hub.*`, and
  `mcp_session.*`. For manual Codex use, prefer native current-turn MCP when it
  is callable. For automation owners such as the resource layer, Hub/fresh-stdio
  gateway calls are allowed as first-class same-boundary execution routes when
  they carry the required acknowledgement and audit metadata; a prior native
  failure is no longer a hard gate. In the Hub catalog,
  `mcp_gateway.complete_route` is
  the diagnostic/dynamic route handler for ambiguous mappings or route-evidence
  requirements. Use the CLI `mcp_session_doctor.py complete-route` only when Hub
  is unavailable, not exposed, or cannot complete the same-boundary route.
- Direct Hub map: GitHub -> `github.api` / `github.gh`; resource layer ->
  `resource.request` / `resource.status` / `resource.progress` /
  `resource.attach_result`; resource-layer generic source discovery and
  explicit-URL extraction -> `resource_search.*` through the registered
  `generic_search` owner adapter; mobile bridge supplements ->
  `mobile_bridge.get_pending_batch` / `mobile_bridge.ack_message`;
  OpenAI Docs, Context7, and Microsoft Docs -> known `mcp_gateway.call` with
  profile `openai-docs`, `context7`, or `microsoftdocs` and exact target
  tool/arguments. Do not use
  `mcp_gateway.complete_route` when one of these direct mappings fits.
- Capability map: `hub.capabilities` separates `fresh_stdio_profiles` that can
  go through `mcp_gateway.call` from `profile_specific_fallback_profiles` such
  as Node REPL and protected agent-bridge mutations. GitHub is hub-native
  through `github.api` and `github.gh`; Chrome DevTools is hub-native through
  `chrome_devtools.*` aliases that still call the governed `chrome-devtools`
  gateway; desktop Weixin is hub-native
  through the same `desktop_weixin.*` schema and is also a fresh-stdio profile;
  agent-bridge is hub-native only for read-only status. Resource broker is
  hub-native for same-boundary request/status receipts. Generic search is
  hub-native, read-only, bounded to the project-managed DDGS dependency, and
  receives per-request network route/proxy evidence from the gateway; it does
  not download, install, or write persistent state. Do not claim Hub can
  proxy a profile unless it appears in the fresh-stdio set or has a hub-native
  tool.
- Avoid: using `mcp_gateway.call` to invent a new permission path. File writes,
  filesystem-admin, GUI/browser, mobile
  OpenClaw bridge mutation, SQLite writes, and PMB memory writes remain
  governed by their owning MCP/server/wrapper permissions; Hub only carries the
  same-boundary call and must preserve the target tool's safety schema. Desktop Weixin actions
  exposed through Hub keep the same MCP safety schema, including
  `confirm_send=SEND` for sends. GitHub writes are allowed through Hub because
  GitHub is high-frequency, but only with environment token, Secret Vault
  backed GitHub App aliases, Secret Vault `github.token`, or `gh` keyring
  permissions and the explicit write ack:
  `github-write-through-hub-uses-existing-permissions`.
- Validate: `python _bridge\local_mcp_hub.py validate` and
  `python _bridge\local_mcp_hub.py smoke --host 127.0.0.1 --port 18881`.
  Validation must fail when no owned listener is present or `/health` is not
  ready; a static tool catalog or scheduled-task definition alone is not
  runtime availability. WSL process control uses the Windows interop
  executables `powershell.exe`, `schtasks.exe`, and `taskkill.exe`.
  After Hub source changes, use
  `python _bridge\local_mcp_hub.py reload --confirm-reload` when the running
  HTTP Hub still exposes an older tool set. This command stops only
  `local_mcp_hub.py serve` processes on the target port and restarts the
  `CodexLocalMcpHub` scheduled task through a hidden no-window path.
- Fallback: continue forward through the generated Hub gateway, local Hub, and
  owner CLI stages; do not jump backward to an earlier native stage.

### `custom-slash-commands`

- Use when: a task matches a repeated workflow, checklist, repair flow, or
  closeout decision.
- Optimize calls: render `workflow-router` for unclear tasks, then render only
  the one or two domain templates that will guide the next action.
- Topology: local stateless stdio. Hub slash aliases are the read entry stage;
  failures continue forward to Hub gateway/local registry validation and rendering.
- Avoid: treating rendered text as execution, permission, or proof.
- Validate: `slash_validate_registry`; for encoding, render a Chinese variable
  smoke such as `intent=测试中文`.
- Fallback: use the relevant skill/checklist manually and record the missing
  current-turn tool evidence.

### `cli-anything` / `cli-hub`

- Use when: the task asks to build, refine, validate, list, install, or use an
  agent-native CLI harness for a GUI app, desktop app, codebase, or workflow.
- Optimize calls: read the installed `cli-anything` skill for build/refine/test
  methodology; use `_bridge/cli_anything_governance.py search <query>` or
  `info <name>` before installing; prefer `cli-hub` JSON discovery for
  machine-readable catalog evidence. For installed harnesses, first run
  `_bridge/cli_anything_governance.py installed` or
  `_bridge/cli_anything_governance.py commands <entrypoint>` to discover the
  current command surface from `--help`, then call the specific command.
- Topology: trusted project CLI plus installed Codex skill, not an MCP. The
  governance wrapper disables `cli-hub` analytics for Codex-managed
  invocations and keeps default operations read-only.
- Avoid: treating registry trust as permission to install arbitrary harnesses
  silently, bypassing package-manager review, or replacing existing owning
  MCPs when a native MCP is already the authoritative interface.
- Validate: `_bridge/cli_anything_governance.py validate`, installed skill
  resource checks, `cli-hub --version`, `cli-hub list --json`, and harness
  post-install `--help`/`--json`/test commands for any concrete harness.
- Fallback: if `cli-hub` is unavailable, use the installed skill methodology
  and the source repository directly; if the skill is unavailable, clone
  `HKUDS/CLI-Anything` and read `cli-anything-plugin/HARNESS.md`.

### Resource request broker

- Use when: the task needs a local file, URL, document, artifact, dependency,
  or external resource and the right owner tool is not obvious, or when Codex
  should receive a durable receipt instead of juggling ad hoc probes.
- Optimize calls: call `python _bridge\resource_cli.py request --path ...` or
  `--url ... --intent ... --json`. Add `--auto-owner` when supported read-only
  owner adapters should run before returning a handoff. Use
  `--need-materialization` plus `--allow-filesystem-write` only when a local
  artifact is actually required.
  Read receipts with `python _bridge\resource_cli.py status --request-id ...`.
  When the broker returns `handoff_required`, treat it as an internal
  intermediate state for the same resource need: call the requested owner
  MCP/tool, or let the resource layer route to web if that is its selected
  path, then write the result back to the same request with
  `resource.attach_result` or `python _bridge\resource_cli.py attach-result ...`.
  Do not start an independent replacement fetch for the same need while
  `same_need_fetch_allowed=false`.
- Topology: project CLI broker plus Hub-native wrapper. It classifies through
  `resource_router.py`, executes local safe `resource_cli` attempts and
  supported read-only owner adapters through `resource_owner_executor.py`
  (`github`, `package_manager`, `openai-docs`, `microsoftdocs`, `context7`, `markitdown`,
  `playwright`, and `chrome-devtools`), logs
  progress to `_bridge/logs/resource-broker-events.jsonl`, and writes receipts
  to `_bridge/logs/resource-broker-receipts.jsonl`. Hub-backed owner execution
  uses `resource_owner_hub_adapter.py` for known Hub tools and governed
  `mcp_gateway.call` / fresh-stdio routes, preserving the target tool's
  permission boundary. Every request writes a
  manifest under `_bridge/resources/_requests/<request_id>/manifest.json`; URL
  previews also write `preview.txt`, and artifacts keep their sha256-addressed
  cache path. URL acquisition attaches route evidence from `network_policy.py`
  so Codex can distinguish target DNS/proxy/path failures from resource policy
  failures, and includes a compact next-action hint for network-sensitive
  targets. Hub exposes `resource.request`, `resource.status`,
  `resource.progress`, and `resource.attach_result`. Use `resource.progress` or
  `python _bridge\resource_cli.py progress ...` as the default conversation
  readback; it compresses receipts/manifests/batches into status, next action,
  owner handoff, network route, and paths without loading full logs.
  `resource_cli.py route --target ...` is the read-only way to inspect
  target-only routes, such as a library/framework documentation request before
  execution. Supported owner adapters return a normalized `owner_result`
  envelope with source tool, result kind, title, URL/citations, summary,
  execution route, permission boundary, and next action so downstream Codex
  work does not need to parse each MCP's native text shape.
- Authorization: submitting a resource request authorizes Codex/resource layer
  to orchestrate necessary owner tools for resource acquisition, including
  dependency/package acquisition planning. It does not authorize destructive
  local changes, permission changes, secret extraction, message sending, or
  unrelated remote writes. Install/package-manager steps must carry source,
  version, scope, side-effect, and risk evidence so Codex can judge whether to
  execute inside the resource-acquisition grant or request separate approval.
- Avoid: treating `handoff_required` as failure or as permission to bypass the
  resource job with an independent Codex fetch; treating HEAD/probe metadata as
  fetched content; using the broker to bypass Context7, Microsoft Docs, GitHub,
  browser, MarkItDown, package-manager, or Chrome DevTools permissions.
  The broker may automate supported read-only adapters through Hub/fresh-stdio;
  unsupported or side-effecting operations still return a handoff or approval
  requirement.
- Validate: `python _bridge\resource_fetcher_tests.py` plus one local-file
  request, one preview URL request, and one owner-MCP URL request. Local-file
  requests should return `completed/artifact`; preview requests should return
  `completed/preview`; owner-MCP/package-manager cases may first return
  `handoff_required`, but completed read-only owner requests should include a
  normalized `owner_result` and the receipt must include `owner_execution` with
  concrete owner call arguments, and the completed workflow must attach the
  owner result back to the same manifest/receipt. For GitHub metadata with
  `--auto-owner`, verify `owner_execution_route=local_hub_github_api` or a
  recorded read-only fallback.
  Validation is profile-based: `quick` avoids live network/package-index/MCP
  dependencies and may use fixtures or short caches; `smoke` runs one bounded
  live representative per owner class while still using package fixtures;
  `full` and `live` allow real package-index/network probes. Use
  `resource_cli.py scenario-smoke --mode quick|smoke|full|live` rather than
  folding all live paths into the default regression test.
  Runtime optimization is also profile-bounded: network route plans are
  batched through the network gateway where possible. The resource layer's
  in-process network-plan cache is only a near-call coalescing guard; durable
  route freshness, stale-while-revalidate behavior, and circuit evidence belong
  to the network gateway. Request timeout remains an execution budget rather
  than a route-cache identity. Completed read-only owner results may use short
  in-process TTL caches and a bounded runtime disk cache for repeated
  GitHub/docs/Context7/URL MarkItDown lookups across short-lived CLI
  processes. Package metadata may use the PyPI JSON API before slower
  `pip index` fallback, and may cache explicit PyPI 404 results for a short
  negative TTL so repeated missing-package checks fail fast without treating
  transient network errors as package absence. If the gateway plan already
  proves no usable route, URL fetchers should return `network_route_unavailable`
  instead of spending the request timeout on the same route. These caches do not
  write persistent memory, do not cache generic network failures/handoffs,
  browser state, local file conversions, permission decisions, or network
  route decisions. Large or resumable URL materialization may request
  `download_backend=auto|curl|aria2` or `resume_download=true`; the resource
  layer then uses optional curl/aria2c process backends when available while
  preserving the same request receipt, network route metadata, and permission
  boundary. The backend layer does not install tools, run aria2 RPC daemons, or
  mutate global proxy/DNS settings. Windows tool requests may use the
  Chocolatey/winget package-manager adapter for search and install planning;
  actual installs require explicit `package_action=install` and
  `install_approved=true` metadata, use only per-process network guidance, and
  must not mutate global proxy/DNS or package-manager configuration. Python
  package requests use the same approval gate plus
  `allow_filesystem_write=true`; approved installs always use pip `--target`
  isolation, verify installed distribution metadata, and never write global
  site-packages. An explicit `--target-dir` is preserved; otherwise the
  resource layer uses `_bridge/runtime_dependencies/<package>`. Quick
  validation profiles may shorten metadata/search work but must not downgrade
  an explicitly approved install into a plan-only result. Codex
  should represent external tool/package installs as `resource_cli.py job run`
  requests first; direct `choco`/`winget`/package-manager commands are fallback
  evidence only after a resource-layer receipt releases or blocks the same
  resource need.
  policy, and do not replace full/live validation coverage.
- Fallback: if the broker is unavailable, use `resource_cli.py route` for a
  read-only plan, then call the owner MCP or local resource command directly
  under the same permission boundary.

### Memory absorption candidates

- Use when: external memory/knowledge projects suggest reusable patterns for
  local memory, PMB, note absorption, external knowledge capture, or recall
  verification.
- Optimize calls: keep the existing local stack as the owner. Evaluate candidate
  patterns against `memory_governance.py absorb-plan`,
  `pmb-organize-plan`, `recall-checks`, `recall-verify`, work notes, and
  `_bridge/external_knowledge.py`. Prefer local-first evidence, source
  attribution, lifecycle status, temporal/drift handling, deduplication, and
  queryability.
- Topology: capability-source pattern only. `agent-memory-engine`,
  `ArcRift`, and `localmem` are not active dependencies unless explicitly
  installed later with a separate validation plan.
- Avoid: writing raw incident noise into long-term memory, adding a second
  uncontrolled memory database, bypassing approval for memory writes, storing
  secrets, or treating external project popularity as local suitability.
- Validate: `python _bridge\memory_governance.py validate`,
  `python _bridge\memory_governance.py recall-verify`, and
  `python _bridge\external_knowledge.py doctor` when sources were captured.
- Fallback: memory folder quick pass, PMB read-only recall, and approved
  ad-hoc note proposals under the normal memory rules.

### Developer toolchain utilities

- Use when: code or workspace work benefits from fast search, file discovery,
  repeatable Python environments, quick Python quality checks, or browser
  verification.
- Optimize calls: `rg` is the default broad search tool with generated-tree
  exclusions. Use `fd` for file discovery when available and simpler than
  `rg --files`. Use `uv` for reproducible Python tool execution or isolated
  environments when a task needs package-managed tooling. Use `uvx` for
  one-shot Python CLI tools that should not become permanent global installs.
  Use `ruff check` for fast lint feedback on targeted changed Python files.
  Use `playwright` only when UI or browser state must be proven.
- Topology: utility CLIs owned by the task, not MCPs. They support the owning
  module, doctor, or validation command rather than replacing it.
- Avoid: making package-manager changes without explicit need, broad formatting
  churn, hidden dependency upgrades during unrelated work, or using browser
  automation to prove states better answered by an API/doctor.
- Validate: `python _bridge\code_maintainability.py toolchain`, version smoke
  for the tool being used, targeted command result, and the owning module's
  validator or readback.
- Fallback: built-in shell/Python equivalents, but record if a missing utility
  made the task slower enough to justify a later installation proposal.

#### Local harness: `cli-anything-weixin`

- Use when: the task is about the Windows desktop Weixin app itself, not the
  mobile OpenClaw bridge or delegated task queue, and the `desktop-weixin` MCP
  is unavailable or needs CLI validation.
- Optimize calls: prefer `desktop-weixin` MCP for normal agent work. Use
  `cli-anything-weixin --json status` before any CLI fallback action;
  or use `_bridge/cli_anything_governance.py commands cli-anything-weixin` when
  the exact command surface may have changed. Use `activate` and `screenshot`
  for evidence. Current command groups cover visible chat row selection,
  search and clear-search, emoji panel smoke, file picker smoke, verified draft
  smoke, draft preparation, and explicit-confirmation text sending.
- Topology: local editable CLI-Anything harness at
  `_bridge/cli_anything_weixin/agent-harness`, installed as
  `cli-anything-weixin`.
- Avoid: chat transcript extraction, login automation, contact changes, calls,
  payments, or sending without explicit approval. `draft send-current` and
  `message send-text` require `--confirm-send SEND`; `message prepare` requires
  `--confirm-prepare DRAFT`.
- Validate: `cli-anything-weixin --help`, `cli-anything-weixin --json status`,
  guarded-send refusal, and package tests under
  `_bridge/cli_anything_weixin/agent-harness/cli_anything/weixin/tests`.
- Fallback: use `gui-app-weixin` plus `gui-automation` directly when the harness
  lacks a needed workflow.

### `desktop-weixin`

- Use when: operating the Windows desktop Weixin app through the installed
  CLI-Anything Weixin harness. This is the normal structured route for desktop
  Weixin actions.
- Optimize calls: start with `desktop_weixin.status`. If no usable window is
  visible, use `desktop_weixin.open`; if a minimized/offscreen window exists,
  use `desktop_weixin.activate`. Use `desktop_weixin.screenshot` for evidence.
  Use `desktop_weixin.chat_search` or `desktop_weixin.chat_select_row` to
  target a conversation, then `desktop_weixin.message_prepare` for draft-only
  workflows or `desktop_weixin.message_send_text` only after explicit send
  approval. Use `desktop_weixin.close` only with `confirm_close=CLOSE`.
- Topology: local stateless stdio MCP launched by
  `_bridge/mcp_profile_launcher.py weixin`, wrapping
  `_bridge/desktop_weixin_mcp_server.py`, which in turn calls the editable
  `cli-anything-weixin` Python backend. It is configured as non-blocking at
  startup so desktop UI state cannot block Codex conversation recovery.
- Extension model: new desktop Weixin abilities should first be added to
  `cli_anything.weixin.core.windows` with tests, then exposed as a bounded
  `desktop_weixin.*` MCP tool with a concrete schema. Do not add a free-form
  command executor.
- Avoid: treating desktop Weixin MCP as the mobile OpenClaw bridge; extracting
  chat history; automating login, payments, contact mutation, calls, or sending
  without explicit confirmation. Sending tools require `confirm_send=SEND`;
  closing the window requires `confirm_close=CLOSE`; draft preparation requires
  `confirm_prepare=DRAFT`; smoke tools require their exact smoke token.
- Validate: direct initialize/tools-list smoke for
  `_bridge/desktop_weixin_mcp_server.py`, `desktop_weixin.capabilities`,
  guarded-send refusal without `confirm_send`, and the existing
  `cli-anything-weixin` package tests.
- Fallback: after native current-turn failure, use `cli-anything-weixin` CLI for
  the same bounded action. If the harness lacks the needed capability, use
  `gui-app-weixin` plus `gui-automation` and then promote the useful operation
  back into the harness/MCP extension path.

### `myskills`

- Use when: session skill metadata is insufficient or a skill source needs
  discovery.
- Optimize calls: query for the named skill or narrow domain, then read the
  returned `SKILL.md` directly.
- Avoid: loading broad skill trees or trusting metadata without reading the
  selected skill body.
- Validate: selected skill path exists and the full file was read.
- Fallback: after native `myskills` current-turn failure, use the known
  same-boundary Hub gateway call for the target tool when mapped. Use
  `mcp_gateway.complete_route` or CLI `mcp_session_doctor.py complete-route`
  only when the route is unclear, diagnostic evidence is required, or Hub is
  unavailable. Use the session skill list and direct filesystem read only after
  direct Hub call evidence, `route_complete`, or a concrete same-boundary
  blocker is present.

## Files And Conversion MCPs

### `filesystem`

- Use when: compatibility with a restricted filesystem profile is specifically
  required, or the configured `filesystem-admin` route has failed and the
  forward fallback chain reaches this narrower profile.
- Optimize calls: use `read_text_file` for one known file when available,
  `read_multiple_files` for 2+ known files or rule/config comparisons, and
  `list_directory` / `list_directory_with_sizes` for bounded structure or size
  checks. Prefer `codegraph` for indexed source structure and call paths, and
  prefer `rg` for broad text search, symbol discovery, or generated-tree
  exclusions.
- Write rule: use `apply_patch` for manual code edits. Use the owning project
  CLI or maintenance command for governed paths. Use filesystem writes only for
  simple complete-file generated artifacts or explicitly approved full-file
  replacements, after the backup route is satisfied and with readback
  validation.
- Topology: external stateless stdio. Hub `owner_mcp.call_readonly` is preferred
  for bounded reads; native MCP, PowerShell `-LiteralPath`, Python `Path`, and
  `apply_patch` are continuity or write-specific routes.
- Avoid: broad recursive scans, generated/browser/cache trees, admin paths,
  permission-boundary changes, and patch-style code edits through complete-file
  overwrite.
- Validate: targeted readback, hash/JSON/schema/UTF-8 validation as appropriate.
- Fallback: PowerShell `-LiteralPath`, Python `Path`, or `apply_patch`, with the
  same approval, backup, and permission rules.

### `filesystem-admin`

- Use when: performing bounded file inspection or an authorized file operation
  within its configured roots. It is the default filesystem MCP profile; the
  selected tool still determines whether the call is read-only or mutating.
- Route: it is `hub_managed` and must not be registered as a Desktop stdio MCP.
  Allowlisted reads start at Hub `owner_mcp.call_readonly`; authorized writes,
  moves, edits, and directory creation start at Hub `mcp_gateway.call`, which
  launches the existing filesystem-admin profile under the same permission
  boundary and exits after the call.
- Optimize calls: treat it as an elevated version of filesystem. Prefer
  read-only `list_directory`, bounded file reads, and `read_multiple_files` for
  explicit known paths. Make one narrow operation per call and record that the
  elevated boundary was used.
- Write rule: writes, moves, deletes, and repairs require explicit task scope,
  the planned backup route, one narrow target set, and readback/owner-validator
  evidence. It is not a convenience route for bypassing normal filesystem or
  module ownership.
- Avoid: treating default profile selection as write approval, broad recursive
  scans, recursive destructive moves/deletes, generated-cache sweeps, or using
  it to bypass a module owner's permission contract.
- Validate: readback plus an explicit note that the elevated boundary was used;
  for writes, also validate backup metadata and the owner module's validator
  when one exists.
- Fallback: if the direct read adapter or same-boundary gateway is unavailable,
  use the governed local owner route or an already-approved native admin-safe
  command; do not silently re-register an eager Desktop filesystem-admin MCP.

### `markitdown`

- Use when: converting PDF, Office, HTML, or rich documents into Markdown/text.
- Optimize calls: convert once, then inspect the generated text with targeted
  searches.
- Avoid: using OCR/conversion for plain text files or code.
- Validate: output exists and sampled headings/body text are readable.
- Fallback: bundled workspace document libraries or format-specific CLI tools.

## Code And Runtime MCPs

### `codegraph`

- Use when: understanding code architecture, call paths, symbol impact, blast
  radius, or editing indexed code. For non-simple code changes, ask CodeGraph a
  specific structure/impact question before falling back to broad `rg` or
  manual reads.
- Optimize calls: ask one specific architecture or symbol-flow question; include
  exact file paths and domain-specific symbols, and cap `maxFiles`.
- Avoid: repeated calls after `Transport closed`, using it for broad
  Markdown/config/rule lookups, or using generic anchors such as `rules`,
  `map`, `main`, `build_parser`, or `validate` as the primary query terms.
- Validate: response contains relevant source. Hub and native CodeGraph both
  use `_bridge/codegraph_query_runtime.py`: it validates the SQLite index,
  returns analysis immediately from a usable index, reports target freshness
  as `fresh`, `stale`, or `unknown`, and coalesces status/sync into a bounded
  background refresh. Freshness uncertainty is degraded evidence, not a
  terminal query failure; a missing, corrupt, incomplete, or empty index is
  terminal. For strict maintenance, run `_bridge/codegraph_health.py freshness
  --target <path>` and `_bridge/codegraph_health.py ensure-fresh --target
  <path>`. Run `mcp-session smoke --profile codegraph` for
  protocol/tools-list evidence and `_bridge/codegraph_health.py validate` when
  index relevance or freshness matters; run tests/lints separately for
  correctness. Treat protocol smoke as backend readiness only, not proof that
  the current Codex turn can call native CodeGraph.
- Route: Hub `codegraph.explore` is the default stateless path. If it fails,
  use native CodeGraph when current-turn callable. If neither same-capability
  route works, use `_bridge/codegraph_health.py` for health,
  then `rg` with generated tree exclusions plus targeted reads for the current
  task.
  For the WSL Work Git repository root, the Windows Hub resolves the owner and
  CLI under `workspace/_bridge`. When only `workspace/.codegraph/codegraph.db`
  exists, the query project also resolves to that nested workspace;
  repositories with a top-level index and `_bridge` keep that layout.
  Query-path refresh uses a per-project lock, cooldown, pending signature, and
  hidden background worker to prevent duplicate sync and retry storms. MCP
  startup uses the same runtime and blocks only when the index is unusable.
  If target files remain newer than the index after bounded refresh, read those
  files directly and use CodeGraph only for the indexed structural context.

### `gitnexus`

- Use when: semantic or hybrid code-flow search, 360-degree symbol context,
  traces, diff impact, bounded Cypher, code-shape checks, or cross-repository
  groups. It complements rather than replaces CodeGraph's narrow symbol route.
- Route: Hub `gitnexus.list_tools`, then read-only `gitnexus.call`; use Hub
  `hub.call` only when current-turn tool metadata is stale. The adapter fixes
  the Work Git scope and rejects upstream mutating tools.
- Avoid: `setup`, hooks, rename/remove/clean, embeddings, PDG builds, and
  global installation unless separately approved.

### `graphify`

- Use when: a managed graph spans code and other retained artifacts, or when a
  graph-guided review needs `review_delta`, `review_analysis`, `query_graph`,
  or a focused node/community traversal.
- Route: Hub `graphify.list_tools`, then allowlisted read-only
  `graphify.call`; use Hub `hub.call` only when current-turn tool metadata is
  stale. The adapter only accepts a managed `graph.json` outside Work Git.
- Avoid: extraction/build, hooks/watchers, graph creation, or external-model
  backends unless separately approved.

### `node_repl`

- Use when: JavaScript execution, browser/plugin helper code, persistent JS
  state, or fast structured transforms are useful.
- Optimize calls: keep snippets small, use dynamic imports, and reuse bindings
  deliberately.
- Avoid: redeclaring top-level `const`/`let`, hiding long-running processes, or
  using it for file edits that belong in `apply_patch`.
- Validate: explicit printed result, emitted image/artifact, or state reset.
- Fallback: one-shot `node`/PowerShell/Python script.

## Browser And GUI MCPs

### Browser Tool Routing

- Start from the browser state the task needs, not from a favorite tool.
- Existing Chrome state required: use `chrome:control-chrome` / browser-client
  for tabs, logged-in sessions, extensions, and profile-bound state. If the
  Chrome extension path works, it is the most ergonomic route for normal page
  interaction. If its screenshot path times out, test DevTools separately
  before concluding browser capture is broken.
- Existing Chrome/CDP target inspection: use native `chrome-devtools` when
  current-turn callable; after `Transport closed`, unbound namespace, or a
  bounded hang, record the negative observation and use Hub
  `chrome_devtools.*` aliases. Prefer DevTools for target listing,
  accessibility/DOM snapshots, console/network evidence, evaluation inside an
  existing target, and screenshots of the active Chrome target.
- Fresh deterministic browser run: use `playwright` when the task does not
  depend on the user's existing Chrome profile. Prefer it for reproducible UI
  flows, local web app regression checks, page assertions, screenshots, and
  traceable interactions.
- In-app browser: use `browser:control-in-app-browser` only when the user asks
  for the in-app browser or the task benefits from the app-managed browser
  surface. Do not silently substitute it when the user explicitly requested
  Chrome.
- Native desktop/browser shell: use `gui-automation` for file pickers,
  permission dialogs, OS windows, browser chrome that is not DOM-accessible,
  and fallback visual verification. Do not use GUI clicks for ordinary DOM
  automation when Playwright or Chrome/DevTools can see the target.
- Backend-only checks: use HTTP/API probes before browser automation when the
  question is server health, endpoint content, or a non-visual contract.
- Evidence rule: classify each browser result as one of
  `existing_chrome_state`, `fresh_browser`, `cdp_target`, `in_app_browser`,
  `native_gui`, or `backend_probe`. Do not treat success or failure in one
  route as proof about the others.
- Fallback order: same-state route first, same-permission Hub alias second when
  available, alternate browser route third only if it preserves the user's
  requirement, GUI/manual fallback last.

### `playwright`

- Use when: deterministic browser automation, page assertions, screenshots, or
  web app regression checks are needed.
- Optimize calls: launch one context, assert the specific state, capture a
  screenshot only when it proves the claim.
- Avoid: using it for native desktop apps or as a substitute for HTTP/API smoke.
- Validate: assertion result, console/network evidence, and screenshot when UI
  matters.
- Fallback: `chrome-devtools` for existing Chrome sessions or HTTP probes for
  backend-only checks.

### `next-ai-drawio`

- Use when: the requested result must remain editable as Draw.io XML or needs
  the Next AI Draw.io browser preview/session workflow.
- Priority: session-native first because `start_session`, edits, page changes,
  and export share process-bound diagram state. If the native surface fails,
  continue forward through the configured MCP gateway/smoke route; do not
  replace it with an unrelated browser fetch.
- Optimize calls: start one session, create or load one diagram, batch coherent
  edits, then export or read back the final XML. Keep Mermaid as the lighter
  default when editability is not required.
- Avoid: using it as a general browser, starting it for ordinary Markdown
  diagrams, or making it required at Codex startup.
- Validate: protocol smoke must expose `start_session`,
  `create_new_diagram`, `edit_diagram`, `get_diagram`, and `export_diagram`;
  task completion also requires readable Draw.io XML or a verified export.
- Package: the resource layer owns the isolated npm dependency at
  `_bridge/runtime_dependencies/next-ai-drawio-mcp`; runtime `npx@latest`
  downloads are not the default route.

### `cloakbrowser` optional owner

- Use only when the user explicitly requests CloakBrowser or an authorized
  compatibility task cannot be completed by the normal Chrome, DevTools,
  Playwright, HTTP, or GUI routes.
- The Python wrapper is isolated under
  `_bridge/runtime_dependencies/cloakbrowser`. The patched browser binary is a
  separate resource with its own license, source, size, hash, and cache-path
  receipt; wrapper installation does not authorize binary download or launch.
- Start with `python _bridge/cloakbrowser_owner.py plan --task "<task>"` and
  add `--authorized` only when the task is explicitly authorized. The owner
  must not replace the default browser chain, mutate a global profile, or
  rewrite global proxy settings.
- Validate: `python _bridge/cloakbrowser_owner.py validate`.

### `chrome-devtools`

- Use when: inspecting or controlling an already-running Chrome/CDP target.
- Optimize calls: use native `chrome-devtools` first when current-turn callable.
  Discover the active target with `list_pages`, then perform one focused
  inspect/action such as `take_snapshot`, `evaluate_script`,
  `list_console_messages`, `list_network_requests`, or `take_screenshot`.
- Hub route: use Hub aliases such as `chrome_devtools.list_pages`,
  `chrome_devtools.take_snapshot`, `chrome_devtools.evaluate_script`,
  `chrome_devtools.take_screenshot`, `chrome_devtools.list_console_messages`,
  or `chrome_devtools.list_network_requests` when native current-turn MCP is not
  the right execution surface or when an automation owner needs a stable
  same-boundary route. These aliases require
  `fallback_ack=native-mcp-unavailable-and-original-permissions-apply` and
  internally call the existing guarded `chrome-devtools` gateway; they do not
  bypass browser policy or expand permissions.
- Avoid: assuming a fixed port; stale CDP listeners have caused false
  negatives. Do not keep retrying the same dead native handle after a
  current-turn `Transport closed`; switch to the Hub alias or another owner
  route. Do not use raw CDP workarounds to bypass site or browser policy.
- Validate: target URL/version and observed DOM/dialog/network result. For
  screenshot-specific failures, distinguish Chrome extension screenshot
  timeout from DevTools MCP availability by testing `chrome_devtools.list_pages`
  before `chrome_devtools.take_screenshot`.
- Fallback: Playwright fresh context or configured CDP auto-discovery when
  neither native nor Hub DevTools routes can complete the focused browser task.

### `gui-automation`

- Use when: native Windows UI interaction is unavoidable.
- Optimize calls: `gui_ensure_window`, inspect, then one observe-plan-act-verify
  step at a time.
- Avoid: blind multi-step scripts, visible disruptive polling, and GUI work when
  a CLI/API can answer.
- Validate: refreshed inspection after each action.
- Fallback: app-specific CLI/API, browser automation, or a concise manual step.

### Windows generic desktop automation candidates

- Use when: an external Windows desktop automation project offers a reusable
  capability that is missing from the current GUI/browser/CLI layer. The
  current reference candidate is `CursorTouch/Windows-MCP`; absorb capability
  patterns, not the whole project.
- Useful capabilities to absorb first: fast screenshot-first inspection,
  accessibility-tree snapshot, in-tool `WaitFor` polling, focused window/app
  switching, label-to-coordinate action flow, multi-field edit/select patterns,
  and explicit tool filtering.
- Integration rule: prefer the existing `gui-automation`, `desktop-weixin`,
  browser, or CLI-Anything route when it owns the surface. Add or register a
  generic Windows MCP only after a concrete workflow needs it and after a
  smoke test proves `config_ok`, `protocol_ok`, `current_turn_exposed`,
  `current_turn_callable`, and `call_completed`.
- Safety defaults for any future Windows-MCP-style route:
  `ANONYMIZED_TELEMETRY=false`, `WINDOWS_MCP_DISABLE_FLASH=1`, bounded
  screenshot scale when needed, local-only transport by default, and
  tool allowlists before exclusions.
- Avoid: installing the whole project just because it is popular, exposing
  unrestricted PowerShell/FileSystem/Registry/Process-kill as a shortcut, using
  visible screenshot flash during ordinary work, or replacing domain-specific
  Weixin/mobile bridge contracts with generic clicks.
- Validate: read-only `Screenshot` or `Snapshot` first, then `WaitFor` on a
  harmless condition, then a guarded non-destructive action if needed. Record
  the route as candidate/validated/failed in the capability matrix or owning
  maintenance surface; do not claim native availability without a real
  current-turn call.
- Fallback: existing `gui-automation`, app-specific CLI-Anything harness,
  Playwright/Chrome for browser surfaces, or manual user step.

## Remote Repository And Docs MCPs

### `github`

- Use when: inspecting or changing GitHub-hosted repositories, issues, PRs, or
  remote files.
- Optimize calls: GitHub is Hub-first. Call Hub `github.api` or `github.gh` for
  permission context and targeted reads before writes; use native GitHub MCP
  only when the Hub route is unavailable or cannot represent the operation.
  Hub `github.api` resolves credentials in this order: environment token,
  GitHub App installation token from Secret Vault aliases
  `github_app.app_id`, `github_app.installation_id`, and
  `github_app.private_key`, then Secret Vault alias `github.token`, then fail
  closed. Hub `github.gh` uses the existing `gh` keyring. `gh auth status` is
  a read-only capability/authentication probe; token-printing commands remain
  blocked.
- Avoid: using local `git status` as proof for remote changes, or exposing
  tokens/recovery codes in tool inputs. Hub blocks token-printing commands such
  as `gh auth token` and `gh auth status --show-token`; do not try to route
  around that block.
- Validate: returned SHA/URL/status and remote file readback when needed. For
  Hub REST calls, check HTTP status and rate-limit metadata; for `github.gh`,
  check command exit status and returned JSON/URL.
- Fallback: Hub `github.api` / `github.gh`, then direct `gh` CLI or GitHub REST
  with the same credential boundary. Do not pass tokens as command arguments;
  use Secret Vault handoff, environment variables, stdin, or `gh` keyring as
  appropriate.
- Hub write rule: Hub GitHub proxy is full-capability under existing
  credentials. Non-GET REST calls and likely mutating `gh` commands require
  `write_ack=github-write-through-hub-uses-existing-permissions`. `gh api` with
  `--method POST|PATCH|PUT|DELETE`, `-f/--raw-field`, `-F/--field`, or
  `--input` is treated as mutating unless explicitly confirmed.
- GitHub App auth: store App ID, installation ID, and PEM private key only via
  Secret Vault aliases `github_app.app_id`, `github_app.installation_id`, and
  `github_app.private_key`. Use `github_app.snapshot`, `github_app.doctor`, and
  `github_app.validate` for non-secret diagnostics. Generated JWTs and
  installation tokens are consumer handoffs only and must not be printed or
  persisted.

### `context7`

- Use when: current library/framework docs are needed.
- Optimize calls: resolve the library id first, then ask a narrow API/version
  question. The local proxy expects both `libraryName` and `query` for
  `resolve_library_id`; after the first `Context7-compatible library ID` is
  found, call `query_docs` with `libraryId` and the same narrow query. The
  resource broker can do this automatically for read-only target-only requests
  such as `--target python --intent documentation_lookup --auto-owner`.
- Avoid: broad tutorial searches when official docs can answer.
- Validate: cite library id/source and compare version-sensitive facts.
- Fallback: official documentation web search.

### `microsoftdocs`

- Use when: Microsoft Learn, Windows, Azure, .NET, or PowerShell docs are
  needed.
- Optimize calls: use the local stdio proxy and narrow the query to product,
  version, and command/API.
- Avoid: treating remote endpoint availability as current-turn callability.
- Validate: Microsoft Learn URL or fetched page/tool result.
- Fallback: Microsoft Learn web search.

### `openai-docs`

- Use when: official OpenAI API, Codex, Apps SDK, or ChatGPT developer-product
  documentation is needed.
- Optimize calls: enter through Hub `owner_mcp.call_readonly`, call
  `search_openai_docs` or `list_openai_docs`, then call `fetch_openai_doc` for
  the selected URL. Use `get_openapi_spec` for endpoint-schema questions.
- Evidence rule: empty output, metadata-only output, and search snippets without
  a fetched page do not establish a factual claim. Missing public documentation
  means "not established by public docs", not "unsupported".
- Validate: require non-empty content plus provenance from
  `developers.openai.com`, `platform.openai.com`, or `learn.chatgpt.com`.
- Fallback: bounded official OpenAI-domain search after the MCP route fails or
  remains insufficient; do not route OpenAI product docs through Microsoft Docs
  or Context7.

## Memory And Bridge MCPs

### Memory Router and `local-pmb-memory`

- Use when: project/user/profile/tool history can affect the answer or
  closeout. Start with `_bridge/memory_router.py route --message <task>` for
  non-simple work; then call PMB only when the route selects PMB.
- Optimize calls: query by task domain, problem type, object, and freshness
  needs. Use PMB for durable lessons, prior root causes, reusable project
  facts, and user/workflow preferences. Use user profile for stable preferences,
  external knowledge for reusable sourced web facts, record-store indexes for
  historical execution evidence, and one-shot work notes for current-task side
  issues.
- Topology: Hub-managed daemon-backed fresh-stdio proxy. The PMB daemon is the
  durable memory surface; common reads use direct Hub tools and the remaining
  read/write surface uses the governed gateway. `local-pmb-memory` is not a
  Desktop-registered MCP, so absence of a native namespace is expected rather
  than session drift. `_bridge/local_pmb_memory.py` remains the later owner-CLI
  continuity route.
- Avoid: forcing PMB into simple/self-contained tasks, storing secrets, raw
  logs, incident noise, or unverified guesses. Do not treat PMB as live proof
  for ports, processes, current-turn MCP callability, task state, file
  existence, or remote repository state.
- Validate: returned memory entry id/citation, `local_pmb_memory.py validate`,
  `memory_router.py validate`, or `memory_governance.py recall-verify`.
- Fallback: Hub PMB tools, then `_bridge/local_pmb_memory.py pmb-prepare` or
  `pmb-recall`, then memory folder quick pass and ad-hoc note only for approved
  writeback candidates.

### `agent-bridge`

- Use when: coordinating with Reasonix or shared bridge tasks.
- Optimize calls: claim/read/complete explicitly and keep task state evidence.
- Avoid: guessing malformed task intent or relying only on heartbeat flags.
- Validate: bridge DB task state plus worker log/activity evidence.
- Fallback: read-only bridge DB/CLI inspection under project rules.

### `mobile-openclaw-bridge`

- Use when: handling Weixin/mobile delegation, supplements, dashboard send, or
  repair/status commands.
- Optimize calls: follow owned-result protocol exactly; consume supplements
  after ack and before final when required.
- Avoid: placeholder mobile results, visible-input ownership checks, or retries
  without checking old result codes.
- Validate: owned result markers, delivery receipt, or CLI queued/ack status.
- Current-thread supplements and acknowledgements are session-native-first.
  Task lookup/status may use owner CLI or Hub, but must preserve exact task and
  thread identity. A workflow handoff is complete only after its result is
  attached to the same `workflow_run_id`.
- Fallback: if Hub is unavailable or cannot complete the same-boundary call,
  use `mobile_openclaw_cli.py supplement-fallback ...` and bridge DB
  diagnostics. A `Transport closed` observation is not complete until the Hub
  route or this local fallback has been attempted or explicitly blocked.

## Database MCPs

### `sqlite-bridge-ro`

- Use when: bridge DB state, queues, delivery evidence, or diagnostics need
  read-only SQL.
- Topology: local stateless stdio. Hub read-only SQLite aliases are the read
  entry stage; failures continue forward to Hub gateway/local read-only SQLite.
- Optimize calls: for bridge queue/task/delivery/receipt status, inspect schema
  first, use small SELECTs with limits, and report concrete rows/counts before
  opening broad logs. Use this as evidence for diagnosis, not as a repair
  executor.
- Avoid: writes, exports of secrets, or broad dumps.
- Repair boundary: after SQLite identifies a state problem, route changes
  through the bridge worker/permission/task maintenance entrypoint and validate
  with the owning doctor or queue readback; never direct-write the bridge DB.
- Validate: query result and read-only mode maintained.
- Fallback: Python/sqlite read-only URI.

### `sqlite-scratch`

- Use when: temporary structured notes, analysis tables, or derived task state
  help organize work.
- Topology: Hub-managed stateless stdio. Hub SQLite aliases are the read entry
  stage; execute/insert/upsert use the governed fresh-stdio gateway and exit
  after each call. Local temp JSON/CSV or SQLite helpers remain later
  continuity routes.
- Optimize calls: create small task-scoped tables with explicit names and
  cleanup/summary intent.
- Avoid: production state, credentials, secrets, durable authority, or using
  scratch tables as a substitute for the owner system's repair path.
- Validate: schema and row count.
- Fallback: local temp JSON/CSV in the workspace.

### Hub SQLite Aliases

- Use when: native SQLite MCP is unavailable in the current turn, or when a
  stable Hub route is preferable for structured status queries.
- Topology: Hub exposes Codex-discoverable alias tools:
  `sqlite_bridge_health`, `sqlite_bridge_tables`, `sqlite_bridge_schema`,
  `sqlite_bridge_query`, `sqlite_scratch_health`, `sqlite_scratch_tables`,
  `sqlite_scratch_schema`, `sqlite_scratch_query`, `record_store_health`,
  `record_store_tables`, `record_store_schema`, `record_store_query`,
  `email_state_health`, `email_state_tables`, `email_state_schema`, and
  `email_state_query`.
  These map to the same read-only services as the older dotted Hub names.
  If the active Codex turn has not reloaded Hub metadata yet, use the currently
  exposed names `sqlite_bridge_sqlite_query`, `sqlite_scratch_sqlite_query`,
  and `record_store_sqlite_query`; otherwise discover hidden aliases through
  Hub catalog/search/describe when the alias is on-demand.
- Optimize calls: use `sqlite_bridge_query` for bridge queue/task/delivery
  evidence, `sqlite_scratch_query` for task-scoped scratch reads, and
  `record_store_query` for indexed record/resource evidence. Use
  `email_state_query` for mail task, inbox, outbox, delivery, draft, and SMTP
  receipt status after refreshing with
  `python _bridge\shared\email_scheduler.py state-index --apply` when freshness
  matters.
- Avoid: direct production DB writes through Hub. Production repairs still go
  through the owning maintenance CLI/API after SQLite diagnosis.
- Validate: run Hub validate and a bounded alias query, such as
  `sqlite_bridge_query` with `SELECT status, COUNT(*) ... LIMIT ...`.

### `record_store.sqlite` via Hub

- Use when: querying global record-store indexes, resource request manifests,
  execution records, scheduler records, mail mirrors, or indexed resource-layer
  evidence.
- Topology: read-only SQLite service exposed by Hub as the preferred aliases
  `record_store_health`, `record_store_tables`, `record_store_schema`, and
  `record_store_query`; dotted `record_store.sqlite_*` names remain internal
  compatibility routes. The database is the derived index at
  `C:\Users\45543\Desktop\Codex资源库\文档\系统维护\索引\record_store.sqlite`.
- Optimize calls: for indexed record questions, use SQL first instead of text
  scanning or broad CLI output. Query `records` by `area`, `kind`, `status`,
  `tags`, `source_path`, and `created_at`; use `records_fts` when full-text
  search is useful. Keep limits small and return rows/counts, not dumps.
- Refresh cadence: the derived record index is refreshed by the scheduler every
  1 hour. For freshness-sensitive changes, run the owner refresh explicitly
  rather than scanning the raw record tree.
- Avoid: writes, treating the derived index as source of truth, or refreshing
  the index just to answer a narrow query when the current index is fresh enough.
- Validate: `record_store_health` plus a bounded `record_store_query`; for
  freshness-sensitive changes, run `record_store_maintenance.py index --apply`
  then query by SQL.
- Fallback: `python _bridge\shared\record_store_maintenance.py query --term ...`
  or a bounded Python sqlite3 read-only query against the same index.

## External Proxy MCPs

### `openai-docs`, `context7`, and `microsoftdocs`

- Use these through local stdio wrappers because direct remote MCP binding can
  drift in Codex Desktop.
- Optimize calls by making the remote lookup narrow; broad queries waste the
  fragile current-turn surface.
- Validate both tool result and source URL.
- Trigger them proactively when the user explicitly asks to search online, look
  up external knowledge, or consult docs. `openai-docs` owns OpenAI product
  documentation, `microsoftdocs` owns
  Microsoft/Windows/Azure facts; `context7` owns package, SDK, framework, and
  library docs. Generic web search must have a concrete fallback reason, not
  just habit.
- Hub-first continuity: enter through the known Hub read-only owner call. If it
  fails, continue forward to Hub gateway/local owner routes. Use
  `mcp_gateway.complete_route` only if the target tool/schema/permission route
  is unknown, ambiguous, or diagnostic evidence is required; do not jump
  backward to native merely to probe health.

### `github`

- Hub `github.api` and `github.gh` are the GitHub entry stage because they reuse
  the existing token or `gh` auth without exposing the secret. Failures move
  forward to Hub gateway/local Hub and `gh`; do not mirror tokens into files.
- If permission fails, treat it as credential scope, not a local repo problem.

## Circuit Breakers

- `Transport closed`: stop calling the failed selected route for this turn,
  record evidence, and continue with the next step from its classified affinity.
  Do not reorder a Hub-first profile merely to probe native health.
- `tool_unbound`: check config and protocol smoke before blaming the server.
- `hung_call`: one bounded timeout, then stop.
- `permission_mismatch`: do not silently promote to admin or broader token.
- `mojibake_output`: fix stdin/stdout/env encoding before trusting rendered
  paths or variables.

## Validation Commands

```powershell
python _bridge\tool_exposure_doctor.py validate
python _bridge\mcp_session_doctor.py validate
python _bridge\resource_process_doctor.py metrics
python _bridge\encoding_governance.py validate
python _bridge\mcp_capability_routes.py build
python _bridge\mcp_capability_routes.py validate
```

`mcp_capability_routes.py` writes a machine-first derived route index under
`_bridge\runtime\mcp_capability_routes.json`. The Markdown matrix remains the
source of truth for human-facing policy; the JSON index is optimized for Codex
tool-routing decisions.

For slash command registry changes:

```powershell
python _bridge\slash_command_governance.py snapshot
python _bridge\slash_command_governance.py proposal --name <name> --category <category> --description "<description>" --target-module <module> --output-contract <contract> --variable <var> --template "<template>"
python _bridge\slash_command_governance.py validate
python _bridge\slash_command_governance.py apply --confirm-apply --proposal-file <proposal.json>
python _bridge\slash_command_governance.py render-smoke --name <name>
```

Prefer MCP `slash_validate_registry` for read-only current-turn registry health. Use
`slash_command_governance.py` for any registry change because it performs proposal
checks, routed backup, post-apply validation, and render-smoke without turning the
custom slash MCP into an executor.
