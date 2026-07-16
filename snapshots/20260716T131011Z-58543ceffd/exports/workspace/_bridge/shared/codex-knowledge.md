---
name: codex-cli
description: OpenAI Codex CLI 架构、能力、配置和生态知识，包含本机实例详情、Reasonix 对比表和 Agent Bridge 集成方案。
---

# Codex CLI 知识库

> OpenAI Codex CLI 的架构、能力、配置和生态知识。用于理解本机 Codex 实例、设计 Agent Bridge 交互方案、排查兼容性问题。

---

## 一、Codex 概述

| 维度 | 详情 |
|------|------|
| **全称** | OpenAI Codex CLI |
| **仓库** | github.com/openai/codex |
| **语言** | Rust 96.2%, Python 2.8%, Starlark 0.2% |
| **许可** | Apache-2.0 |
| **当前版本** | 0.140.0 (2026-06-15) |
| **GitHub Stars** | 91.5k |
| **安装** | `npm -g @openai/codex` / `brew install --cask codex` / PowerShell 一键脚本 |
| **形态** | CLI (tui) + IDE 插件 (VS Code/Cursor/Windsurf) + Desktop App + Web (chatgpt.com/codex) |

### 本机实例

| 配置 | 值 |
|------|-----|
| **安装路径** | `C:\Users\45543\AppData\Local\OpenAI\Codex\` |
| **CLI 版本** | 0.140.0-alpha.19 (2026-06-16) |
| **Codex++桌面版** | `C:\Users\45543\AppData\Local\Programs\Codex++\codex-plus-plus.exe` |
| **配置目录** | `~/.codex/` |
| **项目目录** | `mcsmanager/` (Minecraft 服务端运维) |
| **模型** | gpt-5.4 (custom provider, 127.0.0.1:15721, wire_api=responses) |
| **推理强度** | high |
| **沙箱模式** | `danger-full-access` (对 mcsmanager 项目) |

---

## 二、核心架构

### 2.1 代码结构

```
codex/
├── codex-rs/          ← Rust 主体 (~96%)
│   ├── core/           ← 核心引擎 (配置、上下文、会话、沙箱)
│   ├── tui/            ← 终端 UI (ratatui 框架)
│   ├── codex-mcp/      ← MCP 协议实现
│   ├── app-server/     ← IDE 插件通信 (v2 API)
│   └── ...
├── codex-cli/          ← CLI 入口
├── sdk/                ← SDK
├── docs/               ← 不在仓库内，另托管
└── AGENTS.md           ← 给 AI 开发者的项目级指令
```

### 2.2 启动流程

```
codex 命令
  ↓
加载 config.toml (home → project 层级合并)
  ↓
连接模型 (custom provider / ChatGPT API)
  ↓
初始化沙箱 (danger-full-access / workspace / ...)
  ↓
加载 Skills (skills/ 目录)
  ↓
加载 AGENTS.md + MEMORY.md (项目上下文)
  ↓
连接 MCP Servers (mcp_servers 配置)
  ↓
TUI 主循环开始
```

---

## 三、配置系统 (config.toml)

### 3.1 核心配置项

```toml
model_provider = "custom"        # chatgpt / custom
model = "gpt-5.4"
model_reasoning_effort = "high"  # low / medium / high / max

[model_providers.custom]
name = "My Codex"
base_url = "http://127.0.0.1:15721/v1"
wire_api = "responses"

[mcp_servers.<name>]             # MCP 服务器注册
command = '...'
args = [...]
startup_timeout_sec = 120

[windows]
sandbox = "elevated"             # Windows 沙箱级别

[features]                       # 功能开关
goals = true
js_repl = true
memories = true
```

### 3.2 沙箱模式

| 模式 | 说明 |
|------|------|
| `danger-full-access` | 完全访问（本机 mcsmanager 项目用此模式） |
| `workspace` | 限制在项目工作区 |
| `seatbelt` | macOS Seatbelt 沙箱 |
| `landlock` | Linux Landlock 沙箱 |

### 3.3 MCP Servers 配置格式

```toml
[mcp_servers.<name>]
command = '<executable>'
args = ['arg1', 'arg2']
startup_timeout_sec = 120

[mcp_servers.<name>.env]
ENV_VAR = "value"
```

### 3.4 Plugins 系统

```toml
[plugins."computer-use@openai-bundled"]
enabled = true

[plugins."browser@openai-bundled"]
enabled = true

