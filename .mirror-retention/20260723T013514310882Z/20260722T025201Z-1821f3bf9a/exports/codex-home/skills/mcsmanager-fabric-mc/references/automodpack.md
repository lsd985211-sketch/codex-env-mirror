# AutoModpack 配置

## 当前 syncedFiles
```
!/kubejs/server_scripts/**
/mods/*.jar
/client-mods/*
/config/**
/client-config/**
/kubejs/**
/emotes/*
```

## 当前 allowEditsInFiles
```
/config/**, /client-config/**, /mods/*, /client-mods/*, /resourcepacks/*, /shaderpacks/*
```

## 关键配置
- autoExcludeServerSideMods: true（自动排除纯服务端 MOD）
- generateModpackOnStart: true
- requireAutoModpackOnClient: true

## 同步逻辑
1. 服务端启动 → 扫描 syncedFiles 目录 → 生成 host-modpack/automodpack-content.json
2. autoExcludeServerSideMods 移除 easyauth/ledger/LuckPerms/servux
3. 所有条目 editable: true → 客户端只补充不覆盖
4. 客户端从 automodpack/modpacks/localhost-25565/ 加载 MOD

## MOD 处理规则
- 双端 MOD (env=*): mods/ 保留 + 复制到 client-mods/
- 纯客户端 MOD (env=client): 从 mods/ 移入 client-mods/
- 纯服务端 MOD (env=server): 留在 mods/，autoExcludeServerSideMods 自动排除

## automodpack-server.json 修改规则
- 只覆盖 syncedFiles 和 allowEditsInFiles
- 必须保留 DO_NOT_CHANGE_IT、customField 等用户自定义字段
- 修改前备份
