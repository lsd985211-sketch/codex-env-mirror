# Minecraft World Generation Skill: core

This reference preserves detailed material moved out of `SKILL.md` for progressive disclosure. Load it only when the task matches these topics.

## Two Approaches to Custom Worldgen

| Approach | Best When | Platform |
|----------|-----------|----------|
| **Datapack JSON** | Overriding/extending vanilla worldgen | Vanilla, any server |
| **Mod + Datagen** | Registering new biomes/dimensions, code-driven | NeoForge / Fabric |
| **Biome Modifier (NeoForge)** | Adding features/spawns to existing biomes | NeoForge |
| **BiomeModification API (Fabric)** | Adding features/spawns to existing biomes | Fabric |


---

## Directory Layout (Datapack / Mod Resources)

```
data/<namespace>/
????? worldgen/
??  ????? biome/
??  ??  ????? my_biome.json
??  ????? configured_feature/
??  ??  ????? my_ore.json
??  ????? placed_feature/
??  ??  ????? my_ore_placed.json
??  ????? noise_settings/
??  ??  ????? my_dimension_noise.json
??  ????? density_function/
??  ??  ????? my_density.json    (advanced)
??  ????? structure/
??  ??  ????? my_structure.json
??  ????? structure_set/
??  ??  ????? my_structures.json
??  ????? processor_list/
??  ??  ????? my_processors.json
??  ????? template_pool/
??  ??  ????? my_pool.json
??  ????? carver/
??      ????? my_carver.json
????? dimension/
??  ????? my_dimension.json
????? dimension_type/
??  ????? my_type.json
????? tags/
??  ????? worldgen/
??      ????? biome/
??          ????? is_forest.json
????? neoforge/
    ????? biome_modifier/      (NeoForge mod only)
        ????? add_ores.json
```

---

## Development Workflow

1. Create or edit worldgen JSON files in `data/<namespace>/worldgen/` (or equivalent mod resources path).
2. Run the bundled validator to catch JSON and cross-reference errors before loading:
   ```bash
   ./scripts/validate-worldgen-json.sh --root /path/to/datapack-or-mod-resources
   # Strict mode treats warnings as failures:
   ./scripts/validate-worldgen-json.sh --root /path/to/datapack-or-mod-resources --strict
   ```
3. Fix any reported errors and re-validate until clean. The validator checks:
   - JSON validity for `worldgen/**` and `neoforge/biome_modifier/**`
   - Cross-reference integrity for `placed_feature -> configured_feature`
   - Cross-reference integrity for `structure_set -> structure` and biome/biome_modifier feature targets
   - Cross-reference integrity for `jigsaw structure -> start_pool` and `template_pool -> structure template / processor_list`
4. In-game biome and structure testing:
   ```mcfunction
   /locate structure <namespace>:my_structure
   /locate biome <namespace>:my_biome
   /placefeature <namespace>:my_ore_placed
   ```
5. For dimension testing, use `/execute in` (dimension must exist at world load, not added via `/reload`):
   ```mcfunction
   execute in <namespace>:my_dimension run tp @s 0 100 0
   ```
6. Check `latest.log` for worldgen errors (missing biome references, malformed noise settings).
7. Note: `/reload` refreshes datapack JSON but does **not** re-generate already-generated chunks. Test new worldgen in a fresh world or newly generated chunks. For existing test worlds, use a disposable copy and a purpose-built chunk reset/regeneration workflow; `/fill` only replaces blocks and is not a substitute for world generation.

---

## References

- Minecraft Wiki ??World generation: https://minecraft.wiki/w/Custom_world_generation
- Minecraft Wiki ??Biome: https://minecraft.wiki/w/Biome/JSON_format
- Minecraft Wiki ??Features: https://minecraft.wiki/w/World_generation/Configured_feature
- NeoForge Biome Modifiers: https://docs.neoforged.net/docs/worldgen/biomemodifier/
- Fabric BiomeModifications: https://wiki.fabricmc.net/tutorial:biomemodification
- misode's data pack generator (worldgen UI): https://misode.github.io/worldgen/
