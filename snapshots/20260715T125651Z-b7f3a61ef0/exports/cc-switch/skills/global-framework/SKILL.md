---
name: global-framework
description: >
  Skill index and loading rules. Always active. Routes tasks to correct
  skills via trigger keywords,
  common file/output constraints, and conflict resolution priority.
---

# Global Skill Framework (always loaded)

## Loading Rules

1. **Trigger rules:** match the user request against the keywords
   below and load all matching skills whose full body is needed.
2. **Priority:** project-local skills > global skills; safety rules >
   convenience rules.

## Common Constraints (apply to ALL tasks)

- **File safety:** Never read >2000 lines per file; prefer `rg` for
  search. Skip `node_modules/`, `.git/`, `build/`, `dist/`, large logs.
- **Output:** Concise, evidence-first, structured for automation
- **Edits:** apply_patch, backup before destructive, no cat write tricks
- **Network:** Invoke-WebRequest when sandbox blocks fetch, require_escalated for external
- **AGENTS.md:** Check before modifying files outside workspace
  update the `self-improvement` skill.

## Skill Index

### Minecraft (15 skills)

| Skill | Triggers | Path |
|---|---|---|
| `fabric-mc-26-1-2` | Fabric, mod, ??, ?????, Minecraft 26.x | `fabric-mc-26-1-2/SKILL.md` |
| `minecraft-modding` | ??mod, NeoForge, Fabric mod, ??, ??, ?? | `minecraft-modding/SKILL.md` |
| `minecraft-multiloader` | ??mod, Architectury, ???? | `minecraft-multiloader/SKILL.md` |
| `minecraft-plugin-dev` | Paper, Bukkit, Spigot, ?????, plugin.yml | `minecraft-plugin-dev/SKILL.md` |
| `minecraft-server-admin` | ??, ?????, ????, ??, Docker | `minecraft-server-admin/SKILL.md` |
| `minecraft-commands-scripting` | ??, command block, execute, scoreboard, NBT | `minecraft-commands-scripting/SKILL.md` |
| `minecraft-datapack` | ???, .mcfunction, advancement, loot table | `minecraft-datapack/SKILL.md` |
| `minecraft-resource-pack` | ???, ??, ??JSON, blockstate, ?? | `minecraft-resource-pack/SKILL.md` |
| `minecraft-world-generation` | ????, ??, biome, dimension, structure | `minecraft-world-generation/SKILL.md` |
| `minecraft-worldedit-ops` | WorldEdit, //set, //stack, schematic | `minecraft-worldedit-ops/SKILL.md` |
| `minecraft-essentials-ops` | EssentialsX, /home, /warp, ??, ?? | `minecraft-essentials-ops/SKILL.md` |
| `minecraft-ci-release` | CI/CD, Modrinth??, CurseForge, Gradle publish | `minecraft-ci-release/SKILL.md` |
| `minecraft-testing` | GameTest, MockBukkit, JUnit, ???? | `minecraft-testing/SKILL.md` |
| `mc-mod-automation` | mod??, client-mods, AutoModpack, Fabric Loader?? | `mc-mod-automation/SKILL.md` |`n| `minecraft-imagegen` | ??MC??, ????, ?????? | `minecraft-imagegen/SKILL.md` |

### Documentation (3 skills)

| Skill | Triggers | Path |
|---|---|---|
| `context7-cli` | ctx7, context7, ??/????, MCP?? | `context7-cli/SKILL.md` |
| `context7-mcp` | ???, API??, React/Vue/Next.js, ???? | `context7-mcp/SKILL.md` |
| `find-docs` | ????, ??API, ????, ???/?? | `find-docs/SKILL.md` |

### Agent Engineering (4 skills)

| Skill | Triggers | Path |
|---|---|---|
| `diagnose` | debug, ??, ??, ????, reproduce | `diagnose/SKILL.md` |
| `memory-systems` | agent memory, ?????, entity, knowledge graph | `memory-systems/SKILL.md` |
| `multi-agent-patterns` | ?agent, supervisor, swarm, handoff, context isolation | `multi-agent-patterns/SKILL.md` |
| `self-improvement` | ????, ??, ????, ???? | `self-improvement/SKILL.md` |

### Web and Automation (4 skills)

| Skill | Triggers | Path |
|---|---|---|
| `playwright` | ??????, form fill, screenshot, E2E?? | `playwright/SKILL.md` |
| `webapp-testing` | ??localhost, ????, Playwright debug, UI test | `webapp-testing/SKILL.md` |
| `agent-browser` | ???????, agent-browser CLI, ????? | `agent-browser/SKILL.md` |
| `context-compression` | ?????, token??, ????, ???? | `context-compression/SKILL.md` |

### System (5 skills, .system)`n
| Skill | When to use |
|---|---|
| `skill-installer` | Install skills from curated list or GitHub repo. |
| `skill-creator` | Create new skills with proper structure. |
| `plugin-creator` | Scaffold Codex plugin directories. |
| `imagegen` | Generate/edit raster images (photos, textures). |
| `openai-docs` | OpenAI product/API documentation reference. |

## Conflict Resolution Priority

1. **Safety > Everything.** Rules tagged `safety` in any skill
   override convenience rules.
2. **Project-local skill** (e.g. in workspace `AGENTS.md`) overrides
   global skill when both match.
3. **Specific > General.** `minecraft-modding` overrides general
   `diagnose` for mod-build failures.
4. **Latest-installed wins** when two skills have identical scope.## Post-Task Routine

After every task:
1. Run `context-compression`: trim from context anything irrelevant.
2. Run `self-improvement`: capture new learnings, errors, user prefs.
3. If a skill knowledge became outdated, flag it for update.