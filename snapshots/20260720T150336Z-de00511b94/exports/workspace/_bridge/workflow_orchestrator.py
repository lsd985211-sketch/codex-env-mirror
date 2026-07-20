#!/usr/bin/env python3
"""Read-only task router for memory, skills, slash templates, and MCP tools.

This script is intentionally small. It does not execute task actions, write
memory, mutate business state, or replace the capability matrix. It turns a
user request into a bounded work plan that points Codex at the current sources
of truth: memory governance, skill bodies, slash templates, and MCP routing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bounded_output import aggregate_validator_cli_payload
from shared.json_cli import configure_utf8_stdio, now_iso, print_json
from workflow_plan_detail import apply_detail_level, infer_detail_level
from workflow_environment_context import build_environment_context, validate as validate_environment_context
from workflow_plan_build_steps import (
    build_skill_orchestration,
    collect_domain_routes,
    phase_execution_summary,
    skill_orchestration_summary,
)
from workflow_automation_delegation import automation_delegation_policy
from workflow_orchestrator_routes import NETWORK_ROUTING_EXTRA_KEYWORDS
from execution_route_pack import build_execution_route_pack
from intent_routing import IntentRule, matched_terms, rank_intents, term_matches, term_weight
from mcp_route_policy import call_priority_pack
from workflow_validation import VALIDATION_SAMPLES, build_validation_checks
from task_route_contract import resolve_task_route_contract


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "_bridge"
AGENTS_MIRROR = BRIDGE / "agents_rule_mirror.py"
MATRIX = BRIDGE / "docs" / "mcp_capability_matrix.md"
MAINTENANCE_SURFACE_MAP = BRIDGE / "docs" / "maintenance_surface_map.md"
CODE_MAINTAINABILITY = BRIDGE / "code_maintainability.py"
MAINTENANCE_UPGRADE_GOVERNANCE = BRIDGE / "maintenance_upgrade_governance.py"
ENVIRONMENT_CONTEXT = BRIDGE / "workflow_environment_context.py"
MODULE_CAPABILITY_INDEX = BRIDGE / "runtime" / "module_capability_index.json"
SLASH_REGISTRY = BRIDGE / "slash_commands" / "commands.json"
WORKSPACE_AGENTS = ROOT / "AGENTS.md"
GLOBAL_AGENTS = Path.home() / ".codex" / "AGENTS.md"

configure_utf8_stdio()

try:
    from skill_orchestrator import build_plan as build_skill_orchestration_plan
    from skill_orchestrator import prepare_routing_context as prepare_skill_routing_context
except Exception:  # noqa: BLE001 - workflow routing must keep working if the optional layer fails.
    build_skill_orchestration_plan = None
    prepare_skill_routing_context = None

try:
    from memory_router import route as build_memory_route
except Exception:  # noqa: BLE001 - memory routing must keep working in degraded quick-pass mode.
    build_memory_route = None

try:
    from intent_resource_router import build_route as build_intent_resource_route
except Exception:  # noqa: BLE001 - workflow routing must keep working if this advisory layer fails.
    build_intent_resource_route = None

try:
    from system_membership import retirement_signal as build_retirement_signal
except Exception:  # noqa: BLE001 - workflow routing must remain available without the advisory guard.
    build_retirement_signal = None


@dataclass(frozen=True)
class Domain:
    key: str
    label: str
    keywords: tuple[str, ...]
    skills: tuple[str, ...]
    slash: tuple[str, ...]
    matrix_terms: tuple[str, ...]
    maintenance: tuple[str, ...]
    validation: tuple[str, ...]


DOMAINS: tuple[Domain, ...] = (
    Domain(
        "structured_state",
        "structured state / queues / database-backed evidence",
        (
            "状态",
            "结构化状态",
            "队列",
            "任务表",
            "任务状态",
            "入队",
            "出队",
            "卡住",
            "已完成",
            "待处理",
            "回执",
            "投递",
            "收件箱",
            "发件箱",
            "调度",
            "轮询",
            "数据库",
            "sqlite",
            "db",
            "queue",
            "task state",
            "status",
            "receipt",
            "delivery",
        ),
        ("global-framework",),
        ("sqlite-scratch-plan",),
        ("sqlite_state", "sqlite-bridge-ro", "sqlite-scratch", "record_store.sqlite", "email_state.sqlite"),
        ("_bridge/docs/mcp_capability_matrix.md", "_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py", "_bridge/shared/record_store_maintenance.py"),
        ("bounded SQLite readback", "owning business maintenance validate for repairs"),
    ),
    Domain(
        "bridge",
        "mobile bridge / Weixin delegation",
        (
            "桥接",
            "微信",
            "手机",
            "回发",
            "backup1",
            "backup2",
            "mobile",
            "weixin",
            "openclaw",
            "codex_delegation",
            "mobile_ack",
            "mobile_result",
            "result_begin",
            "只ack",
            "ack-only",
        ),
        ("mobile-weixin-bridge-ops",),
        ("mobile-bridge-task", "mobile-bridge-maintenance"),
        ("mobile-openclaw-bridge", "sqlite-bridge-ro"),
        ("_bridge/mobile_openclaw_bridge/",),
        ("queue state", "permission table", "delivery/receipt evidence"),
    ),
    Domain(
        "audio",
        "local audio operations and governed music-library organization",
        (
            "音乐库",
            "整理音乐",
            "音乐整理",
            "音频文件整理",
            "整理音频文件",
            "音频文件",
            "歌词整理",
            "封面整理",
            "歌词封面",
            "专辑曲序",
            "歌手分类",
            "歌手专辑",
            "歌手专辑分类",
            "音乐分类",
            "音频处理",
            "本地音频工具",
            "music的文件",
            "music 文件",
            "music文件",
            "music library",
            "organize music",
            "audio file organization",
            "album artwork",
            "lyrics sidecar",
        ),
        ("windows-audio-ops",),
        (),
        (),
        (
            "_bridge/music_library_owner.py",
            "_bridge/audio_toolkit/audio_toolkit.py",
            "_bridge/usb_device_owner.py storage",
            "_bridge/docs/audio_system_capability_model.md",
        ),
        (
            "music_library_owner.py doctor",
            "music_library_owner.py validate",
            "python -m unittest _bridge\\music_library_owner_tests.py",
            "usb_device_owner.py storage --drive-letter <drive>",
        ),
    ),
    Domain(
        "hardware",
        "local hardware / USB device inventory and diagnostics",
        (
            "usb设备",
            "usb 设备",
            "usb",
            "usb诊断",
            "usb 诊断",
            "pnp设备",
            "pnp 设备",
            "设备管理器",
            "pci设备",
            "pci 设备",
            "显示适配器",
            "显卡设备",
            "音频设备",
            "声卡设备",
            "摄像头设备",
            "电池设备",
            "传感器设备",
            "硬件问题",
            "硬件设备",
            "硬件诊断",
            "硬件控制",
            "外设",
            "热插拔",
            "串口设备",
            "智能卡读卡器",
            "fido设备",
            "hid设备",
            "adb设备",
            "fastboot设备",
            "usb device",
            "usb inventory",
            "usb diagnostic",
            "pnp device",
            "device manager",
            "pci device",
            "display adapter",
            "audio device",
            "camera device",
            "battery device",
            "sensor device",
            "hardware problem",
            "hardware device",
            "hardware diagnostic",
            "device control",
            "hotplug",
            "hot-plug",
            "serial port",
            "smart card reader",
            "hid device",
            "fido device",
            "adb device",
            "fastboot device",
        ),
        ("global-framework", "windows-codex-ops"),
        (),
        (),
        ("_bridge/windows_hardware_owner.py", "_bridge/usb_device_owner.py", "_bridge/usb_device_control.py", "_bridge/docs/maintenance_surface_map.md"),
        ("windows_hardware_owner.py doctor", "windows_hardware_owner.py validate", "usb_device_owner.py doctor", "usb_device_owner.py validate", "usb_device_control.py validate"),
    ),
    Domain(
        "mcp_tools",
        "MCP / tool stability",
        ("mcp", "工具", "transport closed", "tool", "hub", "codegraph", "filesystem", "当前turn"),
        ("global-framework", "mcp-builder", "windows-codex-ops"),
        ("tool-stability-matrix", "tool-surface-drift", "mcp-health"),
        ("local-mcp-hub", "mcp_session_doctor", "codegraph"),
        ("_bridge/mcp_session_doctor.py", "_bridge/local_mcp_hub.py", "_bridge/resource_process_doctor.py"),
        ("mcp_session_doctor.py validate", "local_mcp_hub.py validate"),
    ),
    Domain(
        "network_routing",
        "network routing / proxy / DNS / connectivity",
        (
            "网络",
            "代理",
            "dns",
            "DNS",
            "卡断",
            "连接慢",
            "回答慢",
            "网关",
            "网络层",
            "网络配置",
            "隔离代理",
            "临时代理",
            "代理租约",
            "租约",
            "节点测速",
            "切换节点",
            "openai慢",
            "chatgpt慢",
            "proxy",
            "gateway",
            "lease",
            "isolated proxy",
            "network gateway",
            "network",
            "connectivity",
            "timeout",
            "openai slow",
            "chatgpt slow",
            *NETWORK_ROUTING_EXTRA_KEYWORDS,
        ),
        ("global-framework", "windows-codex-ops"),
        ("tool-stability-matrix",),
        ("network_routing", "codex_network_gateway", "local-mcp-hub"),
        ("_bridge/codex_network_gateway.py", "_bridge/network_doctor.py", "_bridge/network_policy.py", "_bridge/docs/mcp_capability_matrix.md"),
        ("codex_network_gateway.py validate", "network_doctor.py validate", "network_doctor.py probe-suite", "local_mcp_hub.py validate"),
    ),
    Domain(
        "cli_harness",
        "CLI-Anything / agent-native CLI harnesses and base developer tools",
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
            "基础工具",
            "开发工具",
            "ripgrep",
            "rg",
            "fd",
            "uv",
            "ruff",
            "playwright",
        ),
        ("cli-anything", "global-framework", "mcp-builder"),
        ("cli-anything-flow",),
        ("cli-anything", "cli-hub", "cli_anything_governance", "developer_toolchain"),
        ("_bridge/cli_anything_governance.py", "_bridge/docs/code_maintainability_guidelines.md", "cli-hub"),
        (
            "cli_anything_governance.py validate",
            "rg --version",
            "fd --version when used",
            "uv --version when used",
            "ruff --version when used",
            "playwright smoke when used",
            "harness --help/--json smoke when installed",
        ),
    ),
    Domain(
        "office_native",
        "installed Microsoft Office native editing and rendering",
        (
            "本机 Word", "本机Word", "本机 Excel", "本机Excel", "本机 PowerPoint", "本机PowerPoint",
            "真实 Office", "真实Office", "Office COM", "原生 Office", "原生Office", "真实分页",
            "公式重算", "原生 PDF 导出", "原生PDF导出", "native office", "installed office",
            "cli-anything-microsoft-office",
        ),
        ("cli-anything-microsoft-office", "office-craft"),
        ("cli-anything-flow",),
        ("cli-anything-microsoft-office", "office_native"),
        ("_bridge/cli_anything_microsoft_office/agent-harness", "_bridge/cli_anything_governance.py"),
        ("cli-anything-microsoft-office --json system status", "python _bridge\\cli_anything_governance.py validate"),
    ),
    Domain(
        "memory",
        "memory / PMB / notes / knowledge absorption",
        (
            "记忆",
            "记忆系统",
            "记忆机制",
            "记忆治理",
            "记忆利用",
            "利用方式",
            "使用方式",
            "回忆",
            "召回",
            "召回机制",
            "记忆路由",
            "记忆层",
            "长期记忆",
            "短期记忆",
            "候选记忆",
            "记忆候选",
            "记忆重构",
            "记忆优化",
            "记忆检索",
            "记忆写入",
            "记忆吸收",
            "记忆沉淀",
            "记忆验证",
            "记忆整理",
            "pmb",
            "PMB",
            "note",
            "memory_router",
            "memory_governance",
            "local_pmb_memory",
            "recall",
            "memory recall",
            "memory routing",
            "memory governance",
            "memory system",
            "memory layer",
            "memory usage",
            "memory optimization",
            "memory refactor",
            "memory retrieval",
            "memory absorption",
            "memory verification",
            "用户画像",
            "个人画像",
            "画像",
            "用户偏好",
            "工作偏好",
            "user_profile",
            "user profile",
            "profile_guidance",
            "吸收",
            "知识吸收",
            "外部知识",
            "knowledge",
            "memory",
            "agent-memory-engine",
            "arcrift",
            "localmem",
            "临时笔记",
            "一次性笔记",
            "旁支",
            "收口",
            "易遗失",
            "统一处理",
            "deferred",
            "work-note",
        ),
        ("memory-systems", "memory-checkpoint-ops"),
        ("memory-query-flow", "memory-skill-closeout", "iteration-review"),
        ("local-pmb-memory", "memory_governance", "external_knowledge"),
        ("_bridge/memory_governance.py", "_bridge/codex_workflow_gate.py", "_bridge/external_knowledge.py"),
        (
            "memory_governance.py doctor|validate",
            "codex_workflow_gate.py memory-preflight",
            "external_knowledge.py doctor when web sources are captured",
        ),
    ),
    Domain(
        "workflow_governance",
        "workflow mechanism / context budget / agent efficiency",
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
            "工具路由",
            "路由收敛",
            "分类触发",
            "主入口",
            "架构合理",
            "系统简洁",
            "触发工作流",
            "工作流触发",
            "触发时机",
            "触发条件",
            "分流机制",
            "分流规则",
            "路由机制",
            "意图路由",
            "意图分流",
            "草案区",
            "草案",
            "draft",
            "保留草案",
            "保存草案",
            "提交草案审批",
            "草案审批",
            "草案审阅",
            "draft governance",
            "retained_reference",
            "pending_review",
            "artifact_ref",
            "工具利用",
            "工具利用率",
            "充分利用",
            "未充分利用",
            "没有充分利用",
            "最大作用",
            "发挥作用",
            "覆盖完全",
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
            "效率",
            "准确",
            "准确率",
            "codex desktop",
            "desktop",
            "启动器",
            "重启后",
            "模型列表",
            "模型选择",
            "模型可见",
            "不显示",
            "推理强度",
            "推理选择",
            "reasoning selector",
            "model picker",
            "减少上下文",
            "token",
            "context",
            "workflow optimization",
            "workflow governance",
            "global governance",
            "coherence",
            "redundant",
            "contradiction",
            "conflict",
            "overlap",
            "legacy mechanism",
            "responsibility boundary",
            "context budget",
            "agent efficiency",
            "工作机制优化",
        ),
        ("global-framework",),
        ("workflow-router", "post-work-closeout"),
        ("workflow_orchestrator", "memory_router", "skill_orchestrator", "mcp_capability_routes", "draft_governance"),
        ("_bridge/workflow_orchestrator.py", "_bridge/codex_workflow_entry.py", "_bridge/memory_router.py", "_bridge/skill_orchestrator.py", "_bridge/draft_governance.py"),
        ("python _bridge\\workflow_orchestrator.py validate", "python _bridge\\draft_governance.py validate", "python _bridge\\tool_utilization_audit.py validate", "targeted compact-plan readback"),
    ),
    Domain(
        "wsl_workspace",
        "WSL primary workspace / Work Git lifecycle",
        (
            "wsl主环境",
            "wsl 主环境",
            "wsl作为主环境",
            "wsl 作为主环境",
            "wsl工作区",
            "wsl 工作区",
            "wsl工作环境",
            "wsl 工作环境",
            "主工作区转移到wsl",
            "主工作区转移到 wsl",
            "淘汰原生工作区",
            "逐步淘汰原生工作区",
            "原生工作区降级",
            "长期活动工作区",
            "裸git仓库",
            "裸 git 仓库",
            "工作git",
            "工作 git",
            "work git",
            "bare git",
            "windows bare git",
            "codex-workspace.git",
            "codex-workspace",
            "wsl workspace",
            "wsl primary workspace",
            "primary wsl workspace",
            "declarative work git",
            "workspace lifecycle",
            "workspace handoff",
            "workspace bootstrap",
            "workspace validate",
        ),
        ("codex-cli", "codex-environment-mirror"),
        ("system-change-contract", "post-work-closeout"),
        ("wsl_workspace_owner", "git", "codex_environment_mirror"),
        ("_bridge/wsl_workspace_owner.py", "_bridge/bootstrap_wsl_workspace.py", "_bridge/codex_environment_mirror.py"),
        (
            "python _bridge\\wsl_workspace_owner.py status",
            "python _bridge\\wsl_workspace_owner.py plan",
            "python _bridge\\wsl_workspace_owner.py validate",
            "python _bridge\\codex_workflow_entry.py maintenance catalog --system wsl_workspace --term workspace --limit 20",
        ),
    ),
    Domain(
        "email",
        "mail / inbox / outbox / scheduler",
        ("邮箱", "邮件", "收件", "发件", "回信", "smtp", "imap", "outbox", "inbox", "email", "mail"),
        ("email-ops",),
        ("email-send-flow", "email-inbox-flow"),
        ("email scheduler",),
        ("mail identity table", "mail task table", "mail worker logs"),
        ("identity table", "task table", "inbox/outbox queues", "worker logs"),
    ),
    Domain(
        "skills_templates",
        "skills / slash templates / routing policy",
        (
            "技能",
            "命令",
            "模板",
            "slash",
            "custom-slash",
            "workflow",
            "workflow entry",
            "machine-first",
            "工作入口",
            "编排",
            "阶段",
            "准则",
            "规则",
        ),
        ("global-framework", "skill-creator", "skill-analyzer"),
        ("workflow-router", "command-catalog", "slash-template-governance", "memory-skill-closeout"),
        ("custom-slash-commands", "myskills"),
        ("_bridge/slash_commands/commands.json", "_bridge/custom_slash_commands_mcp.py", "_bridge/slash_command_governance.py"),
        ("slash_command_governance.py validate", "tool_coordination.py validate", "targeted SKILL.md readback"),
    ),
    Domain(
        "code_maintainability",
        "code maintainability / refactor targeting",
        (
            "可维护性",
            "代码整理",
            "代码结构",
            "重构",
            "大文件",
            "大函数",
            "重复函数",
            "代码模块",
            "模块系统",
            "模块化",
            "模块边界",
            "模块命名",
            "维护升级治理",
            "升级治理",
            "maintainability",
            "refactor",
            "large function",
            "large file",
            "module",
            "module-context",
            "module context",
            "maintenance upgrade",
            "upgrade governance",
            "technical debt",
            "适配器",
            "执行器",
            "分类器",
            "adapter",
            "executor",
            "classifier",
        ),
        ("global-framework", "diagnose"),
        ("backup-safe-edit",),
        ("codegraph", "filesystem", "apply_patch"),
        (
            "_bridge/code_maintainability.py",
            "_bridge/maintenance_upgrade_governance.py",
            "_bridge/shared/json_cli.py",
            "_bridge/runtime/module_capability_index.json",
        ),
        (
            "python _bridge\\maintenance_upgrade_governance.py plan --message <task>",
            "python _bridge\\code_maintainability.py validate",
            "python _bridge\\code_maintainability.py module-context --limit 8",
            "py_compile",
            "targeted readback",
        ),
    ),
    Domain(
        "github",
        "GitHub remote repository",
        ("github", "仓库", "repo", "pull request", "readme"),
        ("global-framework",),
        ("github-repo-flow",),
        ("github", "codex_network_gateway"),
        ("GitHub MCP", "_bridge/codex_network_gateway.py"),
        ("codex_network_gateway.py validate", "remote read/write result", "commit SHA when writing"),
    ),
    Domain(
        "external_docs_research",
        "external docs / web research / network resource MCPs",
        (
            "联网",
            "搜索",
            "查资料",
            "相关知识",
            "官方文档",
            "文档",
            "api 文档",
            "API 文档",
            "sdk",
            "library docs",
            "documentation",
            "docs",
            "research",
            "web search",
            "Context7",
            "context7",
            "Microsoft Docs",
            "Microsoft Learn",
            "microsoftdocs",
        ),
        ("global-framework", "find-docs", "context7-mcp"),
        ("workflow-router", "post-work-closeout"),
        ("context7", "microsoftdocs", "github", "chrome-devtools", "playwright"),
        ("Context7 MCP", "Microsoft Docs MCP", "GitHub MCP", "browser/devtools MCP"),
        (
            "owner MCP result with source URL",
            "resource-layer exception or explicit user direct-web evidence when generic web search is used",
            "external knowledge candidate decision",
            "python _bridge\\online_access_gate.py validate",
        ),
    ),
    Domain(
        "gui_browser",
        "GUI / browser automation",
        ("gui", "浏览器", "页面", "面板", "cdp", "playwright", "chrome"),
        ("gui-automation", "playwright", "chrome:control-chrome"),
        ("dashboard-cdp-health",),
        ("playwright", "chrome-devtools", "gui-automation"),
        ("dashboard probes", "browser or GUI MCP"),
        ("screenshot or UI state readback", "API/HTTP state when available"),
    ),
    Domain(
        "records_resources",
        "records / resource library / large files",
        (
            "资源库",
            "执行记录",
            "记录治理",
            "记录库",
            "记录存储",
            "大文件",
            "大日志",
            "索引刷新",
            "记录索引",
            "索引优先",
            "索引查询",
            "冷归档",
            "record-store",
            "record store",
            "record index",
            "index-first",
            "indexed query",
            "resource library",
        ),
        ("global-framework",),
        ("resource-library-cleanup", "performance-network-check"),
        ("filesystem", "sqlite-scratch"),
        ("_bridge/shared/record_store_maintenance.py", "_bridge/resource_router.py"),
        ("record store doctor", "index refresh evidence"),
    ),
    Domain(
        "resource_acquisition",
        "resource acquisition layer / broker strategy",
        (
            "资源获取",
            "获取资源",
            "资源层获取",
            "资源层执行",
            "资源层下载",
            "资源层检索",
            "资源层委托",
            "细调委托",
            "调整委托",
            "委托结果",
            "结果过窄",
            "覆盖不足",
            "扩展联网工具",
            "切换联网工具",
            "资源层处理资源",
            "资源层空结果",
            "资源层假完成",
            "空结果",
            "假完成",
            "完成判定",
            "验收判定",
            "总预算",
            "预算耗尽",
            "资源委托",
            "资源请求",
            "资源策略",
            "资源代理",
            "委托清单",
            "resource acquisition",
            "resource layer acquisition",
            "resource layer request",
            "resource broker",
            "resource strategy",
            "resource request",
            "resource delegate",
            "resource receipt",
            "acceptance predicate",
            "completion predicate",
            "retry budget",
            "total budget",
            "handoff_required",
            "same_need_fetch_allowed",
            "curl",
            "aria2",
            "aria2c",
            "断点续传",
            "续传",
            "下载后端",
            "download_backend",
            "resumable",
            "安装",
            "install",
            "package install",
            "choco",
            "chocolatey",
            "winget",
        ),
        ("global-framework",),
        ("workflow-router", "post-work-closeout"),
        ("resource_acquisition", "resource_cli", "resource_broker", "context7", "microsoftdocs", "github"),
        ("_bridge/resource_broker.py", "_bridge/resource_strategy_policy.py", "_bridge/resource_cli.py"),
        ("resource_cli scenario-smoke", "resource_fetcher_tests.py", "workflow_orchestrator.py validate"),
    ),
    Domain(
        "editing_backup",
        "local edits / backup hygiene",
        ("修改", "编辑", "备份", "bak", "backup", "edit"),
        ("global-framework",),
        ("backup-safe-edit", "backup-hygiene"),
        ("filesystem", "apply_patch"),
        ("_bridge/shared/backup_router.py", "_bridge/backup_hygiene_doctor.py"),
        ("backup manifest", "readback", "backup_hygiene_doctor.py validate"),
    ),
    Domain(
        "encoding",
        "UTF-8 / Chinese paths / mojibake",
        ("乱码", "编码", "中文路径", "utf-8", "mojibake", "encoding"),
        ("global-framework",),
        ("encoding-governance",),
        ("filesystem",),
        ("_bridge/encoding_governance.py",),
        ("read-only byte/content comparison", "encoding governance output"),
    ),
)

DEFAULT_DOMAIN = Domain(
    "general",
    "general workspace task",
    (),
    ("global-framework",),
    ("workflow-router", "post-work-closeout"),
    ("capability matrix lookup",),
    ("_bridge/docs/mcp_capability_matrix.md",),
    ("readback or smallest relevant doctor/validate",),
)

LOW_SIGNAL_KEYWORDS = {
    "状态",
    "工具",
    "命令",
    "规则",
    "修改",
    "编辑",
    "backup",
    "db",
    "gui",
    "hub",
    "rg",
    "uv",
    "草案",
    "draft",
}

MIN_STRONG_ROUTE_SCORE = 2
AMBIGUITY_RATIO = 0.75

WORKFLOW_GOVERNANCE_CONTEXT_TERMS = (
    "工作流",
    "工作机制",
    "工具路由",
    "路由收敛",
    "路由机制",
    "分类触发",
    "触发工作流",
    "工作流触发",
    "触发时机",
    "触发条件",
    "分流机制",
    "分流规则",
    "意图路由",
    "意图分流",
)

GOVERNANCE_ACTION_TERMS = (
    "完善",
    "优化",
    "治理",
    "改进",
    "成熟",
    "修复",
    "根治",
    "落地",
)

EXTERNAL_EVIDENCE_TERMS = (
    "联网",
    "搜索",
    "查资料",
    "相关知识",
    "官方文档",
    "research",
    "web search",
    "docs",
)

DRAFT_WORKFLOW_CONTEXT_TERMS = ("草案", "draft", "artifact_ref", "retained_reference", "pending_review")
DRAFT_WORKFLOW_ACTION_TERMS = ("保留", "保存", "提交", "审批", "审阅", "评审", "keep", "retain", "review", "approve")
IMPLEMENTATION_CONTEXT_TERMS = (
    "本地",
    "代码",
    "脚本",
    "配置",
    "模块",
    "机制",
    "工作流",
    "资源层",
    "路由",
    "分流",
    "适配器",
    "执行器",
    "分类器",
    "_bridge",
    "router",
    "adapter",
    "executor",
    "classifier",
    "workflow",
    "broker",
)
IMPLEMENTATION_ACTION_TERMS = (*GOVERNANCE_ACTION_TERMS, "重构", "修改", "实现", "repair", "fix", "refactor", "implement")


def load_slash_commands() -> dict[str, dict[str, Any]]:
    if not SLASH_REGISTRY.exists():
        return {}
    try:
        payload = json.loads(SLASH_REGISTRY.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - validate reports absence/invalidity separately.
        return {}
    commands = payload.get("commands") if isinstance(payload, dict) else []
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(commands, list):
        return result
    for item in commands:
        if not isinstance(item, dict):
            continue
        names = [str(item.get("name") or "").strip()]
        aliases = item.get("aliases") if isinstance(item.get("aliases"), list) else []
        names.extend(str(alias or "").strip() for alias in aliases)
        for name in names:
            if name:
                result[name] = item
    return result


def keyword_matches(text: str, keyword: str) -> bool:
    return term_matches(text, keyword)


def keyword_weight(keyword: str) -> int:
    return term_weight(keyword, low_signal_terms=LOW_SIGNAL_KEYWORDS)


def contextual_domain_bonus(text: str, domain: Domain, hits: list[str]) -> int:
    """Keep the requested action as owner and treat named evidence/tools as context."""
    has_implementation_action = bool(matched_terms(text, IMPLEMENTATION_ACTION_TERMS))
    has_implementation_context = bool(matched_terms(text, IMPLEMENTATION_CONTEXT_TERMS))
    has_external_evidence = bool(matched_terms(text, EXTERNAL_EVIDENCE_TERMS))
    if domain.key == "code_maintainability":
        return 22 if has_implementation_action and has_implementation_context and has_external_evidence else 0
    if domain.key != "workflow_governance":
        return 0
    has_workflow_context = bool(matched_terms(text, WORKFLOW_GOVERNANCE_CONTEXT_TERMS))
    has_action = bool(matched_terms(text, GOVERNANCE_ACTION_TERMS))
    has_external_evidence = bool(matched_terms(text, EXTERNAL_EVIDENCE_TERMS))
    has_draft_context = bool(matched_terms(text, DRAFT_WORKFLOW_CONTEXT_TERMS))
    has_draft_action = bool(matched_terms(text, DRAFT_WORKFLOW_ACTION_TERMS))
    if has_draft_context and has_draft_action:
        return 2
    if has_workflow_context and has_action and has_external_evidence:
        return 4
    if has_workflow_context and has_action:
        return 2
    if any(hit in WORKFLOW_GOVERNANCE_CONTEXT_TERMS for hit in hits):
        return 1
    return 0


def domain_match_record(
    domain: Domain,
    hits: list[str],
    score: int,
    top_score: int,
    second_score: int,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    route_confidence = 1.0 if second_score <= 0 else round(top_score / max(top_score + second_score, 1), 3)
    candidate_ratio = round(score / max(top_score, 1), 3)
    if score < MIN_STRONG_ROUTE_SCORE:
        quality = "low_confidence"
    elif second_score > 0 and score == top_score and second_score / max(top_score, 1) >= AMBIGUITY_RATIO:
        quality = "ambiguous_top"
    elif score == top_score:
        quality = "strong"
    elif second_score > 0 and candidate_ratio >= AMBIGUITY_RATIO:
        quality = "ambiguous_candidate"
    else:
        quality = "supporting"
    record = {
        "domain": domain,
        "hits": hits,
        "score": score,
        "confidence": route_confidence,
        "route_confidence": route_confidence,
        "candidate_ratio": candidate_ratio,
        "match_quality": quality,
    }
    if evidence:
        record["signal_weights"] = dict(evidence.get("weights") or {})
        record["suppressed_negated_hits"] = list(evidence.get("suppressed_negated_hits") or [])
        record["low_signal_only"] = bool(evidence.get("low_signal_only"))
    return record


def fallback_record(reason: str) -> dict[str, Any]:
    return {
        "domain": DEFAULT_DOMAIN,
        "hits": [],
        "score": 0,
        "confidence": 0.0,
        "route_confidence": 0.0,
        "candidate_ratio": 0.0,
        "match_quality": reason,
    }


def domain_drives_execution(item: dict[str, Any]) -> bool:
    domain = item.get("domain")
    key = str(getattr(domain, "key", "") or "")
    quality = str(item.get("match_quality") or "")
    if key == DEFAULT_DOMAIN.key:
        return True
    return quality == "strong"


def classify(message: str, *, max_domains: int = 3) -> list[dict[str, Any]]:
    text = str(message or "")
    domains_by_key = {domain.key: domain for domain in DOMAINS}
    hits_by_key = {domain.key: matched_terms(text, domain.keywords) for domain in DOMAINS}
    bonuses = {
        domain.key: contextual_domain_bonus(text.lower(), domain, hits_by_key[domain.key])
        for domain in DOMAINS
    }
    ranked = rank_intents(
        text,
        tuple(IntentRule(domain.key, domain.keywords, tuple(LOW_SIGNAL_KEYWORDS)) for domain in DOMAINS),
        bonuses=bonuses,
    )
    evidence_by_key = {str(item["key"]): item for item in ranked}
    scored = [
        (
            int(item["score"]),
            domains_by_key[str(item["key"])],
            list(item["hits"]),
        )
        for item in ranked
    ]
    scored.sort(key=lambda item: (-item[0], item[1].key))
    if not scored:
        return [fallback_record("no_match_fallback")]

    top_score = scored[0][0]
    second_score = scored[1][0] if len(scored) > 1 else 0
    records = [
        domain_match_record(domain, hits, score, top_score, second_score, evidence_by_key.get(domain.key))
        for score, domain, hits in scored[:max_domains]
    ]
    needs_fallback = (
        top_score < MIN_STRONG_ROUTE_SCORE
        or (second_score > 0 and second_score / max(top_score, 1) >= AMBIGUITY_RATIO)
    )
    if needs_fallback and all(item["domain"].key != DEFAULT_DOMAIN.key for item in records):
        if len(records) >= max_domains:
            records = records[: max_domains - 1]
        records.append(fallback_record("confidence_or_ambiguity_fallback"))
    return records


def unique_limited(values: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        output.append(value)
        if len(output) >= limit:
            break
    return output


MODULE_GATE_ACTION_TERMS = (
    "修复",
    "优化",
    "完善",
    "治理",
    "重构",
    "整理代码",
    "改代码",
    "修改代码",
    "模块化",
    "抽模块",
    "拆分",
    "落地",
    "执行",
    "repair",
    "fix",
    "optimize",
    "refactor",
    "cleanup",
    "modular",
    "module",
    "code",
)

DIAGNOSE_TERMS = ("检查", "诊断", "分析", "评估", "查看", "status", "doctor", "diagnose", "inspect", "check")
RESEARCH_TERMS = ("联网", "搜索", "资料", "external", "research", "docs", "web")
EXTERNAL_ACTION_TERMS = ("发送", "发布", "提交", "创建", "send", "post", "create", "submit")
MOBILE_PROTOCOL_TERMS = (
    "mobile",
    "手机",
    "回发",
    "codex_delegation",
    "mobile_ack",
    "mobile_result",
    "result_begin",
    "result_end",
    "只ack",
    "ack-only",
)


def module_gate_needed(message: str, selected_domain_keys: list[str]) -> bool:
    if "code_maintainability" in selected_domain_keys:
        return True
    if not any(
        key in selected_domain_keys
        for key in ("workflow_governance", "mcp_tools", "network_routing", "bridge", "hardware", "audio", "memory", "email", "cli_harness", "resource_acquisition")
    ):
        return False
    text = str(message or "").lower()
    return bool(matched_terms(text, MODULE_GATE_ACTION_TERMS))


def infer_profile(message: str, selected_domain_keys: list[str], *, enable_module_gate: bool, risk: str) -> dict[str, Any]:
    text = str(message or "").lower()
    reasons: list[str] = []
    profile = "general"
    task_contract = resolve_task_route_contract(message, selected_domain_keys)
    task_facts = task_contract.task_facts or {}
    if task_contract.profile_override:
        profile = task_contract.profile_override
        reasons.append(task_contract.reason)
    elif "bridge" in selected_domain_keys and matched_terms(text, MOBILE_PROTOCOL_TERMS):
        profile = "mobile_delegation"
        reasons.append("bridge_mobile_terms")
    elif "workflow_governance" in selected_domain_keys and matched_terms(text, GOVERNANCE_ACTION_TERMS):
        profile = "maintenance_governance"
        reasons.append("workflow_governance_domain")
    elif enable_module_gate:
        profile = "repair_or_code_change"
        reasons.append("module_gate_enabled")
    elif matched_terms(text, RESEARCH_TERMS):
        profile = "research"
        reasons.append("research_terms")
    elif "workflow_governance" in selected_domain_keys:
        profile = "maintenance_governance"
        reasons.append("workflow_governance_domain")
    elif any(key in selected_domain_keys for key in ("mcp_tools", "network_routing", "records_resources", "resource_acquisition", "memory", "email", "structured_state", "hardware", "audio")):
        profile = "diagnose_only" if matched_terms(text, DIAGNOSE_TERMS) else "maintenance_governance"
        reasons.append("maintenance_domain")
    if any(key in selected_domain_keys for key in ("github", "email", "gui_browser")) and matched_terms(text, EXTERNAL_ACTION_TERMS):
        profile = "external_action"
        reasons.append("external_action_terms")
    validation_tier = {
        "diagnose_only": "quick",
        "research": "quick",
        "repair_or_code_change": "full",
        "maintenance_governance": "full",
        "external_action": "full",
        "mobile_delegation": "full",
    }.get(profile, "quick")
    if str(risk or "").lower() in {"l3", "high", "dangerous"}:
        validation_tier = "deep"
        reasons.append("risk_requires_deep_validation")
    return {
        "profile": profile,
        "reasons": unique_limited(reasons, 6),
        "validation_tier": validation_tier,
        "state_change_expected": bool(task_contract.system_change_gate)
        or bool(task_facts.get("durable_closeout_required"))
        or profile in {"repair_or_code_change", "external_action", "mobile_delegation"},
        "broad_scan_default": False,
        "scope_discovery_default": profile in {"repair_or_code_change", "maintenance_governance", "diagnose_only"},
        "scope_expansion_rule": "start from declared files, owners, indexes, and stable identifiers; expand only when bounded evidence is insufficient",
    }


def machine_command(
    command: str,
    *,
    read_only: bool = True,
    required: bool = True,
    timeout_seconds: int = 60,
    retry_policy: str = "none",
) -> dict[str, Any]:
    return {
        "cmd": command,
        "read_only": read_only,
        "required": required,
        "action_contract": {
            "idempotent": bool(read_only),
            "writes_state": not bool(read_only),
            "timeout_seconds": timeout_seconds,
            "retry_policy": retry_policy,
            "permission_boundary": "same_as_owner_tool",
        },
    }


def stable_message_hash(message: str) -> str:
    return hashlib.sha256(str(message or "").encode("utf-8")).hexdigest()[:16]


def shell_quote(value: str) -> str:
    text = str(value)
    return '"' + text.replace('"', '\\"') + '"'


def machine_phase_context(
    *,
    message: str,
    risk: str,
    selected_domains: list[dict[str, Any]],
    selected_skills: list[str],
    selected_slash: list[str],
    missing_slash: list[str],
    matrix_terms: list[str],
    maintenance: list[str],
    validation: list[str],
    skill_orchestration: dict[str, Any],
) -> dict[str, Any]:
    skill_names = [
        str(item.get("name") or "")
        for item in skill_orchestration.get("selected_skills", [])[:4]
        if isinstance(item, dict) and item.get("name")
    ] or selected_skills
    route_terms = unique_limited(matrix_terms, 8)
    selected_domain_keys = [str(item.get("key") or "") for item in selected_domains]
    execution_domain_keys = [
        str(item.get("key") or "")
        for item in selected_domains
        if bool(item.get("drives_execution"))
    ] or [DEFAULT_DOMAIN.key]
    enable_module_gate = module_gate_needed(message, execution_domain_keys)
    profile = infer_profile(message, execution_domain_keys, enable_module_gate=enable_module_gate, risk=risk)
    if build_memory_route is not None:
        try:
            memory_route = build_memory_route(message, selected_domains, risk=risk)
        except Exception as exc:  # noqa: BLE001 - keep workflow planning usable.
            memory_route = {
                "schema": "memory_router.route.v1",
                "ok": False,
                "primary": "quick_pass",
                "reason": f"{type(exc).__name__}: {exc}",
                "layers": [],
            }
    else:
        memory_route = {
            "schema": "memory_router.route.v1",
            "ok": False,
            "primary": "quick_pass",
            "reason": "memory_router_unavailable",
            "layers": [],
        }
    if build_retirement_signal is not None:
        try:
            retirement_guard = build_retirement_signal(message=message)
        except Exception as exc:  # noqa: BLE001 - keep planning usable and expose degraded evidence.
            retirement_guard = {
                "schema": "system_membership.v2.retirement_signal",
                "ok": False,
                "triggered": False,
                "status": "unavailable",
                "directive": "none",
                "reason": f"{type(exc).__name__}: {exc}",
            }
    else:
        retirement_guard = {
            "schema": "system_membership.v2.retirement_signal",
            "ok": False,
            "triggered": False,
            "status": "unavailable",
            "directive": "none",
            "reason": "system_membership_unavailable",
        }
    module_seed_terms: list[str] = []
    for domain in selected_domains:
        if not isinstance(domain, dict):
            continue
        module_seed_terms.append(str(domain.get("key") or ""))
        module_seed_terms.extend(str(item) for item in domain.get("keyword_hits", []) if str(item).strip())
        if domain.get("key") == "code_maintainability":
            module_seed_terms.extend(["module", "workflow", "maintainability", "refactor"])
        elif enable_module_gate:
            module_seed_terms.extend(["module", "maintenance", "repair", "refactor"])
    module_terms = unique_limited(
        [term for term in module_seed_terms if term not in {"filesystem", "apply_patch"}],
        8,
    )
    return {
        "message": message,
        "risk": risk,
        "selected_domain_keys": selected_domain_keys,
        "execution_domain_keys": execution_domain_keys,
        "enable_module_gate": enable_module_gate,
        "profile": profile,
        "selected_skills": selected_skills,
        "selected_slash": selected_slash,
        "missing_slash": missing_slash,
        "skill_names": skill_names,
        "route_terms": route_terms,
        "module_terms": module_terms,
        "memory_route": memory_route,
        "retirement_signal": retirement_guard,
        "route_terms_arg": " ".join(shell_quote(term) for term in route_terms),
        "module_terms_arg": " ".join(f"--term {shell_quote(term)}" for term in module_terms),
        "maintenance": unique_limited(maintenance, 8),
        "validation_commands": unique_limited(validation, 8) or ["readback_or_smallest_relevant_validate"],
    }


def phase_preflight(ctx: dict[str, Any]) -> dict[str, Any]:
    message = ctx["message"]
    risk = ctx["risk"]
    retirement_guard = ctx.get("retirement_signal", {})
    commands = [
        machine_command(
            f"python _bridge\\workflow_orchestrator.py plan --message {shell_quote(message)} --risk {shell_quote(risk)} --detail auto",
            read_only=True,
            required=False,
        )
    ]
    if retirement_guard.get("triggered"):
        commands.append(
            machine_command(
                f"python _bridge\\system_membership.py retirement-signal --message {shell_quote(message)}",
                read_only=True,
                required=True,
            )
        )
    return {
        "id": "phase_1_preflight",
        "owner": "workflow_orchestrator",
        "inputs": {"message_hash": stable_message_hash(message), "risk": risk, "retirement_signal": retirement_guard},
        "outputs": ["domains", "risk", "complexity_budget", "retirement_guard"],
        "commands": commands,
        "read_only": True,
        "approval_required": False,
        "approval_reason": "",
        "fallback": {"kind": "manual_domain_classification", "when": "workflow_orchestrator_unavailable"},
        "validation": {"kind": "schema", "checks": ["domains_present", "phase_ids_present"]},
        "stop_conditions": ["missing_required_workspace_rules"],
        "evidence_to_record": [],
        "next_phase": "phase_2_recall",
    }


def phase_recall(ctx: dict[str, Any]) -> dict[str, Any]:
    message = ctx["message"]
    memory_route = ctx.get("memory_route", {})
    return {
        "id": "phase_2_recall",
        "owner": "memory_governance",
        "inputs": {"domains": ctx["selected_domain_keys"], "memory_route": memory_route},
        "outputs": ["memory_route_decision", "memory_recall_plan", "work_note_state"],
        "commands": [
            machine_command(
                f"python _bridge\\memory_router.py route --message {shell_quote(message)}",
                read_only=True,
                required=True,
            ),
            machine_command(
                f"python _bridge\\codex_workflow_gate.py memory-preflight --message {shell_quote(message)}",
                read_only=True,
                required=False,
            ),
            machine_command("python _bridge\\memory_governance.py work-note-read --limit 100", read_only=True),
        ],
        "read_only": True,
        "approval_required": False,
        "approval_reason": "",
        "fallback": {"kind": "memory_quick_pass", "when": "memory_router_or_preflight_unavailable"},
        "validation": {"kind": "readback", "checks": ["memory_route_has_primary", "memory_result_or_skip_reason"]},
        "stop_conditions": ["memory_tool_reports_sensitive_blocker"],
        "evidence_to_record": ["memory_route_primary", "memory_layers_used_or_skipped"],
        "next_phase": "phase_3_skill_selection",
    }


def phase_skill_selection(ctx: dict[str, Any]) -> dict[str, Any]:
    message = ctx["message"]
    selected_skills = ctx["selected_skills"]
    return {
        "id": "phase_3_skill_selection",
        "owner": "skill_orchestrator",
        "inputs": {"static_skills": selected_skills},
        "outputs": ["selected_skills", "skill_gap_proposals"],
        "commands": [machine_command(f"python _bridge\\skill_orchestrator.py plan --message {shell_quote(message)}", read_only=True)],
        "read_only": True,
        "approval_required": False,
        "approval_reason": "",
        "fallback": {"kind": "static_skill_candidates", "skills": selected_skills},
        "validation": {"kind": "bounded_selection", "max_skills": 4},
        "stop_conditions": ["selected_skill_missing_required_file"],
        "evidence_to_record": ["planned_vs_actual_skill_use_at_closeout"],
        "next_phase": "phase_4_template_render",
    }


def phase_template_render(ctx: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "phase_4_template_render",
        "owner": "custom-slash-commands",
        "inputs": {"templates": ctx["selected_slash"]},
        "outputs": ["rendered_checklists"],
        "commands": [machine_command("python _bridge\\slash_command_governance.py validate", read_only=True, required=False)],
        "read_only": True,
        "approval_required": False,
        "approval_reason": "",
        "fallback": {"kind": "read_registry_directly", "path": str(SLASH_REGISTRY)},
        "validation": {"kind": "registry", "missing_templates": ctx["missing_slash"]},
        "stop_conditions": ["required_slash_template_missing"],
        "evidence_to_record": ["templates_rendered_or_skipped"],
        "next_phase": "phase_5_tool_route",
    }


def phase_tool_route(ctx: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "phase_5_tool_route",
        "owner": "mcp_capability_routes",
        "inputs": {"lookup_terms": ctx["route_terms"]},
        "outputs": ["owning_tool", "native_mcp", "hub_route", "fallback", "permission_boundary"],
        "commands": [
            machine_command("python _bridge\\mcp_capability_routes.py build", read_only=False, required=False),
            machine_command(f"python _bridge\\mcp_capability_routes.py lookup --terms {ctx['route_terms_arg']}", read_only=True, required=False),
            machine_command(
                f"python _bridge\\tool_utilization_audit.py audit --message {shell_quote(ctx['message'])}",
                read_only=True,
                required=False,
            ),
        ],
        "read_only": False,
        "approval_required": False,
        "approval_reason": "build only writes derived runtime cache; source of truth remains Markdown matrix",
        "fallback": {"kind": "markdown_matrix_lookup", "path": str(MATRIX)},
        "validation": {"kind": "route_index_or_matrix_readback", "checks": ["permission_boundary_present"]},
        "stop_conditions": ["no_same_permission_route_for_required_external_action"],
        "evidence_to_record": ["native_mcp_preferred", "fallback_only_after_current_turn_failure", "tool_utilization_audit"],
        "next_phase": "phase_6_module_context",
    }


def phase_module_context(ctx: dict[str, Any]) -> dict[str, Any]:
    is_code_task = bool(ctx.get("enable_module_gate"))
    term_args = ctx["module_terms_arg"]
    context_command = "python _bridge\\code_maintainability.py module-context --limit 8"
    lookup_command = "python _bridge\\code_maintainability.py lookup-module --limit 8"
    placement_command = f"python _bridge\\code_maintainability.py placement-plan --message {shell_quote(ctx['message'])} --limit 8"
    upgrade_command = f"python _bridge\\maintenance_upgrade_governance.py plan --message {shell_quote(ctx['message'])}"
    if term_args:
        context_command = f"{context_command} {term_args}"
        lookup_command = f"{lookup_command} {term_args}"
        placement_command = f"{placement_command} {term_args}"
        upgrade_command = f"{upgrade_command} {term_args}"
    return {
        "id": "phase_6_module_context",
        "owner": "code_maintainability",
        "inputs": {
            "enabled": is_code_task,
            "reason": "code_or_maintenance_repair_intent" if is_code_task else "diagnostic_or_non_code_task",
            "lookup_terms": ctx["module_terms"],
        },
        "outputs": [
            "module_routes",
            "module_reuse_candidates",
            "placement_decision",
            "maintenance_upgrade_batches",
            "conditional_evidence_chain",
            "module_gate",
            "validation_by_module",
        ],
        "commands": [
            machine_command("python _bridge\\code_maintainability.py build-module-index --all-bridge --limit 1000", read_only=False, required=False),
            machine_command(lookup_command, read_only=True, required=False),
            machine_command(context_command, read_only=True, required=False),
            machine_command(placement_command, read_only=True, required=True),
            machine_command(upgrade_command, read_only=True, required=False),
        ] if is_code_task else [],
        "read_only": False if is_code_task else True,
        "approval_required": False,
        "approval_reason": "build-module-index writes only a derived runtime cache; source of truth remains code and maintainability guidelines",
        "fallback": {"kind": "manual_module_classification", "when": "module_context_or_index_unavailable"},
        "validation": {
            "kind": "module_route_readback",
            "checks": ["owner_module_present", "reuse_candidate_checked_or_new_module_reason", "validation_command_present"],
        },
        "stop_conditions": [
            "code_task_without_owner_module_or_explicit_new_module_plan",
            "placement_plan_recommends_peer_module_but_edit_targets_large_owner_file",
            "maintenance_upgrade_plan_fixed_to_irrelevant_tools_instead_of_task_specific_evidence",
            "write_path_without_validation_command",
        ],
        "evidence_to_record": [
            "module_capability_lookup_used_or_skipped",
            "module_context_used_or_skipped",
            "placement_plan_recommendation",
            "maintenance_upgrade_batches_when_system_governance_changes",
            "changed_module_purpose_at_closeout",
        ],
        "next_phase": "phase_7_execution",
    }


def phase_execution(ctx: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "phase_7_execution",
        "owner": "owning_tool_or_module",
        "inputs": {"maintenance_surfaces": ctx["maintenance"]},
        "outputs": ["task_result", "tool_result"],
        "commands": [],
        "read_only": None,
        "approval_required": ctx["risk"].lower() in {"l3", "high", "write", "dangerous"},
        "approval_reason": "depends_on_selected_tool_and_task_risk",
        "fallback": {"kind": "same_permission_bounded_fallback", "when": "native_current_turn_unavailable"},
        "validation": {"kind": "defer_to_phase_8"},
        "stop_conditions": ["permission_mismatch", "destructive_action_without_explicit_approval"],
        "evidence_to_record": ["current_turn_callable_or_negative_observation"],
        "next_phase": "phase_8_validation",
    }


def phase_validation(ctx: dict[str, Any]) -> dict[str, Any]:
    validation_commands = ctx["validation_commands"]
    return {
        "id": "phase_8_validation",
        "owner": "smallest_relevant_validator",
        "inputs": {"validation_commands": validation_commands},
        "outputs": ["validation_result"],
        "commands": [machine_command(command, read_only=True, required=False) for command in validation_commands],
        "read_only": True,
        "approval_required": False,
        "approval_reason": "",
        "fallback": {"kind": "targeted_readback", "when": "validator_unavailable"},
        "validation": {"kind": "validator_result", "checks": ["ok_or_known_residual_risk"]},
        "stop_conditions": ["validation_blocker_unresolved"],
        "evidence_to_record": ["validator_used", "residual_risk"],
        "next_phase": "phase_9_closeout",
    }


def phase_closeout(ctx: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "phase_9_closeout",
        "owner": "closeout_governance",
        "inputs": {"skill_names": ctx["skill_names"], "templates": ctx["selected_slash"]},
        "outputs": ["closeout_package", "tool_evidence", "proposals", "work_note_dispositions"],
        "commands": [
            machine_command(
                "python _bridge\\codex_workflow_entry.py closeout --task-kind <kind> --selected <skill,...> --used <skill,...> --outcome <ok|partial|failed>",
                read_only=True,
            ),
            machine_command(
                "python _bridge\\codex_workflow_entry.py closeout --task-kind <kind> --outcome ok --config-changed --auto-finalize --finalization-summary <verified-config-change>",
                read_only=False,
                required=False,
            ),
            machine_command(
                "python _bridge\\codex_workflow_entry.py closeout --task-kind <project-id> --outcome ok --major-change --auto-finalize --finalization-title <title> --finalization-summary <verified-summary>",
                read_only=False,
                required=False,
            ),
            machine_command(
                "python _bridge\\skill_orchestrator.py record-usage --task-kind <kind> --selected <skill,...> --used <skill,...> --outcome <ok|partial|failed> --notes <short>",
                read_only=False,
                required=False,
            ),
        ],
        "read_only": False,
        "approval_required": False,
        "approval_reason": "record-usage writes non-sensitive usage ledger only; explicit config_changed/major_change signals authorize bounded baseline/checkpoint finalization; durable memory/skill writes still need approval",
        "fallback": {"kind": "manual_closeout_package", "when": "closeout_tools_unavailable"},
        "validation": {
            "kind": "closeout",
            "checks": ["closeout_package_read", "work_notes_handled", "tool_evidence_classified", "proposals_visible"],
        },
        "stop_conditions": ["work_note_write_action_without_separate_approval"],
        "evidence_to_record": [
            "memory_skill_template_tool_usage",
            "tool_evidence",
            "module_context_usage_and_module_route_changes",
            "persistence_decision",
        ],
        "next_phase": None,
    }


PHASE_DEPENDENCIES = {
    "phase_1_preflight": [],
    "phase_2_recall": ["phase_1_preflight"],
    "phase_3_skill_selection": ["phase_2_recall"],
    "phase_4_template_render": ["phase_3_skill_selection"],
    "phase_5_tool_route": ["phase_4_template_render"],
    "phase_6_module_context": ["phase_5_tool_route"],
    "phase_7_execution": ["phase_5_tool_route", "phase_6_module_context"],
    "phase_8_validation": ["phase_7_execution"],
    "phase_9_closeout": ["phase_8_validation"],
}

PHASE_BASE_TRIGGERS = {
    "phase_1_preflight": "always_classify_request",
    "phase_2_recall": "non_simple_or_workspace_context_task",
    "phase_3_skill_selection": "selected_domains_have_skill_candidates",
    "phase_4_template_render": "selected_workflow_templates_available",
    "phase_5_tool_route": "tool_or_capability_route_needed",
    "phase_6_module_context": "code_or_maintenance_repair_intent",
    "phase_7_execution": "after_routing_and_required_preflight",
    "phase_8_validation": "after_execution_or_read_only_diagnosis",
    "phase_9_closeout": "non_simple_task_closeout",
}

CHECKPOINT_TRIGGERS = {
    "phase_1_preflight": ["task_scope_unclear", "workspace_rules_conflict"],
    "phase_2_recall": ["memory_conflict", "prior_conclusion_may_be_stale"],
    "phase_3_skill_selection": ["selected_skill_missing", "skill_gap_found", "skill_trigger_missed"],
    "phase_4_template_render": ["template_missing", "template_output_invalid"],
    "phase_5_tool_route": ["native_mcp_unavailable", "transport_closed", "fallback_route_used", "permission_boundary_unclear"],
    "phase_6_module_context": ["owner_module_unclear", "new_module_needed", "module_index_stale"],
    "phase_7_execution": ["scope_changed", "tool_failure", "long_running_step", "derived_write_needs_approval"],
    "phase_8_validation": ["validation_failed", "validator_unavailable", "residual_risk_found"],
    "phase_9_closeout": ["work_notes_active", "persistence_proposal_needed", "external_knowledge_candidate_found"],
}


def phase_enabled(phase: dict[str, Any], ctx: dict[str, Any]) -> tuple[bool, str]:
    phase_id = str(phase.get("id") or "")
    profile = str(ctx.get("profile", {}).get("profile") or "general")
    if phase_id == "phase_4_template_render" and not ctx.get("selected_slash"):
        return False, "no_selected_templates"
    if phase_id == "phase_6_module_context" and not ctx.get("enable_module_gate"):
        return False, "diagnostic_or_non_code_task"
    if phase_id == "phase_7_execution" and profile == "research":
        return False, "research_profile_has_no_local_execution_step"
    return True, ""


def phase_validation_tier(phase: dict[str, Any], ctx: dict[str, Any], enabled: bool) -> str:
    if not enabled:
        return "none"
    phase_id = str(phase.get("id") or "")
    profile_tier = str(ctx.get("profile", {}).get("validation_tier") or "quick")
    if phase_id in {"phase_1_preflight", "phase_2_recall", "phase_3_skill_selection", "phase_4_template_render", "phase_5_tool_route"}:
        return "quick"
    return profile_tier


def phase_action_contract(phase: dict[str, Any], enabled: bool, validation_tier: str) -> dict[str, Any]:
    commands = phase.get("commands") if isinstance(phase.get("commands"), list) else []
    writes_state = any(bool(command.get("action_contract", {}).get("writes_state")) for command in commands if isinstance(command, dict))
    return {
        "enabled": enabled,
        "writes_state": writes_state,
        "idempotent": not writes_state,
        "validation_tier": validation_tier,
        "retry_policy": "none_for_writes_retry_read_only_only" if writes_state else "read_only_retry_allowed_once",
        "timeout_seconds": max([int(command.get("action_contract", {}).get("timeout_seconds") or 0) for command in commands if isinstance(command, dict)] or [0]),
    }


def enrich_phase(phase: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    enabled, skip_reason = phase_enabled(phase, ctx)
    validation_tier = phase_validation_tier(phase, ctx, enabled)
    phase_id = str(phase.get("id") or "")
    enriched = dict(phase)
    enriched["enabled"] = enabled
    enriched["skip_reason"] = skip_reason
    enriched["trigger_reason"] = PHASE_BASE_TRIGGERS.get(phase_id, "phase_defined_by_workflow")
    enriched["checkpoint_triggers"] = CHECKPOINT_TRIGGERS.get(phase_id, [])
    enriched["checkpoint_command"] = (
        "python _bridge\\codex_workflow_entry.py checkpoint "
        f"--phase {shell_quote(phase_id)} --trigger <trigger> --summary <short> "
        "--evidence key=value --pending <item>"
    )
    enriched["depends_on"] = PHASE_DEPENDENCIES.get(phase_id, [])
    enriched["validation_tier"] = validation_tier
    enriched["action_contract"] = phase_action_contract(phase, enabled, validation_tier)
    if not enabled:
        enriched["commands"] = []
    return enriched


def build_machine_phases(
    *,
    message: str,
    risk: str,
    selected_domains: list[dict[str, Any]],
    selected_skills: list[str],
    selected_slash: list[str],
    missing_slash: list[str],
    matrix_terms: list[str],
    maintenance: list[str],
    validation: list[str],
    skill_orchestration: dict[str, Any],
) -> list[dict[str, Any]]:
    ctx = machine_phase_context(
        message=message,
        risk=risk,
        selected_domains=selected_domains,
        selected_skills=selected_skills,
        selected_slash=selected_slash,
        missing_slash=missing_slash,
        matrix_terms=matrix_terms,
        maintenance=maintenance,
        validation=validation,
        skill_orchestration=skill_orchestration,
    )
    phases = [
        phase_preflight(ctx),
        phase_recall(ctx),
        phase_skill_selection(ctx),
        phase_template_render(ctx),
        phase_tool_route(ctx),
        phase_module_context(ctx),
        phase_execution(ctx),
        phase_validation(ctx),
        phase_closeout(ctx),
    ]
    return [enrich_phase(phase, ctx) for phase in phases]


def _selected_domain_keys(selected_domains: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("key")) for item in selected_domains]


def _policy_domain_keys(selected_domains: list[dict[str, Any]]) -> list[str]:
    return [
        str(item.get("key"))
        for item in selected_domains
        if bool(item.get("drives_execution"))
        or str(item.get("match_quality") or "") not in {
            "low_confidence",
            "confidence_or_ambiguity_fallback",
            "no_match_fallback",
        }
    ]


def _compact_call_priority(pack: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "mcp_call_priority.compact.v1",
        "profile": pack.get("profile"),
        "tool": pack.get("tool"),
        "execution_affinity": pack.get("execution_affinity"),
        "required_first_step": pack.get("required_first_step"),
        "steps": [
            {
                "id": step.get("id"),
                "action": step.get("action"),
                "on_failure_next_step": step.get("on_failure_next_step"),
            }
            for step in pack.get("steps", [])
            if isinstance(step, dict)
        ],
    }


def workflow_tools_contract(
    *,
    matrix_terms: list[str],
    intent_resource_route: dict[str, Any],
    selected_domains: list[dict[str, Any]],
) -> dict[str, Any]:
    domain_keys = _policy_domain_keys(selected_domains)
    codegraph_call_priority = _compact_call_priority(
        call_priority_pack("codegraph", "codegraph_explore", "code_structure")
    )
    codegraph_sequence = [
        f"{step.get('id')}: {step.get('action')}"
        for step in codegraph_call_priority.get("steps", [])
        if isinstance(step, dict)
    ]
    structured_state_domains = {"structured_state", "records_resources", "email", "bridge", "resource_acquisition", "memory"}
    codegraph_domains = {"code_maintainability", "workflow_governance", "mcp_tools", "resource_acquisition", "network_routing", "memory"}
    network_domains = {"network_routing", "resource_acquisition", "external_docs_research", "github"}
    system_incident_domains = {
        "workflow_governance",
        "mcp_tools",
        "network_routing",
        "resource_acquisition",
        "bridge",
        "hardware",
        "audio",
        "memory",
        "email",
        "gui_browser",
        "code_maintainability",
    }
    return {
        "matrix": str(MATRIX),
        "lookup_terms": unique_limited(matrix_terms, 8),
        "rule": "resolve classified execution affinity before the first MCP call; hub_first, session_native_first, and owner_cli_first override generic tool or skill defaults",
        "intent_resource_route": intent_resource_route,
        "execution_gate": {
            "generic_web_search_requires_owner_route": bool(
                intent_resource_route.get("generic_web_gate", {}).get("requires_fallback_reason_if_used")
            )
            if isinstance(intent_resource_route, dict)
            else False,
            "generic_web_first_violation": bool(
                intent_resource_route.get("generic_web_gate", {}).get("violation_if_generic_web_first")
            )
            if isinstance(intent_resource_route, dict)
            else False,
            "rule": "For explicit external research, submit a resource-layer request first; generic web search requires resource-layer unavailable/terminal-blocker evidence or explicit user direct-web instruction.",
            "online_access_gate": "python _bridge\\online_access_gate.py plan --message <task>; python _bridge\\online_access_gate.py check --web-used ...",
        },
        "structured_state_policy": {
            "enabled": any(key in domain_keys for key in structured_state_domains),
            "query_rule": "For queue, task, delivery, receipt, scheduler, inbox/outbox, record-store, indexed-resource, .sqlite/.db, or database-backed status questions, use SQLite MCP/Hub read-only queries before broad log scanning, rg, or large CLI dumps.",
            "repair_rule": "SQLite may supply evidence and scratch state, but production repairs must go through the owning business maintenance CLI/API; do not directly mutate production databases.",
            "route_terms": ["sqlite_state", "sqlite-bridge-ro", "sqlite-scratch", "record_store.sqlite", "email_state.sqlite"],
            "preferred_tools": ["native sqlite MCP when exposed", "Hub sqlite aliases", "owner query CLI"],
            "record_store_query_commands": [
                "python _bridge\\shared\\record_store_maintenance.py query --term <term> --limit 5",
                "python _bridge\\shared\\record_store_maintenance.py query --area <area> --kind <kind> --status <status> --limit 10",
                "python _bridge\\shared\\system_maintenance_cli.py record-store query --term <term> --limit 5",
            ],
            "query_receipt_fields": ["query_used_index", "source_read_required", "next_action", "rows[].source_path"],
            "validation": "bounded SELECT/readback for diagnosis; owning doctor/validate after any repair",
        },
        "codegraph_policy": {
            "enabled": any(key in domain_keys for key in codegraph_domains),
            "query_rule": "For source structure, symbol flow, call paths, impact/blast-radius, or non-simple code edits, use CodeGraph with explicit freshness_targets before falling back to rg/read.",
            "route_terms": ["codegraph", "code_structure", "call path", "blast radius"],
            "call_priority": codegraph_call_priority,
            "preferred_sequence": codegraph_sequence,
            "example_command": "codegraph_explore(query=<specific symbol or architecture question>, freshness_targets=[<changed-or-target-file>], maxFiles=12)",
            "validation": "result contains relevant current source or explicit current-turn negative evidence plus same-boundary fallback evidence",
        },
        "maintenance_upgrade_policy": {
            "enabled": "code_maintainability" in domain_keys or any(
                key in domain_keys for key in ("workflow_governance", "mcp_tools", "resource_acquisition", "network_routing", "hardware", "audio")
            ),
            "query_rule": "For system maintenance or upgrade work, use maintenance_upgrade_governance.py to choose task-specific evidence and batches. CodeGraph, SQLite, network, resource, owner validator, and membership checks are conditional tools, not a fixed checklist.",
            "route_terms": ["maintenance_upgrade_governance", "module_context", "conditional_evidence_chain", "owner_surface"],
            "validation": "maintenance_upgrade_governance.py validate plus the owning module validators selected by the plan",
        },
        "system_incident_policy": {
            "enabled": any(key in domain_keys for key in system_incident_domains),
            "query_rule": "For system-level anomalies, first reconstruct the full execution chain and verify each layer before changing state. Treat service health, protocol smoke, current-session binding, cache/filter behavior, runtime lifecycle, and UI/result state as separate evidence layers.",
            "route_terms": [
                "system_incident_chain",
                "layered_diagnostics",
                "native_mechanism_first",
                "minimal_persistent_repair",
                "stable_receipt_schema",
            ],
            "principles": [
                "protect_native_mechanism_before_parallel_replacement",
                "patch_only_the_confirmed_breakpoint",
                "respect_lifecycle_reload_restart_cache_boundaries",
                "unify_start_recover_existing_process_paths",
                "make_cross_boundary_receipts_schema_stable_and_field_safe",
            ],
            "evidence_required": [
                "chain_map",
                "per_layer_probe_or_reason_unavailable",
                "native_mechanism_preserved_or_reason_not_possible",
                "minimal_breakpoint_repair",
                "all_entry_paths_share_the_repair_or_validation_path",
                "post_fix_layered_validation",
            ],
            "validation": "owner doctor/validate plus targeted per-layer probes; do not use one healthy layer as proof of whole-chain health",
        },
        "self_update_policy": {
            "enabled": any(key in domain_keys for key in ("workflow_governance", "memory", "resource_acquisition")),
            "query_rule": "For stale skills, workflows, memories, or resource delegation behavior, use self_update_governance.py as a read-only aggregate health surface, then repair only through the owning module it points to.",
            "route_terms": ["self_update_governance", "stale_skill", "stale_memory", "workflow_drift", "resource_strategy_drift"],
            "principles": [
                "detect_stale_surfaces_before_adding_new_rules",
                "route_repairs_to_owner_modules",
                "keep_candidate_updates_reviewable_before_absorption",
                "treat_usage_outcomes_as_feedback_not_static_truth",
            ],
            "evidence_required": [
                "self_update_governance_doctor_or_validate",
                "owner_surface_for_each_risk",
                "repair_plan_or_no_write_reason",
            ],
            "validation": "python _bridge\\self_update_governance.py validate plus owner validators for changed surfaces",
        },
        "network_policy": {
            "enabled": any(key in domain_keys for key in network_domains),
            "query_rule": "For proxy, DNS, OpenAI/ChatGPT/GitHub/npm connectivity, timeout, or slow-response work, use codex_network_gateway.py for caller-facing route/env/lease decisions; use network_doctor or Hub network.* for lower-level diagnosis.",
            "route_terms": ["network_routing", "codex_network_gateway", "network.snapshot", "network.recommend", "network.plan", "network.probe_suite"],
            "validation": "codex_network_gateway.py validate for gateway control-plane health; network_doctor.py validate/probe-suite for lower-level route discovery and target latency evidence",
        },
        "external_docs_policy": {
            "enabled": "external_docs_research" in domain_keys,
            "query_rule": "When the user explicitly asks to search online, research, look up docs, or use external knowledge, submit a resource-layer request first. The resource layer owns source discovery and should choose Microsoft Docs, Context7, GitHub MCP, browser/DevTools/Playwright, or another owner route internally. Deferred or insufficient results require a refined resource delegation; failed or blocked results require the configured owner/Hub online route chain before any direct generic web fallback.",
            "route_terms": ["external_docs_research", "context7", "microsoftdocs", "github_remote", "browser_devtools", "web_search_fallback"],
            "owner_mcp_candidates": ["microsoftdocs", "context7", "github", "chrome-devtools", "playwright"],
            "generic_web_search_allowed_only_with": [
                "resource_layer_unavailable",
                "predefined_online_route_exhausted",
                "explicit_user_direct_web",
                "higher_precedence_platform_web_required",
            ],
            "closeout_required_evidence": [
                "resource_layer_receipt",
                "owner_route_used_inside_resource_layer",
                "online_access_gate_exception_if_generic_web_used",
            ],
            "validation": "online_access_gate check plus resource-layer receipt; direct generic web only after resource-layer unavailable evidence, configured owner/Hub route-chain exhaustion evidence, explicit user request, or an explicit higher-precedence platform requirement flag",
        },
    }


def machine_first_contract() -> dict[str, Any]:
    return {
        "enabled": True,
        "human_readability_goal": False,
        "primary_consumers": ["codex", "workflow_entry", "tool_router"],
        "module_context": {
            "enabled_for_code_tasks": True,
            "entrypoint": "_bridge/code_maintainability.py module-context",
            "upgrade_governance_entrypoint": "_bridge/maintenance_upgrade_governance.py plan",
            "index_path": "_bridge/runtime/module_capability_index.json",
            "build_entrypoint": "_bridge/code_maintainability.py build-module-index",
            "lookup_entrypoint": "_bridge/code_maintainability.py lookup-module",
            "purpose": "route code edits by reusable module capability, module purpose, state behavior, owner CLI, and validation command",
            "rule": "reuse or extend existing modules first; create a new module only with a boundary and validation-owner reason",
        },
        "field_contract": [
            "id",
            "owner",
            "enabled",
            "skip_reason",
            "trigger_reason",
            "checkpoint_triggers",
            "checkpoint_command",
            "depends_on",
            "commands",
            "action_contract",
            "validation_tier",
            "read_only",
            "approval_required",
            "approval_reason",
            "fallback",
            "validation",
            "stop_conditions",
            "evidence_to_record",
            "next_phase",
        ],
    }


def validation_tiers_contract(validation: list[str]) -> dict[str, list[str]]:
    return {
        "quick": unique_limited(validation, 4) or ["targeted readback"],
        "full": unique_limited([*validation, "python _bridge\\workflow_orchestrator.py validate"], 8),
        "deep": unique_limited(
            [
                *validation,
                "python _bridge\\workflow_orchestrator.py validate",
                "python _bridge\\mcp_session_doctor.py validate",
                "python _bridge\\local_mcp_hub.py validate",
                "python _bridge\\code_maintainability.py validate",
            ],
            10,
        ),
    }


def structured_closeout_contract() -> dict[str, Any]:
    return {
        "schema": "workflow_orchestrator.closeout_contract.v1",
        "required_fields": [
            "tools_used",
            "native_mcp_failures",
            "fallback_used",
            "module_reuse_decision",
            "work_notes",
            "external_knowledge_candidates",
            "memory_route_decision",
            "memory_layers_used",
            "memory_or_skill_proposals",
            "validation_result",
        ],
        "write_policy": "proposal_or_existing_approval_required_for_durable_memory_skill_baseline_or_external_state",
    }


def closeout_steps() -> list[str]:
    return [
        "generate and read _bridge/codex_workflow_entry.py closeout; treat work_notes as one closeout-package field, mark each as handled now, proposal/next task, deferred with reason, or discarded; then clear the temporary file",
        "if web/external sources were used, batch judge reusable candidates with external_knowledge capture-decision; do not run it per page",
        "if generic web search was used, closeout must include owner MCP evidence or an explicit owner MCP fallback reason",
        "tool matrix update proposal if routing was missing or stale",
        "module route or purpose update proposal if code ownership changed",
        "skill update proposal if selected skill was wrong or incomplete",
        "slash template proposal if a repeated workflow lacked a template",
        "memory absorb/stale proposal if durable facts changed",
        "no-persistence reason if nothing durable changed",
    ]


def build_plan(
    message: str,
    risk: str = "unknown",
    detail: str = "full",
    *,
    skill_routing_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    domains = classify(message)
    intent_resource_route = build_intent_resource_route(message) if build_intent_resource_route else {}
    slash_commands = load_slash_commands()
    route_candidates = collect_domain_routes(
        domains,
        domain_drives_execution_fn=domain_drives_execution,
    )
    selected_domains = route_candidates["selected_domains"]
    skill_candidates = route_candidates["skill_candidates"]
    slash_candidates = route_candidates["slash_candidates"]
    matrix_terms = route_candidates["matrix_terms"]
    for owner_route in intent_resource_route.get("owner_routes", []) if isinstance(intent_resource_route, dict) else []:
        if isinstance(owner_route, dict):
            matrix_terms.extend(str(term) for term in owner_route.get("route_terms", []) if str(term).strip())
    maintenance = route_candidates["maintenance"]
    validation = route_candidates["validation"]
    missing_slash: list[str] = []
    selected_skills = unique_limited(skill_candidates, 4)
    selected_slash = unique_limited(slash_candidates, 3)
    for command in selected_slash:
        if command not in slash_commands:
            missing_slash.append(command)

    skill_plan_builder = build_skill_orchestration_plan
    if skill_plan_builder is not None and skill_routing_context is not None:
        skill_plan_builder = lambda value: build_skill_orchestration_plan(  # noqa: E731 - narrow adapter for shared validation context.
            value,
            routing_context=skill_routing_context,
        )
    skill_orchestration = build_skill_orchestration(
        message,
        build_skill_orchestration_plan=skill_plan_builder,
    )

    machine_phases = build_machine_phases(
        message=message,
        risk=risk,
        selected_domains=selected_domains,
        selected_skills=selected_skills,
        selected_slash=selected_slash,
        missing_slash=missing_slash,
        matrix_terms=matrix_terms,
        maintenance=maintenance,
        validation=validation,
        skill_orchestration=skill_orchestration,
    )
    profile = machine_phase_context(
        message=message,
        risk=risk,
        selected_domains=selected_domains,
        selected_skills=selected_skills,
        selected_slash=selected_slash,
        missing_slash=missing_slash,
        matrix_terms=matrix_terms,
        maintenance=maintenance,
        validation=validation,
        skill_orchestration=skill_orchestration,
    )["profile"]
    phase_summary = phase_execution_summary(machine_phases)
    selected_domain_keys = [str(item.get("key") or "") for item in selected_domains]
    task_contract = resolve_task_route_contract(message, selected_domain_keys)
    task_facts = task_contract.task_facts or {}
    resource_layer_contract = intent_resource_route.get("resource_layer_contract", {}) if isinstance(intent_resource_route, dict) else {}
    resource_fact_required = bool(
        task_facts.get("external_network_read")
        or task_facts.get("resource_materialization")
        or task_facts.get("package_install")
    )

    plan = {
        "schema": "workflow_orchestrator.plan.v1",
        "ok": not missing_slash,
        "generated_at": now_iso(),
        "message": message,
        "risk": risk,
        "retirement_guard": next(
            (
                phase.get("inputs", {}).get("retirement_signal", {})
                for phase in machine_phases
                if phase.get("id") == "phase_1_preflight"
            ),
            {},
        ),
        "profile": profile,
        "domains": selected_domains,
        "structured_route": {
            "schema": "workflow_structured_route.v1",
            "input_mode": "natural_language_classification",
            "task_contract": task_contract.to_dict(),
            "domain_keys": selected_domain_keys,
            "primary_domain": next((str(item.get("key") or "") for item in selected_domains if item.get("drives_execution")), "general"),
            "profile": profile.get("profile"),
            "validation_tier": profile.get("validation_tier"),
            "state_change_expected": profile.get("state_change_expected"),
            "resource_delegation": {
                "required": bool(resource_layer_contract.get("required")) or resource_fact_required,
                "task_class": resource_layer_contract.get("task_class", ""),
                "source_discovery_owner": resource_layer_contract.get("source_discovery_owner", "") or ("resource_layer" if resource_fact_required else ""),
                "candidate_review_before_materialization": bool(resource_layer_contract.get("candidate_review_before_materialization")),
            },
            "downstream_rule": "consume_this_route_contract; do_not_independently_reclassify_raw_message_without_new_evidence",
        },
        "workflow": [
            "recall relevant memory only for selected domains",
            "read selected SKILL.md files before relying on them",
            "delegate complete, low-risk, verifiable, repeatable execution to the owning environment tool; keep Codex on judgment, analysis, design, and exceptions",
            "check mcp_capability_matrix.md for owning MCP and fallback",
            "render selected slash templates only as checklists",
            "write a lightweight checkpoint on phase transitions, scope changes, tool exceptions, fallback use, validation failures, or approval-boundary discoveries",
            "capture valuable non-blocking side issues in one-shot work notes instead of interrupting the main task",
            "if web research is used, keep a compact candidate-source list during research instead of judging every page",
            "when web research is explicitly requested, submit a resource-layer request first; let the resource layer choose owner MCP/tool routes internally before any generic web fallback",
            "for non-simple code work, run module context before edits and keep module ownership/facades explicit",
            "execute with the owning MCP/CLI/API or bounded fallback",
            "verify with the smallest relevant maintenance/readback path",
            "generate and read the machine-first closeout package; process work_notes as one field, then clear handled notes",
            "surface synchronization proposals during closeout",
        ],
        "memory": {
            "router": "_bridge/memory_router.py route",
            "entrypoint": "_bridge/codex_workflow_gate.py memory-preflight",
            "governance": "_bridge/memory_governance.py",
            "route": next(
                (phase.get("inputs", {}).get("memory_route", {}) for phase in machine_phases if phase.get("id") == "phase_2_recall"),
                {},
            ),
            "rule": "route memory by task fit; PMB is for long-lived lessons and prior root causes, not a mandatory ritual; verify drift-prone facts live before relying on them",
        },
        "skills": {
            "selected": selected_skills,
            "rule": "load full SKILL.md only for selected skills; do not merge broad skill bodies",
        },
        "automation_delegation": automation_delegation_policy(),
        "skill_orchestration": {
            **skill_orchestration_summary(skill_orchestration, limit=4),
        },
        "tools": workflow_tools_contract(
            matrix_terms=matrix_terms,
            intent_resource_route=intent_resource_route,
            selected_domains=selected_domains,
        ),
        "classification": {
            "strategy": "weighted_signal_with_negation_and_abstention",
            "fallback_domain": DEFAULT_DOMAIN.key,
            "min_strong_route_score": MIN_STRONG_ROUTE_SCORE,
            "ambiguity_ratio": AMBIGUITY_RATIO,
            "rule": "Structured and bounded lexical signals are weighted consistently; locally negated evidence is suppressed. Low confidence or near-tie matches remain evidence-only candidates and append general fallback so downstream routing can abstain instead of executing an arbitrary top match.",
        },
        "slash_templates": {
            "selected": selected_slash,
            "missing": missing_slash,
            "rule": "templates are prompts/checklists, never execution or permission",
        },
        "machine_first": machine_first_contract(),
        "machine_phases": machine_phases,
        "execution_plan": {
            "active_phase_ids": phase_summary["active_phase_ids"],
            "active_dependency_graph": phase_summary["active_dependency_graph"],
            "skipped_phases": phase_summary["skipped_phases"],
            "profile": profile.get("profile"),
            "validation_tier": profile.get("validation_tier"),
            "state_change_expected": profile.get("state_change_expected"),
            "rule": "execute enabled phases in dependency order; checkpoint only on transitions, exceptions, fallback use, validation failures, or approval boundaries; skipped phases are explicit evidence, not failures",
        },
        "maintenance": unique_limited(maintenance, 8),
        "validation": unique_limited(validation, 8),
        "validation_tiers": validation_tiers_contract(validation),
        "complexity_budget": {
            "max_selected_domains": 3,
            "max_selected_skills": 4,
            "max_selected_slash_templates": 3,
            "default_no_broad_scans": True,
            "default_no_state_mutation": True,
        },
        "structured_closeout": structured_closeout_contract(),
        "closeout": closeout_steps(),
    }
    environment_context = build_environment_context(
        message=message,
        domain_keys=selected_domain_keys,
        task_facts=task_facts,
        selected_skills=selected_skills,
        matrix_terms=matrix_terms,
    )
    plan["execution_route_pack"] = build_execution_route_pack(
        plan,
        environment_context=environment_context,
    )
    plan["structured_route"]["route_decision_ref"] = "execution_route_pack.route_decision"
    plan["structured_route"]["asset_guidance_ref"] = "execution_route_pack.asset_guidance"
    plan["structured_route"]["decision_owner"] = "execution_route_pack"
    plan["structured_route"]["downstream_rule"] = (
        "consume_structured_route.task_contract_then_execution_route_pack.route_decision; do_not_reclassify_raw_message_without_new_evidence"
    )
    detail_level = infer_detail_level(profile, selected_domains, requested=detail)
    return apply_detail_level(plan, detail_level)


def snapshot() -> dict[str, Any]:
    slash = load_slash_commands()
    return {
        "schema": "workflow_orchestrator.snapshot.v1",
        "ok": True,
        "generated_at": now_iso(),
        "paths": {
            "workspace": str(ROOT),
            "global_agents": str(GLOBAL_AGENTS),
            "workspace_agents": str(WORKSPACE_AGENTS),
            "agents_rule_mirror": str(AGENTS_MIRROR),
            "capability_matrix": str(MATRIX),
            "maintenance_surface_map": str(MAINTENANCE_SURFACE_MAP),
            "code_maintainability": str(CODE_MAINTAINABILITY),
            "maintenance_upgrade_governance": str(MAINTENANCE_UPGRADE_GOVERNANCE),
            "environment_context": str(ENVIRONMENT_CONTEXT),
            "module_capability_index": str(MODULE_CAPABILITY_INDEX),
            "module_context_entrypoint": f"{CODE_MAINTAINABILITY} module-context",
            "module_lookup_entrypoint": f"{CODE_MAINTAINABILITY} lookup-module",
            "slash_registry": str(SLASH_REGISTRY),
            "skill_orchestrator": str(BRIDGE / "skill_orchestrator.py"),
        },
        "exists": {
            "global_agents": GLOBAL_AGENTS.exists(),
            "workspace_agents": WORKSPACE_AGENTS.exists(),
            "agents_rule_mirror": AGENTS_MIRROR.exists(),
            "capability_matrix": MATRIX.exists(),
            "maintenance_surface_map": MAINTENANCE_SURFACE_MAP.exists(),
            "code_maintainability": CODE_MAINTAINABILITY.exists(),
            "maintenance_upgrade_governance": MAINTENANCE_UPGRADE_GOVERNANCE.exists(),
            "environment_context": ENVIRONMENT_CONTEXT.exists(),
            "module_capability_index": MODULE_CAPABILITY_INDEX.exists(),
            "slash_registry": SLASH_REGISTRY.exists(),
            "skill_orchestrator": (BRIDGE / "skill_orchestrator.py").exists(),
        },
        "domain_count": len(DOMAINS),
        "slash_command_count": len({str((item.get("name") or "")) for item in slash.values() if isinstance(item, dict)}),
        "contracts": {
            "read_only": True,
            "executes_task_actions": False,
            "writes_memory": False,
            "mutates_business_state": False,
            "duplicates_tool_table": False,
        },
    }


def doctor() -> dict[str, Any]:
    snap = snapshot()
    issues: list[dict[str, str]] = []
    for key, exists in snap["exists"].items():
        if not exists:
            if key == "module_capability_index":
                issues.append(
                    {
                        "severity": "advisory",
                        "code": "module_capability_index_missing",
                        "message": "Derived module capability index is missing; rebuild with code_maintainability.py build-module-index.",
                    }
                )
                continue
            issues.append({"severity": "blocker", "code": f"{key}_missing", "message": f"Required file missing: {key}"})
    slash = load_slash_commands()
    for domain in DOMAINS + (DEFAULT_DOMAIN,):
        for command in domain.slash:
            if command not in slash:
                issues.append(
                    {
                        "severity": "risk",
                        "code": "slash_template_missing",
                        "message": f"{domain.key} references missing slash template {command}",
                    }
                )
    if MAINTENANCE_SURFACE_MAP.exists():
        text = MAINTENANCE_SURFACE_MAP.read_text(encoding="utf-8", errors="replace")
        required_terms = (
            "workflow_orchestrator.py",
            "mcp_session_doctor.py",
            "local_mcp_hub.py",
            "backup_hygiene_doctor.py",
            "memory_governance.py",
            "code_maintainability.py",
            "maintenance_upgrade_governance.py",
            "tool_coordination.py",
            "Do not run every doctor by default",
        )
        missing_terms = [term for term in required_terms if term not in text]
        if missing_terms:
            issues.append(
                {
                    "severity": "risk",
                    "code": "maintenance_surface_map_incomplete",
                    "message": f"Maintenance surface map missing terms: {', '.join(missing_terms)}",
                }
            )
    skill_orchestrator_path = BRIDGE / "skill_orchestrator.py"
    if not skill_orchestrator_path.exists():
        issues.append(
            {
                "severity": "risk",
                "code": "skill_orchestrator_missing",
                "message": "Optional MySkills-aware skill orchestration surface is missing.",
            }
        )
    severities = {item["severity"] for item in issues}
    status = "unhealthy" if "blocker" in severities else ("degraded" if "risk" in severities else "ok")
    return {
        "schema": "workflow_orchestrator.doctor.v1",
        "ok": status == "ok",
        "generated_at": now_iso(),
        "status": status,
        "issues": issues,
        "snapshot": snap,
    }


def validate() -> dict[str, Any]:
    doc = doctor()
    skill_routing_context = None
    if prepare_skill_routing_context is not None:
        try:
            skill_routing_context = prepare_skill_routing_context()
        except Exception:  # noqa: BLE001 - normal plan fallback still validates optional skill failure handling.
            skill_routing_context = None
    validation_plan = lambda message, detail="full": build_plan(  # noqa: E731 - shared-context validation adapter.
        message,
        detail=detail,
        skill_routing_context=skill_routing_context,
    )
    plans = [validation_plan(sample) for sample in VALIDATION_SAMPLES]
    checks = build_validation_checks(
        doc,
        plans,
        validation_plan,
        MAINTENANCE_SURFACE_MAP,
        AGENTS_MIRROR,
        CODE_MAINTAINABILITY,
    )
    codegraph_plan = validation_plan("代码调用路径与影响范围分析", detail="full")
    codegraph_policy = codegraph_plan.get("tools", {}).get("codegraph_policy", {})
    central_priority = _compact_call_priority(
        call_priority_pack("codegraph", "codegraph_explore", "code_structure")
    )
    expected_sequence = [
        f"{step.get('id')}: {step.get('action')}"
        for step in central_priority.get("steps", [])
        if isinstance(step, dict)
    ]
    checks.append(
        {
            "name": "codegraph_priority_uses_central_pack",
            "ok": (
                codegraph_policy.get("call_priority") == central_priority
                and codegraph_policy.get("preferred_sequence") == expected_sequence
            ),
            "detail": {
                "required_first_step": central_priority.get("required_first_step"),
                "workflow_first_step": codegraph_policy.get("call_priority", {}).get("required_first_step"),
            },
        }
    )
    retirement_members = []
    if build_retirement_signal is not None:
        retirement_members = list(build_retirement_signal(message="retirement guard probe").get("do_not_route", []))
    retirement_probe_member = retirement_members[0] if retirement_members else ""
    retirement_message = f"清除 {retirement_probe_member} 退役成员残留" if retirement_probe_member else "retirement guard probe"
    retirement_plan = validation_plan(retirement_message, detail="full")
    retirement_guard = retirement_plan.get("retirement_guard", {})
    retirement_guard_ok = (
        bool(retirement_guard.get("triggered")) and retirement_probe_member in retirement_guard.get("do_not_route", [])
        if retirement_probe_member
        else retirement_guard.get("status") == "clear" and not retirement_guard.get("triggered")
    )
    checks.append(
        {
            "name": "retirement_guard_reaches_workflow_plan",
            "ok": retirement_guard_ok,
            "detail": {
                "status": retirement_guard.get("status"),
                "probe_member": retirement_probe_member,
                "do_not_route": retirement_guard.get("do_not_route", []),
            },
        }
    )
    member_change_plan = validation_plan("新增一个 MCP server 并纳入工作环境", detail="micro")
    member_route = member_change_plan.get("execution_route_pack", {}).get("route_decision", {})
    member_gates = member_route.get("required_gates", []) if isinstance(member_route, dict) else []
    ordinary_plan = validation_plan("查询一个 GitHub 项目", detail="micro")
    ordinary_route = ordinary_plan.get("execution_route_pack", {}).get("route_decision", {})
    ordinary_gates = ordinary_route.get("required_gates", []) if isinstance(ordinary_route, dict) else []
    checks.append(
        {
            "name": "system_member_change_gate_reaches_micro_route_pack",
            "ok": bool(member_gates) and not ordinary_gates and bool(member_change_plan.get("profile", {}).get("state_change_expected")),
            "detail": {
                "member_gate_schemas": [item.get("schema") for item in member_gates if isinstance(item, dict)],
                "ordinary_gate_count": len(ordinary_gates),
                "state_change_expected": member_change_plan.get("profile", {}).get("state_change_expected"),
            },
        }
    )
    environment_validation = validate_environment_context()
    checks.append(
        {
            "name": "environment_context_owner_validation",
            "ok": bool(environment_validation.get("ok")),
            "detail": [
                item.get("name")
                for item in environment_validation.get("checks", [])
                if isinstance(item, dict) and not item.get("ok")
            ],
        }
    )
    ordinary_environment = ordinary_plan.get("execution_route_pack", {}).get("environment_context", {})
    ordinary_systems = {
        item.get("system")
        for item in ordinary_environment.get("relevant_systems", [])
        if isinstance(item, dict)
    }
    checks.append(
        {
            "name": "micro_plan_preserves_environment_orientation",
            "ok": (
                "workflow" in ordinary_systems
                and ordinary_environment.get("architecture_chain")
                == ["instructions", "workflow_decision", "owner_contract", "tool_execution", "owner_state", "validation_closeout"]
                and bool(ordinary_environment.get("tool_entrypoints"))
                and bool(ordinary_environment.get("source_refs"))
            ),
            "detail": {
                "systems": sorted(str(item) for item in ordinary_systems if item),
                "architecture_chain": ordinary_environment.get("architecture_chain", []),
            },
        }
    )
    standard_environment_plan = validation_plan("目前的工作机制需要优化精简，减少上下文消耗", detail="standard")
    standard_environment = standard_environment_plan.get("execution_route_pack", {}).get("environment_context", {})
    standard_members = [
        {
            "member": system.get("member"),
            "source": system.get("member_source"),
        }
        for system in standard_environment.get("relevant_systems", [])
        if isinstance(system, dict)
        and system.get("member")
    ]
    checks.append(
        {
            "name": "standard_plan_adds_bounded_traceable_member_context",
            "ok": (
                bool(standard_members)
                and all(member.get("source") for member in standard_members)
                and len(standard_environment.get("relevant_systems", [])) <= 5
                and len(standard_environment.get("mcp_routes", [])) <= 4
            ),
            "detail": {
                "member_count": len(standard_members),
                "system_count": len(standard_environment.get("relevant_systems", [])),
                "mcp_route_count": len(standard_environment.get("mcp_routes", [])),
            },
        }
    )
    return {
        "schema": "workflow_orchestrator.validate.v1",
        "ok": all(check["ok"] for check in checks),
        "generated_at": now_iso(),
        "checks": checks,
        "sample_domains": [[item["key"] for item in plan["domains"]] for plan in plans],
    }


def metrics() -> dict[str, Any]:
    slash = load_slash_commands()
    return {
        "schema": "workflow_orchestrator.metrics.v1",
        "ok": True,
        "generated_at": now_iso(),
        "domain_count": len(DOMAINS),
        "slash_alias_count": len(slash),
        "read_only": True,
    }


def cli_projection(payload: dict[str, Any], command: str, *, full: bool = False) -> dict[str, Any]:
    if command != "validate":
        return payload
    return aggregate_validator_cli_payload(
        payload,
        full=full,
        full_result_ref="command:python _bridge/workflow_orchestrator.py validate --full",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only workspace workflow orchestrator")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("snapshot", "doctor", "metrics"):
        sub.add_parser(command)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--full", action="store_true", help="Emit every validation check instead of the actionable summary.")
    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("--message", required=True)
    plan_parser.add_argument("--risk", default="unknown")
    plan_parser.add_argument("--detail", choices=["micro", "standard", "full", "auto"], default="full")
    args = parser.parse_args(argv)

    if args.command == "snapshot":
        payload = snapshot()
    elif args.command == "doctor":
        payload = doctor()
    elif args.command == "validate":
        payload = validate()
    elif args.command == "metrics":
        payload = metrics()
    elif args.command == "plan":
        payload = build_plan(args.message, risk=args.risk, detail=args.detail)
    else:  # pragma: no cover
        parser.error(f"unsupported command: {args.command}")
    print_json(cli_projection(payload, args.command, full=bool(getattr(args, "full", False))))
    return 0 if payload.get("ok", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
