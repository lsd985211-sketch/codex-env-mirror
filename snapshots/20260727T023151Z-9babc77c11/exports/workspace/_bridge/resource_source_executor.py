#!/usr/bin/env python3
"""Read-only source-selection executor for resource acquisition.

Ownership: execute bounded metadata/source discovery for resource requests
after `resource_source_strategy` has selected candidate source families.
Non-goals: downloading files, scraping arbitrary pages, bypassing access
controls, installing packages, writing files, or changing network state.
State behavior: read-only network calls to public metadata APIs; no persistent
writes.
Caller context: `resource_broker.py` source-selection attempts and validators.
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from resource_candidate_quality import filter_ranked_candidates, quality_constraints_from_request, quality_summary, rank_candidates
from resource_execution_budget import ResourceExecutionBudget
from resource_source_strategy import candidate_source_plan


USER_AGENT = "codex-resource-layer/1.0"


def _json_result(**payload: Any) -> dict[str, Any]:
    payload.setdefault("schema", "resource_source_executor.result.v1")
    payload.setdefault("writes_files", False)
    payload.setdefault("writes_remote_state", False)
    payload.setdefault("permission_boundary", "source_metadata_read_only")
    return payload


def request_text(request: dict[str, Any]) -> str:
    return " ".join(str(request.get(key) or "") for key in ("task", "target", "name")).strip()


def query_terms(request: dict[str, Any]) -> str:
    text = request_text(request)
    text = re.sub(r"\b(pdf|download|open access|paper|academic)\b", " ", text, flags=re.IGNORECASE)
    for term in ("下载", "查找", "一篇", "关于", "论文", "开放获取", "可下载", "并", "的"):
        text = text.replace(term, " ")
    text = " ".join(text.split())
    return text or request_text(request) or "artificial intelligence"


def image_query_terms(request: dict[str, Any]) -> str:
    text = request_text(request)
    text = re.sub(r"\b(download|image|images|photo|photos|picture|pictures|wallpaper|screenshot)\b", " ", text, flags=re.IGNORECASE)
    for term in ("下载", "查找", "关于", "不同", "图片", "照片", "图像", "壁纸", "十张", "一张", "若干", "的"):
        text = text.replace(term, " ")
    text = " ".join(text.split())
    return text or request_text(request) or "headquarters building"


def _open_text(url: str, *, timeout: int, accept: str = "application/json") -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    with urllib.request.urlopen(request, timeout=max(1, min(timeout, 30))) as response:
        return response.read(1_000_000).decode("utf-8", errors="replace")


def _open_json(url: str, *, timeout: int) -> dict[str, Any]:
    payload = json.loads(_open_text(url, timeout=timeout, accept="application/json"))
    return payload if isinstance(payload, dict) else {}


def _absolute_url(base_url: str, value: str) -> str:
    return urllib.parse.urljoin(base_url, value.replace("&amp;", "&").strip())


def _image_ext(url: str) -> str:
    path = urllib.parse.unquote(urllib.parse.urlparse(url).path.lower())
    for segment in reversed([part for part in path.split("/") if part]):
        if segment in {"content", "download", "raw", "resolve"}:
            continue
        if "." not in segment:
            continue
        ext = segment.rsplit(".", 1)[-1].strip()
        if ext and "/" not in ext and len(ext) <= 16:
            return ext
    return ""


def _looks_like_image_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme in {"http", "https"} and _image_ext(url) in {"jpg", "jpeg", "png", "webp", "gif", "svg", "bmp", "tif", "tiff", "avif"}


IMAGE_NOISE_TERMS = (
    "logo",
    "icon",
    "popup",
    "maskpop",
    "default_img",
    "apple-touch-icon",
    "precomposed",
    "hcomponent-header",
    "hcomponent-side-bar",
    "sprite",
    "favicon",
)

COMMON_DOWNLOAD_EXTENSIONS = {
    "7z",
    "aac",
    "avi",
    "csv",
    "doc",
    "docx",
    "epub",
    "exe",
    "flac",
    "gz",
    "html",
    "jar",
    "json",
    "md",
    "mp3",
    "mp4",
    "msi",
    "onnx",
    "parquet",
    "pdf",
    "ppt",
    "pptx",
    "rar",
    "safetensors",
    "tar",
    "tgz",
    "txt",
    "wav",
    "webm",
    "whl",
    "xls",
    "xlsx",
    "xml",
    "zip",
}

RESOURCE_KIND_EXTENSIONS = {
    "audio": {"aac", "flac", "m4a", "mp3", "ogg", "opus", "wav", "wma"},
    "dataset": {"csv", "json", "jsonl", "parquet", "tsv", "txt", "xml", "zip", "gz", "tar", "tgz"},
    "document": {"csv", "doc", "docx", "epub", "html", "md", "pdf", "ppt", "pptx", "txt", "xls", "xlsx"},
    "generic_download": COMMON_DOWNLOAD_EXTENSIONS,
    "model_artifact": {"bin", "ckpt", "gguf", "onnx", "pt", "pth", "safetensors", "tar", "zip"},
    "video": {"avi", "flv", "m4v", "mkv", "mov", "mp4", "mpeg", "mpg", "webm", "wmv"},
}


def _image_noise_reason(url: str) -> str:
    lowered = urllib.parse.unquote(url).lower()
    for term in IMAGE_NOISE_TERMS:
        if term in lowered:
            return f"noise_term:{term}"
    return ""


def _looks_like_common_download_url(url: str, resource_kind: str = "") -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    ext = _image_ext(url)
    allowed = RESOURCE_KIND_EXTENSIONS.get(resource_kind) or COMMON_DOWNLOAD_EXTENSIONS
    return ext in allowed


def _extension_allowed(value: str, resource_kind: str = "") -> bool:
    ext = _image_ext(value)
    allowed = RESOURCE_KIND_EXTENSIONS.get(resource_kind) or COMMON_DOWNLOAD_EXTENSIONS
    return ext in allowed


def _candidate(
    *,
    source_id: str,
    title: str,
    url: str,
    landing_url: str = "",
    query: str = "",
    source: str = "",
    license_hint: str = "",
    attribution: str = "",
    width: int = 0,
    height: int = 0,
    size: int = 0,
    summary: str = "",
) -> dict[str, Any]:
    values = [title, landing_url, source, summary, urllib.parse.unquote(url)]
    score = _score_text(query, *values) if query else 0.5
    if width and height:
        score += 0.15
    if size and size > 80_000:
        score += 0.1
    return {
        "source_id": source_id,
        "title": title or urllib.parse.unquote(urllib.parse.urlparse(url).path.rsplit("/", 1)[-1]),
        "summary": summary[:800],
        "url": url,
        "direct_url": url,
        "landing_url": landing_url,
        "source_page": landing_url,
        "source": source,
        "score": round(min(1.0, score), 3),
        "license_hint": license_hint,
        "attribution": attribution,
        "resource_kind": "image",
        "file_type": _image_ext(url),
        "estimated_size": size,
        "width": width,
        "height": height,
    }


def _download_candidate(
    *,
    source_id: str,
    title: str,
    url: str,
    landing_url: str = "",
    query: str = "",
    source: str = "",
    resource_kind: str = "generic_download",
    license_hint: str = "",
    summary: str = "",
    size: int = 0,
) -> dict[str, Any]:
    values = [title, landing_url, source, summary, urllib.parse.unquote(url)]
    score = _score_text(query, *values) if query else 0.5
    ext = _image_ext(url)
    if ext:
        score += 0.05
    return {
        "source_id": source_id,
        "title": title or urllib.parse.unquote(urllib.parse.urlparse(url).path.rsplit("/", 1)[-1]),
        "summary": summary[:800],
        "url": url,
        "direct_url": url,
        "landing_url": landing_url,
        "source_page": landing_url,
        "source": source,
        "score": round(min(1.0, score), 3),
        "license_hint": license_hint,
        "attribution": "",
        "resource_kind": resource_kind,
        "file_type": ext,
        "estimated_size": size,
    }


def _web_candidate(
    *,
    source_id: str,
    title: str,
    url: str,
    query: str,
    source: str,
    summary: str,
    license_hint: str = "official_docs",
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "title": title,
        "summary": summary[:800],
        "url": url,
        "direct_url": url,
        "landing_url": url,
        "source_page": url,
        "source": source,
        "score": _score_text(query, title, summary, url),
        "license_hint": license_hint,
        "attribution": source,
        "resource_kind": "generic_web",
        "file_type": "html",
        "estimated_size": 0,
    }


def generic_web_reference_catalog(query: str) -> list[dict[str, Any]]:
    """Return stable source candidates for general engineering research."""

    catalog = [
        _web_candidate(
            source_id="openai_model_release_notes",
            title="OpenAI Help: Model Release Notes",
            url="https://help.openai.com/en/articles/9624314-model-release-notes",
            query=query,
            source="OpenAI Help",
            summary="Official OpenAI release notes covering model availability, product rollout notes, and model selector changes.",
        ),
        _web_candidate(
            source_id="openai_api_models_docs",
            title="OpenAI Platform Docs: Models",
            url="https://platform.openai.com/docs/models",
            query=query,
            source="OpenAI Platform",
            summary="Official OpenAI API model documentation with model capabilities, availability, and usage notes.",
        ),
        _web_candidate(
            source_id="openai_codex_docs",
            title="OpenAI Platform Docs: Codex",
            url="https://platform.openai.com/docs/codex",
            query=query,
            source="OpenAI Platform",
            summary="Official OpenAI Codex developer tooling documentation and product guidance.",
        ),
        _web_candidate(
            source_id="opentelemetry_observability_primer",
            title="OpenTelemetry Observability Primer",
            url="https://opentelemetry.io/docs/concepts/observability-primer/",
            query=query,
            source="OpenTelemetry",
            summary="Official primer on observability with logs, metrics, traces, and signals for making system behavior queryable.",
        ),
        _web_candidate(
            source_id="google_sre_monitoring_distributed_systems",
            title="Google SRE Book: Monitoring Distributed Systems",
            url="https://sre.google/sre-book/monitoring-distributed-systems/",
            query=query,
            source="Google SRE",
            summary="SRE guidance on monitoring, symptoms, causes, and actionable alerts for production systems.",
        ),
        _web_candidate(
            source_id="google_sre_eliminating_toil",
            title="Google SRE Book: Eliminating Toil",
            url="https://sre.google/sre-book/eliminating-toil/",
            query=query,
            source="Google SRE",
            summary="SRE guidance on reducing repetitive manual work through automation without losing engineering control.",
        ),
        _web_candidate(
            source_id="microsoft_well_architected_operational_excellence",
            title="Microsoft Azure Well-Architected Framework: Operational Excellence",
            url="https://learn.microsoft.com/en-us/azure/well-architected/operational-excellence/",
            query=query,
            source="Microsoft Learn",
            summary="Official operational excellence guidance covering observability, automation, deployment, and continuous improvement.",
        ),
        _web_candidate(
            source_id="envoy_circuit_breaking",
            title="Envoy Docs: Circuit Breaking",
            url="https://www.envoyproxy.io/docs/envoy/latest/intro/arch_overview/upstream/circuit_breaking",
            query=query,
            source="Envoy",
            summary="Official gateway documentation for circuit breaking and protecting upstream systems from overload.",
        ),
        _web_candidate(
            source_id="envoy_retries",
            title="Envoy Docs: Retries",
            url="https://www.envoyproxy.io/docs/envoy/latest/intro/arch_overview/http/http_routing#retry-semantics",
            query=query,
            source="Envoy",
            summary="Official routing documentation for retry semantics, useful for bounded fallback and retry-budget design.",
        ),
        _web_candidate(
            source_id="github_rest_search",
            title="GitHub REST API: Search",
            url="https://docs.github.com/en/rest/search/search",
            query=query,
            source="GitHub Docs",
            summary="Official GitHub search API documentation for repository, issue, and code search constraints and query shape.",
        ),
        _web_candidate(
            source_id="sqlite_query_planner",
            title="SQLite Query Planner",
            url="https://www.sqlite.org/queryplanner.html",
            query=query,
            source="SQLite",
            summary="Official SQLite documentation on query planning and indexes for efficient structured state lookups.",
        ),
    ]
    ranked = sorted(catalog, key=lambda item: (-float(item.get("score") or 0.0), str(item.get("source_id") or "")))
    return ranked


def source_page_urls(request: dict[str, Any]) -> list[str]:
    values: list[str] = []
    metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
    for key in ("url", "target"):
        value = str(request.get(key) or "").strip()
        if value.startswith(("http://", "https://")):
            values.append(value)
    for key in ("source_page", "source_url", "landing_url"):
        value = str(metadata.get(key) or "").strip()
        if value.startswith(("http://", "https://")):
            values.append(value)
    raw_pages = metadata.get("source_pages")
    if isinstance(raw_pages, list):
        values.extend(str(item).strip() for item in raw_pages if str(item).strip().startswith(("http://", "https://")))
    return list(dict.fromkeys(values))[:5]


def webpage_image_candidates(request: dict[str, Any], query: str, *, timeout: int, limit: int = 10) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    budget = ResourceExecutionBudget.start(timeout)
    for page_url in source_page_urls(request):
        call_timeout = budget.timeout_seconds(cap=timeout)
        if call_timeout <= 0:
            break
        try:
            html = _open_text(page_url, timeout=call_timeout, accept="text/html,*/*")
        except Exception:
            continue
        urls: set[str] = set()
        for pattern in (
            r"https?://[^\"'\s<>]+?\.(?:jpg|jpeg|png|webp|gif|svg|bmp|tif|tiff|avif)(?:\?[^\"'\s<>]*)?",
            r"(?:/~/media|/-/media|/media|/assets|/Assets|/dam)/[^\"'\s<>]+?\.(?:jpg|jpeg|png|webp|gif|svg|bmp|tif|tiff|avif)(?:\?[^\"'\s<>]*)?",
        ):
            for match in re.findall(pattern, html, flags=re.I):
                urls.add(_absolute_url(page_url, match))
        for match in re.findall(r"""(?:src|href|content)=["']([^"']+\.(?:jpg|jpeg|png|webp|gif|svg|bmp|tif|tiff|avif)(?:\?[^"']*)?)["']""", html, flags=re.I):
            urls.add(_absolute_url(page_url, match))
        for match in re.findall(r"""srcset=["']([^"']+)["']""", html, flags=re.I):
            for part in match.split(","):
                value = part.strip().split(" ", 1)[0]
                if value:
                    urls.add(_absolute_url(page_url, value))
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.I | re.S)
        title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else page_url
        for url in sorted(urls):
            if url in seen or not _looks_like_image_url(url) or _image_noise_reason(url):
                continue
            seen.add(url)
            items.append(
                _candidate(
                    source_id="image_webpage_assets",
                    title=title,
                    url=url,
                    landing_url=page_url,
                    query=query,
                    source=urllib.parse.urlparse(page_url).netloc,
                    license_hint="source_page_terms_apply",
                )
            )
            if len(items) >= limit:
                return items
    return items


