# Codex Environment Mirror — Optimization Specification

| 字段 | 值 |
|------|-----|
| Spec ID | `codex-mirror-opt-v1` |
| 控制平面版本基线 | `2.2.0` |
| 创建日期 | `2026-07-30` |
| 状态 | `draft` |
| 影响范围 | `scripts/mirror_cli.py`, `manifests/`, `tests/`, `AGENTS.md`, `README.md` |

---

## 1. 执行摘要

本规范定义了对 Codex Environment Mirror 仓库的 8 项增强，分为三个实施阶段。所有增强保持以下不变量：

- 静态契约（`static_contract`）的语义变更需通过 `contract-review-plan` 治理。
- 快照资产保持 SHA-256 哈希验证，不可变。
- 激活始终所有者驱动，不被自动化绕过。
- 新增验证检查追加到 `verification-matrix.json`，不削弱现有检查。

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
| CLI 子命令数 | 8（plan, affected-source-plan, compare-snapshots, snapshot, validate, control-plane-validate, restore-plan, stage） |
| 验证矩阵检查数 | 26 |

### 2.2 核心痛点

1. **漂移检测结果不可读**：`validate --live-sources` 输出 JSON，人工排查困难。
2. **快照差异分析不足**：`compare-snapshots` 已存在但只输出机器可读的资产级差异，缺少分类汇总和变更日志生成。
3. **恢复流程断裂**：`stage` 写入隔离目录后，从 stage 到激活之间无引导。
4. **外部存档未闭环**：3 个必需存档状态为 `archive_receipt_missing`，阻止 `full_state_restore_ready`。
5. **刷新依赖手动触发**：除 workspace closeout hook 外，无定时或变更触发的自动刷新。
6. **脱敏配置恢复困难**：`redact_json` 模式产出模板，但恢复时需手动回填敏感值。
7. **缺少端到端恢复验证**：验证矩阵检查快照完整性，但不验证恢复后的环境可用性。
8. **技能健康度不可见**：技能文件被快照，但无法感知损坏、过期或冲突的技能。

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

### 3.2 非目标

- N1：不将本仓库变为实时配置权威（`authority_mode` 保持 `derived_snapshot`）。
- N2：不自动激活恢复状态（`activation_rule` 保持 `owner-driven`）。
- N3：不镜像密钥、Provider/Model 配置或插件载荷。
- N4：不替换 WSL Work Git facade 作为正常操作入口。
- N5：不引入非标准库依赖到 `scripts/mirror_cli.py`（保持纯 Python 标准库）。

---

## 4. 架构原则

所有增强遵循以下原则：

```
┌─────────────────────────────────────────────────────┐
│                 Control Plane v2.3.0                │
│  (control-plane-contract.json + verification-matrix)│
├─────────────┬───────────────┬───────────────────────┤
│  Phase 1    │   Phase 2     │      Phase 3          │
│  可观测性    │   闭环能力     │   自动化与质量         │
├─────────────┼───────────────┼───────────────────────┤
│ F1: drift   │ F3: restore   │ F5: schedule          │
│ F2: diff    │ F4: archive   │ F6: template          │
│             │               │ F7: test-restore      │
│             │               │ F8: skill-health      │
└─────────────┴───────────────┴───────────────────────┘
```

- **Phase 1（可观测性）**：只读增强，不改变快照格式，风险最低。
- **Phase 2（闭环能力）**：涉及外部存档和恢复流程，改变 `full_state_restore_ready` 语义。
- **Phase 3（自动化与质量）**：引入主动行为和深度验证，需要最严格的测试。

---

## 5. 功能规格

---

### F1: 漂移检测可视化（`drift`）

#### 5.1.1 概述

新增 `drift` 子命令，将 `validate --live-sources` 的结果转换为人类可读的差异报告。

#### 5.1.2 CLI 接口

```
python scripts/mirror_cli.py drift [--snapshot latest] [--format human|json] [--severity critical|medium|low|all]
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--snapshot` | `latest` | 对比的快照 ID |
| `--format` | `human` | 输出格式；`json` 输出与 `validate --live-sources` 兼容的结构 |
| `--severity` | `all` | 过滤显示的严重程度等级 |

#### 5.1.3 严重程度分类规则

| 严重程度 | 触发条件 | 示例 |
|----------|---------|------|
| `critical` | `required: true` 的源资产被删除或哈希不匹配 | `codex-skills` 中某 `SKILL.md` 被删除 |
| `medium` | `required: false` 的源资产变更，或生成源 (`generated_sources`) 的命令输出不匹配 | `codex-hooks` 模板内容变更 |
| `low` | 新增文件（源中有、快照中无），且不属于排除规则 | `native-memory/` 新增记忆文件 |
| `info` | 变更资产属于 `reacquire` / `regenerate` / `runtime` disposition | `config.toml` 变更（预期行为） |

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
    snapshot sha256: def456...
    live sha256:     789abc...
    disposition: reacquire (expected to differ, no action needed)

🟢 LOW (1):
  [codex-native-memory-files] ADDED: memories/new-entry.md
    new file, will be captured on next refresh

