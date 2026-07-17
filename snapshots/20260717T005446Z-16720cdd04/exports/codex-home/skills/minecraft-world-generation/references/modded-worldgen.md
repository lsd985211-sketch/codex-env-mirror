# Minecraft World Generation Skill: modded-worldgen

This reference preserves detailed material moved out of `SKILL.md` for progressive disclosure. Load it only when the task matches these topics.

## NeoForge: Biome Modifier

Biome Modifiers let you add features, spawns, or carvers to existing biomes without
replacing the biome JSON.

### JSON biome modifier (`data/<namespace>/neoforge/biome_modifier/add_ores.json`)
```json
{
  "type": "neoforge:add_features",
  "biomes": "#minecraft:is_overworld",
  "features": "<namespace>:my_ore_placed",
  "step": "underground_ores"
}
```

### Other NeoForge biome modifier types
```json
{ "type": "neoforge:add_spawns", "biomes": "#minecraft:is_forest",
  "spawners": [{ "type": "minecraft:wolf", "weight": 5, "minCount": 2, "maxCount": 4 }] }

{ "type": "neoforge:remove_features", "biomes": "#minecraft:is_plains",
  "features": "minecraft:ore_coal_upper", "steps": ["underground_ores"] }

{ "type": "neoforge:remove_spawns", "biomes": "#minecraft:is_ocean",
  "entity_types": "#minecraft:skeletons" }
```

---

## Fabric: BiomeModification API (Code)

```java
import net.fabricmc.fabric.api.biome.v1.BiomeModifications;
import net.fabricmc.fabric.api.biome.v1.BiomeSelectors;
import net.minecraft.world.level.levelgen.GenerationStep;

public class MyModWorldgen {
    public static void init() {
        // Add a placed feature to all overworld biomes
        BiomeModifications.addFeature(
            BiomeSelectors.foundInOverworld(),
            GenerationStep.Decoration.UNDERGROUND_ORES,
            ResourceKey.create(
                Registries.PLACED_FEATURE,
                ResourceLocation.fromNamespaceAndPath(MyMod.MOD_ID, "my_ore_placed")
            )
        );

        // Add mob spawns
        BiomeModifications.addSpawn(
            BiomeSelectors.tag(BiomeTags.IS_FOREST),
            MobCategory.CREATURE,
            EntityType.WOLF,
            5, 2, 4
        );
    }
}
```

---

## Mod-Registered Worldgen (NeoForge + Fabric via Datagen)

### Register worldgen keys in code
```java
// In a dedicated worldgen registry class
public class ModWorldgen {
    public static final ResourceKey<Biome> MY_BIOME = ResourceKey.create(
        Registries.BIOME,
        ResourceLocation.fromNamespaceAndPath(MyMod.MOD_ID, "my_biome")
    );

    public static final ResourceKey<PlacedFeature> MY_ORE_PLACED = ResourceKey.create(
        Registries.PLACED_FEATURE,
        ResourceLocation.fromNamespaceAndPath(MyMod.MOD_ID, "my_ore_placed")
    );
}
```

### Datagen: NeoForge (`DatapackBuiltinEntriesProvider`)

```java
public class ModWorldgenProvider extends DatapackBuiltinEntriesProvider {

    private static final RegistrySetBuilder BUILDER = new RegistrySetBuilder()
        .add(Registries.CONFIGURED_FEATURE, ModWorldgenProvider::bootstrapConfigured)
        .add(Registries.PLACED_FEATURE, ModWorldgenProvider::bootstrapPlaced);

    public ModWorldgenProvider(PackOutput output, CompletableFuture<HolderLookup.Provider> registries) {
        super(output, registries, BUILDER, Set.of(MyMod.MOD_ID));
    }

    private static void bootstrapConfigured(BootstrapContext<ConfiguredFeature<?, ?>> ctx) {
        ctx.register(
            ModWorldgen.MY_ORE_CONFIGURED,
            new ConfiguredFeature<>(Feature.ORE, new OreConfiguration(
                OreConfiguration.target(
                    new TagMatchTest(BlockTags.STONE_ORE_REPLACEABLES),
                    ModBlocks.MY_ORE.get().defaultBlockState()
                ),
                9  // vein size
            ))
        );
    }

    private static void bootstrapPlaced(BootstrapContext<PlacedFeature> ctx) {
        HolderGetter<ConfiguredFeature<?, ?>> configured =
            ctx.lookup(Registries.CONFIGURED_FEATURE);
        ctx.register(
            ModWorldgen.MY_ORE_PLACED,
            new PlacedFeature(
                configured.getOrThrow(ModWorldgen.MY_ORE_CONFIGURED),
                List.of(
                    HeightRangePlacement.triangle(
                        VerticalAnchor.absolute(-64),
                        VerticalAnchor.absolute(32)
                    ),
                    CountPlacement.of(8),
                    InSquarePlacement.spread(),
                    BiomeFilter.biome()
                )
            )
        );
    }
}
```

Register in your `GatherDataEvent` handler:
```java
@SubscribeEvent
public static void onGatherData(GatherDataEvent event) {
    DataGenerator gen = event.getGenerator();
    PackOutput output = gen.getPackOutput();
    gen.addProvider(event.includeServer(),
        new ModWorldgenProvider(output, event.getLookupProvider()));
}
```

### Datagen: Fabric (`FabricDynamicRegistryProvider`)

```java
public class ModWorldgenProvider extends FabricDynamicRegistryProvider {

    public ModWorldgenProvider(FabricDataOutput output, CompletableFuture<HolderLookup.Provider> registries) {
        super(output, registries);
    }

    @Override
    protected void configure(HolderLookup.Provider registries, Entries entries) {
        entries.addAll(registries.lookupOrThrow(Registries.CONFIGURED_FEATURE));
        entries.addAll(registries.lookupOrThrow(Registries.PLACED_FEATURE));
    }

    @Override
    public String getName() {
        return "Worldgen";
    }
}
```

---
