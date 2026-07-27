---
name: self-improvement
description: "记录经验教训、错误和用户偏好以实现持续改进。触发条件：(1) 操作失败 (2) 用户纠正 (3) 发现知识过时 (4) 发现更好方法 (5) 安装/修改技能 (6) 用户表达偏好。每次任务开始前回顾已有学习记录。"
---

# Self-Improvement + Memory

## 跨会话记忆
持久文件: `C:\Users\45543\.codex\MEMORY.md`

**会话开始第一步**: 读取 MEMORY.md。如不存在，创建空文件。

**自动追加时机**:
- 用户陈述偏好/习惯
- 项目结构重大变化
- 用户对行为的反馈
- 影响后续工作的决策
- 技能被安装或修改
- 重要文件路径/配置确定

**格式**: `## YYYY-MM-DD` 下 `- [category] 事实`
分类: `pref`, `proj`, `skill`, `cfg`, `decision`
规则: 只追加不删除，一行一事实，最多20条，超出归档到 MEMORY_archive.md

## Learnings 记录
初始化 `.learnings/`（项目根目录，幂等操作）:
- `LEARNINGS.md` - 经验教训
- `ERRORS.md` - 失败记录
- `FEATURE_REQUESTS.md` - 用户请求

| 场景 | 操作 |
|------|------|
| 命令失败 | `.learnings/ERRORS.md` |
| 用户纠正 | `.learnings/LEARNINGS.md` (correction) |
| 知识过时 | `.learnings/LEARNINGS.md` (knowledge_gap) |
| 更好方法 | `.learnings/LEARNINGS.md` (best_practice) |

不记录密钥/token/完整源码。

## 项目技能 Self-Learning 联动
当项目技能定义了 Self-Learning Protocol，遵循其标准。项目级 lessons.md 优先于全局 MEMORY.md。
