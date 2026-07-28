---
name: workspace-knowledge
description: "3c3u workspace knowledge base: identity, paths, mods, crashes, players, worlds, configs, Reasonix, Agent Bridge, and known issues. Keep updated during work."
---

# 3c3u 工作区知识库

> **维护者:** Reasonix | **创建:** 2026-06-17 | **原则:** 每次发现新信息立即更新

---

## 一、身份标识

| 项目 | 值 |
|------|-----|
| **整合包名** | 3c3u |
| **Minecraft** | 26.1.2 (release, 2026-04-09) |
| **Fabric Loader** | 0.19.3 |
| **Java** | 25 (BellSoft LibericaJDK-25, `C:\Program Files\BellSoft\LibericaJDK-25\bin\java.exe`) |
| **启动器** | HMCL 3.9.1 |
| **用户** | 刘圣铎 (lsd985211), UUID: `4495cc82-7e41-46eb-bbb4-cff255fb39d9`, OP 等级 4 |
| **语言** | zh_cn |
| **HMCL 配置** | maxMemory=3968MB, autoMemory=true, 854×480, 非全屏 |
| **入口类** | `net.fabricmc.loader.impl.launch.knot.KnotClient` |

---

## 二、目录结构速查

```
3c3u/
├── 3c3u.jar           # 原版 Minecraft jar (38MB, 非 fat jar)
├── 3c3u.json          # 版本清单 (含全部 libraries 声明)
├── mods/              # 13 个服务端核心 MOD
├── config/            # 106 个配置目录/文件
├── logs/              # latest.log (41KB) + 28 个压缩历史日志
├── crash-reports/     # 25 次启动崩溃报告
├── saves/             # 1 个世界 "新的世界 (2)"
├── automodpack/       # AutoModpack 分发的 119 个客户端 MOD + 配置
├── .fabric/           # Fabric 反混淆缓存 + processedMods (200+ jar)
├── .mixin.out/        # Mixin 审计: 仅 ClientLanguage.class
├── journeymap/        # 小地图数据 (主世界+下界切片)
├── Concerto/          # 音乐播放器 (7 首歌, kugou/netease 登录)
├── essential/         # Essential 社交 mod (54MB, 装扮缓存)
├── schematics/        # 空 — 无投影文件
├── resourcepacks/     # XK redstone display 26.0.1.zip (已激活)
├── shaderpacks/       # photon_v1.3b.zip (Iris 光影, 当前禁用)
├── screenshots/       # 1 张截图 (2026-06-13)
├── replay_recordings/ # 已清空 (曾 1.6GB, 100 个文件)
├── .reasonix/         # Reasonix 配置 + skills + agent-bridge-mcp
├── .codegraph/        # 代码智能索引
├── zmusic/            # zmusic 原生 DLL
└── natives-windows-x86_64/  # LWJGL 原生库
```

---

## 三、MOD 加载状态

### 3.1 服务端 mods/ (13 个, 全部加载)

| MOD | 版本 | 大小 |
|-----|------|------|
| fabric-api | 0.151.0+26.1.2 | 2.5MB |
| fabric-language-kotlin | 1.13.12+kotlin.2.4.0 | 8.1MB |
| LuckPerms | 5.5.54 | 1.6MB |
| EasyAuth | 3.4.3-SNAPSHOT.48 | 24.3MB |
| Ledger | 1.3.20 | 20.0MB |
| AutoModpack | 4.0.5 | 15.9MB |
| Servux | 0.10.2 | 854KB |
| cloth-config | 26.1.154 | 1.1MB |
| modmenu | 18.0.0-beta.1 | 855KB |
| yet_another_config_lib_v3 | 3.9.4 | 1.1MB |
| mod-loading-screen | 1.0.5 | 1.3MB |
| offers-hud | 2.4.1 | 61KB |
| moblocator | 4.0.0 | 99KB |

### 3.2 客户端 client-mods/ (119 个, ~82 个成功加载)

