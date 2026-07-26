# 已知问题库

## EBUSY 文件锁定错误
- **现象**: 实例运行中替换 MOD 时出现 "EBUSY: resource busy or locked" 错误
- **原因**: Windows 对正在使用的 JAR 文件加锁
- **解决**: 必须先停止实例，再替换 MOD JAR，然后启动实例
- **频率**: 每次运行时替换都会触发

## TPS 过载 / Can't keep up
- **现象**: 控制台输出 "Can't keep up! Is the server overloaded?"
- **原因**: TPS 低于 20，通常由大量实体/Carpet 规则/Chunky 预生成导致
- **解决**:
  1. 检查 Carpet 规则数量，关闭不必要的规则
  2. 停止 Chunky 预生成任务
  3. 降低视距 (server.properties view-distance)
  4. 增加内存分配

## H2 数据库崩溃
- **现象**: EasyAuth/Ledger 启动失败，H2 相关错误
- **原因**: H2 数据库文件损坏（非正常关闭导致）
- **解决**: 删除对应的 .mv.db 文件让 MOD 重新创建（会丢失数据）

## AutoModpack TLS 错误
- **现象**: "Internal TLS cannot be disabled" 警告
- **原因**: disableInternalTLS: true 但绑定在共享端口
- **影响**: 无实际影响，可忽略或改用独立端口

## Concerto 预设歌单不生效
- **现象**: 服务端有预设歌单文件但客户端看不到
- **原因**: AutoModpack syncedFiles 未包含 Concerto 路径，或客户端 automodpack 版本不兼容
- **解决**:
  1. 确保 automodpack-server.json syncedFiles 包含 /Concerto/preset_radios/**
  2. 客户端安装 AutoModpack MOD
  3. 重启服务端重新生成 content.json

## offline-mode 安全风险
- **现象**: online-mode=false 允许离线玩家加入
- **风险**: 任何人可用任意用户名登录，可能冒充管理员
- **缓解**: EasyAuth MOD 提供密码认证作为补偿

## fabric.mod.json 中文编码问题
- **现象**: PowerShell ConvertFrom-Json 解析某些 MOD 的 fabric.mod.json 失败
- **原因**: JSON 包含中文描述（如 carpet-org-addition）
- **解决**: Get-Content 必须加 -Encoding UTF8 参数
