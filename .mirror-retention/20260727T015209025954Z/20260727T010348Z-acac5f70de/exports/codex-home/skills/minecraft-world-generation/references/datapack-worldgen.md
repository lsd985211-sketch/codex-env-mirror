# Minecraft World Generation Skill: datapack-worldgen

This reference preserves detailed material moved out of `SKILL.md` for progressive disclosure. Load it only when the task matches these topics.

## Custom Biome JSON

The biome and dimension examples below match the 1.21.10-and-earlier worldgen
shape. Minecraft 1.21.11 moves many visual/environment fields into Environment
Attributes and Timelines, so for 1.21.11+ projects first verify the current
vanilla registry JSON or generate from a known-good tool before copying old
`effects` fields into new packs.

### `data/<namespace>/worldgen/biome/my_biome.json`
```json
{
  "has_precipitation": true,
  "temperature": 0.7,
  "temperature_modifier": "none",
  "downfall": 0.8,
  "effects": {
    "sky_color": 7907327,
    "fog_color": 12638463,
    "water_color": 4159204,
    "water_fog_color": 329011,
    "grass_color_modifier": "none",
    "ambient_sound": "minecraft:ambient.cave",
    "mood_sound": {
      "sound": "minecraft:ambient.cave",
      "tick_delay": 6000,
      "block_search_extent": 8,
      "offset": 2.0
    }
  },
  "spawners": {
    "monster": [
      { "type": "minecraft:zombie", "weight": 95, "minCount": 4, "maxCount": 4 },
      { "type": "minecraft:skeleton", "weight": 100, "minCount": 4, "maxCount": 4 }
    ],
    "creature": [
      { "type": "minecraft:sheep", "weight": 12, "minCount": 4, "maxCount": 4 }
    ],
    "ambient": [],
    "axolotls": [],
    "underground_water_creature": [],
    "water_creature": [],
    "water_ambient": [],
    "misc": []
  },
  "spawn_costs": {},
  "carvers": {
    "air": ["minecraft:cave", "minecraft:cave_extra_underground", "minecraft:canyon"]
  },
  "features": [
    [],
    [],
    ["minecraft:lake_lava_underground", "minecraft:lake_lava_surface"],
    ["minecraft:amethyst_geode", "minecraft:monster_room"],
    [],
    [],
    [
      "minecraft:ore_dirt", "minecraft:ore_gravel", "minecraft:ore_granite_upper",
      "minecraft:ore_coal_upper", "minecraft:ore_coal_lower",
      "<namespace>:my_ore_placed"
    ],
    [],
    ["minecraft:spring_lava"],
    [],
    ["minecraft:freeze_top_layer"]
  ]
}
```

> The `features` array has exactly 11 slots (indices 0??0), one per `GenerationStep.Decoration`:
>
> | Index | Step | Put here |
> |-------|------|---------|
> | 0 | `RAW_GENERATION` | (rarely used) |
> | 1 | `LAKES` | Surface water/lava lakes |
> | 2 | `LOCAL_MODIFICATIONS` | Underground lava lakes, geodes |
> | 3 | `UNDERGROUND_STRUCTURES` | Amethyst geodes, dungeons |
> | 4 | `SURFACE_STRUCTURES` | Glaciers, blue ice patches |
> | 5 | `STRONGHOLDS` | (unused in biome JSON) |
> | 6 | `UNDERGROUND_ORES` | **All ores go here** |
> | 7 | `UNDERGROUND_DECORATION` | Fossils, infested stone |
> | 8 | `FLUID_SPRINGS` | `spring_water`, `spring_lava` |
> | 9 | `VEGETAL_DECORATION` | Trees, grass, flowers |
> | 10 | `TOP_LAYER_MODIFICATION` | `freeze_top_layer` |
>
> Custom ores added via placed features must be placed at index **6**.

---

## Configured Feature

### `data/<namespace>/worldgen/configured_feature/my_ore.json`
```json
{
  "type": "minecraft:ore",
  "config": {
    "targets": [
      {
        "target": {
          "predicate_type": "minecraft:tag_match",
          "tag": "minecraft:stone_ore_replaceables"
        },
        "state": {
          "Name": "minecraft:emerald_ore"
        }
      }
    ],
    "size": 4,
    "discard_chance_on_air_exposure": 0.0
  }
}
```

### Other feature types
| Type | Use |
|------|-----|
| `minecraft:ore` | Ore veins |
| `minecraft:tree` | Tree placement |
| `minecraft:random_patch` | Grass, flowers, mushrooms |
| `minecraft:block_pile` | Hay bales, pumpkins |
| `minecraft:lake` | Water/lava lakes |
| `minecraft:disk` | Sand/gravel/clay disks |
| `minecraft:no_bonemeal_flower` | Wither roses, etc. |
| `minecraft:simple_block` | Single block placement |
| `minecraft:fill_layer` | Fill an entire layer |
| `minecraft:geode` | Amethyst geodes |
| `minecraft:decorated` | Wraps another feature with placement |

