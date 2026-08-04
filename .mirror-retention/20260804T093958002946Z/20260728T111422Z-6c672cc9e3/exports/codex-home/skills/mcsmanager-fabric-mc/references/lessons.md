# 脚本开发经验教训（时间倒序追加）

> 新条目追加到对应分类的顶部，格式：`[日期] 关键词 — 一句话描述`
> 超过 10 条的分类，旧条目归档到每个分类末尾的 `<details>` 折叠区。

## 快速索引
- ClientModLoader / client-mods：先看 `ClientModLoader 工作准则`，再看 `Minecraft 启动链路与排错准则`
- 启动脚本 / Java 启动 / UUID：先看 `客户端启动` 与 `客户端启动经验`
- AutoModpack 配置：先看 `配置管理`，涉及删除前再看 `幽灵配置检测`
- GUI / HMCL 操作：先看 `GUI 自动化（HMCL / Java Swing）` 与 `GUI 自动化失败总结`
- 服务器命令 / 假人：先看 `Carpet 假人` 与 `RCON 协议`
- PowerShell / Windows 脚本异常：先看全局 `windows-codex-ops`；本文件只保留 Minecraft/MCSManager 特有补充

## 最新（最近 5 条）
- [2026-06-17] 确认 — launch-mc.ps1 在 Windows PowerShell 5 下的首层故障根因是 PowerShell 7 专属 `??` 语法；修复后脚本可继续执行，显式传入 -minecraftDir 时可实际拉起 clone 实例
- [2026-06-17] 确认 — ClientModLoader 工程化重建产物已在 clone 实例运行验证通过；判断新 build 是否可用，必须看运行时日志而不能只看构建成功
- [2026-06-17] 确认 — ClientModLoader 精炼版已在 clone 成功验证，再提升到主实例前必须先备份并受控发布
- [2026-06-17] 教训 — 配置镜像不能只看时间戳；要同时比对日志和目标文件内容，才能确认真的落地
- [2026-06-16] 确认 — 保守幽灵检测在真实实例零误删零误报，方案验证通过
- [2026-06-16] 修复 — 误删 100 项配置：根因是 knownPatterns 只用增量 MOD，已改为全量扫描

---


## Carpet 假人
- [2026-06-16] 教训 — Carpet 假人不能用 kick 命令移除：kick 只是断开连接，Carpet 检测到假人状态丢失后会自动重新生成。正确方式是 `/player <name> kill`，从 Carpet 层面销毁假人实体
- [2026-06-16] 教训 — kick 假人后显示 "Kicked by an operator" 不代表移除成功，假人会被 Carpet 自动重新生成。验证移除：假人 kill 后从 /list 消失即为移除成功
## PowerShell / Windows 脚本
- 通用规则已迁移到全局 `windows-codex-ops`，包括 PowerShell 5.1 兼容、here-string、robocopy、环境变量、编码、进程和 MCP stdio。
- 本项目补充：`launch-mc.ps1` 必须兼容 Windows PowerShell 5.1；不要使用 PowerShell 7 专属语法。
- 本项目补充：含中文 `fabric.mod.json` 必须 `Get-Content -Raw -Encoding UTF8 | ConvertFrom-Json`，否则可能解析失败。

## 幽灵配置检测（血的教训）
- knownPatterns 必须来自全量 MOD（mods/ + client-mods/ 所有 JAR），不能只用本次新增处理的 MOD。曾因只用 `$allProcessed` 导致 73 个 MOD 中只覆盖 51 个，误删 100 项。
- 幽灵检测必须是保守模式 — 只报告不删除。关键词拆分匹配天然存在误匹配风险，删了无法恢复。
- 缺省配置只能通过启动服务器让 MOD 重新生成（Minecraft MOD 默认配置是运行时写入的）。
- 关键词匹配需要黑名单（config/server/client/fabric/common/library/loader/script）+ 子片段最小长度 5 字符。
- 全局三目录扫描：config/、client-config/、host-modpack/config/，任意目录中无 MOD 匹配的项标记为可疑。

## 配置管理

- [2026-06-16] 根因确认 — AutoModpack 4.0.5 generateContent() 用 String.contains("/config/") 判定文件类型。"/client-config/worldedit/worldedit.properties" 不包含字符串 "/config/"（因为 "client-" 前缀），落入 default type="other" 分支。方法：ModpackContent.generateContent() L313-328（反编译证实）
- [2026-06-16] 确认 — forceCopyFilesToStandardLocation 在 generateContent() 中被检查（FORCE_COPY_FILES_TO_STANDARD_LOCATION.hasMatch），传入 ModpackContentItem.forceCopy 字段。但未实际测试是否对 type=other 生效（待验证）

