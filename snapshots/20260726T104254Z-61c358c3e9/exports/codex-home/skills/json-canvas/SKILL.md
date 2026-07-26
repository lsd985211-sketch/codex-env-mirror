---
name: json-canvas
description: Create and edit JSON Canvas .canvas files containing nodes, edges, groups, and spatial relationships. Use for Obsidian canvases, visual maps, flowcharts, and other JSON Canvas 1.0-compatible artifacts.
metadata: {"codex":{"compatibility":"Follow the current JSON Canvas specification. Preserve unknown fields when editing an existing canvas."}}
---

# JSON Canvas

## Core Contract

- A `.canvas` file is JSON with top-level `nodes` and `edges` arrays.
- Every node and edge needs a stable unique ID.
- Nodes carry position and size; edges reference existing node IDs.
- Preserve unknown fields and existing ordering when making bounded edits.

## Workflow

1. Parse existing JSON before modifying it, or create a new structured object.
2. Validate unique IDs, edge endpoints, node dimensions, and required type-specific fields.
3. Keep related nodes spatially organized and avoid unnecessary coordinate churn.
4. Serialize valid UTF-8 JSON and reopen it for structural validation.

## Progressive Reference

Read `references/full-guide.md` for the complete node, edge, color, grouping, layout, and example catalog. Load only the sections needed by the requested canvas.

## Output Contract

- State the canvas path and counts of nodes, edges, and groups.
- Report broken references or unsupported fields rather than silently dropping them.
