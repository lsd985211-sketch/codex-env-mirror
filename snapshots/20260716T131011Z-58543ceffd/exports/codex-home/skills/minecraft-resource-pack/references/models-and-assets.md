# Minecraft Resource Pack Skill: models-and-assets

This reference preserves detailed material moved out of `SKILL.md` for progressive disclosure. Load it only when the task matches these topics.

## Block Models

### `assets/minecraft/models/block/my_cube.json`
Full cube ??all six faces use the same texture:
```json
{
  "parent": "minecraft:block/cube_all",
  "textures": {
    "all": "minecraft:block/stone"
  }
}
```

Column block (like logs):
```json
{
  "parent": "minecraft:block/cube_column",
  "textures": {
    "end": "mypack:block/my_pillar_top",
    "side": "mypack:block/my_pillar_side"
  }
}
```

Different sides:
```json
{
  "parent": "minecraft:block/cube",
  "textures": {
    "up":    "mypack:block/my_block_top",
    "down":  "mypack:block/my_block_bottom",
    "north": "mypack:block/my_block_side",
    "south": "mypack:block/my_block_side",
    "east":  "mypack:block/my_block_side",
    "west":  "mypack:block/my_block_side",
    "particle": "mypack:block/my_block_side"
  }
}
```

Cross model (flowers, plants):
```json
{
  "parent": "minecraft:block/cross",
  "textures": {
    "cross": "mypack:block/my_flower"
  }
}
```

### Custom geometry (elements)
```json
{
  "credit": "Custom model",
  "ambientocclusion": true,
  "textures": {
    "0": "mypack:block/panel",
    "particle": "mypack:block/panel"
  },
  "elements": [
    {
      "from": [0, 0, 7],
      "to": [16, 16, 9],
      "faces": {
        "north": { "texture": "#0", "uv": [0, 0, 16, 16] },
        "south": { "texture": "#0", "uv": [0, 0, 16, 16] }
      }
    }
  ],
  "display": {
    "thirdperson_righthand": {
      "rotation": [75, 45, 0],
      "translation": [0, 2.5, 0],
      "scale": [0.375, 0.375, 0.375]
    }
  }
}
```

> `from` and `to` are in 1/16th block units (0??6). `uv` is `[x1, y1, x2, y2]` in 0??6 units.

---

## Item Models

### Simple flat item
```json
{
  "parent": "minecraft:item/generated",
  "textures": {
    "layer0": "mypack:item/my_item"
  }
}
```

### Held item (in-hand model)
```json
{
  "parent": "minecraft:item/handheld",
  "textures": {
    "layer0": "mypack:item/my_sword"
  }
}
```

### Two-layer item (colored like leather armor)
```json
{
  "parent": "minecraft:item/generated",
  "textures": {
    "layer0": "minecraft:item/leather_helmet",
    "layer1": "minecraft:item/leather_helmet_overlay"
  }
}
```

### Custom model data overrides (1.21.4 and prior)
Each `predicate` entry routes to a different model based on `custom_model_data`:
```json
{
  "parent": "minecraft:item/handheld",
  "textures": {
    "layer0": "minecraft:item/stick"
  },
  "overrides": [
    { "predicate": { "custom_model_data": 1001 }, "model": "mypack:item/magic_wand" },
    { "predicate": { "custom_model_data": 1002 }, "model": "mypack:item/fire_staff" }
  ]
}
```

### 1.21.4+ Item Model (new format)
In 1.21.4, Mojang introduced a new item model system. Place model definitions at
`assets/<namespace>/items/<item_name>.json`:
```json
{
  "model": {
    "type": "minecraft:select",
    "property": "minecraft:custom_model_data",
    "fallback": {
      "type": "minecraft:model",
      "model": "minecraft:item/stick"
    },
    "cases": [
      {
        "when": 1001,
        "model": { "type": "minecraft:model", "model": "mypack:item/magic_wand" }
      }
    ]
  }
}
```

---

## Blockstate Definitions