**加载失败 (~37 个):** MOD 的 Mixin 指向了 26.1.2 中不存在的方法。已知失败:
- LambDynamicLights → 缺 `dev.yumi.commons.event.EventManager`
- musicplayer/configured → Mixin 目标方法不存在
- AppleSkin/AudioPlayer → ClassNotFoundException

**成功加载 (~82 个):** 包括 Sodium, Iris, Lithium, C2ME, JEI, Jade, JourneyMap, Litematica, Tweakeroo, MiniHUD, Carpet 等

**最新成功启动日志:** `logs/latest.log` — 82 mods loaded, 35s 启动, ~30s 后手动关闭

---

## 四、崩溃分析

### 4.1 统计
- **总数:** 25 次
- **时间范围:** 2026-06-13 ~ 2026-06-16
- **类型:** 100% 启动时崩溃
- **最新:** 2026-06-16 21:53:19

### 4.2 根因分类
1. **Mixin 注入失败** — MOD Mixin 指向 26.1.2 中不存在的类/方法
2. **ClassNotFoundException** — LambDynamicLights 缺 yumi-commons
3. **重复注册表项** — automodpack:waiting_music 重复
4. **MOD 间冲突** — WorldEdit/spark/carpet-org 依赖缺失

### 4.3 解决方案
- 为缺失依赖的 MOD 添加 JAR（如 yumi-commons）
- 或移除不兼容的 MOD
- 或等待 MOD 作者更新支持 26.1.2

---

## 五、玩家数据

### 5.1 主玩家
- **lsd985211** (刘圣铎) — OP4, 离线模式, 最后登录 2026-06-16
- 死亡 1 次: 2026-06-09, 下界 (-38,55,101), 等级 172
- 背包: 满附魔下界合金套, 64 结构方块, 38 末影之眼

### 5.2 其他缓存用户
- PPP, Steve, MHF_Spider (假人/测试账号)

---

## 六、世界数据

### 6.1 世界: "新的世界 (2)"
- **局域网端口:** 12939
- **模式:** 生存, PVP 开启, 离线模式
- **预生成:** Chunky 1000×1000 方块范围
- **维度:** 主世界 + 下界 + 末地 (末地含外岛)
- **结构:** kong.nbt (293KB), pingdi.nbt, sw.nbt

### 6.2 施工笔记
- `notes/remote/localhost~25565/施工.txt`: "南：全 西：全"

---

## 七、关键配置

### 7.1 游戏设置
- 渲染距离: 6 | 模拟距离: 12 | FPS: 60 | 垂直同步: ON
- 画质: custom, AO: OFF, 粒子: 最少, 云: fast
- 资源包: XK redstone display 26.0.1.zip
- 光影: photon_v1.3b.zip (Iris, 当前关闭), 色彩空间: Display P3

### 7.2 性能优化
- Sodium: chunk_builder_threads=0, entity_culling=ON, fog_occlusion=ON
- C2ME: 默认配置, maxViewDistance=45, 启用扩展视距协议
- VMP: 异步区块加载/传送门/实体追踪全开
- FerriteCore: 所有优化全开
- ModernFix: 默认配置
- Lithium: 默认配置 (空文件)

### 7.3 辅助工具
- Litematica: easyPlaceMode=false, debugLogging=true, 2 线程
- Tweakeroo: freeCameraShowHands=false, handRestock=true
- WorldEdit: cheat-mode=false, max-brush-radius=6
- JEI: 标准配置, cheat mode 关闭
- Jade: overlay 透明度 0.7, dark 主题

### 7.4 社交/音乐
- Essential: Discord 集成开, 缩放关, 截图管理开
- VoiceChat: UDP 24454, 距离 48, 耳语 24, 降噪开
- Concerto: kugou+netease 已登录, 当前播放 "黑白"(方大同)

---

## 八、Reasonix 自身状态

### 8.1 配置 (reasonix.toml)
- 模型: deepseek-flash (主), deepseek-pro (子 agent)
- Providers: DeepSeek + MiMo
- 沙箱: bash=enforce, allow_write=[codex-skills-export, _bridge]
- MCP: agent-bridge v2 (SQLite, 10 tools), codegraph (enabled), time (enabled)
- LSP: enabled
- 权限模式: ask

