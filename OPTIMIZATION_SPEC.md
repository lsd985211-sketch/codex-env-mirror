# Codex Environment Mirror — Optimization Specification

| 字段 | 值 |
|------|-----|
| Spec ID | `codex-mirror-opt-v2` |
| 控制平面版本基线 | `2.2.0` |
| 创建日期 | `2026-07-30` |
| 状态 | `draft` |
| 影响范围 | `scripts/mirror_cli.py`, `manifests/`, `tests/`, `AGENTS.md`, `README.md` |
| 修订 | v2: 合并联网搜索的行业最佳实践，新增 F9-F14 |

---

## 1. 执行摘要

本规范定义了对 Codex Environment Mirror 仓库的 14 项增强，分为四个实施阶段。所有增强保持以下不变量：

- 静态契约（`static_contract`）的语义变更需通过 `contract-review-plan` 治理。
- 快照资产保持 SHA-256 哈希验证，不可变。
- 激活始终所有者驱动，不被自动化绕过。
- 新增验证检查追加到 `verification-matrix.json`，不削弱现有检查。
- 沙箱权限遵循官方推荐：`workspace-write` + `on-request`，不使用 `danger-full-access`。

---

## 2. 背景与动机

### 2.1 当前状态

| 维度 | 当前值 |
|------|--------|
| 快照资产数 | 2189 |
| 总大小 | 27.7 MB |
| `mirror_valid` | `true` |
| `capability_restore_ready` | `true` |
| `full_state_restore_ready` | `false` |
| 必需外部存档缺口 | `codex-native-memory-state`, `codex-goal-state`, `mail-and-scheduler-state` |
| CLI 子命令数 | 8 |
| 验证矩阵检查数 | 26 |
| 技能总数 | 162+ |
| MCP 路由数 | 21 |
| 系统成员数 | 17 个系统，191 个能力 |
| 规则面数 | 33 个规则面，134 个契约 |
| PMB 记忆 daemon | 未运行（`pmb.exe` 缺失） |

### 2.2 核心痛点

1. **漂移检测结果不可读**：`validate --live-sources` 输出 JSON，人工排查困难。
2. **快照差异分析不足**：`compare-snapshots` 只输出资产级差异，缺少分类汇总。
3. **恢复流程断裂**：`stage` 写入隔离目录后，从 stage 到激活之间无引导。
4. **外部存档未闭环**：3 个必需存档缺失，阻止 `full_state_restore_ready`。
5. **刷新依赖手动触发**：无定时或变更触发的自动刷新。
6. **脱敏配置恢复困难**：`redact_json` 产出模板，恢复时需手动回填。
7. **缺少端到端恢复验证**：不验证恢复后的环境可用性。
8. **技能健康度不可见**：无法感知损坏、过期或冲突的技能。
9. **MCP 工具定义膨胀**：21 条路由的全量定义注入上下文窗口，浪费 token（行业最佳实践推荐渐进式发现）。
10. **MCP 服务器生命周期无管理**：缺少基于任务相关性的动态连接/断开。
11. **记忆系统缺乏时序感知**：当前记忆为扁平结构，无法区分持久事实与易变事实，无法解决时间冲突。
12. **沙箱权限缺乏审计**：快照不记录 Codex 的沙箱配置，恢复时无法验证权限一致性。
13. **会话缺少归档和审计**：无会话级别的操作审计链。
14. **AGENTS.md 分层未验证**：全局/workspace/bridge 三层规则的优先级和一致性未自动验证。

---

## 3. 目标与非目标

### 3.1 目标

- G1：提供人类可读的漂移检测报告，按严重程度分类。
- G2：增强快照差异分析，支持变更分类汇总和自动 CHANGELOG 生成。
- G3：提供交互式恢复向导，从 stage 到激活全流程引导。
- G4：统一外部加密存档管理，将 `full_state_restore_ready` 推进为 `true`。
- G5：支持定时和文件监听触发的自动刷新。
- G6：引入模板化配置模式，支持变量替换和 vault 集成。
- G7：在隔离沙箱中执行端到端恢复验证。
- G8：扫描技能健康度，生成报告并集成到验证矩阵。
- G9：实现 MCP 工具渐进式发现，减少上下文窗口占用。
- G10：实现 MCP 服务器动态管理，按任务相关性连接/断开。
- G11：引入时序知识图谱记忆，支持事实有效期和冲突解决。
- G12：快照记录并验证沙箱权限配置。
- G13：实现会话归档和审计链。
- G14：验证 AGENTS.md 三层规则的优先级和一致性。

### 3.2 非目标

- N1：不将本仓库变为实时配置权威（`authority_mode` 保持 `derived_snapshot`）。
- N2：不自动激活恢复状态（`activation_rule` 保持 `owner-driven`）。
- N3：不镜像密钥、Provider/Model 配置或插件载荷。
- N4：不替换 WSL Work Git facade 作为正常操作入口。
- N5：不引入非标准库依赖到 `scripts/mirror_cli.py`（保持纯 Python 标准库）。
- N6：不使用 `danger-full-access` 沙箱模式（遵循 Codex 官方安全预设）。
- N7：不替换现有 PMB 记忆服务，而是为其增加时序图层。

---

## 4. 架构原则

所有增强遵循以下原则：

