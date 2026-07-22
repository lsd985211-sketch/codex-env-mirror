---
name: minecraft-plugin-dev
description: Build and maintain Java Minecraft server plugins for Paper, Bukkit, or Spigot, including commands, events, configuration, scheduling, persistence, permissions, and plugin packaging. Use for server plugins, not client mods or datapacks.
metadata: {"codex":{"compatibility":"Confirm server API and Minecraft versions. Prefer Paper APIs when the target server is Paper, while preserving declared Bukkit/Spigot compatibility."}}
---

# Minecraft Plugin Development

## Workflow

1. Inspect build files, plugin descriptor, package layout, target Java version, and server API.
2. Identify the owning command, listener, service, scheduler, configuration, or persistence component.
3. Keep event handlers small and move reusable behavior into testable services.
4. Avoid blocking I/O on the server thread and respect synchronous API boundaries.
5. Build the plugin, validate its descriptor/resources, and run the smallest available server or test harness.

## References

Read `references/full-guide.md` for commands, listeners, schedulers, inventories, persistence, Adventure, configuration, Vault, and Paper-specific patterns. Use `references/runtime-patterns.md` for focused runtime guidance.

## Output Contract

- State target API/version, Java version, files changed, and produced JAR path.
- Report build, descriptor, and runtime/test evidence.
- Call out main-thread, permission, persistence, and compatibility risks.
