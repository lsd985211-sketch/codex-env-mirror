---
name: fabric-mc-architecture
description: "Fabric loader architecture, Mixin, Fabric API, AutoModpack, ClientModLoader, and 3c3u Fabric integration analysis."
---

# Fabric 底层架构知识库（含 3c3u 整合包实例分析）

## 一、Fabric 三层架构总览

```
┌─────────────────────────────────────────────┐
│   L2: Fabric API        (游戏特定钩子)        │
│       事件系统 · 注册表 API · 网络 API        │
│       渲染 API · 世界生成 API · 权限 API      │
├─────────────────────────────────────────────┤
│   L1: Fabric Loader     (模组加载器核心)       │
│       Knot 类加载器 · Mixin 混入引擎           │
│       入口点系统 · 依赖解析 · 反混淆层          │
├─────────────────────────────────────────────┤
│   L0: Minecraft JAR     (原始游戏)            │
│       26.1+ 已提供官方映射 (unobfuscated)      │
└─────────────────────────────────────────────┘
```

---

## 二、Fabric Loader — 最底层核心

### 2.1 启动流程

```
HMCL / 官方启动器
  ↓ 指定 mainClass
KnotClient / KnotServer (Fabric 自定义类加载器)
  ↓
GameProvider 定位游戏 JAR
  ↓
Mixin 服务初始化 (Sponge Mixin 修改版)
  ↓
解析所有 fabric.mod.json → 构建依赖图
  ↓
Side Stripping (@Environment 注解处理)
  ↓
反混淆 (生产环境: Mojang 映射 -> Intermediary)
  ↓
加载 Entrypoints (preLaunch → main → client/server)
  ↓
游戏主循环开始
```

### 2.2 Knot 类加载器 — Fabric 的心脏

**Knot** 是 Fabric Loader 自带的类加载器，代替标准的 `URLClassLoader`：

| 特性 | 说明 |
|------|------|
| **类隔离** | MOD 类和 Minecraft 类用 Knot 加载，库类委托给系统类加载器 |
| **运行时类转换** | 加载类之前先应用 Mixin 转换 |
| **Side Stripping** | 移除 `@Environment(CLIENT/SERVER)` 标注的不匹配类/方法/字段 |
| **包访问破解** | 生产环境扁平包结构不需要；开发环境将 protected/package-private 转为 public |
| **缓存** | 反混淆后的 JAR 缓存到 `{gameDir}/.fabric/remappedJars/{mcVersion}/` |

**类加载隔离架构：**
```
KnotClassLoader (@Mod 类 + @Minecraft 类)
  └── 委托: 无法转换的库类 → 系统类加载器
```

### 2.3 fabric.mod.json — MOD 清单规范

**必填字段：** `schemaVersion: 1`, `id`（字母开头, 2-64字符）, `version`（语义化版本 2.0）

**关键字段详解：**

| 字段 | 说明 |
|------|------|
| `environment` | `*`(全环境) / `client` / `server` |
| `entrypoints` | `main`(ModInitializer), `client`(ClientModInitializer), `server`(DedicatedServerModInitializer), `preLaunch` |
| `mixins` | mixin 配置文件路径列表，可指定 `environment` |
| `accessWidener` | 类扩权文件（访问宽化/接口注入/枚举扩展） |
| `jars` | 嵌套 JAR（使用 `include` 依赖自动添加） |
| `depends`/`recommends`/`suggests`/`breaks`/`conflicts` | 依赖声明 |
| `provides` | 别名，声明本 MOD 替代其他 MOD 的 ID |
| `languageAdapters` | 语言适配器（如 `kotlin` → `KotlinAdapter`） |

**依赖版本范围语法：**
```
"minecraft": ">=26.1 <26.2"    // 范围
"fabric-api": "*"              // 任意版本
"java": ">=25"                 // Java 版本约束
```

### 2.4 入口点系统 (Entrypoints)

入口点是 Fabric Loader 初始化 MOD 的机制，类比 Java SPI：

