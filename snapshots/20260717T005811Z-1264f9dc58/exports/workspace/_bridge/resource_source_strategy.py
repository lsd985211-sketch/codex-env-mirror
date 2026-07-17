#!/usr/bin/env python3
"""Source-catalog strategy for resource acquisition requests.

Ownership: classify source-selection needs and return ordered candidate
sources/tools for the resource layer.
Non-goals: fetching resources, scraping sites, bypassing paywalls, installing
packages, calling MCP tools, writing files, or changing network state.
State behavior: pure read-only decisions from request and route payloads.
Caller context: resource_broker strategy planning, resource-layer validators,
and future source-selection adapters.
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.parse
from dataclasses import asdict, dataclass
from typing import Any

from intent_routing import matched_terms, negated_terms, term_matches
from resource_fetcher import ResourceIntent
from structured_task_envelope import resource_contract_from_metadata


@dataclass(frozen=True)
class SourceCandidate:
    id: str
    resource_kind: str
    owner_tool: str
    source_type: str
    priority: int
    use_for: tuple[str, ...]
    avoid_when: tuple[str, ...]
    query_hint: str
    materialization: str
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


ACADEMIC_TERMS = ("论文", "学术", "开放获取", "期刊", "会议", "arxiv", "doi")
ACADEMIC_WORD_TERMS = ("academic", "conference", "journal", "paper", "proceedings", "scholar")
IMAGE_TERMS = ("图片", "照片", "photo", "photos", "image", "images", "wallpaper", "壁纸", "插图")
VIDEO_TERMS = ("视频", "video", "mp4", "film", "clip")
AUDIO_TERMS = ("音频", "audio", "mp3", "podcast", "播客")
DATA_TERMS = ("dataset", "data set", "数据集", "数据", "csv", "parquet", "模型数据")
MODEL_TERMS = ("model", "模型", "checkpoint", "huggingface", "onnx", "safetensors")
PACKAGE_TERMS = ("package", "dependency", "pip", "npm", "pnpm", "uv", "winget", "choco", "安装", "依赖")
DOC_TERMS = ("docs", "documentation", "api", "sdk", "framework", "library", "文档", "接口", "框架")
DOCUMENT_TERMS = ("document", "manual", "pdf", "docx", "xlsx", "pptx", "手册", "说明书", "文件")
GITHUB_TERMS = ("github", "github.com", "repo", "repository", "仓库", "issue", "pull request", "actions")
OFFICIAL_VENDOR_TERMS = ("official", "site:", "openai", "openai.com", "chatgpt", "codex", "platform.openai.com", "help.openai.com", "官方")
VERSION_LOOKUP_TERMS = ("release notes", "latest version", "version", "model availability", "model selector", "版本", "发布说明")
MICROSOFT_DOC_TERMS = ("microsoft", "windows", "powershell", "azure", "office", "visual studio", "vs code", ".net", "微软")
LIBRARY_DOC_TERMS = ("library", "framework", "sdk", "package api", "module api", "库", "框架", "软件包", "模块接口")
OFFICIAL_SOURCE_KINDS = {"official_docs", "official_web", "vendor_docs", "product_docs"}
OPENAI_DOC_DOMAINS = ("developers.openai.com", "platform.openai.com", "learn.chatgpt.com", "help.openai.com", "openai.com")
RESOURCE_KIND_HINTS = {
    "academic_paper",
    "image",
    "dataset",
    "model_artifact",
    "package",
    "github_project",
    "documentation",
    "document",
    "generic_download",
    "generic_web",
}


def _text(request: dict[str, Any]) -> str:
    parts = [str(request.get(key) or "") for key in ("task", "target", "url", "name")]
    metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
    custom_delegation = metadata.get("custom_delegation") if isinstance(metadata.get("custom_delegation"), dict) else {}
    constraints = custom_delegation.get("constraints") if isinstance(custom_delegation.get("constraints"), dict) else {}
    envelope = resource_contract_from_metadata(metadata)
    resource = envelope.get("resource", {}) if envelope else {}
    source_policy = resource.get("source_policy", {}) if isinstance(resource, dict) else {}
    freshness = resource.get("freshness", {}) if isinstance(resource, dict) else {}
    parts.extend(
        str(value or "")
        for value in (
            metadata.get("resource_kind_hint"),
            constraints.get("source_kind"),
            constraints.get("site_or_domain"),
            constraints.get("authority"),
            constraints.get("freshness"),
            resource.get("kind"),
            source_policy.get("source_kind"),
            " ".join(source_policy.get("domains") or []),
            source_policy.get("authority"),
            freshness.get("mode"),
        )
    )
    return " ".join(parts).lower()


def _has_word(text: str, words: tuple[str, ...]) -> bool:
    return bool(matched_terms(text, words))


def _normalize_domain(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = re.sub(r"^site:\s*", "", text)
    parsed = urllib.parse.urlparse(text if "://" in text else f"//{text}")
    domain = parsed.netloc or parsed.path.split("/", 1)[0]
    return domain.split(":", 1)[0].strip(". ")


def _request_domains(request: dict[str, Any]) -> list[str]:
    metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
    custom = metadata.get("custom_delegation") if isinstance(metadata.get("custom_delegation"), dict) else {}
    constraints = custom.get("constraints") if isinstance(custom.get("constraints"), dict) else {}
    envelope = resource_contract_from_metadata(metadata)
    resource = envelope.get("resource", {}) if envelope else {}
    source_policy = resource.get("source_policy", {}) if isinstance(resource, dict) else {}
    explicit_values: list[Any] = []
    for value in (
        source_policy.get("domains"),
        metadata.get("source_domains"),
        constraints.get("site_or_domain"),
        metadata.get("site_or_domain"),
        request.get("url"),
    ):
        if isinstance(value, list | tuple | set):
            explicit_values.extend(value)
        elif value:
            explicit_values.extend(part for part in re.split(r"[,;\s]+", str(value)) if part)
    raw_values = list(explicit_values)
    if not explicit_values:
        target = str(request.get("target") or "").lower()
        raw_values.extend(
            match.group(1)
            for match in re.finditer(
                r"(?:https?://|\bsite:)((?:[a-z0-9-]+\.)+[a-z]{2,})(?:/[^\s,;]*)?",
                target,
            )
        )
    domains: list[str] = []
    for value in raw_values:
        domain = _normalize_domain(value)
        if domain and domain not in domains:
            domains.append(domain)
    return domains


def _domain_matches(domain: str, suffixes: tuple[str, ...]) -> bool:
    return any(domain == suffix or domain.endswith(f".{suffix}") for suffix in suffixes)


def _github_repository_target(request: dict[str, Any]) -> str:
    target = str(request.get("url") or request.get("target") or "").strip()
    if target.startswith(("http://", "https://")):
        parsed = urllib.parse.urlparse(target)
        if parsed.netloc.lower() not in {"github.com", "www.github.com", "api.github.com"}:
            return ""
        parts = [part for part in parsed.path.strip("/").split("/") if part]
        if parsed.netloc.lower() == "api.github.com" and parts[:1] == ["repos"]:
            parts = parts[1:]
        return "/".join(parts[:2]) if len(parts) >= 2 else ""
    return target.strip("/") if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", target) else ""


def source_execution_plan(request: dict[str, Any], resource_kind: str) -> dict[str, Any]:
    """Compile explicit resource operations into an ordered, source-neutral phase plan."""

    metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
    envelope = resource_contract_from_metadata(metadata)
    resource = envelope.get("resource", {}) if envelope else {}
    execution = resource.get("execution", {}) if isinstance(resource, dict) else {}
    explicit_phases = execution.get("phases") if isinstance(execution.get("phases"), list) else []
    operations = [str(item).strip().lower() for item in execution.get("operations") or [] if str(item).strip()]
    deliverables = list(dict.fromkeys(str(item).strip().lower() for item in execution.get("deliverables") or [] if str(item).strip()))
    selectors = execution.get("selectors") if isinstance(execution.get("selectors"), dict) else {}
    limits = execution.get("limits") if isinstance(execution.get("limits"), dict) else {}
    acceptance = execution.get("acceptance") if isinstance(execution.get("acceptance"), dict) else {}
    inference_source = "explicit_structured_fields" if operations or explicit_phases or deliverables else "compatibility_default"

    if explicit_phases:
        phases = [dict(item) for item in explicit_phases if isinstance(item, dict)]
        if not operations:
            operations = list(dict.fromkeys(str(item.get("operation") or "").strip().lower() for item in phases if str(item.get("operation") or "").strip()))
        if not deliverables:
            deliverables = list(
                dict.fromkeys(
                    str(deliverable).strip().lower()
                    for item in phases
                    for deliverable in (item.get("deliverables") or [])
                    if str(deliverable).strip()
                )
            )
    else:
        phases: list[dict[str, Any]] = []
        if resource_kind == "github_project":
            repository = str(selectors.get("repository") or _github_repository_target(request)).strip()
            text = _text(request)
            if not operations:
                operations = ["repository_read" if repository else "repository_search"]
                if not deliverables:
                    inferred: list[str] = []
                    for name, terms in (
                        ("readme", ("readme", "介绍", "项目说明")),
                        ("tree", ("tree", "目录", "结构")),
                        ("files", ("file", "files", "文件", "源码")),
                        ("releases", ("release", "releases", "版本", "发布")),
                        ("issues", ("issue", "issues", "问题")),
                        ("code_matches", ("code search", "symbol", "代码搜索", "符号")),
                    ):
                        if any(term in text for term in terms):
                            inferred.append(name)
                    deliverables = inferred or (["metadata"] if repository else ["candidates"])
                    inference_source = "deterministic_text_supplement"
            expanded = list(operations)
            if "repository_read" in expanded:
                expanded = [item for item in expanded if item != "repository_read"]
                expanded.insert(0, "repository_metadata")
                mapping = {
                    "readme": "readme_read", "tree": "tree_read", "files": "file_read",
                    "releases": "release_read", "issues": "issue_search", "code_matches": "code_search",
                }
                expanded.extend(mapping[item] for item in deliverables if item in mapping)
            if not repository and any(item != "repository_search" for item in expanded):
                expanded = ["repository_search", *[item for item in expanded if item != "repository_search"]]
            operations = list(dict.fromkeys(expanded))
        for index, operation in enumerate(operations):
            phases.append(
                {
                    "id": f"phase-{index + 1}-{operation}",
                    "operation": operation,
                    "required": True,
                    "depends_on": [phases[-1]["id"]] if phases else [],
                    "limit": None,
                    "selectors": {},
                    "deliverables": [],
                    "acceptance": {},
                }
            )

    return {
        "schema": "resource_source_strategy.execution_plan.v1",
        "source_adapter": "github" if resource_kind == "github_project" else "generic",
        "operations": operations,
        "selectors": selectors,
        "deliverables": deliverables,
        "limits": limits,
        "acceptance": acceptance,
        "phases": phases,
        "phase_count": len(phases),
        "inference_source": inference_source,
        "rule": "structured operations and phases are authoritative; deterministic text only supplements an absent execution specification",
    }


def _documentation_route(request: dict[str, Any], route: dict[str, Any]) -> dict[str, Any]:
    metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
    custom = metadata.get("custom_delegation") if isinstance(metadata.get("custom_delegation"), dict) else {}
    constraints = custom.get("constraints") if isinstance(custom.get("constraints"), dict) else {}
    envelope = resource_contract_from_metadata(metadata)
    resource = envelope.get("resource", {}) if envelope else {}
    source_policy = resource.get("source_policy", {}) if isinstance(resource, dict) else {}
    text = _text(request)
    domains = _request_domains(request)
    source_kind = str(source_policy.get("source_kind") or constraints.get("source_kind") or "").strip().lower()
    authority = str(source_policy.get("authority") or constraints.get("authority") or "").strip().lower()
    primary_tool = str(route.get("primary_tool") or "").strip()

    if any(_domain_matches(domain, OPENAI_DOC_DOMAINS) for domain in domains):
        route_kind = "openai_official_docs"
        owner = "openai-docs"
    elif any(_domain_matches(domain, ("learn.microsoft.com", "microsoft.com")) for domain in domains):
        route_kind = "microsoft_docs"
        owner = "microsoftdocs"
    elif domains and primary_tool == "context7" and matched_terms(text, LIBRARY_DOC_TERMS):
        route_kind = "library_framework_docs"
        owner = "context7"
    elif domains:
        route_kind = "official_vendor_docs"
        owner = "generic_search"
    elif source_kind in OFFICIAL_SOURCE_KINDS or authority in {"official", "first_party", "vendor"}:
        route_kind = "official_vendor_docs"
        owner = "generic_search"
    elif primary_tool in {"openai-docs", "openaiDeveloperDocs"}:
        route_kind = "openai_official_docs"
        owner = "openai-docs"
    elif primary_tool == "microsoftdocs":
        route_kind = "microsoft_docs"
        owner = "microsoftdocs"
    elif primary_tool == "context7":
        route_kind = "library_framework_docs"
        owner = "context7"
    elif matched_terms(text, ("openai", "chatgpt", "codex", "developers.openai.com", "platform.openai.com", "help.openai.com")):
        route_kind = "openai_official_docs"
        owner = "openai-docs"
    elif matched_terms(text, MICROSOFT_DOC_TERMS):
        route_kind = "microsoft_docs"
        owner = "microsoftdocs"
    elif matched_terms(text, LIBRARY_DOC_TERMS):
        route_kind = "library_framework_docs"
        owner = "context7"
    elif matched_terms(text, OFFICIAL_VENDOR_TERMS):
        route_kind = "official_vendor_docs"
        owner = "generic_search"
    else:
        route_kind = "generic_documentation"
        owner = "generic_search"
    return {
        "documentation_route_kind": route_kind,
        "registered_owner_adapter": owner,
        "official_domains": domains,
        "source_kind": source_kind,
        "authority": authority,
    }


def classify_resource_kind(request: dict[str, Any], route: dict[str, Any]) -> str:
    metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
    envelope = resource_contract_from_metadata(metadata)
    structured_kind = envelope.get("resource", {}).get("kind", "") if envelope else ""
    hint = str(structured_kind or metadata.get("resource_kind_hint") or "").strip()
    if hint in RESOURCE_KIND_HINTS:
        return hint
    text = _text(request)
    intent = str(request.get("intent") or route.get("intent") or "")
    primary_tool = str(route.get("primary_tool") or "")
    if intent == ResourceIntent.PACKAGE_DEPENDENCY or primary_tool == "package_manager":
        return "package"
    if intent == ResourceIntent.DOCUMENTATION_LOOKUP or primary_tool in {"context7", "microsoftdocs", "openai-docs", "openaiDeveloperDocs"}:
        return "documentation"
    if primary_tool == "github":
        return "github_project"
    if matched_terms(text, ACADEMIC_TERMS) or _has_word(text, ACADEMIC_WORD_TERMS):
        return "academic_paper"
    if matched_terms(text, IMAGE_TERMS):
        return "image"
    if matched_terms(text, VIDEO_TERMS):
        return "video"
    if matched_terms(text, AUDIO_TERMS):
        return "audio"
    if matched_terms(text, DATA_TERMS):
        return "dataset"
    if matched_terms(text, MODEL_TERMS):
        return "model_artifact"
    if matched_terms(text, PACKAGE_TERMS):
        return "package"
    non_github_domain_hint = bool(re.search(r"(?<![a-z0-9-])(?:[a-z0-9-]+\.)+[a-z]{2,}(?![a-z0-9-])", text)) and not any(
        term in text for term in ("github.com", "api.github.com")
    )
    structured_official_hint = bool(matched_terms(text, ("generic_web", "official_docs", "official_web")))
    github_strong = (
        bool(matched_terms(text, GITHUB_TERMS)) or term_matches(text, "github release")
    ) and not (non_github_domain_hint and structured_official_hint)
    official_web_lookup = (
        structured_official_hint or non_github_domain_hint or bool(matched_terms(text, OFFICIAL_VENDOR_TERMS))
    ) and bool(matched_terms(text, VERSION_LOOKUP_TERMS))
    if official_web_lookup and not github_strong:
        return "generic_web"
    if github_strong and not official_web_lookup:
        return "github_project"
    if matched_terms(text, DOC_TERMS):
        return "documentation"
    if matched_terms(text, DOCUMENT_TERMS):
        return "document"
    if str(request.get("need_materialization") or "").lower() in {"1", "true"}:
        return "generic_download"
    return "generic_web"


def source_catalog() -> tuple[SourceCandidate, ...]:
    return (
        SourceCandidate(
            id="academic_arxiv",
            resource_kind="academic_paper",
            owner_tool="resource_cli",
            source_type="open_access_repository",
            priority=10,
            use_for=("open-access papers", "preprints", "direct PDF materialization when URL is resolved"),
            avoid_when=("paid-only publication required", "publisher license is unclear"),
            query_hint="Use title/keywords plus site:arxiv.org or arXiv API; prefer /pdf/ URLs only after metadata relevance passes.",
            materialization="download_pdf_after_metadata_relevance",
            notes=("Open access source; good first path for AI papers.",),
        ),
        SourceCandidate(
            id="academic_openalex",
            resource_kind="academic_paper",
            owner_tool="resource_cli",
            source_type="metadata_index",
            priority=20,
            use_for=("paper discovery", "author/institution filtering", "DOI and open access URL lookup"),
            avoid_when=("full text is required but no OA URL is present",),
            query_hint="Use OpenAlex works search for title/keywords, institution/country filters, and open_access.oa_url.",
            materialization="metadata_then_follow_open_access_url",
            notes=("Metadata-first route keeps paywalled results from being treated as downloadable.",),
        ),
        SourceCandidate(
            id="academic_semantic_scholar",
            resource_kind="academic_paper",
            owner_tool="resource_cli",
            source_type="metadata_index",
            priority=30,
            use_for=("paper discovery", "citation/abstract ranking", "PDF URL hints"),
            avoid_when=("rate limited", "result lacks openAccessPdf"),
            query_hint="Use paper search; accept only results with relevant title and openAccessPdf when materialization is required.",
            materialization="metadata_then_follow_open_access_pdf",
        ),
        SourceCandidate(
            id="academic_crossref",
            resource_kind="academic_paper",
            owner_tool="resource_cli",
            source_type="doi_metadata",
            priority=40,
            use_for=("DOI lookup", "publisher metadata", "title disambiguation"),
            avoid_when=("download is required and no license/full-text link exists",),
            query_hint="Use Crossref works for DOI/title metadata; do not treat DOI metadata as downloadable content.",
            materialization="metadata_only_unless_full_text_link_available",
        ),
        SourceCandidate(
            id="academic_official_repository",
            resource_kind="academic_paper",
            owner_tool="resource_cli",
            source_type="official_repository",
            priority=50,
            use_for=("university or lab PDFs", "China-region institution filter", "official landing pages"),
            avoid_when=("robots/license unclear", "requires login"),
            query_hint="Prefer official university/lab/repository pages after metadata identifies the paper.",
            materialization="download_pdf_after_source_license_check",
        ),
        SourceCandidate(
            id="image_webpage_assets",
            resource_kind="image",
            owner_tool="resource_cli",
            source_type="source_page_parser",
            priority=5,
            use_for=("official media pages", "known source pages", "pages that embed direct image assets"),
            avoid_when=("source page is not known", "page requires login or script-only rendering"),
            query_hint="When a source page URL is already available, parse image assets and filter logos/icons/default placeholders.",
            materialization="return_verified_image_asset_candidates",
            notes=("Keeps page URL discovery separate from image materialization.",),
        ),
        SourceCandidate(
            id="image_wikimedia_commons",
            resource_kind="image",
            owner_tool="resource_cli",
            source_type="open_media_repository",
            priority=10,
            use_for=("public buildings", "encyclopedic images", "license-visible downloads"),
            avoid_when=("brand-sensitive commercial use without license review",),
            query_hint="Search Wikimedia Commons/Openverse-style open media sources before generic image search.",
            materialization="download_original_or_thumbnail_with_license_metadata",
        ),
        SourceCandidate(
            id="image_openverse",
            resource_kind="image",
            owner_tool="resource_cli",
            source_type="open_media_search",
            priority=20,
            use_for=("open-license images", "source attribution metadata"),
            avoid_when=("exact current product imagery is required and source is stale",),
            query_hint="Use Openverse for open-license media; keep creator/license/source URL in receipt.",
            materialization="download_image_with_attribution",
        ),
        SourceCandidate(
            id="github_repository_search",
            resource_kind="github_project",
            owner_tool="github",
            source_type="owner_mcp",
            priority=10,
            use_for=("repositories", "issues", "releases", "project metadata"),
            avoid_when=("non-GitHub source requested"),
            query_hint="Use GitHub MCP/Hub search before generic web; sort by stars/relevance only after query terms are specific.",
            materialization="metadata_first_then_release_or_clone_after_approval",
        ),
        SourceCandidate(
            id="docs_openai_official",
            resource_kind="documentation",
            owner_tool="openai-docs",
            source_type="official_owner_mcp",
            priority=3,
            use_for=("OpenAI API", "Codex", "ChatGPT developer products", "official OpenAI product documentation"),
            avoid_when=("non-OpenAI documentation",),
            query_hint="Search the official OpenAI Docs MCP, then fetch the relevant official page; search-only or empty output is insufficient for factual claims.",
            materialization="owner_text_receipt",
        ),
        SourceCandidate(
            id="docs_context7",
            resource_kind="documentation",
            owner_tool="context7",
            source_type="owner_mcp",
            priority=10,
            use_for=("library/framework/SDK docs", "API examples"),
            avoid_when=("Microsoft Learn target", "paper/news/general web target"),
            query_hint="Resolve library id first, then query docs; no-results must degrade rather than complete.",
            materialization="owner_text_receipt",
        ),
        SourceCandidate(
            id="docs_microsoft_learn",
            resource_kind="documentation",
            owner_tool="microsoftdocs",
            source_type="owner_mcp",
            priority=5,
            use_for=("Windows", "PowerShell", "Azure", ".NET", "Microsoft Learn"),
            avoid_when=("non-Microsoft docs"),
            query_hint="Use Microsoft Docs search/fetch before generic docs routes.",
            materialization="owner_text_receipt",
        ),
        SourceCandidate(
            id="docs_official_vendor_search",
            resource_kind="documentation",
            owner_tool="generic_search",
            source_type="official_domain_search",
            priority=15,
            use_for=("official product documentation", "vendor help centers", "documentation outside library-specific owners"),
            avoid_when=("Microsoft Learn target", "library/framework target with a dedicated docs owner"),
            query_hint="Constrain search to structured official domains when available; preserve vendor authority in the receipt.",
            materialization="owner_text_receipt",
        ),
        SourceCandidate(
            id="package_registry",
            resource_kind="package",
            owner_tool="package_manager",
            source_type="package_registry",
            priority=10,
            use_for=("package metadata", "install-risk review", "version/source checks"),
            avoid_when=("install side effects are not approved"),
            query_hint="Choose ecosystem from metadata; inspect registry metadata before any install.",
            materialization="metadata_first_install_requires_approval",
        ),
        SourceCandidate(
            id="dataset_huggingface",
            resource_kind="dataset",
            owner_tool="resource_cli",
            source_type="dataset_repository",
            priority=10,
            use_for=("AI datasets", "model-related datasets", "dataset cards"),
            avoid_when=("license missing", "large download without size budget"),
            query_hint="Use Hugging Face dataset/model metadata first; require license and size before download.",
            materialization="metadata_then_file_download_with_size_budget",
        ),
        SourceCandidate(
            id="dataset_zenodo",
            resource_kind="dataset",
            owner_tool="resource_cli",
            source_type="research_archive",
            priority=20,
            use_for=("research datasets", "DOI-backed archives", "supplementary files"),
            avoid_when=("huge archives without explicit approval"),
            query_hint="Use Zenodo/Figshare-style archive metadata before file materialization.",
            materialization="metadata_then_file_download_with_checksum_if_available",
        ),
        SourceCandidate(
            id="model_huggingface",
            resource_kind="model_artifact",
            owner_tool="resource_cli",
            source_type="model_repository",
            priority=10,
            use_for=("model cards", "weights", "tokenizers", "AI model artifacts"),
            avoid_when=("license/size unknown", "requires login token"),
            query_hint="Resolve model card and files first; require license, size, and target path before large downloads.",
            materialization="metadata_then_large_download_policy",
        ),
        SourceCandidate(
            id="document_webpage_assets",
            resource_kind="document",
            owner_tool="resource_cli",
            source_type="source_page_parser",
            priority=10,
            use_for=("manuals", "PDF/Office documents", "known pages that link downloadable documents"),
            avoid_when=("academic paper identity is required", "SDK/API documentation text is required"),
            query_hint="When a source page URL is available, parse document links and filter by requested type, title, and size.",
            materialization="return_verified_document_candidates_then_download",
            notes=("Keeps ordinary downloadable documents separate from academic papers and owner documentation lookups.",),
        ),
        SourceCandidate(
            id="generic_web_page",
            resource_kind="generic_web",
            owner_tool="resource_cli",
            source_type="generic_web",
            priority=90,
            use_for=("ordinary URLs", "web pages without owner MCP"),
            avoid_when=("owner MCP exists for the source", "download requested but source URL is missing"),
            query_hint="Use only after owner/source-specific routes are unavailable or insufficient.",
            materialization="preview_or_materialize_explicit_url",
        ),
        SourceCandidate(
            id="generic_download_url",
            resource_kind="generic_download",
            owner_tool="resource_cli",
            source_type="explicit_url_download",
            priority=90,
            use_for=("already-resolved direct URLs", "unknown file types"),
            avoid_when=("URL missing", "filesystem write not allowed"),
            query_hint="Probe URL, validate content type/size, then download with builtin/curl/aria2 policy.",
            materialization="probe_then_download",
        ),
    )


def source_execution_capability(resource_kind: str, *, registered_owner_adapter: str = "") -> dict[str, Any]:
    if resource_kind == "generic_web":
        return {
            "mode": "curated_reference_catalog_plus_owner_adapter",
            "bounded_execution_available": True,
            "arbitrary_search_available": True,
            "registered_owner_adapter": "generic_search",
            "required_capability_for_arbitrary_search": "generic_web_search_owner_adapter",
            "runtime_health_required": True,
            "rule": "skill or workflow names are not executable backends; the registered generic_search owner adapter must pass runtime health",
        }
    if resource_kind == "documentation" and registered_owner_adapter:
        return {
            "mode": "registered_documentation_owner_adapter",
            "bounded_execution_available": True,
            "arbitrary_search_available": registered_owner_adapter == "generic_search",
            "registered_owner_adapter": registered_owner_adapter,
            "required_capability_for_arbitrary_search": "generic_web_search_owner_adapter" if registered_owner_adapter == "generic_search" else "",
            "runtime_health_required": True,
            "rule": "documentation source selection delegates to the semantically matched registered owner adapter",
        }
    return {
        "mode": "resource_source_executor_adapter",
        "bounded_execution_available": True,
        "arbitrary_search_available": False,
        "required_capability_for_arbitrary_search": "",
        "rule": "candidate execution is limited to adapters implemented by the resource source executor",
    }


def candidate_source_plan(request: dict[str, Any], route: dict[str, Any], *, limit: int = 6) -> dict[str, Any]:
    kind = classify_resource_kind(request, route)
    text = _text(request)
    candidates = [item for item in source_catalog() if item.resource_kind == kind]
    documentation = _documentation_route(request, route) if kind == "documentation" else {}
    if kind == "documentation":
        owner = str(documentation.get("registered_owner_adapter") or "")
        fallback_order = {
            "openai-docs": ("openai-docs", "generic_search"),
            "microsoftdocs": ("microsoftdocs", "generic_search"),
            "context7": ("context7", "generic_search"),
            "generic_search": ("generic_search",),
        }.get(owner, (owner,))
        owner_rank = {value: index for index, value in enumerate(fallback_order)}
        candidates = [item for item in candidates if item.owner_tool in owner_rank]
        candidates = sorted(candidates, key=lambda item: (owner_rank[item.owner_tool], item.priority))
    if kind == "generic_download" and str(request.get("url") or "").strip():
        candidates = [item for item in source_catalog() if item.id == "generic_download_url"]
    if not candidates and kind != "generic_web":
        candidates = [item for item in source_catalog() if item.resource_kind == "generic_web"]
    candidates = sorted(candidates, key=lambda item: item.priority)[: max(1, limit)] if kind != "documentation" else candidates[: max(1, limit)]
    source_missing = not str(request.get("url") or request.get("path") or "").strip()
    need_materialization = bool(request.get("need_materialization"))
    execution_plan = source_execution_plan(request, kind)
    return {
        "schema": "resource_source_strategy.plan.v1",
        "ok": True,
        "resource_kind": kind,
        "classification_evidence": {
            "intent": str(request.get("intent") or route.get("intent") or ""),
            "primary_tool": str(route.get("primary_tool") or ""),
            "package_hits": matched_terms(text, PACKAGE_TERMS),
            "suppressed_package_hits": negated_terms(text, PACKAGE_TERMS),
            "documentation_hits": matched_terms(text, DOC_TERMS),
            "github_hits": matched_terms(text, GITHUB_TERMS),
            **documentation,
        },
        "source_missing": source_missing,
        "need_materialization": need_materialization,
        "candidate_count": len(candidates),
        "candidates": [item.to_dict() for item in candidates],
        "execution_plan": execution_plan,
        "execution_capability": source_execution_capability(
            kind,
            registered_owner_adapter=str(documentation.get("registered_owner_adapter") or ""),
        ),
        "selection_policy": {
            "rank_by": ["source_specificity", "license_or_access_clarity", "metadata_relevance", "network_health", "materialization_support"],
            "accept_only_if": [
                "relevance_matches_request",
                "source_access_is_clear",
                "download_url_is_direct_or_followed_from_trusted_metadata",
                "size_and_type_fit_request_constraints",
            ],
            "retry_refinement_fields": [
                "resource_kind",
                "keywords",
                "site_or_domain",
                "language",
                "country_or_institution",
                "license",
                "file_format",
                "max_bytes",
            ],
        },
        "fallback_policy": {
            "on_no_results": "refine_query_then_try_next_candidate",
            "on_low_relevance": "try_next_candidate_or_refine_terms",
            "on_blocked_or_paywalled": "metadata_only_or_report_access_blocker",
            "on_slow_download": "switch_backend_or_background_download_after_policy",
            "on_repeated_network_failure": "ask_network_layer_for_route_refresh",
        },
        "next_action": (
            "execute_source_selection_candidate"
            if source_missing
            else "use_route_plan_for_concrete_source"
        ),
    }


def validate() -> dict[str, Any]:
    paper = candidate_source_plan(
        {"task": "查找并下载一篇关于人工智能的中国区论文", "target": "中国 人工智能 论文 PDF", "need_materialization": True},
        {"primary_tool": "resource_router", "intent": ResourceIntent.EXTERNAL_DEPENDENCY},
    )
    wallpaper = candidate_source_plan(
        {"task": "download wallpaper pdf", "target": "wallpaper pdf", "need_materialization": True},
        {"primary_tool": "resource_router", "intent": ResourceIntent.EXTERNAL_DEPENDENCY},
    )
    docs = candidate_source_plan(
        {"task": "查询 LangChain 官方文档", "target": "LangChain docs"},
        {"primary_tool": "context7", "intent": ResourceIntent.DOCUMENTATION_LOOKUP},
    )
    openai_docs = candidate_source_plan(
        {
            "task": "查找 OpenAI Codex plugins 和 Sites 的官方文档，不要使用 Microsoft Docs",
            "target": "OpenAI official product documentation",
            "intent": ResourceIntent.DOCUMENTATION_LOOKUP,
            "metadata": {
                "resource_kind_hint": "documentation",
                "source_domains": ["openai.com", "help.openai.com", "developers.openai.com"],
            },
        },
        {"primary_tool": "resource_router", "intent": ResourceIntent.DOCUMENTATION_LOOKUP},
    )
    microsoft_docs = candidate_source_plan(
        {"task": "查询 PowerShell 官方文档", "target": "learn.microsoft.com PowerShell docs", "intent": ResourceIntent.DOCUMENTATION_LOOKUP},
        {"primary_tool": "microsoftdocs", "intent": ResourceIntent.DOCUMENTATION_LOOKUP},
    )
    stripe_docs = candidate_source_plan(
        {
            "task": "查询 Stripe 官方 API 文档",
            "target": "docs.stripe.com API",
            "intent": ResourceIntent.DOCUMENTATION_LOOKUP,
            "metadata": {"resource_kind_hint": "documentation", "source_domains": ["docs.stripe.com"]},
        },
        {"primary_tool": "resource_router", "intent": ResourceIntent.DOCUMENTATION_LOOKUP},
    )
    ambiguous_docs = candidate_source_plan(
        {"task": "查找产品使用文档", "target": "product usage docs", "intent": ResourceIntent.DOCUMENTATION_LOOKUP},
        {"primary_tool": "resource_router", "intent": ResourceIntent.DOCUMENTATION_LOOKUP},
    )
    package = candidate_source_plan(
        {"task": "安装 ruff", "target": "ruff", "intent": ResourceIntent.PACKAGE_DEPENDENCY},
        {"primary_tool": "package_manager", "intent": ResourceIntent.PACKAGE_DEPENDENCY},
    )
    image = candidate_source_plan(
        {"task": "下载十张关于华为总部的不同图片", "target": "华为总部 Huawei headquarters photos", "need_materialization": True},
        {"primary_tool": "resource_router", "intent": ResourceIntent.EXTERNAL_DEPENDENCY},
    )
    dataset = candidate_source_plan(
        {"task": "查找一个开放 AI 训练数据集", "target": "AI training dataset csv open license", "need_materialization": True},
        {"primary_tool": "resource_router", "intent": ResourceIntent.EXTERNAL_DEPENDENCY},
    )
    document = candidate_source_plan(
        {"task": "下载两份 PDF 手册", "target": "manual pdf", "need_materialization": True, "metadata": {"resource_kind_hint": "document"}},
        {"primary_tool": "resource_router", "intent": ResourceIntent.EXTERNAL_DEPENDENCY},
    )
    openai_official = candidate_source_plan(
        {
            "task": "official Codex CLI latest version lookup",
            "target": "OpenAI Codex CLI latest version release notes official",
            "metadata": {
                "resource_kind_hint": "generic_web",
                "custom_delegation": {
                    "constraints": {
                        "source_kind": "official_docs",
                        "site_or_domain": "openai.com",
                    }
                },
            },
        },
        {"primary_tool": "resource_router", "intent": ResourceIntent.EXTERNAL_DEPENDENCY},
    )
    negated_package_research = candidate_source_plan(
        {
            "task": "Research mature routing projects. Do not install packages or mutate the workspace.",
            "target": "intent routing documentation and GitHub projects",
            "intent": ResourceIntent.DOCUMENTATION_LOOKUP,
        },
        {"primary_tool": "resource_router", "intent": ResourceIntent.DOCUMENTATION_LOOKUP},
    )
    dotted_filename_docs = candidate_source_plan(
        {
            "task": "Find official Codex AGENTS.md guidance",
            "target": "site:developers.openai.com Codex AGENTS.md instruction hierarchy",
            "intent": ResourceIntent.DOCUMENTATION_LOOKUP,
            "metadata": {"source_domains": ["developers.openai.com", "openai.com"]},
        },
        {"primary_tool": "resource_router", "intent": ResourceIntent.DOCUMENTATION_LOOKUP},
    )
    generic_capability = openai_official.get("execution_capability") or {}
    checks = [
        {"name": "paper_has_academic_sources", "ok": paper["resource_kind"] == "academic_paper" and paper["candidates"][0]["id"] == "academic_arxiv"},
        {"name": "wallpaper_not_academic", "ok": wallpaper["resource_kind"] != "academic_paper"},
        {"name": "library_docs_use_context7", "ok": docs["resource_kind"] == "documentation" and docs["candidates"][0]["owner_tool"] == "context7"},
        {"name": "openai_docs_use_official_owner", "ok": openai_docs["candidates"][0]["owner_tool"] == "openai-docs" and openai_docs["classification_evidence"]["documentation_route_kind"] == "openai_official_docs"},
        {"name": "openai_docs_fallback_is_generic_only", "ok": [item["owner_tool"] for item in openai_docs["candidates"]] == ["openai-docs", "generic_search"]},
        {"name": "openai_domains_preserved", "ok": openai_docs["classification_evidence"]["official_domains"] == ["openai.com", "help.openai.com", "developers.openai.com"]},
        {"name": "microsoft_docs_use_microsoft_owner", "ok": microsoft_docs["candidates"][0]["owner_tool"] == "microsoftdocs"},
        {"name": "other_vendor_docs_use_official_search", "ok": stripe_docs["candidates"][0]["owner_tool"] == "generic_search"},
        {"name": "ambiguous_docs_do_not_default_microsoft_or_context7", "ok": ambiguous_docs["candidates"][0]["owner_tool"] == "generic_search"},
        {"name": "package_uses_registry", "ok": package["resource_kind"] == "package" and package["candidates"][0]["owner_tool"] == "package_manager"},
        {"name": "image_uses_image_sources", "ok": image["resource_kind"] == "image" and image["candidates"][0]["id"] == "image_webpage_assets"},
        {"name": "dataset_uses_dataset_sources", "ok": dataset["resource_kind"] == "dataset" and dataset["candidates"][0]["id"] == "dataset_huggingface"},
        {"name": "document_uses_document_assets", "ok": document["resource_kind"] == "document" and document["candidates"][0]["id"] == "document_webpage_assets"},
        {"name": "openai_official_release_notes_not_github", "ok": openai_official["resource_kind"] == "generic_web"},
        {"name": "negated_package_research_stays_documentation", "ok": negated_package_research["resource_kind"] == "documentation"},
        {
            "name": "dotted_filename_is_not_inferred_as_domain",
            "ok": dotted_filename_docs["classification_evidence"]["official_domains"] == ["developers.openai.com", "openai.com"],
        },
        {
            "name": "generic_web_capability_is_truthful",
            "ok": generic_capability.get("mode") == "curated_reference_catalog_plus_owner_adapter"
            and generic_capability.get("arbitrary_search_available") is True
            and generic_capability.get("registered_owner_adapter") == "generic_search"
            and generic_capability.get("required_capability_for_arbitrary_search") == "generic_web_search_owner_adapter",
        },
    ]
    return {
        "schema": "resource_source_strategy.validate.v1",
        "ok": all(bool(item["ok"]) for item in checks),
        "checks": checks,
        "catalog_count": len(source_catalog()),
        "writes_files": False,
        "network_calls": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan resource source candidates without side effects.")
    parser.add_argument("command", choices=("plan", "validate"))
    parser.add_argument("--request-json", default="{}")
    parser.add_argument("--route-json", default="{}")
    parser.add_argument("--limit", type=int, default=6)
    args = parser.parse_args()
    if args.command == "validate":
        payload = validate()
    else:
        payload = candidate_source_plan(json.loads(args.request_json), json.loads(args.route_json), limit=args.limit)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
