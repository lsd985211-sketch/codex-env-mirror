#!/usr/bin/env python3
"""Pure routing rules for resource acquisition requests.

Ownership: read-only resource route classification and route-plan assembly.
Non-goals: fetching resources, calling MCP tools, installing packages, writing
cache files, or changing network state.
State behavior: read-only and deterministic from caller-supplied fields.
Caller context: `resource_router.route_resource` facade and resource broker.
"""

from __future__ import annotations

import urllib.parse
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from intent_routing import matched_terms, term_matches
from resource_fetcher import ResourceIntent, ResourceStage


MCP_TOOL_HINTS = {
    "context7": "Use for current library, framework, SDK, API, CLI, and cloud-service documentation.",
    "microsoftdocs": "Use for Microsoft Learn, Windows, PowerShell, Azure, .NET, and VS Code documentation.",
    "github": "Use for GitHub repository, issue, PR, action, release, and account metadata.",
    "markitdown": "Use for converting supported file/http/data resources to Markdown when durable cache metadata is not required.",
    "playwright": "Use for controlled webpage automation, screenshots, rendered DOM evidence, and E2E-style checks.",
    "chrome-devtools": "Use for inspecting or controlling an existing Chrome/CDP page, including console and network evidence.",
    "package_manager": "Use for package/dependency acquisition through the owning package manager with source, version, scope, and install-risk evidence.",
}

ARTIFACT_EXTENSIONS = {
    ".zip",
    ".jar",
    ".tar",
    ".gz",
    ".tgz",
    ".7z",
    ".exe",
    ".msi",
    ".whl",
    ".gem",
    ".nupkg",
}

DOCUMENT_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".csv",
    ".md",
    ".txt",
    ".html",
    ".htm",
}


@dataclass(frozen=True)
class ResourceRoute:
    ok: bool
    read_only: bool
    source_kind: str
    intent: str
    need_materialization: bool
    primary_tool: str
    secondary_tools: tuple[str, ...]
    recommended_stage: str
    resource_cli_command: str
    reasons: tuple[str, ...]
    risk_flags: tuple[str, ...]
    safety_boundaries: tuple[str, ...]
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RouteDecision:
    primary: str
    intent: str
    stage: str
    secondary: list[str]
    reasons: list[str]
    risks: list[str]
    notes: list[str]


def _extension_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return Path(parsed.path.lower()).suffix


def _host(url: str) -> str:
    return urllib.parse.urlparse(url).netloc.lower()


def _path(url: str) -> str:
    return urllib.parse.urlparse(url).path.lower()


def _wants_markdown_conversion(task_text: str) -> bool:
    markdown_terms = (
        "markdown",
        "markitdown",
        "to md",
        "to markdown",
        "html to markdown",
        "convert to markdown",
        "convert html",
        "convert page",
        "转换为 markdown",
        "转换成 markdown",
        "转 markdown",
        "转md",
        "网页转md",
        "转成md",
    )
    return bool(matched_terms(task_text, markdown_terms))


def _wants_chrome_devtools(task_text: str) -> bool:
    terms = (
        "chrome-devtools",
        "chrome devtools",
        "existing chrome",
        "current chrome",
        "inspect chrome",
        "devtools page",
        "cdp",
        "现有 chrome",
        "当前 chrome",
    )
    return bool(matched_terms(task_text, terms))


def _has_academic_paper_signal(task_text: str) -> bool:
    substring_terms = (
        "arxiv",
        "doi",
        "学术",
        "开放获取",
        "期刊",
        "会议",
        "论文",
    )
    word_terms = ("academic", "conference", "journal", "paper", "proceedings", "scholar")
    phrase_terms = ("open access",)
    return bool(matched_terms(task_text, substring_terms + phrase_terms + word_terms))


def _has_image_signal(task_text: str) -> bool:
    substring_terms = (
        "图片",
        "照片",
        "图像",
        "配图",
        "截图",
        "壁纸",
        "海报",
    )
    word_terms = ("image", "images", "photo", "photos", "picture", "pictures", "wallpaper", "screenshot", "poster")
    return bool(matched_terms(task_text, substring_terms + word_terms))


def _has_dataset_signal(task_text: str) -> bool:
    substring_terms = (
        "数据集",
        "训练数据",
        "样本数据",
        "模型数据",
    )
    phrase_terms = ("data set", "training data", "sample data")
    word_terms = ("dataset", "datasets", "data", "csv", "parquet", "jsonl")
    return bool(matched_terms(task_text, substring_terms + phrase_terms + word_terms))


