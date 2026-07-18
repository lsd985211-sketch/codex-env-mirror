# Minecraft Datapack Skill: core

This reference preserves detailed material moved out of `SKILL.md` for progressive disclosure. Load it only when the task matches these topics.

## Skill Scope


---

## Pack Metadata (1.21.x)

| Minecraft Version | Preferred `pack` metadata |
|-------------------|---------------------------|
| 1.21 / 1.21.1     | `pack_format: 48` |
| 1.21.2 / 1.21.3   | `pack_format: 57` |
| 1.21.4            | `pack_format: 61` |
| 1.21.5            | `pack_format: 71` |
| 1.21.6            | `pack_format: 80` |
| 1.21.7 / 1.21.8   | `pack_format: 81` |
| 1.21.9 / 1.21.10  | `min_format: [88, 0]`, `max_format: [88, 0]` |
| 1.21.11           | `min_format: [94, 1]`, `max_format: [94, 1]` |

Use `pack_format` through 1.21.8. Starting in 1.21.9, Mojang replaced that
single field with explicit `min_format` / `max_format` values.
For exact patch targeting, use `[major, minor]` arrays for both `min_format` and
`max_format`, including `.0` versions such as `[88, 0]`. A single integer is
equivalent to `[major, 0]` for `min_format`, while a single integer in
`max_format` allows any minor version on that major line. Do not write decimal
JSON numbers such as `94.1`.

Keep `pack.mcmeta` exact for the patch you target instead of trying to span the
entire 1.21.x line with one metadata block.

---

## Directory Layout

```
my-datapack/
????? pack.mcmeta
????? data/
    ????? <namespace>/           ??use your pack's name (e.g., mypack)
        ????? function/
        ??  ????? main.mcfunction
        ??  ????? tick.mcfunction
        ????? advancement/
        ??  ????? custom_advancement.json
        ????? recipe/
        ??  ????? custom_recipe.json
        ????? loot_table/
        ??  ????? custom_loot.json
        ????? predicate/
        ??  ????? is_night.json
        ????? item_modifier/
        ??  ????? add_name.json
        ????? tags/
            ????? block/
            ??  ????? climbable.json
            ????? entity_type/
            ??  ????? bosses.json
            ????? function/
                ????? load.json     ??runs on /reload
                ????? tick.json     ??runs every game tick
```

---

## `pack.mcmeta`

### 1.21.8 and earlier

```json
{
  "pack": {
    "pack_format": 81,
    "description": "My Custom Datapack v1.0"
  }
}
```

### 1.21.9 / 1.21.10

```json
{
  "pack": {
    "min_format": [88, 0],
    "max_format": [88, 0],
    "description": "My Custom Datapack v1.0"
  }
}
```

### 1.21.11

```json
{
  "pack": {
    "min_format": [94, 1],
    "max_format": [94, 1],
    "description": "My Custom Datapack v1.0"
  }
}
```

---

## Installation & Testing

```bash
# Place datapack in world folder
/datapacks/my-datapack/

# Or as a zip
/datapacks/my-datapack.zip

# In-game commands
/datapack list               # see all datapacks
/datapack enable "file/my-datapack"
/datapack disable "file/my-datapack"
/reload                      # hot-reload all datapacks without restart
```

### Development workflow
1. Edit `.mcfunction` or `.json` files
2. Run the bundled validator to catch JSON and path errors before loading:
   ```bash
   ./scripts/validate-datapack.sh --root /path/to/datapack
   ```
3. If errors, fix and re-validate until clean
4. Run `/reload` in-game (or `/minecraft:reload` if a mod intercepts it)
5. Test with target command (e.g., `/function mypack:setup`, trigger an advancement)
6. Check `latest.log` for runtime errors (missing references, bad selectors)

---

## Common Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `Unknown or invalid command` | Syntax error in function | Check whitespace, selector, trailing space |
| `Datapack did not load` | Invalid JSON in any file | Validate with `jq . < file.json` |
| `pack metadata mismatch` | Wrong `pack_format` or `min_format` / `max_format` values | Update `pack.mcmeta` for the exact 1.21.x patch |
| Function not running on tick | Missing tick tag or wrong namespace | Check `tags/function/tick.json` path |
| Macro error | `$` line but no `with` | Provide `with storage/entity/block` |

## Validator Script

Use the bundled validator script before shipping a datapack update:

```bash
# Run from the installed skill directory (for example `.codex/skills/minecraft-datapack`):
./scripts/validate-datapack.sh --root /path/to/datapack

# Strict mode treats warnings as failures:
./scripts/validate-datapack.sh --root /path/to/datapack --strict
```

What it checks:
- JSON validity for `pack.mcmeta` and `data/**/*.json`
- Legacy pluralized path mistakes for loot tables, functions, and block/item/function tags
- `tags/function/load.json` and `tags/function/tick.json` references resolve to real `.mcfunction` files

---

## References

- Minecraft Wiki ??Data Pack: https://minecraft.wiki/w/Data_pack
- Minecraft Wiki ??Function: https://minecraft.wiki/w/Function_(Java_Edition)
- Minecraft Wiki ??Commands: https://minecraft.wiki/w/Commands
- Pack format history: https://minecraft.wiki/w/Pack_format
- NBT format: https://minecraft.wiki/w/NBT_format
- Predicate conditions: https://minecraft.wiki/w/Predicate
- Loot table format: https://minecraft.wiki/w/Loot_table
