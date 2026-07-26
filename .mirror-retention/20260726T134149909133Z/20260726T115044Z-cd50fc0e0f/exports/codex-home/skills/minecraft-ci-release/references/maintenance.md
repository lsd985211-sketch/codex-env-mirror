# Minecraft CI / Release Skill: maintenance

This reference preserves detailed material moved out of `SKILL.md` for progressive disclosure. Load it only when the task matches these topics.

## CHANGELOG.md Convention

```markdown
# Changelog

## [1.1.0] ??2025-06-01
### Added
- New `/kit` command
- PDC-based kill tracker

### Fixed
- Death message not appearing on Paper 1.21.11

## [1.0.0] ??2025-05-01
### Added
- Initial release
```

Automate CHANGELOG parsing in Gradle (as shown above in modrinth block) by extracting
the section between version headers.

---

## Dependabot Configuration

### `.github/dependabot.yml`
```yaml
version: 2
updates:
  - package-ecosystem: "gradle"
    directory: "/"
    schedule:
      interval: "weekly"
    groups:
      gradle-plugins:
        patterns:
          - "com.gradleup.shadow"
          - "dev.architectury.loom"
          - "com.modrinth.minotaur"
          - "net.darkhax.curseforgegradle"

  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
```

---

## Build Caching Best Practices

```yaml
# In all workflow jobs:
- name: Setup Gradle
  uses: gradle/actions/setup-gradle@v4
  with:
    # Read-only cache on PRs, read-write on main
    cache-read-only: ${{ github.event_name == 'pull_request' }}
    # Cache Minecraft assets (speeds up loom tasks by minutes)
    gradle-home-cache-includes: |
      caches
      notifications
      .gradle/loom-cache
```

---

## Branch Protection + Required Checks

Recommended GitHub branch protection for `main`:
- Require status checks: `build (fabric)`, `build (neoforge)`, `test`
- Require linear history (squash/rebase merges)
- Require signed commits (optional but recommended for release workflows)

---

## Tag and Release Script

```bash
#!/usr/bin/env bash
# scripts/release.sh <version>
# Usage: ./scripts/release.sh 1.1.0
set -euo pipefail

VERSION="${1:?Usage: release.sh <version>}"

# Update gradle.properties
sed -i "s/^mod_version=.*/mod_version=${VERSION}/" gradle.properties

# Stage and commit
git add gradle.properties
git commit -m "chore: release v${VERSION}"

# Tag
git tag "v${VERSION}"

echo "Created commit and tag v${VERSION}"
echo "Push with: git push && git push --tags"
```

## Workflow Snippet Validator

Use the bundled validator script to keep `SKILL.md` workflow snippets copy-paste safe:

```bash
# Run from the installed skill directory:
./scripts/validate-workflow-snippets.sh --root .

# Strict mode treats warnings as failures:
./scripts/validate-workflow-snippets.sh --root . --strict
```

The validator is bundled and self-contained. Run it from a copied `.agents/`,
`.codex/`, or `.claude/` `minecraft-ci-release` skill directory without relying
on repo-root `node_modules`.

What it checks:
- YAML snippet structure for workflow-like blocks (`name`, `on`, `jobs`)
- Unresolved placeholder tokens and suspicious glob patterns
- `${{ secrets.* }}` usage stays consistent with secrets documented in this file

---