| 入口点 | 接口 | 调用时机 |
|--------|------|----------|
| `preLaunch` | `PreLaunchEntryPoint` | 游戏启动前（慎用） |
| `main` | `ModInitializer.onInitialize()` | 客户端+服务端通用初始化 |
| `client` | `ClientModInitializer.onInitializeClient()` | 仅物理客户端，main 之后 |
| `server` | `DedicatedServerModInitializer.onInitializeServer()` | 仅专用服务端，main 之后 |

**加载顺序：** main 全部执行完 → client/server 全部执行完。同一列表内顺序按声明顺序，**无法跨 MOD 控制顺序**。

**代码引用类型：**
- 类引用：`"net.fabricmc.example.ExampleMod"`（需有无参构造+实现接口）
- 方法引用：`"net.fabricmc.example.ExampleMod::method"`
- 静态字段引用：`"net.fabricmc.example.ExampleMod::field"`

**入口点用于 MOD 间集成：**
```java
// 模组 A 调用模组 B 的入口点
FabricLoader.getInstance().getEntrypointContainers("my-api", MyApi.class)
```

---

## 三、Mixin 混入系统

Mixin 是 Fabric MOD **唯一官方支持的类转换方式**。

### 3.1 Mixin 架构

```
Mixin 配置文件 (mod.mixins.json)
  ↓
Mixin 服务 (Sponge Mixin 0.17.3, Fabric 修改版)
  ├── 解析 @Mixin 注解 → 目标类
  ├── 应用注入 (@Inject, @Overwrite, @Redirect, @ModifyArg...)
  ├── 实现接口注入 (@Implements)
  └── 访问器 (@Accessor, @Invoker)
```

### 3.2 注入点类型

| 注入点 | 用途 |
|--------|------|
| `@Inject(at = @At("HEAD"/"RETURN"/"INVOKE"), method = "...")` | 在方法头/返回/调用处注入代码 |
| `@Overwrite` | 完整替换方法体（不推荐，兼容性差） |
| `@Redirect` | 重定向方法调用到另一个方法 |
| `@ModifyArg`/`@ModifyVariable` | 修改参数/局部变量 |
| `@ModifyConstant` | 修改常量值 |
| `@Accessor` | 生成 getter/setter 访问私有字段 |
| `@Invoker` | 生成调用器访问私有方法 |

**Fabric Mixin 修改版增强：**
- 允许构造函数内的所有默认注入点
- 优化未使用的 callback info
- 修复向后兼容性
- 修复静态 shadow
- 允许接口中的注入器

### 3.3 Class Tweaker（类扩权）

Fabric Loader 0.18.0+ 引入的增强功能，替代旧的 Access Widener：

| 指令 | 功能 |
|------|------|
| `accessible` | 将私有成员改为可访问 |
| `extendable` | 将 final 类/方法改为可继承/可覆写 |
| `mutable` | 将 final 字段改为可变 |
| `inject-interface` | 为目标类添加接口实现 |
| `extend-enum` | 向枚举添加新常量 |
| `transitive-*` | 使扩权对依赖本 MOD 的其他 MOD 可见 |

---

## 四、Fabric API 体系

Fabric API 是一组模块化的钩子库，覆盖游戏各层面。**不是 Fabric Loader 的一部分**，需要单独安装。

### 4.1 核心模块

| 模块 | 功能 |
|------|------|
| `fabric-api-base` | 事件系统基础 |
| `fabric-resource-loader-v0` | 资源包加载 |
| `fabric-networking-api-v1` | 网络通信 |
| `fabric-registry-sync-v0` | 注册表同步 |
| `fabric-renderer-api-v1` | 渲染引擎 API |
| `fabric-lifecycle-events-v1` | 游戏生命周期事件 |
| `fabric-command-api-v2` | 命令 API |
| `fabric-events-interaction-v0` | 交互事件 |
| `fabric-object-builder-api-v1` | 方块/物品/实体构建 |

### 4.2 事件系统

Fabric API 的事件系统替代 Mixin 满足常见需求：

```java
// 注册事件监听
AttackBlockCallback.EVENT.register((player, level, hand, pos, direction) -> {
    // 自定义逻辑
    return InteractionResult.PASS;
});
```

