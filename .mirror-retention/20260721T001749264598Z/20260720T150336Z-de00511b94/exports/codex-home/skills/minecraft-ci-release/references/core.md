# Minecraft CI / Release Skill: core

This reference preserves detailed material moved out of `SKILL.md` for progressive disclosure. Load it only when the task matches these topics.

## Workflow Overview

```
PR opened ??build + test checks
main branch push ??build artifacts
Tag push (v*) ??build + publish to Modrinth + CurseForge + GitHub Releases
```


---

## Versioning Convention

Minecraft mod versions follow: `{mod_version}+{mc_version}`

```
1.0.0+1.21.11  ??mod 1.0.0 for MC 1.21.11
1.2.3+1.21.11
2.0.0+1.21.11
```

Git tag format: `v1.0.0` (mod version only, not MC version in the tag).

---

## Semantic Versioning for Mods

| Change | Version bump |
|--------|-------------|
| New features, no breaking changes | Minor: `1.1.0` |
| Bug fixes only | Patch: `1.0.1` |
| API/config breaking changes | Major: `2.0.0` |
| Minecraft version update | Keep mod version, change `+1.21.11` suffix |
| Pre-release | `1.0.0-beta.1`, `1.0.0-rc.1` |

---

## References

- GitHub Actions: https://docs.github.com/en/actions
- minotaur (Modrinth): https://github.com/modrinth/minotaur
- curseforgegradle: https://github.com/Darkhax-Minecraft/CurseForgeGradle
- softprops/action-gh-release: https://github.com/softprops/action-gh-release
- gradle/actions: https://github.com/gradle/actions
- Modrinth API docs: https://docs.modrinth.com/
