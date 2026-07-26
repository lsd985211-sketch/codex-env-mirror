---
name: gui-skill-evolution
description: GUI automation experience capture and skill evolution workflow. Use after GUI operations, GUI failures, app-specific discoveries, OCR/control matching improvements, or repeated desktop workflows to classify lessons and create or update reusable GUI skills from general GUI practice down to app-specific skills.
---

# GUI Skill Evolution

## Purpose

Convert GUI automation experience into reusable skills without polluting long-term knowledge with one-off accidents. Keep the hierarchy:

- `gui-automation`: general Windows GUI operating rules.
- `gui-skill-evolution`: how to capture and promote GUI lessons.
- `gui-skill-evolution/references/general-*.md`: reusable GUI pattern pool across apps.
- `gui-app-<app-name>`: app-specific workflows and selectors.

## Classification

Classify every GUI lesson into one primary category:

- `window-routing`: process names, window titles, startup timing, rebinding.
- `control-selection`: UIA name, AutomationId, control type, DOM, OCR, icons.
- `input-flow`: keyboard entry, file picker, drag/drop, hotkeys, submit behavior.
- `modal-recovery`: dialogs, permission prompts, login, captcha, disabled controls.
- `output-verification`: file creation, UI state, transfer success, conversion result.
- `stability`: retries, timeouts, checkpoints, stale windows, crashes.
- `app-specific`: facts only valid for one application.

Promote a lesson only when it is reusable, verified, and privacy-safe.

## Promotion Rules

Use this gate before writing a skill:

1. One confirmed success plus one matching repeat, or one severe failure with clear root cause.
2. Clear scope: general GUI, one app, or one workflow in one app.
3. Evidence exists: screenshot path, UIA element summary, command output, or concise trace.
4. No secrets or private content.
5. The lesson changes future behavior.

If a lesson does not pass the gate, record it as a draft note, not as a formal skill rule.

## Mature Flow Policy

A GUI workflow may be marked as a mature fixed flow when it is verified,
repeatable, low-risk between checkpoints, and has clear preconditions and
result verification. Mature fixed flows should batch stable intermediate
actions to reduce tool round trips, while preserving screenshots or state checks
at identity, irreversible-action, and final-result boundaries.

Mature high-frequency workflows may be promoted into high-level tools when the
tool boundary improves speed without hiding risk. The tool must declare its
preconditions, relevant variables, failed-stage reporting, recovery boundary,
and final verification evidence. Prefer parameterized tools over app-locked
tools when the same stable segment applies across apps, such as native file
picker attach/open/save flows.

Before promoting a high-frequency workflow, capture a small baseline: normal
route used, number of tool round trips, recognition source mix (UIA, DOM, OCR,
image, coordinates), and final verification method. The promoted route should
reduce round trips or recognition scope while preserving at least the same
identity and result checks. If the speed gain comes only from skipping checks,
do not promote it.

Recognition precision policy for mature flows:

- Prefer UIA/DOM selectors for identity and action targets.
- Use OCR as scoped region recognition, not full-window recognition, once the
  target area is known.
- Reuse OCR workers and cached UIA trees when the tool supports it.
- Keep coordinate clicks as a last resort and pair them with screenshot or UIA
  verification at the next boundary.

Mature flows should also define their session-lifecycle strategy:

- which parent/main window session may be reused;
- which dialogs or child windows are short-lived and must be discarded after
  completion;
- which verification failures force rebind or recovery;
- which checkpoint proves it is safe to keep using the current session.

When a mature fixed flow fails:

1. Stop the batch immediately.
2. Capture the current screenshot and available UIA/control state.
3. Downgrade to single-step observe-plan-act-verify diagnosis.
4. Identify the failed step and root cause.
5. Update the app skill's success, candidate, failed, or lessons ledger before
   treating the revised flow as mature again.

Never let a failed mature flow keep running on assumptions. A fixed flow is a
speed optimization for verified segments, not permission to skip verification
or recovery.

When a high-level tool fails, demote only the affected workflow variant. Keep
the broader pattern usable if evidence shows the failure was caused by a
relevant variable such as layout, locale, dialog class, target identity, focus,
permission, or disabled state. Record that variable in the app ledger and, when
transferable, in the general candidate/conditional/failed pool.

Do not let a high-level tool failure poison the whole GUI system by default.
Classify the failed segment first: selector drift, OCR miss, focus/window
ownership, timing, modal state, permissions, or final-result mismatch. Demote
only the segment whose evidence failed, then retry through the slower
single-step route.

## General Pattern Pool

Extract reusable GUI patterns from app-specific skills into the general pool before treating them as broadly applicable:

- `references/general-candidates.md`: app-derived patterns that may transfer but are not yet trusted.
- `references/general-trusted.md`: patterns verified across distinct apps or UI surfaces.
- `references/general-conditional.md`: patterns that work only under explicit conditions.
- `references/general-failed-or-avoid.md`: tempting general patterns that failed, are unsafe, or are too brittle.

External skills or repositories may seed this pool only as candidates or
conditional patterns unless they are tested locally. Absorb their transferable
ideas, cite the source type in the entry, and keep OS-specific or tool-specific
details out of general trusted rules until verified in this workspace.

For unfamiliar GUI scenes, try the smallest applicable `general-trusted` pattern first, then `general-conditional` only when its conditions match, then `general-candidates` for exploration. Always verify after each action. Do not use a general pattern when an app-specific verified route exists for the same task.

