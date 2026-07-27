#!/usr/bin/env python3
"""Default desktop resource-library paths for resource acquisition.

Ownership: default path selection for user-visible acquired resources.
Non-goals: downloading resources, classifying owner MCPs, or mutating global
system settings.
State behavior: read-only path calculation; callers create directories when
they actually write.
Caller context: resource_cli, resource_broker, and Codex delegation builders.
"""

from __future__ import annotations

import urllib.parse
from pathlib import Path

from intent_routing import matched_terms


RESOURCE_LIBRARY_ROOT = Path.home() / "Desktop" / "Codex资源库"
RESOURCE_REQUEST_STORE_ROOT = RESOURCE_LIBRARY_ROOT / "文档" / "系统维护" / "资源获取"
FALLBACK_CATEGORY = "临时待分类"

IMAGE_EXTENSIONS = {".avif", ".bmp", ".gif", ".heic", ".jpeg", ".jpg", ".png", ".svg", ".tif", ".tiff", ".webp"}
VIDEO_EXTENSIONS = {".avi", ".flv", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".webm", ".wmv"}
AUDIO_EXTENSIONS = {".aac", ".ape", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".wma"}
DOCUMENT_EXTENSIONS = {".csv", ".doc", ".docx", ".epub", ".md", ".pdf", ".ppt", ".pptx", ".txt", ".xls", ".xlsx"}
PACKAGE_EXTENSIONS = {".7z", ".appx", ".deb", ".dmg", ".exe", ".jar", ".msi", ".nupkg", ".pkg", ".rar", ".tar", ".tgz", ".whl", ".zip"}
SCRIPT_EXTENSIONS = {".bat", ".cmd", ".ps1", ".py", ".sh"}
PAPER_TERMS = {
    "arxiv",
    "doi",
    "学术",
    "开放获取",
    "期刊",
    "会议",
    "论文",
}
PAPER_WORD_TERMS = {
    "academic",
    "conference",
    "journal",
    "paper",
    "proceedings",
    "scholar",
}
PAPER_PHRASE_TERMS = {"open access"}


def extension_from_resource(*, name: str = "", url: str = "", path: str = "") -> str:
    for value in (name, path):
        suffix = Path(str(value or "")).suffix.lower()
        if suffix:
            return suffix
    if url:
        parsed = urllib.parse.urlparse(url)
        return Path(parsed.path).suffix.lower()
    return ""


def has_paper_signal(*, name: str = "", url: str = "", path: str = "", task: str = "") -> bool:
    text = " ".join(str(value or "") for value in (name, url, path, task)).lower()
    return bool(matched_terms(text, tuple(PAPER_TERMS | PAPER_PHRASE_TERMS | PAPER_WORD_TERMS)))


def category_for_resource(*, name: str = "", url: str = "", path: str = "", task: str = "") -> str:
    suffix = extension_from_resource(name=name, url=url, path=path)
    if has_paper_signal(name=name, url=url, path=path, task=task):
        return "论文"
    if suffix in IMAGE_EXTENSIONS:
        return "图片"
    if suffix in VIDEO_EXTENSIONS:
        return "视频"
    if suffix in AUDIO_EXTENSIONS:
        return "音频"
    if suffix in DOCUMENT_EXTENSIONS:
        return "文档"
    if suffix in PACKAGE_EXTENSIONS:
        return "安装包"
    if suffix in SCRIPT_EXTENSIONS:
        return "脚本工具"
    return FALLBACK_CATEGORY


def default_artifact_dir(*, name: str = "", url: str = "", path: str = "", task: str = "") -> Path:
    return RESOURCE_LIBRARY_ROOT / category_for_resource(name=name, url=url, path=path, task=task)


def validate() -> dict[str, object]:
    checks = [
        {
            "name": "image_routes_to_picture_library",
            "ok": default_artifact_dir(name="sample.jpg") == RESOURCE_LIBRARY_ROOT / "图片",
        },
        {
            "name": "unknown_routes_to_inbox",
            "ok": default_artifact_dir(name="sample") == RESOURCE_LIBRARY_ROOT / FALLBACK_CATEGORY,
        },
        {
            "name": "paper_pdf_routes_to_paper_library",
            "ok": default_artifact_dir(name="ai-paper.pdf", task="中国区人工智能论文") == RESOURCE_LIBRARY_ROOT / "论文",
        },
        {
            "name": "plain_pdf_routes_to_document_library",
            "ok": default_artifact_dir(name="manual.pdf") == RESOURCE_LIBRARY_ROOT / "文档",
        },
        {
            "name": "wallpaper_does_not_match_paper",
            "ok": default_artifact_dir(name="wallpaper.pdf") == RESOURCE_LIBRARY_ROOT / "文档",
        },
        {
            "name": "request_store_under_resource_library",
            "ok": str(RESOURCE_REQUEST_STORE_ROOT).startswith(str(RESOURCE_LIBRARY_ROOT)),
        },
    ]
    return {
        "schema": "resource_library_paths.validate.v1",
        "ok": all(bool(item["ok"]) for item in checks),
        "resource_library_root": str(RESOURCE_LIBRARY_ROOT),
        "request_store_root": str(RESOURCE_REQUEST_STORE_ROOT),
        "checks": checks,
    }


if __name__ == "__main__":
    import json

    print(json.dumps(validate(), ensure_ascii=False, sort_keys=True))
