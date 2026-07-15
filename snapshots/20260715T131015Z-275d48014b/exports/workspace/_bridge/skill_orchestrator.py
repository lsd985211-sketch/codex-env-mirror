#!/usr/bin/env python3
"""Skill routing, gap detection, and usage evidence for this workspace.

The orchestrator is intentionally conservative:

- `plan`, `snapshot`, `doctor`, `validate`, and `metrics` do not modify skill
  sources; they may refresh the derived lifecycle SQLite index.
- MySkills remains the owning system for skill inventory and gated writes.
- This script can consume a MySkills inventory JSON file when Codex has one,
  but it can also fall back to local SKILL.md metadata so routing still works
  when the MCP namespace is unavailable in the current turn.
- Usage logging is opt-in through `record-usage`; it stores routing evidence,
  not task content or secrets.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from _bridge.shared.cli_contract import enum_arg, normalize_enum_value  # noqa: E402
from _bridge.shared.json_cli import now_iso, read_text  # noqa: E402
from _bridge.intent_routing import IntentRule, rank_intents, term_matches  # noqa: E402
from _bridge import skill_active_catalog, skill_lifecycle_governance as skill_lifecycle  # noqa: E402
from _bridge import skill_lifecycle_state  # noqa: E402

BRIDGE = ROOT / "_bridge"
RUNTIME = BRIDGE / "runtime" / "skill_orchestrator"
USAGE_LOG = RUNTIME / "skill_usage.jsonl"
DEFAULT_MYSKILLS_INVENTORY = RUNTIME / "myskills_inventory.snapshot.json"
GLOBAL_SKILLS = Path.home() / ".codex" / "skills"
PLUGIN_CACHE = Path.home() / ".codex" / "plugins" / "cache"
MATRIX = BRIDGE / "docs" / "mcp_capability_matrix.md"
MEMORY_GOVERNANCE = BRIDGE / "memory_governance.py"
SKILL_USAGE_OUTCOMES = {"ok", "partial", "failed", "skipped"}

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


@dataclass(frozen=True)
class SkillDomain:
    key: str
    label: str
    keywords: tuple[str, ...]
    preferred_skills: tuple[str, ...]
    tools: tuple[str, ...]
    memory_queries: tuple[str, ...]
    missing_if_absent: tuple[str, ...] = ()


DOMAINS: tuple[SkillDomain, ...] = (
    SkillDomain(
        "workflow_governance",
        "workflow / global governance / coherence",
        (
            "工作机制",
            "工作流",
            "工作模式",
            "执行策略",
            "全局机制",
            "全局系统",
            "系统机制",
            "机制问题",
            "机制冲突",
            "治理机制",
            "职责冲突",
            "职责重叠",
            "职责边界",
            "职责划分",
            "旧机制",
            "残余机制",
            "旧机制残余",
            "冗余",
            "重复",
            "矛盾",
            "拮抗",
            "冲突",
            "互相矛盾",
            "互相拮抗",
            "不一致",
            "上下文消耗",
            "上下文预算",
            "精简",
            "workflow governance",
            "global governance",
            "coherence",
            "redundant",
            "contradiction",
            "conflict",
            "overlap",
            "legacy mechanism",
            "responsibility boundary",
        ),
        ("global-framework", "diagnose", "memory-systems", "mcp-builder"),
        ("workflow_orchestrator.py", "memory_router.py", "skill_orchestrator.py", "global_coherence_doctor.py"),
        ("workflow governance coherence", "global mechanism conflicts", "tool memory skill routing boundaries"),
        ("global-framework", "diagnose"),
    ),
    SkillDomain(
        "bridge",
        "mobile bridge / Weixin operations",
        ("桥接", "微信", "手机", "回发", "附件", "backup1", "backup2", "openclaw", "weixin", "mobile"),
        ("mobile-weixin-bridge-ops", "gui-app-weixin", "windows-codex-ops"),
        ("mobile-openclaw-bridge", "sqlite-bridge-ro", "gui-automation"),
        ("mobile bridge owned result recovery", "bridge capability tokens"),
        ("mobile-weixin-bridge-ops",),
    ),
    SkillDomain(
        "mcp_tools",
        "MCP / tool stability",
        ("mcp", "工具", "transport closed", "hub", "codegraph", "namespace", "当前turn", "stdio"),
        ("global-framework", "mcp-builder", "windows-codex-ops", "codegraph-ops"),
        ("local-mcp-hub", "mcp_session_doctor", "codegraph", "custom-slash-commands"),
        ("mcp tool layer stability", "tools mcp stability"),
        ("mcp-builder", "windows-codex-ops"),
    ),
    SkillDomain(
        "codex_runtime",
        "Codex CLI and Desktop runtime configuration",
        ("codex cli", "codex desktop", "provider", "模型配置", "模型列表", "推理强度", "config.toml", "codex配置"),
        ("codex-cli", "windows-codex-ops"),
        ("local-mcp-hub", "filesystem"),
        ("Codex runtime configuration", "provider and model projection"),
        ("codex-cli",),
    ),
    SkillDomain(
        "cli_harness",
        "CLI-Anything / agent-native CLI harnesses",
        (
            "cli-anything",
            "cli_hub",
            "cli-hub",
            "cli anything",
            "harness",
            "agent-native cli",
            "agent cli",
            "命令行封装",
            "cli封装",
            "工具封装",
        ),
        ("cli-anything", "global-framework", "mcp-builder"),
        ("cli-anything", "cli-hub", "cli_anything_governance.py"),
        ("CLI-Anything harness methodology", "agent-native CLI harness"),
        ("cli-anything",),
    ),
    SkillDomain(
        "office_native",
        "installed Microsoft Office native editing and rendering",
        (
            "本机 Word", "本机Word", "本机 Excel", "本机Excel", "本机 PowerPoint", "本机PowerPoint",
            "真实 Office", "Office COM", "真实分页", "公式重算", "原生PDF导出", "cli-anything-microsoft-office",
        ),
        ("cli-anything-microsoft-office", "office-craft"),
        ("cli-anything-microsoft-office", "filesystem"),
        ("installed Office editing", "native Office rendering and calculation"),
        ("cli-anything-microsoft-office",),
    ),
    SkillDomain(
        "memory",
        "memory / PMB / notes",
        ("记忆", "pmb", "note", "画像", "吸收", "临时笔记", "work-note", "memory"),
        ("memory-systems", "memory-checkpoint-ops", "self-improvement", "global-framework"),
        ("local-pmb-memory", "memory_governance.py"),
        ("memory system governance", "skills index"),
        ("memory-systems",),
    ),
    SkillDomain(
        "skills",
        "skills / MySkills / command templates",
        ("技能", "myskills", "skill", "场景", "缺少技能", "命令模板", "custom-slash", "slash"),
        ("global-framework", "skill-creator", "skill-analyzer", "self-improvement"),
        ("myskills", "custom-slash-commands", "local-pmb-memory"),
        ("skills index", "memory skill closeout"),
        ("global-framework", "skill-creator"),
    ),
    SkillDomain(
        "code_review_refactor",
        "code review / refactor governance",
        (
            "code review",
            "review",
            "审查",
            "评审",
            "代码审查",
            "重构",
            "refactor",
            "coderabbit",
            "外部审查",
            "合并前",
        ),
        ("global-framework", "diagnose", "codegraph-ops", "bug-hunt-swarm"),
        ("codegraph", "filesystem", "github", "code_maintainability.py"),
        ("external review workflow", "code refactor governance", "review feedback verification"),
        (),
    ),
    SkillDomain(
        "email",
        "mail / inbox / scheduler",
        ("邮箱", "邮件", "收件", "发件", "回信", "smtp", "imap", "inbox", "outbox", "mail", "email"),
        ("email-ops", "workflow-automator"),
        ("email scheduler", "sqlite-bridge-ro"),
        ("email scheduler maintenance", "email workflow"),
        ("email-ops",),
    ),
    SkillDomain(
        "github",
        "GitHub remote repository",
        ("github", "仓库", "repo", "readme", "pull request", "issue", "pr", "release", "tag", "actions", "gh", "远端"),
        ("github-ops", "global-framework"),
        ("github", "local-mcp-hub"),
        ("github remote repository", "github auth", "github release", "github app"),
        (),
    ),
    SkillDomain(
        "whitepaper_lifecycle",
        "evidence-to-whitepaper-to-publication lifecycle",
        (
            "白皮书",
            "whitepaper",
            "证据治理",
            "公网发布",
            "同步网站",
            "同步网页",
            "public site",
            "public website",
        ),
        ("whitepaper-pipeline", "doc-coauthoring", "office-craft", "github-ops"),
        ("resource layer", "office-craft", "github", "playwright"),
        ("whitepaper lifecycle", "evidence publication pipeline", "public whitepaper synchronization"),
        ("whitepaper-pipeline",),
    ),
    SkillDomain(
        "feishu",
        "Feishu / Lark documents and wiki",
        ("飞书", "feishu", "lark", "多维表格", "bitable", "飞书知识库", "飞书文档"),
        ("feishu-wiki",),
        ("resource layer", "network gateway"),
        ("Feishu Open API operations", "Feishu credential and permission handling"),
        ("feishu-wiki",),
    ),
    SkillDomain(
        "docs_files",
        "documents / PDF / Office",
        ("pdf", "docx", "xlsx", "ppt", "文档", "表格", "幻灯片", "markdown", "md"),
        ("office-craft", "pdf", "docx", "xlsx", "pptx", "presentation-craft"),
        ("markitdown", "filesystem"),
        ("document pdf external knowledge",),
        (),
    ),
    SkillDomain(
        "json_canvas",
        "JSON Canvas files",
        ("json canvas", ".canvas", "canvas 文件", "canvas流程图", "canvas 流程图", "无限画布"),
        ("json-canvas",),
        ("filesystem",),
        ("JSON Canvas format",),
        ("json-canvas",),
    ),
    SkillDomain(
        "obsidian_markdown",
        "Obsidian Markdown syntax",
        ("obsidian markdown", "wikilink", "callout", "双链", "块引用", "obsidian frontmatter"),
        ("obsidian-markdown", "obsidian"),
        ("filesystem",),
        ("Obsidian Markdown conventions",),
        ("obsidian-markdown",),
    ),
    SkillDomain(
        "obsidian_bases",
        "Obsidian Bases files",
        ("obsidian base", "obsidian bases", ".base", "base 表格", "base视图", "bases 视图"),
        ("obsidian-bases", "obsidian"),
        ("filesystem",),
        ("Obsidian Bases schema",),
        ("obsidian-bases",),
    ),
    SkillDomain(
        "slide_deck",
        "slide deck generation and presentation design",
        ("演示文稿", "幻灯片", "slide deck", "slides", "presentation", "ppt", "pptx"),
        ("baoyu-slide-deck", "presentation-craft", "pptx"),
        ("filesystem", "resource layer"),
        ("slide deck generation", "presentation design"),
        (),
    ),
    SkillDomain(
        "media_video",
        "programmatic video and media rendering",
        ("remotion", "视频", "video", "视频渲染", "字幕", "音画同步", "时间轴", "composition"),
        ("remotion-video", "ffmpeg-usage"),
        ("resource layer", "filesystem"),
        ("Remotion rendering", "video asset and output validation"),
        (),
    ),
    SkillDomain(
        "media_processing",
        "FFmpeg audio and video processing",
        ("ffmpeg", "ffprobe", "视频压缩", "视频转换", "音频提取", "视频拼接", "转码", "字幕烧录", "gif 制作"),
        ("ffmpeg-usage",),
        ("filesystem",),
        ("FFmpeg media processing",),
        ("ffmpeg-usage",),
    ),
    SkillDomain(
        "xhs_images",
        "Xiaohongshu infographic image series",
        ("小红书图片", "小红书配图", "小红书图文", "xhs images", "rednote", "小红书种草"),
        ("baoyu-xhs-images",),
        ("resource layer", "filesystem"),
        ("Xiaohongshu image series",),
        ("baoyu-xhs-images",),
    ),
    SkillDomain(
        "deepl_translation",
        "DeepL and XLIFF translation",
        ("deepl", "xliff", "deepl api", "deepl翻译", "deepl 翻译"),
        ("deepl",),
        ("resource layer", "network gateway"),
        ("DeepL translation", "XLIFF translation"),
        ("deepl",),
    ),
    SkillDomain(
        "gui_browser",
        "GUI / browser automation",
        ("gui", "浏览器", "页面", "面板", "cdp", "playwright", "chrome", "ocr"),
        ("gui-automation", "playwright", "chrome:control-chrome", "agent-browser", "ppocrv5"),
        ("gui-automation", "playwright", "chrome-devtools"),
        ("gui automation", "cdp delivery route"),
        ("gui-automation",),
    ),
    SkillDomain(
        "minecraft",
        "Minecraft / Fabric / MCSManager",
        ("minecraft", "fabric", "mcsmanager", "mod", "模组", "服务器", "auto modpack"),
        ("workspace-knowledge", "mcsmanager-fabric-mc", "fabric-mc-architecture", "mc-mod-automation"),
        ("codegraph", "filesystem"),
        ("workspace mcsmanager operational",),
        ("workspace-knowledge", "mcsmanager-fabric-mc"),
    ),
    SkillDomain(
        "minecraft_commands",
        "Minecraft commands and mcfunction scripting",
        ("minecraft command", "mcfunction", "scoreboard", "tellraw", "命令方块", "选择器", "execute 命令", "nbt 命令"),
        ("minecraft-commands-scripting",),
        ("filesystem",),
        ("Minecraft command syntax",),
        ("minecraft-commands-scripting",),
    ),
    SkillDomain(
        "minecraft_plugin",
        "Minecraft server plugin development",
        ("paper plugin", "bukkit", "spigot", "服务端插件", "plugin.yml", "paper api"),
        ("minecraft-plugin-dev",),
        ("codegraph", "filesystem"),
        ("Minecraft plugin runtime",),
        ("minecraft-plugin-dev",),
    ),
    SkillDomain(
        "minecraft_multiloader",
        "Minecraft multi-loader mod architecture",
        ("multiloader", "multi-loader", "多加载器", "architectury", "fabric neoforge", "forge fabric", "跨加载器"),
        ("minecraft-multiloader", "fabric-mc-architecture"),
        ("codegraph", "filesystem"),
        ("Minecraft multi-loader architecture",),
        ("minecraft-multiloader",),
    ),
    SkillDomain(
        "web_research",
        "web research / external knowledge",
        ("联网", "搜索", "查资料", "网页", "research", "look up", "external knowledge"),
        ("agent-reach", "find-docs", "web-scraper", "context7-mcp", "openai-docs"),
        ("web", "context7", "microsoftdocs"),
        ("external source knowledge",),
        ("agent-reach",),
    ),
)

DOMAIN_SUPPRESSIONS: dict[str, set[str]] = {
    "whitepaper_lifecycle": {"docs_files", "github", "web_research"},
    "office_native": {"docs_files"},
    "feishu": {"docs_files"},
    "json_canvas": {"docs_files"},
    "obsidian_markdown": {"docs_files"},
    "obsidian_bases": {"docs_files"},
    "slide_deck": {"docs_files"},
    "minecraft_commands": {"minecraft"},
    "minecraft_plugin": {"minecraft"},
    "minecraft_multiloader": {"minecraft"},
    "media_processing": {"media_video"},
}

DOMAIN_SKILL_SUPPRESSIONS: dict[str, set[str]] = {
    "office_native": {"office-craft", "pdf", "docx", "xlsx", "pptx", "presentation-craft"},
    "obsidian_markdown": {"obsidian"},
    "obsidian_bases": {"obsidian"},
}

MIN_CANDIDATE_SCORE = 3
ROUTING_SKILL_NAMES = {"global-framework", "agent-reach", "office-craft"}
CONTEXT_SKILL_NAMES = {"workspace-knowledge", "memory-systems", "codegraph-ops", "context7-mcp"}
CONSTRAINT_SKILL_NAMES = {
    "diagnose",
    "fabric-mc-architecture",
    "security-best-practices",
    "security-threat-model",
    "karpathy-guidelines",
}

WHITEPAPER_SUBJECT_TERMS = ("白皮书", "whitepaper")
WHITEPAPER_LIFECYCLE_TERMS = (
    "搜集",
    "收集",
    "证据治理",
    "创建",
    "制作",
    "生成",
    "编写",
    "撰写",
    "更新",
    "维护",
    "同步",
    "全流程",
    "生命周期",
    "collect",
    "research",
    "create",
    "build",
    "write",
    "update",
    "maintain",
    "sync",
    "pipeline",
    "lifecycle",
)
WHITEPAPER_PUBLICATION_TERMS = (
    "发布",
    "公网",
    "网站",
    "网页",
    "站点",
    "public site",
    "public website",
    "publish",
    "publication",
    "deploy",
    "website",
)


def normalize_name(value: str) -> str:
    return re.sub(r"\s+", "-", str(value or "").strip().lower())


def normalize_path(value: str) -> str:
    try:
        return str(Path(value).resolve()).lower()
    except OSError:
        return str(value or "").lower()


def skill_layer(name: str) -> str:
    key = normalize_name(name)
    if key in ROUTING_SKILL_NAMES:
        return "routing"
    if key in CONTEXT_SKILL_NAMES:
        return "context"
    if key in CONSTRAINT_SKILL_NAMES:
        return "constraint"
    return "execution"


def select_layered_candidates(candidates: list[dict[str, Any]], max_skills: int) -> list[dict[str, Any]]:
    """Keep generic routes to one executor plus one non-execution helper."""
    explicit_executors = [
        row for row in candidates
        if row.get("layer") == "execution" and "name_mentioned" in (row.get("reasons") or [])
    ]
    if len(explicit_executors) > 1:
        return explicit_executors[:max_skills]

    execution = next((row for row in candidates if row.get("layer") == "execution"), None)
    helper = next((row for row in candidates if row.get("layer") != "execution"), None)
    if execution is None:
        return candidates[: min(max_skills, 2)]
    selected = {id(execution)}
    if helper is not None and len(selected) < max_skills:
        selected.add(id(helper))
    return [row for row in candidates if id(row) in selected]


def _matches_any(message: str, terms: tuple[str, ...]) -> bool:
    return any(term_matches(message, term) for term in terms)


def domain_is_qualified(domain_key: str, message: str) -> bool:
    if domain_key != "whitepaper_lifecycle":
        return True
    return all(
        (
            _matches_any(message, WHITEPAPER_SUBJECT_TERMS),
            _matches_any(message, WHITEPAPER_LIFECYCLE_TERMS),
            _matches_any(message, WHITEPAPER_PUBLICATION_TERMS),
        )
    )


def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    raw = text[3:end].strip().splitlines()
    out: dict[str, str] = {}
    current_key = ""
    current_lines: list[str] = []
    for line in raw:
        if re.match(r"^[A-Za-z_][A-Za-z0-9_-]*\s*:", line):
            if current_key:
                out[current_key] = "\n".join(current_lines).strip()
            key, value = line.split(":", 1)
            current_key = key.strip()
            current_lines = [value.strip().strip('"')]
        elif current_key:
            current_lines.append(line.strip())
    if current_key:
        out[current_key] = "\n".join(current_lines).strip()
    return out


def skill_roots() -> list[Path]:
    roots = [GLOBAL_SKILLS]
    seen: set[Path] = set()
    out: list[Path] = []
    for root in roots:
        try:
            resolved = root.resolve()
        except Exception:
            resolved = root
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append(root)
    return out


def local_skill_inventory() -> dict[str, dict[str, Any]]:
    skills: dict[str, dict[str, Any]] = {}
    for skill_file, source in skill_active_catalog.discover_active_skill_files(
        global_skills=GLOBAL_SKILLS,
        plugin_cache=PLUGIN_CACHE,
    ):
        text = read_text(skill_file)
        fm = parse_frontmatter(text)
        name = str(fm.get("name") or skill_file.parent.name).strip()
        display_name = name
        if source == "plugin":
            try:
                relative = skill_file.relative_to(PLUGIN_CACHE)
                package = relative.parts[1]
                display_name = f"{package}:{name}" if ":" not in name else name
            except (IndexError, ValueError):
                display_name = f"plugin:{name}" if ":" not in name else name
        key = normalize_name(display_name)
        if key in skills:
            continue
        skills[key] = {
            "name": display_name,
            "key": key,
            "description": str(fm.get("description") or "").strip(),
            "path": str(skill_file),
            "source": f"{source}_files",
            "scenarios": [],
            "needsAttention": False,
            "platforms": {},
        }
    return skills


def resolved_myskills_inventory_path(path: str = "") -> Path | None:
    if path:
        return Path(path)
    if DEFAULT_MYSKILLS_INVENTORY.exists():
        return DEFAULT_MYSKILLS_INVENTORY
    return None


def load_myskills_inventory(path: str = "") -> dict[str, dict[str, Any]]:
    payload_path = resolved_myskills_inventory_path(path)
    if payload_path is None:
        return {}
    if not payload_path.exists():
        return {}
    try:
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    skills = payload.get("skills") if isinstance(payload, dict) else []
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(skills, list):
        return result
    for item in skills:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        key = normalize_name(name)
        result[key] = {
            "id": item.get("id"),
            "name": name,
            "key": key,
            "description": str(item.get("description") or "").strip(),
            "path": "",
            "source": "myskills_inventory",
            "scenarios": item.get("scenarios") if isinstance(item.get("scenarios"), list) else [],
            "needsAttention": bool(item.get("needsAttention")),
            "platforms": item.get("platforms") if isinstance(item.get("platforms"), dict) else {},
            "missingOn": item.get("missingOn") if isinstance(item.get("missingOn"), list) else [],
        }
    return result


def merged_inventory(myskills_inventory: str = "") -> dict[str, dict[str, Any]]:
    local = local_skill_inventory()
    myskills = load_myskills_inventory(myskills_inventory)
    merged = dict(local)
    for key, item in myskills.items():
        base = dict(merged.get(key) or {})
        base.update({k: v for k, v in item.items() if v not in ("", [], {})})
        if not base.get("path") and key in local:
            base["path"] = local[key].get("path", "")
        if not base.get("description") and key in local:
            base["description"] = local[key].get("description", "")
        base["source"] = "myskills+local" if key in local else "myskills_inventory"
        merged[key] = base
    return merged


def classify(message: str, *, max_domains: int = 4) -> list[dict[str, Any]]:
    domains_by_key = {domain.key: domain for domain in DOMAINS}
    ranked = rank_intents(message, tuple(IntentRule(domain.key, domain.keywords) for domain in DOMAINS))
    ranked = [item for item in ranked if domain_is_qualified(str(item["key"]), message)]
    if not ranked:
        return []
    matched_keys = {str(item["key"]) for item in ranked}
    suppressed = {
        suppressed_key
        for matched_key in matched_keys
        for suppressed_key in DOMAIN_SUPPRESSIONS.get(matched_key, set())
    }
    ranked = [item for item in ranked if str(item["key"]) not in suppressed]
    return [
        {
            "domain": domains_by_key[str(item["key"])],
            "hits": list(item["hits"]),
            "score": int(item["score"]),
            "suppressed_negated_hits": list(item["suppressed_negated_hits"]),
        }
        for item in ranked[:max_domains]
    ]


def score_skill(skill: dict[str, Any], message: str, domains: list[dict[str, Any]]) -> tuple[int, list[str]]:
    text = str(message or "").lower()
    name = str(skill.get("name") or "")
    key = normalize_name(name)
    desc = str(skill.get("description") or "").lower()
    scenarios = " ".join(str(x) for x in skill.get("scenarios") or []).lower()
    score = 0
    reasons: list[str] = []
    if key and term_matches(text, key):
        score += 12
        reasons.append("name_mentioned")
    for item in domains:
        domain: SkillDomain = item["domain"]
        preferred = [normalize_name(x) for x in domain.preferred_skills]
        if key in set(preferred):
            score += 10
            score += max(0, 4 - preferred.index(key))
            reasons.append(f"domain_preferred:{domain.key}")
        for kw in item["hits"]:
            kw_l = str(kw).lower()
            if kw_l and (term_matches(desc, kw_l) or term_matches(scenarios, kw_l)):
                score += 2
                reasons.append(f"metadata_keyword:{kw}")
    if skill.get("needsAttention"):
        score -= 20
        reasons.append("needs_attention_penalty")
    if not skill.get("path"):
        score -= 1
        reasons.append("no_local_path")
    return score, reasons


def unique(values: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
        if len(out) >= limit:
            break
    return out


def prepare_routing_context(*, myskills_inventory: str = "") -> dict[str, Any]:
    """Refresh skill assets once so a message batch can reuse the same snapshot."""
    lifecycle_refresh = skill_lifecycle.refresh_incremental()
    inventory = merged_inventory(myskills_inventory)
    lifecycle_records_by_path = {
        normalize_path(str(row.get("path") or "")): row
        for row in lifecycle_refresh["records"]
    }
    quality_by_key = {
        normalize_name(name): item
        for name, item in skill_lifecycle_state.quality_summary().get("skills", {}).items()
    }
    discovered_skill_keys = {
        normalize_name(row["name"])
        for row in lifecycle_refresh["records"]
    }
    myskills_inventory_path = resolved_myskills_inventory_path(myskills_inventory)
    return {
        "lifecycle_refresh": lifecycle_refresh,
        "inventory": inventory,
        "lifecycle_records_by_path": lifecycle_records_by_path,
        "quality_by_key": quality_by_key,
        "discovered_skill_keys": discovered_skill_keys,
        "myskills_inventory_path": myskills_inventory_path,
    }


def build_plan(
    message: str,
    *,
    myskills_inventory: str = "",
    max_skills: int = 4,
    routing_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = routing_context or prepare_routing_context(myskills_inventory=myskills_inventory)
    lifecycle_refresh = context["lifecycle_refresh"]
    inventory = context["inventory"]
    lifecycle_records_by_path = context["lifecycle_records_by_path"]
    quality_by_key = context["quality_by_key"]
    discovered_skill_keys = context["discovered_skill_keys"]
    myskills_inventory_path = context["myskills_inventory_path"]
    domains = classify(message)
    selected_domains = [
        {"key": item["domain"].key, "label": item["domain"].label, "keyword_hits": item["hits"]}
        for item in domains
    ]
    suppressed_skill_keys = {
        normalize_name(skill_name)
        for item in domains
        for skill_name in DOMAIN_SKILL_SUPPRESSIONS.get(item["domain"].key, set())
    }
    candidates: list[dict[str, Any]] = []
    for skill in inventory.values():
        if normalize_name(str(skill.get("name") or "")) in suppressed_skill_keys:
            continue
        lifecycle_record = lifecycle_records_by_path.get(normalize_path(str(skill.get("path") or "")))
        if lifecycle_record and not lifecycle_record.get("routing_eligible", True):
            continue
        quality = quality_by_key.get(normalize_name(str(skill.get("name") or "")), {})
        score, reasons = score_skill(skill, message, domains)
        if score < MIN_CANDIDATE_SCORE:
            continue
        candidates.append(
            {
                "name": skill.get("name"),
                "score": score,
                "reasons": unique(reasons, 8),
                "path": skill.get("path", ""),
                "scenarios": skill.get("scenarios", []),
                "needsAttention": bool(skill.get("needsAttention")),
                "source": skill.get("source", ""),
                "skill_id": str((lifecycle_record or {}).get("skill_id") or ""),
                "admission_state": str((lifecycle_record or {}).get("admission_state") or "unregistered"),
                "trust_state": str((lifecycle_record or {}).get("trust_state") or "provisional"),
                "quality": quality,
                "quality_tiebreak": int(quality.get("ranking_signal") or 0),
                "layer": skill_layer(str(skill.get("name") or "")),
            }
        )
    candidates.sort(
        key=lambda item: (
            -int(item.get("score") or 0),
            -int(item.get("quality_tiebreak") or 0),
            str(item.get("name") or ""),
        )
    )
    selected = select_layered_candidates(candidates, max_skills)

    tools: list[str] = []
    memory_queries: list[str] = []
    expected_missing: list[str] = []
    for item in domains:
        domain: SkillDomain = item["domain"]
        tools.extend(domain.tools)
        memory_queries.extend(domain.memory_queries)
        expected_missing.extend(
            skill
            for skill in domain.missing_if_absent
            if normalize_name(skill) not in inventory and normalize_name(skill) not in discovered_skill_keys
        )

    gap_proposals = detect_gaps(message, inventory, domains, selected, expected_missing)
    return {
        "schema": "skill_orchestrator.plan.v1",
        "ok": True,
        "generated_at": now_iso(),
        "message_hash": hashlib.sha256(str(message or "").encode("utf-8")).hexdigest()[:16],
        "domains": selected_domains,
        "inventory": {
            "source": "myskills_inventory+local_files" if myskills_inventory_path else "local_files",
            "skill_count": len(inventory),
            "myskills_inventory_used": bool(myskills_inventory_path),
            "myskills_inventory_path": str(myskills_inventory_path) if myskills_inventory_path else "",
            "routing_quarantine_count": sum(
                1 for row in lifecycle_refresh["records"] if not row.get("routing_eligible", True)
            ),
            "trust_counts": {
                state: sum(1 for row in lifecycle_refresh["records"] if row.get("trust_state") == state)
                for state in ("trusted", "managed", "provisional", "deferred", "blocked")
            },
            "lifecycle_refresh": lifecycle_refresh["summary"],
            "lifecycle_state_db": lifecycle_refresh["state_db"],
        },
        "selected_skills": selected,
        "selection_policy": {
            "implicit_execution_limit": 1,
            "helper_limit": 1,
            "explicit_multi_execution_allowed": True,
            "rule": "Generic routes use one execution skill and one routing/context/constraint helper; explicitly named multiple execution skills remain available.",
        },
        "read_full_skill_md": [
            {"name": item.get("name"), "path": item.get("path"), "required": bool(item.get("path"))}
            for item in selected
        ],
        "memory": {
            "queries": unique(memory_queries, 6),
            "rule": "Use memory/PMB as routing evidence, not as a replacement for SKILL.md bodies.",
        },
        "tools": {
            "suggested": unique(tools, 8),
            "matrix": str(MATRIX),
            "rule": "Use owning MCP/tool after skill selection; record current-turn failures separately.",
        },
        "gap_proposals": gap_proposals,
        "closeout": {
            "record_usage_command": "python _bridge\\skill_orchestrator.py record-usage --task-kind <kind> --selected <skill,...> --used <skill,...> --outcome <ok|partial|failed> --notes <short reason>",
            "review_required_if": [
                "gap_proposals is non-empty",
                "selected skill lacks a local SKILL.md path",
                "a selected skill proved wrong or incomplete",
                "a repeated workflow had no matching skill or template",
            ],
        },
    }


def detect_gaps(
    message: str,
    inventory: dict[str, dict[str, Any]],
    domains: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    expected_missing: list[str],
) -> list[dict[str, Any]]:
    proposals: list[dict[str, Any]] = []
    domain_keys = {item["domain"].key for item in domains}
    if domains and not selected:
        proposals.append(
            {
                "kind": "missing_skill",
                "severity": "risk",
                "title": "No skill selected for a classified task",
                "reason": "The task matched a domain but no existing skill metadata scored above zero.",
                "next_step": "Review whether an existing skill description/scenario is too narrow before authoring a new skill.",
            }
        )
    for skill in expected_missing:
        proposals.append(
            {
                "kind": "missing_expected_skill",
                "severity": "risk",
                "title": f"Expected skill is absent: {skill}",
                "reason": "A high-value domain expects this reusable workflow skill, but it was not found in local/MySkills inventory.",
                "next_step": "Search catalog or draft a disabled skill only after user approval.",
            }
        )
    # Current local MySkills taxonomy drift observed in this workspace.
    if {"email", "skills"} & domain_keys and normalize_name("email-ops") in inventory:
        scenarios = [str(x) for x in inventory[normalize_name("email-ops")].get("scenarios") or []]
        if not scenarios:
            proposals.append(
                {
                    "kind": "scenario_assignment",
                    "severity": "advisory",
                    "skill": "email-ops",
                    "proposed_scenarios": ["邮件与调度", "维护生产力"],
                    "reason": "email-ops exists but has no MySkills scenario assignment, so task routing cannot discover it reliably from scenario taxonomy.",
                    "write_requires_approval": True,
                }
            )
    if {"bridge", "skills"} & domain_keys and normalize_name("mobile-weixin-bridge-ops") in inventory:
        scenarios = [str(x) for x in inventory[normalize_name("mobile-weixin-bridge-ops")].get("scenarios") or []]
        if "桥接运维" not in scenarios:
            proposals.append(
                {
                    "kind": "scenario_assignment",
                    "severity": "advisory",
                    "skill": "mobile-weixin-bridge-ops",
                    "proposed_scenarios": ["桥接运维", *scenarios[:3]],
                    "reason": "Bridge skill exists but is not assigned to the bridge operations scenario.",
                    "write_requires_approval": True,
                }
            )
    text = str(message or "").lower()
    if "myskills" in text or "技能" in text:
        proposals.append(
            {
                "kind": "usage_feedback_loop",
                "severity": "advisory",
                "title": "Skill usage evidence should be recorded for this task",
                "reason": "This task is about skill-system improvement; closeout should record planned vs actual skill use and any missed triggers.",
                "write_requires_approval": False,
            }
        )
    return proposals


def suggested_domains_for_skill(skill: dict[str, Any]) -> list[dict[str, Any]]:
    """Infer bounded routing domains for local skills absent from MySkills."""
    name = str(skill.get("name") or "")
    key = normalize_name(name)
    searchable = f"{name} {skill.get('description') or ''}".lower()
    ranked: list[tuple[int, SkillDomain]] = []
    for domain in DOMAINS:
        preferred = [normalize_name(item) for item in domain.preferred_skills]
        score = 8 if key in preferred else 0
        score += sum(1 for keyword in domain.keywords if term_matches(searchable, str(keyword).lower()))
        if score:
            ranked.append((score, domain))
    ranked.sort(key=lambda item: (-item[0], item[1].key))
    return [
        {"key": domain.key, "label": domain.label, "score": score}
        for score, domain in ranked[:2]
    ]


def routing_coverage(inv: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Expose domain ownership for every routed skill without writing MySkills."""
    routed: list[dict[str, Any]] = []
    manual_reference: list[str] = []
    myskills_assigned = 0
    for skill in sorted(inv.values(), key=lambda item: normalize_name(str(item.get("name") or ""))):
        scenarios = [str(item) for item in skill.get("scenarios") or [] if str(item).strip()]
        if scenarios:
            myskills_assigned += 1
            routed.append({"skill": skill.get("name"), "source": "myskills", "scenarios": scenarios})
            continue
        domains = suggested_domains_for_skill(skill)
        if domains:
            routed.append({"skill": skill.get("name"), "source": "local-inference", "domains": domains})
        else:
            manual_reference.append(str(skill.get("name") or ""))
    return {
        "schema": "skill_orchestrator.routing_coverage.v1",
        "skill_count": len(inv),
        "myskills_assigned_count": myskills_assigned,
        "locally_inferred_count": sum(1 for item in routed if item["source"] == "local-inference"),
        "manual_reference_count": len(manual_reference),
        "manual_reference": manual_reference,
        "routed": routed,
        "rule": "Local inference is a routing overlay only. MySkills remains the owner of persisted scenario assignments.",
    }