```
┌──────────────────────────────────────────────────────────────────┐
│                 Control Plane v2.4.0                             │
│  (control-plane-contract.json + verification-matrix)             │
├──────────────┬───────────────┬────────────────┬──────────────────┤
│  Phase 1     │   Phase 2     │   Phase 3      │   Phase 4        │
│  可观测性     │   闭环能力     │   自动化与质量  │   智能与安全      │
├──────────────┼───────────────┼────────────────┼──────────────────┤
│ F1: drift    │ F3: restore   │ F5: schedule   │ F9: prog-discovery│
│ F2: diff     │ F4: archive   │ F6: template   │ F10: dyn-mcp-mgmt │
│              │               │ F7: test-restore│ F11: temporal-mem │
│              │               │ F8: skill-health│ F12: sandbox-audit│
│              │               │                │ F13: session-audit│
│              │               │                │ F14: agents-validate│
└──────────────┴───────────────┴────────────────┴──────────────────┘
```

- **Phase 1（可观测性）**：只读增强，不改变快照格式，风险最低。
- **Phase 2（闭环能力）**：涉及外部存档和恢复流程，改变 `full_state_restore_ready` 语义。
- **Phase 3（自动化与质量）**：引入主动行为和深度验证。
- **Phase 4（智能与安全）**：基于行业最佳实践的智能化增强，引入时序记忆和 MCP 优化。

---

## 5. 功能规格

### F1: 漂移检测可视化（`drift`）

#### 5.1.1 概述

新增 `drift` 子命令，将 `validate --live-sources` 的结果转换为人类可读的差异报告。

#### 5.1.2 CLI 接口

```
python scripts/mirror_cli.py drift [--snapshot latest] [--format human|json] [--severity critical|medium|low|all]
```

#### 5.1.3 严重程度分类规则

| 严重程度 | 触发条件 |
|----------|---------|
| `critical` | `required: true` 的源资产被删除或哈希不匹配 |
| `medium` | `required: false` 的源资产变更，或生成源的命令输出不匹配 |
| `low` | 新增文件（源中有、快照中无），且不属于排除规则 |
| `info` | 变更资产属于 `reacquire` / `regenerate` / `runtime` disposition |

#### 5.1.4 人类可读输出格式

```
=== Codex Mirror Drift Report ===
Snapshot: 20260728T111422Z-6c672cc9e3
Checked at: 2026-07-30T10:00:00Z
Source freshness: DRIFTED (3 differences)

🔴 CRITICAL (1):
  [codex-skills] DELETED: skills/my-skill/SKILL.md
    expected sha256: abc123...
    source: C:\Users\45543\.codex\skills\my-skill\SKILL.md

🟡 MEDIUM (1):
  [codex-hooks] MODIFIED: hooks.template.json
    disposition: reacquire (expected to differ, no action needed)

🟢 LOW (1):
  [codex-native-memory-files] ADDED: memories/new-entry.md

Summary: 1 critical, 1 medium, 1 low
Recommended action: run 'mirror refresh --confirm REFRESH-CODEX-MIRROR'
```

#### 5.1.5 实现细节

- 复用 `validate_snapshot(snapshot, live_sources=True)` 内部逻辑。
- 新增 `classify_drift_severity(diff_entry, source_config)` 函数。
- `json` 格式与现有 `validate` 结构兼容，追加 `drift_report` 字段。

---

### F2: 快照差异分析增强（`diff`）

#### 5.2.1 概述

增强现有 `compare-snapshots`，新增 `diff` 子命令提供分类汇总和 CHANGELOG 生成。

#### 5.2.2 CLI 接口

```
python scripts/mirror_cli.py diff <left-snapshot> <right-snapshot> [--format human|json|changelog] [--by-source] [--summary-only]
```

#### 5.2.3 变更分类

| 分类 | 识别规则 |
|------|---------|
| `skill_added` | 路径匹配 `exports/codex-home/skills/*/SKILL.md` 且 right 独有 |
| `skill_removed` | 同上但 left 独有 |
| `skill_modified` | 同路径在两侧均存在但 sha256 不同 |
| `config_changed` | 路径匹配 `*.template.json` 或 `hooks.template.json` |
| `memory_updated` | 路径匹配 `exports/codex-home/native-memory/**` |
| `derived_refreshed` | 路径匹配 `exports/derived/**` |
| `manifest_changed` | 路径匹配 `manifests/**` |
| `script_changed` | 路径匹配 `scripts/**` |
| `other` | 不匹配以上规则 |

#### 5.2.4 CHANGELOG 格式输出

```markdown
## Snapshot diff: 20260728T080308Z → 20260728T111422Z

### Skills
- **Added (2):** `baoyu-new-skill`, `minecraft-xyz`
- **Removed (1):** `old-deprecated-skill`
- **Modified (0):** —

### Configuration
- **Modified (1):** `hooks.template.json`

### Memory
- **Updated (5 files):** `MEMORY.md`, `memory_summary.md`, +3

### Summary
- Total assets: 2187 → 2189 (+2)
- Total bytes: 27,612,345 → 27,717,465 (+105,120)
```

---

### F3: 交互式恢复向导（`restore-wizard`）

#### 5.3.1 概述

新增 `restore-wizard` 子命令，在 `stage` 完成后引导用户从隔离暂存到激活的全流程。

#### 5.3.2 CLI 接口

```
python scripts/mirror_cli.py restore-wizard --target-root <path> [--snapshot latest] [--dry-run] [--non-interactive]
```

#### 5.3.3 向导流程

```
Step 0: Preflight — 验证 target-root、快照完整性、控制平面版本兼容
Step 1: Backup — 扫描现有配置，创建带时间戳的备份目录，生成 backup-manifest.json
Step 2: Stage — 按 restore-order.json 的 DAG 顺序执行，每步验证 stage-hashes
Step 3: Pre-activation Validation — 只读验证技能语法、配置 JSON 有效性、DAG 无环
Step 4: Activation Guidance — 生成 activation-checklist.md，标注自动/手动步骤和所需密钥
Step 5: Rollback Readiness — 记录备份路径，生成 rollback-instructions.md 和 rollback.sh
```

