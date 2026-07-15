---
name: codex-cli
description: "OpenAI Codex CLI configuration, models, providers, MCP, permissions, and Reasonix bridge integration for this workspace."
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
All user and project-specific skills live in `~/.codex/skills/`; project roots do not own separate active skill copies.
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

---

## 八、受控迭代层

本项目使用受控迭代层处理工作经验、工具更新和技能优化建议。默认入口是只读命令：

```powershell
python _bridge/iteration_layer_review.py --dry-run
```

该命令只生成提案，不修改技能、记忆、工具注册表、CLI 或项目文件。任何持久化更新仍必须先询问用户，并按项目规则创建标记备份。

### 8.0 工作流守门

系统级任务开始前先运行只读记忆前置检查，避免 PMB 记忆体空闲搁置或当前会话 MCP 断开却未被发现：

```powershell
python _bridge/codex_workflow_gate.py memory-preflight --message "<用户请求>" --check-session
```

如果输出 `should_prepare_memory=true` 且无 blocker，开始实质分析前必须调用 PMB `prepare` 或 `recall`。如果守门器显示 `local_pmb_memory_session_smoke_failed` 或当前会话工具调用报 `Transport closed`，不得假装已读取记忆；应在当前任务里说明“记忆前置被阻断”，并使用 CLI/维护输出作为只读 fallback，同时安排 MCP 会话刷新。

大变动、框架性修改、系统级修复、工具状态变化、用户纠正、可复用根因产生后，收尾前必须运行只读收口守门：

```powershell
python _bridge/codex_workflow_gate.py finalization-gate --message "<本轮工作摘要>" --include-approval-block
```

如果 `requires_iteration_review=true`，最终回复必须附带该守门器生成的中文迭代审批块，供用户确认。该块只提交方案，不代表授权；未获确认前不得写入技能、长期记忆、项目知识、CLI 或桥接状态。

JSON 输出会包含 `proposal_packages`，每个包都必须视为待确认草案；不得把它们自动写入目标文件或记忆系统。
同时关注 `proposal_groups` 和 `recommended_next_actions`：

- `proposal_groups` 用于按优先级和目标归类提案，避免把近期文件元数据、已固化规则和真正需要审查的自动化改进混在一起。
- `recommended_next_actions` 是下一步建议，不是授权；只有用户确认后才能备份、修改和验证。

大变动或框架性工作完成后，如果本轮产生了可复用规则、技能/记忆/CLI/项目知识候选、工具状态变化或用户纠正，最终回复必须附带中文可读的“迭代提案”块，而不是只说“已生成 proposal”。优先由 `codex_workflow_gate.py finalization-gate --include-approval-block` 触发；也可用短输出直接生成：

```powershell
python _bridge/iteration_layer_review.py --approval-only --recent-limit 8
```

该块必须用中文说明状态、模式、默认是否批准、建议范围、未确认前禁止项和等待用户确认的请求。它只用于让用户审阅和批准，不等于授权，也不得触发自动推广。

避免重复展示：只有出现新的可审批内容时，才完整附带“迭代提案”。如果本轮只是执行、记录或验证上一条已经确认的提案，最终回复应写“已按上一条提案执行”，或使用简短执行结果输出：

```powershell
python _bridge/iteration_layer_review.py --approval-only --approval-context execution-result --recent-limit 8
```

如果确实需要再次展示，应明确标注“这是新的提案”或“这是上次提案的执行结果”，避免把同一审批块反复发送。

验证闭环默认只展示 `validation_plan`。只有在用户批准验证执行时，才运行：

```powershell
python _bridge/iteration_layer_review.py --run-validation --validation-profile quick
```

该验证只调用已知安全检查，并把结果作为 `validation_results` 输出。日常收尾默认使用
`quick` 档；需要完整巡检时显式运行：

```powershell
python _bridge/iteration_layer_review.py --run-validation --validation-profile full
```