Summary: 1 critical, 1 medium, 1 low
Recommended action: run 'mirror refresh --confirm REFRESH-CODEX-MIRROR' to capture changes
```

#### 5.1.5 实现细节

- 复用 `validate_snapshot(snapshot, live_sources=True)` 的内部逻辑，不重复实现漂移采集。
- 新增 `classify_drift_severity(diff_entry, source_config)` 函数，基于 `source-authorities.json` 的 `required` 字段和 `asset-dispositions.json` 的 disposition 规则进行分类。
- `human` 格式输出到 stdout，`json` 格式输出与现有 `validate` 结构兼容，追加 `drift_report` 字段。

#### 5.1.6 验证矩阵新增

```json
{
  "id": "drift-classification",
  "tier": "mirror",
  "required": false,
  "owner": "mirror_cli",
  "predicate": "drift report classifies every live-source difference by severity with an explicit disposition-aware action recommendation"
}
```

---

### F2: 快照差异分析增强（`diff`）

#### 5.2.1 概述

增强现有 `compare-snapshots` 命令，新增 `diff` 子命令提供分类汇总和 CHANGELOG 生成能力。

#### 5.2.2 CLI 接口

```
python scripts/mirror_cli.py diff <left-snapshot> <right-snapshot> [--format human|json|changelog] [--by-source] [--summary-only]
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| 位置参数 `left` | 必填 | 较早的快照 ID |
| 位置参数 `right` | 必填 | 较晚的快照 ID |
| `--format` | `human` | `human`：终端友好；`json`：机器可读；`changelog`：Markdown CHANGELOG 片段 |
| `--by-source` | `false` | 按源 ID 分组输出 |
| `--summary-only` | `false` | 只输出汇总统计，不列出逐文件差异 |

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
- **Updated (5 files):** `MEMORY.md`, `memory_summary.md`, `raw_memories.md`, +2 rollout summaries

### Derived Data
- **Refreshed (12 files):** system-membership, rule-governance, mcp-capability-routes, ...

### Manifests
- **Unchanged**

### Summary
- Total assets: 2187 → 2189 (+2)
- Total bytes: 27,612,345 → 27,717,465 (+105,120)
```

#### 5.2.5 实现细节

- 复用现有 `compare_snapshots(left, right)` 函数获取原始差异。
- 新增 `classify_asset_change(asset_path, change_type)` 函数进行分类。
- `changelog` 格式追加到 `CHANGELOG.md`（若存在）或输出到 stdout（若不存在）。

#### 5.2.6 与现有 `compare-snapshots` 的关系

- `compare-snapshots` 保持不变（向后兼容），仍输出原始资产级 JSON 差异。
- `diff` 是 `compare-snapshots` 的人类可读 + 分类汇总层，内部调用相同的核心比较逻辑。

---

### F3: 交互式恢复向导（`restore-wizard`）

#### 5.3.1 概述

新增 `restore-wizard` 子命令，在 `stage` 完成后引导用户从隔离暂存到激活的全流程。

#### 5.3.2 CLI 接口

```
python scripts/mirror_cli.py restore-wizard --target-root <path> [--snapshot latest] [--dry-run] [--non-interactive]
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--target-root` | 必填 | stage 的隔离目标目录 |
| `--snapshot` | `latest` | 恢复的快照 ID |
| `--dry-run` | `false` | 只打印将要执行的步骤，不实际执行 |
| `--non-interactive` | `false` | 跳过确认提示，按 `restore-order.json` 自动执行到 stage 完成为止 |

#### 5.3.3 向导流程

```
┌──────────────────────────────────────────────────┐
│ Step 0: Preflight                                │
│  - 验证 target-root 存在且为空（或已 stage）      │
│  - 验证快照完整性（调用 validate）                │
│  - 验证 control-plane 版本兼容                    │
├──────────────────────────────────────────────────┤
│ Step 1: Backup Current State                     │
│  - 扫描目标环境的现有配置                         │
│  - 创建带时间戳的备份目录                         │
│  - 生成 backup-manifest.json（记录哈希）          │
│  - 用户确认后继续                                 │
├──────────────────────────────────────────────────┤
│ Step 2: Stage (复用现有 stage 命令)               │
│  - 按 restore-order.json 的 DAG 顺序执行          │
│  - 每步完成后验证 stage-hashes                    │
├──────────────────────────────────────────────────┤
│ Step 3: Pre-activation Validation                │
│  - 在 stage 目录中运行只读验证                    │
│  - 检查技能文件语法、配置文件 JSON 有效性          │
│  - 检查 restore-dag 无环                          │
│  - 输出 validation-report.json                   │
├──────────────────────────────────────────────────┤
│ Step 4: Activation Guidance (不自动执行)          │
│  - 按 restore-order.json 生成激活指令清单         │
│  - 每条指令标注 owner 和所需密钥                  │
│  - 标注哪些步骤可以自动执行，哪些需要所有者手动   │
│  - 生成 activation-checklist.md 到 target-root   │
├──────────────────────────────────────────────────┤
│ Step 5: Rollback Readiness                       │
│  - 记录 backup-manifest.json 路径                 │
│  - 生成 rollback-instructions.md                 │
│  - 记录回滚命令到 target-root/rollback.sh        │
└──────────────────────────────────────────────────┘
```

#### 5.3.4 activation-checklist.md 格式

```markdown
# Activation Checklist
Snapshot: 20260728T111422Z-6c672cc9e3
Target: /mnt/c/CodexRestoreStage
Generated: 2026-07-30T10:00:00Z

