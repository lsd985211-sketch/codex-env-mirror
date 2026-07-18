## 2026-06-16
- [skill] computer-use 重启后需重建 sky 连接；Java/Swing 窗口用 sky.press_key 而非 SendKeys
- [cfg] 3c3u UUID=4495cc82-7e41-46eb-bbb4-cff255fb39d9, username=lsd985211
- [cfg] Java 25: C:\Program Files\BellSoft\LibericaJDK-25\bin\java.exe
- [cfg] Node/npm/npx: mindcraft-main\nodejs\
- [cfg] Git 2.54.0 已安装
- [decision] 新技能提取核心(≤6行)合并入已有体系
- [pref] 先证实再解释，断言必须有文件证据
- [pref] GUI 交互可自动化，不过早放弃
- [cfg] --quickPlaySingleplayer 对括号存档名做前缀匹配 bug，临时重命名可绕过
- [cfg] SetCursorPos+mouse_event 是 GLFW 窗口可靠点击方案
- [pref] GUI不可靠时优先非GUI（GLFW/click失败→换方案）
- [pref] 先证实再断言，不加推测性前缀
- [decision] 任务结束评估经验→更新lessons.md
- [cfg] VectorMemoryCodex/Reasonix 已通过重启后验收：NSSM 自动启动，HTTP health、MCP tools、search/write/delete 均正常
- [cfg] bridge 向 Reasonix 发送协作消息时优先使用 ASCII，PowerShell 管道可能把中文标题/正文写成问号
- [decision] Reasonix 只接收已验证的协作边界、风险与证据，不混入 Codex 运维过程细节
- [cfg] memory-graph 可直接通过 `graph_add_observation` 写入 verified 观察，目标常用 `system:vector-memory`
- [cfg] post-reboot 验收通过后，向量记忆中的临时测试项要立即删除，避免污染最近记忆
- [cfg] 公共向量记忆池 VectorMemoryShared: http://127.0.0.1:15725, store=_bridge/vector_memory/shared, 用于 Codex/Reasonix 共享稳定事实/知识/技能
- [cfg] 向量记忆访问矩阵: Codex 私库 RW=15723, Reasonix 私库 RW=15724, 公共 RW=15725, Codex 读 Reasonix RO=15726, Reasonix 读 Codex RO=15727
- [cfg] Codex 已注册 vector-memory-codex/vector-memory-reasonix-ro/vector-memory-shared；Reasonix 已注册 vector-memory-codex-ro/vector-memory-shared

## 2026-06-20
- [skill] Playwright 已安装到 Codex 捆绑 Python；网页测试优先用系统 Edge/Chrome executable_path，避免慢速下载 bundled Chromium。
- [cfg] mobile_openclaw_bridge 外置只读面板入口：http://127.0.0.1:18808/，脚本 _bridge/mobile_openclaw_bridge/mobile_dashboard.py，冒烟测试 dashboard_smoke.py。
- [pref] 网页/面板 UI 优先遵循原生静态页规范：语义 HTML、CSS 变量、原生轻量 JS、浅层 DOM、清晰模块注释、少依赖，方便 Codex 后续重构。
- [cfg] mobile_openclaw_bridge dashboard V2 为 Codex 风格三栏只读对话面板，仍不修改 worker/API/回发链路。
- [skill] 前端轮询页应区分 summary state 与 selected detail cache；自动刷新不能清空用户当前查看的详情/事件。
## 2026-06-23
- [skill] openai-docs local skill path missing in this workspace; use official OpenAI docs or project knowledge as fallback for Codex research.
- [proj] Codex analysis should treat layered memory as filesystem/project KB/checkpoints plus ad-hoc notes, not only chat context.
- [cfg] Controlled iteration layer report now exposes proposal_groups and recommended_next_actions; treat them as review aids, not authorization.
- [cfg] Bridge supplement payloads must be validated against durable task status and active owner state before Codex consumes them.
- [skill] Windows toolchain gaps were resolved by adding jq, sqlite3, pnpm, and 7z visibility via WinGet Links / node cache shims.
- [decision] Use official OpenAI docs first for Codex research, and record durable findings in private memory plus project checkpoints.