When a general pattern is tried in a scene:

1. If it works, increase confidence in the general pool and record the scene in the app skill's `verified-success.md` when the app skill exists or should be created.
2. If it fails, record the failure cause in the general pool and in the app skill's `failed-or-avoid.md` when the app skill exists or should be created.
3. If proof is incomplete, keep it in `candidate-unverified.md` for the app and `general-candidates.md` for the general pool.
4. If a single app-specific success looks reusable, abstract it only to `general-candidates.md` unless it already has cross-app evidence.
5. During abstraction, identify relevant and irrelevant variables. Promote only
   the stable core action sequence; keep app-specific selectors, coordinates,
   labels, account names, filenames, and locale assumptions in the app ledger or
   conditional notes.

Each general entry must state the pattern, source app or scene, preconditions, verification proof, known failures or missing proof, and confidence status.

## App Skill Three-Ledger Model

Every app-specific GUI skill should keep `SKILL.md` as a lean router and store detailed workflows in these reference files:

- `references/verified-success.md`: strictly verified workflows that may be used as primary automation routes.
- `references/candidate-unverified.md`: plausible or partially tested workflows that require retesting before use.
- `references/failed-or-avoid.md`: failed, unsafe, misleading, or brittle routes that should not be used unless deliberately retesting.
- `references/lessons.md`: cause-level lessons extracted from verified successes and failures.

Use these promotion and demotion rules:

1. Promote a candidate to `verified-success.md` only after strict evidence proves the outcome, such as UIA/OCR state plus file readback or another durable result check.
2. Keep a flow in `candidate-unverified.md` when it is plausible but lacks complete proof, is version-dependent, or only succeeded through a fragile path.
3. Move a flow to `failed-or-avoid.md` when it repeatedly fails, targets the wrong control, cannot be verified, risks destructive behavior, or depends on unstable coordinates.
4. Update `lessons.md` after meaningful success or failure by recording the cause, not just the step list.
5. Do not let unverified or failed flows appear as callable primary routes in `SKILL.md`.

After GUI work, summarize reusable causes from `verified-success.md` and `failed-or-avoid.md` into the memory system when the user has explicitly asked to record experience or the active project rules require it. Do not store private filenames, raw screenshots, account data, or full transcripts.

When app-specific evidence reveals a transferable pattern, also abstract it into the general pool with conservative confidence. A single app success may create or update a general candidate, but must not directly promote to `general-trusted.md`.

## Write Policy

Follow the active project rules:

- Ask before modifying local skill files unless the user already approved the current update.
- Back up existing skill files before changing them.
- Use `apply_patch` for manual edits.
- Validate with `skill-creator/scripts/quick_validate.py`.

When creating app-specific skills, name them with lowercase hyphenated app names, such as `gui-app-formatfactory`, `gui-app-codex-desktop`, or `gui-app-openclaw-login`.

## Helper Script

Use `scripts/gui_experience_to_skill.py` to turn a concise experience record into a draft skill update.

Default behavior is dry-run. Use `--apply` only after confirming the change is safe and a backup exists. Use `--auto-abstract-general` when an app-specific result should also produce a conservative general pool entry.

Example:

```powershell
& 'C:\Python314\python.exe' 'C:\Users\45543\.codex\skills\gui-skill-evolution\scripts\gui_experience_to_skill.py' `
  --app FormatFactory `
  --task "WAV to MP3 conversion through GUI" `
  --outcome success `
  --category input-flow `
  --lesson "Use UIA controls before OCR; verify output file exists before closing." `
  --evidence "screenshot path redacted"
```

To record an app-specific result and automatically abstract a general candidate:

```powershell
& 'C:\Python314\python.exe' 'C:\Users\45543\.codex\skills\gui-skill-evolution\scripts\gui_experience_to_skill.py' `
  --app Notepad `
  --task "Open an existing file by launch argument" `
  --outcome success `
  --category input-flow `
  --lesson "Launching the app with an absolute path argument can bypass fragile file-picker GUI paths." `
  --ledger verified-success `
  --verification "window title and disk readback matched expected file" `
  --general-pattern "Open a file by launching the app with an absolute path argument when the app supports it." `
  --general-conditions "The app accepts file paths on its command line and exposes verifiable title or content state." `
  --auto-abstract-general
```

## Skill Content Shape

App-specific skills should contain:

- Entry conditions in `SKILL.md`: executable, process name, and window title patterns.
- A fast route map in `SKILL.md` that points to the smallest relevant reference file.
- Stable selectors in reference files: UIA names/AutomationIds first, OCR text second, coordinates last.
- Known workflows in `verified-success.md` with verification after each stage.
- Candidate workflows in `candidate-unverified.md` with missing proof clearly stated.
- Failure handling and forbidden shortcuts in `failed-or-avoid.md`.
- Verification standards and cause summaries in `lessons.md`.
- General abstractions recorded in the general pool references when app evidence reveals a transferable pattern.

Do not include raw screenshots, private filenames, credentials, account names, or full transcripts.

## Preflight

- Confirm the GUI lesson is reusable and verified before promoting it.
- Classify the lesson into one primary category.
- Keep app-specific facts out of the general pool until they are proven transferable.

## Output Contract

- Return the category, evidence type, and promotion target.
- State whether the lesson belongs in verified-success, candidate, failed, or general pool.
- If the evidence is incomplete, keep it as a draft.