#### 5.3.4 安全约束

- 向导**永不自动执行激活步骤**（Step 4 只生成清单）。
- `--non-interactive` 模式只跳过确认提示，仍止步于 Step 3。
- 备份目录创建失败时中止整个流程。
- 所有文件操作记录到 `wizard-audit-log.json`。

---

### F4: 外部加密存档管理（`archive`）

#### 5.4.1 概述

新增 `archive` 子命令族，统一管理外部加密存档，将 `full_state_restore_ready` 推进为 `true`。

#### 5.4.2 CLI 接口

```
python scripts/mirror_cli.py archive <subcommand> [options]
  archive status                          — 显示所有存档状态
  archive create --asset-id <id>          — 创建加密存档
  archive verify [--asset-id <id>]        — 验证存档完整性
  archive restore --asset-id <id> --target-root <path>  — 从存档恢复
  archive list                            — 列出所有存档
```

#### 5.4.3 后端抽象

| 后端 | 说明 | 适用场景 |
|------|------|---------|
| `restic` | 加密备份仓库，增量、去重 | 大型数据库、会话历史 |
| `directory` | 加密 tar.gz + SHA-256 | 小型文件、单次导出 |

#### 5.4.4 full_state_restore_ready 推进逻辑

```
full_state_restore_ready = true
  当且仅当：
    1. mirror_valid == true
    2. capability_restore_ready == true
    3. 所有 required_for != "optional_*" 的外部存档 status 为 archive_verified
    4. 所有 secret-requirements 中的必需密钥有对应的注入路径声明
```

#### 5.4.5 安全约束

- 存档 payload 永不进入 Git 仓库。
- `RESTIC_REPOSITORY_PASSWORD` 通过 `secret-requirements.json` 声明。
- 加密密钥缺失时报错而非降级为明文。

---

### F5: 自动调度刷新（`schedule`）

#### 5.5.1 概述

新增 `schedule` 子命令族，支持定时刷新和文件监听触发。

#### 5.5.2 CLI 接口

```
python scripts/mirror_cli.py schedule <subcommand> [options]
  schedule register --cron "<expr>" [--label <name>]  — 注册定时任务
  schedule list                                        — 列出已注册任务
  schedule remove --label <name>                       — 移除任务
  schedule watch --source-id <id> [--interval 300]     — 监听源变更
```

#### 5.5.3 安全约束

- 自动刷新使用与手动刷新相同的 `--confirm` 确认令牌。
- 自动刷新失败不回滚到更早的快照。
- 监听模式在 `CODEX_MIRROR_SOURCE_READ_ONLY=1` 环境下运行。
- 调度注册需要 `--confirm SCHEDULE-REGISTER` 确认令牌。

---

### F6: 配置模板化（`template` 模式）

#### 5.6.1 概述

为 `source-authorities.json` 中 `mode: "redact_json"` 的源新增 `mode: "template_json"` 选项。

#### 5.6.2 模板格式

```json
{
  "notifications": {
    "slack": {
      "webhook_url": "{{SLACK_WEBHOOK_URL}}",
      "enabled": true
    }
  }
}
```

#### 5.6.3 向后兼容

- `redact_json` 模式保持不变，不强制迁移。
- `template_json` 是 `redact_json` 的超集。
- 迁移是逐源可选的。

---

### F7: 端到端恢复验证（`test-restore`）

#### 5.7.1 概述

新增 `test-restore` 子命令，在隔离沙箱中执行完整恢复流程并验证环境可用性。

#### 5.7.2 CLI 接口

```
python scripts/mirror_cli.py test-restore [--snapshot latest] [--sandbox docker|wsl|directory] [--keep-on-failure] [--format human|ci]
```

#### 5.7.3 测试流程

```
1. 创建沙箱环境（临时目录/WSL clone/Docker容器）
2. 在沙箱中执行 stage（复用现有 stage 逻辑）
3. 验证 stage-hashes
4. 模拟激活：解析技能 SKILL.md、JSON/TOML 配置、restore-order DAG
5. 技能冒烟测试：必填字段、命名冲突、依赖文件存在
6. 生成 test-restore-report.json
7. 销毁沙箱（除非 --keep-on-failure 且失败）
```

#### 5.7.4 CI 集成

`--format ci` 输出 JUnit XML，可被 GitHub Actions 直接消费。

---

### F8: 技能健康度分析（`skill-health`）

#### 5.8.1 概述

新增 `skill-health` 子命令，扫描快照中所有技能的有效性、依赖完整性和命名冲突。

#### 5.8.2 检查类别

| 类别 | 检查项 | 严重程度 |
|------|--------|---------|
| `structure` | SKILL.md 存在且非空 | `critical` |
| `structure` | YAML frontmatter 包含 `name` 字段 | `critical` |
| `structure` | 无硬编码绝对路径 | `medium` |
| `dependencies` | 声明的依赖文件存在 | `critical` |
| `conflicts` | 技能名称在三个技能目录间无重复 | `high` |
| `conflicts` | 触发词/命令无冲突 | `high` |
| `licenses` | LICENSE.txt 文件存在且非空 | `low` |

---

### F9: MCP 工具渐进式发现（`mcp-discovery`）

> **来源**：MCP 官方 Client Best Practices（2026-07-28 版本）推荐渐进式工具发现模式。

#### 5.9.1 概述