## Automatic Steps (can be scripted)
- [ ] Copy skills to ${CODEX_HOME}\skills (owner: skill_lifecycle_governance)
- [ ] Copy scripts to ${CODEX_HOME}\scripts (owner: codex_cli)
- [ ] Copy tools to ${CODEX_HOME}\tools (owner: codex_cli)

## Owner-Driven Steps (require manual action)
- [ ] Merge hooks.template.json into ${CODEX_HOME}\hooks.json (owner: codex_rule_observer)
      ⚠️  Sensitive values must be filled from Secret Vault
- [ ] Import native memory text (owner: memory_governance)
      ⚠️  Requires compatible schema; verify version.json
- [ ] Reacquire provider credentials (owner: codex_model_provider)
      🔑  Secret required: CODEX_PROVIDER_CREDENTIALS
- [ ] Reacquire plugins from inventory (owner: codex_cli)
- [ ] Recreate Windows scheduled tasks (owner: windows_integration)

## Post-Activation Validation
- [ ] Run verification matrix (activation tier)
- [ ] Confirm memory recall
- [ ] Confirm MCP routes
- [ ] Emit restore receipt
```

#### 5.3.5 安全约束

- 向导**永不自动执行激活步骤**（Step 4 只生成清单）。
- `--non-interactive` 模式只跳过确认提示，仍止步于 Step 3。
- 备份目录创建失败时中止整个流程。
- 所有文件操作记录到 `wizard-audit-log.json`。

#### 5.3.6 验证矩阵新增

```json
{
  "id": "restore-wizard-preflight",
  "tier": "stage",
  "required": false,
  "owner": "mirror_cli",
  "predicate": "restore wizard validates snapshot integrity, target emptiness, control-plane compatibility, and creates a hash-verified backup before staging"
}
```

---

### F4: 外部加密存档管理（`archive`）

#### 5.4.1 概述

新增 `archive` 子命令族，统一管理外部加密存档，将 `full_state_restore_ready` 从 `false` 推进为 `true`。

#### 5.4.2 CLI 接口

```
python scripts/mirror_cli.py archive <subcommand> [options]
```

| 子命令 | 说明 |
|--------|------|
| `archive status` | 显示所有外部存档的状态、哈希、最近验证时间 |
| `archive create --asset-id <id> [--backend restic\|directory]` | 为指定资产创建加密存档 |
| `archive verify [--asset-id <id>]` | 验证存档完整性（哈希 + 解密测试） |
| `archive restore --asset-id <id> --target-root <path>` | 从存档恢复到隔离目标 |
| `archive list` | 列出所有存档及其 receipt 状态 |

#### 5.4.3 存档 Receipt 格式

在 `snapshots/<id>/` 下新增 `archive-receipts/` 目录，每个存档一个 receipt：

```json
{
  "schema": "codex_mirror.archive_receipt.v1",
  "asset_id": "codex-native-memory-state",
  "snapshot_id": "20260728T111422Z-6c672cc9e3",
  "created_at": "2026-07-30T10:00:00Z",
  "backend": "restic",
  "repository": "encrypted:restic:local:/mnt/c/CodexArchives/native-memory",
  "archive_sha256": "abc123...",
  "content_sha256": "def456...",
  "bytes": 1048576,
  "encryption": "aes-256-cbc via RESTIC_REPOSITORY_PASSWORD",
  "verified_at": "2026-07-30T10:05:00Z",
  "verify_ok": true,
  "owner": "memory_governance",
  "restore_mode": "owner_import_only"
}
```

#### 5.4.4 external-archives.json 变更

新增 `backend.configuration` 字段，记录 restic 仓库配置状态：

```json
{
  "backend": {
    "preferred": "restic",
    "installed": true,
    "repository": "configured:local:/mnt/c/CodexArchives",
    "password_requirement": "RESTIC_REPOSITORY_PASSWORD",
    "status": "configured",
    "initialized_at": "2026-07-30T09:00:00Z"
  }
}
```

每个资产的 `status` 字段从 `archive_receipt_missing` 更新为 `archive_receipt_present` 或 `archive_verified`。

#### 5.4.5 后端抽象

支持两种后端：

| 后端 | 说明 | 适用场景 |
|------|------|---------|
| `restic` | 加密备份仓库，增量、去重 | 大型数据库、会话历史 |
| `directory` | 加密 tar.gz + SHA-256 | 小型文件、单次导出 |

后端选择逻辑：
1. 若 `restic` 已安装且仓库已初始化 → 使用 `restic`。
2. 否则回退到 `directory`（使用 `openssl enc` 或 Python `cryptography` 标准库）。
3. 两者都不可用时报告 `backend_unavailable` 并阻止 `full_state_restore_ready`。

#### 5.4.6 full_state_restore_ready 推进逻辑

```
full_state_restore_ready = true
  当且仅当：
    1. mirror_valid == true
    2. capability_restore_ready == true
    3. 所有 required_for != "optional_*" 的外部存档
       status 为 archive_verified
    4. 所有 secret-requirements 中的必需密钥
       有对应的注入路径声明