def _has_broad_research_signal(task_text: str) -> bool:
    substring_terms = (
        "相关知识",
        "成熟知识",
        "成熟做法",
        "成熟方案",
        "成熟项目",
        "多源",
        "多个来源",
        "多种",
        "对比",
        "比较",
        "综述",
        "汇总",
        "综合",
        "完善计划",
        "完善方案",
        "辅助设计",
    )
    phrase_terms = (
        "best practice",
        "best practices",
        "multi source",
        "multi-source",
        "mature project",
        "mature projects",
    )
    word_terms = ("comparison", "compare", "alternatives", "survey", "overview", "projects", "mature")
    return bool(matched_terms(task_text, substring_terms + phrase_terms + word_terms))


def _resource_cli_command(*, url: str = "", path: str = "", intent: str, stage: str, name: str = "") -> str:
    parts = [
        "python",
        r"_bridge\resource_cli.py",
        "acquire",
        "--intent",
        intent,
        "--stage",
        stage,
    ]
    if url:
        parts += ["--url", f'"{url}"']
    if path:
        parts += ["--path", f'"{path}"']
    if name:
        parts += ["--name", f'"{name}"']
    parts += ["--json"]
    return " ".join(parts)


def _ambiguous_route(*, intent: str, need_materialization: bool) -> ResourceRoute:
    return ResourceRoute(
        ok=False,
        read_only=True,
        source_kind="ambiguous",
        intent=intent,
        need_materialization=need_materialization,
        primary_tool="none",
        secondary_tools=(),
        recommended_stage=ResourceStage.DISCOVER,
        resource_cli_command="",
        reasons=("choose either url or path, not both",),
        risk_flags=("ambiguous_reference",),
        safety_boundaries=("no_network_fetch", "no_filesystem_write", "no_tool_execution"),
        notes=("Split the request into one route decision per resource.",),
    )


def _route_local_file(*, path: str, task_text: str, intent: str, need_materialization: bool) -> RouteDecision:
    extension = Path(path).suffix.lower()
    secondary: list[str] = []
    notes: list[str] = []
    risks: list[str] = []
    if _wants_markdown_conversion(task_text) or "convert" in task_text or extension in {".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm"}:
        primary = "markitdown"
        secondary.append("resource_cli")
        reasons = ["local document conversion is better handled by markitdown first"]
    else:
        primary = "resource_cli" if need_materialization else "local_parser"
        reasons = ["local file can be verified or analyzed without network access"]
    resolved_intent = intent if intent != ResourceIntent.UNKNOWN else ResourceIntent.EXPLICIT_LOCAL_FILE
    stage = ResourceStage.MATERIALIZE if need_materialization else ResourceStage.AUDIT
    return RouteDecision(primary, resolved_intent, stage, secondary, reasons, risks, notes)


def _route_microsoft_docs_url(*, intent: str) -> RouteDecision:
    return RouteDecision(
        primary="microsoftdocs",
        intent=ResourceIntent.DOCUMENTATION_LOOKUP,
        stage=ResourceStage.PROBE,
        secondary=["context7"],
        reasons=["Microsoft documentation should use Microsoft Docs MCP before URL materialization"],
        risks=[],
        notes=[],
    )


def _route_documentation_url() -> RouteDecision:
    return RouteDecision(
        primary="context7",
        intent=ResourceIntent.DOCUMENTATION_LOOKUP,
        stage=ResourceStage.PROBE,
        secondary=["resource_cli"],
        reasons=["documentation lookup should prefer dedicated docs MCP"],
        risks=[],
        notes=[],
    )


def _route_github_url(*, path_part: str, extension: str) -> RouteDecision:
    if "/releases/" in path_part or "/archive/" in path_part or extension in ARTIFACT_EXTENSIONS:
        return RouteDecision(
            primary="github",
            intent=ResourceIntent.EXTERNAL_DEPENDENCY,
            stage=ResourceStage.PROBE,
            secondary=["resource_cli"],
            reasons=["GitHub artifacts need metadata/readback before materialization"],
            risks=["external_dependency"],
            notes=[],
        )
    intent = ResourceIntent.DOCUMENTATION_LOOKUP if "readme" in path_part else ResourceIntent.EXTERNAL_DEPENDENCY
    return RouteDecision(
        primary="github",
        intent=intent,
        stage=ResourceStage.DISCOVER,
        secondary=["resource_cli"],
        reasons=["GitHub metadata should use GitHub MCP before raw URL fetch"],
        risks=[],
        notes=[],
    )