验证步骤有单步超时保护，可用 `--validation-timeout <seconds>` 调整。超时会作为
`timed_out=true` 的结构化结果写入报告，不应让主线程无限等待。
CDP 常规验证使用 `cdp-route-quick-check`，只检查 TCP 与 `/json/version`；
`cdp-route-doctor-check` 保留为专项回归诊断，不作为日常迭代验证的默认路径。
维护消费层已经接入该只读决策：

- `maintenance summary` 输出面向人的 `Iteration Decision` 摘要，适合快速查看当前主批次、主边界以及 ready/validation-first 分流。
- `maintenance doctor` 在 `diagnosis` 之外单独返回 `advisories`，用于承载只读的下一步建议，不污染真实健康问题。
- `maintenance repair` dry-run 同样透传 `advisories`，方便在修复计划视图里统一消费“修完之后看什么”，但这不会改变 repair 语义，也不会自动执行推广。

`kpb-001` 目前优先固化的经验是：

- `tool-registry-health` 属于桥接/工具能力判断前的优先验证路径。
- `resource-layer-smoke-check` 属于资源层的 bounded validation 路径。
- 上述内容先作为 proposal-only 的技能或 CLI 规则候选，不直接写入运行逻辑。

批准推广 `kpb-001` 后，相关技能、基线、项目知识或 CLI 自动化候选在落地前必须保持一个小而稳定的验证闭环：

```powershell
python _bridge\iteration_layer_review.py --dry-run --recent-limit 3
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py tool-registry-health
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py resource-layer-smoke-check
python _bridge\memory_governance.py validate
```

这四项分别验证：迭代层仍是只读提案、工具注册与运行能力未漂移、资源层仍能安全物化/分析资源、长期记忆治理与 PMB 入口可用。它们是推广前检查，不是授权本身；任何写入仍需要本轮明确批准、备份、窄范围修改和事后验证。

### 8.0.1 Codex/MCP 重启稳定性基线

处理“重启后配置或能力丢失”时，优先把问题拆成三层，而不是直接重装或全量恢复配置：

1. `baseline` 是否覆盖当前全局能力：
   - 只读检查：`python _bridge\codex_baseline_update.py --check-current`
   - 如果全局配置里的 MCP/插件是用户确认的新能力，而 baseline 落后，再运行 `--adopt-current`。
   - 采用 additive convergence；不得因为一次 live snapshot 缺项就从 baseline 裁剪 MCP、插件、权限、记忆或项目 trust。
2. Hub 登录自启动是否能恢复：
   - 只读检查：`python _bridge\local_mcp_hub.py doctor`
   - `CodexLocalMcpHub` 任务应启用 `StartWhenAvailable`，并有有限重试次数和重试间隔。
   - 启动脚本只能受控重启命令行包含 `local_mcp_hub.py` 的监听进程；非 Hub 占用端口必须报错，不得乱杀进程。
3. 当前 Codex turn 的工具绑定是否刷新：
   - `local_mcp_hub.py smoke/validate` 或 HTTP health 只能证明 Hub 服务健康。
   - `mcp_session_doctor.py validate` 的 advisory 需要继续区分 config/protocol/current-turn callability。
   - 不把 Hub 或 protocol smoke 成功误记为 native MCP 当前 turn 可调用。

日常回归最小命令：

```powershell
python _bridge\codex_baseline_update.py --check-current
python _bridge\codex_config_guard.py validate
python _bridge\local_mcp_hub.py validate
powershell -NoProfile -ExecutionPolicy Bypass -File _bridge\shared\run-local-mcp-hub.ps1 -MaxAttempts 2 -HealthWaitSeconds 4
```

### 8.1 固化规则

触发场景：

- 用户要求总结经验、更新技能、更新记忆、优化 Codex 框架、自动进化、进入下一阶段。
- 工作中出现可复用故障根因、工具状态变化、用户纠正或稳定流程。

执行顺序：

