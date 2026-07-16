#!/usr/bin/env python3
"""Argparse registration for the resource CLI.

Ownership: resource CLI command-surface construction.
Non-goals: resource acquisition, owner-tool execution, network routing, cache
cleanup, or policy decisions.
State behavior: read-only; builds parser objects only.
Caller context: `resource_cli.py build_parser()` facade.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


CommandHandler = Callable[[argparse.Namespace], int]


@dataclass(frozen=True)
class ResourceCliParserConfig:
    bridge_root: Path
    default_cache_dir: Path
    default_log: Path
    default_event_log: Path
    default_receipt_log: Path
    resource_intent_choices: tuple[str, ...]
    resource_stage_choices: tuple[str, ...]
    validation_profiles: tuple[str, ...]
    intent_unknown: str
    intent_explicit_local_file: str
    intent_explicit_user_url: str
    stage_materialize: str
    command_fetch_file: CommandHandler
    command_fetch_url: CommandHandler
    command_materialize_url: CommandHandler
    command_probe_url: CommandHandler
    command_preview_url: CommandHandler
    command_acquire: CommandHandler
    command_verify: CommandHandler
    command_strategy_review: CommandHandler
    command_classify_url: CommandHandler
    command_route: CommandHandler
    command_request: CommandHandler
    command_delegate: CommandHandler
    command_get: CommandHandler
    command_custom: CommandHandler
    command_collect: CommandHandler
    command_job: CommandHandler
    command_request_batch: CommandHandler
    command_batch_status: CommandHandler
    command_progress: CommandHandler
    command_scenario_smoke: CommandHandler
    command_status: CommandHandler
    command_attach_result: CommandHandler
    command_inspect_cache: CommandHandler
    command_clean_cache: CommandHandler
    command_legacy_audit: CommandHandler


def add_common_fetch_args(parser: argparse.ArgumentParser, config: ResourceCliParserConfig) -> None:
    parser.add_argument("--target-dir", default="", help="Resource cache/output directory. User-facing URL downloads default to the desktop Codex resource library.")
    parser.add_argument("--name", default="", help="Stored display filename. Defaults to source basename.")
    parser.add_argument("--max-bytes", default=None, help="Maximum accepted size, e.g. 10MB or 10485760.")
    parser.add_argument("--sha256", default="", help="Expected sha256 digest.")
    parser.add_argument("--source", default="resource_cli", help="Source label for metadata/logs.")


def add_download_backend_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--download-backend",
        choices=("auto", "curl", "aria2"),
        default="",
        help="Optional materialization backend for resumable URL downloads.",
    )
    parser.add_argument(
        "--resume-download",
        action="store_true",
        help="Request resumable download semantics; auto-selects the best available backend when no backend is named.",
    )


def add_package_manager_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--package-ecosystem", default="", help="Package ecosystem, e.g. python, npm, windows_tool, choco, or winget.")
    parser.add_argument("--package-action", default="", help="Package action, e.g. search, plan, audit, or install.")
    parser.add_argument("--windows-package-manager", choices=("", "choco", "winget", "chocolatey"), default="", help="Preferred Windows package manager.")
    parser.add_argument("--package-id", default="", help="Package-manager package id when it differs from target.")
    parser.add_argument("--winget-id", default="", help="Winget package id for exact install/search planning.")
    parser.add_argument("--verify-binary", default="", help="Binary name to verify after an approved install.")
    parser.add_argument("--install-approved", action="store_true", help="Explicitly approve package install side effects for this resource request.")
    parser.add_argument("--accept-winget-agreements", action="store_true", help="Pass winget source/package agreement flags for an approved winget install.")


def add_custom_delegation_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--resource-kind",
        choices=("auto", "academic_paper", "audio", "dataset", "document", "documentation", "generic_download", "generic_web", "github_project", "image", "model_artifact", "package", "video"),
        default="auto",
        help="Codex-authored resource kind hint for source selection.",
    )
    parser.add_argument("--owner-tool", action="append", default=[], help="Preferred owner tool. Repeat or comma-separate values.")
    parser.add_argument("--avoid-owner-tool", action="append", default=[], help="Owner tool to avoid for this request. Repeat or comma-separate values.")
    parser.add_argument("--source-kind", default="", help="Desired source kind, e.g. official_docs, open_media, academic_index, repository.")
    parser.add_argument("--site-or-domain", default="", help="Preferred site or domain constraint.")
    parser.add_argument("--language", default="", help="Language or locale constraint.")
    parser.add_argument("--freshness", default="", help="Freshness constraint, e.g. latest, recent, stable, versioned.")
    parser.add_argument("--authority", default="", help="Authority constraint, e.g. official, primary, peer_reviewed.")
    parser.add_argument("--format", dest="file_format", default="", help="Desired resource format, e.g. pdf, png, markdown, repo.")
    parser.add_argument("--license", dest="license_filter", default="", help="License/access constraint.")
    parser.add_argument("--relevance-threshold", type=float, default=None, help="Minimum relevance threshold for owner results.")
    parser.add_argument("--required-source-count", type=int, default=None, help="Minimum independent source count for the resource result.")
    parser.add_argument("--constraint", action="append", default=[], help="Extra key=value constraint. Repeat as needed.")
    parser.add_argument("--exclude", action="append", default=[], help="Exclusion constraint. Repeat as needed.")
    parser.add_argument("--refine-from", default="", help="Prior resource request id this request refines.")
    parser.add_argument("--refine-reason", default="", help="Reason for this refinement.")
    parser.add_argument("--candidate-review", action="store_true", help="Return source candidates first when materialization lacks a concrete URL/path.")
    parser.add_argument("--quantity", type=int, default=None, help="Requested resource count.")
    parser.add_argument("--minimum-quantity", type=int, default=None, help="Minimum acceptable resource count.")
    parser.add_argument("--maximum-quantity", type=int, default=None, help="Maximum accepted resource count.")
    parser.add_argument("--unique", action="store_true", help="Require distinct resources rather than repeated equivalents.")
    parser.add_argument("--uniqueness-dimension", action="append", default=[], help="Uniqueness dimension such as content, viewpoint, version, or source_url.")
    parser.add_argument("--dedup-key", action="append", default=[], help="Deduplication key such as content_hash or canonical_url.")
    parser.add_argument("--source-mode", choices=("", "single_source", "multi_source", "specified_domains", "official_first"), default="", help="Source coverage policy.")
    parser.add_argument("--source-domain", action="append", default=[], help="Allowed or preferred source domain. Repeat as needed.")
    parser.add_argument("--freshness-mode", default="", help="Structured freshness mode such as latest, recent, stable, or versioned.")
    parser.add_argument("--max-age-days", type=int, default=None, help="Maximum source age in days when freshness is time-bounded.")
    parser.add_argument("--destination-policy", choices=("", "resource_cache", "user_resource_library", "explicit_target_dir"), default="", help="Artifact destination policy.")


def add_structured_request_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--request-json", default="", help="Inline structured_task_envelope.v1 JSON. Do not combine with individual delegation fields.")
    parser.add_argument("--request-file", default="", help="UTF-8 JSON file containing structured_task_envelope.v1. Do not combine with --request-json.")


def add_output_args(
    parser: argparse.ArgumentParser,
    config: ResourceCliParserConfig,
    *,
    suppress_defaults: bool = False,
) -> None:
    default = argparse.SUPPRESS if suppress_defaults else None
    parser.add_argument("--json", action="store_true", default=default, help="Emit JSON.")
    parser.add_argument(
        "--no-log",
        action="store_true",
        default=default,
        help="Do not append to the resource JSONL log.",
    )
    parser.add_argument(
        "--log-path",
        type=Path,
        default=argparse.SUPPRESS if suppress_defaults else config.default_log,
        help="Resource JSONL log path.",
    )


def add_scenario_smoke_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    config: ResourceCliParserConfig,
) -> None:
    scenario_smoke = subparsers.add_parser(
        "scenario-smoke",
        help="Run reusable resource-layer and network-gateway cooperation smoke scenarios.",
    )
    scenario_smoke.add_argument(
        "--mode",
        choices=config.validation_profiles,
        default="quick",
        help="quick is deterministic; smoke/full/live progressively enable real owner/network paths.",
    )
    scenario_smoke.add_argument("--max-active", type=int, default=4, help="Maximum concurrently active requests.")
    scenario_smoke.add_argument("--per-host-limit", type=int, default=1, help="Maximum concurrently active requests per host.")
    scenario_smoke.add_argument("--tmp-root", default=str(config.bridge_root / "tmp"), help="Temporary scenario output root.")
    scenario_smoke.add_argument("--print-payload", action="store_true", help="Print the selected scenario payload without executing it.")
    scenario_smoke.add_argument("--validate-only", action="store_true", help="Validate scenario definitions without executing requests.")
    scenario_smoke.add_argument("--json", action="store_true", help="Emit JSON.")
    scenario_smoke.set_defaults(func=config.command_scenario_smoke)


def add_materialization_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    config: ResourceCliParserConfig,
) -> None:
    fetch_file = subparsers.add_parser("fetch-file", help="Compatibility shortcut for explicit local-file materialization.")
    fetch_file.add_argument("path", help="Local file path.")
    add_common_fetch_args(fetch_file, config)
    fetch_file.add_argument(
        "--intent",
        choices=config.resource_intent_choices,
        default=config.intent_explicit_local_file,
        help="Resource intent policy. Defaults to explicit_local_file for compatibility.",
    )
    fetch_file.add_argument("--purpose", default="", help="Short reason for acquiring this resource.")
    fetch_file.add_argument("--strict", action="store_true", help="Return nonzero for deferred policy decisions.")
    add_output_args(fetch_file, config, suppress_defaults=True)
    fetch_file.set_defaults(func=config.command_fetch_file)

    fetch_url = subparsers.add_parser("fetch-url", help="Compatibility shortcut for explicit user-URL materialization.")
    fetch_url.add_argument("url", help="Resource URL.")
    add_common_fetch_args(fetch_url, config)
    fetch_url.add_argument("--timeout", type=int, default=30, help="Download timeout in seconds.")
    fetch_url.add_argument("--retries", type=int, default=2, help="Retry count after the first attempt.")
    fetch_url.add_argument("--retry-delay", type=float, default=1.0, help="Delay between retries in seconds.")
    fetch_url.add_argument(
        "--intent",
        choices=config.resource_intent_choices,
        default=config.intent_explicit_user_url,
        help="Resource intent policy. Defaults to explicit_user_url for compatibility.",
    )
    fetch_url.add_argument("--purpose", default="", help="Short reason for acquiring this resource.")
    add_download_backend_args(fetch_url)
    fetch_url.add_argument("--strict", action="store_true", help="Return nonzero for deferred policy decisions.")
    add_output_args(fetch_url, config, suppress_defaults=True)
    fetch_url.set_defaults(func=config.command_fetch_url)

    materialize_url = subparsers.add_parser(
        "materialize-url",
        help="Lightweight resource-layer path for an already-resolved explicit URL download.",
    )
    materialize_url.add_argument("url", help="Resource URL.")
    add_common_fetch_args(materialize_url, config)
    materialize_url.add_argument("--task", default="", help="Codex task/purpose that needs this URL artifact.")
    materialize_url.add_argument("--purpose", default="", help="Short reason for acquiring this resource.")
    materialize_url.add_argument("--timeout", type=int, default=30, help="Download timeout in seconds.")
    materialize_url.add_argument("--retries", type=int, default=1, help="Retry count after the first attempt.")
    materialize_url.add_argument("--retry-delay", type=float, default=1.0, help="Delay between retries in seconds.")
    materialize_url.add_argument("--validation-profile", choices=config.validation_profiles, default="quick")
    add_download_backend_args(materialize_url)
    materialize_url.add_argument("--receipt-log", default=str(config.default_receipt_log), help="Resource receipt JSONL path.")
    materialize_url.add_argument("--store-root", default=str(config.default_cache_dir), help="Resource manifest/store root.")
    materialize_url.add_argument("--resource-log", default=str(config.default_log), help="Underlying resource acquisition JSONL path.")
    materialize_url.add_argument("--no-resource-log", action="store_true", help="Do not mirror the resource attempt to resource log.")
    materialize_url.add_argument("--json", action="store_true", help="Emit JSON.")
    materialize_url.set_defaults(func=config.command_materialize_url)

    probe_url = subparsers.add_parser("probe-url", help="Probe a URL without downloading its body.")
    probe_url.add_argument("url", help="Resource URL.")
    add_common_fetch_args(probe_url, config)
    probe_url.add_argument("--timeout", type=int, default=15, help="Probe timeout in seconds.")
    probe_url.add_argument("--purpose", default="", help="Short reason for probing this resource.")
    probe_url.add_argument("--strict", action="store_true", help="Return nonzero for deferred policy decisions.")
    add_output_args(probe_url, config, suppress_defaults=True)
    probe_url.set_defaults(func=config.command_probe_url)

    preview_url = subparsers.add_parser("preview-url", help="Fetch a bounded URL preview without materializing it.")
    preview_url.add_argument("url", help="Resource URL.")
    add_common_fetch_args(preview_url, config)
    preview_url.add_argument("--timeout", type=int, default=15, help="Preview timeout in seconds.")
    preview_url.add_argument("--preview-bytes", type=int, default=8192, help="Maximum response bytes to preview.")
    preview_url.add_argument("--purpose", default="", help="Short reason for previewing this resource.")
    preview_url.add_argument("--strict", action="store_true", help="Return nonzero for deferred policy decisions.")
    add_output_args(preview_url, config, suppress_defaults=True)
    preview_url.set_defaults(func=config.command_preview_url)

    acquire = subparsers.add_parser("acquire", help="Acquire or record a resource through ResourceIntent policy.")
    acquire.add_argument("--intent", choices=config.resource_intent_choices, default=config.intent_unknown, help="Resource intent policy.")
    acquire.add_argument("--stage", choices=config.resource_stage_choices, default=config.stage_materialize, help="Resource acquisition stage.")
    acquire.add_argument("--path", default="", help="Local file path for local-file resources.")
    acquire.add_argument("--url", default="", help="Resource URL for URL-backed resources.")
    acquire.add_argument("--purpose", default="", help="Short reason for acquiring or recording this resource.")
    add_common_fetch_args(acquire, config)
    acquire.add_argument("--timeout", type=int, default=30, help="Download timeout in seconds.")
    acquire.add_argument("--retries", type=int, default=0, help="Retry count after the first attempt; policy defaults apply when zero.")
    acquire.add_argument("--retry-delay", type=float, default=1.0, help="Delay between retries in seconds.")
    add_download_backend_args(acquire)
    acquire.add_argument("--strict", action="store_true", help="Return nonzero for deferred policy decisions.")
    add_output_args(acquire, config, suppress_defaults=True)
    acquire.set_defaults(func=config.command_acquire)

    verify = subparsers.add_parser("verify", help="Verify a local file's size and sha256.")
    verify.add_argument("path", help="Local file path.")
    verify.add_argument("--max-bytes", default=None, help="Maximum accepted size.")
    verify.add_argument("--sha256", default="", help="Expected sha256 digest.")
    verify.add_argument("--source", default="resource_cli", help="Source label for metadata/logs.")
    add_output_args(verify, config, suppress_defaults=True)
    verify.set_defaults(func=config.command_verify)

    strategy_review = subparsers.add_parser("strategy-review", help="Review resource logs and propose safe strategy improvements.")
    strategy_review.add_argument("--resource-log", type=Path, default=config.default_log, help="Resource JSONL log to review.")
    strategy_review.add_argument("--limit", type=int, default=200, help="Most recent log entries to inspect; 0 means all entries.")
    strategy_review.add_argument("--hide-legacy", action="store_true", help="Hide legacy CLI entries when reviewing current policy behavior.")
    strategy_review.add_argument("--json", action="store_true", help="Emit JSON.")
    strategy_review.set_defaults(func=config.command_strategy_review)


def add_get_command(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    config: ResourceCliParserConfig,
) -> None:
    get = subparsers.add_parser("get", help="High-level Codex resource request: run, wait, and return a consumable receipt.")
    get.add_argument("--task", default="", help="Codex task/purpose that needs a resource.")
    get.add_argument("--target", default="", help="Generic resource target; URL targets are auto-detected later by the broker.")
    get.add_argument("--url", default="", help="URL resource.")
    get.add_argument("--path", default="", help="Local file resource.")
    get.add_argument("--name", default="", help="Optional output/display name.")
    get.add_argument("--intent", choices=config.resource_intent_choices, default=config.intent_unknown)
    get.add_argument("--need-materialization", action="store_true", help="Request a durable local artifact.")
    get.add_argument("--download", action="store_true", help="Shortcut for a user-facing URL download/materialization request.")
    get.add_argument("--allow-network", action=argparse.BooleanOptionalAction, default=True)
    get.add_argument("--allow-filesystem-write", action=argparse.BooleanOptionalAction, default=False)
    get.add_argument("--max-bytes", type=int, default=None)
    get.add_argument("--sha256", default="")
    get.add_argument("--timeout", type=int, default=30)
    get.add_argument("--retries", type=int, default=1)
    get.add_argument("--target-dir", default="", help="Resource cache/output directory.")
    get.add_argument("--auto-owner", action=argparse.BooleanOptionalAction, default=True)
    get.add_argument("--owner-execution-mode", default="read_only", choices=("read_only",))
    get.add_argument("--purpose", default="", help="Short reason for the request.")
    get.add_argument("--validation-profile", choices=config.validation_profiles, default="")
    get.add_argument("--fast", action="store_true", help="Use quick validation defaults when no profile is specified.")
    get.add_argument("--runtime", default="generic")
    add_download_backend_args(get)
    add_package_manager_args(get)
    get.add_argument("--event-log", default=str(config.default_event_log), help="Broker event JSONL path.")
    get.add_argument("--receipt-log", default=str(config.default_receipt_log), help="Broker receipt JSONL path.")
    get.add_argument("--store-root", default=str(config.default_cache_dir), help="Resource manifest/store root.")
    get.add_argument("--resource-log", default=str(config.default_log), help="Underlying resource acquisition JSONL path.")
    get.add_argument("--no-resource-log", action="store_true", help="Do not mirror successful resource attempts to resource log.")
    get.add_argument("--read-result", action=argparse.BooleanOptionalAction, default=True, help="Read a bounded text excerpt from the returned resource result.")
    get.add_argument("--content-chars", type=int, default=1200, help="Maximum text-result characters to include.")
    get.add_argument("--json", action="store_true", help="Emit JSON.")
    get.set_defaults(func=config.command_get)


def add_routing_and_request_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    config: ResourceCliParserConfig,
) -> None:
    classify_url = subparsers.add_parser("classify-url", help="Classify URL semantics without fetching or writing files.")
    classify_url.add_argument("url", help="URL to classify.")
    classify_url.add_argument(
        "--context",
        choices=("unknown", "inline_text", "explicit_user", "documentation", "dependency"),
        default="unknown",
        help="Caller context for semantic routing.",
    )
    classify_url.add_argument("--json", action="store_true", help="Emit JSON.")
    classify_url.set_defaults(func=config.command_classify_url)

    route = subparsers.add_parser("route", help="Plan resource routing across MCPs and resource materialization without side effects.")
    route.add_argument("--target", default="", help="Generic resource target for owner-tool routing, such as a library or package name.")
    route.add_argument("--url", default="", help="URL resource to route.")
    route.add_argument("--path", default="", help="Local file resource to route.")
    route.add_argument("--intent", choices=config.resource_intent_choices, default=config.intent_unknown, help="Declared resource intent.")
    route.add_argument("--need-materialization", action="store_true", help="Whether a durable local artifact is required.")
    route.add_argument("--task", default="", help="Short task description for route hints.")
    route.add_argument("--name", default="", help="Optional output/display name.")
    route.add_argument("--json", action="store_true", help="Emit JSON.")
    route.set_defaults(func=config.command_route)

    request = subparsers.add_parser("request", help="Submit a resource request through the broker.")
    request.add_argument("--json-payload", default="", help="Inline JSON resource request.")
    request.add_argument("--payload-file", default="", help="JSON resource request file.")
    request.add_argument("--target", default="", help="Generic resource target; URL targets are auto-detected.")
    request.add_argument("--url", default="", help="URL resource.")
    request.add_argument("--path", default="", help="Local file resource.")
    request.add_argument("--task", default="", help="Task/purpose hint for routing.")
    request.add_argument("--name", default="", help="Optional output/display name.")
    request.add_argument("--intent", choices=config.resource_intent_choices, default=config.intent_unknown)
    request.add_argument("--need-materialization", action="store_true", help="Request a durable local artifact.")
    request.add_argument("--allow-network", action=argparse.BooleanOptionalAction, default=True)
    request.add_argument("--allow-filesystem-write", action=argparse.BooleanOptionalAction, default=False)
    request.add_argument("--target-dir", default=str(config.default_cache_dir), help="Resource cache/output directory.")
    request.add_argument("--max-bytes", default=None, help="Maximum accepted size.")
    request.add_argument("--sha256", default="", help="Expected sha256 digest.")
    request.add_argument("--timeout", type=int, default=30, help="Request timeout in seconds.")
    request.add_argument("--retries", type=int, default=1, help="Retry budget for executable resource_cli attempts.")
    request.add_argument("--auto-owner", action="store_true", help="Run supported read-only owner executors before handoff.")
    request.add_argument("--owner-execution-mode", default="read_only", choices=("read_only",), help="Owner executor risk mode.")
    request.add_argument("--purpose", default="", help="Short reason for the request.")
    request.add_argument("--validation-profile", choices=config.validation_profiles, default="", help="Optional resource validation profile.")
    add_download_backend_args(request)
    add_package_manager_args(request)
    request.add_argument("--event-log", default=str(config.default_event_log), help="Broker event JSONL path.")
    request.add_argument("--receipt-log", default=str(config.default_receipt_log), help="Broker receipt JSONL path.")
    request.add_argument("--store-root", default=str(config.default_cache_dir), help="Resource manifest/store root.")
    request.add_argument("--resource-log", default=str(config.default_log), help="Underlying resource acquisition JSONL path.")
    request.add_argument("--no-resource-log", action="store_true", help="Do not mirror successful resource attempts to resource log.")
    request.add_argument("--json", action="store_true", help="Emit JSON.")
    request.set_defaults(func=config.command_request)

    delegate = subparsers.add_parser("delegate", help="Build a Codex-authored machine-readable resource request payload.")
    delegate.add_argument("--task", default="", help="Codex task/purpose that needs a resource. Required unless --request-json/--request-file is used.")
    delegate.add_argument("--target", default="", help="Generic resource target; URL targets are auto-detected later by the broker.")
    delegate.add_argument("--url", default="", help="URL resource.")
    delegate.add_argument("--path", default="", help="Local file resource.")
    delegate.add_argument("--name", default="", help="Optional output/display name.")
    delegate.add_argument("--intent", choices=config.resource_intent_choices, default=config.intent_unknown)
    delegate.add_argument("--need-materialization", action="store_true", help="Request a durable local artifact.")
    delegate.add_argument("--allow-network", action=argparse.BooleanOptionalAction, default=True)
    delegate.add_argument("--allow-filesystem-write", action=argparse.BooleanOptionalAction, default=False)
    delegate.add_argument("--max-bytes", type=int, default=None)
    delegate.add_argument("--sha256", default="")
    delegate.add_argument("--timeout", type=int, default=30)
    delegate.add_argument("--retries", type=int, default=1)
    delegate.add_argument("--target-dir", default="", help="Resource cache/output directory.")
    delegate.add_argument("--auto-owner", action=argparse.BooleanOptionalAction, default=True)
    delegate.add_argument("--owner-execution-mode", default="read_only", choices=("read_only",))
    delegate.add_argument("--purpose", default="", help="Short reason for the request.")
    delegate.add_argument("--validation-profile", choices=config.validation_profiles, default="")
    delegate.add_argument("--runtime", default="generic")
    add_download_backend_args(delegate)
    add_package_manager_args(delegate)
    add_custom_delegation_args(delegate)
    add_structured_request_args(delegate)
    delegate.add_argument("--submit", action="store_true", help="Submit the generated request to the resource broker and return its receipt.")
    delegate.add_argument("--event-log", default=str(config.default_event_log), help="Broker event JSONL path when --submit is used.")
    delegate.add_argument("--receipt-log", default=str(config.default_receipt_log), help="Broker receipt JSONL path when --submit is used.")
    delegate.add_argument("--store-root", default=str(config.default_cache_dir), help="Resource manifest/store root when --submit is used.")
    delegate.add_argument("--resource-log", default=str(config.default_log), help="Underlying resource acquisition JSONL path when --submit is used.")
    delegate.add_argument("--no-resource-log", action="store_true", help="Do not mirror successful resource attempts to resource log when --submit is used.")
    delegate.add_argument("--json", action="store_true", help="Emit JSON.")
    delegate.set_defaults(func=config.command_delegate)

    add_get_command(subparsers, config)

    collect = subparsers.add_parser(
        "collect",
        help="Discover candidates and materialize enough resources to satisfy a counted request.",
    )
    collect.add_argument("--task", required=True, help="Codex task/purpose that needs multiple resources.")
    collect.add_argument("--target", default="", help="Search/source target, such as Huawei headquarters photos.")
    collect.add_argument("--count", type=int, default=1, help="Required successful artifact count.")
    collect.add_argument(
        "--resource-kind",
        choices=("auto", "academic_paper", "audio", "dataset", "document", "generic_download", "github_project", "image", "model_artifact", "video"),
        default="auto",
        help="Resource kind to collect; auto lets the source strategy infer from task and target.",
    )
    collect.add_argument("--source-page", default="", help="Optional source page whose media assets should be inspected first.")
    collect.add_argument("--target-dir", default="", help="Output directory; defaults to the desktop Codex resource library.")
    collect.add_argument("--candidate-limit", type=int, default=24, help="Maximum source candidates to consider.")
    collect.add_argument("--batch-size", type=int, default=6, help="Candidate materialization window size.")
    collect.add_argument("--max-active", type=int, default=4, help="Maximum concurrently active materializations.")
    collect.add_argument("--per-host-limit", type=int, default=2, help="Maximum concurrently active requests per host.")
    collect.add_argument("--timeout", type=int, default=30, help="Per-request timeout in seconds.")
    collect.add_argument("--retries", type=int, default=1, help="Retry budget per candidate.")
    collect.add_argument("--max-bytes", type=int, default=None, help="Maximum accepted bytes per artifact.")
    add_download_backend_args(collect)
    collect.add_argument("--event-log", default=str(config.default_event_log), help="Broker event JSONL path.")
    collect.add_argument("--receipt-log", default=str(config.default_receipt_log), help="Broker receipt JSONL path.")
    collect.add_argument("--store-root", default=str(config.default_cache_dir), help="Resource manifest/store root.")
    collect.add_argument("--resource-log", default=str(config.default_log), help="Underlying resource acquisition JSONL path.")
    collect.add_argument("--no-resource-log", action="store_true", help="Do not mirror successful resource attempts to resource log.")
    collect.add_argument("--json", action="store_true", help="Emit JSON.")
    collect.set_defaults(func=config.command_collect)

    job = subparsers.add_parser("job", help="Submit and inspect resource requests as resumable jobs.")
    job_sub = job.add_subparsers(dest="job_command", required=True)

    def add_job_request_args(job_parser: argparse.ArgumentParser) -> None:
        job_parser.add_argument("--task", default="", help="Codex task/purpose that needs a resource. Required unless --request-json/--request-file is used.")
        job_parser.add_argument("--target", default="", help="Generic resource target; URL targets are auto-detected later by the broker.")
        job_parser.add_argument("--url", default="", help="URL resource.")
        job_parser.add_argument("--path", default="", help="Local file resource.")
        job_parser.add_argument("--name", default="", help="Optional output/display name.")
        job_parser.add_argument("--intent", choices=config.resource_intent_choices, default=config.intent_unknown)
        job_parser.add_argument("--need-materialization", action="store_true", help="Request a durable local artifact.")
        job_parser.add_argument("--allow-network", action=argparse.BooleanOptionalAction, default=True)
        job_parser.add_argument("--allow-filesystem-write", action=argparse.BooleanOptionalAction, default=False)
        job_parser.add_argument("--max-bytes", type=int, default=None)
        job_parser.add_argument("--sha256", default="")
        job_parser.add_argument("--timeout", type=int, default=30)
        job_parser.add_argument("--retries", type=int, default=1)
        job_parser.add_argument("--target-dir", default="", help="Resource cache/output directory.")
        job_parser.add_argument("--auto-owner", action=argparse.BooleanOptionalAction, default=True)
        job_parser.add_argument("--owner-execution-mode", default="read_only", choices=("read_only",))
        job_parser.add_argument("--purpose", default="", help="Short reason for the request.")
        job_parser.add_argument("--validation-profile", choices=config.validation_profiles, default="")
        job_parser.add_argument("--runtime", default="generic")
        add_download_backend_args(job_parser)
        add_package_manager_args(job_parser)
        add_custom_delegation_args(job_parser)
        add_structured_request_args(job_parser)

    def add_job_execution_args(job_parser: argparse.ArgumentParser) -> None:
        job_parser.add_argument("--event-log", default=str(config.default_event_log), help="Broker event JSONL path.")
        job_parser.add_argument("--receipt-log", default=str(config.default_receipt_log), help="Broker receipt JSONL path.")
        job_parser.add_argument("--store-root", default=str(config.default_cache_dir), help="Resource manifest/store root.")
        job_parser.add_argument("--resource-log", default=str(config.default_log), help="Underlying resource acquisition JSONL path.")
        job_parser.add_argument("--no-resource-log", action="store_true", help="Do not mirror successful resource attempts to resource log.")
        job_parser.add_argument("--receipt-detail", choices=("compact", "full"), default="full", help="Receipt detail returned by foreground job commands.")
        job_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    job_submit = job_sub.add_parser("submit", help="Submit a Codex-authored resource job and return a request id.")
    add_job_request_args(job_submit)
    job_submit.add_argument("--foreground", action="store_true", help="Run immediately and return the terminal receipt instead of starting a background worker.")
    add_job_execution_args(job_submit)
    job_submit.set_defaults(func=config.command_job)

    job_run = job_sub.add_parser("run", help="Run a Codex-authored resource job and block until the terminal receipt is returned.")
    add_job_request_args(job_run)
    add_job_execution_args(job_run)
    job_run.set_defaults(func=config.command_job)

    custom = subparsers.add_parser(
        "custom",
        help="High-frequency Codex resource delegation: run a custom request, wait, and return guidance/receipt.",
    )
    add_job_request_args(custom)
    add_job_execution_args(custom)
    custom.add_argument("--mode", choices=("run", "submit"), default="run", help="run blocks for a terminal receipt; submit starts a background job.")
    custom.set_defaults(func=config.command_custom)

    for name, help_text in (
        ("status", "Read a resource job receipt by request id."),
        ("receipt", "Read a resource job terminal receipt by request id."),
        ("progress", "Read a compact progress view for a resource job."),
        ("wait", "Poll a resource job until a resource-layer terminal receipt exists or timeout expires."),
    ):
        job_cmd = job_sub.add_parser(name, help=help_text)
        job_cmd.add_argument("--request-id", required=True)
        job_cmd.add_argument("--receipt-log", default=str(config.default_receipt_log), help="Broker receipt JSONL path.")
        if name == "wait":
            job_cmd.add_argument("--timeout", type=float, default=30.0, help="Maximum seconds to wait.")
            job_cmd.add_argument("--interval", type=float, default=1.0, help="Polling interval in seconds.")
        job_cmd.add_argument("--json", action="store_true", help="Emit JSON.")
        job_cmd.set_defaults(func=config.command_job)

    job_attach = job_sub.add_parser("attach", help="Attach an owner-tool result to an existing resource job.")
    job_attach.add_argument("--request-id", required=True)
    job_attach.add_argument("--source-tool", required=True, help="Owner tool that produced the result, such as context7, github, playwright, or markitdown.")
    job_attach.add_argument("--result-kind", default="owner_result", help="Result kind, such as docs, markdown, metadata, artifact, or browser_evidence.")
    job_attach.add_argument("--content", default="", help="Inline textual owner result.")
    job_attach.add_argument("--content-file", default="", help="Text file containing the owner result.")
    job_attach.add_argument("--artifact-path", default="", help="Optional artifact path produced by the owner tool.")
    job_attach.add_argument("--metadata-json", default="", help="Optional JSON metadata for the owner result.")
    job_attach.add_argument("--receipt-log", default=str(config.default_receipt_log), help="Broker receipt JSONL path.")
    job_attach.add_argument("--json", action="store_true", help="Emit JSON.")
    job_attach.set_defaults(func=config.command_job)

    job_consume = job_sub.add_parser("consume", help="Record that Codex consumed or evaluated a completed resource result.")
    job_consume.add_argument("--request-id", required=True)
    job_consume.add_argument("--consumed-path", default="", help="Owned manifest, preview, artifact, or owner-result path that Codex read or evaluated.")
    job_consume.add_argument("--no-read-needed-reason", default="", help="Explicit reason no result file needed to be read.")
    job_consume.add_argument("--consumer", default="codex", help="Consumer identity recorded in the request manifest.")
    job_consume.add_argument("--receipt-log", default=str(config.default_receipt_log), help="Broker receipt JSONL path.")
    job_consume.add_argument("--json", action="store_true", help="Emit JSON.")
    job_consume.set_defaults(func=config.command_job)

    request_batch = subparsers.add_parser("request-batch", help="Submit a bounded batch of resource requests through the scheduler.")
    request_batch.add_argument("--payload-file", required=True, help="JSON list or object with requests array.")
    request_batch.add_argument("--plan-only", action="store_true", help="Plan the batch without executing requests.")
    request_batch.add_argument("--max-active", type=int, default=6, help="Maximum concurrently active requests.")
    request_batch.add_argument("--per-host-limit", type=int, default=2, help="Maximum concurrently active requests per host.")
    request_batch.add_argument("--total-timeout-seconds", type=float, default=0.0, help="Optional total wall-clock budget for the whole batch; zero leaves only per-request budgets active.")
    request_batch.add_argument("--event-log", default=str(config.default_event_log), help="Broker event JSONL path.")
    request_batch.add_argument("--receipt-log", default=str(config.default_receipt_log), help="Broker receipt JSONL path.")
    request_batch.add_argument("--store-root", default=str(config.default_cache_dir), help="Resource manifest/store root.")
    request_batch.add_argument("--resource-log", default=str(config.default_log), help="Underlying resource acquisition JSONL path.")
    request_batch.add_argument("--no-resource-log", action="store_true", help="Do not mirror successful resource attempts to resource log.")
    request_batch.add_argument("--validation-profile", choices=config.validation_profiles, default="", help="Apply a validation profile to all batch requests.")
    request_batch.add_argument("--detail", choices=("compact", "full"), default="compact", help="Receipt detail printed to stdout; full data is always preserved in the batch manifest.")
    request_batch.add_argument("--json", action="store_true", help="Emit JSON.")
    request_batch.set_defaults(func=config.command_request_batch)


def add_progress_and_result_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    config: ResourceCliParserConfig,
) -> None:
    batch_status = subparsers.add_parser("batch-status", help="Read a resource batch manifest summary.")
    batch_status.add_argument("--manifest-path", required=True, help="Batch manifest path produced by request-batch.")
    batch_status.add_argument("--json", action="store_true", help="Emit JSON.")
    batch_status.set_defaults(func=config.command_batch_status)

    progress = subparsers.add_parser("progress", help="Read a compact conversation-oriented resource progress view.")
    progress.add_argument("--request-id", default="", help="Resource request id.")
    progress.add_argument("--manifest-path", default="", help="Request manifest path.")
    progress.add_argument("--batch-manifest-path", default="", help="Batch manifest path.")
    progress.add_argument("--include-items", action="store_true", help="Include bounded batch item summaries.")
    progress.add_argument("--limit", type=int, default=20, help="Maximum item summaries for batch progress.")
    progress.add_argument("--receipt-log", default=str(config.default_receipt_log), help="Broker receipt JSONL path.")
    progress.add_argument("--json", action="store_true", help="Emit JSON.")
    progress.set_defaults(func=config.command_progress)

    add_scenario_smoke_parser(subparsers, config)

    status = subparsers.add_parser("status", help="Read a resource broker receipt by request id.")
    status.add_argument("--request-id", required=True)
    status.add_argument("--receipt-log", default=str(config.default_receipt_log), help="Broker receipt JSONL path.")
    status.add_argument("--json", action="store_true", help="Emit JSON.")
    status.set_defaults(func=config.command_status)

    attach_result = subparsers.add_parser("attach-result", help="Attach an owner-tool result to an existing resource request.")
    attach_result.add_argument("--request-id", required=True)
    attach_result.add_argument("--source-tool", required=True, help="Owner tool that produced the result, such as context7, github, playwright, or markitdown.")
    attach_result.add_argument("--result-kind", default="owner_result", help="Result kind, such as docs, markdown, metadata, artifact, or browser_evidence.")
    attach_result.add_argument("--content", default="", help="Inline textual owner result.")
    attach_result.add_argument("--content-file", default="", help="Text file containing the owner result.")
    attach_result.add_argument("--artifact-path", default="", help="Optional artifact path produced by the owner tool.")
    attach_result.add_argument("--metadata-json", default="", help="Optional JSON metadata for the owner result.")
    attach_result.add_argument("--receipt-log", default=str(config.default_receipt_log), help="Broker receipt JSONL path.")
    attach_result.add_argument("--json", action="store_true", help="Emit JSON.")
    attach_result.set_defaults(func=config.command_attach_result)

    inspect_cache = subparsers.add_parser("inspect-cache", help="List cache contents.")
    inspect_cache.add_argument("--target-dir", default=str(config.default_cache_dir), help="Resource cache directory.")
    inspect_cache.add_argument("--limit", type=int, default=50, help="Printed row limit for text output.")
    add_output_args(inspect_cache, config, suppress_defaults=True)
    inspect_cache.set_defaults(func=config.command_inspect_cache)

    clean_cache = subparsers.add_parser("clean-cache", help="Remove old cache files.")
    clean_cache.add_argument("--target-dir", default=str(config.default_cache_dir), help="Resource cache directory.")
    clean_cache.add_argument("--older-than-days", type=float, default=30.0, help="Remove files older than this many days.")
    clean_cache.add_argument("--transient-only", action="store_true", help="Limit cleanup to incomplete download/temp suffixes.")
    clean_cache.add_argument("--limit", type=int, default=100, help="Maximum candidate rows returned in JSON output.")
    clean_cache.add_argument("--dry-run", action="store_true", help="Show candidates without deleting.")
    add_output_args(clean_cache, config, suppress_defaults=True)
    clean_cache.set_defaults(func=config.command_clean_cache)

    legacy_audit = subparsers.add_parser("legacy-audit", help="Read-only audit of legacy resource-layer entry points.")
    legacy_audit.add_argument("--validate-only", action="store_true", help="Validate the legacy-audit contract.")
    legacy_audit.add_argument("--json", action="store_true", help="Emit JSON.")
    legacy_audit.set_defaults(func=config.command_legacy_audit)


def build_resource_parser(config: ResourceCliParserConfig) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Acquire, verify, route, and inspect workspace resources.")
    add_output_args(parser, config)
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_materialization_commands(subparsers, config)
    add_routing_and_request_commands(subparsers, config)
    add_progress_and_result_commands(subparsers, config)
    return parser