### Simple block (no variants)
```json
{
  "variants": {
    "": { "model": "mypack:block/my_block" }
  }
}
```

### Facing block (4 rotations)
```json
{
  "variants": {
    "facing=north": { "model": "mypack:block/my_block" },
    "facing=south": { "model": "mypack:block/my_block",  "y": 180 },
    "facing=east":  { "model": "mypack:block/my_block",  "y": 90  },
    "facing=west":  { "model": "mypack:block/my_block",  "y": 270 }
  }
}
```

### Random texture (multipart)
```json
{
  "variants": {
    "": [
      { "model": "minecraft:block/grass_block",  "weight": 3 },
      { "model": "minecraft:block/grass_block_2" }
    ]
  }
}
```

### Multipart (slabs, fences, walls)
```json
{
  "multipart": [
    { "apply": { "model": "mypack:block/my_slab_bottom" }, "when": { "type": "bottom" } },
    { "apply": { "model": "mypack:block/my_slab_top"    }, "when": { "type": "top"    } },
    { "apply": { "model": "mypack:block/my_block"        }, "when": { "type": "double" } }
  ]
}
```

---

## Textures

- Format: **PNG**, RGBA (32-bit)
- Standard block/item size: **16?16 px**
- Textures can be larger (32?32, 64?64) ??Minecraft scales them, but stick to powers of 2
- Animation requires height = N ? width (e.g., 16?64 for 4 frames)
- Place block textures in `assets/<namespace>/textures/block/`
- Place item textures in `assets/<namespace>/textures/item/`
- All textures are referenced without the `.png` extension in JSON

### Animated texture MCMETA
`assets/minecraft/textures/block/fire_0.png.mcmeta`:
```json
{
  "animation": {
    "frametime": 2,
    "frames": [0, 1, 2, 3, 4, 5, 6, 7]
  }
}
```
If `frames` is omitted, all frames play sequentially. `frametime` is in game ticks (default 1).

### GUI sprites (1.20.2+)
Place sprites at `assets/minecraft/textures/gui/sprites/<category>/<name>.png`.
Reference them with `<category>/<name>` in code/JSON.

---

## Sounds

### `assets/minecraft/sounds.json`
```json
{
  "my_sound.play": {
    "sounds": [
      { "name": "mypack:custom/my_sound", "volume": 1.0, "pitch": 1.0, "weight": 1 },
      { "name": "mypack:custom/my_sound_alt", "weight": 2 }
    ],
    "category": "players"
  },
  "entity.player.levelup": {
    "replace": true,
    "sounds": [
      { "name": "mypack:custom/levelup_replaced", "volume": 0.75, "pitch": 1.0 }
    ]
  }
}
```

- Sound files go in `assets/<namespace>/sounds/` as `.ogg` files (Vorbis encoded)
- Use `"replace": true` to replace vanilla sounds instead of adding to them
- Categories: `master`, `music`, `record`, `weather`, `block`, `hostile`, `neutral`, `player`, `ambient`, `voice`

---

## Language Files

`assets/minecraft/lang/en_us.json`:
```json
{
  "block.mypack.my_block": "My Custom Block",
  "item.mypack.my_item": "Magic Wand",
  "entity.mypack.my_mob": "Forest Guardian",
  "death.attack.mypack.laser": "%1$s was zapped by %2$s"
}
```

- Use the exact translation key format for your mod/datapack namespace
- File name is the locale code (e.g., `fr_fr.json`, `de_de.json`)
- Always provide `en_us.json` as the primary fallback

---

## Fonts

### `assets/minecraft/font/default.json` ??add glyph
```json
{
  "providers": [
    {
      "type": "bitmap",
      "file": "mypack:font/icons.png",
      "ascent": 8,
      "height": 9,
      "chars": ["\uE000", "\uE001", "\uE002"]
    }
  ]
}
```

Custom icons via private use area (U+E000??+F8FF). Reference in text with `\uE000`.
The `icons.png` must have each character cell `height` pixels tall.

---