1. 先运行或参考 `python _bridge/iteration_layer_review.py --dry-run`。
2. 查看 `proposal_packages`，只把它们作为待确认草案。
3. 查看 `proposal_groups` 和 `recommended_next_actions`，先处理 P0/P1，跳过没有明确复用价值的观察项。
4. 大变动收尾时，只有在出现新的可审批内容时，才把 `python _bridge/iteration_layer_review.py --approval-only --recent-limit 8` 的可读提案块附在最终回复里，供用户确认。
5. 对任何持久化更新，先说明目标、风险、备份和验证方式，并等待用户确认。
6. 确认后创建 `_bridge/backups/<timestamp>-<slug>/` 标记备份。
7. 如果只是落地/记录/验证上一条已确认提案，不重复贴完整提案块；可用 `python _bridge/iteration_layer_review.py --approval-only --approval-context execution-result --recent-limit 8` 输出简短执行结果。
8. 修改后运行相关验证；需要日常整体闭环时运行 `python _bridge/iteration_layer_review.py --run-validation --validation-profile quick`，需要深度巡检时再运行 `--validation-profile full`。
9. 如果维护层已经给出 `Iteration Decision` 或 `advisories`，把它们当成只读消费结果：它们帮助排序下一步，但不等于授权，也不替代用户确认。

P0/P1 优先级：

- P0：安全边界、只读提案、显式确认、备份、回滚、结构化验证。
- P1：工具路由、慢检查拆分、技能触发收紧、记忆写入边界、GUI 高频流程沉淀。
- P2：项目文档和知识补充，只在有稳定事实且不含临时噪声时处理。

系统级路由：

- 优先使用 MCP/API/项目 CLI，再使用结构化脚本和 PowerShell，最后才使用 GUI。
- 健康状态优先看 `tool-registry-health`、`maintenance summary`、`cdp-route-quick-check` 和迭代层 quick 验证。
- `_bridge/tmp`、临时 smoke 文件、依赖目录、日志、缓存、备份文件只可作为当前诊断证据，不得直接提升为长期知识。

硬性边界：

- 不自动修改技能、记忆、工具注册表、CLI、项目配置或 Codex 核心。
- 不自动删除桥接任务、消息记录或投递历史。
- 不自动重发微信结果。
- 不自动切换 CDP/MCP/app-server 主路由。
- 不把未验证推断写入长期记忆。
- 桥接专项验证命令只用于检查，不允许顺手执行 repair、reply send、任务清空或路由切换。

### 8.2 工作后迭代确认规则

每次实质性工作完成后，先自评是否需要进入受控迭代层：

- 如果没有产生可复用经验、稳定规则、工具状态变化、用户纠正或已验证根因，说明不需要迭代。
- 如果需要迭代，先询问用户，并说明拟更新目标、原因、风险、备份和验证方式。
- 只有收到用户明确同意后，才创建备份、执行更新并运行验证。
- “同意/确认/批准/进行下一阶段”只对当前明确提案生效，不得作为以后自动迭代的永久授权。
- “同意所有提案”表示可按本次报告中的当前提案批量落地，但仍要逐类执行备份、限定目标、运行验证；它不允许改队列、发微信、清任务、切路由或写入未在报告中出现的目标。
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

### 10.0 工作区资源获取策略

在 `mcsmanager` 工作区内，Codex 遇到下载、附件落盘、依赖资源获取、本地资源复制、哈希校验、缓存检查时，优先使用项目资源获取层，而不是临时调用 `curl`、`Invoke-WebRequest` 或一次性下载脚本。

统一入口：