### 8.2 技能清单
- fabric-mc-architecture (Fabric 底层知识)
- codex-cli (Codex 架构 + 本机实例)
- mcsmanager-fabric-mc (MCSManager 运维, 来自 Codex)
- fabric-mc-26-1-2 (Minecraft 26.1.2 知识, 来自 Codex)

### 8.3 Agent Bridge v2
- 数据库: `mcsmanager/_bridge/bridge.db` (SQLite WAL)
- 10 工具, 6 状态任务生命周期
- Reasonix 在线, Codex 离线

### 8.4 Mobile OpenClaw Weixin Bridge
- 队列数据库: `_bridge/mobile_openclaw_bridge/mobile_openclaw_bridge.db`
- 当前主方向: `codex-app-server`，用于后台投递、线程隔离和结果归属。
- CDP: 保留为 fallback、诊断和手动兜底，不作为默认主路线。
- 已验证能力: app-server 可创建 turn、读取 turn、hydrate Desktop 线程并 resume。
- 待稳定缺口: 数据库事件噪声、旧 reply backlog 审计、worker 日志轮转、
  route notLoaded 预热策略、CDP 诊断事件合并。
- UX 基线: 补充消息只提示已纳为补充信息，不触发正在投递或已投递回执；
  主投递回执按 task id 和语义阶段互斥去重。
- 稳定性计划: `_bridge/shared/checkpoints/mobile-openclaw-bridge/20260623-0657-stability-optimization-plan.md`

---

## 九、已知问题清单

| 问题 | 严重度 | 状态 |
|------|:--:|:--:|
| Mixin 兼容性导致 ~37 个 MOD 加载失败 | 高 | 待解决 |
| Concerto kugou cookie 含 token (已暴露) | 中 | 待脱敏 |
| AutoModpack 更新检查 Connection refused | 低 | 本地环境正常 |
| 部分 MOD 版本重复 (fabric-api 5 版本) | 低 | 清理缓存 |
| MCP Bridge 长消息偶发超时 | 中 | 已降级为短消息 |

---

## 十、更新日志

| 日期 | 变更 |
|------|------|
| 2026-06-17 | 创建知识库 |
| 2026-06-17 | 新增: 崩溃分析 (25次), 玩家数据, 世界数据, Reasonix 状态 |
| 2026-06-17 | 新增: Agent Bridge v2 配置, 已知问题清单 |
| 2026-06-17 | 清理: replay_recordings (释放 1.6GB) |
| 2026-06-22 | 新增: Mobile OpenClaw Weixin Bridge app-server/CDP 基线与补充消息 UX 规则 |
| 2026-06-23 | 新增: Codex 受控迭代层，入口 `_bridge/iteration_layer_review.py`，用于只读扫描、提案包生成和批准后的安全验证闭环 |
| 2026-06-23 | 新增: Mobile OpenClaw Bridge 稳定性优化计划，优先处理事件噪声/数据库体积、旧回发积压审计、worker 日志轮转和稳定性套件 |

---

## 十一、Codex 受控迭代层

用途：
- 处理工作经验、工具更新、用户纠正和框架优化建议。
- 将经验分类为技能、记忆、项目知识、工具注册表、CLI 自动化或忽略。
- 生成 `proposal_packages`，但不自动写入目标系统。

入口：

```powershell
python _bridge/iteration_layer_review.py --dry-run
python _bridge/iteration_layer_review.py --run-validation
```

当前阶段：
- Phase 1: 设计和路由规则已落地。
- Phase 2: 只读 review 命令已落地。
- Phase 3: 标准提案包已落地。
- Phase 4: 验证闭环已落地。
- Phase 5: 项目技能固化已落地。

安全边界：
- 修改本机文件前仍必须询问用户。
- 修改前必须创建 `_bridge/backups/` 标记备份。
- 任何技能、记忆、工具注册表、CLI、项目配置更新都必须先由用户确认。
- 迭代层自身也只能通过提案路径进化，不能降低确认要求。
- Codex 每次实质性工作后应自评是否需要迭代；需要时先询问用户，收到明确同意后才执行。
