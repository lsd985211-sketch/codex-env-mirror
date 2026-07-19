# Scenario, Routing, and Validation Matrix

## Scenario Table

| Cluster | What it covers | First routing choice |
|---|---|---|
| routing/governance | framework rules, layer ownership, handoff policy | `global-framework` |
| memory/knowledge | memory writes, recall, durable learning, checkpoints | `self-improvement` / `memory-checkpoint-ops` |
| GUI/desktop | native app windows, file pickers, desktop controls | `gui-automation` + app-specific GUI skill |
| bridge/mobile | Weixin/OpenClaw delivery, supplements, queue recovery | `mobile-weixin-bridge-ops` |
| research/docs | docs, web references, manifests, external sources | `find-docs` / `context7-*` / project doc skills |
| content/media | writing, summarization, publishing, images, slides | domain writing/image/presentation skills |
| code/tools | code change, scripts, repo repair, toolchain checks | domain/project skill or `diagnose` |

## Routing Matrix

| Symptom | Owning layer | Next handoff |
|---|---|---|
| trigger ambiguity | routing | choose smallest useful skill set |
| state drift / regression | execution + governance | diagnose root cause before patching |
| cross-layer conflict | governance | project rules override global defaults |
| memory needed | memory/knowledge | quick-pass first, then deeper lookup only if needed |
| GUI delivery issue | bridge/GUI | preserve live state and avoid brittle automation |

## Validation Matrix

| Change type | Quick check | Deeper check |
|---|---|---|
| routing text change | confirm owning layer and handoff still clear | test against realistic prompts |
| new skill boundary | check overlap and trigger drift | compare with nearby skills |
| maintenance rule change | verify read-only/controlled interfaces remain intact | run the maintenance chain |
| scenario table change | confirm cluster names still map cleanly | review a few representative tasks |

## Use Rule

- Keep this file read-only unless the framework itself changes.
- Update the table when new stable clusters or handoff rules appear.
- Do not expand it into skill bodies.