```

#### 5.4.7 验证矩阵变更

更新现有检查：

```json
{
  "id": "external-archives",
  "tier": "full_state",
  "required": true,
  "owner": "external_archive",
  "predicate": "required archive receipts exist, hashes verify, restore test is current, and backend is installed and initialized"
}
```

新增检查：

```json
{
  "id": "archive-restore-test",
  "tier": "full_state",
  "required": false,
  "owner": "external_archive",
  "predicate": "each required archive has a successful isolated restore test within the last 30 days"
}
```

#### 5.4.8 安全约束

- 存档 payload 永不进入 Git 仓库（`external-archives.json` 规则不变）。
- `RESTIC_REPOSITORY_PASSWORD` 通过 `secret-requirements.json` 声明，不从仓库中读取。
- `archive restore` 只写入隔离目标，不直接导入活跃环境。
- 加密密钥缺失时，`archive create` 报错而非降级为明文。

---

### F5: 自动调度刷新（`schedule`）

#### 5.5.1 概述

新增 `schedule` 子命令族，支持定时刷新和文件监听触发。

#### 5.5.2 CLI 接口

```
python scripts/mirror_cli.py schedule <subcommand> [options]
```

| 子命令 | 说明 |
|--------|------|
| `schedule register --cron "<expr>" [--label <name>]` | 注册定时刷新任务 |
| `schedule list` | 列出已注册的定时任务 |
| `schedule remove --label <name>` | 移除定时任务 |
| `schedule watch --source-id <id> [--interval 300]` | 监听指定源的文件变更并触发刷新 |

#### 5.5.3 定时刷新实现

- **WSL 环境**：生成 crontab 条目，调用 `codex_workflow_entry.py mirror refresh`。
- **Windows 环境**：生成计划任务 XML，调用 `refresh-mirror.ps1`。
- 调度元数据存储在 `runtime/schedule-registry.json`（`runtime/` 已在 `global_exclude_dirs` 中，不进入快照）。

#### 5.5.4 schedule-registry.json 格式

```json
{
  "schema": "codex_mirror.schedule_registry.v1",
  "schedules": [
    {
      "label": "daily-refresh",
      "cron": "0 3 * * *",
      "platform": "wsl",
      "command": "python3 _bridge/codex_workflow_entry.py mirror refresh --confirm REFRESH-CODEX-MIRROR",
      "created_at": "2026-07-30T10:00:00Z",
      "last_run": null,
      "last_status": null
    }
  ],
  "watches": [
    {
      "source_id": "codex-skills",
      "interval_seconds": 300,
      "platform": "wsl",
      "last_check": "2026-07-30T10:00:00Z",
      "last_hash": "abc123..."
    }
  ]
}
```

#### 5.5.5 文件监听模式

- `schedule watch` 以轮询方式（非 inotify，保持标准库兼容）定期计算源目录的哈希摘要。
- 哈希变化时触发 `refresh --confirm`。
- 监听进程写入 PID 文件到 `runtime/watch-<source-id>.pid`，防止重复启动。
- `schedule list` 显示活跃监听的状态。

#### 5.5.6 安全约束

- 自动刷新使用与手动刷新相同的 `--confirm` 确认令牌。
- 自动刷新失败不回滚到更早的快照（保持当前 latest.json）。
- 监听模式在 `CODEX_MIRROR_SOURCE_READ_ONLY=1` 环境下运行，防止反向覆盖。
- 调度注册需要 `--confirm SCHEDULE-REGISTER` 确认令牌。

#### 5.5.7 验证矩阵新增

```json
{
  "id": "schedule-registry",
  "tier": "mirror",
  "required": false,
  "owner": "mirror_cli",
  "predicate": "schedule registry parses, every registered schedule references a valid command, and no schedule targets a prohibited source root"
}
```

---

### F6: 配置模板化（`template` 模式）

#### 5.6.1 概述

为 `source-authorities.json` 中 `mode: "redact_json"` 的源新增 `mode: "template_json"` 选项，支持变量占位符和 vault 集成。

#### 5.6.2 source-authorities.json 变更

```json
{
  "id": "codex-hooks",
  "kind": "file",
  "source": "${CODEX_HOME}\\hooks.json",
  "destination": "exports/codex-home/hooks.template.json",
  "restore_path": "${CODEX_HOME}\\hooks.json",
  "mode": "template_json",
  "classification": "configuration_template",
  "owner": "codex_rule_observer",
  "activation": "owner_merge_only",
  "template_config": {
    "placeholders": [
      {
        "key": "{{SLACK_WEBHOOK_URL}}",
        "secret_id": "SLACK_WEBHOOK_URL",
        "owner": "codex_rule_observer",
        "description": "Slack incoming webhook for notifications"
      },
      {
        "key": "{{GITHUB_TOKEN}}",
        "secret_id": "GITHUB_AUTH",
        "owner": "github",
        "description": "GitHub token for API operations"
      }
    ],
    "validation": "json_schema",
    "schema_path": "manifests/schemas/hooks.schema.json"
  },
  "required": false
}
```

#### 5.6.3 模板格式

模板文件中使用 `{{SECRET_ID}}` 格式的占位符：

```json
{
  "notifications": {
    "slack": {
      "webhook_url": "{{SLACK_WEBHOOK_URL}}",
      "enabled": true
    }
  },
  "github": {
    "token": "{{GITHUB_TOKEN}}",
    "enabled": true
  }
}
```

#### 5.6.4 快照行为

- `template_json` 模式在快照时：读取源文件 → 用占位符替换敏感值 → 写入模板到 `destination`。
- 替换规则：对 `template_config.placeholders` 中声明的每个 key，在 JSON 值中查找匹配的敏感值并替换为占位符。
- 敏感值识别复用现有的 `SENSITIVE_KEY` 正则和 `HIGH_CONFIDENCE_SECRET_PATTERNS`。

#### 5.6.5 恢复行为

- 恢复时读取模板 → 从 Secret Vault 或交互式输入获取每个占位符的值 → 替换后写入 `restore_path`。
- 新增 `mirror fill-template --source-id <id> --vault <path>` 子命令支持非交互式填充。
- 缺失密钥时报告 `secret_missing` 但不阻止其他非密钥依赖项的恢复。

#### 5.6.6 向后兼容

- `redact_json` 模式保持不变，不强制迁移。
- `template_json` 是 `redact_json` 的超集：`redact_json` 产出无元数据的脱敏文件，`template_json` 产出带占位符的模板 + 占位符声明。
- 迁移是逐源可选的。

#### 5.6.7 验证矩阵新增

```json
{
  "id": "template-placeholders",
  "tier": "mirror",
  "required": false,
  "owner": "mirror_cli",
  "predicate": "every template_json source has matching placeholders in its exported file and every placeholder has a declared secret_id in secret-requirements.json"
}
```

---

### F7: 端到端恢复验证（`test-restore`）

#### 5.7.1 概述

新增 `test-restore` 子命令，在隔离沙箱中执行完整恢复流程并验证环境可用性。

#### 5.7.2 CLI 接口

```
python scripts/mirror_cli.py test-restore [--snapshot latest] [--sandbox docker|wsl|directory] [--keep-on-failure]
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--snapshot` | `latest` | 测试的快照 ID |
| `--sandbox` | `directory` | 沙箱类型 |
| `--keep-on-failure` | `false` | 失败时保留沙箱环境用于调试 |

#### 5.7.3 沙箱类型

| 类型 | 说明 | 隔离强度 |
|------|------|---------|
| `directory` | 临时目录，最轻量 | 低（共享主机环境） |
| `wsl` | 临时 WSL 发行版克隆 | 中（独立文件系统） |
| `docker` | 一次性容器 | 高（完全隔离） |

#### 5.7.4 测试流程

```
┌────────────────────────────────────────────────────┐
│ 1. 创建沙箱环境（临时目录/WSL clone/Docker容器）    │
│ 2. 在沙箱中执行 stage（复用现有 stage 逻辑）        │
│ 3. 验证 stage-hashes                               │
│ 4. 模拟激活（不写入真实路径）：                     │
│    a. 解析所有技能 SKILL.md，验证 YAML frontmatter  │
│    b. 解析所有 JSON 配置，验证语法                  │
│    c. 解析所有 TOML 配置，验证语法                  │
│    d. 验证 restore-order DAG 可解析且无环           │
│    e. 验证 verification-matrix 中 mirror tier 全过 │
│ 5. 技能冒烟测试：                                   │
│    a. 检查每个 SKILL.md 的必填字段（name, trigger） │
│    b. 检查技能间无命名冲突                          │
│    c. 检查声明的依赖文件存在                        │
│ 6. 生成 test-restore-report.json                   │
│ 7. 销毁沙箱（除非 --keep-on-failure 且测试失败）    │
└────────────────────────────────────────────────────┘
```

#### 5.7.5 test-restore-report.json 格式

```json
{
  "schema": "codex_mirror.test_restore_report.v1",
  "snapshot_id": "20260728T111422Z-6c672cc9e3",
  "sandbox_type": "directory",
  "sandbox_path": "/tmp/codex-test-restore-abc123",
  "started_at": "2026-07-30T10:00:00Z",
  "completed_at": "2026-07-30T10:02:30Z",
  "overall_ok": true,
  "stages": {
    "stage": {"ok": true, "duration_seconds": 15.2},
    "hash_verification": {"ok": true, "assets_checked": 2189},
    "config_validation": {"ok": true, "json_files": 5, "toml_files": 2, "yaml_files": 0},
    "dag_validation": {"ok": true, "steps": 15},
    "mirror_tier_checks": {"ok": true, "checks_passed": 18, "checks_failed": 0},
    "skill_smoke": {"ok": true, "skills_checked": 162, "conflicts": 0, "missing_deps": 0}
  },
  "failures": [],
  "warnings": [
    "3 skills lack optional 'description' field"
  ]
}
```

#### 5.7.6 CI 集成

提供 `--format ci` 选项，输出 JUnit XML 格式，可被 GitHub Actions 直接消费：

```yaml
# .github/workflows/test-restore.yml
- name: Run end-to-end restore test
  run: python scripts/mirror_cli.py test-restore --sandbox directory --format ci > test-results.xml
