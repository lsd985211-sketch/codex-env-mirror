# 实例概况

## 基本信息
- MCSManager v10.16.2，面板 http://127.0.0.1:23333/
- 实例 UUID: 178ab7fc73354fe684b15e2ac9c173a0，昵称 "lsd"
- 登录: 刘圣铎 / Aliushengduo985
- Fabric 26.1.2，Java: C:\Program Files\BellSoft\LibericaJDK-25\bin\java.exe
- 启动命令: java -Xms4G -Xmx6G -jar fabric-server-mc.26.1.2-loader.0.19.3-launcher.1.1.1.jar nogui
- online-mode: false
- 客户端: C:\Users\45543\Downloads\HMCL-3.9.1\.minecraft\versions\3c3u\
- 当前 helper mod: clientmodloader-1.0.0.jar，主实例已验证可接管 AutoModpack 的 client-mods 加载链
- 受控升级原则: 主实例替换前先备份，clone 验证通过后再提升；失败立即回撤备份

## 目录结构
```
daemon/data/InstanceData/178ab7fc73354fe684b15e2ac9c173a0/
  mods/                    - 服务端 MOD (73 JAR)
  client-mods/             - 客户端 MOD (120 JAR，含双端副本)
  config/                  - 服务端配置 (15 目录)
  client-config/           - 纯客户端配置 (26 目录)
  automodpack/             - AutoModpack 同步数据
  Concerto/                - 音乐 MOD
  world/                   - 世界存档
  logs/                    - 实例日志
  backup/                  - MCSManager 备份
```

## 运维操作
- 日志: daemon/logs/current.log，实例日志在 InstanceLog/，客户端日志在 versions/3c3u/logs/
- 关键词: ERROR, WARN, EBUSY, ENOENT, overloaded, Can't keep up
- MOD 替换: 必须先停止实例再替换，避免 EBUSY
- MCSManager 面板需同时启动 daemon + web (start-daemon.bat)
