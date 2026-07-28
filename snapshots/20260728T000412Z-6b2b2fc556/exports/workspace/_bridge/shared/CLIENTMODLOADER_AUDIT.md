# Fabric Loader 注入点审计 — ClientModLoader 方案

> **审计者:** Reasonix | **日期:** 2026-06-17  
> **被审方案:** ClientModLoader — 通过 `addCodeSource` + `addToClassPath` 动态注入 `client-mods/` 中的 MOD

---

## 一、Fabric Loader 0.19.3 MOD 加载全链路

```
Fabric Loader 启动
  │
  ├─ 1. MOD 发现 (ModDiscoverer)
  │    扫描 mods/ 目录 → 找到所有 fabric.mod.json → 生成 ModCandidate 列表
  │
  ├─ 2. MOD 解析 (ModResolver)
  │    依赖图构建 → 版本冲突检测 → 确定加载顺序
  │
  ├─ 3. MOD 容器创建 (ModContainer)
  │    每个 ModCandidate → 创建 ModContainer 实例
  │    → 注入 modMap (key=modId, value=ModContainer)
  │    → 注入 mods (有序列表)
  │
  ├─ 4. 入口点注册 (EntrypointStorage)
  │    freeze() 锁定注册表 → setupMods() 处理所有 entrypoints
  │
  ├─ 5. Mixin 配置加载
  │    解析每个 MOD 的 mixin.json → 注册到 Mixin 服务
  │
  └─ 6. 游戏初始化
       ModInitializer.onInitialize() 按序调用
```

---

## 二、注入点分析

### ⚠️ 注入点 1: addCodeSource

```java
FabricLauncherBase.getLauncher().addToClassPath(path)
```

**作用:** 将 JAR 加入 Knot 类加载器的搜索路径  
**时机要求:** 必须在 MOD 发现阶段（步骤1）**之前**调用  
**风险:** 
- 如果 MOD 发现已经完成，加入的 JAR 不会被扫描
- classpath 中添加但不创建 ModContainer → MOD 是"幽灵状态"（类可用但事件不触发）

### ⚠️ 注入点 2: ModContainer 注册

```java
// 必须同时做两件事：
FabricLoaderImpl.mods.add(modContainer);     // 有序列表
FabricLoaderImpl.modMap.put(id, modContainer); // ID 索引
```

**作用:** 让 Fabric Loader 知道这个 MOD 的存在  
**风险:**
- 只加 classpath 不加 ModContainer → MOD 的 `fabric.mod.json` 中的 `entrypoints`、`mixins`、`depends` 都不会被处理
- ModContainer 需要正确的 `ModOrigin`（指向 JAR 的 URL）

### ⚠️ 注入点 3: Entrypoint 注册

根据 Codex 的 AGENTS.md: **"永远不要手动注册 entrypoints——freeze()->setupMods() 自动处理"**

这需要确认:
- `freeze()` 是在所有 MOD 发现完成后调用的
- 如果在 `freeze()` **之后** 才添加 ModContainer → entrypoints 不会被处理
- 如果在 `freeze()` **之前** 添加 ModContainer → entrypoints 正常处理

### ⚠️ 注入点 4: Mixin 配置

```java
MixinConfig config = MixinConfig.create("mod.mixins.json", modContainer);
// 必须在 setupMods() 之前
```

**风险:**
- Mixin 配置文件需要在类转换开始前注册
- 如果 Mixin 配置在游戏循环开始后注册 → 已经被加载过的类不会被 mixin

---

## 三、ClientModLoader 的 AGENTS.md 约束

```
1. 源码: clientmodloader/src/main/java/pl/skidam/clientmodloader/ClientModLoader.java
2. 编译: javac --release 25 -cp fabric-loader-0.19.3.jar;log4j-api-2.25.2.jar
3. 部署: 3c3u/mods/clientmodloader-1.0.0.jar
4. client-mods源: 3c3u/automodpack/modpacks/localhost-25565/client-mods/
```

---

## 四、关键审计问题

### Q1: 注入时机

ClientModLoader 是在哪个阶段运行的？

- [ ] **方案 A:** Fabric Loader 本身的 PreLaunch entrypoint
  - ✅ 最早，在所有 MOD 发现之前
  - ❌ 需要 ClientModLoader 自己作为 MOD 先被加载（鸡生蛋问题）

- [ ] **方案 B:** Hack Fabric Loader 的 MOD 发现阶段
  - ✅ 完美时机：在 ModDiscoverer 扫描 mods/ 之后、freeze() 之前
  - ❌ 需要 patch FabricLoaderImpl（Mixin 注入）

- [ ] **方案 C:** Fabric Loader 初始化完成后
  - ❌ 太晚了——Mixin 已经处理完，entrypoints 已经 freeze

### Q2: client-mods 中的 MOD 是否有 fabric.mod.json？

客户端的 119 个 MOD 大部分有 `fabric.mod.json`（因为它们本来是正常 MOD）。但需要确认：
- 它们的 `environment` 字段是否正确设为 `"client"`
- 有没有 MOD 相互依赖而依赖的 MOD 不在 client-mods 中

### Q3: ModContainer 创建的完整性

`addToClassPath` 只是第一步。还需要:
```java
// 伪代码
ModContainer mod = ModContainerImpl.create(
    modJson,        // 从 fabric.mod.json 解析
    origin,          // ModOrigin 指向 JAR URL
    loader
);
FabricLoaderImpl.INSTANCE.addMod(mod);  // 注册到 modMap + mods 列表
```

### Q4: 嵌套 JAR 处理

部分 MOD（如 Essential 54MB）使用了嵌套 JAR（`jars` 字段）。这些嵌套 JAR 也需要被递归处理。

---

## 五、建议的验证方法

### 5.1 启动日志检查

在 `logs/latest.log` 中搜索:
```
[FabricLoader] Loading X mods:    ← 数字是否包含 client-mods 的 MOD
```

### 5.2 运行时检查

```java
// 验证 MOD 是否正确加载
FabricLoader.getInstance().isModLoaded("modid");
FabricLoader.getInstance().getAllMods().size();
```

### 5.3 功能验证

1. 检查 Sodium 是否生效（F3 调试界面 FPS 大幅提升）
2. 检查 JEI 是否出现物品列表
3. 检查 JourneyMap 是否显示小地图

---

## 六、潜在替代方案

如果 ClientModLoader 方案在注入时机上遇到困难，可以考虑:

### 方案 B: 修改 AutoModpack 服务端逻辑
使 AutoModpack 直接将 client-mods 的 MOD 复制到客户端的 `mods/` 目录，而不是单独的 `client-mods/` 目录

### 方案 C: 符号链接
Windows 支持目录符号链接:
```powershell
New-Item -ItemType Junction -Path "3c3u/mods/client-extras" -Target "3c3u/automodpack/.../client-mods"
```
但 Fabric Loader 可能不遍历子目录。

---

> **下一步:** 让 Codex 给出当前的 `ClientModLoader.java` 源码，我逐行审查注入时序是否正确。