- uses: dorny/test-reporter@v1
  with:
    name: Restore Test Results
    path: test-results.xml
    reporter: java-junit
```

#### 5.7.7 验证矩阵新增

```json
{
  "id": "test-restore",
  "tier": "stage",
  "required": false,
  "owner": "mirror_cli",
  "predicate": "end-to-end restore test completes in isolated sandbox with all mirror-tier checks passing and no skill conflicts"
}
```

---

### F8: 技能健康度分析（`skill-health`）

#### 5.8.1 概述

新增 `skill-health` 子命令，扫描快照中所有技能的有效性、依赖完整性和命名冲突。

#### 5.8.2 CLI 接口

```
python scripts/mirror_cli.py skill-health [--snapshot latest] [--format human|json] [--check all|structure|dependencies|conflicts|licenses]
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--snapshot` | `latest` | 检查的快照 ID |
| `--format` | `human` | 输出格式 |
| `--check` | `all` | 检查类别，可逗号分隔指定多项 |

#### 5.8.3 检查类别

| 类别 | 检查项 | 严重程度 |
|------|--------|---------|
| `structure` | SKILL.md 存在且非空 | `critical` |
| `structure` | YAML frontmatter 包含 `name` 字段 | `critical` |
| `structure` | YAML frontmatter 包含 `trigger` 或 `description` 字段 | `medium` |
| `structure` | 无硬编码的绝对路径（Windows 或 Unix） | `medium` |
| `dependencies` | 声明的依赖文件（如 `requirements.txt`）存在 | `critical` |
| `dependencies` | 引用的其他技能存在 | `medium` |
| `conflicts` | 技能名称在 codex-skills / agent-skills / cc-switch-skills 间无重复 | `high` |
| `conflicts` | 触发词/命令无冲突 | `high` |
| `licenses` | 包含 `LICENSE.txt` 的技能其许可证文件存在且非空 | `low` |
| `licenses` | 无未声明的专有许可证标记 | `low` |

#### 5.8.4 人类可读输出

```
=== Skill Health Report ===
Snapshot: 20260728T111422Z-6c672cc9e3
Checked at: 2026-07-30T10:00:00Z