当前 21 条 MCP 路由的全量定义在会话开始时注入上下文窗口，浪费 token。MCP 官方最佳实践推荐：当工具定义占用超过上下文窗口 1%-5% 时，应切换到渐进式发现模式。

#### 5.9.2 设计

新增 `mcp-discovery` 子命令，生成 MCP 路由的轻量级索引：

```
python scripts/mirror_cli.py mcp-discovery [--snapshot latest] [--format index|full]
```

- `--format index`：生成轻量级 `search_tools` 元工具描述，只包含 capability 名称和一行描述。
- `--format full`：输出完整路由定义（与现有 `mcp-capability-routes.json` 相同）。

#### 5.9.3 渐进式发现策略

```
会话开始
  │
  ├─ 注入 search_tools 元工具（~21 行，约 500 token）
  │   而非 21 条完整路由定义（约 5000+ token）
  │
  ├─ 模型调用 search_tools("code structure")
  │   → 返回匹配路由：code_structure (codegraph, hub_first)
  │
  └─ 按需加载完整路由定义到上下文
```

#### 5.9.4 搜索策略支持

| 策略 | 说明 | 实现复杂度 |
|------|------|-----------|
| `keyword` | 关键词匹配（BM25/正则） | 低 |
| `embedding` | 向量相似度检索 | 高（需 embedding 模型） |
| `hybrid` | 关键词 + 向量混合 | 中 |

Phase 4 先实现 `keyword` 策略（纯 Python 标准库可实现），`embedding` 留待后续。

#### 5.9.5 缓存策略

- 路由定义按 capability 名称缓存，命中时跳过 `tools/list` 调用。
- 缓存键为 `capability + route_definition_revision`。
- 缓存失效条件：`mcp-capability-routes.json` 的 `route_definition_sha256` 变化。

#### 5.9.6 验证矩阵新增

```json
{
  "id": "mcp-progressive-discovery",
  "tier": "mirror",
  "required": false,
  "owner": "mcp",
  "predicate": "MCP route index is generated and every route in the full definition is discoverable via the lightweight index"
}
```

---

### F10: MCP 服务器动态管理（`mcp-lifecycle`）

> **来源**：MCP 官方 Client Best Practices — Dynamic Server Management 模式。

#### 5.10.1 概述

当前 MCP 服务器（codegraph、gitnexus、graphify、filesystem 等）可能全量常驻，消耗资源。MCP 官方推荐：仅当模型确定需要某服务器的能力时才连接，任务完成后断开。

#### 5.10.2 设计

新增 `mcp-lifecycle` 子命令族：

```
python scripts/mirror_cli.py mcp-lifecycle <subcommand> [options]
  mcp-lifecycle status              — 显示所有 MCP 服务器连接状态
  mcp-lifecycle connect --capability <id>   — 按需连接指定能力的服务器
  mcp-lifecycle disconnect --capability <id> — 断开不再需要的服务器
  mcp-lifecycle auto-policy          — 生成自动连接/断开策略建议
```

#### 5.10.3 自动策略生成规则

| 能力类型 | 连接时机 | 断开时机 |
|---------|---------|---------|
| `local_filesystem_read` | 会话开始（always-on） | 会话结束 |
| `code_structure` | 检测到代码分析意图 | 代码分析任务完成 |
| `gitnexus_semantic_graph` | 检测到语义搜索意图 | 语义搜索任务完成 |
| `context_compression` | 上下文接近窗口阈值 | 压缩完成后 |
| `memory_router` | 记忆查询请求时 | 查询完成后（保持 warm 5 分钟） |
| `browser_session` | 浏览器操作请求时 | 浏览器会话关闭后 |
| `desktop_weixin` | 微信操作请求时 | 微信操作完成后 |

#### 5.10.4 超时与取消

遵循 MCP 规范的生命周期管理：
- 所有请求设置超时（默认 30 秒）。
- 超时后发送 `$/cancelRequest` 通知。
- 服务器未在合理时间内响应 `SIGTERM` 后发送 `SIGKILL`。

#### 5.10.5 验证矩阵新增

```json
{
  "id": "mcp-lifecycle-management",
  "tier": "mirror",
  "required": false,
  "owner": "mcp",
  "predicate": "MCP server lifecycle policy exists for every route, with explicit connect trigger, disconnect trigger, and timeout"
}
```

---

### F11: 时序知识图谱记忆（`temporal-memory`）

> **来源**：Zep/Graphiti 时序知识图谱架构、LiCoMemory CogniGraph 轻量分层图、ROMEM 连续相位旋转。

#### 5.11.1 概述

当前记忆系统（MEMORY.md + PMB）是扁平的，无法区分持久事实（如"用户偏好先证实再解释"）与易变事实（如"当前 Java 版本 25"），也无法解决时间冲突（如"旧配置 vs 新配置"）。

基于 Zep 的双时序模型和 LiCoMemory 的 CogniGraph，为记忆系统增加时序感知层。

#### 5.11.2 双时序模型

```
每条记忆事实包含两个时间线：
  T  (event timeline)     — 事实发生的时间
  T' (ingestion timeline) — 记忆被录入的时间

示例：
  {fact: "Java 版本", value: "25", T: "2026-06-16", T': "2026-06-16", valid_until: null}
  {fact: "Java 版本", value: "25.0.1", T: "2026-07-20", T': "2026-07-20", valid_until: null}
  → 旧事实自动标记 valid_until: "2026-07-20"
```

#### 5.11.3 CogniGraph 分层结构