[plugins."chrome@openai-bundled"]
enabled = true
```

---

## 四、Skills 技能系统

### 4.1 目录结构

```
~/.codex/skills/          ← 全局技能
所有用户维护技能统一位于 `~/.codex/skills/`；工作区不再维护独立技能根。
```

每个技能是一个目录，包含 `SKILL.md` 文件（YAML frontmatter + Markdown body），格式与 Reasonix 技能兼容。

### 4.2 本机已有技能

| 技能 | 位置 | 说明 |
|------|------|------|
| `mcsmanager-fabric-mc` | `~/.codex/skills/` | MC 服务端运维 |
| `fabric-mc-architecture` | `~/.codex/skills/` | Fabric 底层架构 (由 Reasonix 共享) |
| `context-compression` 等 7 个 | `mcsmanager/codex-skills-export/` | 通用技能集 |

---

## 五、AGENTS.md 系统

### 5.1 加载层级

```
~/.codex/AGENTS.md         ← 全局个性/规则
<project>/AGENTS.md         ← 项目级技术约束 (优先级更高)
```

### 5.2 本机 mcsmanager 的 AGENTS.md

对 Minecraft ClientModLoader 项目的技术约束（Fabric MOD 注入、编译命令、部署路径等）。

---

## 六、Memory 记忆系统

### 6.1 存储

```
~/.codex/memories/           ← Markdown 文件
~/.codex/memories_1.sqlite    ← 索引
MEMORY.md                     ← 项目级记忆 (自动生成)
```

### 6.2 配置

```toml
[memories]
generate_memories = true
use_memories = true
disable_on_external_context = true
```

---

## 七、多 Agent 系统

### 7.1 配置

```toml
[agents]
max_depth = 5              # 最大嵌套深度
max_threads = 10           # 最大并发 Agent 数
interrupt_message = true

[agents.<role_name>]
config_file = "path/to/agent-role.toml"
description = "..."
nickname_candidates = ["alice", "bob"]
```

### 7.2 多 Agent v2 特性

- Agent 可以有独立的配置文件和角色定义
- 支持嵌套（父 Agent 生子 Agent）
- 支持并发线程
- 中断消息机制

---

## 八、丰富功能

| 功能 | 说明 | 本机状态 |
|------|------|:--:|
| **Goals** | 持久化目标追踪 | ✅ |
| **JS REPL** | JavaScript 运行时 | ✅ |
| **Memories** | 跨会话记忆 | ✅ |
| **Computer Use** | 桌面自动化 | ✅ 已启用 |
| **Browser** | 内置浏览器 | ✅ 已启用 |
| **Chrome** | Chrome 控制 | ✅ 已启用 |
| **Web Search** | 网络搜索 | ✅ 可用 |
| **Shell Tool** | 终端命令执行 | ✅ |
| **SQLite** | 数据库操作 | ✅ 可用 |
| **Code Mode** | 专注编码模式 | ✅ 可用 |
| **Personality** | 个性定制 | ✅ |
| **Notifications** | 系统通知 | ✅ |
| **Hooks** | 事件钩子 | ✅ |
| **Collaboration** | 多人协作 | ✅ |
| **Sleep Tool** | 等待/延时 | ✅ |
| **Token Budget** | Token 预算管理 | ✅ |
| **Undo** | 操作撤销 | ✅ |
| **Telepathy** | Agent 间直接通信 | ⬜ 实验性 |

---

## 九、Reasonix ↔ Codex 能力对比

| 维度 | Codex | Reasonix |
|------|-------|----------|
| **语言** | Rust 96% | Go |
| **MCP** | ✅ 原生支持 | ✅ 原生支持 |
| **Skills** | ✅ SKILL.md | ✅ SKILL.md |
| **AGENTS.md** | ✅ | ❌ (用 Skills 替代) |
| **Memory** | ✅ MEMORY.md + SQLite | ✅ memory/ 目录 + 索引 |
| **沙箱** | ✅ 多模式 | ✅ sandbox 模式 |
| **浏览器** | ✅ Built-in Browser + Chrome | ⚠️ Playwright (需安装) |
| **多 Agent** | ✅ 原生 spawn | ❌ 需外部 Bridge |
| **桌面自动化** | ✅ Computer Use | ❌ |
| **Shell** | ✅ | ✅ |
| **SQLite** | ✅ 原生工具 | ❌ (需通过 bash) |
| **Git 集成** | ✅ codex_git_commit | ❌ |
| **IDE 集成** | ✅ VS Code / Cursor / Windsurf | ❌ |

---

## 十、实用模式

### 10.1 给 Codex 的任务格式

```markdown
## 任务
具体要做什么