def _route_browser_url(*, task_text: str, need_materialization: bool) -> RouteDecision:
    if _wants_chrome_devtools(task_text):
        return RouteDecision(
            primary="chrome-devtools",
            intent=ResourceIntent.TOOL_OUTPUT,
            stage=ResourceStage.MATERIALIZE if need_materialization else ResourceStage.PREVIEW,
            secondary=["playwright"],
            reasons=["existing Chrome/CDP inspection should use chrome-devtools before browser fallback"],
            risks=[],
            notes=[],
        )
    return RouteDecision(
        primary="playwright",
        intent=ResourceIntent.TOOL_OUTPUT,
        stage=ResourceStage.MATERIALIZE if need_materialization else ResourceStage.PREVIEW,
        secondary=["chrome-devtools"],
        reasons=["rendered page evidence needs browser automation"],
        risks=[],
        notes=[],
    )


def _route_url(*, url: str, task_text: str, intent: str, need_materialization: bool) -> RouteDecision:
    host = _host(url)
    path_part = _path(url)
    extension = _extension_from_url(url)
    if "learn.microsoft.com" in host or "microsoft" in task_text and "docs" in task_text:
        return _route_microsoft_docs_url(intent=intent)
    if "docs" in host or "/docs/" in path_part or "/documentation/" in path_part or "documentation" in task_text:
        return _route_documentation_url()
    if "github.com" in host or "api.github.com" in host:
        return _route_github_url(path_part=path_part, extension=extension)
    if _wants_chrome_devtools(task_text) or matched_terms(task_text, ("screenshot", "rendered", "render page", "console", "browser network", "devtools")):
        return _route_browser_url(task_text=task_text, need_materialization=need_materialization)
    if extension in ARTIFACT_EXTENSIONS:
        return RouteDecision(
            primary="resource_cli",
            intent=ResourceIntent.EXPLICIT_USER_URL if need_materialization else ResourceIntent.EXTERNAL_DEPENDENCY,
            stage=ResourceStage.MATERIALIZE if need_materialization else ResourceStage.PROBE,
            secondary=[],
            reasons=[f"artifact extension {extension} requires probe before materialization"],
            risks=["network_artifact"],
            notes=[],
        )
    if _wants_markdown_conversion(task_text) or (extension in DOCUMENT_EXTENSIONS and "convert" in task_text):
        return RouteDecision(
            primary="markitdown",
            intent=ResourceIntent.DOCUMENTATION_LOOKUP,
            stage=ResourceStage.PREVIEW,
            secondary=["resource_cli"],
            reasons=["document or page conversion should use markitdown before storing"],
            risks=[],
            notes=[],
        )
    resolved_intent = intent
    if resolved_intent == ResourceIntent.UNKNOWN:
        resolved_intent = ResourceIntent.EXPLICIT_USER_URL if need_materialization else ResourceIntent.INLINE_URL_CANDIDATE
    risks = [] if need_materialization else ["implicit_resource"]
    return RouteDecision(
        primary="resource_cli",
        intent=resolved_intent,
        stage=ResourceStage.MATERIALIZE if need_materialization else ResourceStage.PREVIEW,
        secondary=[],
        reasons=["generic URL defaults to non-materializing preview unless user explicitly asks to save it"],
        risks=risks,
        notes=[],
    )


