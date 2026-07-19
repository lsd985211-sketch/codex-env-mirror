# Codex 本机工作框架架构白皮书

版本：2026-07-13  
定位：面向人类阅读的本机 Codex 工作环境白皮书  
范围：`C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager`

本文档说明当前本机 Codex 工作框架的设计目标、模块分层、运行机制、工具协同、治理边界和演进方向。它沿用旧版“框架总览”的思路，但把重点从“列出有哪些东西”升级为“解释系统为什么这样组织、各模块如何协作、问题应该从哪里治理”。

本文档不是新的配置源，不替代以下来源：

- 全局规则：`C:\Users\45543\.codex\AGENTS.md`
- 工作区规则：`AGENTS.md`
- 工具能力矩阵：`_bridge/docs/mcp_capability_matrix.md`
- 维护面地图：`_bridge/docs/maintenance_surface_map.md`
- 代码级真实入口：`_bridge/` 下各 owning module

## 0. 方法来源与吸收方式

新版白皮书吸收了几类成熟架构文档方法，但只取适合本机框架的部分，不照搬模板。

| 方法 | 成熟做法 | 本机吸收方式 |
| --- | --- | --- |
| C4 model | 用不同缩放层级解释系统，从上下文到容器、组件、代码；不是每个层级都必须画，只画有价值的层级。 | 本文先给总体上下文，再给工具、资源、记忆、桥接、调度等容器级模块；代码级细节交给维护面和模块索引。 |
| arc42 | 用目标、约束、上下文、解决策略、构建块、运行时、质量、风险和技术债组织架构知识。 | 本文把设计原则、模块地图、关键运行链路、维护面、风险路线分开，避免把状态、规则和愿景混在一起。 |
| 开发者文档风格 | 标题要能导航，步骤用编号，关系型信息用表格，代码/路径用等宽格式。 | 本文减少长段说明，使用表格、短节、图表和入口命令，让人能快速定位“该看哪里、该跑什么”。 |
| 运营卓越 | 自动化、监控、诊断、恢复、指标和持续改进是系统能力，不是事后补丁。 | 本机每个长期模块都应有 snapshot、doctor、repair-plan、validate、metrics 或等价维护入口。 |

这些方法给出的不是新的权限或执行规则，而是文档组织和系统治理准则：先把系统边界讲清楚，再把运行路径讲清楚，最后把风险、验证和演进路线讲清楚。

## 1. 执行摘要

本机 Codex 环境已经不是“一个聊天模型加一组脚本”，而是一个本地 Agent 工作系统。它由规则、记忆、技能、工具、资源层、业务模块、维护面和收口机制共同组成。

系统的核心目标有四个：

1. 让 Codex 在复杂任务中更少临场猜测，更多依赖记忆、技能、工具表和维护入口。
2. 让 MCP、Hub、CLI、SQLite、GUI、微信桥接、邮箱、资源层各自承担清晰职责。
3. 让系统可治理：每个重要模块都应有 snapshot、doctor、repair-plan、validate、metrics 或等价维护面。
4. 让能力不断沉淀：工作中发现的经验进入临时笔记、提案、记忆、技能、基线或外部知识库，而不是丢在长对话里。

当前系统状态可概括为：

| 领域 | 当前状态 | 关键事实 |
| --- | --- | --- |
| 工作流编排 | 健康 | validator 通过，结构化 task contract 和九阶段 route pack 可用 |
| Hub | 健康 | classified affinity 已启用；当前进程累计 1378 次请求、282 次工具调用 |
| MCP 会话诊断 | 健康但需按 turn 验证 | 22 个 profile，9 个已配置且进程存在，15 个有 fallback |
| PMB/记忆 | 健康 | PMB 523 条活动事实/事件；daemon 单实例运行，记忆候选为 0 |
| 技能系统 | 健康 | 238 个有效技能入口，无无效契约、未解决冲突或陈旧候选 |
| 资源层 | 可用但 source executor 仍需补齐 | 请求所有权、进度、回执和 handoff 成熟 |
| 网络网关 | 可用但节点控制降级 | 路线缓存、batch plan、lease 正常；Mihomo TCP controller 不可达 |
| 邮件调度 | 健康 | 常驻 scheduler、附件上下文、分阶段发送和索引查询面可用 |
| 记录库 | 可用且索引化 | 36635 条 records、1134 条资源请求、8078 条资源事件 |
| 备份治理 | 健康 | 21 个项目备份文件、897 份 manifest，资源库统一使用 `_backup` |
| 代码可维护性 | 持续治理中 | 548 个模块资产，87 个稳定维护面，42 个重构候选 |

## 2. 设计原则

### 2.1 规则先于动作

Codex 做本机任务时，优先读取规则与领域入口，而不是直接写脚本。全局规则负责通用安全边界；工作区规则负责本项目入口、模块所有权和验证路径。

这条原则解决的问题是：避免每次任务都从零开始、避免误用工具、避免把短期修复写成长期隐患。

### 2.2 按 Profile Affinity 选择起点，按统一链路降级

