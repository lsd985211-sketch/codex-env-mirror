---
name: minecraft-multiloader
description: Design and maintain Minecraft mods that target multiple loaders or platforms, including shared code, loader-specific entrypoints, mappings, build configuration, and conditional integrations. Use for Fabric/NeoForge/Forge cross-loader architecture.
metadata: {"codex":{"compatibility":"Loader APIs, mappings, and build plugins are version-sensitive. Inspect the target repository and locked dependency versions before changing architecture."}}
---

# Minecraft Multi-Loader Development

## Workflow

1. Inspect the current modules, Gradle settings, mappings, loader versions, and shared-source strategy.
2. Separate common domain logic from loader lifecycle, registry, networking, configuration, and platform services.
3. Preserve one-way dependencies from loader modules toward common code.
4. Add a platform abstraction only for real loader differences; avoid duplicating shared logic.
5. Build and test every supported loader, not only the module being edited.

## References

Read `references/full-guide.md` for architecture patterns, Gradle layouts, entrypoints, service bridges, networking, data generation, and publishing details. Read other bundled references only for the exact runtime pattern in use.

## Output Contract

- State supported Minecraft/loaders/versions and modules changed.
- Report per-loader build/test results and any intentionally unsupported behavior.
- Do not upgrade mappings or loaders incidentally during an unrelated fix.