def webpage_download_candidates(
    request: dict[str, Any],
    query: str,
    *,
    timeout: int,
    resource_kind: str,
    limit: int = 12,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    explicit_url = str(request.get("url") or "").strip()
    metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
    is_source_page = explicit_url and explicit_url == str(metadata.get("source_page") or "").strip()
    if explicit_url and not is_source_page and _looks_like_common_download_url(explicit_url, resource_kind):
        return [
            _download_candidate(
                source_id="generic_download_url",
                title=str(request.get("name") or request.get("target") or explicit_url),
                url=explicit_url,
                landing_url=explicit_url,
                query=query,
                source=urllib.parse.urlparse(explicit_url).netloc,
                resource_kind=resource_kind,
                license_hint="explicit_url",
            )
        ]
    extension_pattern = "|".join(sorted(COMMON_DOWNLOAD_EXTENSIONS, key=len, reverse=True))
    budget = ResourceExecutionBudget.start(timeout)
    for page_url in source_page_urls(request):
        call_timeout = budget.timeout_seconds(cap=timeout)
        if call_timeout <= 0:
            break
        try:
            html = _open_text(page_url, timeout=call_timeout, accept="text/html,*/*")
        except Exception:
            continue
        urls: set[str] = set()
        for pattern in (
            rf"https?://[^\"'\s<>]+?\.(?:{extension_pattern})(?:\?[^\"'\s<>]*)?",
            rf"(?:/download|/downloads|/files|/assets|/media|/dataset|/model|/releases)/[^\"'\s<>]+?\.(?:{extension_pattern})(?:\?[^\"'\s<>]*)?",
        ):
            for match in re.findall(pattern, html, flags=re.I):
                urls.add(_absolute_url(page_url, match))
        for match in re.findall(rf"""(?:href|src|content)=["']([^"']+\.(?:{extension_pattern})(?:\?[^"']*)?)["']""", html, flags=re.I):
            urls.add(_absolute_url(page_url, match))
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.I | re.S)
        page_title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else page_url
        for url in sorted(urls):
            if url in seen or not _looks_like_common_download_url(url, resource_kind):
                continue
            seen.add(url)
            items.append(
                _download_candidate(
                    source_id="webpage_download_assets",
                    title=Path(urllib.parse.urlparse(url).path).name or page_title,
                    url=url,
                    landing_url=page_url,
                    query=query,
                    source=urllib.parse.urlparse(page_url).netloc,
                    resource_kind=resource_kind,
                    license_hint="source_page_terms_apply",
                )
            )
            if len(items) >= limit:
                return items
    return items