def snapshot(myskills_inventory: str = "") -> dict[str, Any]:
    lifecycle_refresh = skill_lifecycle.refresh_incremental()
    inv = merged_inventory(myskills_inventory)
    myskills_inventory_path = resolved_myskills_inventory_path(myskills_inventory)
    needs_attention = [item.get("name") for item in inv.values() if item.get("needsAttention")]
    local_paths = [Path(str(item.get("path"))) for item in inv.values() if item.get("path")]
    missing_paths = [str(path) for path in local_paths if not path.exists()]
    return {
        "schema": "skill_orchestrator.snapshot.v1",
        "ok": True,
        "generated_at": now_iso(),
        "read_only": True,
        "inventory": {
            "skill_count": len(inv),
            "myskills_inventory_used": bool(myskills_inventory_path),
            "myskills_inventory_path": str(myskills_inventory_path) if myskills_inventory_path else "",
            "needs_attention_count": len(needs_attention),
            "missing_local_path_count": len(missing_paths),
        },
        "paths": {
            "usage_log": str(USAGE_LOG),
            "global_skills": str(GLOBAL_SKILLS),
            "plugin_cache": str(PLUGIN_CACHE),
            "matrix": str(MATRIX),
            "memory_governance": str(MEMORY_GOVERNANCE),
        },
        "skill_roots": [{"path": str(root), "exists": root.exists()} for root in skill_roots()],
        "needs_attention": needs_attention[:20],
        "missing_local_paths": missing_paths[:20],
        "lifecycle_refresh": lifecycle_refresh["summary"],
        "lifecycle_state": skill_lifecycle_state_snapshot(),
    }


