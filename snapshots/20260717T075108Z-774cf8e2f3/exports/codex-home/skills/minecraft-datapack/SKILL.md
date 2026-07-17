---
name: minecraft-datapack
description: "Minecraft 1.21 datapacks: mcfunction, advancements, loot tables, predicates, functions, and pack layout."
---

# Minecraft Datapack Skill

Use this skill for Minecraft Java datapacks, mcfunction logic, pack.mcmeta, function tags, advancements, recipes, loot tables, predicates, tags, storage, macros, and datapack validation.

## Operating Rules

- Identify the target Minecraft patch before writing metadata or JSON paths.
- Load only the reference file matching the requested artifact or bug.
- Use bundled scripts for validation when the task asks to check a pack or generated snippet.
- For project-specific server work, prefer project skills before this generic skill.
- Keep this `SKILL.md` as a router. Do not paste the detailed references into the response unless the user asks for that detail.

## Reference Routing

| Task | Read |
|---|---|
| Pack setup, pack.mcmeta, install/test, validation | `references/core.md` |
| mcfunction load/tick, execute, storage, macros | `references/functions.md` |
| advancements, recipes, loot tables, predicates, tags, worldgen overrides | `references/content-json.md` |

## Validation

- Use the bundled `scripts/` validators when checking generated packs, snippets, or workflows.
- Validate JSON structure and version-specific metadata before finalizing generated files.
- If official Minecraft behavior may have changed, verify against current docs or source before giving a definitive answer.

## Reference Inventory

- `references/core.md`: Skill Scope; Pack Metadata (1.21.x); Directory Layout; `pack.mcmeta`; Installation & Testing; Common Errors; Validator Script; References
- `references/functions.md`: Function Tags (load / tick); Commands and Function Syntax; Macros (1.20.2+)
- `references/content-json.md`: Advancements; Custom Recipes; Loot Tables; Predicates; Tags; Worldgen Overrides

## Preflight
- Confirm the skill matches the task before using it.
- Keep the scope tight and avoid unrelated changes.
## Output Contract
- State the result, blockers, and any key tradeoffs.
- Keep it concise and actionable.