def wikimedia_image_candidates(query: str, *, timeout: int, limit: int = 10) -> list[dict[str, Any]]:
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": "6",
        "gsrlimit": max(1, min(limit, 20)),
        "prop": "imageinfo",
        "iiprop": "url|mime|size|extmetadata",
        "format": "json",
    }
    url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)
    payload = _open_json(url, timeout=timeout)
    pages = (payload.get("query") or {}).get("pages") if isinstance(payload.get("query"), dict) else {}
    items: list[dict[str, Any]] = []
    for page in (pages or {}).values():
        if not isinstance(page, dict):
            continue
        info = (page.get("imageinfo") or [{}])[0]
        if not isinstance(info, dict):
            continue
        image_url = str(info.get("url") or "")
        if not _looks_like_image_url(image_url) or _image_noise_reason(image_url):
            continue
        ext = info.get("extmetadata") if isinstance(info.get("extmetadata"), dict) else {}

        def ext_value(key: str) -> str:
            value = ext.get(key) if isinstance(ext, dict) else {}
            return str(value.get("value") or "") if isinstance(value, dict) else ""

        items.append(
            _candidate(
                source_id="image_wikimedia_commons",
                title=str(page.get("title") or ""),
                url=image_url,
                landing_url=str(info.get("descriptionurl") or ""),
                query=query,
                source="Wikimedia Commons",
                license_hint=ext_value("LicenseShortName") or ext_value("UsageTerms"),
                attribution=re.sub(r"<[^>]+>", "", ext_value("Artist")),
                width=int(info.get("width") or 0),
                height=int(info.get("height") or 0),
                size=int(info.get("size") or 0),
                summary=re.sub(r"<[^>]+>", "", ext_value("ImageDescription")),
            )
        )
    return items