## 约束
- 不要修改 X
- 完成后写入 _bridge/shared/result.md
```

### 10.2 通过 AGENTS.md 设置 Codex 行为

在项目的 AGENTS.md 中加入：
```markdown
## Agent Bridge 规则
- 启动时检查 _bridge/reasonix-to-codex/ 目录
- 完成后通过 agent_bridge_complete 回复
- 重大决策前写入 _bridge/shared/ 供 Reasonix 审阅
```

### 10.3 Codex 的 MCP 工具使用

Codex 连接 agent-bridge MCP 后，工具名前缀为插件名：
```
agent-bridge__agent_bridge_send(to="reasonix", ...)
agent-bridge__agent_bridge_receive(agent="codex")
```

---

## 十一、本机 Codex 深层剖析

### 11.1 行为规则 (~/.codex/AGENTS.md)

Codex 的本机全局规则（优先级高于项目 AGENTS.md）：

1. **修改文件前必须询问用户**
2. **修改时必须生成备份并标记**，以备还原
3. **每次工作完成后总结经验** → 完善技能 → 吸收新技能
4. **技能按框架搭建**，框架优化时征求用户同意

### 11.2 已积累的知识 (~/.codex/MEMORY.md)

```
- computer-use 桌面自动化 (sky 框架, Java/Swing 用 sky.press_key)
- 3c3u UUID=4495cc82..., username=lsd985211
- Java 25: C:\Program Files\BellSoft\LibericaJDK-25\bin\java.exe
- Node/npm: mindcraft-main\nodejs\
- Git 2.54.0 已安装
- GUI 交互可自动化，不过早放弃
- GLFW 窗口 SetCursorPos+mouse_event 可靠点击
- 先证实再断言，不加推测性前缀
```

### 11.3 核心项目: ClientModLoader

Codex 正在积极参与一个名为 **ClientModLoader** 的 Fabric MOD 注入项目：

**目标**: 通过 `addCodeSource` + `addToClassPath` 动态注入客户端 MOD，绕过 AutoModpack 的限制。

**技术栈**:
- `javac --release 25` 编译 Java 源码
- Fabric Loader 0.19.3 + Sponge Mixin 0.17.3 + Log4j API 2.25.2
- 编译输出: `clientmodloader/build/` → 打包为 `clientmodloader-1.0.0.jar`
- 部署到: `3c3u/mods/clientmodloader-1.0.0.jar`
- 测试环境: `C:\tmp\mc-clone-3c3u-test` (克隆 3c3u 实例)

**源码位置**: `mcsmanager/clientmodloader/src/main/java/pl/skidam/clientmodloader/ClientModLoader.java`

**已有规则** (default.rules 中预授权):
- 编译命令 (javac + jar)
- 测试环境克隆命令
- AutoModpack 源码下载 (Fabric Loader, ModDiscoverer, EntrypointStorage, LoaderManager)
- AutoModpack JAR 内省 (nested fabric.mod.json)

### 11.4 完整技能清单 (40+)

**Minecraft 系列** (14 个):
minecraft-server-admin, minecraft-modding, minecraft-plugin-dev, minecraft-datapack, minecraft-resource-pack, minecraft-world-generation, minecraft-worldedit-ops, minecraft-commands-scripting, minecraft-testing, minecraft-multiloader, minecraft-ci-release, minecraft-imagegen, minecraft-essentials-ops, mc-mod-automation

**MCSManager 项目** (4 个):
mcsmanager-fabric-mc, fabric-mc-26-1-2, codex-cli, fabric-mc-architecture

**系统技能** (5 个):
imagegen (图片生成), openai-docs (Codex 文档查询), plugin-creator, skill-creator, skill-installer

**通用技能** (10 个):
context-compression, diagnose, memory-systems, multi-agent-patterns, playwright, self-improvement, webapp-testing, find-docs, global-framework, context7-cli, context7-mcp

**Agent 集成** (1 个):
agent-browser (Windows 桌面自动化, 基于 Playwright)

### 11.5 插件系统详情

| 插件 | 状态 | 能力 |
|------|:--:|------|
| **computer-use** | ✅ | sky CUA 框架, 桌面截图+点击+键盘 |
| **browser** | ✅ | 内置浏览器 (In-app), 多标签, Playwright API |
| **chrome** | ✅ | Chrome CDP 控制, extension-host.exe |
| **latex** | ✅ | tectonic.exe (49MB), 编译 LaTeX |

### 11.6 MCP 工具命名约定

Codex 注册的 MCP 服务器工具名前缀为插件名:

```python
# node_repl MCP: node_repl__execute(...)
# agent-bridge MCP: agent-bridge__agent_bridge_send(to="reasonix", ...)
# context7 MCP: context7__query(...)
```

### 11.7 进程管理

Codex 使用 `~/.codex/process_manager/chat_processes.json` 管理多 Agent 进程生命周期，支持并发 Agent 线程。

### 11.8 Telepathy（实验性）

Codex 有一个实验性的 `telepathy` 功能标记，暗示原生 Agent 间直接通信能力。如果启用，可能不需要外部 Bridge。

---

> **来源:** github.com/openai/codex, 本机 ~/.codex/ 配置文件, config.schema.json  
> **挖掘深度:** global-state.json, MEMORY.md, AGENTS.md, default.rules, skills/ 目录树, 40+ 技能清单  
> **版本:** Codex 0.140.0, 2026-06-17