当前机制不再使用全局 Native-first。无状态只读 owner 通常 Hub-first；GUI、浏览器等会话绑定能力通常 Native-first；GitHub、PMB、SQLite、资源和网络等已有 Hub direct 的能力直接使用 Hub。每个 profile 的起点由 capability matrix 决定，失败后只能从当前阶段沿统一链路向后尝试。

“配置存在”和“当前 turn 可调用”仍不是一回事。工具可用性必须分层判断：

| 层级 | 含义 |
| --- | --- |
| `config_ok` | 配置存在且命令合理 |
| `protocol_ok` | initialize / tools-list smoke 能通过 |
| `current_turn_exposed` | 当前 Codex turn 暴露对应 namespace |
| `current_turn_callable` | 当前 turn 实际调用成功 |
| `call_completed` | 目标调用返回有效结果 |

Hub 和 fallback 的定位是连续性，不是绕过权限，也不是把失败伪装成成功。`complete-route` 只用于未知、权限、schema、路由歧义和诊断，不作为普通调用入口。

### 2.3 维护面是系统能力的一部分

一个系统模块如果只有业务代码，没有维护入口，就很难长期稳定。系统级模块必须至少考虑：

- snapshot：当前状态怎么看。
- doctor：问题如何分类。
- repair-plan：修复前如何 dry-run。
- validate：改完如何证明没有破坏。
- metrics：规模、趋势、风险如何量化。

### 2.4 机器优先，人类可读作为白皮书目标

日常工作入口应优先服务 Codex：低歧义、结构化、低 token、可路由、可验证。面向人类的文档只作为解释层，不应成为运行时唯一依赖。

因此，Markdown、白皮书、总览图用于理解；JSON route index、doctor 输出、metrics、SQLite 索引用于执行。

### 2.5 简洁不等于删功能

系统治理的目标是减少重复劳动和脆弱路径，不是减少能力。遇到弹窗、慢启动、MCP 断连、资源扇出等问题时，优先寻找隐藏执行、稳定入口、生命周期治理、按需启动、守护进程和 bounded fallback，而不是直接砍掉功能。

## 3. 总体架构



![Figure 1: diagram-01](diagram-01.png){width=5.51in}



总体分层可以理解为：

| 层 | 主要责任 | 不应该承担 |
| --- | --- | --- |
| 规则层 | 安全边界、工作准则、授权约束 | 具体执行细节 |
| 工作入口层 | 把任务拆成记忆、技能、工具、验证、收口阶段 | 直接写业务状态 |
| 记忆层 | 提供历史结论、用户偏好、可复用经验 | 存储秘密、替代验证 |
| 技能层 | 提供领域方法和操作习惯 | 静态合并所有知识 |
| 模板层 | 固定流程 checklist | 自动执行或授权 |
| 工具层 | 调用外部系统和本地能力 | 绕过权限边界 |
| 资源层 | 获取、缓存、验证、记录资源 | 假装能替 owner MCP 完成所有外部动作 |
| 业务模块 | 完成具体业务状态变更 | 维护其他模块 |
| 维护面 | 诊断、计划、验证、指标 | 直接替业务逻辑做隐式修复 |
| 收口层 | 把经验变成提案和持久化候选 | 静默写长期记忆 |

### 3.1 十二个受治理系统

`system_membership.py` 当前登记 12 个系统、31 条架构影响规则。成员契约的作用不是增加审批，而是保证新增成员或改变架构时，同步更新真正受影响的入口、路由、维护面和验证证据。

| 系统 | 核心责任 | 主要权威面 |
| --- | --- | --- |
| `workflow` | 任务分类、阶段计划、验证与收口 | `workflow_orchestrator.py`、`codex_workflow_entry.py` |
| `mcp` | profile affinity、Hub/Native/CLI 降级和调用证据 | capability matrix、routes、session doctor |
| `resource` | 结构化资源委托、来源策略、物化、回执与消费 | `resource_cli.py`、request manifest/receipt |
| `network` | 单次请求的直连/代理/节点/lease 路线 | network gateway、route cache |
| `memory` | PMB、画像、候选、外部知识和召回治理 | memory router/governance、PMB daemon |
| `skills` | 技能发现、选择、增量索引、冲突与生命周期 | skill orchestrator、MySkills inventory |
| `records` | 大规模运行记录的索引、查询、归档和指标 | record-store SQLite、maintenance owner |
| `mail` | 收件、附件上下文、任务化回信、SMTP 回执 | email scheduler、mail task table |
| `bridge` | 手机消息、权限、任务投递、补充消息和结果回发 | mobile bridge DB、worker、permission table |
| `office` | Word/Excel/PowerPoint/PDF 的可重复编辑和验证 | Office harness、Office skills、应用 readback |
| `startup` | Codex 启动、会话恢复、provider/catalog/reasoning 同步 | config guard、session doctor、provider watcher |
| `drafts` | 不执行的草案资产及其生命周期元数据 | draft governance；不等于审批队列 |



![Figure 2: diagram-02](diagram-02.png){width=6.20in}



### 3.2 2026-07-13 数据快照

下表是生成白皮书时的观测值，不替代 owner 的实时查询。机器可读版本见同目录 `system_framework_whitepaper_snapshot.json`。