def skill_lifecycle_state_snapshot() -> dict[str, Any]:
    return skill_lifecycle_state.snapshot(recent_limit=0)


def scenario_plan(myskills_inventory: str = "") -> dict[str, Any]:
    inv = merged_inventory(myskills_inventory)
    proposals: list[dict[str, Any]] = []
    for message in ("邮箱收件回信任务 技能治理", "手机桥接回发任务 技能治理"):
        probes = build_plan(message, myskills_inventory=myskills_inventory, max_skills=8)
        for item in probes.get("gap_proposals", []):
            if item.get("kind") != "scenario_assignment":
                continue
            key = f"{item.get('skill')}::{','.join(str(x) for x in item.get('proposed_scenarios') or [])}"
            if key not in {f"{p.get('skill')}::{','.join(str(x) for x in p.get('proposed_scenarios') or [])}" for p in proposals}:
                proposals.append(item)
    coverage = routing_coverage(inv)
    for item in coverage["routed"]:
        if item["source"] != "local-inference":
            continue
        proposals.append(
            {
                "kind": "routing_domain_assignment",
                "severity": "advisory",
                "skill": item["skill"],
                "proposed_domains": item["domains"],
                "reason": "The skill is not persisted in MySkills scenarios, so the local routing overlay supplies a bounded domain assignment.",
                "write_requires_approval": False,
            }
        )
    return {
        "schema": "skill_orchestrator.scenario_plan.v1",
        "ok": True,
        "generated_at": now_iso(),
        "writes_myskills": False,
        "skill_count": len(inv),
        "proposal_count": len(proposals),
        "proposals": proposals[:50],
        "truncated_proposals": max(0, len(proposals) - 50),
        "routing_coverage": {
            key: value for key, value in coverage.items() if key not in {"routed", "manual_reference"}
        },
        "apply_policy": "Use MySkills skills_set_scenarios only after the user approves exact skill ids and scenario keys. Local routing-domain proposals are read-only overlays and do not mutate MySkills.",
    }


