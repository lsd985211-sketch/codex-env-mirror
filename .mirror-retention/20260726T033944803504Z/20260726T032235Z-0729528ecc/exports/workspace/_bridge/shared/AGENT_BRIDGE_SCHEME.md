# Agent Bridge v2 — Reasonix ↔ Codex 交互方案

> **待审阅版本** | 审阅请求见 REVIEW_REQUEST.md

## 架构

双方各自运行 Python MCP Server 实例，连接同一个 `_bridge/bridge.db` (SQLite WAL)，无中间进程。

## 10 个 MCP 工具

| 工具 | 用途 |
|------|------|
| agent_bridge_send | 发任务给另一个 Agent |
| agent_bridge_receive | 读待处理任务（自动 claim） |
| agent_bridge_claim | 显式认领 |
| agent_bridge_complete | 标记 done/failed + 结果 |
| agent_bridge_list | 列表查询（可按状态/收发方筛选） |
| agent_bridge_get | 获取单个任务详情 |
| agent_heartbeat | 心跳（30-60秒一次） |
| agent_status | 查看双方在线状态 |
| knowledge_get | 读共享知识 |
| knowledge_set | 写共享知识 |

## 任务生命周期

pending → claimed → executing → done/failed
5 分钟无更新自动回退 pending

## 角色分工

- Reasonix: 架构分析、配置审查、技能管理
- Codex: 服务端运维、MOD 部署、日志分析

## 安全

Reasonix sandbox 仅开放 _bridge/ + codex-skills-export/ 写权限，但可通过 bridge 间接指挥 Codex。
