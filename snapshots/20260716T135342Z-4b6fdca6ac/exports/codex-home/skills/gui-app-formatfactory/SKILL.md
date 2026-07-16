---
name: gui-app-formatfactory
description: App-specific GUI automation guidance for FormatFactory on Windows. Use when Codex needs to inspect, operate, reverse-engineer, or automate FormatFactory conversion workflows, especially when CLI/profile values are unknown and the GUI must be used to discover task/profile behavior safely.
---

# FormatFactory GUI

## Scope

Use this skill for FormatFactory GUI work only. Prefer direct CLI or documented profile formats if they are confirmed. Use GUI automation when the task requires discovering saved task/profile formats, creating a conversion job, or verifying conversion behavior.

## Entry Conditions

Before operating:

1. Check whether FormatFactory is already running.
2. Bind the main window by process name and title pattern.
3. Capture a screenshot and UIA tree.
4. Pause if a first-run wizard, update prompt, login, installer, or permission dialog appears.

Do not open extra visible windows unless the user has approved a GUI test.

## Discovery Workflow

For reverse-engineering profiles or task files:

1. Snapshot candidate config locations and registry keys before GUI changes.
2. Create one minimal conversion job, such as WAV to MP3, through the GUI.
3. Save or queue the job without starting unrelated work.
4. Snapshot files and registry again.
5. Compare only bounded locations related to FormatFactory.
6. Record discovered profile names, method names, task serialization, and UI selectors without private source file content.

## Automation Rules

- Prefer UIA controls by name, AutomationId, and control type.
- Use OCR only for controls not exposed through UIA.
- Use coordinates only after screenshot evidence confirms layout.
- Verify conversion by output file existence, nonzero size, and expected extension.
- If the GUI reports success but output is absent, treat it as failure and preserve evidence.

## Known Current State

The FormatFactory CLI/profile interface has not been fully confirmed. Do not blindly guess `Method_Name` or `Profile_Name`. Use GUI-created tasks or saved profiles as the source of truth until the profile storage format is verified.

## Experience Capture

After each FormatFactory GUI run, use `gui-skill-evolution` to classify the lesson:

- Put general GUI lessons in `gui-automation`.
- Put FormatFactory selectors, profile names, task-file formats, and conversion-specific recovery in this skill.

Strip private filenames and media contents from all notes.

## Preflight

- Confirm FormatFactory is the actual target before binding.
- Capture a screenshot and UIA tree before attempting discovery.
- Treat output existence and nonzero size as the first real success check.

## Output Contract

- Return the discovered task/profile behavior and verification result.
- Mention whether the workflow is verified, candidate, or avoid.
- If the profile format is unknown, say so instead of guessing.
