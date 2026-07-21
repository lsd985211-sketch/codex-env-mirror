# Minecraft CI / Release Skill: github-actions

This reference preserves detailed material moved out of `SKILL.md` for progressive disclosure. Load it only when the task matches these topics.

## Core CI Workflow (NeoForge + Fabric)

### `.github/workflows/build.yml`
```yaml
name: Build

on:
  push:
    branches: ["main", "develop"]
  pull_request:
    branches: ["main"]

permissions:
  contents: read

jobs:
  build:
    name: Build (${{ matrix.platform }})
    runs-on: ubuntu-latest
    strategy:
      matrix:
        platform: [neoforge, fabric]
      fail-fast: false

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Java 21
        uses: actions/setup-java@v4
        with:
          java-version: "21"
          distribution: "temurin"

      - name: Setup Gradle
        uses: gradle/actions/setup-gradle@v4
        with:
          cache-read-only: ${{ github.ref != 'refs/heads/main' }}

      - name: Grant execute permission for gradlew
        run: chmod +x gradlew

      - name: Build (${{ matrix.platform }})
        run: ./gradlew :${{ matrix.platform }}:build --no-daemon

      - name: Upload artifacts
        uses: actions/upload-artifact@v4
        with:
          name: mod-${{ matrix.platform }}-${{ github.sha }}
          path: ${{ matrix.platform }}/build/libs/*.jar
          if-no-files-found: error
```

---

## Release Workflow (with Publishing)

### `.github/workflows/release.yml`
```yaml
name: Release

on:
  push:
    tags:
      - "v*"

permissions:
  contents: write      # for creating GitHub releases

jobs:
  release:
    name: Release
    runs-on: ubuntu-latest

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Java 21
        uses: actions/setup-java@v4
        with:
          java-version: "21"
          distribution: "temurin"

      - name: Setup Gradle
        uses: gradle/actions/setup-gradle@v4

      - name: Grant execute permission for gradlew
        run: chmod +x gradlew

      - name: Extract version from tag
        id: version
        run: echo "MOD_VERSION=${GITHUB_REF_NAME#v}" >> $GITHUB_OUTPUT

      - name: Build all platforms
        run: ./gradlew build --no-daemon

      - name: Publish to Modrinth & CurseForge
        run: ./gradlew publishMods --no-daemon
        env:
          MODRINTH_TOKEN: ${{ secrets.MODRINTH_TOKEN }}
          CURSEFORGE_TOKEN: ${{ secrets.CURSEFORGE_TOKEN }}

      - name: Create GitHub Release
        uses: softprops/action-gh-release@v2
        with:
          files: |
            fabric/build/libs/*.jar
            neoforge/build/libs/*.jar
          generate_release_notes: true
          draft: false
          prerelease: ${{ contains(github.ref_name, '-alpha') || contains(github.ref_name, '-beta') || contains(github.ref_name, '-rc') }}
```

---

## Paper Plugin CI

### `.github/workflows/build.yml` (plugin)
```yaml
name: Build

on:
  push:
    branches: ["main"]
  pull_request:

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-java@v4
        with:
          java-version: "21"
          distribution: "temurin"
      - uses: gradle/actions/setup-gradle@v4
      - run: chmod +x gradlew
      - run: ./gradlew shadowJar --no-daemon
      - uses: actions/upload-artifact@v4
        with:
          name: plugin-${{ github.sha }}
          path: build/libs/*.jar

  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-java@v4
        with:
          java-version: "21"
          distribution: "temurin"
      - uses: gradle/actions/setup-gradle@v4
      - run: ./gradlew test --no-daemon
```

---