def _usage_events(record: dict[str, Any], event_prefix: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    common = {
        "occurred_at": record["recorded_at"],
        "task_kind": record["task_kind"],
        "outcome": record["outcome"],
        "validation": record.get("validation", ""),
        "fallback": record.get("fallback", ""),
        "notes": record.get("notes", ""),
    }
    selected = list(record.get("selected_skills") or [])
    used = list(record.get("used_skills") or [])
    for name in selected:
        events.append({**common, "event_key": f"{event_prefix}:selected:{normalize_name(name)}", "skill_name": name, "event_kind": "selected"})
    for name in used:
        events.append({**common, "event_key": f"{event_prefix}:applied:{normalize_name(name)}", "skill_name": name, "event_kind": "applied"})
    terminal_kind = {"ok": "completed", "failed": "failed", "partial": "partial", "skipped": "skipped"}[record["outcome"]]
    for name in used or selected:
        events.append({**common, "event_key": f"{event_prefix}:{terminal_kind}:{normalize_name(name)}", "skill_name": name, "event_kind": terminal_kind})
    if record.get("fallback"):
        for name in used or selected:
            events.append({**common, "event_key": f"{event_prefix}:fallback:{normalize_name(name)}", "skill_name": name, "event_kind": "fallback"})
    if record.get("validation"):
        for name in used or selected:
            events.append({**common, "event_key": f"{event_prefix}:validated:{normalize_name(name)}", "skill_name": name, "event_kind": "validated"})
    return events


def migrate_legacy_usage() -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    if USAGE_LOG.exists():
        for line in USAGE_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            record.setdefault("recorded_at", now_iso())
            record.setdefault("task_kind", "unknown")
            record.setdefault("outcome", "partial")
            prefix = "legacy:" + hashlib.sha256(line.encode("utf-8")).hexdigest()
            events.extend(_usage_events(record, prefix))
    result = skill_lifecycle_state.record_quality_events(events)
    result["legacy_log"] = str(USAGE_LOG)
    return result


def record_usage(
    task_kind: str,
    selected: str,
    used: str,
    outcome: str,
    notes: str = "",
    fallback: str = "",
    validation: str = "",
) -> dict[str, Any]:
    migrate_legacy_usage()
    normalized_outcome = normalize_enum_value(
        outcome,
        allowed=SKILL_USAGE_OUTCOMES,
        field_name="record-usage --outcome",
        prose_destination="--notes",
    )
    record = {
        "schema": "skill_orchestrator.usage.v1",
        "recorded_at": now_iso(),
        "task_kind": str(task_kind or "unknown")[:80],
        "selected_skills": [item.strip() for item in selected.split(",") if item.strip()][:8],
        "used_skills": [item.strip() for item in used.split(",") if item.strip()][:8],
        "outcome": normalized_outcome,
        "notes": str(notes or "")[:500],
        "fallback": str(fallback or "")[:160],
        "validation": str(validation or "")[:240],
    }
    event_prefix = "usage:" + uuid.uuid4().hex
    result = skill_lifecycle_state.record_quality_events(_usage_events(record, event_prefix))
    return {
        "schema": "skill_orchestrator.record_usage.v2",
        "ok": result["ok"],
        "state_db": result["state_db"],
        "inserted_count": result["inserted_count"],
        "record": record,
    }


def usage_summary(limit: int = 200) -> dict[str, Any]:
    migration = migrate_legacy_usage()
    quality = skill_lifecycle_state.quality_summary(limit=max(limit * 8, 500))
    selected_counts = {name: item["selected"] for name, item in quality["skills"].items() if item["selected"]}
    used_counts = {name: item["applied"] for name, item in quality["skills"].items() if item["applied"]}
    outcomes = {
        kind: sum(int(item.get(kind) or 0) for item in quality["skills"].values())
        for kind in ("completed", "failed")
    }
    return {
        "schema": "skill_orchestrator.usage_summary.v2",
        "ok": True,
        "generated_at": now_iso(),
        "state_db": str(skill_lifecycle_state.STATE_DB),
        "legacy_migration": migration,
        "record_count": quality["record_count"],
        "selected_counts": dict(sorted(selected_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:20]),
        "used_counts": dict(sorted(used_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:20]),
        "outcomes": outcomes,
        "skills": quality["skills"],
    }


def doctor(myskills_inventory: str = "") -> dict[str, Any]:
    snap = snapshot(myskills_inventory)
    issues: list[dict[str, Any]] = []
    inv = merged_inventory(myskills_inventory)
    core_skill_paths = {
        "global-framework": (GLOBAL_SKILLS / "global-framework" / "SKILL.md",),
        "memory-systems": (GLOBAL_SKILLS / "memory-systems" / "SKILL.md",),
        "skill-creator": (GLOBAL_SKILLS / "skill-creator" / "SKILL.md", GLOBAL_SKILLS / ".system" / "skill-creator" / "SKILL.md"),
        "skill-analyzer": (GLOBAL_SKILLS / "skill-analyzer" / "SKILL.md",),
    }
    for expected, paths in core_skill_paths.items():
        if normalize_name(expected) not in inv and not any(path.exists() for path in paths):
            issues.append({"severity": "risk", "code": "core_skill_missing", "message": f"Core routing skill missing: {expected}"})
    if snap["inventory"]["needs_attention_count"]:
        issues.append({"severity": "risk", "code": "myskills_needs_attention", "message": "One or more MySkills entries need attention."})
    scenario = scenario_plan(myskills_inventory)
    if scenario.get("proposal_count"):
        issues.append({"severity": "advisory", "code": "skill_scenario_proposals", "message": f"{scenario['proposal_count']} MySkills scenario assignment proposal(s) are pending."})
    lifecycle = skill_lifecycle.doctor()
    issues.extend(lifecycle.get("issues", []))
    status = "degraded" if any(item["severity"] == "risk" for item in issues) else ("advisory" if issues else "ok")
    return {
        "schema": "skill_orchestrator.doctor.v1",
        "ok": status != "degraded",
        "generated_at": now_iso(),
        "status": status,
        "issues": issues,
        "snapshot": snap,
        "lifecycle": lifecycle,
    }


def validate(myskills_inventory: str = "") -> dict[str, Any]:
    samples = [
        "MCP transport closed and tool current turn unstable",
        "刚刚主账号收到了三条桥接信息",
        "邮箱收件箱待处理回信任务",
        "记忆 note 吸收和 PMB 整理",
        "检查当前全局下系统存在的冗余和互相矛盾或拮抗的机制问题",
        "对当前代码变更做 review 并按反馈修复",
        "继续重构 mobile_openclaw_cli.py 但保持行为不变",
        "用 GitHub MCP 优化远程仓库 README",
        "把 Markdown 文档生成 PDF",
        "当前 GUI OCR 超时是什么意思",
        "Minecraft Fabric 模组加载问题",
        "读取这个飞书文档并提取正文",
        "创建一个 Remotion 视频并渲染 MP4",
        "检查 Codex CLI 的 provider 和模型配置",
        "创建一个 JSON Canvas 流程图",
        "编辑 Obsidian Markdown 的 wikilink 和 callout",
        "创建一个 Obsidian Base 表格视图",
        "把文章生成十页演示文稿",
        "写一个 Minecraft scoreboard mcfunction",
        "创建一个 Paper plugin 服务端插件",
        "设计 Fabric NeoForge 多加载器模组架构",
        "用 ffmpeg 压缩并转换视频",
        "生成一组小红书图片",
        "用 DeepL 处理 XLIFF 翻译",
        "维护新增技能的生命周期",
        "用本机 Word 调整真实分页并导出 PDF",
        "收集最新资料，制作包含图表、Word、PDF 和 HTML 的行业白皮书，并发布到公网网页，后续更新时同步网站",
        "更新现有白皮书并同步 public website",
    ]
    plans = [build_plan(sample, myskills_inventory=myskills_inventory) for sample in samples]
    plans_by_message = dict(zip(samples, plans))
    specialized_expectations = {
        "读取这个飞书文档并提取正文": "feishu-wiki",
        "创建一个 Remotion 视频并渲染 MP4": "remotion-video",
        "检查 Codex CLI 的 provider 和模型配置": "codex-cli",
        "创建一个 JSON Canvas 流程图": "json-canvas",
        "编辑 Obsidian Markdown 的 wikilink 和 callout": "obsidian-markdown",
        "创建一个 Obsidian Base 表格视图": "obsidian-bases",
        "把文章生成十页演示文稿": "baoyu-slide-deck",
        "写一个 Minecraft scoreboard mcfunction": "minecraft-commands-scripting",
        "创建一个 Paper plugin 服务端插件": "minecraft-plugin-dev",
        "设计 Fabric NeoForge 多加载器模组架构": "minecraft-multiloader",
        "用 ffmpeg 压缩并转换视频": "ffmpeg-usage",
        "生成一组小红书图片": "baoyu-xhs-images",
        "用 DeepL 处理 XLIFF 翻译": "deepl",
        "用本机 Word 调整真实分页并导出 PDF": "cli-anything-microsoft-office",
        "收集最新资料，制作包含图表、Word、PDF 和 HTML 的行业白皮书，并发布到公网网页，后续更新时同步网站": "whitepaper-pipeline",
        "更新现有白皮书并同步 public website": "whitepaper-pipeline",
    }
    whitepaper_negative_messages = (
        "编辑这份白皮书 PDF 的页眉",
        "把现有静态网站发布到 GitHub Pages",
        "查找一篇关于人工智能的白皮书",
    )
    whitepaper_negative_plans = {
        message: build_plan(message, myskills_inventory=myskills_inventory)
        for message in whitepaper_negative_messages
    }
    scenario = scenario_plan(myskills_inventory)
    coverage = scenario.get("routing_coverage", {})
    inventory_count = len(merged_inventory(myskills_inventory))
    checks = [
        {
            "name": "selected_skills_bounded",
            "ok": all(len(plan.get("selected_skills", [])) <= 4 for plan in plans),
            "detail": "max 4 selected skills",
        },
        {
            "name": "implicit_execution_skills_bounded",
            "ok": all(
                sum(1 for skill in plan.get("selected_skills", []) if skill.get("layer") == "execution") <= 1
                for plan in plans
            ),
            "detail": "generic routes select at most one execution skill",
        },
        {
            "name": "classified_tasks_have_skills",
            "ok": all(plan.get("selected_skills") for plan in plans),
            "detail": [plan.get("domains", []) for plan in plans],
        },
        {
            "name": "specialized_domains_select_specialized_owner",
            "ok": all(
                plans_by_message[message].get("selected_skills", [{}])[0].get("name") == owner
                for message, owner in specialized_expectations.items()
            ),
            "detail": {
                owner: [item.get("name") for item in plans_by_message[message].get("selected_skills", [])]
                for message, owner in specialized_expectations.items()
            },
        },
        {
            "name": "whitepaper_lifecycle_does_not_capture_single_stage_tasks",
            "ok": all(
                "whitepaper-pipeline"
                not in {item.get("name") for item in whitepaper_negative_plans[message].get("selected_skills", [])}
                for message in whitepaper_negative_messages
            ),
            "detail": {
                message: [item.get("name") for item in whitepaper_negative_plans[message].get("selected_skills", [])]
                for message in whitepaper_negative_messages
            },
        },
        {
            "name": "core_skill_paths_readable",
            "ok": all((GLOBAL_SKILLS / name / "SKILL.md").exists() for name in ("global-framework", "memory-systems")),
            "detail": str(GLOBAL_SKILLS),
        },
        {
            "name": "system_skills_count_as_discovered_capabilities",
            "ok": not any(
                item.get("kind") == "missing_expected_skill" and "skill-creator" in str(item.get("title") or "")
                for item in plans_by_message["维护新增技能的生命周期"].get("gap_proposals", [])
            ),
            "detail": plans_by_message["维护新增技能的生命周期"].get("gap_proposals", []),
        },
        {
            "name": "usage_log_parent_available",
            "ok": RUNTIME.parent.exists() or BRIDGE.exists(),
            "detail": str(RUNTIME),
        },
        {
            "name": "scenario_plan_available",
            "ok": scenario.get("ok") is True,
            "detail": "dry-run only",
        },
        {
            "name": "routing_coverage_accounts_for_inventory",
            "ok": (
                coverage.get("myskills_assigned_count", 0)
                + coverage.get("locally_inferred_count", 0)
                + coverage.get("manual_reference_count", 0)
                == inventory_count
            ),
            "detail": coverage,
        },
        {
            "name": "skill_lifecycle_valid",
            "ok": skill_lifecycle.validate().get("ok") is True,
            "detail": "skill_lifecycle_governance.py validate",
        },
    ]
    return {
        "schema": "skill_orchestrator.validate.v1",
        "ok": all(check["ok"] for check in checks),
        "generated_at": now_iso(),
        "checks": checks,
        "sample_selected": [
            {"message_hash": plan.get("message_hash"), "skills": [item.get("name") for item in plan.get("selected_skills", [])]}
            for plan in plans
        ],
    }


def metrics(myskills_inventory: str = "") -> dict[str, Any]:
    inv = merged_inventory(myskills_inventory)
    scenario = scenario_plan(myskills_inventory)
    usage = usage_summary(limit=500)
    return {
        "schema": "skill_orchestrator.metrics.v1",
        "ok": True,
        "generated_at": now_iso(),
        "skill_count": len(inv),
        "scenario_proposal_count": scenario.get("proposal_count", 0),
        "usage_record_count": usage.get("record_count", 0),
        "read_only_commands": ["snapshot", "doctor", "validate", "metrics", "plan", "scenario-plan", "usage-summary", "audit-plan", "repair-plan", "pdf-capability", "refresh", "lifecycle-state"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Workspace skill routing orchestrator")
    parser.add_argument("--myskills-inventory", default="", help="Optional path to a MySkills skills_inventory JSON result")
    sub = parser.add_subparsers(dest="command", required=True)
    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("--message", required=True)
    scenario_parser = sub.add_parser("scenario-plan")
    del scenario_parser
    for name in ("snapshot", "doctor", "validate", "metrics", "usage-summary", "audit-plan", "repair-plan", "pdf-capability", "refresh", "lifecycle-state"):
        sub.add_parser(name)
    lifecycle_apply = sub.add_parser("apply-approved")
    lifecycle_apply.add_argument("--ids", required=True)
    lifecycle_apply.add_argument("--confirm-apply", action="store_true")
    record = sub.add_parser("record-usage")
    record.add_argument("--task-kind", required=True)
    record.add_argument("--selected", default="")
    record.add_argument("--used", default="")
    record.add_argument(
        "--outcome",
        default="partial",
        type=enum_arg("record-usage --outcome", SKILL_USAGE_OUTCOMES, prose_destination="--notes"),
        help="Machine status only: ok|partial|failed|skipped. Put prose in --notes.",
    )
    record.add_argument("--notes", default="")
    record.add_argument("--fallback", default="", help="Fallback route used, if any.")
    record.add_argument("--validation", default="", help="Compact validation evidence.")
    args = parser.parse_args(argv)

    if args.command == "plan":
        payload = build_plan(args.message, myskills_inventory=args.myskills_inventory)
    elif args.command == "snapshot":
        payload = snapshot(args.myskills_inventory)
    elif args.command == "doctor":
        payload = doctor(args.myskills_inventory)
    elif args.command == "validate":
        payload = validate(args.myskills_inventory)
    elif args.command == "metrics":
        payload = metrics(args.myskills_inventory)
    elif args.command == "scenario-plan":
        payload = scenario_plan(args.myskills_inventory)
    elif args.command == "usage-summary":
        payload = usage_summary()
    elif args.command == "audit-plan":
        payload = skill_lifecycle.audit()
    elif args.command == "repair-plan":
        payload = skill_lifecycle.repair_plan()
    elif args.command == "pdf-capability":
        payload = skill_lifecycle.audit()["pdf_capability"]
        payload["ok"] = True
        payload["generated_at"] = now_iso()
    elif args.command == "refresh":
        refresh = skill_lifecycle.refresh_incremental()
        payload = {key: value for key, value in refresh.items() if key != "records"}
    elif args.command == "lifecycle-state":
        payload = skill_lifecycle_state_snapshot()
    elif args.command == "apply-approved":
        payload = skill_lifecycle.apply_approved(
            [item.strip() for item in args.ids.split(",") if item.strip()],
            confirm_apply=args.confirm_apply,
        )
    elif args.command == "record-usage":
        payload = record_usage(
            args.task_kind,
            args.selected,
            args.used,
            args.outcome,
            args.notes,
            args.fallback,
            args.validation,
        )
    else:  # pragma: no cover
        parser.error(f"unsupported command: {args.command}")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
