---
name: minecraft-world-generation
description: "Minecraft world generation: datapack JSON, biomes, dimensions, placed features, and mod datagen."
---

# Minecraft World Generation Skill

Use this skill for Minecraft world generation through datapack JSON, custom biomes, dimensions, configured/placed features, structures, structure sets, NeoForge biome modifiers, Fabric BiomeModification, and mod datagen.

## Operating Rules

- Decide whether the task is vanilla datapack worldgen, Fabric, NeoForge, or multiloader modded worldgen.
- Load only the matching reference file before implementing details.
- For version-sensitive registry or JSON behavior, verify against current Minecraft/mod-loader docs when accuracy matters.
- For project-specific server/modpack tasks, use project skills first.
- Keep this `SKILL.md` as a router. Do not paste the detailed references into the response unless the user asks for that detail.

## Reference Routing

| Task | Read |
|---|---|
| Choose datapack JSON vs mod/datagen and review directory layout | `references/core.md` |
| biomes, dimensions, features, structures, structure sets in JSON | `references/datapack-worldgen.md` |
| NeoForge biome modifiers, Fabric API, registry keys, datagen code | `references/modded-worldgen.md` |

## Validation

- Use the bundled `scripts/` validators when checking generated packs, snippets, or workflows.
- Validate JSON structure and version-specific metadata before finalizing generated files.
- If official Minecraft behavior may have changed, verify against current docs or source before giving a definitive answer.

## Reference Inventory

- `references/core.md`: Two Approaches to Custom Worldgen; Directory Layout (Datapack / Mod Resources); Development Workflow; References
- `references/datapack-worldgen.md`: Custom Biome JSON; Configured Feature; Placed Feature; Dimension Type; Custom Dimension; Custom Structure; Structure Set
- `references/modded-worldgen.md`: NeoForge: Biome Modifier; Fabric: BiomeModification API (Code); Mod-Registered Worldgen (NeoForge + Fabric via Datagen)

## Preflight
- Confirm the skill matches the task before using it.
- Keep the scope tight and avoid unrelated changes.
## Output Contract
- State the result, blockers, and any key tradeoffs.
- Keep it concise and actionable.
