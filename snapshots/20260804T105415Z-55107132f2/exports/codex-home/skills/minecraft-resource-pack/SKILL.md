---
name: minecraft-resource-pack
description: "Minecraft 1.21 resource packs: textures, models, blockstates, sounds, lang, fonts, shaders, and JSON assets."
---

# Minecraft Resource Pack Skill

Use this skill for Minecraft Java resource packs: pack.mcmeta, textures, block/item models, blockstates, sounds, language files, fonts, CIT, shader-pack delivery, and resource-pack validation.

## Operating Rules

- Identify target Minecraft patch and whether the pack is vanilla-only or modded.
- Load only the reference file matching the requested asset category.
- Preserve namespace/path casing and validate JSON snippets before presenting them as final.
- For project-specific AutoModpack or server resource-pack distribution, prefer project skills first.
- Keep this `SKILL.md` as a router. Do not paste the detailed references into the response unless the user asks for that detail.

## Reference Routing

| Task | Read |
|---|---|
| Pack setup, metadata, directory layout, install, validation | `references/core.md` |
| models, blockstates, textures, sounds, lang, fonts | `references/models-and-assets.md` |
| OptiFine CIT or Iris shader delivery through resource packs | `references/extensions.md` |

## Validation

- Use the bundled `scripts/` validators when checking generated packs, snippets, or workflows.
- Validate JSON structure and version-specific metadata before finalizing generated files.
- If official Minecraft behavior may have changed, verify against current docs or source before giving a definitive answer.

## Reference Inventory

- `references/core.md`: What Is a Resource Pack?; Pack Metadata (1.21.x); Directory Layout; `pack.mcmeta`; Installation; Common Issues; Validator Script; References
- `references/models-and-assets.md`: Block Models; Item Models; Blockstate Definitions; Textures; Sounds; Language Files; Fonts
- `references/extensions.md`: OptiFine CIT (Custom Item Textures); Iris Shaders (Resource Pack Method)

## Preflight
- Confirm the skill matches the task before using it.
- Keep the scope tight and avoid unrelated changes.
## Output Contract
- State the result, blockers, and any key tradeoffs.
- Keep it concise and actionable.