---

## Placed Feature

### `data/<namespace>/worldgen/placed_feature/my_ore_placed.json`
```json
{
  "feature": "<namespace>:my_ore",
  "placement": [
    {
      "type": "minecraft:count",
      "count": 8
    },
    {
      "type": "minecraft:in_square"
    },
    {
      "type": "minecraft:height_range",
      "height": {
        "type": "minecraft:trapezoid",
        "min_inclusive": { "above_bottom": 0 },
        "max_inclusive": { "absolute": 64 }
      }
    },
    {
      "type": "minecraft:biome"
    }
  ]
}
```

### Common placement modifiers
| Type | Effect |
|------|--------|
| `minecraft:count` | Number of attempts |
| `minecraft:count_on_every_layer` | Per layer |
| `minecraft:in_square` | Randomize X/Z within chunk |
| `minecraft:biome` | Only place if biome has this feature |
| `minecraft:height_range` | Y-level range |
| `minecraft:surface_relative_threshold_filter` | Filter by surface depth |
| `minecraft:noise_based_count` | Count varies with noise |
| `minecraft:rarity_filter` | 1-in-N chance |
| `minecraft:environment_scan` | Scans up/down for a condition |

---

## Dimension Type

The following dimension type is the 1.21.10-and-earlier shape. For 1.21.11+
dimension work, prefer starting from the current vanilla dimension type and
environment attribute registries, then validate in a fresh test world before
shipping. Do not assume older `effects`, `fixed_time`, or bed/anchor booleans
still model every environment behavior on newer runtimes.

### `data/<namespace>/dimension_type/my_type.json`
```json
{
  "ultrawarm": false,
  "natural": true,
  "coordinate_scale": 1.0,
  "has_skylight": true,
  "has_ceiling": false,
  "ambient_light": 0.0,
  "monster_spawn_light_level": {
    "type": "minecraft:uniform",
    "min_inclusive": 0,
    "max_inclusive": 7
  },
  "monster_spawn_block_light_limit": 0,
  "piglin_safe": false,
  "bed_works": true,
  "respawn_anchor_works": false,
  "has_raids": true,
  "logical_height": 384,
  "height": 384,
  "min_y": -64,
  "infiniburn": "#minecraft:infiniburn_overworld",
  "effects": "minecraft:overworld"
}
```

Omit `fixed_time` when the dimension should use the normal day/night cycle.
Only include it when you want a fixed long tick value such as `6000`.

---

## Custom Dimension

### `data/<namespace>/dimension/my_dimension.json`
```json
{
  "type": "<namespace>:my_type",
  "generator": {
    "type": "minecraft:noise",
    "biome_source": {
      "type": "minecraft:fixed",
      "biome": "<namespace>:my_biome"
    },
    "settings": "minecraft:overworld"
  }
}
```

### Multi-biome dimension with `minecraft:multi_noise` source
```json
{
  "type": "<namespace>:my_type",
  "generator": {
    "type": "minecraft:noise",
    "biome_source": {
      "type": "minecraft:multi_noise",
      "biomes": [
        {
          "parameters": {
            "temperature": [ -1.0, -0.45 ],
            "humidity":    [ -1.0, -0.35 ],
            "continentalness": [ -1.2, -1.05 ],
            "erosion":     [ -0.78, 0.0 ],
            "weirdness":   [ 0.0, 0.0 ],
            "depth":       [ 0.0, 0.0 ],
            "offset":      0.0
          },
          "biome": "<namespace>:my_biome"
        }
      ]
    },
    "settings": "minecraft:overworld"
  }
}
```

---

## Custom Structure

### `data/<namespace>/worldgen/structure/my_structure.json`
```json
{
  "type": "minecraft:jigsaw",
  "biomes": "#<namespace>:my_biome_tag",
  "step": "surface_structures",
  "terrain_adaptation": "beard_thin",
  "start_pool": "<namespace>:my_pool/start",
  "size": 6,
  "max_distance_from_center": 80,
  "use_expansion_hack": false,
  "spawn_overrides": {}
}
```

### Template pool for jigsaw structures
```json
{
  "fallback": "minecraft:empty",
  "elements": [
    {
      "weight": 1,
      "element": {
        "element_type": "minecraft:single_pool_element",
        "location": "<namespace>:my_structure/start",
        "projection": "rigid",
        "processors": "minecraft:empty"
      }
    }
  ]
}
```

---

## Structure Set

### `data/<namespace>/worldgen/structure_set/my_structures.json`
```json
{
  "structures": [
    {
      "structure": "<namespace>:my_structure",
      "weight": 1
    }
  ],
  "placement": {
    "type": "minecraft:random_spread",
    "spacing": 32,
    "separation": 8,
    "salt": 12345678
  }
}
```

---