**事件类型：**
- 数组回调 (`EventFactory.createArrayBacked`) — 按注册顺序迭代，直到非 PASS
- 环形回调 (`EventFactory.createLoop`) — 适合无返回值的广播

### 4.3 注册表系统

```java
// 26.1+ 的注册方式
ResourceKey<Item> itemKey = ResourceKey.create(Registries.ITEMS, 
    Identifier.of(MOD_ID, "my_item"));
Registry.register(BuiltInRegistries.ITEM, itemKey, new Item(settings));
```

---

## 五、映射 (Mappings) 体系

### 5.1 映射格式

26.1 是 Minecraft **首个未混淆版本**，Mojang 提供官方映射：

```
26.0 及以前:     混淆名 (obf) → Intermediary → Yarn/Mojmap
26.1+:          直接使用 Mojang 官方映射 (official mapping)
```

### 5.2 映射层

| 名称 | 说明 |
|------|------|
| **official** | Mojang 提供的官方映射名（如 `net.minecraft.world.item.Item`） |
| **intermediary** | Fabric 的中间映射，确保跨版本兼容（如 `net.minecraft.class_1792`） |
| **named/Yarn** | 社区提供的可读名（26.1+ 不再官方支持） |

### 5.3 运行时反混淆

```
生产环境:
  Minecraft JAR (混淆) → Intermediary 映射 → Knot 反混淆 → MOD 类

开发环境 (Loom):
  Minecraft JAR → Yarn/Official 映射 → IDE 可读源码
```

---

## 六、整合包架构模式 — 以 3c3u 为例

### 6.1 五层架构模型

```
┌────────────────────────────────────────┐
│  L4: 玩法层  生存辅助 · 红石/生电        │
│              · 社交/音乐 · 录像/地图      │
├────────────────────────────────────────┤
│  L3: 工具层  WE · Litematica · Tweakeroo│
│              · MiniHUD · JEI · Jade     │
├────────────────────────────────────────┤
│  L2: Carpet 生态层 (核心+12衍生模组)     │
│  L2: 辅助模组层 VoiceChat · Essential   │
├────────────────────────────────────────┤
│  L1: 性能/渲染层 Sodium · Iris · Lithium│
│              · C2ME · ModernFix · VMP   │
├────────────────────────────────────────┤
│  L0: 服务端核心层 Fabric API · EasyAuth │
│           · LuckPerms · Ledger          │
└────────────────────────────────────────┘
```

### 6.2 关键性能优化原则

| 优化目标 | 方案 | MOD 示例 |
|----------|------|----------|
| **CPU** | 密度函数编译为 JVM 字节码 | C2ME |
| **GPU** | 重写渲染管线，面剔除 | Sodium |
| **内存** | 方块状态去重，紧凑存储 | FerriteCore, ModernFix |
| **IO** | 异步区块读写，两级缓存 | C2ME |
| **网络** | 多 Netty 事件循环 | Krypton, VMP |

### 6.3 Carpet 生态架构

```
Carpet Core (fabric-carpet)
  ├── 规则扩展系列: TIS · AMS · Ayaka · Extra Extras · Lab · SDK · GuGu
  ├── 功能扩展系列: Org · Gugle · REMS · Cuo
  └── 工具系列: CarpetGUI · CarpetBotRelog
```

/player 假人系统是 Carpet 核心功能，配合多个衍生模组实现：
- 假人管理（白名单/黑名单/群组）
- 断线自动重连
- 行为扩展（控制器/备份）
- 图形化规则编辑

### 6.4 服务端安全架构

```
玩家进入
  ↓
EasyAuth (离线登录验证) ─── H2 数据库
  ↓
LuckPerms (细粒度权限) ─── SQLite/MySQL/H2
  ↓
Ledger (操作审计回滚) ─── H2 数据库
  ↓
Minecraft 原版 OP 系统
```

### 6.5 模组分发

AutoModpack 实现了服务端→客户端的自动同步：
- 服务端 `mods/` 目录只放必需的 13 个核心 MOD
- 通过 AutoModpack 分发 119 个客户端 MOD 和完整配置
- 连接时校验版本差异 → 自动下载缺失/更新的 MOD