def openverse_image_candidates(query: str, *, timeout: int, limit: int = 10) -> list[dict[str, Any]]:
    url = "https://api.openverse.org/v1/images/?" + urllib.parse.urlencode(
        {"q": query, "page_size": max(1, min(limit, 20))}
    )
    payload = _open_json(url, timeout=timeout)
    items: list[dict[str, Any]] = []
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        image_url = str(item.get("url") or "")
        if not _looks_like_image_url(image_url) or _image_noise_reason(image_url):
            continue
        items.append(
            _candidate(
                source_id="image_openverse",
                title=str(item.get("title") or ""),
                url=image_url,
                landing_url=str(item.get("foreign_landing_url") or item.get("url") or ""),
                query=query,
                source=str(item.get("source") or "Openverse"),
                license_hint=str(item.get("license") or ""),
                attribution=str(item.get("creator") or ""),
                width=int(item.get("width") or 0),
                height=int(item.get("height") or 0),
            )
        )
    return items


def _score_text(query: str, *values: str) -> float:
    tokens = [item.lower() for item in re.findall(r"[a-zA-Z][a-zA-Z0-9_+-]{2,}|[\u4e00-\u9fff]{2,}", query)]
    tokens = [token for token in tokens if token not in {"pdf", "paper", "论文", "下载", "开放获取"}]
    if not tokens:
        return 0.5
    content = " ".join(values).lower()
    matches = sum(1 for token in tokens[:8] if token in content)
    return round(matches / max(1, min(len(tokens), 8)), 3)


def _arxiv_pdf_url(entry: ET.Element) -> str:
    for link in entry.findall("{http://www.w3.org/2005/Atom}link"):
        if link.attrib.get("title") == "pdf" and link.attrib.get("href"):
            return str(link.attrib["href"]).replace("/abs/", "/pdf/")
    entry_id = entry.findtext("{http://www.w3.org/2005/Atom}id") or ""
    if "/abs/" in entry_id:
        return entry_id.replace("/abs/", "/pdf/")
    return ""


def arxiv_candidates(query: str, *, timeout: int, limit: int = 3) -> list[dict[str, Any]]:
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(
        {"search_query": f"all:{query}", "start": 0, "max_results": max(1, min(limit, 5))}
    )
    text = _open_text(url, timeout=timeout, accept="application/atom+xml")
    root = ET.fromstring(text)
    items: list[dict[str, Any]] = []
    for entry in root.findall("{http://www.w3.org/2005/Atom}entry")[:limit]:
        title = " ".join((entry.findtext("{http://www.w3.org/2005/Atom}title") or "").split())
        summary = " ".join((entry.findtext("{http://www.w3.org/2005/Atom}summary") or "").split())
        url_pdf = _arxiv_pdf_url(entry)
        if not url_pdf:
            continue
        items.append(
            {
                "source_id": "academic_arxiv",
                "title": title,
                "summary": summary[:800],
                "url": url_pdf,
                "landing_url": entry.findtext("{http://www.w3.org/2005/Atom}id") or "",
                "score": _score_text(query, title, summary),
                "open_access": True,
                "license_hint": "arxiv_open_access_repository",
            }
        )
    return items


def openalex_candidates(query: str, *, timeout: int, limit: int = 5) -> list[dict[str, Any]]:
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(
        {"search": query, "filter": "is_oa:true", "per-page": max(1, min(limit, 10))}
    )
    payload = json.loads(_open_text(url, timeout=timeout))
    items: list[dict[str, Any]] = []
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "")
        oa = item.get("open_access") if isinstance(item.get("open_access"), dict) else {}
        primary = item.get("primary_location") if isinstance(item.get("primary_location"), dict) else {}
        pdf_url = str(oa.get("oa_url") or primary.get("pdf_url") or "").strip()
        if not pdf_url:
            continue
        abstract = ""
        if isinstance(item.get("abstract_inverted_index"), dict):
            pairs: list[tuple[int, str]] = []
            for word, positions in item["abstract_inverted_index"].items():
                if isinstance(positions, list):
                    pairs.extend((int(pos), str(word)) for pos in positions[:20] if isinstance(pos, int))
            abstract = " ".join(word for _pos, word in sorted(pairs)[:80])
        items.append(
            {
                "source_id": "academic_openalex",
                "title": title,
                "summary": abstract[:800],
                "url": pdf_url,
                "landing_url": str(item.get("doi") or item.get("id") or ""),
                "score": _score_text(query, title, abstract),
                "open_access": bool(oa.get("is_oa", True)),
                "license_hint": str(primary.get("license") or oa.get("oa_status") or ""),
            }
        )
    return items