| 维度 | 数值 | 含义 |
| --- | ---: | --- |
| 系统 / 架构影响规则 | 12 / 31 | 成员契约覆盖范围 |
| 模块资产 | 548 | 可被维护任务和代码任务检索的模块 |
| 稳定维护面 / 重构候选 / 回归资产 | 87 / 42 / 26 | 当前代码治理结构 |
| 有效技能 | 238 | 用户 148、系统 5、插件 85 |
| MCP profile / 进程存在 / 有 fallback | 22 / 9 / 15 | 配置存在不等于本 turn 已调用 |
| PMB 活动事实与事件 | 523 | 当前长期连续性证据 |
| 记录 / 资源请求 / 资源事件 | 36635 / 1134 / 8078 | SQLite 索引中的运行资产 |
| 事故家族 / 事故发生次数 | 31 / 2155 | 去重后的故障类型与原始发生量 |
| SMTP 回执 / inbox 消息 / 完成 inbox job | 21 / 55 / 6 | 邮件链路规模 |
| 备份文件 / manifest / 散落备份 | 21 / 897 / 3 | 统一备份治理结果 |
| Hub 请求 / 工具调用 | 1378 / 282 | 当前 Hub 进程累计观测 |
| 网络 route decision / observation / active lease | 20 / 1 / 0 | 当前网络画像规模 |
| provider catalog 模型数 | 8 | 当前模型目录与 reasoning bridge 健康 |



![Figure 3: diagram-03](diagram-03.png){width=6.20in}



## 4. 标准工作机制

非简单任务应按下面路径执行。



![Figure 4: diagram-04](diagram-04.png){width=6.20in}



对应本机入口：

| 阶段 | 推荐入口 | 说明 |
| --- | --- | --- |
| 统一预检 | `_bridge/codex_workflow_entry.py preflight --message "..."` | 机器优先工作包 |
| 工作流计划 | `_bridge/workflow_orchestrator.py plan --message "..."` | 任务域、技能、模板、工具路线 |
| 记忆路由 | `_bridge/memory_router.py route --message "..."` | 按任务选择 current context / quick pass / PMB / 用户画像 / 外部知识 / record-store / 临时笔记 |
| 记忆预检 | `_bridge/codex_workflow_gate.py memory-preflight --message "..."` | 低成本辅助判断；不替代记忆路由 |
| 技能选择 | `_bridge/skill_orchestrator.py plan --message "..."` | 动态技能路由 |
| 工具路线 | `_bridge/mcp_capability_routes.py lookup --terms "..."` | JSON 派生路由 |
| 模块复用 | `_bridge/code_maintainability.py lookup-module ...` | 先找可复用模块 |
| 收口 | `_bridge/codex_workflow_entry.py closeout ...` | 统一收口包 |

关键改进点是：收口不再只看临时笔记，而是读取一个统一 closeout package。临时笔记只是其中一项，Codex 需要在主任务完成后处理这些 side items；但主任务授权不会自动扩展到临时笔记派生的新任务。

所有需要用户审批的 closeout 项必须附带具体 `review_items`：标题、实际内容摘要、来源、属性、建议动作和必要检查。owner 只有状态或数量但没有明细时，收口必须标记 `evidence_complete=false`，不能生成看似可审批的泛化卡片。已经通过既定路线解决的证据，例如原生 MCP 失败后 Hub fallback 成功，只保留为工具证据，不进入待审批卡片。相同事项若已由 work note、proposal 等具体队列承载，自更新层不得重复生成摘要卡片。

### 4.1 草案、提案、审批卡片和执行任务

这四类对象必须分开，否则 closeout 会反复展示已经处理或根本不应审批的内容。

| 对象 | 含义 | 是否执行 | 是否默认展示审批 |
| --- | --- | --- | --- |
| 草案区 artifact | 尚未进入执行或审批流程的参考材料 | 否 | 否 |
| proposal | 有明确变更目标、影响面和验证方案的候选 | 否 | 只有进入 review queue 才展示 |
| review card | owner 提供具体内容、来源和建议动作的待决事项 | 否 | 是，处置后不再重复展示 |
| execution task | 已获授权、由 owner 执行并产生回执的任务 | 是 | 不作为待审卡片重复出现 |

“保留草案”只表示留在草案区，不执行、不进入审批队列、不在每次收口重复提醒。文件名、目录名和自然语言不能单独决定状态；状态由 owner 的生命周期元数据决定。

## 5. 工具层与 Hub 机制

工具层由 Native MCP、Hub、CLI、SQLite、浏览器/GUI 和项目维护命令共同构成。它不是越多越好，而是要让每个工具做自己最擅长的事。不同 profile 可以有不同起点，但所有失败都必须沿同一优先级链继续。



![Figure 5: diagram-05](diagram-05.png){width=3.01in}



当前工具策略：

