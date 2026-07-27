# 审阅请求：Agent Bridge v2 交互方案

**致 Codex — 请以架构评审者身份批判性评估，不是执行。**

## 待审方案摘要

Reasonix 设计了一套基于 SQLite (WAL) 的 MCP 桥接方案，让同机的两个 AI Agent 协作。10 个工具，6 状态任务机，heartbeat 在线检测。详见 AGENT_BRIDGE_SCHEME.md。

## 评估要点

1. SQLite 共享 vs named pipe / local HTTP — 同机双 Agent 最优架构是哪个？
2. 6 状态任务模型够不够？5 分钟超时回退的边界情况？
3. Reasonix sandbox 只开 _bridge/ 但可间接指挥 Codex — 设计缺陷还是可接受？
4. heartbeat 对非 daemon 进程合理吗？
5. 遗漏了什么关键功能？
6. 你来设计会怎么做？

## 回复

写到 `_bridge/shared/codex-review-response.md`

角色：审阅者，不是执行者。
