---
name: minecraft-ci-release
description: "Minecraft mod CI/CD, Gradle, GitHub Actions, packaging, and Modrinth/CurseForge release automation."
---

# Minecraft CI / Release Skill

Use this skill for Minecraft mod and plugin CI/CD: Gradle builds, GitHub Actions workflows, release publishing, Modrinth/CurseForge deployment, versioning, changelogs, Dependabot, build caching, and workflow validation.

## Operating Rules

- Identify whether the target is Fabric, NeoForge, multiloader, or Paper before choosing workflow snippets.
- Load only the reference file matching the CI, publishing, or maintenance task.
- Keep tokens and publishing credentials out of committed files; prefer GitHub Actions secrets and local untracked properties.
- Use bundled workflow validators before treating generated YAML as ready.
- For project-specific build scripts or release constraints, inspect the repository before applying generic snippets.
- Keep this `SKILL.md` as a router. Do not paste detailed CI templates into the response unless the user asks for them.

## Reference Routing

| Task | Read |
|---|---|
| Versioning conventions, release flow overview, semantic versioning | `references/core.md` |
| GitHub Actions build/release workflows for Fabric, NeoForge, or Paper | `references/github-actions.md` |
| Modrinth, CurseForge, publishing tokens, Gradle secret patterns | `references/publishing.md` |
| changelog examples, Dependabot, caching, branch protection, tag scripts, workflow validation | `references/maintenance.md` |

## Validation

- Run the bundled `scripts/validate-workflow-snippets.sh` when checking generated workflow YAML snippets.
- Validate Gradle task names and module paths against the target repository before finalizing CI commands.
- Verify publishing credentials are referenced as secrets and are not committed in generated examples.

## Reference Inventory

- `references/core.md`: Workflow Overview; Versioning Convention; Semantic Versioning for Mods; References
- `references/github-actions.md`: Core CI Workflow (NeoForge + Fabric); Release Workflow (with Publishing); Paper Plugin CI
- `references/publishing.md`: Modrinth Publishing (minotaur); CurseForge Publishing; `gradle.properties` Secrets Pattern
- `references/maintenance.md`: CHANGELOG.md Convention; [1.1.0] ??2025-06-01; [1.0.0] ??2025-05-01; Dependabot Configuration; Build Caching Best Practices; Branch Protection + Required Checks; Tag and Release Script; Workflow Snippet Validator

## Preflight
- Confirm the skill matches the task before using it.
- Keep the scope tight and avoid unrelated changes.
## Output Contract
- State the result, blockers, and any key tradeoffs.
- Keep it concise and actionable.