- CodeGraph：用于代码结构、调用路径、影响面；不用于宽泛 Markdown/config 搜索。
- `rg` / `fd`：用于宽文本和文件发现，避免扫生成树和大型缓存。
- SQLite MCP：用于结构化状态检查和 scratch；生产写入仍走业务 owner。
- GitHub MCP：远端仓库权威来源；本地 git 不能证明远端变化。
- Hub：无状态 owner、GitHub、PMB、SQLite、资源和网络的稳定入口；低频工具通过 catalog/search/describe/call 按需发现。
- GUI / Browser：用于 UI 证据，不应替代 API/CLI 可证明的状态。
- CLI-Anything / cli-hub：把外部 GUI 或桌面能力封装为可重复 CLI。

当前 MCP session 诊断显示：profile 总数 22，9 个已配置且进程存在，15 个有 fallback。当前 turn 没有逐个做真实调用，因此 callability 仍是未逐项验证状态。这是健康但保守的结论。

## 6. 资源层机制

资源层的定位是：Codex 提交机器可读资源委托，资源层持有该需求直到终态回执，自动分类、规划来源、调用 owner、保存 receipt、记录进度和异常。它减轻 Codex 手动判断检索、下载、缓存、校验、预览和归档的负担。



![Figure 6: diagram-06](diagram-06.png){width=1.66in}



当前入口：

```powershell
python _bridge\resource_cli.py request ...
python _bridge\resource_cli.py status ...
python _bridge\resource_cli.py attach-result ...
python _bridge\resource_cli.py strategy-review
python _bridge\resource_cli.py inspect-cache --json
```

当前记录索引包含 1134 条资源请求和 8078 条资源事件。资源层已经具备 request ownership、批处理、进度、异常分类、回执、consume contract 和结果附回能力。

边界：

- 资源层可以自动分类和尝试安全路径。
- 资源层可以记录 package-manager 风险，交给 Codex 判断。
- 资源层不能绕过 MCP/系统权限边界。
- owner MCP 才能证明 GitHub、浏览器、外部文档等领域状态。

高频委托主要依靠结构化字段：`resource_kind`、数量范围、来源模式、站点/域、时效、权威性、格式、许可、唯一性、去重键、保存策略、owner 偏好、超时和重试。自然语言用于描述目标和补充缺失字段，不承担全部分流责任。



![Figure 7: diagram-07](diagram-07.png){width=4.27in}



`handoff_required` 不代表资源层释放任务。Codex 应在同一个 request_id 下调用指定 owner 并把结果附回；只有 completed 且 Codex 读取 required consume path，资源需求才真正结束。

当前主要缺口是：跨多个普通官方站点的无 URL 研究，可能被误分到 Context7/Microsoft Docs，而 broker 内部 arbitrary search executor 尚未覆盖所有场景。本次白皮书研究请求 `res_5a203f41e7549e2a` 通过同一请求调用 `resource_search_text` 并附回结果完成。后续应补齐正式 source executor adapter，而不是让 Codex 绕开资源层直接搜索。

### 6.1 网络网关协作

资源层决定“找什么、用哪个来源和 owner”；网络网关决定“这次请求如何联网”。网关按 `target_kind + host + owner_tool + runtime` 生成路线，比较直连和代理，返回进程级环境，维护缓存、熔断和 TTL lease，不修改系统代理、DNS、Clash 配置或 Codex 对话路由。

| 项目 | 当前状态 |
| --- | --- |
| 路线缓存 | 正常，20 条 decision、1 条 observation 快照 |
| batch plan | 正常，owner_tool 已纳入缓存维度 |
| 隔离 lease | 正常，当前 active lease 为 0 |
| Mihomo TCP control | 降级：pipe-only，配置的 TCP controller 不可达 |
| 当前代理监听 | `127.0.0.1:7897` |

网络重试采用有限预算：只重试超时、连接拒绝、408、429、5xx 等瞬时错误；404、参数错误、权限错误和资源不存在不盲目重试；尊重 `Retry-After`，使用指数退避、jitter 和短期熔断。

## 7. 记忆、PMB、外部知识与临时笔记

记忆系统分为四类：长期记忆、PMB、外部知识证据层、一次性临时笔记。



![Figure 8: diagram-08](diagram-08.png){width=3.72in}



当前状态：

- PMB daemon running/warm。
- 用户画像 20 条 fact，12 条 active guidance。
- ad hoc candidate notes 4 条。
- one-shot work notes 当前 0 条。
- 外部知识 evidence item 32 条，`official` 12、`primary` 15、`reputable` 5。

记忆不是“任务结束才写点总结”，而是持续工作层：

1. 开始前召回历史结论。
2. 诊断中用记忆作为第一假设。
3. 发现旧记忆错误时标记待修正。
4. 收口时把可复用经验压缩成候选。
5. 批准后写入 PMB、画像、基线或技能。

临时笔记的角色非常窄：只记录当前任务中不直接阻塞但容易丢失的 side issue。它不是第二个笔记本，也不是授权凭证。

## 8. 技能、模板与模块复用

技能系统负责“怎么做”，模板系统负责“固定流程从哪里开始”，模块索引负责“代码里有没有现成能力可复用”。用户维护技能的唯一活跃根是 `~/.codex/skills`，MySkills 的 canonical platform 必须指向该根；`.bak-*` 等历史副本只能进入桌面资源库的统一备份区，不能留在技能发现路径中。