def _route_unknown_source(*, task_text: str, intent: str) -> RouteDecision:
    dependency_terms = ("install", "package", "dependency", "pip", "npm", "pnpm", "uv", "uvx", "winget", "choco")
    docs_terms = ("docs", "documentation", "api", "sdk", "framework", "library", "文档", "接口", "库", "框架")
    microsoft_terms = (
        "microsoft",
        "windows",
        "powershell",
        "azure",
        "defender",
        "office",
        "visual studio",
        "vs code",
        ".net",
    )
    github_terms = ("github", "github.com", "repo", "repository", "issue", "pull request", "actions", "仓库")
    browser_terms = ("browser", "chrome", "playwright", "screenshot", "dom", "rendered", "浏览器", "页面", "截图")

    official_vendor_terms = (
        "official",
        "site:",
        "openai",
        "openai.com",
        "chatgpt",
        "codex",
        "platform.openai.com",
        "help.openai.com",
        "官方",
    )
    version_lookup_terms = (
        "release notes",
        "latest version",
        "version",
        "model availability",
        "model selector",
        "更新",
        "版本",
        "发布说明",
    )
    non_github_domain_hint = bool(re.search(r"(?<![a-z0-9-])(?:[a-z0-9-]+\.)+[a-z]{2,}(?![a-z0-9-])", task_text)) and not bool(
        matched_terms(task_text, ("github.com", "api.github.com"))
    )
    structured_official_hint = bool(matched_terms(task_text, ("resource_kind:generic_web", "source_kind:official_docs", "source_kind:official_web")))
    github_strong = (
        bool(matched_terms(task_text, github_terms)) or term_matches(task_text, "github release")
    ) and not (non_github_domain_hint and structured_official_hint)
    official_web_lookup = (structured_official_hint or non_github_domain_hint or bool(matched_terms(task_text, official_vendor_terms))) and (
        bool(matched_terms(task_text, version_lookup_terms))
        or intent in {ResourceIntent.EXTERNAL_DEPENDENCY, ResourceIntent.DOCUMENTATION_LOOKUP}
    )
    if official_web_lookup and not github_strong:
        return RouteDecision(
            primary="resource_router",
            intent=ResourceIntent.EXTERNAL_DEPENDENCY if intent == ResourceIntent.UNKNOWN else intent,
            stage=ResourceStage.DISCOVER,
            secondary=["resource_cli", "context7"],
            reasons=["official vendor/version lookup should use source selection instead of GitHub release routing"],
            risks=["official_source_selection"],
            notes=["Generic release notes are not GitHub releases unless GitHub is explicitly requested."],
        )
    if github_strong:
        return RouteDecision(
            primary="github",
            intent=ResourceIntent.EXTERNAL_DEPENDENCY if intent == ResourceIntent.UNKNOWN else intent,
            stage=ResourceStage.DISCOVER,
            secondary=["resource_cli"],
            reasons=["GitHub target without a URL should use GitHub MCP for repository/search metadata before generic web search"],
            risks=[],
            notes=[],
        )
    if intent == ResourceIntent.DOCUMENTATION_LOOKUP and matched_terms(task_text, microsoft_terms):
        return RouteDecision(
            primary="microsoftdocs",
            intent=ResourceIntent.DOCUMENTATION_LOOKUP,
            stage=ResourceStage.PROBE,
            secondary=["context7"],
            reasons=["Microsoft/Windows documentation target should use Microsoft Docs MCP before generic docs routing"],
            risks=[],
            notes=[],
        )
    if matched_terms(task_text, browser_terms):
        return _route_browser_url(task_text=task_text, need_materialization=False)
    if intent == ResourceIntent.DOCUMENTATION_LOOKUP or matched_terms(task_text, docs_terms):
        return RouteDecision(
            primary="context7",
            intent=ResourceIntent.DOCUMENTATION_LOOKUP,
            stage=ResourceStage.PROBE,
            secondary=[],
            reasons=["documentation target without a URL should resolve through Context7 before generic resource handling"],
            risks=[],
            notes=[],
        )
    if intent == ResourceIntent.PACKAGE_DEPENDENCY or matched_terms(task_text, dependency_terms):
        return RouteDecision(
            primary="package_manager",
            intent=ResourceIntent.PACKAGE_DEPENDENCY,
            stage=ResourceStage.AUDIT,
            secondary=[],
            reasons=["package/dependency acquisition needs package-manager ownership and install-risk evidence"],
            risks=["package_dependency", "install_side_effect"],
            notes=["Resource request authorizes owner-tool orchestration for acquisition, but Codex must judge risk before install side effects."],
        )
    if _has_dataset_signal(task_text):
        return RouteDecision(
            primary="resource_router",
            intent=ResourceIntent.EXTERNAL_DEPENDENCY if intent == ResourceIntent.UNKNOWN else intent,
            stage=ResourceStage.DISCOVER,
            secondary=["resource_cli"],
            reasons=["dataset source is missing; resource layer should discover dataset candidates before materialization"],
            risks=["dataset_source_selection"],
            notes=["Dataset routing must keep license, size, and file-format checks visible before download."],
        )
    if _has_broad_research_signal(task_text):
        return RouteDecision(
            primary="resource_router",
            intent=ResourceIntent.EXTERNAL_DEPENDENCY if intent == ResourceIntent.UNKNOWN else intent,
            stage=ResourceStage.DISCOVER,
            secondary=["context7", "microsoftdocs", "github", "resource_cli"],
            reasons=["broad research needs resource-layer source strategy and multi-source owner expansion before any Codex direct web fallback"],
            risks=["multi_source_research"],
            notes=["If an owner result is narrow but relevant, refine the resource delegation rather than replacing the resource layer."],
        )
    if _has_image_signal(task_text):
        return RouteDecision(
            primary="resource_router",
            intent=ResourceIntent.EXTERNAL_DEPENDENCY if intent == ResourceIntent.UNKNOWN else intent,
            stage=ResourceStage.DISCOVER,
            secondary=["resource_cli"],
            reasons=["image source is missing; resource layer should discover image candidates before materialization"],
            risks=["image_source_selection"],
            notes=["Do not route brand/place image discovery to package_manager without explicit package/install terms."],
        )
    if _has_academic_paper_signal(task_text):
        return RouteDecision(
            primary="resource_router",
            intent=ResourceIntent.EXTERNAL_DEPENDENCY,
            stage=ResourceStage.DISCOVER,
            secondary=["resource_cli"],
            reasons=["academic paper source is missing; resource layer should perform source selection before materialization"],
            risks=["academic_source_selection"],
            notes=["Do not route academic paper discovery to package_manager or Context7 documentation lookup."],
        )
    if intent == ResourceIntent.EXTERNAL_DEPENDENCY:
        return RouteDecision(
            primary="resource_router",
            intent=intent,
            stage=ResourceStage.DISCOVER,
            secondary=["resource_cli"],
            reasons=["external resource source is missing; classify source before choosing package manager or download route"],
            risks=["external_source_selection"],
            notes=["Package manager requires explicit package/dependency/install evidence."],
        )
    return RouteDecision(
        primary="resource_router",
        intent=intent,
        stage=ResourceStage.DISCOVER,
        secondary=[],
        reasons=["resource source is missing; classify source before acquiring"],
        risks=["unknown_resource"],
        notes=[],
    )


