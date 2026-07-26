---
name: gui-app-notepad
description: Windows Notepad GUI automation guidance. Use when Codex needs to open Notepad, open a file in Notepad, create a new Notepad window/page/tab/document, type requested text, insert date/time, use Notepad find or replace, save content through native Notepad menus, use clipboard editing, undo or redo changes, apply verified Markdown bold formatting, verify Notepad content, or recover from focus stealing between Codex Desktop and Notepad during Windows GUI automation.
---

# Notepad GUI Automation

## Operating Rule

Use `gui-automation` first. For Notepad-specific behavior, load only the smallest relevant reference:

- Verified workflows: `references/verified-success.md`
- Plausible but not yet strict enough workflows: `references/candidate-unverified.md`
- Known unsafe or misleading routes: `references/failed-or-avoid.md`
- Cross-workflow causes and promotion rules: `references/lessons.md`

Only verified workflows may be used as primary automation routes. Candidate workflows are for exploration only. Failed/avoid workflows should not be used except when deliberately retesting.

## Entry Conditions

- Executable: `notepad.exe`
- Process names: `Notepad.exe`, sometimes `notepad.exe`
- Window title patterns: `Notepad`, `记事本`, `无标题 - Notepad`, `无标题 - 记事本`

## Core Safety Rules

1. Use temporary files or explicit user-approved files for tests.
2. Back up existing files before modifying them.
3. Keep activation, focus, and typing/clicking in one bounded action for foreground-sensitive operations.
4. Verify foreground title contains `Notepad` or `记事本` before typing, hotkeys, or menu actions.
5. Prefer UIA exact names and AutomationIds scoped to the bound Notepad window.
6. Treat coordinates as a last resort after screenshot evidence.
7. Never accept overwrite, close unsaved tabs, print, or change settings unless the user explicitly asks.
8. A click is not success. Verify through UIA/OCR, clipboard readback, window-title changes, or disk readback.

## Fast Route Map

- Open an existing file: use `notepad.exe <absolute_path>` from `verified-success.md`.
- New blank top-level window: use plain `notepad.exe` from `verified-success.md`.
- New blank tab: use `AddButton` / `添加新标签页` from `verified-success.md`.
- Type text: follow safe typing in `verified-success.md`.
- Save named file: use native `文件 > 保存` from `verified-success.md`.
- Find/replace: use `Find`, `Replace All`, and exact close-panel workflows in `verified-success.md`.
- Clipboard edit or undo/redo: use `Clipboard Editing` and `Undo Redo` in `verified-success.md`.
- Markdown bold: use only the disk-verified bold workflow in `verified-success.md`.
- Date/time insertion: use the `F5` workflow in `verified-success.md`.

## Before Retesting A Failed Feature

Read both:

- `references/candidate-unverified.md`
- `references/failed-or-avoid.md`

Retest in a temporary file/window, constrain selectors to the target Notepad rectangle, and promote only after result proof exists.

## Preflight

- Confirm the target window is Notepad and the file action is safe.
- Back up any file you might modify before touching content.
- Prefer verified-success routes only.

## Output Contract

- Return the file path, action, and verification evidence.
- State whether the route came from verified-success, candidate, or failed/avoid.
- Mention if the file was opened, changed, or only inspected.
