# Concerto 音频系统

## 架构
Concerto 是一个 Minecraft Fabric 音乐 MOD，支持客户端本地播放和服务端预设歌单。

### 文件结构
```
Concerto/
  server_config.json       - 服务端音乐代理配置
  preset_radios/           - 服务器预设歌单（JSON）
    *.json                 - 每个歌单一个文件

audioplayer_uploads/       - 本地音频上传目录

客户端:
  Concerto/                - 客户端 Concerto 数据
  automodpack/modpacks/localhost-25565/Concerto/ - AutoModpack 同步数据
```

### server_config.json 关键配置
```json
{
  "enableMusicAgent": false,   // 服务端音乐代理开关
  "musicAgentVolume": 100,
  "enablePresetRadios": true,  // 服务器预设歌单
  "allowUpload": true
}
```

## 已知问题

### 无法播放音乐
- **原因**: server_config.json 中 enableMusicAgent 应设为 false（客户端本地播放），设为 true 会导致服务端尝试代理播放而客户端无对应音频文件
- **解决**: 设置 enableMusicAgent: false

### 预设歌单无法加载
- **原因**: 预设歌单 JSON 未放入 Concerto/preset_radios/ 目录，或 AutoModpack syncedFiles 未包含 Concerto 路径
- **解决**: 确保 syncedFiles 包含 /Concerto/preset_radios/** 或相应路径

### 服务端点歌/音乐房间无声音
- **原因**: 上传的音频文件过大或被限制
- **解决**: 检查 config/audioplayer/audioplayer-server.properties 中的文件大小限制

## 预设歌单格式
```json
{
  "name": "歌单名称",
  "songs": [
    {
      "name": "歌曲名",
      "url": "https://example.com/song.mp3",
      "type": "url"
    }
  ]
}
```