![Figure 9: diagram-09](diagram-09.png){width=6.20in}



当前状态：

- 当前用户活跃技能 148 个、系统技能 5 个、插件技能 85 个，总有效入口 238 个；MySkills inventory 按来源维护 canonical 记录。
- MySkills inventory 可用。
- 无无效契约、未解决冲突或陈旧候选。
- 模块能力索引由 `code_maintainability.py` 派生，服务于后续代码修改。

设计约束：

- 技能不应静态合并到一个大规则里。
- 技能按渐进披露加载：启动阶段只读名称、描述和兼容性元数据，命中后再读完整 `SKILL.md`，脚本与引用只在执行需要时加载。
- 缺失核心脚本或缺少声明环境变量的技能保留源文件但进入路由隔离；补齐依赖后自动恢复，不用维护按名称黑名单。
- 旧技能入口可通过 `metadata.codex.superseded_by` 保留兼容性，但不再参与自动路由；当前 owner 负责唯一执行实现。
- 专项业务域优先于通用文件域，例如飞书文档进入 `feishu-wiki`，不会同时加载普通 Office/PDF 技能。
- 插件技能使用来源前缀命名空间；用户技能保留无前缀名称，因此能力重叠不等于未解决冲突。
- 默认 `SKILL.md` 建议不超过 500 行；超出时保留短入口，把完整语法、案例和历史说明迁入 `references/` 按需加载。
- 单一元数据关键词只产生低相关候选，不应进入最终技能集；专项域可以显式抑制通用父技能，避免内容和上下文重复。
- `references/full-guide.md` 不能成为孤立存档：默认入口必须写明何时加载它，生命周期 doctor 会持续检查该连接。
- 每次技能路由和自更新检查都会先执行轻量增量 refresh。新增、删除、重命名或修改技能时，只重新解析发生变化的技能树；未变化技能复用 SQLite 索引，不重复做全量内容治理。
- 增量指纹覆盖 `SKILL.md`、scripts、references、assets 和 agents 元数据；环境变量等运行时条件每次重新判定。变更事件保存在 `_bridge/runtime/skill_lifecycle/skill_lifecycle.sqlite`，供 `refresh`、`state`、doctor、metrics 和后续回归治理查询。
- 模板不执行，只给 checklist。
- 代码修改前应先查模块能力，避免重复造工具。
- 高价值跨领域模块要进入 module capability index。

模块目录提供两种视图，而不是维护两套索引：

| 任务模式 | 首要过滤维度 | 典型问题 |
| --- | --- | --- |
| 维护治理 | 所属系统、owner、承担职责、维护面、影响规则 | “修改 provider 同步机制会影响哪些系统？” |
| 代码实现 | 模块能力、适用场景、输入输出、复用边界、测试资产 | “是否已有下载、邮件附件或 Office 预览模块？” |

两种视图共享同一份派生模块事实，分别投影所需字段，避免“为了分类再复制一套目录”。

## 9. 微信桥接系统

微信桥接是本机最复杂的业务基础设施。它连接手机端消息、OpenClaw、权限、Codex 投递、owned-result、附件、dashboard、repair 和 worker。



![Figure 10: diagram-10](diagram-10.png){width=6.20in}



当前桥接 DB 状态：

- integrity check ok。
- schema ok。
- FK check 0。
- paused false。
- shadow_mode false。
- allowed users 14。
- status counts 包含 `done=111`、`pushed_to_wecom=770`、`failed=26`、`push_failed=3`。

关键机制：

- `mobile_ack` 只表示收到，不表示任务完成。
- 最终结果必须走 owned-result marker。
- 生成物放桥接附件区，并按账号隔离。
- capability token 只扩展窄权限，不能读取敏感信息或破坏本机。
- 用户请求涉及默认没有的权限时，先判断令牌是否有效，再要求口令。
- 附件发送要区分文本成功、媒体上传成功、手机端投递成功，不能只看本地提交。

当前最大工程风险仍是代码体量：

- `mobile_openclaw_cli.py` 约 13197 行。
- `mobile_maintenance.py` 约 5870 行。
- `mobile_dashboard.py` 约 3155 行。

治理策略不是一次性重写，而是继续把纯功能、状态判断、prompt contract、权限、补充消息、route state、dashboard state、worker 子阶段拆到明确模块中，同时保留 facade 和回归测试。

## 10. 邮箱、收件箱与调度机制

邮箱模块已从发件能力扩展为收件、识别、回信、统一队列和定时驱动。



![Figure 11: diagram-11](diagram-11.png){width=2.54in}



优先级的含义不是“谁更重要先处理”，而是避免系统卡顿：

- 控制同时生成数量。
- 控制附件/异常邮件进入 review。
- 避免普通收件箱吞掉 worker。
- 统一入口仍是任务表和调度器。

一次性邮件发送成功可以算完成；周期邮件不应按一次性任务销毁。

当前可观测值：SMTP 回执 21、inbox 消息 55、完成 inbox job 6；scheduler 间隔 60 秒；失败最多重试 3 次，基础退避 300 秒。回信附件必须先实际落盘、进入任务上下文，再随回复交给 Codex；只保存附件元数据不算完成接入。