def choose_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        return {}
    return sorted(candidates, key=lambda item: (-float(item.get("quality_score", item.get("score") or 0.0)), str(item.get("source_id") or "")))[0]


def image_candidates(request: dict[str, Any], *, timeout: int, limit: int = 12) -> tuple[str, list[dict[str, Any]], list[dict[str, str]]]:
    query = image_query_terms(request)
    errors: list[dict[str, str]] = []
    candidates: list[dict[str, Any]] = []
    budget = ResourceExecutionBudget.start(timeout)
    for source_id, func in (
        ("image_webpage_assets", lambda q, timeout: webpage_image_candidates(request, q, timeout=timeout, limit=limit)),
        ("image_wikimedia_commons", lambda q, timeout: wikimedia_image_candidates(q, timeout=timeout, limit=limit)),
        ("image_openverse", lambda q, timeout: openverse_image_candidates(q, timeout=timeout, limit=limit)),
    ):
        call_timeout = budget.timeout_seconds(cap=timeout)
        if call_timeout <= 0:
            errors.append({"source_id": source_id, "error_class": "total_budget_exhausted", "reason": "total source-selection budget exhausted"})
            break
        try:
            candidates.extend(func(query, call_timeout))
        except Exception as exc:
            errors.append({"source_id": source_id, "error_class": type(exc).__name__, "reason": str(exc)[:300]})
        candidates = rank_candidates(candidates, resource_kind="image", constraints=quality_constraints_from_request(request))
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in candidates:
            url = str(item.get("url") or "")
            if url and url not in seen:
                seen.add(url)
                deduped.append(item)
        candidates = deduped[:limit]
        if len(candidates) >= limit:
            break
    return query, candidates, errors


def generic_download_candidates(
    request: dict[str, Any],
    *,
    timeout: int,
    resource_kind: str,
    limit: int = 12,
) -> tuple[str, list[dict[str, Any]], list[dict[str, str]]]:
    query = query_terms(request)
    errors: list[dict[str, str]] = []
    candidates: list[dict[str, Any]] = []
    budget = ResourceExecutionBudget.start(timeout)
    for source_id, func in (
        ("github_release_assets", lambda call_timeout: github_release_candidates(request, query, timeout=call_timeout, resource_kind=resource_kind, limit=limit)),
        ("huggingface_files", lambda call_timeout: huggingface_candidates(request, query, timeout=call_timeout, resource_kind=resource_kind, limit=limit)),
        ("zenodo_files", lambda call_timeout: zenodo_candidates(request, query, timeout=call_timeout, resource_kind=resource_kind, limit=limit)),
    ):
        call_timeout = budget.timeout_seconds(cap=timeout)
        if call_timeout <= 0:
            errors.append({"source_id": source_id, "error_class": "total_budget_exhausted", "reason": "total source-selection budget exhausted"})
            break
        try:
            candidates.extend(func(call_timeout))
        except Exception as exc:
            errors.append({"source_id": source_id, "error_class": type(exc).__name__, "reason": str(exc)[:300]})
    call_timeout = budget.timeout_seconds(cap=timeout)
    if call_timeout <= 0:
        errors.append({"source_id": "webpage_download_assets", "error_class": "total_budget_exhausted", "reason": "total source-selection budget exhausted"})
    else:
        try:
            candidates.extend(webpage_download_candidates(request, query, timeout=call_timeout, resource_kind=resource_kind, limit=limit))
        except Exception as exc:
            errors.append({"source_id": "webpage_download_assets", "error_class": type(exc).__name__, "reason": str(exc)[:300]})
    candidates = rank_candidates(candidates, resource_kind=resource_kind, constraints=quality_constraints_from_request(request))
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in candidates:
        url = str(item.get("url") or "")
        if url and url not in seen:
            seen.add(url)
            deduped.append(item)
    return query, deduped[:limit], errors


def _github_repo_from_request(request: dict[str, Any]) -> tuple[str, str] | None:
    text = str(request.get("url") or request.get("target") or request.get("task") or "").strip()
    if not text:
        return None
    parsed = urllib.parse.urlparse(text if "://" in text else f"https://github.com/{text}")
    if parsed.netloc.lower() not in {"github.com", "www.github.com", "api.github.com"}:
        return None
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if parsed.netloc.lower() == "api.github.com" and len(parts) >= 3 and parts[0] == "repos":
        return parts[1], parts[2]
    if len(parts) >= 2:
        return parts[0], parts[1]
    return None


def github_release_candidates(
    request: dict[str, Any],
    query: str,
    *,
    timeout: int,
    resource_kind: str,
    limit: int = 12,
) -> list[dict[str, Any]]:
    repo = _github_repo_from_request(request)
    if not repo:
        return []
    owner, name = repo
    metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
    tag = str(metadata.get("github_release_tag") or "").strip()
    path = f"/repos/{owner}/{name}/releases/tags/{urllib.parse.quote(tag)}" if tag else f"/repos/{owner}/{name}/releases/latest"
    payload = _open_json(f"https://api.github.com{path}", timeout=timeout)
    assets = payload.get("assets") if isinstance(payload.get("assets"), list) else []
    items: list[dict[str, Any]] = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        url = str(asset.get("browser_download_url") or "")
        if not _looks_like_common_download_url(url, resource_kind):
            continue
        items.append(
            _download_candidate(
                source_id="github_release_assets",
                title=str(asset.get("name") or ""),
                url=url,
                landing_url=str(payload.get("html_url") or f"https://github.com/{owner}/{name}/releases"),
                query=query,
                source=f"github:{owner}/{name}",
                resource_kind=resource_kind,
                license_hint="repository_license_applies",
                summary=str(payload.get("name") or payload.get("tag_name") or ""),
                size=int(asset.get("size") or 0),
            )
        )
        if len(items) >= limit:
            break
    return items