def _finalize_route(
    *,
    source_kind: str,
    url: str,
    path: str,
    name: str,
    need_materialization: bool,
    decision: RouteDecision,
) -> ResourceRoute:
    secondary = list(decision.secondary)
    notes = list(decision.notes)
    if need_materialization and decision.primary != "resource_cli":
        secondary.append("resource_cli")
        notes.append("Use the primary tool for discovery/inspection, then resource_cli for stable local materialization if needed.")
    command = ""
    if source_kind in {"url", "local_file"}:
        command = _resource_cli_command(
            url=url,
            path=path,
            intent=decision.intent,
            stage=ResourceStage.MATERIALIZE if need_materialization else decision.stage,
            name=name,
        )
    if decision.primary in MCP_TOOL_HINTS:
        notes.append(MCP_TOOL_HINTS[decision.primary])
    if "resource_cli" in secondary or decision.primary == "resource_cli":
        notes.append("resource_cli is the only route that creates stable cache metadata, sha256, and replayable acquisition logs.")
    return ResourceRoute(
        ok=True,
        read_only=True,
        source_kind=source_kind,
        intent=decision.intent,
        need_materialization=need_materialization,
        primary_tool=decision.primary,
        secondary_tools=tuple(dict.fromkeys(secondary)),
        recommended_stage=decision.stage,
        resource_cli_command=command,
        reasons=tuple(decision.reasons),
        risk_flags=tuple(dict.fromkeys(decision.risks)),
        safety_boundaries=(
            "route_plan_only",
            "owner_tool_required_for_mcp_browser_or_install",
            "resource_request_authorizes_acquisition_not_destructive_actions",
        ),
        notes=tuple(dict.fromkeys(notes)),
    )


def build_resource_route(
    *,
    url: str = "",
    path: str = "",
    target: str = "",
    intent: str = ResourceIntent.UNKNOWN,
    need_materialization: bool = False,
    task: str = "",
    name: str = "",
    resource_kind_hint: str = "",
    source_kind_hint: str = "",
    site_or_domain: str = "",
) -> ResourceRoute:
    normalized_intent = intent or ResourceIntent.UNKNOWN
    if url and path:
        return _ambiguous_route(intent=normalized_intent, need_materialization=need_materialization)
    structured_hints = " ".join(
        part
        for part in (
            f"resource_kind:{resource_kind_hint}" if resource_kind_hint else "",
            f"source_kind:{source_kind_hint}" if source_kind_hint else "",
            site_or_domain or "",
        )
        if part
    )
    task_text = " ".join(part for part in (task or "", target or "", name or "", structured_hints) if part).lower()
    source_kind = "url" if url else ("local_file" if path else "unknown")
    if source_kind == "local_file":
        decision = _route_local_file(
            path=path,
            task_text=task_text,
            intent=normalized_intent,
            need_materialization=need_materialization,
        )
    elif source_kind == "url":
        decision = _route_url(
            url=url,
            task_text=task_text,
            intent=normalized_intent,
            need_materialization=need_materialization,
        )
    else:
        decision = _route_unknown_source(task_text=task_text, intent=normalized_intent)
    return _finalize_route(
        source_kind=source_kind,
        url=url,
        path=path,
        name=name,
        need_materialization=need_materialization,
        decision=decision,
    )