## 11. 文件、备份、资源库与记录库

文件修改必须先走备份路由。当前备份策略是：



![Figure 12: diagram-12](diagram-12.png){width=6.20in}



备份当前可观测：

- `backup_hygiene_doctor.py validate` 通过。
- 项目备份文件 21 个、manifest 897 份、散落备份 3 个。
- 桌面资源库唯一正式备份根：`C:\Users\45543\Desktop\Codex资源库\_backup`。
- repair-plan 为 dry-run only。

记录库当前可观测：

- records：36635。
- resource requests：1134；resource events：8078。
- incident families：31；incident occurrences：2155。
- 索引：`C:\Users\45543\Desktop\Codex资源库\文档\系统维护\索引\record_store.sqlite`。

记录库治理的重点是查询化、索引化、分区化，而不是继续制造大 JSON 或让人工翻文件。高频查询应先走 SQLite；原始文件保留审计和恢复价值，但不再作为默认扫描面。新记录优先写“摘要 + 稳定元数据 + raw 引用”，冷数据按周期归档，索引刷新根据使用频率缩短而不是每次全量重建。

## 12. GUI、浏览器、OCR 与桌面微信

GUI 层负责真实界面证据，但不是默认首选。优先级：

1. API / CLI / 数据库能证明状态时，不用 GUI。
2. 需要界面证据时，先用 GUI MCP / 浏览器 MCP / Playwright。
3. OCR 优先区域化，避免全屏重 OCR。
4. 能隐藏执行就不弹窗；不能牺牲功能来换安静，而应改进隐藏执行路径。

桌面微信能力目前分三层：

- mobile OpenClaw bridge：手机桥接和远端微信消息链路。
- desktop-weixin MCP：本机桌面微信结构化工具入口。
- cli-anything-weixin：将桌面操作封装为可重复 CLI 的 harness。

这三者不能混为一谈。手机桥接负责任务投递和回发；桌面微信工具负责本机窗口、截图、草稿、联系人等操作；GUI/OCR 作为兜底证据层。

### 12.1 Office 自动化

Office 系统采用“结构化编辑优先、原生应用验证补充”的分层方式：

1. 文档、表格、演示文稿和 PDF 的创建/结构化编辑优先使用对应技能和库。
2. 需要真实 Microsoft Office 行为时，通过 CLI-Anything Office harness 调用已安装应用。
3. 保存后必须做结构 readback；涉及版式、分页、公式、动画或宏时再做应用级或渲染级验证。
4. harness 是 adapter，不复制 Office 文件状态；权威状态仍是实际文件和应用保存结果。

Office 自动化不应通过任意 GUI 点击完成所有工作，也不能把应用“打开成功”误判为内容“保存正确”。

### 12.2 Codex 启动、Provider、模型目录与推理强度

启动系统同时治理配置、会话恢复、MCP 启动基线、provider catalog、Desktop 目录缓存和 reasoning 选项。模型列表和推理强度是两个相关但独立的数据面：页面重载必须同时刷新 catalog 与 reasoning catalog，不能只更新单一 `ultra` gate。



![Figure 13: diagram-13](diagram-13.png){width=6.20in}



持久原则：

- provider 改变由指纹触发同步，不依赖人工记忆重启。
- 原生模型与自定义模型是否并存由当前 provider catalog 语义决定，不强行合并。
- reasoning 选项按模型能力集合生成，不能假设所有模型只有固定四档或只补一个 gate。
- 页面重载和进程重启都应收敛到相同结果；重载只刷新前端而不刷新 reasoning 数据源不算完成。
- 不修改 Desktop ASAR，不全局禁用 Statsig，不用持续覆盖配置来掩盖 owner 同步问题。
- API Key 模式下 `/wham/tasks/list`、`/wham/usage` 的 `hadToken=false` 是 ChatGPT 账号令牌缺失导致的非致命后台 401，不应再误判为 Cookies 数据库缺失或启动失败根因。

## 13. 代码可维护性机制

本机代码治理已经从“发现哪个文件大就拆哪个”升级为“按模块用途和业务边界治理”。



![Figure 14: diagram-14](diagram-14.png){width=3.41in}



模块命名原则：

- 模块名要贴近功能，不用抽象大词。
- 模块顶部写简短功能注释，说明 owns / does not own。
- 业务状态、维护状态、工具状态分层。
- 大 CLI 可以作为 facade，但核心逻辑要逐步迁到专门模块。
- 新代码优先 snake_case / PascalCase / UPPER_SNAKE_CASE，避免无意义变量。

当前可维护性风险：

| 模块 | 风险 |
| --- | --- |
| `mobile_openclaw_cli.py` | 文件仍大，`main` 和 result/reply recovery 复杂 |
| `mobile_maintenance.py` | `diagnose_system`、`summary_report` 过大 |
| `mcp_session_doctor.py` | 文件大，但边界清晰 |
| `resource_process_doctor.py` | 文件大，需继续拆输出组装和清理计划 |
| `workflow_orchestrator.py` | `build_plan` 偏大，可继续拆 profile/phase builder |

