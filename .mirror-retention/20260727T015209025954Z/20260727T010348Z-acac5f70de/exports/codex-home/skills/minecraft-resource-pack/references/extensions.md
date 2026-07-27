# Minecraft Resource Pack Skill: extensions

This reference preserves detailed material moved out of `SKILL.md` for progressive disclosure. Load it only when the task matches these topics.

## OptiFine CIT (Custom Item Textures)

> OptiFine-only feature. Does not work in vanilla or Iris.

### `assets/minecraft/optifine/cit/my_sword.properties`
```properties
type=item
items=minecraft:diamond_sword
texture=my_sword_texture.png
model=my_sword_model
nbt.display.Name=ipattern:*Excalibur*
```

Common CIT properties:
- `type=item` ??item texture override
- `type=enchantment` ??custom enchantment glint
- `type=armor` ??armor overlay
- `items=` ??comma-separated item IDs
- `damage=` ??damage range (e.g., `0-50%`)
- `nbt.display.Name=ipattern:*text*` ??NBT name filter
- `texture=` ??PNG file (relative to `.properties` file)
- `model=` ??JSON model file (relative)

---

## Iris Shaders (Resource Pack Method)

Iris shaders live inside a resource pack at:
```
assets/iris/
    shaders/
        core/
            rendertype_terrain.vsh    ??vertex shader override
            rendertype_terrain.fsh    ??fragment shader override
```

Full shader pack distribution uses the `.zip` format with a `shaders/` root folder
(not inside `assets/`). Resource pack shader overrides target specific render types.

---
