---
name: skill-logger
description: |
  记录一次 Skill 使用情况到日志文件。每次用完某个 Skill 后调用，追加一条使用记录。
  触发词："记录这次使用"、"记录用了 xxx"、"log skill"、"记录一下"（在使用完某个 Skill 之后）。
  用于追踪 Skill 使用频率和满意度，为 skill-analyzer 提供数据。
metadata: {"codex":{"required_env":["OBSIDIAN_ROOT"],"compatibility":"Requires a user-selected Obsidian vault exposed through OBSIDIAN_ROOT."}}
---

# Skill 使用日志记录

每次使用完某个 Skill 后，调用本 Skill 记录一条使用日志。日志追加到 `06 计划/skill_usage_log.jsonl`。

## 日志文件位置

```
$OBSIDIAN_VAULT/06 计划/skill_usage_log.jsonl
```

`OBSIDIAN_VAULT` 环境变量需在 `.env` 中配置（见下方）。

## 使用方式

**方式一：用完 Skill 后手动触发**

```
记录这次使用：x-post，场景是把播客笔记改成即刻动态，满意度 4 分，结尾不够有力手动改了
```

**方式二：让 AI 引导填写**

直接说"记录一下"，AI 会依次询问：
1. 用了哪个 Skill？
2. 使用场景是什么？
3. 满意度（1-5 分）？
4. 备注（可选）？

## 工作流程

### Step 0：先检查可执行条件

在运行脚本前，先执行下面的检查命令：

```bash
printenv OBSIDIAN_VAULT
```

判定标准：
- 输出非空，且路径存在并可读取时，继续下一步
- 输出为空、命令无结果，或路径不可读时，先提示用户配置 `.env` 后再继续，不要直接执行脚本

然后检查目标目录和日志文件：

```bash
vault="$OBSIDIAN_VAULT"
[ -d "$vault/06 计划" ] && [ -r "$vault/06 计划" ]
[ -e "$vault/06 计划/skill_usage_log.jsonl" ] || [ -w "$vault/06 计划" ]
```

判定标准：
- 如果目录存在且可读，继续下一步
- 如果目录不存在，先确认 `OBSIDIAN_VAULT` 是否指向正确的 vault
- 如果目录存在但日志文件不存在，只要该目录可写就允许继续；如果目录不可写，先提示用户检查 vault 权限或路径

如果 `OBSIDIAN_VAULT` 缺失或 vault 路径不可用，先提示用户配置 `.env` 或确认 vault 路径后再继续，不要直接执行脚本。

### Step 1：收集信息

如果用户没有提供完整信息，逐项询问：

- **skill**：Skill 名称（如 `x-post`）
- **scene**：这次用它做了什么（一句话描述）
- **satisfaction**：满意度 1-5 分（1=很差，3=一般，5=完美）
- **note**：备注，比如哪里不满意、手动改了什么（可为空）

### Step 2：运行追加脚本

```bash
python scripts/log_skill_usage.py \
  --skill "x-post" \
  --scene "把播客笔记改成即刻动态" \
  --satisfaction 4 \
  --note "结尾不够有力，手动改了"
```

如果脚本报错，先把错误信息反馈给用户，并提示检查 `OBSIDIAN_VAULT`、目标路径和脚本依赖；不要假设已经写入成功。

### Step 3：确认写入

脚本输出追加成功后，向用户确认：

```
✅ 已记录：x-post（满意度 4/5）
   场景：把播客笔记改成即刻动态
   备注：结尾不够有力，手动改了
```

## 日志格式

每条记录是一行 JSON：

```json
{"date": "2026-03-24", "weekday": "周二", "week": "W13", "skill": "x-post", "scene": "把播客笔记改成即刻动态", "satisfaction": 4, "note": "结尾不够有力，手动改了"}
```

## 环境变量配置

在 `~/.env` 或项目根目录的 `.env` 文件中配置：

```bash
# Skill 日志文件所在的 Obsidian vault 路径
OBSIDIAN_VAULT=<OBSIDIAN_ROOT>
```

脚本通过 `os.environ['OBSIDIAN_VAULT']` 读取，不要把路径硬编码进脚本。

## Preflight
- Confirm the skill matches the task before using it.
- Keep the scope tight and avoid unrelated changes.
## Output Contract
- State the result, blockers, and any key tradeoffs.
- Keep it concise and actionable.