## 14. 维护面地图

| 维护面 | 主要责任 | 常用命令 |
| --- | --- | --- |
| 工作流 | 任务路由、阶段计划 | `workflow_orchestrator.py plan|validate` |
| 统一入口 | preflight / closeout package | `codex_workflow_entry.py preflight|closeout` |
| 工具层 | MCP session、current-turn、gateway | `mcp_session_doctor.py validate|metrics` |
| Hub | 本地 HTTP Hub 健康 | `local_mcp_hub.py validate|smoke` |
| 资源进程 | 进程扇出、stale roots | `resource_process_doctor.py doctor|metrics` |
| 资源层 | 资源请求、manifest、receipt | `resource_cli.py request|status|strategy-review` |
| 记忆 | notes、PMB、画像、临时笔记 | `memory_governance.py snapshot|doctor|validate` |
| 技能 | 技能路由、gap、usage | `skill_orchestrator.py plan|snapshot|validate` |
| 备份 | 备份路由和散落备份 | `backup_router.py create` / `backup_hygiene_doctor.py validate` |
| 记录库 | 大记录索引和指标 | `shared/record_store_maintenance.py metrics|validate` |
| 编码 | UTF-8、中文路径、乱码 | `encoding_governance.py doctor|validate` |
| 桥接 | 手机任务、权限、回发 | `mobile_openclaw_cli.py status|maintenance|stability-check` |
| 弹窗 | PowerShell/cmd/conhost 来源 | `popup_window_doctor.py snapshot|observe` |
| 可维护性 | 大文件、大函数、模块索引 | `code_maintainability.py snapshot|plan|validate` |

维护面的共同原则：能读状态先读状态；能 dry-run 先 dry-run；能局部验证不跑全量验证。

### 14.1 可观测性公共字段

借鉴 OpenTelemetry 的稳定语义字段思想，各 owner 不需要共用一个数据库，但应输出可关联的最小字段：

| 字段 | 用途 |
| --- | --- |
| `schema` / `generated_at` | 解释数据版本和新鲜度 |
| `request_id` / `task_id` / `trace_id` | 跨层关联一次工作 |
| `system` / `owner` / `operation` | 说明谁负责什么动作 |
| `stage` / `status` / `attempt` | 说明生命周期和重试位置 |
| `duration_ms` / `timeout_ms` | 量化延迟与预算 |
| `error_class` / `reason` | 区分瞬时、策略、权限、参数和终态失败 |
| `artifact_path` / `receipt_path` | 指向结果和可审计证据 |
| `source` / `host` / `route_mode` | 解释外部资源和网络路径 |

快照必须带 `generated_at`、owner schema 和过期阈值；过期快照只能作为历史证据，不能继续支撑“当前健康”的结论。

## 15. 安全与权限模型

本机系统的安全边界不是单点，而是叠加模型：

| 位置 | 边界 |
| --- | --- |
| AGENTS | 全局安全、备份、授权、工具使用规则 |
| bridge permission table | 手机账号权限、风险等级、令牌 |
| capability tokens | 限时/限次/限范围扩展能力 |
| Hub gateway | 同权限 fallback，不绕过 owner |
| resource layer | 分类风险，不擅自安装/下载高风险资源 |
| memory policy | 不存秘密，不把一次性内容写长期记忆 |
| backup router | 修改前可回滚 |
| doctor/repair-plan | 大范围修复必须先计划 |

最重要的一条：授权必须有作用域。主任务授权不自动授权临时笔记派生任务；令牌授权不等于管理员；Hub fallback 不等于提高权限。

## 16. 已知薄弱点与治理路线

| 优先级 | 薄弱点 | 可见影响 | 推荐治理与完成条件 |
| --- | --- | --- | --- |
| P0 | 资源层无 URL 通用研究 executor 覆盖不全 | 某些请求进入 `handoff_required`，容易诱发绕过资源所有权 | 补齐 source executor adapter；同 request 附回并消费；资源层失败前 Codex 不重复检索 |
| P0 | MCP current-turn 可调用性动态 | 配置健康、协议健康和本 turn 可调用容易混淆 | capability route 提供起点；失败只向后；记录 `call_completed` 证据 |
| P0 | Provider catalog 与 reasoning catalog 刷新曾不同步 | 模型可见但推理档位缺失，或会话中目录回退 | provider 指纹触发双目录刷新；重载与重启结果一致；禁止单 gate 假设 |
| P1 | Mihomo TCP controller 为 pipe-only 降级 | 网关不能通过 TCP 控制口获取完整节点状态 | 明确 pipe adapter 或启用受控 TCP endpoint；不改系统全局代理 |
| P1 | 微信桥接核心文件仍大 | 故障影响面大、回归成本高 | 按 owner 边界继续抽取，稳定 facade，回归矩阵覆盖消息/附件/权限/回发 |
| P1 | 42 个重构候选 | 维护成本和误改概率上升 | module-context + placement-plan 驱动小批次治理，候选下降且 validator 不退化 |
| P1 | 记录量持续增长 | broad scan、备份和索引刷新成本增加 | 高频查询 SQLite 化；冷数据归档；增量索引有新鲜度指标 |
| P2 | Hub/MCP callability 未逐 profile 实测 | snapshot 只能给保守结论 | 按实际任务惰性验证，不为白皮书启动全部 profile |
| P2 | 草案与审批历史语义曾混淆 | 已处理卡片重复出现 | lifecycle 元数据成为唯一状态；草案不进 review queue；处置后去重 |

