---
name: minecraft-commands-scripting
description: Design and validate Minecraft commands, selectors, execute chains, scoreboards, NBT/data operations, command blocks, and mcfunction scripts. Use for command-system logic rather than Java mod or plugin implementation.
metadata: {"codex":{"compatibility":"Command syntax is version-sensitive. Confirm the target Minecraft edition and version before generating commands."}}
---

# Minecraft Commands And Scripting

## Workflow

1. Confirm Java or Bedrock edition and exact game version.
2. Define the execution context, target selectors, coordinates, permissions, and expected state change.
3. Build the smallest command chain and isolate scoreboard, storage, or NBT state explicitly.
4. For multi-step logic, use `.mcfunction` files with deterministic ordering and comments only where needed.
5. Validate selectors, execute context, objective existence, NBT paths, and version-specific syntax.

## References

- Read `references/full-guide.md` for the complete selector, execute, scoreboard, NBT, text-component, command-block, and scripting catalog.
- Use `references/execute-cheat-sheet.md` or `references/selector-cheat-sheet.md` for narrow questions.
- Reuse bundled examples only after adapting version, coordinates, and objective names.

## Output Contract

- State edition/version assumptions and installation path for generated functions.
- Explain required objectives, tags, storage, permissions, and cleanup commands.
- Do not claim a command was tested unless it ran in the target environment or a compatible validator.