```
Layer 1: Episode（原始事件）
  - 每次 MEMORY.md 条目、PMB 记忆写入都是一个 episode
  - 保留原始文本和时间戳

Layer 2: Semantic Entity（语义实体）
  - 从 episodes 中提取实体和关系
  - 实体：工具、配置项、用户偏好、决策
  - 关系：配置值变更、偏好确立、技能使用经验

Layer 3: Community（主题社区）
  - 相关实体聚类成主题
  - 如"开发工具链"、"Minecraft 生态"、"记忆系统"
```

#### 5.11.4 关系波动性分类

> **来源**：ROMEM 的 Semantic Speed Gate 概念。

每条关系自动分类为持久或易变：

| 关系类型 | 波动性 | 示例 | 轮换速度 |
|---------|--------|------|---------|
| `persistent` | α ≈ 0 | "用户偏好先证实再解释" | 不轮换 |
| `slow` | α ≈ 0.3 | "Minecraft 服务端架构" | 缓慢 |
| `fast` | α ≈ 0.7 | "当前 Node 版本" | 快速 |
| `volatile` | α ≈ 1 | "当前会话状态" | 立即 |

#### 5.11.5 冲突解决

当新事实与旧事实冲突时：
1. 检查关系波动性。
2. 持久关系（α ≈ 0）：保留两者，标记为"补充"而非"替换"。
3. 易变关系（α > 0.5）：旧事实标记 `valid_until`，新事实成为当前值。
4. 生成冲突解决日志到 `memory-conflict-log.json`。

#### 5.11.6 CLI 接口

```
python scripts/mirror_cli.py temporal-memory <subcommand> [options]
  temporal-memory index [--snapshot latest]      — 从 MEMORY.md 和 PMB 快照构建时序图
  temporal-memory query --fact "<keyword>"       — 查询特定事实的时间线
  temporal-memory conflicts                       — 列出所有时间冲突
  temporal-memory export --format graphml|json   — 导出知识图谱
```

#### 5.11.7 与现有记忆系统的关系

- **不替换** PMB 记忆服务或 MEMORY.md。
- 作为 PMB 之上的**索引和分析层**。
- PMB daemon 恢复后，时序图层为其提供查询优化。
- 当前 PMB daemon 未运行时，时序图层仍可从 MEMORY.md 离线构建。

#### 5.11.8 验证矩阵新增

```json
{
  "id": "temporal-memory-consistency",
  "tier": "mirror",
  "required": false,
  "owner": "memory",
  "predicate": "temporal memory graph has no unresolved conflicts for persistent relations, and every volatile relation has a valid_until boundary when superseded"
}
```

---

### F12: 沙箱权限审计（`sandbox-audit`）

> **来源**：Codex 官方最佳实践 — `workspace-write` + `on-request` 安全预设；Codex v0.136 生产硬化清单。

#### 5.12.1 概述

快照当前不记录 Codex 的沙箱配置（sandbox mode、approval policy、writable_roots）。恢复时无法验证权限一致性，可能导致恢复后的环境使用不安全的 `danger-full-access` 模式。

#### 5.12.2 设计

在快照的 `exports/derived/` 中新增 `codex-sandbox-config.json`：

```json
{
  "schema": "codex_sandbox_config.v1",
  "snapshot_id": "20260728T111422Z-6c672cc9e3",
  "generated_at": "2026-07-30T10:00:00Z",
  "config_source": "${CODEX_HOME}/config.toml",
  "sandbox": {
    "mode": "workspace-write",
    "approval_policy": "on-request",
    "writable_roots": [
      "C:\\Users\\45543\\Desktop\\Codex资源库",
      "\\\\wsl.localhost\\Codex-Wsl-Lab\\home\\codexlab\\work"
    ],
    "network_access": "on-request",
    "danger_full_access": false
  },
  "security_checks": {
    "no_full_access": true,
    "writable_roots_bounded": true,
    "approval_required_for_network": true,
    "git_hooks_injection_protected": true
  },
  "recommendations": []
}
```

#### 5.12.3 审计规则

| 规则 | 检查 | 严重程度 |
|------|------|---------|
| `no-full-access` | `danger_full_access` 必须为 `false` | `critical` |
| `writable-roots-bounded` | `writable_roots` 不包含根目录或整个盘符 | `high` |
| `approval-for-network` | 网络访问需要批准 | `medium` |
| `git-hooks-safe` | 未启用不受信任的 `.gitconfig` diff 外部工具 | `high` |

#### 5.12.4 CLI 接口

```
python scripts/mirror_cli.py sandbox-audit [--snapshot latest] [--fix-suggestions]
```

- `--fix-suggestions`：生成安全配置修复建议（不自动应用）。

#### 5.12.5 验证矩阵新增

```json
{
  "id": "sandbox-security-audit",
  "tier": "full_state",
  "required": true,
  "owner": "codex_config_guard",
  "predicate": "sandbox config uses workspace-write mode with on-request approval, writable_roots are bounded, and danger-full-access is false"
}
```

---

### F13: 会话归档与审计链（`session-audit`）

> **来源**：Codex v0.136 生产硬化清单 — Session Management and Audit Trails。

#### 5.13.1 概述

当前环境缺少会话级别的操作审计链。Codex v0.136 引入了会话归档功能，本特性将此能力集成到镜像快照中。

#### 5.13.2 设计

新增 `session-audit` 子命令族：

```
python scripts/mirror_cli.py session-audit <subcommand> [options]
  session-audit capture [--output <path>]   — 捕获当前会话审计摘要
  session-audit verify [--snapshot latest]  — 验证快照中的审计链完整性
  session-audit chain                        — 显示会话审计链
```

