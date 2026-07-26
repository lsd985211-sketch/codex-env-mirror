#!/usr/bin/env python3
"""Pure planning primitives for the governed music library owner.

Ownership: local media inventory, resilient ffprobe tag extraction, structured
correction consumption, sidecar association, deterministic target paths, and
duplicate/collision classification.
Non-goals: network access, device control, file mutation, media transcoding,
metadata rewriting, or deletion.
State behavior: read-only except when the caller explicitly persists the
returned plan through the owner facade.
Caller context: music_library_owner.py.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable


SCHEMA = "music_library_owner.v1"
AUDIO_EXTENSIONS = {".wav", ".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".ape"}
LYRIC_EXTENSIONS = {".lrc"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MANAGED_TOP_LEVEL = {"音乐库", "待确认", "整理记录"}
IGNORED_PARTS = {".thumbnails", "$recycle.bin", "system volume information"}
ALLOWED_DISPOSITIONS = {
    "active",
    "duplicate_candidate",
    "metadata_conflict",
    "suspected_truncated",
    "version_unresolved",
    "orphan_sidecar",
}
ALLOWED_KINDS = {"audio", "lyrics", "image"}
INVALID_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
TAG_RE = re.compile(
    rb'"(?P<key>title|artist|album|album_artist|track)"\s*:\s*(?P<value>"(?:\\.|[^"\\])*")',
    re.IGNORECASE,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_id(*values: object) -> str:
    text = "\x1f".join(str(value) for value in values)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def normalize_key(value: str) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"\s*-\s*[^_]+_www\.[^.]+\.com$", "", text)
    text = re.sub(r"\s*-new$", "", text)
    text = re.sub(r"\s*-\s*副本$", "", text)
    text = re.sub(r"\s*[（(]\s*(?:live|demo|钢琴版)\s*[）)]\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*live\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[\s_：:，,。！？!？（）()\[\]【】‘’'\"“”·.\-]", "", text)
    return text


def clean_source_stem(stem: str) -> str:
    text = re.sub(r"\s*-\s*[^_]+_www\.[^.]+\.com$", "", stem, flags=re.IGNORECASE)
    text = re.sub(r"\s*-new$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*-\s*副本$", "", text, flags=re.IGNORECASE)
    return text.strip()


def sanitize_component(value: str, fallback: str) -> str:
    text = INVALID_PATH_CHARS.sub("_", str(value or "")).strip().rstrip(". ")
    text = re.sub(r"\s+", " ", text)
    if text.casefold() in {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}:
        text = f"_{text}"
    return (text or fallback)[:120]


def normalize_track(value: object) -> str:
    match = re.search(r"\d{1,3}", str(value or ""))
    if not match:
        return ""
    number = int(match.group(0))
    return f"{number:02d}" if 0 < number < 100 else str(number)


def parse_ffprobe_tags(raw: bytes) -> dict[str, str]:
    """Keep the first valid duplicate tag instead of a later mojibake value."""
    values: dict[str, str] = {}
    for match in TAG_RE.finditer(raw):
        key = match.group("key").decode("ascii").lower()
        token = match.group("value").decode("utf-8", errors="replace")
        try:
            value = str(json.loads(token))
        except json.JSONDecodeError:
            continue
        current = values.get(key, "")
        if not current or ("�" in current and "�" not in value):
            values[key] = value
    return values


def find_ffprobe() -> str:
    found = shutil.which("ffprobe")
    if found:
        return found
    candidate = Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Links" / "ffprobe.exe"
    if candidate.is_file():
        return str(candidate)
    raise RuntimeError("ffprobe_not_found")


def probe_audio(path: Path, *, ffprobe: str | None = None) -> dict[str, str]:
    executable = ffprobe or find_ffprobe()
    completed = subprocess.run(
        [
            executable,
            "-v",
            "error",
            "-show_entries",
            "format=duration:format_tags=title,artist,album,album_artist,track",
            "-of",
            "json",
            "--",
            str(path),
        ],
        capture_output=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
    )
    if completed.returncode != 0:
        return {"probe_error": completed.stderr.decode("utf-8", errors="replace")[-800:]}
    tags = parse_ffprobe_tags(completed.stdout)
    try:
        payload = json.loads(completed.stdout.decode("utf-8", errors="replace"))
        duration = ((payload.get("format") or {}).get("duration")) if isinstance(payload, dict) else None
        if duration:
            tags["duration_seconds"] = str(round(float(duration), 3))
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return tags


def load_corrections(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"schema": f"{SCHEMA}.corrections", "files": {}, "album_years": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != f"{SCHEMA}.corrections":
        raise ValueError("invalid_corrections_schema")
    files = payload.get("files")
    years = payload.get("album_years")
    if not isinstance(files, dict) or not isinstance(years, dict):
        raise ValueError("invalid_corrections_fields")
    allowed = {"title", "artist", "album_artist", "album", "year", "track", "version", "disposition", "note", "role"}
    normalized_files: dict[str, dict[str, Any]] = {}
    for relative, values in files.items():
        if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError(f"unsafe_correction_path:{relative}")
        if not isinstance(values, dict) or set(values) - allowed:
            raise ValueError(f"unsupported_correction_fields:{relative}")
        normalized_relative = Path(relative).as_posix()
        if not normalized_relative or normalized_relative in normalized_files:
            raise ValueError(f"duplicate_or_empty_correction_path:{relative}")
        for field, value in values.items():
            if field == "track":
                valid_type = isinstance(value, (str, int)) and not isinstance(value, bool)
            else:
                valid_type = isinstance(value, str)
            if not valid_type or len(str(value)) > (1000 if field == "note" else 300):
                raise ValueError(f"invalid_correction_value:{relative}:{field}")
        disposition = str(values.get("disposition") or "active")
        if disposition not in ALLOWED_DISPOSITIONS:
            raise ValueError(f"invalid_disposition:{relative}:{disposition}")
        normalized_files[normalized_relative] = dict(values)
    normalized_years: dict[str, str] = {}
    for key, value in years.items():
        if not isinstance(key, str) or not re.fullmatch(r"\d{4}", str(value or "")):
            raise ValueError(f"invalid_album_year:{key}")
        normalized_years[key] = str(value)
    return {**payload, "files": normalized_files, "album_years": normalized_years}


def scan_media(root: Path) -> list[Path]:
    result: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0].casefold() in {item.casefold() for item in MANAGED_TOP_LEVEL}:
            continue
        if any(part.casefold() in IGNORED_PARTS for part in relative.parts):
            continue
        if path.suffix.casefold() in AUDIO_EXTENSIONS | LYRIC_EXTENSIONS | IMAGE_EXTENSIONS:
            result.append(path)
    return sorted(result, key=lambda item: str(item).casefold())


def detect_version(stem: str) -> tuple[str, str]:
    patterns = (
        (r"\s*[（(]\s*live\s*[）)]", "Live"),
        (r"\s*[（(]\s*demo\s*[）)]", "Demo"),
        (r"\s*[（(]\s*钢琴版\s*[）)]", "钢琴版"),
        (r"\s*live\s*$", "Live"),
    )
    title = stem
    version = ""
    for pattern, label in patterns:
        if re.search(pattern, title, flags=re.IGNORECASE):
            title = re.sub(pattern, "", title, flags=re.IGNORECASE).strip()
            version = label
            break
    return title, version


def apply_correction(metadata: dict[str, str], correction: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = dict(metadata)
    for key in ("title", "artist", "album_artist", "album", "year", "track", "version", "disposition", "note", "role"):
        if key in correction:
            result[key] = correction[key]
    return result


def album_year(metadata: dict[str, Any], years: dict[str, Any]) -> str:
    explicit = str(metadata.get("year") or "").strip()
    if re.fullmatch(r"\d{4}", explicit):
        return explicit
    artist = str(metadata.get("album_artist") or metadata.get("artist") or "").strip()
    album = str(metadata.get("album") or "").strip()
    value = years.get(f"{artist}|{album}")
    return str(value) if re.fullmatch(r"\d{4}", str(value or "")) else ""


def audio_target(metadata: dict[str, Any], source: Path, root: Path, years: dict[str, Any]) -> tuple[Path, str]:
    disposition = str(metadata.get("disposition") or "active")
    artist = sanitize_component(str(metadata.get("album_artist") or metadata.get("artist") or ""), "未知歌手")
    title_seed = str(metadata.get("title") or clean_source_stem(source.stem))
    title, detected_version = detect_version(title_seed)
    version = sanitize_component(str(metadata.get("version") or detected_version), "") if (metadata.get("version") or detected_version) else ""
    if "副本" in source.stem and disposition == "active":
        disposition = "duplicate_candidate"
    if artist == "未知歌手" and disposition == "active":
        disposition = "metadata_conflict"
    pending_labels = {
        "duplicate_candidate": "重复候选",
        "metadata_conflict": "元数据冲突",
        "suspected_truncated": "疑似截断",
        "version_unresolved": "版本归属待确认",
        "orphan_sidecar": "无对应音频的歌词",
    }
    track = normalize_track(metadata.get("track"))
    title = sanitize_component(title, "未命名音频")
    filename = f"{track} - {title}" if track else title
    if version:
        filename += f" [{version}]"
    filename += source.suffix.casefold()
    if disposition != "active":
        return Path("待确认") / pending_labels[disposition] / "音频" / sanitize_component(artist, "未知歌手") / filename, disposition
    album = sanitize_component(str(metadata.get("album") or ""), "") if metadata.get("album") else ""
    if version.casefold() in {"live", "demo", "钢琴版"}:
        folder = Path("音乐库") / "艺术家" / artist / "现场与特别版本"
    elif album:
        year = album_year(metadata, years)
        folder_name = f"{year} - {album}" if year else album
        folder = Path("音乐库") / "艺术家" / artist / sanitize_component(folder_name, album)
    else:
        folder = Path("音乐库") / "艺术家" / artist / "单曲与合作"
    return folder / filename, disposition


def preferred_duplicate(paths: Iterable[Path]) -> Path:
    return min(
        paths,
        key=lambda path: (
            "副本" in path.stem,
            "_www." in path.stem.casefold(),
            path.name.startswith("."),
            len(path.name),
            path.name.casefold(),
        ),
    )


def _entry(path: Path, root: Path, *, kind: str, sha256: str, size: int) -> dict[str, Any]:
    relative = path.relative_to(root)
    return {
        "item_id": stable_id(relative.as_posix(), size, sha256),
        "kind": kind,
        "source": relative.as_posix(),
        "size_bytes": size,
        "sha256": sha256,
    }


def build_plan(
    root: Path,
    *,
    corrections: dict[str, Any],
    hardware_binding: dict[str, Any],
    metadata_reader: Callable[[Path], dict[str, str]] = probe_audio,
    hash_reader: Callable[[Path], str] = sha256_file,
    inventory_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    correction_rows = corrections.get("files") if isinstance(corrections.get("files"), dict) else {}
    years = corrections.get("album_years") if isinstance(corrections.get("album_years"), dict) else {}
    inventory: list[dict[str, Any]] = []
    if inventory_rows is None:
        for path in scan_media(root):
            inventory.append({"path": path, "sha256": hash_reader(path), "size": path.stat().st_size})
    else:
        for item in inventory_rows:
            if not isinstance(item, dict):
                raise ValueError("inventory_row_not_object")
            path = Path(str(item.get("path") or "")).resolve()
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"inventory_path_outside_root:{path}") from exc
            digest = str(item.get("sha256") or "")
            size = item.get("size")
            if not re.fullmatch(r"[0-9a-f]{64}", digest) or not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise ValueError(f"invalid_inventory_row:{path}")
            inventory.append({"path": path, "sha256": digest, "size": size, "metadata": item.get("metadata", {})})

    entries: list[dict[str, Any]] = []
    audio_rows: list[dict[str, Any]] = []
    audio_targets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    audio_inventory = [item for item in inventory if item["path"].suffix.casefold() in AUDIO_EXTENSIONS]
    for item in audio_inventory:
        path = item["path"]
        relative = path.relative_to(root).as_posix()
        metadata = dict(item.get("metadata") or {}) if isinstance(item.get("metadata"), dict) else metadata_reader(path)
        correction = correction_rows.get(relative) if isinstance(correction_rows.get(relative), dict) else {}
        metadata = apply_correction(metadata, correction)
        target, disposition = audio_target(metadata, path, root, years)
        row = _entry(path, root, kind="audio", sha256=item["sha256"], size=item["size"])
        row.update({"target": target.as_posix(), "disposition": disposition, "metadata": metadata})
        entries.append(row)
        audio_rows.append(row)
        for key in {normalize_key(str(metadata.get("title") or "")), normalize_key(clean_source_stem(path.stem))} - {""}:
            audio_targets[key].append(row)

    lyrics = [item for item in inventory if item["path"].suffix.casefold() in LYRIC_EXTENSIONS]
    lyric_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in lyrics:
        lyric_groups[normalize_key(item["path"].stem)].append(item)
    for key, group in lyric_groups.items():
        candidates = audio_targets.get(key, [])
        active = [row for row in candidates if row.get("disposition") == "active"]
        standard = [
            row
            for row in active
            if not detect_version(str((row.get("metadata") or {}).get("title") or Path(row["source"]).stem))[1]
            and not str((row.get("metadata") or {}).get("version") or "")
        ]
        selected_audio = active[0] if len(active) == 1 else (standard[0] if len(standard) == 1 else None)
        canonical = preferred_duplicate(item["path"] for item in group)
        for item in group:
            path = item["path"]
            row = _entry(path, root, kind="lyrics", sha256=item["sha256"], size=item["size"])
            if selected_audio and path == canonical:
                target = Path(selected_audio["target"]).with_suffix(".lrc")
                disposition = "active"
            elif selected_audio:
                same_content = item["sha256"] == next(value["sha256"] for value in group if value["path"] == canonical)
                bucket = "重复候选" if same_content else "歌词版本冲突"
                target = Path("待确认") / bucket / "歌词" / path.name
                disposition = "duplicate_candidate" if same_content else "metadata_conflict"
            else:
                target = Path("待确认") / "无对应音频的歌词" / path.name
                disposition = "orphan_sidecar"
            row.update({"target": target.as_posix(), "disposition": disposition, "metadata": {}})
            entries.append(row)

    images = [item for item in inventory if item["path"].suffix.casefold() in IMAGE_EXTENSIONS]
    album_targets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    title_targets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in audio_rows:
        if row.get("disposition") != "active":
            continue
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        album = normalize_key(str(metadata.get("album") or ""))
        title = normalize_key(str(metadata.get("title") or ""))
        if album:
            album_targets[album].append(row)
        if title:
            title_targets[title].append(row)
    provisional_images: list[dict[str, Any]] = []
    for item in images:
        path = item["path"]
        relative = path.relative_to(root).as_posix()
        correction = correction_rows.get(relative) if isinstance(correction_rows.get(relative), dict) else {}
        key = normalize_key(str(correction.get("album") or correction.get("title") or path.stem))
        role = str(correction.get("role") or "")
        album_candidates = album_targets.get(key, [])
        title_candidates = title_targets.get(key, [])
        target: Path
        disposition = "active"
        if (role == "album_cover" or album_candidates) and album_candidates:
            parent = Path(album_candidates[0]["target"]).parent
            target = parent / f"cover{path.suffix.casefold()}"
        elif (role == "track_art" or title_candidates) and len(title_candidates) == 1:
            target = Path(title_candidates[0]["target"]).with_suffix(path.suffix.casefold())
        else:
            target = Path("待确认") / "未识别封面" / path.name
            disposition = "orphan_sidecar"
        row = _entry(path, root, kind="image", sha256=item["sha256"], size=item["size"])
        row.update({"target": target.as_posix(), "disposition": disposition, "metadata": correction})
        provisional_images.append(row)

    image_target_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in provisional_images:
        image_target_groups[row["target"].casefold()].append(row)
    for group in image_target_groups.values():
        if len(group) == 1:
            entries.append(group[0])
            continue
        canonical = max(group, key=lambda row: (row["size_bytes"], -len(Path(row["source"]).name)))
        entries.append(canonical)
        for row in group:
            if row is canonical:
                continue
            same_content = row["sha256"] == canonical["sha256"]
            bucket = "重复候选" if same_content else "封面版本冲突"
            row["target"] = (Path("待确认") / bucket / "封面" / Path(row["source"]).name).as_posix()
            row["disposition"] = "duplicate_candidate" if same_content else "metadata_conflict"
            entries.append(row)

    hash_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in entries:
        hash_groups[(row["kind"], row["sha256"])].append(row)
    for group in hash_groups.values():
        if len(group) < 2:
            continue
        canonical_source = preferred_duplicate(root / Path(row["source"]) for row in group)
        for row in group:
            if (root / Path(row["source"])) == canonical_source:
                continue
            if row["disposition"] == "active":
                row["disposition"] = "duplicate_candidate"
                row["target"] = (Path("待确认") / "重复候选" / row["kind"] / Path(row["source"]).name).as_posix()

    target_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in entries:
        target_groups[row["target"].casefold()].append(row)
    for group in target_groups.values():
        if len(group) < 2:
            continue
        for row in sorted(group, key=lambda item: item["source"].casefold()):
            source_name = Path(row["source"]).name
            row["disposition"] = "metadata_conflict"
            row["target"] = (Path("待确认") / "目标路径冲突" / f"{row['item_id'][:8]} - {source_name}").as_posix()

    final_targets: set[str] = set()
    for row in entries:
        key = row["target"].casefold()
        if key in final_targets:
            source_name = Path(row["source"]).name
            row["disposition"] = "metadata_conflict"
            row["target"] = (Path("待确认") / "目标路径冲突" / f"{row['item_id']} - {source_name}").as_posix()
            key = row["target"].casefold()
        final_targets.add(key)

    entries.sort(key=lambda row: (row["kind"], row["source"].casefold()))
    total_bytes = sum(int(row["size_bytes"]) for row in entries)
    correction_digest = hashlib.sha256(
        json.dumps(corrections, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    plan_seed = {
        "root": str(root),
        "hardware": hardware_binding.get("stable_fingerprint"),
        "corrections": correction_digest,
        "items": [(row["item_id"], row["target"]) for row in entries],
    }
    plan_id = hashlib.sha256(json.dumps(plan_seed, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:24]
    dispositions = Counter(str(row["disposition"]) for row in entries)
    kinds = Counter(str(row["kind"]) for row in entries)
    return {
        "schema": f"{SCHEMA}.plan",
        "ok": True,
        "plan_id": plan_id,
        "root": str(root),
        "hardware_binding": hardware_binding,
        "corrections_sha256": correction_digest,
        "source_snapshot": {"file_count": len(entries), "total_bytes": total_bytes, "kind_counts": dict(kinds)},
        "summary": {"entry_count": len(entries), "total_bytes": total_bytes, "disposition_counts": dict(dispositions)},
        "safety": {
            "delete_supported": False,
            "overwrite_supported": False,
            "content_rewrite_supported": False,
            "same_volume_move_only": True,
            "hardware_recheck_before_apply": True,
        },
        "entries": entries,
    }


def calculate_plan_id(plan: dict[str, Any]) -> str:
    binding = plan.get("hardware_binding") if isinstance(plan.get("hardware_binding"), dict) else {}
    entries = plan.get("entries") if isinstance(plan.get("entries"), list) else []
    seed = {
        "root": str(plan.get("root") or ""),
        "hardware": binding.get("stable_fingerprint"),
        "corrections": str(plan.get("corrections_sha256") or ""),
        "items": [
            (str(row.get("item_id") or ""), str(row.get("target") or ""))
            for row in entries
            if isinstance(row, dict)
        ],
    }
    return hashlib.sha256(json.dumps(seed, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:24]


def validate_plan_structure(plan: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if plan.get("schema") != f"{SCHEMA}.plan":
        issues.append({"code": "invalid_plan_schema"})
    root_text = str(plan.get("root") or "")
    root = Path(root_text) if root_text else Path(".")
    entries = plan.get("entries")
    if not isinstance(entries, list):
        return [*issues, {"code": "entries_not_list"}]
    targets: set[str] = set()
    sources: set[str] = set()
    item_ids: set[str] = set()
    for index, row in enumerate(entries):
        if not isinstance(row, dict):
            issues.append({"code": "entry_not_object", "index": index})
            continue
        for field in ("item_id", "kind", "source", "target", "size_bytes", "sha256", "disposition"):
            if field not in row:
                issues.append({"code": "entry_field_missing", "index": index, "field": field})
        for field in ("source", "target"):
            raw_value = str(row.get(field) or "")
            value = Path(raw_value)
            if not raw_value or value.is_absolute() or ".." in value.parts:
                issues.append({"code": "unsafe_relative_path", "index": index, "field": field})
        source_key = str(row.get("source") or "").casefold()
        if source_key in sources:
            issues.append({"code": "duplicate_source", "index": index, "source": row.get("source")})
        sources.add(source_key)
        target_key = str(row.get("target") or "").casefold()
        if target_key in targets:
            issues.append({"code": "duplicate_target", "index": index, "target": row.get("target")})
        targets.add(target_key)
        item_id = str(row.get("item_id") or "")
        if not re.fullmatch(r"[0-9a-f]{24}", item_id) or item_id in item_ids:
            issues.append({"code": "invalid_or_duplicate_item_id", "index": index})
        item_ids.add(item_id)
        if source_key == target_key:
            issues.append({"code": "source_equals_target", "index": index})
        if str(row.get("kind") or "") not in ALLOWED_KINDS:
            issues.append({"code": "invalid_entry_kind", "index": index})
        if str(row.get("disposition") or "") not in ALLOWED_DISPOSITIONS:
            issues.append({"code": "invalid_entry_disposition", "index": index})
        size = row.get("size_bytes")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            issues.append({"code": "invalid_entry_size", "index": index})
        if not re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256") or "")):
            issues.append({"code": "invalid_entry_sha256", "index": index})
    if not root_text or not root.is_absolute():
        issues.append({"code": "root_not_absolute"})
    binding = plan.get("hardware_binding") if isinstance(plan.get("hardware_binding"), dict) else {}
    if not re.fullmatch(r"[0-9a-f]{64}", str(binding.get("stable_fingerprint") or "")):
        issues.append({"code": "invalid_hardware_binding"})
    if not re.fullmatch(r"[0-9a-f]{64}", str(plan.get("corrections_sha256") or "")):
        issues.append({"code": "invalid_corrections_sha256"})
    if str(plan.get("plan_id") or "") != calculate_plan_id(plan):
        issues.append({"code": "plan_id_integrity_failed"})
    return issues