def _huggingface_repo_from_request(request: dict[str, Any], resource_kind: str) -> tuple[str, str] | None:
    metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
    explicit = str(metadata.get("huggingface_repo") or metadata.get("hf_repo") or "").strip()
    text = explicit or str(request.get("url") or request.get("target") or "").strip()
    if not text:
        return None
    repo_type = "datasets" if resource_kind == "dataset" else "models"
    parsed = urllib.parse.urlparse(text if "://" in text else f"https://huggingface.co/{text}")
    if parsed.netloc.lower() not in {"huggingface.co", "www.huggingface.co"}:
        return None
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if parts and parts[0] in {"datasets", "spaces", "models"}:
        repo_type = parts[0]
        parts = parts[1:]
    if len(parts) >= 2:
        return repo_type, "/".join(parts[:2])
    return None


def huggingface_candidates(
    request: dict[str, Any],
    query: str,
    *,
    timeout: int,
    resource_kind: str,
    limit: int = 12,
) -> list[dict[str, Any]]:
    repo = _huggingface_repo_from_request(request, resource_kind)
    if not repo:
        return []
    repo_type, repo_id = repo
    api_type = "datasets" if repo_type == "datasets" else "models"
    payload = _open_json(f"https://huggingface.co/api/{api_type}/{repo_id}", timeout=timeout)
    siblings = payload.get("siblings") if isinstance(payload.get("siblings"), list) else []
    items: list[dict[str, Any]] = []
    for sibling in siblings:
        if not isinstance(sibling, dict):
            continue
        filename = str(sibling.get("rfilename") or "")
        url = f"https://huggingface.co/{'datasets/' if api_type == 'datasets' else ''}{repo_id}/resolve/main/{urllib.parse.quote(filename)}"
        if not _looks_like_common_download_url(url, resource_kind):
            continue
        items.append(
            _download_candidate(
                source_id="huggingface_files",
                title=filename,
                url=url,
                landing_url=f"https://huggingface.co/{'datasets/' if api_type == 'datasets' else ''}{repo_id}",
                query=query,
                source=f"huggingface:{repo_id}",
                resource_kind=resource_kind,
                license_hint=str(payload.get("cardData", {}).get("license") if isinstance(payload.get("cardData"), dict) else ""),
                summary=str(payload.get("pipeline_tag") or payload.get("id") or ""),
                size=int(sibling.get("size") or 0),
            )
        )
        if len(items) >= limit:
            break
    return items


def _zenodo_record_id(request: dict[str, Any]) -> str:
    metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
    explicit = str(metadata.get("zenodo_record_id") or "").strip()
    if explicit:
        return explicit
    text = str(request.get("url") or request.get("target") or "").strip()
    match = re.search(r"zenodo\.org/(?:records|record)/(\d+)", text)
    return match.group(1) if match else ""


def zenodo_candidates(
    request: dict[str, Any],
    query: str,
    *,
    timeout: int,
    resource_kind: str,
    limit: int = 12,
) -> list[dict[str, Any]]:
    record_id = _zenodo_record_id(request)
    if not record_id:
        return []
    payload = _open_json(f"https://zenodo.org/api/records/{record_id}", timeout=timeout)
    files = payload.get("files") if isinstance(payload.get("files"), list) else []
    items: list[dict[str, Any]] = []
    for file_item in files:
        if not isinstance(file_item, dict):
            continue
        links = file_item.get("links") if isinstance(file_item.get("links"), dict) else {}
        url = str(links.get("self") or links.get("download") or "")
        key = str(file_item.get("key") or "")
        if not url or not (_looks_like_common_download_url(url, resource_kind) or _extension_allowed(key, resource_kind)):
            continue
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        items.append(
            _download_candidate(
                source_id="zenodo_files",
                title=key,
                url=url,
                landing_url=str(payload.get("links", {}).get("html") if isinstance(payload.get("links"), dict) else ""),
                query=query,
                source=f"zenodo:{record_id}",
                resource_kind=resource_kind,
                license_hint=str(metadata.get("license", {}).get("id") if isinstance(metadata.get("license"), dict) else metadata.get("license") or ""),
                summary=str(metadata.get("title") or ""),
                size=int(file_item.get("size") or 0),
            )
        )
        if len(items) >= limit:
            break
    return items