#### 5.13.3 审计摘要格式

```json
{
  "schema": "codex_session_audit.v1",
  "snapshot_id": "20260728T111422Z-6c672cc9e3",
  "sessions": [
    {
      "session_id": "abc123",
      "started_at": "2026-07-28T08:00:00Z",
      "ended_at": "2026-07-28T11:14:00Z",
      "sandbox_mode": "workspace-write",
      "approval_interactions": 3,
      "tools_invoked": ["shell", "apply_patch", "mcp_call"],
      "mcp_servers_connected": ["codegraph", "filesystem"],
      "files_modified": 12,
      "git_operations": ["commit"],
      "rule_observer_triggers": 47,
      "closeout_receipt": "receipt-xyz"
    }
  ],
  "chain_integrity": {
    "ok": true,
    "gaps": [],
    "hash_linked": true
  }
}
```

#### 5.13.4 审计链完整性

- 每个会话的审计摘要有 SHA-256 哈希。
- 相邻会话通过 `previous_hash` 链接（类区块链结构）。
- 链中断（缺失会话）时报告 `chain_gap`。

#### 5.13.5 隐私约束

- 审计摘要**不包含** prompt 内容、工具输出内容或文件内容。
- 只记录操作元数据（类型、时间、计数）。
- 敏感路径脱敏处理。

#### 5.13.6 验证矩阵新增

```json
{
  "id": "session-audit-chain",
  "tier": "mirror",
  "required": false,
  "owner": "codex_rule_observer",
  "predicate": "session audit chain has no gaps, every session has a closeout receipt, and audit metadata contains no prompt or output content"
}
```

---

### F14: AGENTS.md 分层验证（`agents-validate`）

> **来源**：Codex 官方最佳实践 — AGENTS.md layering；48 Codex tips — AGENTS.md 分层配置。

#### 5.14.1 概述

当前环境有三层 AGENTS.md：全局（`~/.codex/AGENTS.md`）、workspace（`worktree:AGENTS.md`）、bridge 子树（`_bridge/AGENTS.md`）。规则的优先级和一致性未自动验证，可能出现低层规则意外削弱高层硬边界的情况。

#### 5.14.2 设计

新增 `agents-validate` 子命令：

```
python scripts/mirror_cli.py agents-validate [--snapshot latest] [--format human|json]
```

#### 5.14.3 验证规则

| 规则 | 检查 | 严重程度 |
|------|------|---------|
| `precedence-consistency` | 低层 AGENTS.md 不削弱高层硬边界 | `critical` |
| `no-duplicate-owners` | 同一规则面不在多个层级重复声明所有者 | `medium` |
| `boundary-completeness` | 每个声明的硬边界都有对应的验证器 | `high` |
| `layer-discovery` | Codex 能按 cwd 正确发现所有适用层 | `high` |
| `language-consistency` | 审批文档规则（中文默认）在所有层一致 | `low` |

#### 5.14.4 分层发现验证

```
验证 cwd = C:\Users\45543\.codex\skills\my-skill
  → Layer 1: ~/.codex/AGENTS.md (machine scope, precedence 10)
  → Layer 2: (无 workspace AGENTS.md)
  → Layer 3: (无 bridge AGENTS.md)
  → 结果: 仅 Layer 1 生效

验证 cwd = /home/codexlab/work/codex-workspace/workspace/_bridge/
  → Layer 1: ~/.codex/AGENTS.md (machine scope, precedence 10)
  → Layer 2: worktree:AGENTS.md (workspace scope, precedence 20)
  → Layer 3: AGENTS.md (bridge scope, precedence 25)
  → 结果: 三层叠加，Layer 3 最具体
```

#### 5.14.5 与 rule-governance.json 的关系

- `rule-governance.json` 已记录 33 个规则面及其 `precedence` 和 `enforcement_strength`。
- `agents-validate` 交叉验证 `rule-governance.json` 中的声明与实际 AGENTS.md 文件内容一致。
- 发现不一致时报告 `rule_drift`。

#### 5.14.6 验证矩阵新增

```json
{
  "id": "agents-layer-validation",
  "tier": "mirror",
  "required": true,
  "owner": "codex_platform_and_global_agents",
  "predicate": "AGENTS.md layers have consistent precedence, no lower layer weakens a higher hard boundary, and every declared rule surface matches its AGENTS.md source"
}
```

---

## 6. 数据模型变更汇总

### 6.1 新增文件

| 文件 | 阶段 | 说明 |
|------|------|------|
| `runtime/schedule-registry.json` | P3 | 调度和监听注册表（不进入快照） |
| `snapshots/<id>/archive-receipts/*.json` | P2 | 外部存档 receipt |
| `snapshots/<id>/skill-health-report.json` | P3 | 技能健康报告 |
| `snapshots/<id>/test-restore-report.json` | P3 | 恢复测试报告 |
| `snapshots/<id>/temporal-memory-graph.json` | P4 | 时序知识图谱 |
| `snapshots/<id>/memory-conflict-log.json` | P4 | 记忆冲突解决日志 |
| `exports/derived/codex-sandbox-config.json` | P4 | 沙箱权限配置快照 |
| `exports/derived/session-audit-chain.json` | P4 | 会话审计链 |
| `exports/derived/mcp-route-index.json` | P4 | MCP 路由轻量级索引 |
| `exports/derived/mcp-lifecycle-policy.json` | P4 | MCP 服务器生命周期策略 |
| `exports/derived/agents-validation-report.json` | P4 | AGENTS.md 分层验证报告 |
| `manifests/schemas/hooks.schema.json` | P3 | hooks.json 模板验证 schema |

