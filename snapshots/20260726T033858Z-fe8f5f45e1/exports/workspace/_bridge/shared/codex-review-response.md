# Codex Review: Agent Bridge v2

## 结论

这个方案在“同机、双 Agent、低部署复杂度”的约束下是成立的，作为 v2 原型是合理的；但它现在更像“可用的任务邮箱”，还不是“稳健的协作总线”。

我会通过以下原则调整：

1. 保留 SQLite WAL 作为当前传输层，不急着改成 named pipe 或 local HTTP。
2. 把任务状态机和超时语义收紧，避免重复执行和任务回退抖动。
3. 明确 bridge 是“能力放大器”，把授权边界从“谁发任务”改成“哪些任务允许被执行”。
4. heartbeat 只作为“最近活跃”信号，不要把它当强一致在线判定。
5. 增加审计、幂等、取消、优先级和附件引用这几个缺口。

## 逐点评审

### 1. SQLite shared DB vs named pipe / local HTTP

对于当前场景，我支持 **SQLite WAL 优先**：

- 优点：
  - 零常驻中间服务，部署最简单。
  - 天然有持久化、审计和重启后恢复。
  - 对任务队列、心跳、知识共享这类“低频小消息”足够。
  - 两边都容易调试，直接查库即可排障。

- 缺点：
  - 不适合高频事件流，也不适合长消息/大附件。
  - 并发语义要自己补，不然容易出现 claim/timeout 竞态。
  - “在线”本质是轮询推断，不是实时连接状态。

对比建议：

- `named pipe`
  - 更适合低延迟实时消息。
  - 但调试性、可审计性、断线恢复都不如 SQLite。
  - Windows 友好，但跨平台和工具生态更差。

- `local HTTP`
  - 更标准，扩展到多进程/多机更自然。
  - 但要管理端口、服务生命周期、鉴权和异常恢复。
  - 对你现在这个同机双 Agent 场景，复杂度偏高。

结论：**v2 继续用 SQLite；如果以后要扩到多 Agent 或需要流式通信，再考虑 local HTTP。**

### 2. 状态机与 5 分钟超时回退

现在的 `pending -> claimed -> executing -> done/failed` 基本够用，但超时自动回退到 `pending` 有明显风险：

- Agent 实际还在执行，只是没刷新心跳，任务会被二次领取。
- 长任务和短任务共用 5 分钟超时，语义太粗。
- `claimed` 和 `executing` 的边界不清晰，恢复策略不好做。

我建议最少改成：

- `pending`
- `claimed`
- `running`
- `done`
- `failed`
- `cancelled`

并增加这些字段：

- `lease_expires_at`
- `attempt_count`
- `worker_session_id`
- `idempotency_key`
- `last_progress_at`

关键语义：

- `claimed` 只表示“拿到了租约，还没开始干”。
- `running` 表示“已经开始执行，并持续刷新租约”。
- 超时不要直接回退 `pending`，而是：
  - 先标记为 `stale_claim` 或重新置 `pending` 时递增 `attempt_count`
  - 并保留原执行者信息
- 达到最大重试次数后自动 `failed`

如果你坚持保持最小状态数，那也至少要把“5 分钟回退”改成“**租约过期后可重新领取，但必须保留 attempt 和 owner 痕迹**”。

### 3. Reasonix 只写 `_bridge/` 但可间接指挥 Codex

这不是偶然副作用，而是**设计上的能力代理**。它可以接受，但前提是你明确承认这一点，并做约束。

本质上：

- Reasonix 虽然没有工作区写权限，
- 但它可以通过桥接任务要求 Codex 去改文件、执行命令、读取高权限上下文，
- 所以真实安全边界不在 sandbox，而在 **Codex 对任务的执行策略**。

因此这不是“致命缺陷”，但如果不加治理，就是“伪隔离”。

我建议：

- 给任务增加 `risk_level`：`read_only` / `workspace_write` / `external_side_effect`
- Codex 侧按风险做二次审批或策略拒绝
- 对高风险任务记录：
  - 请求方
  - 原始任务文本
  - 最终执行摘要
- 明确禁止“转译式提权”：
  - 例如 Reasonix 生成模糊任务，让 Codex 自主补全危险操作

结论：**可接受，但要把 trust boundary 写进方案，而不是假设 sandbox 自己能兜底。**

### 4. heartbeat 对非 daemon 进程是否合理

合理，但只能做弱在线信号。

对 CLI Agent 来说：

- 有会话时发 heartbeat，没会话时离线，这个模型是自然的。
- 不要把“没 heartbeat”解释成“进程异常”，只能解释成“当前没有活动会话或最近未汇报”。

建议：

- 状态展示只分：
  - `online_recently`
  - `idle`
  - `stale`
  - `offline`
- 把 heartbeat TTL 设计成 90 到 180 秒，而不是 30 秒硬判死
- 长任务期间由执行侧每 30 秒续租/续心跳

如果未来要做真正在线协作，再补一个常驻 bridge daemon；在当前阶段没必要。

### 5. 当前遗漏的关键功能

我认为缺这几项最关键：

1. **幂等与去重**
   - 同一任务被重复发送或重复完成时怎么处理，现在没定义。

2. **取消机制**
   - 没有 `cancel` 或 `superseded`，长任务不好中断。

3. **进度上报**
   - 只有最终结果不够，至少要支持短进度日志或阶段状态。

4. **结果附件引用**
   - 长文本、日志、diff、截图不该都塞进 `result` 字段。
   - 应支持文件路径或 artifact 引用。

5. **会话隔离**
   - 需要 `conversation_id` / `session_id`，避免跨轮污染。

6. **审计字段**
   - 谁创建、谁领取、谁完成、重试几次、何时租约续期，都应可追。

7. **失败分类**
   - `failed` 至少区分：
     - invalid_request
     - permission_denied
     - execution_error
     - timeout
     - abandoned

### 6. 如果由我来设计

我会分两层：

- **Layer 1: SQLite task bus**
  - 只负责任务、租约、心跳、知识条目、审计。
  - 保持无常驻服务、可恢复、可排障。

- **Layer 2: execution policy**
  - 不放在 bridge 里，而放在各 Agent 本地规则里。
  - 例如：
    - 什么任务可自动执行
    - 什么任务必须征求用户同意
    - 什么任务必须拒绝

最小可升级方案：

1. 维持 SQLite WAL。
2. 引入 lease 字段和 attempt 计数。
3. 增加 `cancelled`、`risk_level`、`session_id`。
4. 给 `result` 增加 artifact 引用约定。
5. 把“安全边界在执行侧策略，不在 bridge”正式写入文档。

## 最终判断

**这套 v2 方案值得继续推进。**

它的正确方向不是“换传输层”，而是：

- 保留 SQLite 的简单性，
- 补齐租约、幂等、审计和执行策略，
- 把它从“能通消息”提升到“可控协作”。