---

## 七、常见诊断模式

### 7.1 启动崩溃诊断

```
1. 检查 Java 版本 (java -version) → 需 Java 25
2. 检查 Fabric Loader 版本 → 需 0.18.4+
3. 查看 logs/latest.log → Mixin 冲突 (Mixin apply failed)
4. 检查 fabric.mod.json → MOD ID 冲突
5. 临时移除一半 MOD → 二分法定位问题
```

### 7.2 模组冲突分类

| 冲突类型 | 症状 | 排查方法 |
|----------|------|----------|
| Mixin 冲突 | 启动时 `Mixin apply failed` | 查看 Mixin 目标的类名 |
| 注册表冲突 | 世界加载失败 | 检查 `Registry DUPLICATE` 日志 |
| 依赖缺失 | 启动时 `Missing dependency` | 检查 `depends` 声明 |
| 版本不匹配 | 运行时异常 | 检查所有 MOD 的 Minecraft 版本 |

### 7.3 性能诊断

| 症状 | 工具 | 排查方向 |
|------|------|----------|
| FPS 低 | Spark (`/spark profiler`), Sodium 调试 | GPU 瓶颈 / 渲染距离 |
| MSPT 高 (卡顿) | Spark (`/spark healthreport`), TPS 监测 | 实体数量 / 区块加载 / 红石机械 |
| 内存溢出 | FerriteCore 状态, JVM GC 日志 | 区块缓存 / 泄漏 |
| 网络延迟 | VMP 日志, Krypton | 区块同步 / 实体追踪 |

---

## 八、关键文件路径规范

```
游戏根目录/
├── mods/              # MOD JAR 放置目录
│   └── fabric-api-*.jar  # Fabric API 必须
├── config/            # 各 MOD 配置文件
├── shaderpacks/       # 光影包 .zip
├── resourcepacks/     # 资源包
├── saves/             # 世界存档
├── logs/
│   └── latest.log     # 最新启动日志（排错第一读）
├── crash-reports/     # 崩溃报告
├── .fabric/
│   └── remappedJars/  # 反混淆后的 JAR 缓存
├── essential/         # Essential MOD 数据
├── journeymap/        # JourneyMap 地图数据
├── automodpack/       # AutoModpack 分发包
└── options.txt        # 客户端设置
```

---

## 九、架构决策指南

### 9.1 选择 MOD 的原则

| 场景 | 推荐 |
|------|------|
| 需兼容性 | Fabric + Sodium + Iris |
| 大型工业模组 | NeoForge |
| 技术生存/红石 | Fabric + Carpet 生态 |
| 光影优先 | Fabric + Iris |
| 性能优先 | Fabric + C2ME + Lithium + Sodium |

### 9.2 整合包构建建议

1. **分层管理**：服务端核心 → 性能MOD → 功能MOD → 客户端美化，各层职责分离
2. **Carpet 规则**：使用 `carpet.conf` 统一管理，备份规则变更
3. **权限体系**：离线服必用 EasyAuth + LuckPerms
4. **审计回滚**：多人服必用 Ledger
5. **模组分发**：使用 AutoModpack 或 Modrinth 分发客户端 MOD
6. **配置管理**：服务端 `config/` 和 AutoModpack 分发的客户端配置版本一致

### 9.3 性能调优优先级

1. **Sodium** → 最直接的 FPS 提升（2-5倍）
2. **Lithium** → 服务器逻辑优化
3. **C2ME** → 区块系统重写（大世界探索必备）
4. **FerriteCore + ModernFix** → 内存占用降低
5. **VMP** → 实体追踪优化（多人服必备）
6. **Krypton** → 网络优化

---

> **数据来源：** Fabric 官方文档 (docs.fabricmc.net)、Fabric Wiki (wiki.fabricmc.net)、3c3u 整合包实例分析  
> **版本适用：** Minecraft 26.1.2, Fabric Loader 0.19.x, Java 25  
> **最后更新：** 2026年6月