def execute_source_selection(request: dict[str, Any], route: dict[str, Any], *, timeout: int = 20) -> dict[str, Any]:
    plan = candidate_source_plan(request, route)
    kind = str(plan.get("resource_kind") or "")
    if kind == "image":
        query, candidates, errors = image_candidates(request, timeout=timeout)
        constraints = quality_constraints_from_request(request)
        candidates, skipped = filter_ranked_candidates(candidates, resource_kind="image", constraints=constraints)
        selected = choose_candidate(candidates)
        metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
        if not selected:
            return _json_result(
                ok=False,
                status="degraded",
                result_kind="source_selection",
                error_class="no_image_candidate",
                reason="no image source returned a usable candidate",
                query=query,
                attempted_sources=errors,
                source_strategy=plan,
                next_action="refine_image_query_or_provide_source_page",
            )
        return _json_result(
            ok=True,
            status="completed",
            source="resource_source_executor",
            result_kind="source_selection",
            resource_kind="image",
            query=query,
            selected_url=selected.get("url", ""),
            selected_name=selected.get("title", ""),
            selected_source_id=selected.get("source_id", ""),
            candidates=candidates[:12],
            skipped_candidates=skipped[:12],
            candidate_count=len(candidates),
            quality_summary=quality_summary(candidates, skipped),
            candidate_review_required=bool(metadata.get("candidate_review_before_materialization")),
            attempted_sources=errors,
            source_strategy=plan,
            content=json.dumps({"selected": selected, "candidates": candidates[:12]}, ensure_ascii=False),
            next_action="return_image_candidates_for_codex_review" if metadata.get("source_selection_only") else "materialize_selected_url",
        )
    if kind in {"audio", "dataset", "document", "generic_download", "model_artifact", "video"}:
        query, candidates, errors = generic_download_candidates(request, timeout=timeout, resource_kind=kind)
        constraints = quality_constraints_from_request(request)
        candidates, skipped = filter_ranked_candidates(candidates, resource_kind=kind, constraints=constraints)
        selected = choose_candidate(candidates)
        metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
        if not selected:
            return _json_result(
                ok=False,
                status="degraded",
                result_kind="source_selection",
                error_class="no_download_candidate",
                reason="no source page or explicit URL returned a usable downloadable candidate",
                query=query,
                resource_kind=kind,
                attempted_sources=errors,
                source_strategy=plan,
                next_action="provide_source_page_or_refine_resource_kind",
            )
        return _json_result(
            ok=True,
            status="completed",
            source="resource_source_executor",
            result_kind="source_selection",
            resource_kind=kind,
            query=query,
            selected_url=selected.get("url", ""),
            selected_name=selected.get("title", ""),
            selected_source_id=selected.get("source_id", ""),
            candidates=candidates[:12],
            skipped_candidates=skipped[:12],
            candidate_count=len(candidates),
            quality_summary=quality_summary(candidates, skipped),
            candidate_review_required=bool(metadata.get("candidate_review_before_materialization")),
            attempted_sources=errors,
            source_strategy=plan,
            content=json.dumps({"selected": selected, "candidates": candidates[:12]}, ensure_ascii=False),
            next_action="return_download_candidates_for_codex_review" if metadata.get("source_selection_only") else "materialize_selected_url",
        )
    if kind == "github_project":
        query = query_terms(request)
        metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
        errors: list[dict[str, str]] = []
        try:
            candidates = github_release_candidates(request, query, timeout=timeout, resource_kind="generic_download", limit=12)
        except Exception as exc:
            errors = [{"source_id": "github_release_assets", "error_class": type(exc).__name__, "reason": str(exc)[:300]}]
            candidates = []
        candidates, skipped = filter_ranked_candidates(candidates, resource_kind="github_project", constraints=quality_constraints_from_request(request))
        selected = choose_candidate(candidates)
        if not selected:
            return _json_result(
                ok=False,
                status="handoff_required",
                result_kind="source_selection",
                reason="github_release_asset_candidate_unavailable",
                resource_kind=kind,
                attempted_sources=errors,
                source_strategy=plan,
                next_action="use_github_owner_metadata_or_refine_release_tag",
            )
        return _json_result(
            ok=True,
            status="completed",
            source="resource_source_executor",
            result_kind="source_selection",
            resource_kind=kind,
            query=query,
            selected_url=selected.get("url", ""),
            selected_name=selected.get("title", ""),
            selected_source_id=selected.get("source_id", ""),
            candidates=candidates[:12],
            skipped_candidates=skipped[:12],
            candidate_count=len(candidates),
            quality_summary=quality_summary(candidates, skipped),
            candidate_review_required=bool(metadata.get("candidate_review_before_materialization")),
            attempted_sources=errors,
            source_strategy=plan,
            content=json.dumps({"selected": selected, "candidates": candidates[:12]}, ensure_ascii=False),
            next_action="return_github_release_candidates_for_codex_review" if metadata.get("source_selection_only") else "materialize_selected_url",
        )
    if kind == "generic_web":
        query = query_terms(request)
        candidates = generic_web_reference_catalog(query)
        constraints = quality_constraints_from_request(request)
        candidates = rank_candidates(candidates, resource_kind="generic_web", constraints=constraints)
        candidates, skipped = filter_ranked_candidates(candidates, resource_kind="generic_web", constraints=constraints)
        selected = choose_candidate(candidates)
        if not selected:
            capability = plan.get("execution_capability") if isinstance(plan.get("execution_capability"), dict) else {}
            return _json_result(
                ok=False,
                status="degraded",
                result_kind="source_selection",
                error_class="curated_catalog_no_match",
                reason="curated generic web catalog did not match; continue to the registered generic_search owner adapter",
                query=query,
                resource_kind=kind,
                source_strategy=plan,
                execution_capability=capability,
                required_capability=capability.get("required_capability_for_arbitrary_search") or "generic_web_search_owner_adapter",
                available_backend="curated_reference_catalog",
                available_owner_adapter=capability.get("registered_owner_adapter") or "generic_search",
                next_action="continue_resource_layer_with_registered_search_owner",
            )
        return _json_result(
            ok=True,
            status="completed",
            source="resource_source_executor",
            result_kind="source_selection",
            resource_kind=kind,
            query=query,
            selected_url=selected.get("url", ""),
            selected_name=selected.get("title", ""),
            selected_source_id=selected.get("source_id", ""),
            candidates=candidates[:8],
            skipped_candidates=skipped[:8],
            candidate_count=len(candidates),
            quality_summary=quality_summary(candidates, skipped),
            source_strategy=plan,
            content=json.dumps({"selected": selected, "candidates": candidates[:8]}, ensure_ascii=False),
            next_action="consume_source_selection",
        )
    if kind == "documentation":
        capability = plan.get("execution_capability") if isinstance(plan.get("execution_capability"), dict) else {}
        owner = str(capability.get("registered_owner_adapter") or "").strip()
        evidence = plan.get("classification_evidence") if isinstance(plan.get("classification_evidence"), dict) else {}
        return _json_result(
            ok=False,
            status="degraded",
            result_kind="source_selection",
            error_class="documentation_owner_execution_required",
            reason="documentation request is classified; continue with the registered documentation owner adapter",
            resource_kind=kind,
            source_strategy=plan,
            execution_capability=capability,
            available_owner_adapter=owner,
            documentation_route_kind=evidence.get("documentation_route_kind", ""),
            official_domains=evidence.get("official_domains", []),
            next_action="continue_resource_layer_with_registered_documentation_owner",
        )
    if kind != "academic_paper":
        return _json_result(
            ok=False,
            status="handoff_required",
            result_kind="source_selection",
            reason="source_executor_not_implemented_for_resource_kind",
            resource_kind=kind,
            source_strategy=plan,
            next_action="use_source_strategy_plan_or_add_adapter",
        )
    query = query_terms(request)
    errors: list[dict[str, str]] = []
    candidates: list[dict[str, Any]] = []
    budget = ResourceExecutionBudget.start(timeout)
    for source_id, func in (("academic_arxiv", arxiv_candidates), ("academic_openalex", openalex_candidates)):
        call_timeout = budget.timeout_seconds(cap=timeout)
        if call_timeout <= 0:
            errors.append({"source_id": source_id, "error_class": "total_budget_exhausted", "reason": "total source-selection budget exhausted"})
            break
        try:
            candidates.extend(func(query, timeout=call_timeout))
        except Exception as exc:
            errors.append({"source_id": source_id, "error_class": type(exc).__name__, "reason": str(exc)[:300]})
    candidates, skipped = filter_ranked_candidates(candidates, resource_kind="academic_paper", constraints=quality_constraints_from_request(request))
    selected = choose_candidate(candidates)
    if not selected:
        return _json_result(
            ok=False,
            status="degraded",
            result_kind="source_selection",
            error_class="no_source_candidate",
            reason="no candidate source returned a downloadable open-access URL",
            query=query,
            attempted_sources=errors,
            source_strategy=plan,
            next_action="refine_query_or_try_next_source_candidate",
        )
    return _json_result(
        ok=True,
        status="completed",
        source="resource_source_executor",
        result_kind="source_selection",
        query=query,
        selected_url=selected.get("url", ""),
        selected_name=selected.get("title", ""),
        selected_source_id=selected.get("source_id", ""),
        candidates=candidates[:8],
        skipped_candidates=skipped[:8],
        quality_summary=quality_summary(candidates, skipped),
        attempted_sources=errors,
        source_strategy=plan,
        content=json.dumps({"selected": selected, "candidates": candidates[:5]}, ensure_ascii=False),
        next_action="materialize_selected_url" if request.get("need_materialization") else "consume_source_selection",
    )


