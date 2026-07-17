---
name: mcsmanager-fabric-mc
description: "MCSManager Fabric 26.1.2 Minecraft server operations for this workspace: mod/config classification, AutoModpack distribution, server maintenance, ghost config checks, log analysis, Concerto audio diagnosis, and client command-line launch. Use for this server's mod management, config sync, AutoModpack optimization, or troubleshooting."
---

# MCSManager Fabric MC 运维技能（索引制）

## 触发条件
- RCON 远程命令（踢人/封禁/白名单/服务器状态）
- MOD 管理、配置同步、AutoModpack、幽灵配置
- 服务端启动/停止/日志分析、Concerto 音频
- 客户端启动（单人/多人/主菜单）
- 脚本修改、配置还原

## 核心规则（不可违反）
- 修改文件前必须询问用户，创建带时间戳备份
- 幽灵检测只报告不删除，knownPatterns 全量扫描所有 MOD
- 服务端关闭后才能运行 organize-mods.ps1
- Windows/PowerShell 通用操作先用全局 `windows-codex-ops`；本技能只保留 MCSManager/Minecraft 特有差异
- 客户端启动：优先非 GUI 方案（Java 直接启动），GUI 仅在非 GUI 不可行时使用

## Self-Learning Protocol
每次工作中发现新知识时，自动执行以下步骤：
1. 任务结束后判断是否属于 "可复用经验"（见下方判定标准）
2. 是 → 更新 [references/lessons.md](references/lessons.md)，按时间倒序追加到对应分类
3. 同时检查是否需要更新其他 reference 文件（如 mods.md、known-issues.md）
4. 更新时保持精简：每条经验 ≤3 行，用 `[日期] 发现/教训/确认 + 一句话总结` 格式
5. 主实例发布必须先在 clone 实例验证；主实例只做受控升级，失败立即回撤到已备份版本
6. 配置同步类结论必须同时看日志证据和文件对照，只有日志或只有时间戳都不算最终确认

**判定标准**（满足任一即更新）：
- 操作失败后发现的新错误原因或修复方法
- 被用户纠正的假设或判断
- 脚本/命令在真实环境表现与测试环境不同的行为
- 新发现的 Windows/PowerShell 通用规则（同步到 `windows-codex-ops`）或 Java/MCSManager 行为规则
- 之前未知的 MOD 间冲突或兼容性问题

**非复用经验**（不更新）：
- 一次性路径、临时文件操作
- 已有记录且无新信息的重复错误
- 用户个人偏好（除非用户明确要求记录）

## 脚本索引

| 文件 | 位置 | 用途 |
|------|------|------|
| [launch-mc.ps1](../../launch-mc.ps1) | 项目根目录 | 通用 Fabric 客户端启动器（参数化实例/存档/服务器/用户名） |
| [launch-mc.bat](../../launch-mc.bat) | 项目根目录 | Batch 包装器（双击运行或命令行快捷调用） |
| [organize-mods.ps1](organize-mods.ps1) | skill 目录 | MOD 分类管理 + 幽灵配置检测 |

### launch-mc.ps1 关键设计
- **存档名自动修复**：去括号 → 重名检测（追加 `_1`, `_2`）→ 启动 → 退出后还原
- **UUID 自动解析**：从 `.minecraft/usercache.json` 读取用户名对应的真实 UUID
- **三种模式**：`-saveName`（单人存档）/ `-server`（多人服务器）/ 无参数（主菜单）
- **classpath 复用**：优先使用预构建的 `cp-<version>.txt`，回退到动态扫描 libraries
- **实例路径参数化**：`-instanceDir` 支持任意版本目录，不硬编码 3c3u

## Carpet 假人操作
- 移除假人：/player <name> kill（不是 kick）
- kick 假人会被 Carpet 自动重新生成，无效
- 假人生命周期由 Carpet 管理，不受服务器连接状态影响

## 文件索引

| 文件 | 用途 | 何时读取 |
|------|------|---------|
| [references/instance.md](references/instance.md) | 实例概况、目录结构、面板信息、服务器配置 | 任何涉及该服务端的操作 |
| [references/mods.md](references/mods.md) | 全部 MOD 清单、分类规则 | MOD 管理、分类、增减 |
| [references/automodpack.md](references/automodpack.md) | AutoModpack 配置详解、同步逻辑 | 配置同步、syncedFiles 修改 |
| [references/lessons.md](references/lessons.md) | 全部经验教训（按时间倒序追加） | 修改脚本/排查问题前必读 |
| [references/known-issues.md](references/known-issues.md) | 已知问题及解决方案 | 故障排查 |
| [references/concerto.md](references/concerto.md) | Concerto 音频系统 | 音乐播放问题 |
