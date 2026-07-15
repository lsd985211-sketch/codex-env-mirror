# Minecraft CI / Release Skill: publishing

This reference preserves detailed material moved out of `SKILL.md` for progressive disclosure. Load it only when the task matches these topics.

## Modrinth Publishing (minotaur)

### `build.gradle.kts` (root or platform-specific)
```kotlin
plugins {
    id("com.modrinth.minotaur") version "2.8.7"
}

// === Fabric subproject ===
modrinth {
    token.set(System.getenv("MODRINTH_TOKEN") ?: "")
    projectId.set("YOUR-PROJECT-ID")    // from modrinth.com project slug or ID

    versionNumber.set("${project.version}")
    versionType.set("release")          // release | beta | alpha

    uploadFile.set(tasks.remapJar)      // the JAR to upload

    gameVersions.addAll("1.21.11")
    loaders.addAll("fabric")

    changelog.set(
        rootProject.file("CHANGELOG.md").readText()
            .substringAfter("## [${project.version}]")
            .substringBefore("\n## [")
            .trim()
    )

    dependencies {
        required.project("fabric-api")
        // optional.project("some-optional-mod")
    }
}
```

### Combined Fabric + NeoForge publish task (root-level)
```kotlin
// root build.gradle.kts
tasks.register("publishMods") {
    dependsOn(":fabric:modrinth", ":neoforge:modrinth")
    dependsOn(":fabric:curseforge", ":neoforge:curseforge")
    group = "publishing"
    description = "Publish all platforms to Modrinth and CurseForge"
}
```

---

## CurseForge Publishing

### `build.gradle.kts`
```kotlin
plugins {
    id("net.darkhax.curseforgegradle") version "1.1.25"
}

tasks.register<net.darkhax.curseforgegradle.TaskPublishCurseForge>("curseforge") {
    apiToken = System.getenv("CURSEFORGE_TOKEN") ?: ""

    val cf = upload(PROJECT_ID, tasks.named("remapJar"))  // or shadowJar
    cf.changelogType = "markdown"
    cf.changelog = rootProject.file("CHANGELOG.md").readText()
        .substringAfter("## [${project.version}]")
        .substringBefore("\n## [")
        .trim()

    cf.releaseType = "release"
    cf.addGameVersion("1.21.11")
    cf.addModLoader("Fabric")     // "NeoForge" for NeoForge subproject
    cf.addRequirement("fabric-api")
    // cf.addJavaVersion("Java 21")

    // Replace PROJECT_ID with your numeric CurseForge project ID
}
```

> Replace `PROJECT_ID` with your actual numeric CurseForge project ID (found in project settings).

---

## `gradle.properties` Secrets Pattern

Never hardcode tokens. Read them from environment:

```properties
# gradle.properties (committed)
mod_id=mymod
mod_version=1.0.0
minecraft_version=1.21.11
modrinth_project_id=AABBCCDD
curseforge_project_id=123456

# DO NOT commit tokens
# Set these as GitHub repo secrets:
# MODRINTH_TOKEN, CURSEFORGE_TOKEN
```

---