def validate() -> dict[str, Any]:
    request = {"task": "查找并下载一篇关于人工智能的中国区论文", "target": "中国 人工智能 论文 PDF", "need_materialization": True}
    route = {"primary_tool": "resource_router", "intent": "external_dependency", "source_kind": "unknown"}
    plan = candidate_source_plan(request, route)
    image_request = {"task": "下载十张关于华为总部的不同图片", "target": "华为总部 Huawei headquarters photos", "need_materialization": True}
    image_route = {"primary_tool": "resource_router", "intent": "external_dependency", "source_kind": "unknown"}
    image_plan = candidate_source_plan(image_request, image_route)
    generic_request = {
        "task": "research mature practices",
        "target": "observability workflow routing retries circuit breakers sqlite indexed state",
    }
    generic_route = {"primary_tool": "resource_router", "intent": "external_dependency", "source_kind": "unknown"}
    generic_result = execute_source_selection(generic_request, generic_route, timeout=5)
    openai_request = {
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
    }
    openai_result = execute_source_selection(openai_request, generic_route, timeout=5)
    unmatched_request = {
        "task": "locate a source outside the curated catalog",
        "target": "unregistered-domain-specific-resource",
        "metadata": {
            "resource_kind_hint": "generic_web",
            "custom_delegation": {"constraints": {"site_or_domain": "unregistered.invalid"}},
        },
    }
    unmatched_result = execute_source_selection(unmatched_request, generic_route, timeout=5)
    return {
        "schema": "resource_source_executor.validate.v1",
        "ok": plan.get("resource_kind") == "academic_paper"
        and bool(query_terms(request))
        and image_plan.get("resource_kind") == "image"
        and bool(image_query_terms(image_request))
        and generic_result.get("status") == "completed"
        and int(generic_result.get("candidate_count") or 0) >= 3
        and openai_result.get("status") == "completed"
        and str(openai_result.get("selected_source_id") or "").startswith("openai_")
        and unmatched_result.get("status") == "degraded"
        and unmatched_result.get("error_class") == "curated_catalog_no_match"
        and unmatched_result.get("available_owner_adapter") == "generic_search",
        "query": query_terms(request),
        "image_query": image_query_terms(image_request),
        "generic_web_candidate_count": generic_result.get("candidate_count"),
        "openai_selected_source_id": openai_result.get("selected_source_id"),
        "unmatched_generic_web_status": unmatched_result.get("status"),
        "unmatched_generic_web_required_capability": unmatched_result.get("required_capability"),
        "source_strategy_ok": bool(plan.get("ok")),
        "image_source_strategy_ok": bool(image_plan.get("ok")),
        "writes_files": False,
        "writes_remote_state": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute read-only resource source selection.")
    parser.add_argument("command", choices=("execute", "validate"))
    parser.add_argument("--request-json", default="{}")
    parser.add_argument("--route-json", default="{}")
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()
    if args.command == "validate":
        payload = validate()
    else:
        payload = execute_source_selection(json.loads(args.request_json), json.loads(args.route_json), timeout=args.timeout)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("ok") or payload.get("status") in {"handoff_required", "degraded"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
