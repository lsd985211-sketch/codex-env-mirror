# Minecraft Resource Pack Skill: core

This reference preserves detailed material moved out of `SKILL.md` for progressive disclosure. Load it only when the task matches these topics.

## What Is a Resource Pack?

A resource pack is a folder (or `.zip`) that overrides or adds Minecraft's visual and
audio assets: textures, models, sounds, language files, fonts, and shaders. No Java
or mod loader required. Works on vanilla clients and servers.


---

## Pack Metadata (1.21.x)

| Minecraft Version | Preferred `pack` metadata |
|-------------------|---------------------------|
| 1.21 / 1.21.1     | `pack_format: 34` |
| 1.21.2 / 1.21.3   | `pack_format: 42` |
| 1.21.4            | `pack_format: 46` |
| 1.21.5            | `pack_format: 55` |
| 1.21.6            | `pack_format: 63` |
| 1.21.7 / 1.21.8   | `pack_format: 64` |
| 1.21.9 / 1.21.10  | `min_format: [69, 0]`, `max_format: [69, 0]` |
| 1.21.11           | `min_format: [75, 0]`, `max_format: [75, 0]` |

Use `pack_format` through 1.21.8. Starting in 1.21.9, `pack.mcmeta` switches to
`min_format` / `max_format` instead of the older single-number field.
For exact patch targeting, use `[major, minor]` arrays for both `min_format` and
`max_format`, including `.0` versions such as `[75, 0]`. A single integer is
equivalent to `[major, 0]` for `min_format`, while a single integer in
`max_format` allows any minor version on that major line. Do not write decimal
JSON numbers.

---

## Directory Layout

```
my-pack/
????? pack.mcmeta
????? pack.png                   ??64?64 icon (optional)
????? assets/
    ????? minecraft/             ??override vanilla (or <namespace>/ for new packs)
        ????? models/
        ??  ????? block/
        ??  ??  ????? stone.json
        ??  ????? item/
        ??      ????? diamond_sword.json
        ????? items/              ??1.21.4+ item model definitions
        ??  ????? diamond_sword.json
        ????? blockstates/
        ??  ????? stone.json
        ????? textures/
        ??  ????? block/
        ??  ??  ????? stone.png
        ??  ????? item/
        ??  ??  ????? diamond_sword.png
        ??  ????? gui/
        ??  ??  ????? sprites/
        ??  ??      ????? my_sprite.png
        ??  ????? entity/
        ??      ????? zombie/
        ??          ????? zombie.png
        ????? sounds/
        ??  ????? custom/
        ??      ????? my_sound.ogg
        ????? sounds.json
        ????? font/
        ??  ????? default.json
        ????? lang/
        ??  ????? en_us.json
        ????? shaders/           ??core shader overrides (advanced)
        ????? optifine/          ??OptiFine CIT / CTM (OptiFine only)
            ????? cit/
                ????? my_item.properties
```

---

## `pack.mcmeta`

### 1.21.8 and earlier

```json
{
  "pack": {
    "pack_format": 64,
    "description": "My Custom Resource Pack v1.0"
  }
}
```

### 1.21.9 / 1.21.10

```json
{
  "pack": {
    "min_format": [69, 0],
    "max_format": [69, 0],
    "description": "My Custom Resource Pack v1.0"
  }
}
```

### 1.21.11

```json
{
  "pack": {
    "min_format": [75, 0],
    "max_format": [75, 0],
    "description": "My Custom Resource Pack v1.0"
  }
}
```

---

## Installation

```bash
# Singleplayer: place in
~/.minecraft/resourcepacks/my-pack/
# or
~/.minecraft/resourcepacks/my-pack.zip

# Server-side (forces on clients):
# Set in server.properties:
resource-pack=https://example.com/my-pack.zip
resource-pack-sha1=<sha1 hash>
resource-pack-prompt={"text":"Required pack","color":"gold"}
```

---

## Common Issues

| Problem | Cause | Fix |
|---------|-------|-----|
| Model not showing | Wrong JSON path or syntax error | Check `assets/<namespace>/models/` path; validate JSON |
| Black/pink checkerboard | Texture path wrong or missing | Check `textures/` path, file extension not in JSON |
| Blockstate not applying | Wrong state property name | Match exact property names from `/blockdata` |
| Animation not working | Wrong MCMETA location | Must be same folder as texture, named `texture.png.mcmeta` |
| Custom sound not playing | Not in `sounds.json` | Register sound event in `sounds.json`, match namespace |
| Pack not loading | Wrong `pack_format` or `min_format` / `max_format` values | Update `pack.mcmeta` for the exact 1.21.x patch |

## Validator Script

Use the bundled validator script before shipping a resource-pack update:

```bash
# Run from the installed skill directory (for example `.claude/skills/minecraft-resource-pack`):
./scripts/validate-resource-pack.sh --root /path/to/resource-pack

# Strict mode treats warnings as failures:
./scripts/validate-resource-pack.sh --root /path/to/resource-pack --strict
```

What it checks:
- JSON validity for `pack.mcmeta` and `assets/**/*.json`
- Model/blockstate/font/sounds references resolve to real files
- Every `*.png.mcmeta` has a matching `*.png`

---

## References

- Minecraft Wiki ??Resource pack: https://minecraft.wiki/w/Resource_pack
- Minecraft Wiki ??Model: https://minecraft.wiki/w/Tutorials/Models
- Minecraft Wiki ??Blockstates: https://minecraft.wiki/w/Blockstate_(Java_Edition)
- Pack format history: https://minecraft.wiki/w/Pack_format
- Misode's model viewer: https://misode.github.io/
- OptiFine CIT guide: https://optifine.readthedocs.io/cit.html