- [2026-06-16] 确认 — 客户端 automodpack-server.json 的 syncedFiles 必须与服务端一致，否则 /client-config/** 等条目不会被同步，导致 AutoModpack 下载失败断开连接。allowEditsInFiles 也应一致。
- [2026-06-16] 教训 — client-config 目录差异：服务端有 carpet/carpetgui/carpetorgaddition/fzzy_config/jei/trade_cycling 但客户端 modpack 中缺失，需手动同步。
- 修改文件前必须询问用户并创建带时间戳备份。每次脚本运行前做快照（记录目录列表 + 备份 automodpack-server.json）。
- automodpack-server.json 更新时只覆盖 syncedFiles 和 allowEditsInFiles，必须保留其他用户自定义字段。
- 配置还原准确做法：从 config/ 复制到 client-config/（内容相同）+ 空目录占位（让 MOD 下次加载时自动填充）。
- JSON 解析必须 `Get-Content -Raw -Encoding UTF8`，否则含中文描述的 fabric.mod.json 会解析失败。

## 验证流程
隔离测试环境 → 真实实例快照备份 → 运行脚本 → 对比快照确认零误删 → 启动服务器确认服务端未被破坏。

## 文件分类策略（来自 file-organizer 方法论）
- 按类型分: JAR(MOD)/JSON(配置)/TOML(配置)/CFG(配置)/ZIP(资源包/光影包)/PNG(纹理)
- 按用途分: 服务端/客户端/双端/未知 — MOD 按 fabric.mod.json 中 environment 字段判定
- 按日期分: 新安装 MOD/最近更新/长期未变动 — 用文件修改时间辅助判定
- 去重规则: 同名MOD取最新版本; 同名配置取内容最长(覆盖更完整); 去重前必须快照备份
- 执行原则: 所有移动/复制/删除操作前展示完整计划，经用户确认后批量执行

## Issue 分类状态机（来自 triage 方法论）
- 两轴分类: 类型(bug/enhancement) + 状态(needs-triage/needs-info/ready/wontfix)
- Bug 修复前必须尝试复现: 成功了写 agent brief，失败了标 needs-info
- 上下文复用: 读已有 triage notes，不重复提问已解决的问题

## GUI 自动化（HMCL / Java Swing）
- computer-use `sky.press_key()` 能操作 Java Swing 窗口（底层 SendInput API），但 Codex 重启后客户端关闭，必须重新跑 `setupComputerUseRuntime` 重建连接。
- `WScript.Shell.SendKeys()` 对 Java 窗口不可靠（依赖 COM 焦点代理，Java 窗口拿不到真正焦点）。
- HMCL `--launch` CLI 在已运行 GUI 实例时走 IPC，不会启动新进程。必须作为唯一进程使用。
- HMCL GUI 键盘序列:`ESC` 清搜索 → 输入版本名 → `Enter` 选中 → 等待焦点转移到启动按钮 → `Enter` 启动。
- 存档升级对话框不是"无法自动化"：`Tab` 聚焦"是"按钮 → `Enter` 即可。

## 客户端启动
- [2026-06-16] 通用化 — launch-mc.ps1：参数化 instanceDir/ram/username/server/saveName，存档名去括号+重名检测（追加_1_2），UUID 从 usercache.json 自动匹配。三种模式（单人/多人/菜单），classpath 复用 cp-<version>.txt
- Java 直接启动从 `3c3u.json` 解析 classpath + javaw 调用，绕过 HMCL GUI。离线模式 `--accessToken 0` 对单人游戏有效。
- 真实 UUID 从 `usercache.json` 读取（HMCL 实例目录下）。假 UUID 会导致存档中产生新玩家实体，物品/成就与旧 UUID 分离。
- 先证实再解释：不要在没有检查存档文件的情况下断言行为原因。

## Skill 架构
- 从不提供幻觉知识：技术上不确定的答案必须用文件系统证据（如存档 playerdata 里的 UUID 文件）验证后才给出。
- 优先级：可验证的证据 > 合理推断 > 猜测。没有证据就不能做断言。
- 新技能必须合并入已有体系而非独立留存；合并时提取核心方法论（≤6行），删除重复或无关内容。
- 技能触发基于系统提示中的 name+description 匹配，不是基于完整文件的加载。精简 description 影响触发精度，不能过度截短。

## GUI 自动化失败总结（2026-06-16 血的教训）
- 固定坐标点击不可靠：Minecraft 窗口大小在 1536x864 和 1920x1080 间变化，768,293 等硬编码坐标在窗口缩放后完全失效。
- `get_window_state` 不返回 width/height 时不应 fallback 到固定值，应通过 Win32 `GetWindowRect` 获取实际窗口尺寸再计算比例。
- "user input detected" 是 computer-use 的正常状态报告而非错误，只需调用 `get_window_state` 刷新即可继续，之前误判为"反自动化机制"。
- 窗口最小化不是 Minecraft 自身行为——应先问用户，不武断归咎于系统。
- 应该先读文档再行动：`SendInput` API 文档、Fabric wiki launch args、GLFW 窗口类名等基础知识应提前了解。
- 核心教训：遇到失败时应该停下来联网搜索相关知识，而不是在同一错误路径上反复重试。

## GUI 自动化实战教训
- 固定坐标不可靠：MC窗口大小在1536x864和1920x1080间变化，必须用GetWindowRect获取实时尺寸后计算比例
- "user input detected"是computer-use正常状态报告，不是反自动化机制，只需get_window_state刷新
- window is minimized应先问用户是否误触，不武断归咎系统
- SetCursorPos+mouse_event物理点击是GLFW窗口唯一可靠方案，sky.click底层SendInput对GLFW不兼容
- 同一个错误路径反复重试是浪费，遇到失败应联网搜索相关知识或换方案

## 客户端启动经验
- --quickPlaySingleplayer 对含括号存档名做前缀匹配（行为可复现，推测 startsWith 实现但未验证源码），"新的世界 (2)"匹配到"新的世界"
- 解决方案：临时重命名存档去括号→启动后改回，零副作用
- 存档UUID校验：players目录下以真实UUID命名的.dat/.json文件标识玩家数据归属
- 假UUID会导致存档分裂（新UUID产生独立数据），必须从usercache.json读取
- 精炼 helper mod 的晋升路径：先在 clone 验证 `client-mods` 加载与配置镜像，再备份主实例后替换；主实例失败即回撤
- clone 实例的 `version.json` 中 `libraries[*].downloads.artifact.path` 可能是相对路径，不能直接据此反推 `.minecraft` 根目录
- 对 clone 启动脚本而言，`cp-$versionJson.id.txt` 比从实例目录向上推导更稳定；`cp-$versionName.txt` 失效时应把它作为默认回退源

## Minecraft 启动链路与排错准则
- Fabric Loader 0.19.3 客户端入口链路：`KnotClient.main()` -> `Knot.launch(args, CLIENT)` -> `Knot.init(args)` -> `FabricLoaderImpl.load()` -> `freeze()` -> `loadClassTweakers()` -> `FabricMixinBootstrap.init()` -> `invokeEntrypoints("preLaunch")` -> `provider.launch()`
- `preLaunch` 位于 `load()` 和 `freeze()` 之后；普通 preLaunch 阶段新增完整 mod 已经太晚，容易导致 mixin、entrypoint、access widener 或配置链不完整
- 脚本启动不是另一套运行机制，最终仍是调用 `javaw.exe` 和 `net.fabricmc.loader.impl.launch.knot.KnotClient`；差异主要在参数装配、路径推断、UUID 解析、classpath 选择和 quickPlay 处理
- 排查“脚本影响启动结果”时先用 `launch-mc.ps1 -dryRun` 对比最终 `minecraftDir`、`classpathSource`、`username`、`uuid`、`gameDir`、`assetsDir`，不要直接猜测脚本有副作用
- 判断启动是否成功不能只看窗口；优先证据顺序：`-dryRun` 参数 -> `logs/latest.log` 的 `Loading X mods`/目标 mod/clientmodloader 日志 -> `crash-reports` 的首个 `Caused by` -> Java 进程和窗口状态
- 3c3u 已验证：`clientmodloader` 不依赖脚本本身，直接 Java 启动主实例时仍可生效；脚本的价值是复用正确启动参数并减少人工拼错

## ClientModLoader 工作准则
- 目标不是“让 jar 存在”，而是让它进入 Fabric 正式加载链并在运行时可验证
- 任何 helper mod 先在 clone 验证，再备份主实例受控晋升
- 版本归档要同时保留源码、JAR、日志和哈希，方便对比成功与失败差异
- 只要涉及 `client-mods`、AutoModpack、classpath 或 UUID，优先查日志和实测，不凭名称或路径猜测
- 发现成功版本后要冻结为原型，再在新版本号上迭代，不直接覆盖

## Agent行为准则
- GUI路径不可靠时（如GLFW窗口SendInput不兼容），切换非GUI方案而非反复重试
- 在不确定时必须先探索证实再断言，不加猜测性前缀（如"这可能是…"）
- 修改文件前备份+用户确认（AGENTS.md规则），不绕过
- 每次任务结束后评估是否产生可复用经验→更新lessons.md
- 主实例与 clone 的结论必须分开写；clone 成功不自动等价于主实例成功，提升前要重新验证


## Token 优化准则
- 验证操作：检查一个关键证据（存档LastWriteTime/进程ID）即可，不逐秒轮询日志尾部
- 重复代码：启动客户端的JVM参数/classpath只生成一次脚本并复用，不在每次调用时内联重建
- 失败处理：只报告"未成功"和一个简明的下一步（非GUI方案/联网搜索/询问用户），不加分析解释
- 衔接语：每turn中只在一个关键动作前加一句简练说明，不在每次_前都加_
- 操作前先判断：是否能一步到位（quickPlay+重命名），能就不分三步走（启动→等菜单→点按钮→选存→点进入）


## RCON 协议
- [2026-06-16] 教训 — MCSManager 服务端的 RCON 登录响应 type 字段返回 2（而非标准 2+auth）时不要重试登录，直接发命令即可，说明已处于已认证状态
- [2026-06-16] 教训 — RCON 命令的 length 字段不包含自身 4 字节：`length = 4(id) + 4(type) + len(command_bytes_with_nulls)`；与 Minecraft 标准 RCON 一致
- [2026-06-16] 确认 — Python socket 实现 RCON 比 PowerShell Invoke-Expression 更可靠，TCP 连接不会被中间代理中断