Summary: 162 skills checked, 158 healthy, 4 issues

🔴 CRITICAL (1):
  [codex-skills:broken-skill] SKILL.md is empty (0 bytes)

🟡 MEDIUM (2):
  [codex-skills:my-skill] Missing 'description' field in frontmatter
  [agent-skills:helper-skill] References non-existent skill 'missing-dep'

🟢 LOW (1):
  [codex-skills:licensed-skill] LICENSE.txt referenced but file missing

✅ CONFLICTS: None detected
✅ DEPENDENCIES: All declared dependencies present (except 1 noted above)
```

#### 5.8.5 健康报告存储

报告写入 `snapshots/<id>/skill-health-report.json`，作为快照的伴随产物（不进入 asset 清单，不参与哈希验证，但被 `control-plane-state.json` 引用）。

#### 5.8.6 验证矩阵新增

```json
{
  "id": "skill-health",
  "tier": "mirror",
  "required": false,
  "owner": "skill_lifecycle_governance",
  "predicate": "all active skills pass structure, dependency, and conflict checks; critical issues are zero"
}
```

---

## 6. 数据模型变更汇总

### 6.1 新增文件

| 文件 | 类型 | 说明 |
|------|------|------|
| `runtime/schedule-registry.json` | 运行时状态（不进入快照） | 调度和监听注册表 |
| `snapshots/<id>/archive-receipts/*.json` | 快照伴随产物 | 外部存档 receipt |
| `snapshots/<id>/skill-health-report.json` | 快照伴随产物 | 技能健康报告 |
| `snapshots/<id>/test-restore-report.json` | 快照伴随产物 | 恢复测试报告 |
| `manifests/schemas/hooks.schema.json` | 静态契约（新增） | hooks.json 模板验证 schema |

### 6.2 修改的现有文件

| 文件 | 变更内容 |
|------|---------|
| `scripts/mirror_cli.py` | 新增 6 个子命令（drift, diff, restore-wizard, archive, schedule, test-restore, skill-health） |
| `manifests/verification-matrix.json` | 新增 6 项检查 |
| `manifests/external-archives.json` | 新增 `backend.configuration` 字段，资产 `status` 语义扩展 |
| `manifests/source-authorities.json` | 新增 `template_json` mode 和 `template_config` 字段（可选） |
| `manifests/control-plane-contract.json` | 版本从 `2.2.0` 升级到 `2.3.0`，新增 schema 文件到 static_contract |
| `manifests/secret-requirements.json` | 追加模板化产生的新的 secret_id（如 `SLACK_WEBHOOK_URL`） |
| `AGENTS.md` | 追加新命令的入口说明 |
| `README.md` | 追加新命令的使用文档 |
| `CURRENT.md` | 追加 `skill_health` 和 `test_restore` 状态字段 |

### 6.3 控制平面版本升级

```
2.2.0 → 2.3.0
```

变更类型：`minor`（新增功能，向后兼容）。

理由：
- 新增子命令不改变现有命令行为。
- 新增验证检查为 `required: false`，不阻断现有流程。
- `external-archives.json` 的 `status` 字段扩展是增量式的（新值 `archive_verified` 不影响旧值的理解）。
- `source-authorities.json` 的 `template_json` mode 是可选的，`redact_json` 保持不变。

---

## 7. 实施阶段

### Phase 1：可观测性（F1 + F2）

| 特性 | 风险 | 依赖 |
|------|------|------|
| F1: drift | 低（只读） | 无 |
| F2: diff | 低（只读） | 无 |

**完成标准**：
- `drift` 和 `diff` 命令可用且通过测试。
- 新增验证矩阵检查通过。
- `AGENTS.md` 和 `README.md` 更新。
- 控制平面版本升级到 `2.3.0`。

**测试要求**：
- 构造已知漂移场景，验证分类正确性。
- 对比两个已有快照，验证 diff 输出完整性。
- `drift --format json` 输出与 `validate --live-sources` 结构兼容。

### Phase 2：闭环能力（F3 + F4）

| 特性 | 风险 | 依赖 |
|------|------|------|
| F3: restore-wizard | 中（涉及文件操作） | F1（复用 validate 逻辑） |
| F4: archive | 高（涉及加密和外部后端） | restic 安装（可选） |

**完成标准**：
- `restore-wizard` 完整流程可通过 dry-run 验证。
- 至少 1 个必需外部存档（`codex-native-memory-state`）创建并验证成功。
- `full_state_restore_ready` 在该存档验证后推进为 `true`（或明确报告剩余缺口）。
- `external-archives.json` 的 `backend.status` 更新。

**测试要求**：
- restore-wizard dry-run 不写入任何真实路径。
- archive create → verify → restore 链路在隔离目录中端到端通过。
- 密钥缺失时 archive create 正确报错而非降级。

### Phase 3：自动化与质量（F5 + F6 + F7 + F8）

| 特性 | 风险 | 依赖 |
|------|------|------|
| F5: schedule | 中（涉及系统调度） | Phase 1 完成 |
| F6: template | 高（改变快照行为） | Phase 1 完成 |
| F7: test-restore | 中（涉及沙箱管理） | Phase 2 完成 |
| F8: skill-health | 低（只读分析） | 无 |

**完成标准**：
- `schedule register` 在 WSL 和 Windows 上均可创建调度。
- 至少 1 个 `redact_json` 源迁移到 `template_json` 并验证。
- `test-restore --sandbox directory` 在最新快照上通过。
- `skill-health` 报告零 critical 问题。

**测试要求**：
- schedule 注册后 `schedule list` 正确显示。
- template_json 快照产出的模板文件包含正确的占位符。
- test-restore 在沙箱中运行所有 mirror-tier 检查。
- skill-health 检测到故意植入的损坏技能。

---

## 8. 测试策略

### 8.1 单元测试

每个新函数配备单元测试，追加到 `tests/test_mirror_cli.py`：

| 函数 | 测试要点 |
|------|---------|
| `classify_drift_severity()` | 4 种严重程度的正确分类 |
| `classify_asset_change()` | 9 种变更分类的正确识别 |
| `generate_changelog()` | Markdown 格式正确性 |
| `validate_template_placeholders()` | 占位符与 secret_id 匹配 |
| `parse_schedule_registry()` | 注册表解析和验证 |
| `check_skill_structure()` | 结构检查的各种边界情况 |

### 8.2 集成测试

| 测试场景 | 验证点 |
|---------|--------|
| drift 全流程 | 构造漂移 → 运行 drift → 验证报告 |
| diff 全流程 | 对比两个已有快照 → 验证分类和 CHANGELOG |
| restore-wizard dry-run | 从 plan 到 activation-checklist 全流程不写真实路径 |
| archive 端到端 | create → verify → restore → 验证哈希 |
| schedule 注册和移除 | register → list → remove 链路 |
| test-restore directory 沙箱 | 在临时目录中完整运行 |
| skill-health 全量扫描 | 对最新快照运行，验证报告格式 |

### 8.3 回归测试

- 所有现有测试（`tests/test_mirror_cli.py`）保持通过。
- `validate` 和 `validate --live-sources` 行为不变。
- `compare-snapshots` 输出格式不变。
- `snapshot`、`stage`、`restore-plan` 行为不变。
- 控制平面 `2.2.0` 快照在 `2.3.0` 代码上仍能通过 `validate`。

### 8.4 测试命令

```bash
# 运行全部测试
python -m pytest tests/ -v

# 只运行新增测试
python -m pytest tests/test_mirror_cli.py -v -k "drift or diff or wizard or archive or schedule or template or test_restore or skill_health"

# 验证控制平面兼容性
python scripts/mirror_cli.py validate
python scripts/mirror_cli.py control-plane-validate
```

---

## 9. 风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| `template_json` 模式引入占位符解析 bug，导致快照内容损坏 | 中 | 高 | 迁移是逐源可选的；`redact_json` 保持不变；新增 `template-placeholders` 验证检查 |
| `archive` 后端（restic）不可用 | 中 | 中 | 回退到 `directory` 后端；明确报告 `backend_unavailable` |
| `schedule` 自动刷新在并发场景下与手动刷新冲突 | 低 | 中 | 复用现有 `exclusive_operation_lock` 机制 |
| `test-restore` 沙箱残留导致磁盘泄漏 | 低 | 低 | 默认销毁沙箱；`--keep-on-failure` 仅在失败时保留 |
| 控制平面版本升级导致旧代码不兼容 | 低 | 高 | 版本升级为 `minor`，只新增不修改；回归测试验证旧快照兼容 |
| `skill-health` 误报导致刷新被阻断 | 中 | 低 | 该检查为 `required: false`，不阻断刷新；仅作为报告 |

---

## 10. 迁移计划

### 10.1 Phase 1 迁移（零风险）

1. 在 `scripts/mirror_cli.py` 中新增 `drift` 和 `diff` 子命令。
2. 在 `tests/test_mirror_cli.py` 中新增对应测试。
3. 更新 `manifests/verification-matrix.json` 追加 2 项检查。
4. 更新 `AGENTS.md` 和 `README.md`。
5. 升级 `manifests/control-plane-contract.json` 版本到 `2.3.0`。
6. 运行 `validate` 确认通过。
7. 提交并发布。

### 10.2 Phase 2 迁移（需所有者参与）

1. 实现 `restore-wizard` 和 `archive` 子命令。
2. 所有者安装 restic 并初始化仓库。
3. 所有者运行 `archive create --asset-id codex-native-memory-state`。
4. 所有者运行 `archive verify`。
5. 更新 `external-archives.json` 的 `backend` 和资产 `status`。
6. 运行 `validate --live-sources` 确认 `full_state_restore_ready` 推进。
7. 通过 `contract-review-plan` 治理流程更新静态契约。
8. 提交并发布。

### 10.3 Phase 3 迁移（渐进式）

1. 实现 `schedule`、`template`、`test-restore`、`skill-health` 子命令。
2. 逐个迁移 `redact_json` 源到 `template_json`（每次一个，验证后继续）。
3. 注册定时刷新调度。
4. 在 CI 中集成 `test-restore`。
5. 运行 `skill-health` 并修复发现的 critical 问题。
6. 更新所有文档。
7. 提交并发布。

---

## 11. 开放问题

| ID | 问题 | 状态 |
|----|------|------|
| Q1 | `test-restore` 的 `docker` 沙箱是否需要预构建镜像？还是使用 `python:3.12-slim` 通用镜像？ | 待定 |
| Q2 | `schedule watch` 的轮询间隔默认 300 秒是否合理？是否需要根据源大小动态调整？ | 待定 |
| Q3 | `template_json` 迁移完成后，是否废弃 `redact_json`？还是永久保留两种模式？ | 倾向永久保留 |
| Q4 | `skill-health` 是否应该支持自动修复（如删除空 SKILL.md 的技能目录）？还是只报告？ | 倾向只报告 |
| Q5 | `archive` 是否需要支持云后端（S3、OneDrive）？还是只支持本地 restic？ | Phase 2 只支持本地 |

---

## 12. 验收标准

本规范实施的验收标准为：

- [ ] `drift` 命令在已知漂移场景下正确分类所有差异。
- [ ] `diff` 命令在两个已有快照间生成完整 CHANGELOG。
- [ ] `restore-wizard --dry-run` 生成 activation-checklist.md 且不写入真实路径。
- [ ] `archive create` + `archive verify` 对至少 1 个必需存档成功。
- [ ] `schedule register` 在 WSL 上创建可用的 crontab 条目。
- [ ] `template_json` 模式正确替换敏感值为占位符。
- [ ] `test-restore --sandbox directory` 在最新快照上全部通过。
- [ ] `skill-health` 报告格式正确且 critical 问题为零。
- [ ] 所有现有测试保持通过。
- [ ] 控制平面版本为 `2.3.0`。
- [ ] `validate` 和 `control-plane-validate` 在升级后通过。