```powershell
python _bridge\resource_cli.py acquire --intent explicit_user_url --stage probe --url "<url>" --json
python _bridge\resource_cli.py acquire --intent explicit_user_url --stage preview --url "<url>" --json
python _bridge\resource_cli.py acquire --intent explicit_user_url --stage materialize --url "<url>" --target-dir _bridge\resources --json
python _bridge\resource_cli.py acquire --intent explicit_local_file --path "<local-path>" --target-dir _bridge\resources --json
python _bridge\resource_cli.py classify-url "<url>" --json
python _bridge\resource_cli.py strategy-review --hide-legacy --json
python _bridge\resource_cli.py verify "<local-path>" --sha256 <digest> --json
python _bridge\resource_cli.py inspect-cache --target-dir _bridge\resources
python _bridge\resource_cli.py clean-cache --target-dir _bridge\resources --older-than-days 30 --dry-run
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py resource-layer-smoke-check
```

边界：

- 资源获取层是本项目 Codex 的默认资源核心，覆盖下载、本地文件纳入、校验、缓存和烟测。
- `_bridge/resource_fetcher.py` 是核心层，负责 URL、本地文件、bytes 资源的获取、复制、sha256、大小限制、缓存命中、原子落盘和 JSONL 日志。
- `_bridge/resource_cli.py` 是 Codex/脚本使用层，提供稳定命令接口。
- `_bridge/file_toolkit` 只分析已经存在的本地文件，不负责下载或复制。
- 新工作流优先使用 `acquire --intent ... --stage ...`；`fetch-url` 和 `fetch-file` 只是兼容旧脚本的快捷入口，内部仍必须写入 intent/stage/policy 元数据。
- 显式用户 URL 才能默认物化；内联 URL、依赖 URL、文档 URL 应先走 `discover`/`probe`/`preview` 或 deferred，不应被误当成附件下载。
- 不确定 URL 语义时先运行 `classify-url`。它只读分类并给出 recommended intent/stage，不抓网、不安装、不落盘。
- 查看当前资源策略时可用 `strategy-review --hide-legacy` 过滤历史 legacy CLI 噪音；完整审计仍可不加该参数。
- URL 物化路径只接受明确允许的 http/https policy；遇到 unusual scheme 时先做来源和任务风险审查，不绕过资源层直接下载。
- `--max-bytes` 是可选保护参数，不是大文件下载的强制前置条件。
- `fetch-url` 默认仍按 `explicit_user_url` 兼容旧行为并启用有限重试；需要新流程时用 `acquire --intent ... --stage ...` 明确表达语义。
- 提供 `--sha256` 时必须强校验，失败不入缓存。
- 不直接修改 Codex 安装目录或宿主下载逻辑；需要接管 Codex 宿主资源下载前，必须先做单独审计、备份、回归和用户确认。

### 10.0.1 工具注册表与健康检查

在 `mcsmanager` 工作区内，Codex 判断本机工具、MCP、app-server、CDP 或桥接状态前，优先使用项目工具注册表和统一健康命令，而不是临时散查。

统一入口：

```powershell
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py tool-registry-health
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py tool-registry-drift-check
```

注册表：

```text
_bridge/mobile_openclaw_bridge/TOOL_REGISTRY.md
```

边界：

- `tool-registry-health` 是只读命令，输出工具命令、关键项目脚本、OpenClaw Gateway、codex-app-server、CDP、队列状态和推荐工具调用顺序。
- `tool-registry-drift-check` 是只读命令，用实时健康结果对比 `TOOL_REGISTRY.md` 静态说明，发现漂移只报告，不修复。
- `ok=true` 只表示关键路径可用；`status=degraded` 可能来自 CDP 不可用或可选命令缺失。
- `maintenance summary` 的 quick 模式会明确列出 `Probe Evidence Boundary`：`skipped` 表示没深探，不等于故障；真实非 OK 层会单独列出。
- CDP 必须区分 live listener 与 stale OS listener；只有 live listener 且 `/json/version` 可响应时，visible desktop CDP 路线才算可用。
- app-server 是 backup 账号稳定后台路线；CDP 是 primary/current-window 可见交互路线，不应自动互相切换，除非用户明确确认。
- 补充消息必须通过 mobile MCP `bridge.get_pending_batch` 显式读取，并用 `bridge.ack_message` 消费；不要假设补充内容会自动进入模型上下文。

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
