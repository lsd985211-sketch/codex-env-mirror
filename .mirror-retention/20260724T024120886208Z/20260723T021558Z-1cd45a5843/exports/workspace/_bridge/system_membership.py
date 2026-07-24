#!/usr/bin/env python3
"""Read-only system-member contract control plane.

Ownership: plans and validates synchronization surfaces for system members,
starting with MCP servers and MCP-routing architecture.
Non-goals: applying repairs, mutating Codex/MCP config, replacing owner
validators, or bypassing permission boundaries.
State behavior: read-only; commands emit machine-readable plans and findings.
Caller context: Codex workflow, closeout, architecture governance, and future
repair-plan owners that need a compact contract receipt.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from shared.json_cli import configure_utf8_stdio, now_iso, print_json


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "_bridge"
CODEX_HOME = Path.home() / ".codex"
STARTUP_BASELINE = BRIDGE / "codex_startup_baseline.json"
CODEX_CONFIG = CODEX_HOME / "config.toml"

configure_utf8_stdio()

SCHEMA = "system_membership.v2"

LIFECYCLE_STATES = ("active", "deprecated", "decommissioning", "decommissioned", "historical_only")
LIFECYCLE_TRANSITIONS = {
    "active": ["deprecated", "decommissioning"],
    "deprecated": ["active", "decommissioning"],
    "decommissioning": ["active", "decommissioned"],
    "decommissioned": ["historical_only"],
    "historical_only": [],
}

CURRENT_GUIDANCE_PATHS = [
    CODEX_HOME / "skills" / "global-framework" / "SKILL.md",
    CODEX_HOME / "skills" / "memory-checkpoint-ops" / "SKILL.md",
    CODEX_HOME / "skills" / "memory-checkpoint-ops" / "references" / "core.md",
]

HISTORICAL_PATH_MARKERS = (
    "/_backup/",
    "/backups/",
    "/checkpoints/",
    "/archive/",
    "/archives/",
    "/migration/",
    "/migrations/",
)

RETIREMENT_INTENT_TERMS = (
    "decommission",
    "retire",
    "retirement",
    "tombstone",
    "退役",
    "墓碑",
    "下线成员",
)

RETIREMENT_PURGE_SURFACES = (
    "implementation_exit",
    "registration_exit",
    "generation_exit",
    "routing_exit",
    "runtime_exit",
    "maintenance_exit",
    "guidance_exit",
    "data_disposition",
    "dependency_release",
    "prevention_guard",
)

RETIREMENT_PROOF_SURFACES = (
    "lifecycle_identity",
    "replacement_readiness",
    "retirement_receipt",
)


def surface(
    key: str,
    owner: str,
    purpose: str,
    evidence: str,
    required: bool = True,
    update_when: str = "",
) -> dict[str, Any]:
    return {
        "key": key,
        "owner": owner,
        "purpose": purpose,
        "evidence": evidence,
        "required": required,
        "update_when": update_when,
    }


def health_command(
    name: str,
    args: list[str],
    *,
    severity: str = "risk",
    timeout: int = 60,
    compatibility_args: list[str] | None = None,
    compatibility_timeout: int | None = None,
    platform_scope: str = "all",
) -> dict[str, Any]:
    command = {"name": name, "args": args, "severity": severity, "timeout": timeout}
    if compatibility_args:
        command["compatibility_args"] = compatibility_args
        command["compatibility_timeout"] = compatibility_timeout or timeout
    if platform_scope != "all":
        command["platform_scope"] = platform_scope
    return command


def generic_contract(system: str, member_kinds: list[str], health_commands: list[dict[str, Any]], non_goals: list[str]) -> dict[str, Any]:
    return {
        "system": system,
        "member_kinds": member_kinds,
        "default_member_kind": member_kinds[0],
        "required_surfaces": ARCHITECTURE_SURFACES,
        "architecture_surfaces": ARCHITECTURE_SURFACES,
        "health_commands": health_commands,
        "non_goals": non_goals,
    }


MCP_MEMBER_SURFACES: list[dict[str, Any]] = [
    surface(
        "recovery_bundle",
        "_bridge/mcp_recovery_bundle_owner.py",
        "MCP implementations, platform assets, offline dependencies, authorization, and fresh-agent materialization state are content-addressed and distinct from source/config staging",
        "python _bridge\\mcp_recovery_bundle_owner.py validate && python _bridge\\mcp_recovery_bundle_owner.py readiness",
        update_when="adding, replacing, packaging, authorizing, or changing recovery semantics for an MCP implementation or dependency",
    ),
    surface(
        "registration",
        "Codex MCP config / mcp_session_doctor",
        "server is registered and can be diagnosed without treating protocol health as current-turn callability",
        "python _bridge\\mcp_session_doctor.py validate",
        update_when="adding, renaming, removing, or changing startup/blocking semantics for an MCP server",
    ),
    surface(
        "capability_matrix",
        "_bridge/docs/mcp_capability_matrix.md",
        "source-of-truth owner route, execution affinity, session binding, permission boundary, fallback order, and validation command",
        "targeted readback plus python _bridge\\mcp_capability_routes.py validate",
    ),
    surface(
        "derived_route_index",
        "_bridge/mcp_capability_routes.py",
        "machine-first route lookup exposes execution affinity, session binding, direct Hub tools, fallback policy, and evidence rules",
        "python _bridge\\mcp_capability_routes.py build && python _bridge\\mcp_capability_routes.py validate",
    ),
    surface(
        "hub_adapter",
        "_bridge/local_mcp_hub.py and profile-specific Hub adapters",
        "stateless Hub-first adapters and session-bound native routes preserve the same permission boundary",
        "python _bridge\\local_mcp_hub.py validate",
    ),
    surface(
        "diagnostics",
        "_bridge/mcp_session_doctor.py",
        "layered config/protocol/current-turn/call-completed evidence stays distinct",
        "python _bridge\\mcp_session_doctor.py validate",
    ),
    surface(
        "workflow_route",
        "_bridge/workflow_orchestrator.py",
        "routing plans surface the owner MCP, Hub direct route, fallback boundary, and validation tier",
        "python _bridge\\workflow_orchestrator.py validate",
    ),
    surface(
        "resource_owner_route",
        "_bridge/resource_cli.py / resource layer",
        "external-resource requests can delegate owner-tool work instead of forcing Codex to fetch directly",
        "resource request smoke or resource-layer validator when the MCP serves resources",
        required=False,
        update_when="MCP is used by resource acquisition, docs, browser evidence, package metadata, GitHub, or downloads",
    ),
    surface(
        "startup_baseline",
        "Codex startup/baseline governance",
        "required vs nonblocking startup impact is explicit and validated after config changes",
        "closeout with --config-changed --auto-finalize when startup-affecting",
        required=False,
        update_when="MCP is marked required, nonblocking, or changes startup/load behavior",
    ),
    surface(
        "closeout_receipt",
        "_bridge/codex_workflow_entry.py",
        "system changes leave a compact membership/architecture synchronization receipt",
        "python _bridge\\codex_workflow_entry.py closeout ...",
        required=False,
    ),
]


ARCHITECTURE_SURFACES: list[dict[str, Any]] = [
    surface(
        "contract_template",
        "_bridge/system_membership.py",
        "new system or member kind has an explicit surface contract before implementation spreads",
        "python _bridge\\system_membership.py validate",
    ),
    surface(
        "impact_mapping",
        "_bridge/system_membership.py",
        "changed architecture files map to affected systems, surfaces, and required next commands",
        "python _bridge\\system_membership.py impact --changed <file>",
    ),
    surface(
        "maintenance_surface",
        "_bridge/docs/maintenance_surface_map.md",
        "new contract owner and validation commands are discoverable from the workspace maintenance map",
        "targeted readback plus python _bridge\\global_coherence_doctor.py validate",
    ),
]


MEMBER_LIFECYCLE_SURFACES: list[dict[str, Any]] = [
    surface(
        "exit_strategy",
        "system membership owner plus member owner",
        "new members declare their retirement owner, replacement/no-replacement policy, data disposition, activation surfaces, and tombstone location before integration is complete",
        "python _bridge\\system_membership.py retirement-plan --system <system> --member <member> --replacement <member|none> --reason <reason>",
        update_when="adding a member or changing how it is registered, generated, routed, persisted, maintained, or replaced",
    ),
]


MEMBER_INTEGRATION_SURFACES: list[dict[str, Any]] = [
    surface(
        "member_identity",
        "system membership owner plus member owner",
        "stable system/member/kind identity, lifecycle owner, authoritative state source, and replacement boundary are explicit",
        "python _bridge\\system_membership.py plan --system <system> --member <member> --kind <kind>",
        update_when="adding, renaming, splitting, merging, replacing, or changing ownership of a member",
    ),
    surface(
        "domain_discovery",
        "system membership domain binding plus workflow and skill route owners",
        "every active member inherits an existing workflow domain and skill domain; bindings are references only and do not duplicate owner contracts",
        "python _bridge\\system_membership.py validate plus workflow_orchestrator.py validate and skill_orchestrator.py validate",
        update_when="adding a system/member or changing its request discoverability, workflow route, or skill route",
    ),
    surface(
        "activation_contract",
        "member owner plus registration/routing/runtime owners",
        "registration, generation, routing, runtime activation, required-vs-optional startup behavior, reload/restart boundary, and fallback behavior converge before activation",
        "targeted registration, route, runtime, and restart/reload validation",
        update_when="changing how the member becomes visible, callable, blocking, reloadable, or persistent",
    ),
    surface(
        "dependency_contract",
        "member owner plus resource/package owner",
        "runtime dependencies and external assets use the owning acquisition path with explicit version, source, permission, install-script, artifact, and verification boundaries",
        "resource/package receipt plus owner validation",
        update_when="adding or changing packages, binaries, browser assets, models, plugins, or other external dependencies",
    ),
    surface(
        "execution_contract",
        "member owner plus calling facade",
        "versioned structured fields are authoritative; natural language is supplemental; multi-item work preserves item identity, requiredness, acceptance, status, and retry scope",
        "schema validation plus focused multi-item regression",
        update_when="changing request schemas, batching, routing inputs, retries, permissions, or execution semantics",
    ),
    surface(
        "result_contract",
        "member owner plus calling facade",
        "progress, status, errors, reasons, result references, acceptance evidence, and consumption acknowledgement are machine-readable; owner success alone cannot satisfy an unmet caller need",
        "owner receipt readback plus acceptance and consumption regression",
        update_when="changing output, completion, acceptance, progress, error, receipt, or consumer lifecycle semantics",
    ),
    surface(
        "maintenance_regression",
        "member owner plus maintenance owner",
        "doctor, validate, metrics, process ownership, impact mapping, and focused regressions cover the member; tests use isolated state and tolerate legitimate concurrent production activity without hiding test leakage",
        "owner validator plus system membership validate and focused isolated regression",
        update_when="changing state, persistence, process, concurrency, observability, maintenance, or test behavior",
    ),
    surface(
        "closeout_reconciliation",
        "_bridge/workflow_finalization.py plus _bridge/codex_workflow_entry.py",
        "changed architecture files automatically run membership impact at closeout; successful completion requires current membership validation and a system_membership=ok receipt",
        "python _bridge\\workflow_finalization_tests.py plus python _bridge\\system_membership.py validate",
        update_when="changing architecture impact mapping, closeout semantics, member activation, or validation receipt behavior",
    ),
]


RETIREMENT_SURFACES: list[dict[str, Any]] = [
    surface(
        "lifecycle_identity",
        "system membership owner",
        "stable system/member/kind identity, lifecycle state, owner, reason, and effective time are explicit",
        "python _bridge\\system_membership.py retirement-plan --system <system> --member <member>",
    ),
    surface(
        "replacement_readiness",
        "member owner",
        "replacement or explicit no-replacement decision is validated before active ownership is released",
        "targeted owner validation plus retirement receipt",
    ),
    surface(
        "implementation_exit",
        "member implementation owner",
        "active executable entrypoints, launcher aliases, service wrappers, and discoverable implementation modules are removed or moved into an excluded historical archive",
        "targeted active-tree scan plus owner validator",
    ),
    surface(
        "registration_exit",
        "registration/config owner",
        "live registration, service declarations, plugin manifests, and startup requirements no longer activate the member",
        "owner config readback and validator",
    ),
    surface(
        "generation_exit",
        "generator/template owner",
        "defaults, bootstrap, fallback, repair, and code generation cannot recreate the retired member",
        "owner repair-plan/validate plus regression probe",
    ),
    surface(
        "routing_exit",
        "routing/capability owner",
        "workflow, capability, facade, recommendation, and fallback routes no longer select the retired member",
        "route snapshot and targeted validator",
    ),
    surface(
        "runtime_exit",
        "runtime/process owner",
        "services, scheduled tasks, ports, leases, and orphan processes are stopped or explicitly classified as historical evidence",
        "owner runtime doctor or process inventory",
    ),
    surface(
        "maintenance_exit",
        "maintenance owner",
        "current baselines, doctors, repairs, snapshots, metrics, and health checks no longer require or recommend the retired member",
        "owner doctor/repair-plan/validate",
    ),
    surface(
        "guidance_exit",
        "documentation/skill owner",
        "current documentation, skills, prompts, and new-session guidance do not present the retired member as active",
        "python _bridge\\system_membership.py doctor",
    ),
    surface(
        "data_disposition",
        "data owner",
        "authoritative data, migration inputs, backups, and historical evidence have an explicit keep/migrate/archive/delete classification",
        "owner migration ledger or retention receipt",
    ),
    surface(
        "dependency_release",
        "member owner",
        "upstream dependencies and downstream consumers are migrated, released, or explicitly blocked",
        "dependency inventory and replacement validation",
    ),
    surface(
        "prevention_guard",
        "owning repair/validation surface",
        "a tombstone or equivalent negative contract prevents registration, generation, routing, or recommendation from returning",
        "owner regression test plus system membership validate",
    ),
    surface(
        "retirement_receipt",
        "_bridge/codex_workflow_entry.py",
        "completion records the affected surfaces, evidence, retained history, replacement, and rollback boundary",
        "python _bridge\\codex_workflow_entry.py closeout --task-kind system_membership ...",
    ),
]


LIFECYCLE_POLICY = {
    "states": list(LIFECYCLE_STATES),
    "transitions": LIFECYCLE_TRANSITIONS,
    "completion_rule": "decommissioned requires every retirement surface to be satisfied or explicitly marked not_applicable with owner evidence",
    "history_rule": "historical references may remain only in backups, checkpoints, migration sources, and archives; they must not activate, generate, route, recommend, or validate the member as current",
    "ownership_rule": "system membership judges convergence; each surface owner applies and validates its own changes",
}


INTEGRATION_POLICY = {
    "admission_rule": "before activation, the workflow must consume a membership plan that identifies the member, owner, lifecycle, authoritative state, required surfaces, dependency path, and reload/restart boundary",
    "structured_input_rule": "explicit structured fields are authoritative; natural language only fills absent non-safety fields",
    "batch_identity_rule": "each independent item keeps a stable item_id, required flag, acceptance predicate, status, error, and retry scope through every layer",
    "success_rule": "transport or owner ok is not completion unless the caller's acceptance predicate is satisfied",
    "consumption_rule": "result-producing work is end-to-end terminal only after the caller reads or explicitly waives the owned result and records consumption",
    "dependency_rule": "external packages and binaries are acquired through the owning resource/package path; wrapper packages and large runtime assets remain separately authorized and verified",
    "activation_rule": "registration, generated config, route catalogs, runtime launch guards, maintenance baselines, and user-visible discovery must converge before activation is complete",
    "domain_discovery_rule": "each active system/member inherits one workflow domain and one skill domain from SYSTEM_DOMAIN_BINDINGS; both referenced domains must exist in their consumer before admission can validate",
    "optional_member_rule": "optional or session-bound members remain nonblocking unless explicitly promoted to required startup capability",
    "reload_rule": "the contract must state whether a live reload, owner restart, Codex restart, or machine restart is required and must not claim immediate activation otherwise",
    "concurrency_rule": "regressions isolate test-owned state and identify test leakage by ownership; they must not require legitimate production state to remain globally static",
    "change_propagation_rule": "architecture changes update the contract, impact mapping, maintenance surface, validators, and closeout evidence in the same change",
    "reconciliation_rule": "after changed files are known, impact mapping reconciles desired contract state with actual registration, routing, runtime, maintenance, documentation, and retirement surfaces before completion is claimed",
    "closeout_enforcement_rule": "workflow finalization derives membership obligations from changed files and blocks a successful closeout until the membership owner validates and its receipt is present",
}


WORKFLOW_MEMBER_SURFACES: list[dict[str, Any]] = [
    surface(
        "rule_authority_registry",
        "_bridge/policies/rule_authority_registry.json",
        "every active rule-bearing surface has scope, owner, enforcement point, validator, lifecycle, precedence, and an explicit legacy disposition without copying rule bodies",
        "python _bridge\\rule_governance.py validate",
        update_when="adding, moving, merging, superseding, retiring, or discovering a rule-bearing surface",
    ),
    surface(
        "rule_lifecycle",
        "_bridge/rule_governance.py",
        "rule discovery, impact, migration, and retirement evidence remain queryable through the existing workflow governance system",
        "python _bridge\\rule_governance.py doctor && python _bridge\\rule_governance.py validate",
        update_when="changing rule authority, discovery classifications, lifecycle dispositions, or changed-file rule reconciliation",
    ),
    surface(
        "action_synthesis",
        "_bridge/workflow_action_synthesis.py",
        "reliable route-pack evidence becomes typed owner actions while ambiguous routes return machine-readable needs_input",
        "python _bridge\\workflow_action_synthesis.py",
    ),
    surface(
        "action_receipt_contract",
        "_bridge/workflow_owner_facade.py",
        "versioned workflow action and receipt schemas preserve owner identity, state source, permission, and lifecycle capability",
        "python _bridge\\workflow_owner_facade.py validate",
    ),
    surface(
        "facade_lifecycle",
        "_bridge/codex_workflow_entry.py",
        "plan/run/status/wait/cancel/attach-result/closeout stay one facade while owner-native commands remain available",
        "python _bridge\\codex_workflow_entry.py --help",
    ),
    surface(
        "owner_adapter_capability",
        "_bridge/workflow_owner_facade.py",
        "each adapter declares supported lifecycle operations; current-turn tools return handoff_required until their result is attached",
        "python _bridge\\workflow_owner_facade.py snapshot",
    ),
    surface(
        "owner_state_source",
        "owner adapter plus owner query surface",
        "normalized status cites the real owner state source and run references do not become business state",
        "python _bridge\\workflow_owner_facade.py validate",
    ),
    surface(
        "workflow_route",
        "_bridge/workflow_orchestrator.py",
        "route plan selects the owner and exposes the action contract without duplicating owner policy",
        "python _bridge\\workflow_orchestrator.py validate",
    ),
    surface(
        "maintenance_surface",
        "_bridge/docs/maintenance_surface_map.md",
        "new workflow members and lifecycle validation remain discoverable",
        "targeted readback plus python _bridge\\global_coherence_doctor.py validate",
    ),
    surface(
        "closeout_receipt",
        "_bridge/codex_workflow_entry.py",
        "workflow architecture changes leave checkpoint, backup, validation, and fallback evidence",
        "python _bridge\\codex_workflow_entry.py closeout ...",
        required=False,
    ),
]


RESOURCE_MEMBER_SURFACES: list[dict[str, Any]] = [
    surface(
        "delegation_contract",
        "_bridge/structured_task_envelope.py",
        "resource action, target, quantity, uniqueness, source, freshness, materialization, quality, and safety fields are versioned and validated",
        "python _bridge\\structured_task_envelope.py",
    ),
    surface(
        "owner_facade",
        "_bridge/resource_cli.py custom",
        "CLI and Hub/MCP callers share one structured request schema and existing job/receipt lifecycle",
        "python _bridge\\structured_task_envelope_tests.py",
    ),
    surface(
        "strategy_consumption",
        "resource broker, source strategy, collection, and strategy policy",
        "structured fields drive routing, batch size, deduplication, source coverage, freshness, destination, and install gates",
        "python _bridge\\resource_fetcher_tests.py",
    ),
    surface(
        "workflow_route",
        "_bridge/execution_route_pack.py",
        "natural-language workflow classification emits the structured resource command without downstream text reclassification",
        "python _bridge\\workflow_orchestrator.py validate",
    ),
    surface(
        "maintenance_surface",
        "_bridge/docs/maintenance_surface_map.md",
        "resource contract owner, facade, validators, and compatibility path remain discoverable",
        "targeted readback plus python _bridge\\global_coherence_doctor.py validate",
        required=False,
    ),
]


OFFICE_MEMBER_SURFACES: list[dict[str, Any]] = [
    surface(
        "office_harness",
        "_bridge/cli_anything_microsoft_office/agent-harness",
        "bounded Word, Excel, and PowerPoint commands use the installed Office applications",
        "cli-anything-microsoft-office --json system status",
    ),
    surface(
        "office_operation_contract",
        "cli_anything/microsoft_office/core/operations.py",
        "versioned allowlisted operations reject arbitrary COM, VBA, PowerShell, and unknown fields",
        "python -m pytest -q _bridge\\cli_anything_microsoft_office\\agent-harness\\cli_anything\\microsoft_office\\tests\\test_core.py",
    ),
    surface(
        "office_workflow_route",
        "_bridge/workflow_orchestrator.py plus _bridge/workflow_action_synthesis.py",
        "explicit installed-Office tasks select the office owner without replacing OOXML content tools",
        "python _bridge\\workflow_orchestrator.py plan --message \"用本机 Word 检查真实分页\" --detail micro",
    ),
    surface(
        "office_owner_adapter",
        "_bridge/workflow_owner_facade.py",
        "the facade invokes the installed harness and preserves approval and immutable result semantics",
        "python _bridge\\workflow_owner_facade.py validate",
    ),
    surface(
        "office_skill_route",
        "cli-anything-microsoft-office skill copies plus _bridge/skill_orchestrator.py",
        "Codex selects the native Office skill only when real installed Office behavior is required",
        "python _bridge\\skill_orchestrator.py plan --message \"用本机 Excel 重算公式\"",
    ),
    surface(
        "office_maintenance_surface",
        "_bridge/cli_anything_governance.py plus _bridge/docs/maintenance_surface_map.md",
        "local harness discovery, command shape, tests, and owner validation remain queryable",
        "python _bridge\\cli_anything_governance.py validate",
    ),
]


CONTRACTS: dict[str, dict[str, Any]] = {
    "mcp": {
        "system": "mcp",
        "member_kinds": ["mcp_server", "hub_adapter", "readonly_owner_adapter", "owner_tool_route", "session_bound_route", "lazy_stdio_proxy"],
        "default_member_kind": "mcp_server",
        "required_surfaces": MCP_MEMBER_SURFACES,
        "architecture_surfaces": ARCHITECTURE_SURFACES,
        "health_commands": [
            health_command("mcp_session", ["_bridge/mcp_session_doctor.py", "validate"], timeout=90),
            health_command("mcp_recovery_bundle", ["_bridge/mcp_recovery_bundle_owner.py", "readiness"], timeout=120),
        ],
        "non_goals": [
            "do not apply MCP config changes",
            "do not bypass target MCP permissions through Hub, native, CLI, or handoff routes",
            "do not treat protocol smoke as current-turn callability",
            "do not make all tools required at startup by default",
        ],
    },
    "workflow": {
        "system": "workflow",
        "member_kinds": ["facade", "owner_adapter", "route_projection", "lifecycle_contract", "rule_governance_owner"],
        "default_member_kind": "owner_adapter",
        "required_surfaces": WORKFLOW_MEMBER_SURFACES,
        "architecture_surfaces": ARCHITECTURE_SURFACES,
        "health_commands": [
            health_command(
                "workflow_route",
                ["_bridge/workflow_orchestrator.py", "validate"],
                timeout=90,
                compatibility_args=["_bridge/workflow_orchestrator.py", "metrics"],
                compatibility_timeout=30,
            ),
            health_command("owner_facade", ["_bridge/workflow_owner_facade.py", "validate"], timeout=90),
            health_command(
                "rule_governance",
                ["_bridge/rule_governance.py", "validate"],
                timeout=90,
                compatibility_args=["_bridge/rule_governance.py", "doctor"],
                compatibility_timeout=60,
            ),
        ],
        "non_goals": [
            "do not create a second business-state database",
            "do not move owner permissions or retries into the facade",
            "do not claim lifecycle operations the owner cannot perform",
            "do not remove owner-native commands merely because a facade exists",
        ],
    },
    "resource": {
        "system": "resource",
        "member_kinds": [
            "delegation_contract",
            "owner_facade",
            "batch_scheduler",
            "source_adapter",
            "materializer",
            "package_owner",
            "browser_owner",
            "strategy_policy",
        ],
        "default_member_kind": "source_adapter",
        "required_surfaces": RESOURCE_MEMBER_SURFACES,
        "architecture_surfaces": ARCHITECTURE_SURFACES,
        "health_commands": [health_command("resource_process", ["_bridge/resource_process_doctor.py", "validate"], timeout=90)],
        "non_goals": [
            "do not create a second queue, retry engine, or receipt database",
            "do not let natural-language inference override explicit structured fields",
            "do not authorize install, remote write, or destructive actions from inferred text",
            "do not move network route selection out of the network gateway",
        ],
    },
    "office": {
        "system": "office",
        "member_kinds": ["native_harness", "operation_contract", "workflow_owner", "skill_route"],
        "default_member_kind": "native_harness",
        "required_surfaces": OFFICE_MEMBER_SURFACES,
        "architecture_surfaces": ARCHITECTURE_SURFACES,
        "health_commands": [health_command("office_harness", ["_bridge/cli_anything_governance.py", "validate"], severity="advisory", timeout=90)],
        "non_goals": [
            "do not expose arbitrary COM, VBA, macros, PowerShell, or shell execution",
            "do not reuse visible user Office sessions",
            "do not replace OOXML-native tools when installed Office is unnecessary",
            "do not create a second Office queue or document state database",
        ],
    },
    "backup": generic_contract(
        "backup",
        ["backup_router", "recovery_mirror", "restore_stage", "archive_policy"],
        [
            health_command(
                "backup_hygiene",
                ["_bridge/backup_hygiene_doctor.py", "validate"],
                timeout=120,
                compatibility_args=["_bridge/backup_hygiene_doctor.py", "metrics"],
                compatibility_timeout=120,
            ),
            health_command(
                "environment_mirror",
                ["_bridge/codex_environment_mirror.py", "validate"],
                timeout=300,
                compatibility_args=["_bridge/codex_environment_mirror.py", "status"],
                compatibility_timeout=45,
            ),
        ],
        [
            "do not mirror secrets, sessions, runtime databases, logs, caches, or retired members",
            "do not treat an isolated restore stage as activation",
            "do not create a second live configuration authority",
        ],
    ),
    "network": generic_contract(
        "network",
        ["gateway", "route_policy", "target_profile"],
        [health_command("network_gateway", ["_bridge/codex_network_gateway.py", "validate"], timeout=90)],
        ["do not mutate global proxy or DNS for a per-request route", "do not move resource acquisition into the network layer"],
    ),
    "hardware": generic_contract(
        "hardware",
        ["device_owner", "diagnostic_owner"],
        [
            health_command("hardware_system_owner", ["_bridge/hardware_system_owner.py", "validate"], timeout=45),
            health_command("wsl_hardware_owner", ["_bridge/wsl_hardware_owner.py", "validate"], timeout=45),
            health_command("windows_hardware_owner", ["_bridge/windows_hardware_owner.py", "validate"], timeout=45, platform_scope="windows_host"),
            health_command("usb_device_owner", ["_bridge/usb_device_owner.py", "validate"], timeout=90, platform_scope="windows_host"),
            health_command("usb_device_control", ["_bridge/usb_device_control.py", "validate"], timeout=45, platform_scope="windows_host"),
        ],
        [
            "diagnostic owners remain read-only; device state changes require a dedicated control owner, exact identity, policy admission, explicit confirmation, post-state acceptance, and owner receipts",
            "do not install or remove drivers, remove devices, change firmware, mutate storage, format/eject media, or change operating-system device policy",
            "do not expose arbitrary shell, PowerShell, ADB, Fastboot, or vendor-tool execution",
            "do not make optional device discovery a startup requirement or resident service",
        ],
    ),
    "audio": generic_contract(
        "audio",
        ["audio_toolkit", "music_library_owner"],
        [
            health_command("music_library_owner", ["_bridge/music_library_owner.py", "validate"], timeout=90, platform_scope="windows_host"),
            health_command("audio_toolkit", ["_bridge/audio_toolkit/audio_toolkit.py", "validate"], timeout=30, platform_scope="windows_host"),
        ],
        [
            "do not let audio content owners inherit device-control, format, eject, partition, firmware, or driver permissions",
            "do not perform network research inside the music owner; consume only reviewed structured corrections",
            "do not delete, overwrite, transcode, or rewrite media content during library organization",
            "do not bypass exact plan confirmation, source hashes, fresh hardware binding, journals, or post-state validation",
        ],
    ),
    "bridge": generic_contract(
        "bridge",
        ["worker", "transport", "delivery_route", "dashboard"],
        [
            health_command(
                "mobile_bridge",
                ["_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py", "maintenance", "doctor"],
                severity="advisory",
                timeout=90,
                compatibility_args=["_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py", "mobile-execution-contract-check"],
                compatibility_timeout=30,
            ),
            health_command("persistent_task_kernel", ["_bridge/persistent_task_kernel.py", "validate"], severity="advisory", timeout=45),
        ],
        [
            "do not bypass mobile permission or final-reply contracts",
            "do not treat ack as completion",
            "do not let sidecar task state replace an owner module's business state",
        ],
    ),
    "mail": generic_contract(
        "mail",
        ["scheduler", "inbox", "outbox", "attachment_context"],
        [health_command("email_scheduler", ["_bridge/shared/email_scheduler.py", "validate"], timeout=90, platform_scope="windows_host")],
        ["do not separate reply attachments from the mail task context", "do not send without the owning mail contract"],
    ),
    "memory": generic_contract(
        "memory",
        ["router", "governance", "pmb", "profile"],
        [health_command("memory_governance", ["_bridge/memory_governance.py", "validate"], timeout=90)],
        ["do not store secrets", "do not treat memory as live-state evidence"],
    ),
    "records": generic_contract(
        "records",
        ["index", "retention", "archive", "query_surface"],
        [health_command("record_store", ["_bridge/shared/record_store_maintenance.py", "doctor"], timeout=150)],
        ["do not broad-scan indexed records by default", "do not delete or archive without an approved owner plan"],
    ),
    "skills": generic_contract(
        "skills",
        ["active_catalog", "lifecycle", "router", "inventory", "scenario", "metadata_budget"],
        [
            health_command("skill_lifecycle", ["_bridge/skill_lifecycle_governance.py", "doctor"], timeout=90),
            health_command(
                "skill_router",
                ["_bridge/skill_orchestrator.py", "validate"],
                timeout=90,
                compatibility_args=["_bridge/skill_orchestrator.py", "metrics"],
                compatibility_timeout=30,
            ),
        ],
        ["do not duplicate global skills into project-local stores", "do not auto-enable unreviewed skills"],
    ),
    "startup": generic_contract(
        "startup",
        ["guard", "launcher", "baseline", "config_projection", "session_store", "protocol_compatibility"],
        [
            health_command("config_guard", ["_bridge/codex_config_guard.py", "validate"], timeout=90),
            health_command("desktop_protocol_compatibility", ["_bridge/codex_desktop_protocol_compatibility.py", "validate"], timeout=30),
            health_command("config_projection", ["_bridge/codex_config_projection.py", "validate"], timeout=45, platform_scope="windows_host"),
            health_command(
                "session_store",
                ["_bridge/codex_session_store_doctor.py", "doctor"],
                timeout=90,
                compatibility_args=["_bridge/codex_session_store_doctor.py", "validate"],
                compatibility_timeout=30,
            ),
        ],
        ["do not hot-edit active session transcripts", "do not weaken functionality to improve startup time"],
    ),
    "wsl_workspace": {
        "system": "wsl_workspace",
        "member_kinds": ["workspace_lifecycle_owner", "developer_toolchain_owner", "codex_app_server_owner", "work_git_change_owner", "declarative_work_git", "platform_projection", "host_compatibility_projection", "isolated_validation_target"],
        "default_member_kind": "workspace_lifecycle_owner",
        "required_surfaces": [
            surface("identity", "system_membership", "stable long-lived owner identity and lifecycle are explicit", "python _bridge\\system_membership.py plan --system wsl_workspace --member wsl_workspace.lifecycle --kind workspace_lifecycle_owner"),
            surface("lifecycle", "wsl_workspace owner", "status, plan, bootstrap, validate, handoff, cleanup-plan, projection-aware host-cleanup-plan, hash-verified host-audio migration, and work-Git release-readiness are reusable lifecycle operations", "python _bridge\\wsl_workspace_owner.py validate"),
            surface("source_authority", "wsl_workspace owner", "daily authority is the WSL worktree backed by the Windows bare Git repository; mirror is a derived recovery/release product", "owner snapshot and work-Git release receipt"),
            surface("desktop_project_registration", "wsl_workspace owner", "Desktop persists the Windows-visible WSL UNC Git root through Electron IPC and validates disk plus live acceptance", "python _bridge\\wsl_workspace_owner.py desktop-project-status"),
            surface("interop_recovery", "wsl_workspace owner", "a bounded systemd timer restores a missing WSLInterop binfmt registration and validates exact managed files plus active scheduling", "python _bridge\\wsl_workspace_owner.py interop-guard-plan"),
            surface("host_compatibility_projection", "platform projection owners", "the former Windows workspace retains Windows-only execution dependencies without becoming an editing or reverse-sync authority", "platform path snapshot plus startup baseline metadata"),
            surface("clone_bootstrap", "wsl_workspace owner", "clone/bootstrap is validation-first and does not activate host runtime", "python _bridge\\wsl_workspace_owner.py bootstrap --confirm BOOTSTRAP-WSL-WORKSPACE"),
            surface("developer_toolchain", "developer_toolchain owner", "required developer CLIs are version-locked, user-local, resource-acquired, PATH-projected, and validated during WSL bootstrap", "python _bridge\\developer_toolchain_owner.py validate"),
            surface("codex_app_server", "wsl_codex_app_server", "the optional Codex app-server runs as the WSL user under systemd, uses an isolated CODEX_HOME, and exposes only the user-runtime Unix socket", "python _bridge\\wsl_codex_app_server.py validate"),
            surface("work_git_change_set", "work_git_change_owner", "dirty or parallel tasks use isolated worktrees, declared-path commits, fast-forward integration, and local bare-Git receipts", "python _bridge\\work_git_change_owner.py validate"),
            surface("platform_projection", "wsl_workspace owner", "Windows and WSL projections are generated from semantics without sharing writable runtime state", "bootstrap receipt plus projection validator"),
            surface("validation", "wsl_workspace owner", "Git, path, tool, capability, MCP route, and bootstrap acceptance are machine-readable", "python _bridge\\wsl_workspace_owner.py validate"),
            surface("handoff_receipt", "wsl_workspace owner", "handoff records target, worktree, source snapshot, activation status, validation rows, and rollback boundary", "python _bridge\\wsl_workspace_owner.py handoff"),
            surface("work_git_release", "wsl_workspace owner", "release candidate proves clean WSL worktree, matching Windows bare Git head, and one-way derivation into the recovery mirror", "python _bridge\\wsl_workspace_owner.py mirror-export --kind work-git-release"),
            surface("rollback", "wsl_workspace owner", "cleanup is explicit; host cleanup only removes fixed-classification regenerated or redundant artifacts after projection validation, while host-audio migration hashes preserved results before source deletion; neither removes the default WSL distribution, mirror, host runtime, bridge, or opaque user data", "python _bridge\\wsl_workspace_owner.py host-audio-migration-plan"),
            surface("maintenance_surface", "_bridge/docs/maintenance_surface_map.md", "owner commands remain discoverable through the maintenance catalog", "maintenance capability index and map"),
            surface("closeout", "workflow finalization", "owner changes reconcile through membership impact and closeout evidence", "system_membership=ok receipt"),
        ],
        "architecture_surfaces": ARCHITECTURE_SURFACES,
        "health_commands": [
            health_command("wsl_workspace_owner", ["_bridge/wsl_workspace_owner.py", "validate"], timeout=90),
            health_command("developer_toolchain_owner", ["_bridge/developer_toolchain_owner.py", "validate"], timeout=90),
            health_command("wsl_codex_app_server", ["_bridge/wsl_codex_app_server.py", "validate"], timeout=60),
            health_command("work_git_change_owner", ["_bridge/work_git_change_owner.py", "validate"], timeout=45),
        ],
        "non_goals": [
            "do not limit this member to the current Codex-Wsl-Lab distribution or any other single validation target",
            "do not switch the default WSL distribution",
            "do not import Windows Codex runtime, secrets, sessions, SQLite, caches, or plugin state",
            "do not publish mirror releases or replace codex_environment_mirror.py",
            "do not reverse-overwrite the daily work Git repository from the mirror",
            "do not manage Windows-only Office, GUI, browser, Weixin, OMEN, or CC Switch sessions",
        ],
    },
    "drafts": generic_contract(
        "drafts",
        ["artifact_store", "lifecycle_metadata", "review_reference"],
        [
            health_command("draft_governance", ["_bridge/draft_governance.py", "validate"], timeout=45),
            health_command("workflow_review_queue", ["_bridge/workflow_review_queue.py", "validate"], timeout=45),
        ],
        ["draft storage is not a queue", "file names do not determine workflow status"],
    ),
}


# Relationship authority only: route owners keep their detailed keyword/tool
# projections, while membership guarantees that every member is discoverable.
SYSTEM_DOMAIN_BINDINGS: dict[str, dict[str, str]] = {
    "audio": {"workflow_domain": "audio", "skill_domain": "audio"},
    "backup": {"workflow_domain": "editing_backup", "skill_domain": "backup"},
    "bridge": {"workflow_domain": "bridge", "skill_domain": "bridge"},
    "drafts": {"workflow_domain": "workflow_governance", "skill_domain": "drafts"},
    "hardware": {"workflow_domain": "hardware", "skill_domain": "hardware"},
    "mail": {"workflow_domain": "email", "skill_domain": "email"},
    "mcp": {"workflow_domain": "mcp_tools", "skill_domain": "mcp_tools"},
    "memory": {"workflow_domain": "memory", "skill_domain": "memory"},
    "network": {"workflow_domain": "network_routing", "skill_domain": "network_routing"},
    "office": {"workflow_domain": "office_native", "skill_domain": "office_native"},
    "records": {"workflow_domain": "records_resources", "skill_domain": "records_resources"},
    "resource": {"workflow_domain": "resource_acquisition", "skill_domain": "resource_acquisition"},
    "skills": {"workflow_domain": "skills_templates", "skill_domain": "skills"},
    "startup": {"workflow_domain": "workflow_governance", "skill_domain": "codex_runtime"},
    "workflow": {"workflow_domain": "workflow_governance", "skill_domain": "workflow_governance"},
    "wsl_workspace": {"workflow_domain": "wsl_workspace", "skill_domain": "wsl_workspace"},
}


def routing_domains_for_system(system: str) -> dict[str, str]:
    binding = SYSTEM_DOMAIN_BINDINGS.get(str(system or ""), {})
    return {
        "workflow_domain": str(binding.get("workflow_domain") or ""),
        "skill_domain": str(binding.get("skill_domain") or ""),
    }


def systems_for_domain(domain: str, *, consumer: str) -> list[str]:
    field = "workflow_domain" if consumer == "workflow" else "skill_domain"
    return sorted(system for system in CONTRACTS if routing_domains_for_system(system).get(field) == domain)


def domain_binding_report(
    *,
    workflow_domains: set[str] | None = None,
    skill_domains: set[str] | None = None,
) -> dict[str, Any]:
    missing = sorted(system for system in CONTRACTS if not all(routing_domains_for_system(system).values()))
    unknown_workflow = sorted(
        system
        for system in CONTRACTS
        if workflow_domains is not None and routing_domains_for_system(system)["workflow_domain"] not in workflow_domains
    )
    unknown_skill = sorted(
        system
        for system in CONTRACTS
        if skill_domains is not None and routing_domains_for_system(system)["skill_domain"] not in skill_domains
    )
    return {
        "schema": f"{SCHEMA}.domain_coverage",
        "ok": not missing and not unknown_workflow and not unknown_skill,
        "bindings": {system: routing_domains_for_system(system) for system in sorted(CONTRACTS)},
        "missing_systems": missing,
        "unknown_workflow_systems": unknown_workflow,
        "unknown_skill_systems": unknown_skill,
    }


# The membership contract is authoritative for which active capability surfaces
# must participate in the mirror. Capture mode, sanitization, restore mapping,
# and external-archive handling remain owned by the mirror manifest.
MIRROR_MEMBER_REGISTRY: list[dict[str, Any]] = [
    {
        "member_id": "rule-governance.core",
        "system": "workflow",
        "kind": "rule_governance_owner",
        "lifecycle": "active",
        "owner": "rule_governance",
        "source_ids": ["codex-global-agents", "codex-rules", "workspace-agents"],
        "generated_source_ids": ["rule-governance-snapshot"],
        "change_roots": ["workspace:AGENTS.md", "workspace:_bridge/", "codex_home:AGENTS.md", "codex_home:rules/"],
    },
    {
        "member_id": "memory-governance.core",
        "system": "memory",
        "kind": "governance",
        "lifecycle": "active",
        "owner": "memory_governance",
        "source_ids": ["codex-user-preferences", "codex-memory-index", "codex-native-memory-files"],
        "generated_source_ids": ["memory-snapshot", "current-checkpoint-snapshot"],
        "change_roots": ["codex_home:USER_WORKING_PREFERENCES.md", "codex_home:MEMORY.md", "codex_home:memories/", "workspace:_bridge/shared/checkpoints/"],
    },
    {
        "member_id": "codex-startup-and-provider.core",
        "system": "startup",
        "kind": "baseline",
        "lifecycle": "active",
        "owner": "codex_cli",
        "source_ids": ["codex-version", "codex-hooks", "codex-global-state-template", "codex-scripts", "codex-tools", "codex-automations"],
        "generated_source_ids": ["runtime-versions", "windows-scheduled-tasks", "windows-shortcuts", "secret-requirement-snapshot", "codex-plugin-inventory"],
        "change_roots": ["codex_home:version.json", "codex_home:config.toml", "codex_home:hooks.json", "codex_home:.codex-global-state.json", "codex_home:scripts/", "codex_home:tools/", "workspace:_bridge/"],
    },
    {
        "member_id": "skills.active-catalog",
        "system": "skills",
        "kind": "active_catalog",
        "lifecycle": "active",
        "owner": "skill_lifecycle_governance",
        "source_ids": ["codex-skills", "agent-compatibility-skills", "cc-switch-skills"],
        "generated_source_ids": ["codex-plugin-inventory"],
        "change_roots": ["codex_home:skills/", "agent_home:skills/", "cc_switch:skills/"],
    },
    {
        "member_id": "mcp-capability-routing.core",
        "system": "mcp",
        "kind": "route_projection",
        "lifecycle": "active",
        "owner": "mcp_capability_routes",
        "source_ids": ["workspace-bridge-source"],
        "generated_source_ids": ["mcp-route-snapshot", "mcp-bundle-readiness"],
        "change_roots": ["workspace:_bridge/"],
    },
    {
        "member_id": "mcp.local-hub-runtime",
        "system": "mcp",
        "kind": "hub_adapter",
        "lifecycle": "active",
        "owner": "local_mcp_hub_process",
        "source_ids": ["workspace-bridge-source"],
        "generated_source_ids": ["local-mcp-hub-user-unit", "local-mcp-hub-service-status"],
        "change_roots": [
            "workspace:_bridge/local_mcp_hub.py",
            "workspace:_bridge/local_mcp_hub_process.py",
            "workspace:_bridge/local_mcp_hub_process_tests.py",
            "workspace:_bridge/shared/wsl_user_systemd.py",
            "workspace:_bridge/shared/wsl_user_systemd_tests.py",
        ],
    },
    {
        "member_id": "workspace-bridge.core",
        "system": "bridge",
        "kind": "worker",
        "lifecycle": "active",
        "owner": "system_membership",
        "source_ids": ["workspace-bridge-source"],
        "generated_source_ids": ["system-membership-snapshot", "maintenance-capability-snapshot"],
        "change_roots": ["workspace:_bridge/"],
    },
    {
        "member_id": "hardware.system-facade",
        "system": "hardware",
        "kind": "diagnostic_owner",
        "lifecycle": "active",
        "owner": "hardware_system_owner",
        "source_ids": ["workspace-bridge-source"],
        "generated_source_ids": ["hardware-system-route-contract"],
        "change_roots": [
            "workspace:_bridge/hardware_system_owner.py",
            "workspace:_bridge/hardware_system_owner_tests.py",
        ],
    },
    {
        "member_id": "hardware.wsl-visible-projection",
        "system": "hardware",
        "kind": "diagnostic_owner",
        "lifecycle": "active",
        "owner": "wsl_hardware_owner",
        "source_ids": ["workspace-bridge-source"],
        "generated_source_ids": ["wsl-hardware-tool-health", "wsl-hardware-snapshot"],
        "change_roots": [
            "workspace:_bridge/wsl_hardware_owner.py",
            "workspace:_bridge/wsl_hardware_owner_tests.py",
        ],
    },
    {
        "member_id": "hardware.mtp-media-archive",
        "system": "hardware",
        "kind": "device_owner",
        "lifecycle": "active",
        "owner": "mtp_media_archive_owner",
        "source_ids": ["workspace-bridge-source"],
        "generated_source_ids": ["mtp-media-archive-owner-contract"],
        "change_roots": [
            "workspace:_bridge/mtp_media_archive_owner.py",
            "workspace:_bridge/mtp_media_archive_owner_tests.py",
            "workspace:_bridge/shared/windows_powershell.py",
            "workspace:_bridge/shared/windows_powershell_tests.py",
        ],
    },
    {
        "member_id": "wsl_workspace.lifecycle",
        "system": "wsl_workspace",
        "kind": "workspace_lifecycle_owner",
        "lifecycle": "active",
        "owner": "wsl_workspace",
        "source_ids": ["workspace-bridge-source", "wsl-workspace-guide"],
        "generated_source_ids": ["wsl-workspace-bootstrap-receipt", "wsl-workspace-handoff-receipt", "wsl-work-git-release-receipt", "wsl-desktop-project-registration-receipt"],
        "change_roots": ["worktree:AGENTS.md", "workspace:AGENTS.md", "workspace:_bridge/wsl_workspace_owner.py", "workspace:_bridge/wsl_interop_guard.py", "workspace:_bridge/platform_paths.py", "workspace:_bridge/bootstrap_wsl_workspace.py", "worktree:WSL_WORKSPACE.md"],
    },
    {
        "member_id": "wsl_workspace.developer_toolchain",
        "system": "wsl_workspace",
        "kind": "developer_toolchain_owner",
        "lifecycle": "active",
        "owner": "developer_toolchain_owner",
        "source_ids": ["workspace-bridge-source"],
        "generated_source_ids": ["runtime-versions", "wsl-workspace-bootstrap-receipt"],
        "change_roots": [
            "workspace:_bridge/developer_toolchain_owner.py",
            "workspace:_bridge/policies/developer_toolchain.lock.json",
            "workspace:_bridge/code_maintainability_toolchain.py",
            "workspace:_bridge/bootstrap_wsl_workspace.py"
        ],
    },
    {
        "member_id": "wsl_workspace.codex_app_server",
        "system": "wsl_workspace",
        "kind": "codex_app_server_owner",
        "lifecycle": "active",
        "owner": "wsl_codex_app_server",
        "source_ids": ["workspace-bridge-source"],
        "generated_source_ids": ["wsl-codex-app-server-unit", "wsl-codex-app-server-status"],
        "change_roots": [
            "workspace:_bridge/wsl_codex_app_server.py",
            "workspace:_bridge/wsl_codex_app_server_tests.py",
            "workspace:_bridge/wsl_workspace_owner.py",
            "workspace:_bridge/wsl_workspace_owner_tests.py",
            "worktree:WSL_WORKSPACE.md",
        ],
    },
    {
        "member_id": "wsl_workspace.windows_execution_agent",
        "system": "wsl_workspace",
        "kind": "windows_execution_plane_owner",
        "lifecycle": "active",
        "owner": "windows_execution_agent",
        "source_ids": ["workspace-bridge-source", "wsl-workspace-guide"],
        "generated_source_ids": ["windows-execution-agent-status", "host-compatibility-projection"],
        "change_roots": [
            "workspace:_bridge/windows_execution_agent.py",
            "workspace:_bridge/windows_execution_agent_tests.py",
            "workspace:_bridge/shared/codex_scheduler_runner.py",
            "workspace:_bridge/wsl_workspace_owner.py",
            "workspace:_bridge/wsl_workspace_owner_tests.py",
            "worktree:WSL_WORKSPACE.md",
        ],
    },
    {
        "member_id": "wsl_workspace.work_git_change_sets",
        "system": "wsl_workspace",
        "kind": "work_git_change_owner",
        "lifecycle": "active",
        "owner": "work_git_change_owner",
        "source_ids": ["workspace-bridge-source"],
        "generated_source_ids": ["wsl-work-git-release-receipt"],
        "change_roots": [
            "worktree:AGENTS.md",
            "worktree:WSL_WORKSPACE.md",
            "workspace:_bridge/work_git_change_owner.py",
        ],
    },
    {
        "member_id": "codex_desktop_environment_selection",
        "system": "wsl_workspace",
        "kind": "platform_projection",
        "lifecycle": "active",
        "owner": "codex_desktop_environment_selection",
        "source_ids": ["workspace-bridge-source", "codex-scripts"],
        "generated_source_ids": ["system-membership-snapshot"],
        "change_roots": [
            "workspace:_bridge/codex_desktop_environment_selection.py",
            "workspace:_bridge/wsl_codex_runtime.py",
            "workspace:_bridge/codex_state_repair.py",
            "workspace:_bridge/codex_config_guard.py",
            "workspace:_bridge/codex_desktop_protocol_compatibility.py",
            "workspace:_bridge/codex_appserver_model_bridge.py",
            "workspace:_bridge/codex_desktop_model_runtime.py",
            "codex_home:scripts/start-codex-desktop-elevated.ps1",
            "codex_home:scripts/restart-codex-desktop-cdp.ps1",
        ],
    },
]


def mirror_source_projection() -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    members: list[dict[str, Any]] = []
    source_ids: set[str] = set()
    generated_source_ids: set[str] = set()
    change_roots: set[str] = set()
    seen_members: set[str] = set()
    for raw in MIRROR_MEMBER_REGISTRY:
        member = dict(raw)
        member_id = str(member.get("member_id") or "")
        lifecycle = str(member.get("lifecycle") or "")
        if not member_id or member_id in seen_members:
            issues.append({"severity": "risk", "code": "mirror_member_identity_invalid", "member_id": member_id})
            continue
        seen_members.add(member_id)
        if lifecycle != "active":
            continue
        if not str(member.get("system") or "") or not str(member.get("owner") or ""):
            issues.append({"severity": "risk", "code": "mirror_member_owner_missing", "member_id": member_id})
        member_sources = sorted({str(item) for item in member.get("source_ids", []) if str(item)})
        member_generated = sorted({str(item) for item in member.get("generated_source_ids", []) if str(item)})
        if not member_sources and not member_generated:
            issues.append({"severity": "risk", "code": "mirror_member_sources_missing", "member_id": member_id})
        source_ids.update(member_sources)
        generated_source_ids.update(member_generated)
        change_roots.update(str(item) for item in member.get("change_roots", []) if str(item))
        members.append({
            "member_id": member_id,
            "system": str(member.get("system") or ""),
            "kind": str(member.get("kind") or ""),
            "lifecycle": lifecycle,
            "owner": str(member.get("owner") or ""),
            "source_ids": member_sources,
            "generated_source_ids": member_generated,
            "change_roots": sorted(str(item) for item in member.get("change_roots", []) if str(item)),
        })
    return {
        "schema": f"{SCHEMA}.mirror_source_projection",
        "ok": not issues,
        "generated_at": now_iso(),
        "authority": "system_membership.active_member_registry",
        "members": sorted(members, key=lambda item: item["member_id"]),
        "source_ids": sorted(source_ids),
        "generated_source_ids": sorted(generated_source_ids),
        "change_roots": sorted(change_roots),
        "issues": issues,
        "rule": "Active membership selects mirror candidates; mirror policy owns capture, sanitization, restore, and archive disposition.",
    }


IMPACT_RULES: list[dict[str, Any]] = [
    {
        "prefix": "_bridge/codex_rule_observer",
        "systems": ["workflow", "startup"],
        "surfaces": ["workflow_route", "execution_contract", "result_contract", "maintenance_regression", "closeout_reconciliation"],
        "reason": "Non-blocking Codex admission, tool-fact observation, or closeout conformance behavior changed.",
    },
    {
        "prefix": "C:/Users/45543/.codex/hooks.json",
        "systems": ["workflow", "startup"],
        "surfaces": ["registration", "activation_contract", "execution_contract", "maintenance_regression", "closeout_reconciliation"],
        "reason": "Codex hook registration changed; verify allowed events, no PreToolUse interception, trust/reload boundary, and fail-open behavior.",
    },
    {
        "prefix": "_bridge/tools/mcp-wrappers/",
        "systems": ["mcp", "startup", "network"],
        "surfaces": ["registration", "execution_contract", "diagnostics", "capability_matrix", "maintenance_regression", "closeout_receipt"],
        "reason": "An MCP transport wrapper changed; launcher registration, network boundary, protocol smoke, and owner-route compatibility must be reconciled.",
    },
    {
        "prefix": "_bridge/shared/process_liveness",
        "systems": ["startup", "mcp", "backup", "network", "workflow"],
        "surfaces": ["execution_contract", "maintenance_regression", "maintenance_surface", "closeout_reconciliation"],
        "reason": "Shared process-liveness semantics affect launchers, startup guards, owner locks, network leases, and background refresh stability.",
    },
    {
        "prefix": "_bridge/codex_plugin_runtime_doctor",
        "systems": ["startup"],
        "surfaces": ["execution_contract", "result_contract", "maintenance_regression", "maintenance_surface", "closeout_reconciliation"],
        "reason": "Codex plugin/runtime diagnostics or their failure-classification regressions changed.",
    },
    {
        "prefix": "_bridge/codex_plugin_config_health",
        "systems": ["startup", "wsl_workspace"],
        "surfaces": ["registration", "execution_contract", "result_contract", "maintenance_regression", "maintenance_surface", "closeout_reconciliation"],
        "reason": "Codex plugin configuration, marketplace/cache alignment, or plugin-health regression behavior changed.",
    },
    {
        "prefix": "_bridge/project_checkpoint_finalize",
        "systems": ["workflow", "memory"],
        "surfaces": ["closeout_receipt", "closeout_reconciliation", "contract_template"],
        "reason": "Project checkpoint owner, backup behavior, or regression coverage changed.",
    },
    {
        "prefix": "_bridge/shared/checkpoints/",
        "systems": ["workflow", "memory"],
        "surfaces": ["closeout_receipt", "closeout_reconciliation", "contract_template"],
        "reason": "Project checkpoint evidence or its manifest changed through the checkpoint owner.",
    },
    {
        "prefix": "_bridge/codegraph_",
        "systems": ["mcp", "workflow"],
        "surfaces": ["hub_adapter", "derived_route_index", "diagnostics", "workflow_route", "maintenance_regression"],
        "reason": "CodeGraph runtime, wrapper, scope acceptance, freshness, or regression behavior changed.",
    },
    {
        "prefix": "_bridge/maintenance_control_plane_tests.py",
        "systems": ["workflow"],
        "surfaces": ["maintenance_regression", "maintenance_surface"],
        "reason": "Shared maintenance output or owner-failure regression coverage changed.",
    },
    {
        "prefix": "_bridge/maintenance_capability_registry",
        "systems": ["workflow"],
        "surfaces": ["execution_contract", "result_contract", "maintenance_surface", "maintenance_regression", "closeout_reconciliation"],
        "reason": "Maintenance capability classification, bounded discovery, or owner resolution changed.",
    },
    {
        "prefix": "_bridge/codex_environment_mirror",
        "systems": ["backup", "workflow"],
        "surfaces": ["execution_contract", "result_contract", "maintenance_surface", "maintenance_regression", "closeout_reconciliation"],
        "reason": "Environment mirror capture, validation, bounded recovery planning, isolated staging, or unified facade behavior changed.",
    },
    {
        "prefix": "_bridge/docs/codex_environment_architecture_plan.md",
        "systems": ["workflow", "wsl_workspace", "startup", "resource", "backup"],
        "surfaces": ["contract_template", "source_authority", "workflow_route", "platform_projection", "maintenance_surface", "closeout_reconciliation"],
        "reason": "Cross-system production lifecycle architecture, dependency admission, recovery, or phased implementation contract changed.",
    },
    {
        "prefix": "_bridge/mcp_recovery_bundle_",
        "systems": ["mcp", "backup", "resource", "workflow"],
        "surfaces": ["recovery_bundle", "dependency_contract", "execution_contract", "result_contract", "maintenance_surface", "maintenance_regression", "closeout_reconciliation"],
        "reason": "MCP implementation bundle manifest, content-addressed archive owner, authorization boundary, or recovery-state regression changed.",
    },
    {
        "prefix": "_bridge/mcp_launch_guard.py",
        "systems": ["mcp", "startup"],
        "surfaces": ["execution_contract", "diagnostics", "maintenance_regression", "maintenance_surface"],
        "reason": "MCP stdio prelaunch serialization, child supervision, or launcher process-safety behavior changed.",
    },
    {
        "prefix": "_bridge/network_gateway_leases.py",
        "systems": ["network", "startup"],
        "surfaces": ["execution_contract", "maintenance_regression", "maintenance_surface"],
        "reason": "Network lease process ownership, liveness, or cleanup safety behavior changed.",
    },
    {
        "prefix": "codex-env-mirror/",
        "systems": ["backup", "workflow"],
        "surfaces": ["execution_contract", "result_contract", "maintenance_surface", "maintenance_regression", "closeout_reconciliation"],
        "reason": "External recovery-mirror implementation, manifests, tests, or restore documentation changed.",
    },
    {
        "prefix": "_bridge/bounded_output.py",
        "systems": ["workflow", "mcp", "startup", "resource", "bridge", "memory"],
        "surfaces": ["diagnostics", "maintenance_surface", "closeout_receipt", "maintenance_regression"],
        "reason": "Shared bounded output or aggregate-validator failure evidence changed across owner surfaces.",
    },
    {
        "prefix": "_bridge/codex_wsl_resume_context",
        "systems": ["startup", "wsl_workspace", "mcp"],
        "surfaces": ["platform_projection", "activation_contract", "execution_contract", "result_contract", "maintenance_regression", "maintenance_surface"],
        "reason": "WSL Desktop resume context, task visibility, or existing-project assignment projection changed.",
    },
    {
        "prefix": "_bridge/codex_state_repair",
        "systems": ["startup", "mcp"],
        "surfaces": ["contract_template", "impact_mapping", "maintenance_surface", "runtime_exit"],
        "reason": "Startup baseline repair, its regressions, or Desktop-native MCP runtime-pointer reconciliation changed.",
    },
    {
        "prefix": "_bridge/codex_desktop_environment_selection",
        "systems": ["startup", "wsl_workspace", "backup"],
        "surfaces": ["platform_projection", "activation_contract", "execution_contract", "result_contract", "maintenance_regression", "maintenance_surface"],
        "reason": "Bidirectional Codex Desktop host/WSL environment selection reconciliation changed.",
    },
    {
        "prefix": "_bridge/wsl_codex_runtime",
        "systems": ["startup", "wsl_workspace", "mcp", "backup"],
        "surfaces": ["platform_projection", "activation_contract", "execution_contract", "result_contract", "maintenance_regression", "maintenance_surface"],
        "reason": "WSL Codex runtime, config, session, or MCP projection changed.",
    },
    {
        "prefix": "_bridge/codex_startup_baseline.json",
        "systems": ["startup", "mcp"],
        "surfaces": ["contract_template", "impact_mapping", "maintenance_surface", "registration"],
        "reason": "Startup baseline or Desktop-native MCP registration expectation changed.",
    },
    {
        "prefix": "_bridge/rule_governance.py",
        "systems": ["workflow"],
        "surfaces": ["rule_authority_registry", "rule_lifecycle", "closeout_reconciliation", "maintenance_surface"],
        "reason": "Rule authority discovery, lifecycle, or validation behavior changed.",
    },
    {
        "prefix": "_bridge/rule_governance_tests.py",
        "systems": ["workflow"],
        "surfaces": ["rule_authority_registry", "rule_lifecycle", "closeout_reconciliation", "maintenance_regression"],
        "reason": "Rule authority lifecycle or activation-contract regression coverage changed.",
    },
    {
        "prefix": "_bridge/policies/rule_authority_registry.json",
        "systems": ["workflow"],
        "surfaces": ["rule_authority_registry", "rule_lifecycle", "closeout_reconciliation"],
        "reason": "Rule authority ownership, precedence, lifecycle, or legacy disposition changed.",
    },
    {
        "prefix": "_bridge/docs/mcp_capability_matrix.md",
        "systems": ["mcp"],
        "surfaces": ["capability_matrix", "derived_route_index", "workflow_route", "closeout_receipt"],
        "reason": "MCP source-of-truth route matrix changed.",
    },
    {
        "prefix": "_bridge/mcp_capability_routes.py",
        "systems": ["mcp"],
        "surfaces": ["derived_route_index", "hub_adapter", "diagnostics", "workflow_route"],
        "reason": "Machine-first MCP route index changed.",
    },
    {
        "prefix": "_bridge/mcp_capability_routes_tests.py",
        "systems": ["mcp"],
        "surfaces": ["derived_route_index", "hub_adapter", "diagnostics", "workflow_route", "maintenance_regression"],
        "reason": "Machine-first MCP route and Hub-first permission-boundary regression coverage changed.",
    },
    {
        "prefix": "_bridge/runtime/mcp_capability_routes.json",
        "systems": ["mcp"],
        "surfaces": ["derived_route_index", "workflow_route", "closeout_receipt"],
        "reason": "Generated MCP capability route projection changed.",
    },
    {
        "prefix": "_bridge/mcp_execution_priority.py",
        "systems": ["mcp", "workflow"],
        "surfaces": ["capability_matrix", "derived_route_index", "hub_adapter", "diagnostics", "workflow_route", "closeout_receipt"],
        "reason": "Explicit MCP profile/tool execution priority registry changed.",
    },
    {
        "prefix": "_bridge/mcp_route_policy.py",
        "systems": ["mcp"],
        "surfaces": ["derived_route_index", "hub_adapter", "diagnostics"],
        "reason": "MCP native/Hub/fallback ordering changed.",
    },
    {
        "prefix": "_bridge/mcp_lazy_stdio_proxy.py",
        "systems": ["mcp", "startup"],
        "surfaces": ["registration", "capability_matrix", "diagnostics", "startup_baseline", "maintenance_surface", "closeout_receipt"],
        "reason": "Stateful MCP catalog caching or lazy child lifecycle changed.",
    },
    {
        "prefix": "_bridge/mcp_profile_launcher.py",
        "systems": ["mcp", "startup"],
        "surfaces": ["registration", "diagnostics", "startup_baseline", "maintenance_surface", "closeout_receipt"],
        "reason": "Desktop MCP profile launch or eager/lazy activation behavior changed.",
    },
    {
        "prefix": "_bridge/mcp_registration_governance_tests.py",
        "systems": ["mcp"],
        "surfaces": ["registration", "capability_matrix", "diagnostics"],
        "reason": "MCP registration, budget, or affinity regression coverage changed.",
    },
    {
        "prefix": "_bridge/mcp_lazy_stdio_proxy_tests.py",
        "systems": ["mcp", "startup"],
        "surfaces": ["registration", "diagnostics", "startup_baseline", "maintenance_surface"],
        "reason": "Lazy MCP catalog/activation regression coverage changed.",
    },
    {
        "prefix": "_bridge/resource_process_doctor.py",
        "systems": ["mcp", "resource"],
        "surfaces": ["diagnostics", "maintenance_surface", "closeout_receipt"],
        "reason": "MCP process classification, pressure accounting, or cleanup evidence changed.",
    },
    {
        "prefix": "_bridge/tool_exposure_doctor",
        "systems": ["mcp", "startup"],
        "surfaces": ["diagnostics", "hub_adapter", "maintenance_surface", "maintenance_regression", "closeout_receipt"],
        "reason": "Codex MCP exposure classification, Hub-managed registration semantics, or desktop tool availability diagnostics changed.",
    },
    {
        "prefix": "_bridge/mcp_session_doctor.py",
        "systems": ["mcp"],
        "surfaces": ["registration", "diagnostics", "hub_adapter"],
        "reason": "MCP diagnostic state model changed.",
    },
    {
        "prefix": "_bridge/mcp_session_profile_drift",
        "systems": ["mcp"],
        "surfaces": ["registration", "diagnostics", "maintenance_regression"],
        "reason": "Hub-managed desktop registration or stale native-process drift classification changed.",
    },
    {
        "prefix": "_bridge/local_mcp_hub",
        "systems": ["mcp", "startup", "wsl_workspace"],
        "surfaces": ["hub_adapter", "derived_route_index", "diagnostics", "activation_contract", "execution_contract", "maintenance_regression", "maintenance_surface", "closeout_reconciliation"],
        "reason": "Hub core, purpose-owned adapter, or WSL user-systemd lifecycle changed.",
    },
    {
        "prefix": "_bridge/github_hub_client",
        "systems": ["mcp", "workflow"],
        "surfaces": ["hub_adapter", "diagnostics", "maintenance_surface", "maintenance_regression", "closeout_receipt"],
        "reason": "GitHub Hub REST credential-source selection or API transport behavior changed.",
    },
    {
        "prefix": "_bridge/local_pmb_memory",
        "systems": ["memory", "mcp", "startup"],
        "surfaces": ["hub_adapter", "diagnostics", "maintenance_surface", "runtime_exit", "closeout_receipt"],
        "reason": "PMB owner, hidden process lifecycle, daemon recovery, or focused regression behavior changed.",
    },
    {
        "prefix": "_bridge/pmb_compatibility",
        "systems": ["memory", "mcp", "startup"],
        "surfaces": ["package_compatibility", "diagnostics", "maintenance_surface", "runtime_exit", "closeout_receipt"],
        "reason": "PMB package compatibility policy, exact-signature repair, or its focused regression behavior changed.",
    },
    {
        "prefix": "_bridge/venvs/pmb-memory/Lib/site-packages/pmb/",
        "systems": ["memory", "mcp", "startup"],
        "surfaces": ["package_compatibility", "runtime_exit", "daemon_recovery", "maintenance_regression", "closeout_receipt"],
        "reason": "Governed PMB runtime package code changed; compatibility state, daemon lifecycle, and owner validation must be reconciled.",
    },
    {
        "prefix": "_bridge/workflow_orchestrator.py",
        "systems": ["workflow", "mcp", "office"],
        "surfaces": ["workflow_route", "contract_template", "office_workflow_route", "office_skill_route"],
        "reason": "Workflow routing can alter system-member synchronization obligations.",
    },
    {
        "prefix": "_bridge/task_route_contract.py",
        "systems": ["workflow"],
        "surfaces": ["workflow_route", "contract_template", "closeout_reconciliation", "impact_mapping"],
        "reason": "Task-mode contracts decide whether member admission and reconciliation gates trigger.",
    },
    {
        "prefix": "_bridge/workflow_",
        "systems": ["workflow"],
        "surfaces": ["workflow_route", "facade_lifecycle", "closeout_receipt", "closeout_reconciliation", "maintenance_surface"],
        "reason": "A workflow rule, projection, facade, or finalization member changed.",
    },
    {
        "prefix": "_bridge/slash_commands/commands.json",
        "systems": ["workflow"],
        "surfaces": ["workflow_route", "contract_template", "maintenance_surface", "closeout_reconciliation"],
        "reason": "Slash command routing templates changed and can alter the operator-facing workflow entry path.",
    },
    {
        "prefix": "_bridge/execution_route_pack.py",
        "systems": ["workflow", "mcp"],
        "surfaces": ["workflow_route", "derived_route_index"],
        "reason": "Execution route payload can alter tool/member routing.",
    },
    {
        "prefix": "_bridge/workflow_plan_detail.py",
        "systems": ["workflow", "mcp"],
        "surfaces": ["workflow_route", "derived_route_index"],
        "reason": "Compact route projections must preserve execution affinity and session binding.",
    },
    {
        "prefix": "_bridge/structured_task_envelope.py",
        "systems": ["resource", "workflow"],
        "surfaces": ["delegation_contract", "owner_facade", "strategy_consumption", "workflow_route", "maintenance_surface"],
        "reason": "Structured resource delegation schema or validation changed.",
    },
    {
        "prefix": "_bridge/codex_resource_delegation.py",
        "systems": ["resource", "workflow"],
        "surfaces": ["delegation_contract", "owner_facade", "strategy_consumption", "workflow_route", "maintenance_surface"],
        "reason": "Codex resource-delegation envelope construction or task-fact propagation changed.",
    },
    {
        "prefix": "_bridge/intent_resource_router.py",
        "systems": ["resource", "workflow", "mcp"],
        "surfaces": ["delegation_contract", "resource_owner_route", "workflow_route", "impact_mapping"],
        "reason": "Intent-to-resource ownership and structured-source precedence changed.",
    },
    {
        "prefix": "_bridge/resource_",
        "systems": ["resource", "mcp"],
        "surfaces": ["resource_owner_route", "hub_adapter", "workflow_route"],
        "reason": "Resource layer owner-tool routing changed.",
    },
    {
        "prefix": "_bridge/cli_anything_microsoft_office/",
        "systems": ["office", "workflow"],
        "surfaces": ["office_harness", "office_operation_contract", "office_workflow_route", "office_owner_adapter", "office_skill_route", "office_maintenance_surface"],
        "reason": "Native Office harness behavior or its integration contract changed.",
    },
    {
        "prefix": "_bridge/cli_anything_governance.py",
        "systems": ["office", "workflow"],
        "surfaces": ["office_harness", "office_maintenance_surface", "office_workflow_route", "maintenance_regression", "closeout_reconciliation"],
        "reason": "Office/CLI governance validation or platform-aware harness gating changed.",
    },
    {
        "prefix": "C:/Users/45543/.codex/skills/cli-anything-microsoft-office/",
        "systems": ["office"],
        "surfaces": ["office_skill_route", "office_maintenance_surface"],
        "reason": "Global native Office skill routing changed.",
    },
    {
        "prefix": "_bridge/workflow_action_synthesis.py",
        "systems": ["workflow", "office"],
        "surfaces": ["action_synthesis", "action_receipt_contract", "facade_lifecycle", "maintenance_surface", "office_workflow_route", "office_owner_adapter"],
        "reason": "Owner, operation, arguments, or missing-input synthesis changed.",
    },
    {
        "prefix": "_bridge/workflow_owner_facade.py",
        "systems": ["workflow", "office"],
        "surfaces": ["action_receipt_contract", "facade_lifecycle", "owner_adapter_capability", "owner_state_source", "maintenance_surface", "office_owner_adapter"],
        "reason": "Workflow action, receipt, adapter capability, or state-source semantics changed.",
    },
    {
        "prefix": "_bridge/codex_workflow_entry.py",
        "systems": ["workflow", "mcp", "office"],
        "surfaces": ["facade_lifecycle", "action_receipt_contract", "closeout_receipt", "maintenance_surface", "office_owner_adapter", "office_workflow_route"],
        "reason": "Closeout evidence can hide or expose missing system synchronization.",
    },
    {
        "prefix": "_bridge/online_access_gate.py",
        "systems": ["workflow", "resource", "mcp"],
        "surfaces": ["workflow_route", "closeout_receipt", "maintenance_surface", "impact_mapping"],
        "reason": "Direct generic web gating can alter external-resource and owner-tool fallback semantics.",
    },
    {
        "prefix": "_bridge/intent_routing.py",
        "systems": ["workflow"],
        "surfaces": ["workflow_route", "impact_mapping", "maintenance_regression", "closeout_reconciliation"],
        "reason": "Shared lexical evidence, negation, ranking, or compound-context admission changed.",
    },
    {
        "prefix": "_bridge/docs/maintenance_surface_map.md",
        "systems": ["workflow", "mcp"],
        "surfaces": ["maintenance_surface", "contract_template", "impact_mapping"],
        "reason": "Maintenance ownership discovery changed.",
    },
    {
        "prefix": "_bridge/docs/execution_economy.md",
        "systems": ["workflow", "resource", "mcp"],
        "surfaces": ["contract_template", "workflow_route", "resource_owner_route", "maintenance_surface", "impact_mapping"],
        "reason": "Cross-layer machine-first delegation, receipt reuse, batching, and escalation guidance changed.",
    },
    {
        "prefix": "_bridge/shared/backup_router",
        "systems": ["backup", "workflow"],
        "surfaces": ["execution_contract", "result_contract", "maintenance_regression", "closeout_reconciliation", "maintenance_surface"],
        "reason": "Git-aware pre-edit recovery routing, external-copy placement, manifest validation, or repository backup migration changed.",
    },
    {
        "prefix": "_bridge/backup_hygiene_doctor",
        "systems": ["backup"],
        "surfaces": ["maintenance_surface", "maintenance_regression", "result_contract"],
        "reason": "Backup placement, Git-reference validation, retention evidence, or external-root hygiene changed.",
    },
    {
        "prefix": "_bridge/docs/tool_coordination_contract.md",
        "systems": ["workflow", "mcp"],
        "surfaces": ["contract_template", "workflow_route", "closeout_reconciliation", "maintenance_surface"],
        "reason": "The human-readable coordination and rule-resolution contract changed.",
    },
    {
        "prefix": "_bridge/docs/system_framework_overview.md",
        "systems": ["workflow"],
        "surfaces": ["contract_template", "maintenance_surface"],
        "reason": "The human-readable system architecture and rule hierarchy changed.",
    },
    {
        "prefix": "AGENTS.md",
        "systems": ["workflow", "wsl_workspace"],
        "surfaces": ["contract_template", "workflow_route", "source_authority", "platform_projection", "closeout_reconciliation", "maintenance_surface"],
        "reason": "Git-root or bridge-subtree Codex rules changed, including the WSL authority boundary.",
    },
    {
        "prefix": "C:/Users/45543/.codex/AGENTS.md",
        "systems": ["workflow"],
        "surfaces": ["contract_template", "workflow_route", "closeout_reconciliation", "maintenance_surface"],
        "reason": "Machine-wide Codex rules changed.",
    },
    {
        "prefix": "_bridge/global_coherence_doctor.py",
        "systems": ["workflow", "mcp"],
        "surfaces": ["maintenance_surface", "impact_mapping"],
        "reason": "Cross-surface coherence validation changed.",
    },
    {
        "prefix": "_bridge/global_coherence_platform_tests.py",
        "systems": ["workflow", "mcp", "wsl_workspace"],
        "surfaces": ["diagnostics", "platform_projection", "maintenance_regression", "closeout_reconciliation"],
        "reason": "Platform-scoped owner-health admission regression coverage changed.",
    },
    {
        "prefix": "_bridge/platform_scope.py",
        "systems": ["workflow", "wsl_workspace", "startup"],
        "surfaces": ["platform_projection", "owner_health_admission", "maintenance_regression", "closeout_reconciliation"],
        "reason": "Shared execution-platform admission for owner health checks changed.",
    },
    {
        "prefix": "_bridge/platform_scope_tests.py",
        "systems": ["workflow", "wsl_workspace", "startup"],
        "surfaces": ["platform_projection", "owner_health_admission", "maintenance_regression", "closeout_reconciliation"],
        "reason": "Shared execution-platform admission regression coverage changed.",
    },
    {
        "prefix": "_bridge/self_update_governance_tests.py",
        "systems": ["workflow", "wsl_workspace"],
        "surfaces": ["platform_projection", "owner_health_admission", "maintenance_regression", "closeout_reconciliation"],
        "reason": "Federated self-update platform admission regression coverage changed.",
    },
    {
        "prefix": "_bridge/system_membership",
        "systems": sorted(CONTRACTS),
        "surfaces": ["contract_template", "impact_mapping", "maintenance_surface", "lifecycle_identity", "prevention_guard", "retirement_receipt"],
        "reason": "The membership contract itself changed.",
    },
    {
        "prefix": "_bridge/maintenance_upgrade_governance.py",
        "systems": ["workflow", "mcp", "resource"],
        "surfaces": ["contract_template", "impact_mapping", "maintenance_surface", "workflow_route"],
        "reason": "Maintenance upgrade governance can alter how system changes choose owner surfaces and evidence classes.",
    },
    {"prefix": "_bridge/codex_network_gateway.py", "systems": ["network"], "surfaces": ["contract_template", "impact_mapping", "maintenance_surface"], "reason": "Network gateway control-plane behavior changed."},
    {"prefix": "_bridge/mobile_openclaw_bridge/codex_cdp_route_process", "systems": ["bridge", "startup"], "surfaces": ["activation_contract", "execution_contract", "result_contract", "maintenance_regression", "maintenance_surface"], "reason": "Visible CDP recovery process submission or governed Desktop launcher handoff changed."},
    {"prefix": "_bridge/mobile_openclaw_bridge/", "systems": ["bridge"], "surfaces": ["contract_template", "impact_mapping", "maintenance_surface"], "reason": "Mobile bridge member or lifecycle changed."},
    {"prefix": "_bridge/wsl_workspace_owner.py", "systems": ["wsl_workspace", "workflow", "backup", "startup"], "surfaces": ["identity", "lifecycle", "source_authority", "desktop_project_registration", "host_compatibility_projection", "host_cleanup", "clone_bootstrap", "platform_projection", "validation", "handoff_receipt", "rollback", "maintenance_surface", "closeout"], "reason": "Long-lived WSL workspace lifecycle owner, Desktop project registration, Work Git authority, host compatibility projection or cleanup, validation, or handoff behavior changed."},
    {"prefix": "_bridge/wsl_interop_guard.py", "systems": ["wsl_workspace", "workflow", "backup", "startup"], "surfaces": ["interop_recovery", "lifecycle", "validation", "rollback", "maintenance_regression", "maintenance_surface", "closeout"], "reason": "Persistent WSLInterop detection, root-owned guard installation, timer recovery, backup, or readback behavior changed."},
    {"prefix": "_bridge/wsl_workspace_owner_tests.py", "systems": ["wsl_workspace", "workflow", "backup", "startup"], "surfaces": ["host_compatibility_projection", "validation", "maintenance_regression", "closeout_reconciliation"], "reason": "WSL workspace lifecycle, host compatibility projection, backup, or closeout regression coverage changed."},
    {"prefix": "_bridge/wsl_codex_app_server.py", "systems": ["wsl_workspace", "workflow", "startup", "backup"], "surfaces": ["identity", "lifecycle", "activation_contract", "execution_contract", "result_contract", "validation", "maintenance_surface", "maintenance_regression", "closeout"], "reason": "WSL user-level Codex app-server lifecycle, Linux executable selection, isolated CODEX_HOME, Unix socket boundary, or systemd activation changed."},
    {"prefix": "_bridge/wsl_codex_app_server_tests.py", "systems": ["wsl_workspace", "workflow", "startup"], "surfaces": ["validation", "maintenance_regression", "closeout_reconciliation"], "reason": "WSL Codex app-server owner regression coverage changed."},
    {"prefix": "_bridge/shared/wsl_user_systemd", "systems": ["wsl_workspace", "startup", "mcp", "backup"], "surfaces": ["activation_contract", "execution_contract", "validation", "maintenance_surface", "maintenance_regression", "closeout_reconciliation"], "reason": "Shared atomic WSL user-systemd installation, backup, status, or service-control behavior changed."},
    {"prefix": "_bridge/windows_execution_agent.py", "systems": ["wsl_workspace", "workflow", "startup"], "surfaces": ["windows_execution_plane", "permission_boundary", "execution_contract", "result_contract", "validation", "maintenance_surface", "closeout"], "reason": "Typed WSL-to-Windows execution routing, least-privilege task lanes, fixed operation catalog, or result boundary changed."},
    {"prefix": "_bridge/windows_execution_agent_tests.py", "systems": ["wsl_workspace", "workflow", "startup"], "surfaces": ["windows_execution_plane", "permission_boundary", "maintenance_regression", "closeout_reconciliation"], "reason": "Windows execution-plane boundary or regression coverage changed."},
    {"prefix": "_bridge/shared/codex_scheduler_runner.py", "systems": ["wsl_workspace", "workflow", "startup"], "surfaces": ["scheduler_integration", "windows_execution_plane", "execution_contract", "maintenance_regression", "maintenance_surface", "closeout"], "reason": "Unified scheduler task declarations, retries, runtime reconciliation, or Windows execution-plane health scheduling changed."},
    {"prefix": "_bridge/developer_toolchain", "systems": ["wsl_workspace", "workflow", "resource"], "surfaces": ["identity", "lifecycle", "developer_toolchain", "clone_bootstrap", "validation", "maintenance_regression", "maintenance_surface", "closeout"], "reason": "Version-locked developer toolchain ownership, installation, PATH projection, or validation changed."},
    {"prefix": "_bridge/work_git_change_owner", "systems": ["wsl_workspace", "workflow", "backup"], "surfaces": ["identity", "lifecycle", "source_authority", "work_git_change_set", "validation", "rollback", "maintenance_regression", "closeout"], "reason": "Task worktree isolation, declared-path commit, local bare synchronization, fast-forward integration, or Git safety configuration changed."},
    {"prefix": "_bridge/policies/developer_toolchain.lock.json", "systems": ["wsl_workspace", "workflow", "resource"], "surfaces": ["developer_toolchain", "source_authority", "validation", "rollback", "closeout"], "reason": "Required developer tool versions, sources, hashes, or managed paths changed."},
    {"prefix": "_bridge/code_maintainability", "systems": ["wsl_workspace", "workflow"], "surfaces": ["developer_toolchain", "module_context", "placement_plan", "validation", "maintenance_regression", "maintenance_surface", "closeout_reconciliation"], "reason": "Developer toolchain probes, module discovery, placement planning, or their regression contract changed."},
    {"prefix": "_bridge/wsl_platform_owner_validator_tests.py", "systems": ["wsl_workspace", "mcp", "office", "workflow"], "surfaces": ["platform_projection", "diagnostics", "office_maintenance_surface", "workflow_route", "maintenance_regression", "closeout_reconciliation"], "reason": "Platform-aware owner validator regression coverage changed for WSL migration blockers and Windows-only assumptions."},
    {"prefix": "_bridge/platform_paths.py", "systems": ["wsl_workspace", "startup", "workflow"], "surfaces": ["source_authority", "host_compatibility_projection", "platform_projection", "execution_contract", "maintenance_regression", "closeout"], "reason": "Work Git or Windows host compatibility projection path resolution changed."},
    {"prefix": "_bridge/bootstrap_wsl_workspace.py", "systems": ["wsl_workspace"], "surfaces": ["clone_bootstrap", "platform_projection", "validation", "handoff_receipt", "rollback"], "reason": "WSL bootstrap validation or activation boundary changed."},
    {"prefix": "WSL_WORKSPACE.md", "systems": ["wsl_workspace", "workflow"], "surfaces": ["source_authority", "platform_projection", "maintenance_surface", "closeout"], "reason": "Long-lived WSL workspace authority and boundary documentation changed."},
    {"prefix": "_bridge/persistent_task_kernel", "systems": ["bridge"], "surfaces": ["contract_template", "impact_mapping", "maintenance_surface"], "reason": "Sidecar durable task lifecycle or behavior regression changed."},
    {"prefix": "_bridge/shared/email_scheduler.py", "systems": ["mail"], "surfaces": ["contract_template", "impact_mapping", "maintenance_surface"], "reason": "Mail owner behavior changed."},
    {"prefix": "_bridge/memory_", "systems": ["memory"], "surfaces": ["contract_template", "impact_mapping", "maintenance_surface"], "reason": "Memory owner or routing changed."},
    {"prefix": "_bridge/windows_memory_governance", "systems": ["memory"], "surfaces": ["contract_template", "impact_mapping", "maintenance_surface"], "reason": "Windows memory governance owner, tests, or routing changed."},
    {"prefix": "_bridge/hardware_system_owner", "systems": ["hardware", "workflow", "skills"], "surfaces": ["member_identity", "domain_discovery", "execution_contract", "result_contract", "maintenance_surface", "maintenance_regression", "closeout_reconciliation"], "reason": "Cross-platform hardware facade routing, aggregation, or permission-preserving handoff changed."},
    {"prefix": "_bridge/wsl_hardware_owner", "systems": ["hardware", "wsl_workspace"], "surfaces": ["member_identity", "dependency_contract", "platform_projection", "execution_contract", "result_contract", "maintenance_surface", "maintenance_regression", "closeout_reconciliation"], "reason": "WSL-visible block, USB, PCI, or GPU projection inventory and tool health changed."},
    {"prefix": "_bridge/windows_hardware_", "systems": ["hardware"], "surfaces": ["contract_template", "impact_mapping", "platform_projection", "maintenance_surface", "workflow_route", "maintenance_regression"], "reason": "Global read-only Windows hardware inventory, diagnostics, evidence, platform deferral, or routing changed."},
    {"prefix": "_bridge/mtp_media_archive_owner", "systems": ["hardware"], "surfaces": ["contract_template", "impact_mapping", "maintenance_surface", "execution_contract", "result_contract", "maintenance_regression", "closeout_reconciliation"], "reason": "Read-only MTP public-media archive discovery, manifest-planning boundary, or regression coverage changed."},
    {"prefix": "_bridge/shared/windows_powershell", "systems": ["hardware", "mcp"], "surfaces": ["execution_contract", "result_contract", "maintenance_regression", "maintenance_surface"], "reason": "Shared UTF-16LE Windows PowerShell transport changed for fixed owner-authored host calls."},
    {"prefix": "_bridge/shared/windows_runtime_assets", "systems": ["hardware", "mcp", "startup"], "surfaces": ["execution_contract", "platform_projection", "maintenance_regression", "maintenance_surface", "closeout_reconciliation"], "reason": "Shared Windows-only GUI, OCR, audio, or hardware runtime path authority changed."},
    {"prefix": "_bridge/docs/hardware_system_capability_model.md", "systems": ["hardware"], "surfaces": ["contract_template", "maintenance_surface"], "reason": "Hardware capability boundaries, evidence model, or admission order changed."},
    {"prefix": "_bridge/usb_device_", "systems": ["hardware"], "surfaces": ["contract_template", "impact_mapping", "maintenance_surface", "execution_contract", "result_contract", "maintenance_regression"], "reason": "USB device inventory, diagnostics, guarded control, receipt, rollback, or regression behavior changed."},
    {"prefix": "_bridge/runtime/music_library/corrections/", "systems": ["audio"], "surfaces": ["execution_contract", "result_contract", "maintenance_regression"], "reason": "Reviewed music metadata corrections can change governed library planning and validation results."},
    {"prefix": "_bridge/music_library_owner.py", "systems": ["audio", "hardware"], "surfaces": ["contract_template", "impact_mapping", "maintenance_surface", "execution_contract", "result_contract", "maintenance_regression"], "reason": "Music-library orchestration or its USB storage-health handoff changed."},
    {"prefix": "_bridge/music_library_", "systems": ["audio"], "surfaces": ["contract_template", "impact_mapping", "maintenance_surface", "execution_contract", "result_contract", "maintenance_regression"], "reason": "Music-library planning, transaction, rollback, or regression behavior changed."},
    {"prefix": "_bridge/audio_toolkit/", "systems": ["audio"], "surfaces": ["contract_template", "impact_mapping", "maintenance_surface", "execution_contract", "maintenance_regression"], "reason": "Audio inspection, transformation, transcription, or GUI toolkit behavior changed."},
    {"prefix": "_bridge/docs/audio_system_capability_model.md", "systems": ["audio", "hardware"], "surfaces": ["contract_template", "maintenance_surface", "impact_mapping"], "reason": "Audio ownership or the hardware handoff contract changed."},
    {"prefix": "_bridge/resource_process_", "systems": ["resource", "mcp", "startup"], "surfaces": ["contract_template", "impact_mapping", "maintenance_surface"], "reason": "Resource/MCP process lifecycle, reporting, or cleanup governance changed."},
    {"prefix": "_bridge/self_update_governance", "systems": ["workflow", "skills", "memory", "resource"], "surfaces": ["closeout_reconciliation", "maintenance_surface", "impact_mapping"], "reason": "Self-update owner selection, evidence freshness, or closeout review reconciliation changed."},
    {"prefix": "_bridge/shared/record_store_maintenance", "systems": ["records"], "surfaces": ["contract_template", "impact_mapping", "validation", "maintenance_surface", "maintenance_regression"], "reason": "Record index, retention, archive, validation, or regression behavior changed."},
    {"prefix": "_bridge/shared/resource_event_store", "systems": ["records", "resource"], "surfaces": ["state_store", "resource_owner_route", "result_contract", "maintenance_regression"], "reason": "Resource request/event projection storage or query behavior changed."},
    {"prefix": "_bridge/shared/codex_reporter", "systems": ["records", "workflow"], "surfaces": ["result_contract", "maintenance_surface", "platform_projection"], "reason": "Maintenance report evidence, record output, or Windows projection behavior changed."},
    {"prefix": "_bridge/shared/performance_maintenance_job", "systems": ["records", "workflow", "startup"], "surfaces": ["scheduler_integration", "execution_contract", "result_contract", "maintenance_surface", "platform_projection"], "reason": "Scheduled performance maintenance execution, evidence, or Windows projection behavior changed."},
    {"prefix": "_bridge/shared/long_command_receipt", "systems": ["workflow", "records"], "surfaces": ["execution_contract", "result_contract", "maintenance_regression", "closeout_reconciliation"], "reason": "Long-command process consumption, timeout cleanup, bounded output, or durable terminal receipt behavior changed."},
    {"prefix": "_bridge/defender_governance", "systems": ["wsl_workspace", "startup", "workflow"], "surfaces": ["windows_execution_plane", "platform_projection", "execution_contract", "maintenance_surface", "maintenance_regression"], "reason": "Windows Defender maintenance governance or its host projection changed."},
    {"prefix": "_bridge/skill_", "systems": ["skills"], "surfaces": ["contract_template", "impact_mapping", "maintenance_surface"], "reason": "Skill lifecycle or routing changed."},
    {"prefix": "_bridge/codex_config_guard.py", "systems": ["startup"], "surfaces": ["contract_template", "impact_mapping", "maintenance_surface"], "reason": "Codex startup guard changed."},
    {"prefix": "_bridge/codex_baseline_update", "systems": ["startup", "mcp", "wsl_workspace"], "surfaces": ["contract_template", "platform_projection", "impact_mapping", "maintenance_surface", "maintenance_regression"], "reason": "Codex startup-baseline convergence, host-path resolution, or regression coverage changed."},
    {"prefix": "_bridge/codex_desktop_protocol_compatibility", "systems": ["startup"], "surfaces": ["contract_template", "impact_mapping", "maintenance_surface", "maintenance_regression"], "reason": "Codex Desktop protocol compatibility inspection or regression behavior changed."},
    {"prefix": "_bridge/codex_appserver_model_bridge.py", "systems": ["startup"], "surfaces": ["contract_template", "impact_mapping", "maintenance_surface"], "reason": "Codex Desktop signed-package asset discovery or read-only AppServer compatibility support changed."},
    {"prefix": "_bridge/codex_desktop_model_runtime.py", "systems": ["startup", "wsl_workspace"], "surfaces": ["activation_contract", "execution_contract", "result_contract", "host_compatibility_projection", "maintenance_surface", "maintenance_regression"], "reason": "Codex Desktop CDP model-runtime inspection, process-preserving page refresh, or Windows host projection changed."},
    {"prefix": "_bridge/install-codex-config-guard-task.ps1", "systems": ["startup"], "surfaces": ["activation_contract", "maintenance_surface", "startup_baseline"], "reason": "Codex config-guard scheduled-task activation or concurrency policy changed."},
    {"prefix": "_bridge/codex_startup_chain_tests.py", "systems": ["startup"], "surfaces": ["maintenance_regression", "activation_contract"], "reason": "Codex startup ownership, hidden-launch, or scheduled-task regression coverage changed."},
    {"prefix": "codex-home/scripts/start-codex-desktop-elevated.ps1", "systems": ["startup", "wsl_workspace"], "surfaces": ["activation_contract", "host_compatibility_projection", "maintenance_regression"], "reason": "The governed Codex Desktop launch lifecycle or host compatibility projection changed."},
    {"prefix": "codex-home/scripts/restart-codex-desktop-cdp.ps1", "systems": ["startup", "wsl_workspace"], "surfaces": ["activation_contract", "execution_contract", "result_contract", "host_compatibility_projection", "maintenance_regression"], "reason": "The process-preserving Codex Desktop page refresh facade or host compatibility projection changed."},
    {"prefix": "_bridge/codex_config_projection", "systems": ["startup"], "surfaces": ["contract_template", "impact_mapping", "maintenance_surface", "startup_baseline"], "reason": "Codex/CC Switch configuration ownership or replay projection changed."},
    {"prefix": "_bridge/install-codex-model-provider-watcher-task.ps1", "systems": ["startup"], "surfaces": ["activation_contract", "maintenance_regression", "maintenance_surface", "startup_baseline"], "reason": "Provider watcher scheduled-task activation, recovery, or singleton policy changed."},
    {"prefix": "_bridge/codex_model_provider_watcher.py", "systems": ["startup"], "surfaces": ["activation_contract", "maintenance_surface", "startup_baseline"], "reason": "Provider-change monitoring or runtime/config projection activation changed."},
    {"prefix": "_bridge/codex_model_provider_tests.py", "systems": ["startup"], "surfaces": ["maintenance_regression", "activation_contract"], "reason": "Provider watcher or Desktop model-runtime regression coverage changed."},
    {"prefix": "_bridge/codex_prelaunch_maintenance", "systems": ["startup"], "surfaces": ["activation_contract", "execution_contract", "result_contract", "maintenance_regression", "maintenance_surface"], "reason": "Governed Codex launcher pre-launch maintenance, timeout, or fail-open receipt behavior changed."},
    {"prefix": "_bridge/shared/system_maintenance_cli.py", "systems": ["workflow"], "surfaces": ["maintenance_surface", "impact_mapping"], "reason": "Unified maintenance command discovery or dispatch changed."},
    {"prefix": "_bridge/codex_session_store_doctor", "systems": ["startup"], "surfaces": ["contract_template", "impact_mapping", "maintenance_surface"], "reason": "Session restore maintenance or its regression coverage changed."},
    {"prefix": "_bridge/draft_governance.py", "systems": ["drafts", "workflow"], "surfaces": ["contract_template", "impact_mapping", "maintenance_surface", "workflow_route"], "reason": "Draft lifecycle semantics changed."},
    {"prefix": "_bridge/workflow_review_queue.py", "systems": ["drafts", "workflow"], "surfaces": ["state_store", "maintenance_surface", "closeout_receipt"], "reason": "Persistent review disposition semantics changed."},
]


SURFACE_NEXT_COMMANDS: dict[str, list[str]] = {
    "registration": ["python _bridge\\mcp_session_doctor.py validate"],
    "capability_matrix": ["targeted readback _bridge\\docs\\mcp_capability_matrix.md"],
    "derived_route_index": ["python _bridge\\mcp_capability_routes.py build", "python _bridge\\mcp_capability_routes.py validate"],
    "hub_adapter": ["python _bridge\\local_mcp_hub.py validate"],
    "diagnostics": ["python _bridge\\mcp_session_doctor.py validate"],
    "workflow_route": ["python _bridge\\workflow_orchestrator.py validate"],
    "resource_owner_route": ["resource-layer smoke/validator for the affected owner route"],
    "startup_baseline": ["python _bridge\\codex_workflow_entry.py closeout --config-changed --auto-finalize ..."],
    "closeout_receipt": ["python _bridge\\codex_workflow_entry.py closeout --task-kind system_membership --outcome ok ..."],
    "contract_template": ["python _bridge\\system_membership.py validate"],
    "impact_mapping": ["python _bridge\\system_membership.py impact --changed <file>"],
    "maintenance_surface": ["targeted readback _bridge\\docs\\maintenance_surface_map.md", "python _bridge\\global_coherence_doctor.py validate"],
    "member_identity": ["python _bridge\\system_membership.py plan --system <system> --member <member> --kind <kind>"],
    "domain_discovery": ["python _bridge\\system_membership.py validate", "python _bridge\\workflow_orchestrator.py validate", "python _bridge\\skill_orchestrator.py validate"],
    "activation_contract": ["run targeted registration, route, runtime, and restart/reload validation"],
    "dependency_contract": ["consume the owning resource/package receipt and run the member validator"],
    "execution_contract": ["run the member schema validator and focused multi-item regression"],
    "result_contract": ["verify acceptance evidence, result readback, and consumption acknowledgement"],
    "maintenance_regression": ["run the member owner validator and python _bridge\\system_membership.py validate"],
    "closeout_reconciliation": ["python _bridge\\workflow_finalization_tests.py", "python _bridge\\codex_workflow_entry.py closeout --finalization-changed-file <file> --validation-receipt system_membership=ok"],
    "delegation_contract": ["python _bridge\\structured_task_envelope_tests.py"],
    "owner_facade": ["python _bridge\\resource_fetcher_tests.py"],
    "strategy_consumption": ["python _bridge\\resource_fetcher_tests.py"],
    "maintenance_upgrade_governance": ["python _bridge\\maintenance_upgrade_governance.py validate"],
    "action_synthesis": ["python _bridge\\workflow_action_synthesis.py"],
    "action_receipt_contract": ["python _bridge\\workflow_owner_facade.py validate"],
    "facade_lifecycle": ["python _bridge\\codex_workflow_entry.py --help"],
    "owner_adapter_capability": ["python _bridge\\workflow_owner_facade.py snapshot"],
    "owner_state_source": ["python _bridge\\workflow_owner_facade.py validate"],
    "office_harness": ["cli-anything-microsoft-office --json system status"],
    "office_operation_contract": ["python -m pytest -q _bridge\\cli_anything_microsoft_office\\agent-harness\\cli_anything\\microsoft_office\\tests\\test_core.py"],
    "office_workflow_route": ["python _bridge\\workflow_orchestrator.py validate"],
    "office_owner_adapter": ["python _bridge\\workflow_owner_facade.py validate"],
    "office_skill_route": ["python _bridge\\skill_orchestrator.py validate"],
    "office_maintenance_surface": ["python _bridge\\cli_anything_governance.py validate"],
    "lifecycle_identity": ["python _bridge\\system_membership.py retirement-plan --system <system> --member <member>"],
    "replacement_readiness": ["run the replacement owner's targeted validator"],
    "registration_exit": ["run the owning registration/config validator"],
    "generation_exit": ["run the owning generator/repair regression probe"],
    "routing_exit": ["run the owning route snapshot and validator"],
    "runtime_exit": ["run the owning runtime/process doctor"],
    "maintenance_exit": ["run the owning doctor, repair-plan, and validate commands"],
    "guidance_exit": ["python _bridge\\system_membership.py doctor"],
    "data_disposition": ["record retention or migration disposition through the data owner"],
    "dependency_release": ["validate upstream and downstream migration or explicit blocking state"],
    "prevention_guard": ["python _bridge\\system_membership.py validate"],
    "retirement_receipt": ["python _bridge\\codex_workflow_entry.py closeout --task-kind system_membership ..."],
}


def normalize_path(path: str) -> str:
    return str(path or "").replace("\\", "/").lstrip("./")


def contract_for(system: str) -> dict[str, Any]:
    contract = CONTRACTS.get(system, {})
    if not contract:
        return {}
    active_surfaces = list(contract.get("required_surfaces", []))
    active_keys = {str(item.get("key") or "") for item in active_surfaces if isinstance(item, dict)}
    active_surfaces.extend(
        item for item in MEMBER_INTEGRATION_SURFACES if str(item.get("key") or "") not in active_keys
    )
    active_keys = {str(item.get("key") or "") for item in active_surfaces if isinstance(item, dict)}
    active_surfaces.extend(
        item for item in MEMBER_LIFECYCLE_SURFACES if str(item.get("key") or "") not in active_keys
    )
    return {
        **contract,
        "routing_domains": routing_domains_for_system(system),
        "required_surfaces": active_surfaces,
        "integration_policy": INTEGRATION_POLICY,
        "lifecycle_policy": LIFECYCLE_POLICY,
        "retirement_surfaces": RETIREMENT_SURFACES,
    }


def required_surface_keys(system: str, lifecycle: str = "active") -> list[str]:
    contract = contract_for(system)
    surface_key = "required_surfaces" if lifecycle == "active" else "retirement_surfaces"
    return [
        str(item.get("key") or "")
        for item in contract.get(surface_key, [])
        if isinstance(item, dict) and item.get("required", True)
    ]


def load_startup_baseline(path: Path = STARTUP_BASELINE) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def load_decommissioned_mcp(path: Path = STARTUP_BASELINE) -> dict[str, dict[str, Any]]:
    payload = load_startup_baseline(path)
    raw = payload.get("decommissioned_mcp") if isinstance(payload, dict) else {}
    return {str(name): value for name, value in raw.items() if isinstance(value, dict)} if isinstance(raw, dict) else {}


def load_decommissioned_scheduled_tasks(path: Path = STARTUP_BASELINE) -> dict[str, dict[str, Any]]:
    payload = load_startup_baseline(path)
    raw = payload.get("decommissioned_scheduled_tasks") if isinstance(payload, dict) else {}
    return {str(name): value for name, value in raw.items() if isinstance(value, dict)} if isinstance(raw, dict) else {}


def retirement_archive_root(path: Path = STARTUP_BASELINE) -> str:
    payload = load_startup_baseline(path)
    return str(payload.get("decommissioned_member_archive_root") or "")


def normalized_active_trace_paths(spec: dict[str, Any]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    raw_items = spec.get("active_trace_paths")
    for raw in raw_items if isinstance(raw_items, list) else []:
        item = {"path": raw} if isinstance(raw, str) else raw
        if not isinstance(item, dict) or not str(item.get("path") or "").strip():
            continue
        normalized.append(
            {
                "path": str(item["path"]),
                "surface": str(item.get("surface") or "implementation_exit"),
                "kind": str(item.get("kind") or "active_trace"),
            }
        )
    return normalized


def retirement_tombstones(path: Path = STARTUP_BASELINE) -> list[dict[str, Any]]:
    archive_root = retirement_archive_root(path)
    mcp_tombstones = [
        {
            "id": f"mcp:{name}",
            "system": "mcp",
            "member": name,
            "kind": "mcp_server",
            "lifecycle": "decommissioned",
            "owner": "Codex startup and memory governance",
            "replacement": str(spec.get("replaced_by") or ""),
            "reason": str(spec.get("reason") or ""),
            "history_policy": str(spec.get("data_retention") or ""),
            "archive_root": archive_root,
            "active_trace_paths": normalized_active_trace_paths(spec),
            "prevention_evidence": [
                "codex_state_repair removes reintroduced registration",
                "codex_state_audit rejects configured decommissioned MCPs",
                "system_membership doctor rejects current guidance references",
            ],
        }
        for name, spec in sorted(load_decommissioned_mcp(path).items())
    ]
    scheduled_task_tombstones = [
        {
            "id": f"scheduled_task:{name}",
            "system": "scheduled_task",
            "member": name,
            "kind": "windows_scheduled_task",
            "lifecycle": "decommissioned",
            "owner": str(spec.get("owner") or "Codex scheduler governance"),
            "replacement": str(spec.get("replaced_by") or ""),
            "reason": str(spec.get("reason") or ""),
            "history_policy": str(spec.get("data_retention") or ""),
            "archive_root": archive_root,
            "active_trace_paths": normalized_active_trace_paths(spec),
            "prevention_evidence": [
                "legacy scheduled task is absent from Windows Task Scheduler",
                "legacy installer and runner are absent from active paths",
                "system_membership doctor rejects active implementation or registration traces",
            ],
        }
        for name, spec in sorted(load_decommissioned_scheduled_tasks(path).items())
    ]
    return [*mcp_tombstones, *scheduled_task_tombstones]


def configured_mcp_names(path: Path = CODEX_CONFIG) -> set[str]:
    if not path.exists():
        return set()
    try:
        import tomllib

        payload = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return set()
    servers = payload.get("mcp_servers") if isinstance(payload, dict) else {}
    return set(str(name) for name in servers) if isinstance(servers, dict) else set()


def current_guidance_references(
    member_names: list[str], paths: list[Path] | None = None
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for path in paths or CURRENT_GUIDANCE_PATHS:
        normalized_path = "/" + normalize_path(str(path)).lower().strip("/") + "/"
        if any(marker in normalized_path for marker in HISTORICAL_PATH_MARKERS):
            continue
        if not path.exists() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        for member in member_names:
            if member and member in text:
                findings.append({"member": member, "path": str(path), "surface": "guidance_exit"})
    return findings


def active_retirement_path_findings(
    tombstones: list[dict[str, Any]], root: Path = ROOT
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for item in tombstones:
        member = str(item.get("member") or "")
        raw_paths = item.get("active_trace_paths")
        for raw in raw_paths if isinstance(raw_paths, list) else []:
            path_spec = {"path": raw} if isinstance(raw, str) else raw
            if not isinstance(path_spec, dict):
                continue
            raw_path = str(path_spec.get("path") or "").strip()
            if not raw_path:
                continue
            candidate = Path(raw_path)
            resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
            normalized = "/" + normalize_path(str(resolved)).lower().strip("/") + "/"
            if any(marker in normalized for marker in HISTORICAL_PATH_MARKERS):
                continue
            if resolved.exists():
                findings.append(
                    {
                        "member": member,
                        "path": raw_path,
                        "resolved_path": str(resolved),
                        "surface": str(path_spec.get("surface") or "implementation_exit"),
                        "kind": str(path_spec.get("kind") or "active_trace"),
                    }
                )
    return findings


def retirement_state_issues(
    tombstones: list[dict[str, Any]],
    configured_names: set[str],
    guidance_paths: list[Path] | None = None,
    active_root: Path = ROOT,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    required_tombstone_fields = (
        "id",
        "system",
        "member",
        "kind",
        "lifecycle",
        "owner",
        "replacement",
        "reason",
        "history_policy",
        "prevention_evidence",
    )
    for item in tombstones:
        missing = [key for key in required_tombstone_fields if not item.get(key)]
        if missing:
            issues.append(
                {
                    "severity": "risk",
                    "code": "retirement_tombstone_incomplete",
                    "member": item.get("member"),
                    "missing": missing,
                }
            )
    retired_mcp_names = [
        str(item.get("member") or "")
        for item in tombstones
        if item.get("system") == "mcp" and item.get("lifecycle") in {"decommissioned", "historical_only"}
    ]
    configured_retired = sorted(set(retired_mcp_names) & configured_names)
    if configured_retired:
        issues.append(
            {
                "severity": "risk",
                "code": "decommissioned_member_registered",
                "system": "mcp",
                "members": configured_retired,
            }
        )
    retired_member_names = [
        str(item.get("member") or "")
        for item in tombstones
        if item.get("lifecycle") in {"decommissioned", "historical_only"}
    ]
    for finding in current_guidance_references(retired_member_names, guidance_paths):
        issues.append(
            {
                "severity": "risk",
                "code": "decommissioned_member_in_current_guidance",
                **finding,
            }
        )
    for finding in active_retirement_path_findings(tombstones, active_root):
        issues.append(
            {
                "severity": "risk",
                "code": "decommissioned_member_active_path",
                **finding,
            }
        )
    return issues


def retirement_signal(
    message: str = "",
    changed: list[str] | None = None,
    *,
    tombstones: list[dict[str, Any]] | None = None,
    configured_names: set[str] | None = None,
    guidance_paths: list[Path] | None = None,
    active_root: Path = ROOT,
) -> dict[str, Any]:
    """Emit negative membership constraints without reactivating retired members."""

    tombstone_items = list(tombstones) if tombstones is not None else retirement_tombstones()
    configured = set(configured_names) if configured_names is not None else configured_mcp_names()
    changed_items = [str(item) for item in (changed or []) if str(item).strip()]
    signal_text = " ".join([str(message or ""), *changed_items]).casefold()
    lifecycle_intent = any(term.casefold() in signal_text for term in RETIREMENT_INTENT_TERMS)

    by_member = {
        str(item.get("member") or ""): item
        for item in tombstone_items
        if str(item.get("member") or "")
    }
    matched_members = {
        member for member in by_member if member.casefold() in signal_text
    }
    if lifecycle_intent and not matched_members:
        matched_members.update(by_member)

    issues = retirement_state_issues(tombstone_items, configured, guidance_paths, active_root)
    drift_members: set[str] = set()
    for issue in issues:
        if issue.get("member"):
            drift_members.add(str(issue["member"]))
        drift_members.update(str(item) for item in issue.get("members", []) if str(item))

    triggered_members = sorted(matched_members | drift_members)
    active_trace_issues = [
        issue
        for issue in issues
        if not triggered_members
        or str(issue.get("member") or "") in triggered_members
        or bool(set(str(item) for item in issue.get("members", [])) & set(triggered_members))
    ]
    triggered = bool(triggered_members or active_trace_issues)
    purge_required = bool(active_trace_issues)
    replacements = {
        member: str(by_member[member].get("replacement") or "none")
        for member in triggered_members
        if member in by_member
    }
    return {
        "schema": f"{SCHEMA}.retirement_signal",
        "ok": not purge_required,
        "generated_at": now_iso(),
        "read_only": True,
        "triggered": triggered,
        "status": "purge_required" if purge_required else ("guard_active" if triggered else "clear"),
        "directive": (
            "purge_active_influence"
            if purge_required
            else ("enforce_negative_tombstone" if triggered else "none")
        ),
        "do_not_route": triggered_members,
        "do_not_invoke": triggered_members,
        "do_not_generate": triggered_members,
        "do_not_recommend": triggered_members,
        "do_not_repair_or_restore": triggered_members,
        "use_replacement": replacements,
        "purge_surfaces": list(RETIREMENT_PURGE_SURFACES) if triggered else [],
        "proof_surfaces": list(RETIREMENT_PROOF_SURFACES) if triggered else [],
        "required_surfaces": list(RETIREMENT_PURGE_SURFACES) if triggered else [],
        "closure_actions": [
            {
                "surface": key,
                "action": "remove active trace through the owning repair and validation surface",
            }
            for key in RETIREMENT_PURGE_SURFACES
        ]
        if triggered
        else [],
        "owner_actions": [
            {
                "surface": key,
                "action": "remove active trace through the owning repair and validation surface",
            }
            for key in RETIREMENT_PURGE_SURFACES
        ]
        if triggered
        else [],
        "codex_instructions": [
            "Do not invoke, route to, generate, recommend, repair, restore, or register a retired member.",
            "When an active trace is found, send it to the owner of that surface for removal; do not preserve a compatibility facade for a fully retired member.",
            "Read isolated historical evidence only for an explicit migration or audit task, and never treat it as current capability or health evidence.",
            "Do not copy the retirement list into another module; read the authoritative tombstone source dynamically.",
            "Use the declared replacement only after its owner validation succeeds.",
        ]
        if triggered
        else [],
        "active_trace_issues": active_trace_issues,
        "matched_by": {
            "member": sorted(matched_members),
            "active_drift": sorted(drift_members),
            "lifecycle_intent": lifecycle_intent,
            "changed": changed_items,
        },
        "tombstone_source": str(STARTUP_BASELINE),
        "historical_evidence_policy": "retain_as_isolated_evidence_but_never_activate_route_generate_recommend_or_validate_as_current",
        "membership_rule": "negative tombstones are guard signals, not active system members",
    }


def snapshot() -> dict[str, Any]:
    contracts = {system: contract_for(system) for system in sorted(CONTRACTS)}
    tombstones = retirement_tombstones()
    projection = mirror_source_projection()
    return {
        "schema": f"{SCHEMA}.snapshot",
        "ok": True,
        "generated_at": now_iso(),
        "read_only": True,
        "systems": sorted(CONTRACTS),
        "impact_rule_count": len(IMPACT_RULES),
        "contracts": contracts,
        "integration_policy": INTEGRATION_POLICY,
        "lifecycle_policy": LIFECYCLE_POLICY,
        "retirement_tombstones": tombstones,
        "retirement_tombstone_count": len(tombstones),
        "mirror_source_projection": projection,
    }


def plan(
    system: str,
    member: str,
    kind: str = "",
    lifecycle: str = "active",
    replacement: str = "",
    reason: str = "",
) -> dict[str, Any]:
    contract = contract_for(system)
    if not contract:
        return {
            "schema": f"{SCHEMA}.plan",
            "ok": False,
            "generated_at": now_iso(),
            "system": system,
            "member": member,
            "kind": kind,
            "blockers": [{"code": "unknown_system", "message": f"No contract template for system: {system}"}],
            "admission_options": [
                {
                    "action": "add_new_system_contract",
                    "requires": [
                        "contract template with member kinds and health boundaries",
                        "workflow and skill domain binding",
                        "active-member registry entry",
                        "impact rules for owner and tests",
                        "maintenance discovery and focused regression",
                    ],
                    "auto_apply": False,
                }
            ],
        }
    member_kind = kind or str(contract.get("default_member_kind") or "")
    valid_kinds = set(str(item) for item in contract.get("member_kinds", []))
    blockers: list[dict[str, Any]] = []
    if member_kind not in valid_kinds:
        blockers.append({"code": "unknown_member_kind", "message": f"Unsupported member kind for {system}: {member_kind}", "valid_kinds": sorted(valid_kinds)})
    if lifecycle not in LIFECYCLE_STATES:
        blockers.append({"code": "unknown_lifecycle", "message": f"Unsupported lifecycle: {lifecycle}", "valid_lifecycles": list(LIFECYCLE_STATES)})
    if lifecycle in {"decommissioning", "decommissioned"} and not reason.strip():
        blockers.append({"code": "retirement_reason_missing", "message": "Decommissioning requires a durable reason."})
    if lifecycle in {"decommissioning", "decommissioned"} and not replacement.strip():
        blockers.append({"code": "replacement_decision_missing", "message": "Declare a replacement member or the explicit value none."})
    surface_key = "required_surfaces" if lifecycle == "active" else "retirement_surfaces"
    surfaces = contract.get(surface_key, []) if isinstance(contract.get(surface_key), list) else []
    admission_options: list[dict[str, Any]] = []
    if member_kind not in valid_kinds:
        admission_options = [
            {
                "action": "register_member_under_existing_system",
                "system": system,
                "candidate_kind": str(contract.get("default_member_kind") or ""),
                "requires": ["active-member registry entry", "impact rule", "maintenance surface", "focused regression"],
                "auto_apply": False,
            },
            {
                "action": "extend_existing_system_member_kinds",
                "system": system,
                "requested_kind": member_kind,
                "requires": ["contract update", "member identity", "impact rule", "focused regression"],
                "auto_apply": False,
            },
        ]
    return {
        "schema": f"{SCHEMA}.plan",
        "ok": not blockers,
        "generated_at": now_iso(),
        "read_only": True,
        "system": system,
        "member": member,
        "kind": member_kind,
        "lifecycle": lifecycle,
        "replacement": replacement,
        "reason": reason,
        "routing_domains": contract.get("routing_domains", routing_domains_for_system(system)),
        "required_surface_keys": [item.get("key") for item in surfaces if isinstance(item, dict) and item.get("required", True)],
        "optional_surface_keys": [item.get("key") for item in surfaces if isinstance(item, dict) and not item.get("required", True)],
        "surfaces": surfaces,
        "completion_rule": LIFECYCLE_POLICY["completion_rule"] if lifecycle != "active" else "all required active-member surfaces are synchronized and owner validators pass",
        "integration_policy": contract.get("integration_policy", INTEGRATION_POLICY),
        "completion_checks": [
            "membership_admission_plan_consumed_before_activation",
            "all_required_surfaces_have_owner_evidence",
            "registration_generation_routing_runtime_and_maintenance_converge",
            "structured_multi_item_identity_and_acceptance_survive_end_to_end",
            "external_dependencies_have_owner_receipts_and_verification",
            "result_producing_work_is_consumed_or_explicitly_waived",
            "member_has_discoverable_workflow_and_skill_domains",
            "reload_or_restart_boundary_is_explicit_and_validated",
            "focused_regressions_isolate_test_state_and_allow_legitimate_concurrency",
            "changed_files_reconciled_through_membership_impact_before_closeout",
            "workflow_closeout_blocks_unresolved_membership_reconciliation",
        ] if lifecycle == "active" else [],
        "history_rule": LIFECYCLE_POLICY["history_rule"],
        "non_goals": contract.get("non_goals", []),
        "required_next_commands": commands_for_surfaces([str(item.get("key") or "") for item in surfaces if isinstance(item, dict)]),
        "blockers": blockers,
        "admission_options": admission_options,
    }


def retirement_plan(system: str, member: str, kind: str = "", replacement: str = "none", reason: str = "planned retirement") -> dict[str, Any]:
    payload = plan(system, member, kind, "decommissioning", replacement, reason)
    payload["schema"] = f"{SCHEMA}.retirement_plan"
    payload["repair_mode"] = "owner_planned"
    payload["apply_rule"] = "apply each action through its owning surface; do not bulk-delete unclassified history"
    payload["retirement_signal"] = retirement_signal(message=f"retire {member}")
    return payload


def commands_for_surfaces(surface_keys: list[str]) -> list[str]:
    commands: list[str] = []
    for key in surface_keys:
        for command in SURFACE_NEXT_COMMANDS.get(key, []):
            if command not in commands:
                commands.append(command)
    return commands


def impact(changed: list[str]) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    affected_systems: list[str] = []
    affected_surfaces: list[str] = []
    normalized = [normalize_path(item) for item in changed if str(item or "").strip()]
    for path in normalized:
        aliases = [path]
        if path.lower().startswith("workspace/"):
            aliases.append(path[len("workspace/"):])
        marker = "codex-env-mirror/"
        marker_index = path.lower().find(marker)
        if marker_index >= 0:
            aliases.append(path[marker_index:])
        for rule in IMPACT_RULES:
            prefix = normalize_path(str(rule.get("prefix") or ""))
            if any(alias == prefix or alias.startswith(prefix) for alias in aliases):
                match = {"changed": path, **rule}
                matches.append(match)
                for system in rule.get("systems", []):
                    if system not in affected_systems:
                        affected_systems.append(system)
                for surface_key in rule.get("surfaces", []):
                    if surface_key not in affected_surfaces:
                        affected_surfaces.append(surface_key)
    matched_paths = {str(item.get("changed") or "") for item in matches}
    system_candidate_paths = [
        path
        for path in normalized
        if path.startswith("_bridge/")
        or path.startswith("workspace/_bridge/")
        or "codex-env-mirror/" in path.lower()
        or path == "AGENTS.md"
        or path == "C:/Users/45543/.codex/AGENTS.md"
    ]
    unmapped_system_changed = [path for path in system_candidate_paths if path not in matched_paths]
    blockers: list[dict[str, Any]] = []
    if unmapped_system_changed:
        blockers.append(
            {
                "code": "system_change_partially_unmapped",
                "message": "One or more system-level changed files have no membership impact rule.",
                "paths": unmapped_system_changed,
                "safe_next_step": "register each owning prefix in IMPACT_RULES, then rerun impact",
            }
        )
    risks: list[dict[str, Any]] = []
    if any(system in affected_systems for system in ("mcp", "workflow", "resource")) and not affected_surfaces:
        risks.append({"code": "system_change_without_surface_match", "message": "Changed path looks system-level but no surface rule matched."})
    return {
        "schema": f"{SCHEMA}.impact",
        "ok": not blockers,
        "generated_at": now_iso(),
        "read_only": True,
        "changed": normalized,
        "matches": matches,
        "affected_systems": affected_systems,
        "affected_surfaces": affected_surfaces,
        "coverage_complete": not unmapped_system_changed,
        "unmapped_system_changed": unmapped_system_changed,
        "contract_upgrade_required": bool(matches),
        "required_next_commands": commands_for_surfaces(affected_surfaces),
        "blockers": blockers,
        "risks": risks,
        "advisories": [
            {
                "code": "read_only_contract",
                "message": "This command identifies required synchronization surfaces; it does not apply repairs.",
            }
        ] if matches else [],
    }


def upgrade_plan(system: str) -> dict[str, Any]:
    contract = contract_for(system)
    if not contract:
        return {
            "schema": f"{SCHEMA}.upgrade_plan",
            "ok": False,
            "generated_at": now_iso(),
            "system": system,
            "blockers": [{"code": "unknown_system", "message": f"No contract template for system: {system}"}],
        }
    surfaces = contract.get("architecture_surfaces", [])
    return {
        "schema": f"{SCHEMA}.upgrade_plan",
        "ok": True,
        "generated_at": now_iso(),
        "read_only": True,
        "system": system,
        "purpose": "When architecture changes, update the contract template, impact mapping, and maintenance surface before treating the change as complete.",
        "upgrade_surfaces": surfaces,
        "required_next_commands": commands_for_surfaces([str(item.get("key") or "") for item in surfaces if isinstance(item, dict)]),
        "migration_steps": [
            "identify affected systems and member kinds",
            "add or update the contract surface list",
            "add impact rules for files that can change the architecture",
            "update maintenance_surface_map.md so the contract is discoverable",
            "validate system_membership, owner validators, and global coherence",
        ],
        "blockers": [],
    }


def doctor() -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    integration_keys = {
        str(item.get("key") or "")
        for item in MEMBER_INTEGRATION_SURFACES
        if isinstance(item, dict) and item.get("required", True)
    }
    for system, contract in CONTRACTS.items():
        keys = required_surface_keys(system)
        if len(keys) != len(set(keys)):
            issues.append({"severity": "risk", "code": "duplicate_required_surface", "system": system, "keys": keys})
        if "capability_matrix" not in keys and system == "mcp":
            issues.append({"severity": "risk", "code": "mcp_capability_matrix_missing", "system": system})
        if "derived_route_index" not in keys and system == "mcp":
            issues.append({"severity": "risk", "code": "mcp_route_index_missing", "system": system})
        if not contract.get("architecture_surfaces"):
            issues.append({"severity": "risk", "code": "architecture_surfaces_missing", "system": system})
        enriched = contract_for(system)
        missing_integration = sorted(integration_keys - set(keys))
        if missing_integration:
            issues.append(
                {
                    "severity": "risk",
                    "code": "member_integration_surface_missing",
                    "system": system,
                    "surfaces": missing_integration,
                }
            )
        if not enriched.get("integration_policy"):
            issues.append({"severity": "risk", "code": "integration_policy_missing", "system": system})
        retirement_keys = required_surface_keys(system, "decommissioning")
        if not enriched.get("lifecycle_policy") or not enriched.get("retirement_surfaces"):
            issues.append({"severity": "risk", "code": "lifecycle_contract_missing", "system": system})
        if len(retirement_keys) != len(set(retirement_keys)):
            issues.append({"severity": "risk", "code": "duplicate_retirement_surface", "system": system, "keys": retirement_keys})
    domain_coverage = domain_binding_report()
    if not domain_coverage.get("ok"):
        issues.append({"severity": "risk", "code": "system_domain_binding_missing", "detail": domain_coverage})
    tombstones = retirement_tombstones()
    retirement_guard = retirement_signal(tombstones=tombstones)
    issues.extend(retirement_guard.get("active_trace_issues", []))
    if not any("_bridge/mcp_capability_routes.py" == normalize_path(str(rule.get("prefix") or "")) for rule in IMPACT_RULES):
        issues.append({"severity": "risk", "code": "mcp_route_impact_rule_missing"})
    status = "risk" if any(item.get("severity") == "risk" for item in issues) else "ok"
    return {
        "schema": f"{SCHEMA}.doctor",
        "ok": status != "risk",
        "generated_at": now_iso(),
        "status": status,
        "issues": issues,
        "retirement_signal": retirement_guard,
        "summary": {
            "system_count": len(CONTRACTS),
            "impact_rule_count": len(IMPACT_RULES),
            "integration_surface_count": len(MEMBER_INTEGRATION_SURFACES),
            "retirement_surface_count": len(RETIREMENT_SURFACES),
            "retirement_tombstone_count": len(tombstones),
            "risk_count": sum(1 for item in issues if item.get("severity") == "risk"),
        },
    }


def validate() -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    doc = doctor()
    for item in doc.get("issues", []):
        if isinstance(item, dict) and item.get("severity") == "risk":
            issues.append(item)
    projection = mirror_source_projection()
    issues.extend(projection.get("issues", []))
    try:
        import workflow_orchestrator
        import skill_orchestrator

        domain_coverage = domain_binding_report(
            workflow_domains={domain.key for domain in workflow_orchestrator.DOMAINS},
            skill_domains={domain.key for domain in skill_orchestrator.DOMAINS},
        )
    except Exception as exc:  # noqa: BLE001 - retain a consumable admission failure.
        domain_coverage = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if not domain_coverage.get("ok"):
        issues.append({"severity": "risk", "code": "system_domain_coverage_incomplete", "detail": domain_coverage})
    probe_plan = plan("mcp", "example", "mcp_server")
    for key in ("registration", "capability_matrix", "derived_route_index", "hub_adapter", "diagnostics", "workflow_route"):
        if key not in probe_plan.get("required_surface_keys", []):
            issues.append({"severity": "risk", "code": "plan_missing_required_surface", "surface": key})
    if "exit_strategy" not in probe_plan.get("required_surface_keys", []):
        issues.append({"severity": "risk", "code": "plan_missing_exit_strategy"})
    for key in (
        "member_identity",
        "domain_discovery",
        "activation_contract",
        "dependency_contract",
        "execution_contract",
        "result_contract",
        "maintenance_regression",
        "closeout_reconciliation",
    ):
        if key not in probe_plan.get("required_surface_keys", []):
            issues.append({"severity": "risk", "code": "plan_missing_integration_surface", "surface": key})
    if not probe_plan.get("integration_policy") or not probe_plan.get("completion_checks"):
        issues.append({"severity": "risk", "code": "plan_missing_integration_policy"})
    integration_policy = probe_plan.get("integration_policy") if isinstance(probe_plan.get("integration_policy"), dict) else {}
    for key in ("admission_rule", "domain_discovery_rule", "change_propagation_rule", "reconciliation_rule", "closeout_enforcement_rule"):
        if not integration_policy.get(key):
            issues.append({"severity": "risk", "code": "integration_policy_missing_rule", "rule": key})
    for check in (
        "membership_admission_plan_consumed_before_activation",
        "changed_files_reconciled_through_membership_impact_before_closeout",
        "workflow_closeout_blocks_unresolved_membership_reconciliation",
    ):
        if check not in probe_plan.get("completion_checks", []):
            issues.append({"severity": "risk", "code": "completion_check_missing", "check": check})
    resource_package_plan = plan("resource", "resource_node_package_owner", "package_owner")
    if not resource_package_plan.get("ok"):
        issues.append({"severity": "risk", "code": "resource_package_owner_kind_missing"})
    resource_batch_plan = plan("resource", "resource_scheduler", "batch_scheduler")
    if not resource_batch_plan.get("ok"):
        issues.append({"severity": "risk", "code": "resource_batch_scheduler_kind_missing"})
    resource_browser_plan = plan("resource", "cloakbrowser_owner", "browser_owner")
    if not resource_browser_plan.get("ok"):
        issues.append({"severity": "risk", "code": "resource_browser_owner_kind_missing"})
    probe_impact = impact(["_bridge/mcp_capability_routes.py"])
    if not probe_impact.get("contract_upgrade_required"):
        issues.append({"severity": "risk", "code": "impact_did_not_require_contract_upgrade"})
    if "derived_route_index" not in probe_impact.get("affected_surfaces", []):
        issues.append({"severity": "risk", "code": "impact_missing_route_index_surface"})
    repair_test_impact = impact(["_bridge/codex_state_repair_tests.py"])
    if not repair_test_impact.get("coverage_complete") or "startup" not in repair_test_impact.get("affected_systems", []):
        issues.append({"severity": "risk", "code": "codex_state_repair_family_impact_missing"})
    probe_upgrade = upgrade_plan("mcp")
    if not probe_upgrade.get("ok") or not probe_upgrade.get("upgrade_surfaces"):
        issues.append({"severity": "risk", "code": "upgrade_plan_missing"})
    workflow_plan = plan("workflow", "_bridge/workflow_owner_facade.py", "owner_adapter")
    for key in ("action_synthesis", "action_receipt_contract", "facade_lifecycle", "owner_adapter_capability", "owner_state_source", "workflow_route", "maintenance_surface"):
        if key not in workflow_plan.get("required_surface_keys", []):
            issues.append({"severity": "risk", "code": "workflow_plan_missing_required_surface", "surface": key})
    workflow_impact = impact(["_bridge/workflow_owner_facade.py"])
    if "action_receipt_contract" not in workflow_impact.get("affected_surfaces", []):
        issues.append({"severity": "risk", "code": "workflow_impact_missing_contract_surface"})
    probe_maintenance_upgrade = impact(["_bridge/maintenance_upgrade_governance.py"])
    if not probe_maintenance_upgrade.get("contract_upgrade_required"):
        issues.append({"severity": "risk", "code": "maintenance_upgrade_governance_impact_missing"})
    probe_retirement = retirement_plan("mcp", "example-retired", "mcp_server", "example-replacement", "validation probe")
    for key in ("lifecycle_identity", "registration_exit", "generation_exit", "routing_exit", "maintenance_exit", "guidance_exit", "data_disposition", "prevention_guard", "retirement_receipt"):
        if key not in probe_retirement.get("required_surface_keys", []):
            issues.append({"severity": "risk", "code": "retirement_plan_missing_required_surface", "surface": key})
    if probe_retirement.get("lifecycle") != "decommissioning":
        issues.append({"severity": "risk", "code": "retirement_plan_lifecycle_invalid"})
    signal_probe = retirement_signal(
        message="remove legacy-member",
        tombstones=[
            {
                "id": "mcp:legacy-member",
                "system": "mcp",
                "member": "legacy-member",
                "kind": "mcp_server",
                "lifecycle": "decommissioned",
                "owner": "validation",
                "replacement": "replacement-member",
                "reason": "validation probe",
                "history_policy": "isolated evidence only",
                "prevention_evidence": ["validation"],
            }
        ],
        configured_names=set(),
        guidance_paths=[],
    )
    if not signal_probe.get("triggered") or "legacy-member" not in signal_probe.get("do_not_route", []):
        issues.append({"severity": "risk", "code": "retirement_signal_missing_negative_guard"})
    if signal_probe.get("use_replacement", {}).get("legacy-member") != "replacement-member":
        issues.append({"severity": "risk", "code": "retirement_signal_missing_replacement"})
    return {
        "schema": f"{SCHEMA}.validate",
        "ok": not any(item.get("severity") == "risk" for item in issues),
        "generated_at": now_iso(),
        "read_only": True,
        "issues": issues,
        "probes": {
            "plan_ok": probe_plan.get("ok"),
            "integration_policy_present": bool(probe_plan.get("integration_policy")),
            "resource_member_kinds_ok": all(
                item.get("ok") for item in (resource_package_plan, resource_batch_plan, resource_browser_plan)
            ),
            "impact_contract_upgrade_required": probe_impact.get("contract_upgrade_required"),
            "upgrade_plan_ok": probe_upgrade.get("ok"),
            "maintenance_upgrade_impact_required": probe_maintenance_upgrade.get("contract_upgrade_required"),
            "retirement_plan_ok": probe_retirement.get("ok"),
            "retirement_signal_triggered": signal_probe.get("triggered"),
            "mirror_source_projection_ok": projection.get("ok"),
            "mirror_source_member_count": len(projection.get("members", [])),
            "mirror_source_ids": projection.get("source_ids", []),
            "mirror_generated_source_ids": projection.get("generated_source_ids", []),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only system membership contract control plane")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("snapshot")
    sub.add_parser("mirror-source-projection")
    sub.add_parser("doctor")
    sub.add_parser("validate")
    p = sub.add_parser("plan")
    p.add_argument("--system", default="mcp")
    p.add_argument("--member", required=True)
    p.add_argument("--kind", default="")
    p.add_argument("--lifecycle", choices=LIFECYCLE_STATES, default="active")
    p.add_argument("--replacement", default="")
    p.add_argument("--reason", default="")
    r = sub.add_parser("retirement-plan", aliases=["repair-plan"])
    r.add_argument("--system", default="mcp")
    r.add_argument("--member", required=True)
    r.add_argument("--kind", default="")
    r.add_argument("--replacement", default="none")
    r.add_argument("--reason", default="planned retirement")
    s = sub.add_parser("retirement-signal")
    s.add_argument("--message", default="")
    s.add_argument("--changed", action="append", default=[])
    i = sub.add_parser("impact")
    i.add_argument("--changed", action="append", default=[])
    u = sub.add_parser("upgrade-plan")
    u.add_argument("--system", default="mcp")
    args = parser.parse_args(argv)
    if args.command == "snapshot":
        payload = snapshot()
    elif args.command == "mirror-source-projection":
        payload = mirror_source_projection()
    elif args.command == "doctor":
        payload = doctor()
    elif args.command == "validate":
        payload = validate()
    elif args.command == "plan":
        payload = plan(args.system, args.member, args.kind, args.lifecycle, args.replacement, args.reason)
    elif args.command in {"retirement-plan", "repair-plan"}:
        payload = retirement_plan(args.system, args.member, args.kind, args.replacement, args.reason)
    elif args.command == "retirement-signal":
        payload = retirement_signal(args.message, args.changed)
    elif args.command == "impact":
        payload = impact(args.changed)
    elif args.command == "upgrade-plan":
        payload = upgrade_plan(args.system)
    else:  # pragma: no cover
        parser.error(f"unsupported command: {args.command}")
    print_json(payload)
    return 0 if payload.get("ok", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
