# Minecraft Datapack Skill: content-json

This reference preserves detailed material moved out of `SKILL.md` for progressive disclosure. Load it only when the task matches these topics.

## Advancements

### `data/<namespace>/advancement/my_advancement.json`
```json
{
  "display": {
    "icon": {
      "id": "minecraft:diamond"
    },
    "title": {"text": "Diamond Hunter"},
    "description": {"text": "Mine your first diamond"},
    "frame": "task",
    "show_toast": true,
    "announce_to_chat": true,
    "hidden": false
  },
  "criteria": {
    "mined_diamond": {
      "trigger": "minecraft:item_picked_up",
      "conditions": {
        "item": {
          "items": ["minecraft:diamond"]
        }
      }
    }
  },
  "rewards": {
    "function": "mypack:on_diamond_obtained",
    "experience": 10
  }
}
```

### Common advancement triggers
| Trigger | When it fires |
|---------|--------------|
| `minecraft:impossible` | Never (use for manual grants) |
| `minecraft:tick` | Every tick while player is online |
| `minecraft:player_killed_entity` | Player kills an entity |
| `minecraft:entity_killed_player` | Entity kills a player |
| `minecraft:item_picked_up` | Player picks up an item |
| `minecraft:placed_block` | Player places a block |
| `minecraft:inventory_changed` | Player inventory changes |
| `minecraft:changed_dimension` | Player changes dimension |
| `minecraft:consume_item` | Player consumes an item |
| `minecraft:location` | Player at a specific location |
| `minecraft:recipe_unlocked` | Player unlocks a recipe |

---

## Custom Recipes

### Shaped crafting (`data/<namespace>/recipe/shaped.json`)
```json
{
  "type": "minecraft:crafting_shaped",
  "pattern": [
    "DDD",
    "D D",
    "DDD"
  ],
  "key": {
    "D": { "item": "minecraft:diamond" }
  },
  "result": {
    "id": "minecraft:diamond_block",
    "count": 1
  }
}
```

### Shapeless crafting
```json
{
  "type": "minecraft:crafting_shapeless",
  "ingredients": [
    { "item": "minecraft:wheat" },
    { "item": "minecraft:wheat" },
    { "item": "minecraft:wheat" }
  ],
  "result": {
    "id": "minecraft:bread",
    "count": 2
  }
}
```

### Smelting / blasting / smoking / campfire
```json
{
  "type": "minecraft:smelting",
  "ingredient": { "item": "minecraft:beef" },
  "result": { "id": "minecraft:cooked_beef" },
  "experience": 0.35,
  "cookingtime": 200
}
```

### Disable a vanilla recipe (override with empty file)
To remove a vanilla recipe, create a file at the **same path** under `data/minecraft/recipe/`
in your datapack with just `{}` as the content:

```json
{}
```

For example, to disable the piston recipe, create:  
`data/minecraft/recipe/piston.json` containing only `{}`.

> Get the exact filename from the vanilla jar:  
> `jar xf minecraft.jar data/minecraft/recipe/`

### Smithing transform
```json
{
  "type": "minecraft:smithing_transform",
  "template": { "item": "minecraft:netherite_upgrade_smithing_template" },
  "base": { "item": "minecraft:diamond_sword" },
  "addition": { "item": "minecraft:netherite_ingot" },
  "result": { "id": "minecraft:netherite_sword" }
}
```

---

## Loot Tables

### `data/<namespace>/loot_table/custom_chest.json`
```json
{
  "type": "minecraft:chest",
  "pools": [
    {
      "rolls": { "type": "minecraft:uniform", "min": 3, "max": 8 },
      "entries": [
        {
          "type": "minecraft:item",
          "name": "minecraft:diamond",
          "weight": 5,
          "functions": [
            {
              "function": "minecraft:set_count",
              "count": { "type": "minecraft:uniform", "min": 1, "max": 3 }
            }
          ]
        },
        {
          "type": "minecraft:item",
          "name": "minecraft:gold_ingot",
          "weight": 20
        },
        {
          "type": "minecraft:empty",
          "weight": 30
        }
      ]
    }
  ]
}
```

---

## Predicates

### `data/<namespace>/predicate/is_daytime.json`
```json
{
  "condition": "minecraft:time_check",
  "value": { "min": 0, "max": 12000 }
}
```

### `data/<namespace>/predicate/player_has_diamond.json`
```json
{
  "condition": "minecraft:entity_properties",
  "entity": "this",
  "predicate": {
    "inventory": {
      "items": [
        { "items": ["minecraft:diamond"] }
      ]
    }
  }
}
```

### Using predicates in functions
```mcfunction
execute if predicate mypack:is_daytime run say It is daytime!
execute unless predicate mypack:player_has_diamond run tell @s You need a diamond!
```

---

## Tags

### Block tag (`data/minecraft/tags/block/climbable.json` ??override vanilla)
```json
{
  "replace": false,
  "values": [
    "minecraft:ladder",
    "minecraft:vine",
    "#minecraft:wool"
  ]
}
```

### Item tag (`data/<namespace>/tags/item/my_fuel.json`)
```json
{
  "replace": false,
  "values": [
    "minecraft:coal",
    "minecraft:charcoal",
    "minecraft:blaze_rod"
  ]
}
```

Use `"replace": false` to append to existing tags. Use `"replace": true` to completely
override (use with care for vanilla tags).

---

## Worldgen Overrides

### Override biome noise (`data/minecraft/worldgen/noise_settings/overworld.json`)
Edit inside an existing copy ??do NOT create from scratch without the full JSON.
Get the vanilla version from the Minecraft jar: `jar xf minecraft.jar data/`.

### Override a biome's spawn costs
```json
{
  "spawn_costs": {
    "minecraft:zombie": {
      "energy_budget": 0.12,
      "charge": 0.7
    }
  }
}
```

---
