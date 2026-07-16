# Minecraft Datapack Skill: functions

This reference preserves detailed material moved out of `SKILL.md` for progressive disclosure. Load it only when the task matches these topics.

## Function Tags (load / tick)

### `data/<namespace>/tags/function/load.json`
```json
{
  "values": [
    "<namespace>:setup"
  ]
}
```

### `data/<namespace>/tags/function/tick.json`
```json
{
  "values": [
    "<namespace>:tick"
  ]
}
```

### `data/<namespace>/function/setup.mcfunction`
```mcfunction
# Runs once on /reload
scoreboard objectives add deaths deathCount
scoreboard objectives add kills playerKillCount
tellraw @a {"text":"[MyPack] Loaded!","color":"green"}
```

### `data/<namespace>/function/tick.mcfunction`
```mcfunction
# Runs every tick ??KEEP THIS SHORT
# Only put fast, targeted operations here
execute as @a[scores={deaths=1..}] run function mypack:on_death_check
```

---

## Commands and Function Syntax

### Execute subcommands (datapack-specific patterns)
```mcfunction
# Chained execute ??common datapack pattern for conditional per-player logic
execute as @a[gamemode=!spectator] at @s if block ~ ~-1 ~ #minecraft:logs run give @s minecraft:apple

# store result into score (bridge between NBT world and scoreboard state)
execute store result score @s mypack.health run data get entity @s Health

# in: run logic in another dimension
execute in minecraft:the_nether run say This runs in the Nether
```

### Storage NBT (datapack-specific global state)
```mcfunction
# Storage is the datapack-native key-value store ??persists across /reload
data modify storage mypack:data config.difficulty set value "hard"
data get storage mypack:data config.difficulty

# Copy live entity data into storage for macro use or cross-function state
data modify storage mypack:log last_player_pos set from entity @s Pos
```

For full command syntax, selectors, and scoreboard operations see the
[Minecraft Wiki ??Commands](https://minecraft.wiki/w/Commands) reference.
The `minecraft-commands-scripting` skill covers command-only work in depth.

---

## Macros (1.20.2+)

Macro functions let you pass dynamic arguments to a function.

### Define a macro function (`data/mypack/function/greet.mcfunction`)
```mcfunction
# Macro argument: $(name)
$tellraw @a {"text":"Welcome $(name)!","color":"gold"}
$scoreboard players set $(name) points 0
```

### Call with `run function` + `with`
```mcfunction
# Pass values from storage
data modify storage mypack:tmp input set value {name:"Steve"}
function mypack:greet with storage mypack:tmp input

# Pass values from entity NBT
function mypack:greet with entity @p {}

# Pass value from block NBT
function mypack:greet with block 0 64 0 {}
```

---
