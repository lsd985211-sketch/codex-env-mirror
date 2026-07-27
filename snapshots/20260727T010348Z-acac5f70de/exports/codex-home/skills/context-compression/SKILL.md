---
name: context-compression
description: Long-session context compression, token reduction, and cache/context hygiene. Use when context is very long, token use is too high, handoff summaries are needed, or many files/tool outputs need pruning.
---

# Context Compression + Token Saver

## 核心策略

**Anchored Iterative Summarization**（长会话文件追踪）: 维护结构化摘要（意图/文件修改/决策/下一步），增量合并而非全量重新生成。

**Opaque Compression**（短会话最大压缩）: 99%+ 压缩比但不可解释，不适合需调试/追踪场景。

**Regenerative Full Summary**（可读性优先）: 每次触发全量重新生成，风险是跨周期累积细节丢失。

## Token 节省规则

- 一句话响应，除非用户明确要求详细说明
- 不重读已读过的文件
- 跳过前导语、进度更新
- 不用 update_plan 除非用户要求
- 不运行测试/构建/格式化除非用户要求
- 修改文件时只报文件名 + 5词描述
- 不用 Mermaid、表格，代码块不超过10行

## Prompt Cache 优化
- 缓存基于前缀: 一个字节变化 = 全部缓存失效
- 稳定内容前置（规则、技能），动态内容后置（日期、文件列表）
- 编辑后接受约24h缓存重建期
- 缓存命中 ≈ <500ms 响应，未命中 ≈ >1s 首次Token

## 必须保留的内容
- 工具定义和schema（不可压缩）
- 文件路径、函数名、错误码（逐字保留）
- 已验证的架构决策和持久规则（可进入持久序言）
- 当前任务仍有效的临时约束（单独放入 `active_task_constraints`，不得进入持久序言）

## 约束作用域与生命周期

- 每条约束必须记录 `source`、`scope`、`status` 和 `expires_at`。`scope`
  只允许 `turn / task / session / workspace / global`；`status` 只允许
  `active / revoked / superseded / expired`。
- “本轮”“这次”“暂时”“当前任务不做”等表述默认是 `turn` 或 `task`
  约束，并在对应边界结束时失效。不得仅因它出现在旧摘要、交接文档或记忆中
  就提升为 `session / workspace / global`。
- 只有当前权威规则、owner 契约，或用户明确要求长期生效的指令，才能形成
  `workspace / global` 持久约束。持久约束也必须保留来源和生命周期。
- 最新用户指令可以撤销或替代较早的任务级约束。压缩、续接或恢复任务时，
  先对照最新用户请求和当前权威规则；旧摘要只提供证据，不能覆盖当前状态。
- 已撤销、被替代或已过期的约束只保留在决策历史中，不得进入
  `active_task_constraints`，也不得继续驱动工具、验证或收口行为。
- 报告中区分事实与禁令：`本轮未执行 X` 是范围事实；只有存在有效持久来源时，
  才能写成 `不得执行 X` 或 `默认不执行 X`。

## 监测指标
- 重读频率为主要质量信号
- 70-80% 上下文利用率时触发压缩
- 允许稍低压缩比换取更高质量保留

## Handoff 交接
当需要将当前会话压缩给新 agent 继续时:
- 保存到系统临时目录（非工作区）
- 包含"建议技能"章节
- 分开列出 `durable_policies`、`active_task_constraints` 和
  `revoked_or_expired_constraints`；每项保留来源、作用域、状态和失效点
- 在“立即下一步”前加入 `constraint_reconciliation`，确认最新用户指令、当前
  AGENTS/owner 契约与摘要没有冲突；有冲突时以当前高权威来源为准
- 不重复已有 artifact（PRD/plan/issue/commit/diff），引用路径或 URL
- 脱敏：去掉 API key、密码、个人信息
- 若用户给了参数，作为下个会话重点方向定制文档

## Preflight
- Confirm the skill matches the task before using it.
- Keep the scope tight and avoid unrelated changes.
## Output Contract
- State the result, blockers, and any key tradeoffs.
- Keep it concise and actionable.