治理顺序遵循 SRE 式有限预算：先消除会阻断任务或造成状态错误的 P0，再治理成本和可维护性 P1，最后处理体验与覆盖率 P2。每项必须有 owner、指标、失败边界和验收证据，不能只写“继续优化”。

## 17. 推荐读法

如果你想知道“系统整体怎么运作”，读第 3、4、5 节。

如果你想维护工具稳定性，读第 5、14、16 节。

如果你想治理微信桥接，读第 9、13、14、16 节。

如果你想理解记忆、技能、临时笔记、外部知识，读第 7、8 节。

如果你想修改代码，先看第 13 节，然后用：

```powershell
python _bridge\code_maintainability.py module-context --message "<task>"
python _bridge\code_maintainability.py lookup-module --terms "<terms>"
```

如果你想查系统当前健康状况，优先运行最小入口：

```powershell
python _bridge\codex_workflow_entry.py preflight --message "<task>"
python _bridge\mcp_session_doctor.py validate
python _bridge\local_mcp_hub.py validate
python _bridge\memory_governance.py snapshot
python _bridge\resource_process_doctor.py metrics
python _bridge\shared\record_store_maintenance.py metrics
```

## 18. 结论

当前本机框架的方向是正确的：规则给边界，记忆给连续性，技能给方法，模板给流程，MCP/Hub/CLI/SQLite/GUI 给能力，资源层给材料化，维护面给可治理性，收口层给持续改进。

下一阶段的关键不是再添加更多治理工具，而是让现有治理工具更好协同：

1. 继续缩小桥接核心 CLI 和维护诊断大函数。
2. 让资源层、SQLite 索引、记录库和外部知识形成闭环。
3. 让 closeout package 成为每次非简单任务的统一收口入口。
4. 让每个 MCP profile 按 affinity 选择起点，并在失败后只沿统一链路向后继续；Hub-first 和 Native-first 都是 profile 属性，不是全局教条。
5. 让每次系统问题都能回到明确 owner：业务模块、工具层、资源层、记忆层、维护面，而不是散落在对话上下文里。

一句话概括：

> 这套系统的价值，不是让 Codex 拥有更多按钮，而是让 Codex 在正确的时间使用正确的能力，并且每次工作都能留下可验证、可回滚、可复用的改进。

## 19. 外部方法与本机适配

| 来源 | 本机使用的知识 | 明确不照搬的部分 |
| --- | --- | --- |
| C4 Model | 用上下文、容器、组件视角分层解释架构 | 不为每个脚本绘制低价值代码图 |
| arc42 | 目标、约束、构建块、运行时、质量、风险、技术债分章 | 不创建第二套架构权威源 |
| Google SRE Error Budget | 有界重试、退避、jitter、可量化 degraded state | 不把本机个人工作环境改造成大型 SRE 流程 |
| Backstage Software Catalog | owner、系统、组件、能力和生命周期元数据 | 不引入新的中央平台复制现有 owner 状态 |
| Backstage TechDocs | docs-as-code、目录可发现性、随代码维护 | 白皮书不自动覆盖实时 owner 数据 |
| OpenTelemetry Semantic Conventions | 稳定关联字段和跨层可观测语义 | 不强制所有模块迁入一个遥测后端 |

参考入口：C4 `https://c4model.com/diagrams`；arc42 `https://arc42.org/overview`；Google SRE `https://sre.google/workbook/error-budget-policy/`；Backstage Catalog/TechDocs `https://backstage.io/docs/features/software-catalog/`、`https://backstage.io/docs/features/techdocs/`；OpenTelemetry `https://opentelemetry.io/docs/concepts/semantic-conventions/`。

## 20. 术语表

| 术语 | 含义 |
| --- | --- |
| owner | 对某类业务状态或工具能力负最终责任的模块/工具 |
| facade | 提供稳定入口、把标准操作翻译给 owner，但不复制业务状态 |
| profile affinity | 某 MCP profile 的默认起始调用阶段，例如 Hub-first 或 Native-first |
| current-turn callability | 某工具在当前对话轮次是否真实可调用；不同于配置存在或协议健康 |
| resource ownership | 资源请求从提交到终态回执和消费期间由资源层持续持有 |
| handoff | owner 需要由 Codex/Hub 调用，但结果仍附回原 request，不释放需求 |
| receipt | 一次动作的结构化结果、状态、路径、错误和验证证据 |
| maintenance surface | snapshot、doctor、plan、validate、metrics 等长期治理入口 |
| draft artifact | 留存但未进入执行或审批流程的草案资产 |
| review card | 具有具体内容、来源和处置动作的待审批项 |
| degraded | 功能部分可用且原因明确，不等于成功或彻底失败 |