### 6.2 修改的现有文件

| 文件 | 变更内容 |
|------|---------|
| `scripts/mirror_cli.py` | 新增 14 个子命令 |
| `manifests/verification-matrix.json` | 新增 12 项检查 |
| `manifests/external-archives.json` | 新增 `backend.configuration` 字段 |
| `manifests/source-authorities.json` | 新增 `template_json` mode（可选） |
| `manifests/control-plane-contract.json` | 版本 `2.2.0` → `2.4.0` |
| `manifests/secret-requirements.json` | 追加模板化产生的新 secret_id |
| `AGENTS.md` | 追加新命令的入口说明 |
| `README.md` | 追加新命令的使用文档 |
| `CURRENT.md` | 追加新状态字段 |

### 6.3 控制平面版本升级

```
2.2.0 → 2.4.0
```

变更类型：`minor`（新增功能，向后兼容）。

---

## 7. 实施阶段

### Phase 1：可观测性（F1 + F2）

| 特性 | 风险 | 依赖 |
|------|------|------|
| F1: drift | 低（只读） | 无 |
| F2: diff | 低（只读） | 无 |

**完成标准**：`drift` 和 `diff` 命令可用且通过测试。控制平面升级到 `2.3.0`。

### Phase 2：闭环能力（F3 + F4）

| 特性 | 风险 | 依赖 |
|------|------|------|
| F3: restore-wizard | 中 | F1 |
| F4: archive | 高 | restic 安装（可选） |

**完成标准**：至少 1 个必需外部存档验证成功。`full_state_restore_ready` 推进。

### Phase 3：自动化与质量（F5 + F6 + F7 + F8）

| 特性 | 风险 | 依赖 |
|------|------|------|
| F5: schedule | 中 | Phase 1 |
| F6: template | 高 | Phase 1 |
| F7: test-restore | 中 | Phase 2 |
| F8: skill-health | 低 | 无 |

**完成标准**：定时刷新可用。至少 1 个 `redact_json` 源迁移到 `template_json`。`test-restore` 在最新快照通过。`skill-health` 报告零 critical。

### Phase 4：智能与安全（F9 + F10 + F11 + F12 + F13 + F14）

| 特性 | 风险 | 依赖 |
|------|------|------|
| F9: mcp-discovery | 中 | 无 |
| F10: mcp-lifecycle | 中 | F9 |
| F11: temporal-memory | 高 | 无 |
| F12: sandbox-audit | 中 | 无 |
| F13: session-audit | 中 | 无 |
| F14: agents-validate | 低 | 无 |

**完成标准**：MCP 路由索引生成且所有路由可发现。MCP 生命周期策略覆盖所有路由。时序记忆图从 MEMORY.md 成功构建。沙箱审计通过（无 `danger-full-access`）。会话审计链无缺口。AGENTS.md 三层验证通过。控制平面升级到 `2.4.0`。

---

## 8. 测试策略

### 8.1 单元测试

| 函数 | 测试要点 |
|------|---------|
| `classify_drift_severity()` | 4 种严重程度的正确分类 |
| `classify_asset_change()` | 9 种变更分类的正确识别 |
| `generate_changelog()` | Markdown 格式正确性 |
| `validate_template_placeholders()` | 占位符与 secret_id 匹配 |
| `parse_schedule_registry()` | 注册表解析和验证 |
| `check_skill_structure()` | 结构检查的各种边界情况 |
| `build_temporal_graph()` | 双时序模型正确构建 |
| `classify_relation_volatility()` | 关系波动性正确分类 |
| `validate_sandbox_config()` | 沙箱安全规则正确检查 |
| `validate_agents_layers()` | 三层规则优先级一致性验证 |
| `generate_mcp_route_index()` | 轻量级索引覆盖所有路由 |
| `generate_mcp_lifecycle_policy()` | 生命周期策略覆盖所有路由 |

### 8.2 集成测试

| 测试场景 | 验证点 |
|---------|--------|
| drift 全流程 | 构造漂移 → 运行 drift → 验证报告 |
| diff 全流程 | 对比两个已有快照 → 验证 CHANGELOG |
| restore-wizard dry-run | 全流程不写真实路径 |
| archive 端到端 | create → verify → restore → 验证哈希 |
| schedule 注册和移除 | register → list → remove |
| test-restore directory 沙箱 | 在临时目录中完整运行 |
| skill-health 全量扫描 | 对最新快照运行 |
| mcp-discovery 索引完整性 | 索引覆盖所有 21 条路由 |
| temporal-memory 从 MEMORY.md 构建 | 从已有记忆构建时序图 |
| sandbox-audit 安全检查 | 检测 `danger-full-access` 并报告 |
| session-audit 链完整性 | 构造链缺口并检测 |
| agents-validate 分层验证 | 构造规则冲突并检测 |

### 8.3 回归测试

- 所有现有测试保持通过。
- `validate` 和 `validate --live-sources` 行为不变。
- `compare-snapshots` 输出格式不变。
- 控制平面 `2.2.0` 快照在 `2.4.0` 代码上仍能通过 `validate`。

---

## 9. 风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| `template_json` 引入占位符解析 bug | 中 | 高 | 逐源可选迁移；`redact_json` 保持不变 |
| `archive` 后端不可用 | 中 | 中 | 回退到 `directory` 后端 |
| `schedule` 与手动刷新冲突 | 低 | 中 | 复用 `exclusive_operation_lock` |
| `test-restore` 沙箱残留 | 低 | 低 | 默认销毁；`--keep-on-failure` 仅失败时保留 |
| 控制平面升级不兼容 | 低 | 高 | `minor` 升级，只新增不修改 |
| `temporal-memory` 图构建性能 | 中 | 中 | 增量构建；只处理新增记忆条目 |
| `mcp-discovery` 搜索遗漏关键路由 | 中 | 中 | 先实现 keyword 策略；验证索引覆盖率 |
| `session-audit` 隐私泄露 | 低 | 高 | 只记录元数据；不记录 prompt/output 内容 |
| `sandbox-audit` 误报 | 低 | 低 | `required: true` 但只阻断 `danger-full-access` |

---

## 10. 行业最佳实践参考

本规范的以下特性直接来源于行业最新实践：

| 特性 | 参考来源 | 核心借鉴 |
|------|---------|---------|
| F9: MCP 渐进式发现 | [MCP Client Best Practices (2026-07-28)](https://modelcontextprotocol.io/docs/2026-07-28/develop/clients/client-best-practices) | 工具定义超上下文 1-5% 时切换到渐进式发现；`search_tools` 元工具模式 |
| F10: MCP 动态管理 | [MCP Client Best Practices — Dynamic Server Management](https://modelcontextprotocol.io/docs/2026-07-28/develop/clients/client-best-practices) | 按任务相关性连接/断开服务器；always-on 最小集 |
| F11: 时序知识图谱 | [Zep: Temporal Knowledge Graph (arXiv:2501.13956)](https://arxiv.org/pdf/2501.13956) | 双时序模型（T + T'）；分层子图（episode → entity → community） |
| F11: CogniGraph | [LiCoMemory (arXiv:2511.01448)](https://arxiv.org/html/2511.01448) | 轻量分层图；实体关系作为语义索引层；时序感知搜索 |
| F11: 关系波动性 | [ROMEM: Continuous Phase Rotation (arXiv:2604.11544)](https://arxiv.org/pdf/2604.11544) | Semantic Speed Gate；持久 vs 易变关系区分；几何遮蔽过期事实 |
| F12: 沙箱安全 | [Codex 官方最佳实践](https://developer.cloud.tencent.com/article/2704656) | `workspace-write` + `on-request` 安全预设；`writable_roots` 精确放行 |
| F12: 生产硬化 | [Codex v0.136 Production Hardening](https://codex.danielvaughan.com/2026/06/04/codex-cli-v0136-production-hardening-checklist-security-performance-reliability-enterprise/) | Git hook 注入防护；密钥保护；MCP 服务器硬化 |
| F13: 会话审计 | [Codex v0.136 Session Management](https://codex.danielvaughan.com/2026/06/04/codex-cli-v0136-production-hardening-checklist-security-performance-reliability-enterprise/) | 会话归档；审计链；操作追踪 |
| F14: AGENTS.md 分层 | [48 Codex Tips — AGENTS.md Layering](https://github.com/TsangKwunHei/codex-tips) | 全局/workspace/bridge 三层；优先级一致性；低层不削弱高层 |
| F9: MCP 生命周期 | [MCP Lifecycle Specification (2025-06-18)](https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle) | 初始化 → 操作 → 关闭；超时与取消；SIGTERM/SIGKILL |

---

## 11. 开放问题

| ID | 问题 | 状态 |
|----|------|------|
| Q1 | `test-restore` 的 `docker` 沙箱是否需要预构建镜像？ | 待定 |
| Q2 | `schedule watch` 的轮询间隔默认 300 秒是否合理？ | 待定 |
| Q3 | `template_json` 迁移完成后是否废弃 `redact_json`？ | 倾向永久保留 |
| Q4 | `skill-health` 是否应该支持自动修复？ | 倾向只报告 |
| Q5 | `archive` 是否需要支持云后端（S3、OneDrive）？ | Phase 2 只支持本地 |
| Q6 | `temporal-memory` 的 embedding 搜索策略何时实现？ | Phase 4 先 keyword，embedding 留后续 |
| Q7 | `mcp-lifecycle` 的自动断开是否会导致频繁重连？ | 需要warm 窗口（5 分钟） |
| Q8 | `session-audit` 是否需要实时采集，还是快照时采集？ | 倾向快照时采集（离线） |

---

## 12. 验收标准

- [ ] `drift` 命令在已知漂移场景下正确分类所有差异。
- [ ] `diff` 命令在两个已有快照间生成完整 CHANGELOG。
- [ ] `restore-wizard --dry-run` 生成 activation-checklist.md 且不写入真实路径。
- [ ] `archive create` + `archive verify` 对至少 1 个必需存档成功。
- [ ] `schedule register` 在 WSL 上创建可用的 crontab 条目。
- [ ] `template_json` 模式正确替换敏感值为占位符。
- [ ] `test-restore --sandbox directory` 在最新快照上全部通过。
- [ ] `skill-health` 报告格式正确且 critical 问题为零。
- [ ] `mcp-discovery --format index` 生成的索引覆盖所有 21 条路由。
- [ ] `mcp-lifecycle auto-policy` 为每条路由生成连接/断开策略。
- [ ] `temporal-memory index` 从 MEMORY.md 成功构建时序图，持久事实不被易变事实覆盖。
- [ ] `sandbox-audit` 检测到 `danger-full-access` 并报告 critical。
- [ ] `session-audit chain` 无缺口且不包含 prompt/output 内容。
- [ ] `agents-validate` 检测到低层规则削弱高层边界并报告 critical。
- [ ] 所有现有测试保持通过。
- [ ] 控制平面版本为 `2.4.0`。
- [ ] `validate` 和 `control-plane-validate` 在升级后通过。
