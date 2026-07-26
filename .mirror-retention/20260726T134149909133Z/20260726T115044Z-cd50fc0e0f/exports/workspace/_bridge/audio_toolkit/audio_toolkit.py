#!/usr/bin/env python3
"""Audio file toolkit for local Codex workflows.

This module intentionally focuses on audio files. Live Windows playback control
is documented in README.md and should stay evidence-driven because player
windows and audio sessions vary by application.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AUDIO_ASSET_ROOT_ENV = "CODEX_AUDIO_ASSET_ROOT"
DEFAULT_ASR_SAMPLE_RATE = 16000
HEAVY_COMMANDS = {"transcribe-zh", "lyrics-fast-zh", "lyrics-draft-zh", "lyrics-ultra-zh", "lyrics-align-zh"}
DEMUCS_MODEL_NAME = "htdemucs"
DEMUCS_ULTRA_MODEL_NAME = "htdemucs_ft"
DEMUCS_MODEL_FILE = "955717e8-8726e21a.th"
DEMUCS_MODEL_SHA256 = "8726e21a993978c7ba086d3872e7608d7d5bfca646ca4aca459ffda844faa8b4"
HEAVY_REROUTE_ENV = "AUDIO_TOOLKIT_HEAVY_REROUTED"


def default_asset_root() -> Path:
    """Return the non-versioned asset authority instead of the compatibility tree."""

    configured = str(os.environ.get(AUDIO_ASSET_ROOT_ENV) or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "Codex" / "audio"
    return Path.home() / ".local" / "share" / "codex" / "audio"


def default_work_root() -> Path:
    return default_asset_root() / "work"


def default_model_root() -> Path:
    return default_asset_root() / "models"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_stem(path: Path) -> str:
    value = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in path.stem)
    return value[:80] or "audio"


def cache_dir_for(source: Path, work_root: Path, category: str) -> Path:
    return work_root / category / sha256_file(source)


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        local_app_data = os.environ.get("LOCALAPPDATA")
        candidates: list[Path] = []
        if local_app_data:
            candidates.append(Path(local_app_data) / "Microsoft" / "WinGet" / "Links" / f"{name}.exe")
            packages = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
            if packages.exists():
                candidates.extend(packages.glob(f"**/{name}.exe"))
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        raise SystemExit(f"Required tool not found on PATH or WinGet links: {name}")
    return path


def module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def preferred_audio_python() -> Path | None:
    candidates = [
        default_asset_root() / "venv" / "Scripts" / "python.exe",
        default_asset_root() / "venv" / "bin" / "python",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return str(left).lower() == str(right).lower()


def heavy_command_missing_modules(command: str) -> list[str]:
    required = ["funasr"]
    if command in {"lyrics-draft-zh", "lyrics-ultra-zh"}:
        required.extend(["demucs", "torch", "soundfile"])
    return [name for name in required if not module_available(name)]


def maybe_reroute_heavy_command(command: str, argv: list[str]) -> None:
    if command not in HEAVY_COMMANDS or os.environ.get(HEAVY_REROUTE_ENV):
        return
    missing = heavy_command_missing_modules(command)
    if not missing:
        return
    target = preferred_audio_python()
    if not target or same_path(Path(sys.executable), target):
        return
    env = os.environ.copy()
    env[HEAVY_REROUTE_ENV] = "1"
    env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.run([str(target), str(Path(__file__).resolve()), *argv], env=env)
    raise SystemExit(proc.returncode)


def run_json(command: list[str]) -> dict[str, Any]:
    proc = subprocess.run(command, text=True, capture_output=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise SystemExit(
            json.dumps(
                {
                    "ok": False,
                    "command": command,
                    "returncode": proc.returncode,
                    "stderr": proc.stderr[-4000:],
                },
                ensure_ascii=False,
            )
        )
    return json.loads(proc.stdout)


def run_command(command: list[str], env: dict[str, str] | None = None) -> None:
    proc = subprocess.run(command, text=True, capture_output=True, encoding="utf-8", errors="replace", env=env)
    if proc.returncode != 0:
        raise SystemExit(
            json.dumps(
                {
                    "ok": False,
                    "command": command,
                    "returncode": proc.returncode,
                    "stdout": proc.stdout[-2000:],
                    "stderr": proc.stderr[-4000:],
                },
                ensure_ascii=False,
            )
        )


def ensure_input(path: str) -> Path:
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise SystemExit(f"Input file not found: {p}")
    return p


def ensure_output(path: str, overwrite: bool) -> Path:
    p = Path(path).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() and not overwrite:
        raise SystemExit(f"Output already exists; pass --overwrite: {p}")
    return p


def ensure_work_root(path: str | None) -> Path:
    root = Path(path).expanduser().resolve() if path else default_work_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def ffprobe_data(source: Path) -> dict[str, Any]:
    ffprobe = require_tool("ffprobe")
    return run_json(
        [
            ffprobe,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(source),
        ]
    )


def audio_duration_seconds(source: Path) -> float | None:
    try:
        data = ffprobe_data(source)
        duration = data.get("format", {}).get("duration")
        return float(duration) if duration not in (None, "") else None
    except Exception:
        return None


def default_asr_wav_path(source: Path, work_root: Path) -> Path:
    return cache_dir_for(source, work_root, "asr-cache") / "asr-16k-mono.wav"


def convert_to_asr_wav(source: Path, output: Path, overwrite: bool) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and not overwrite:
        return {
            "ok": True,
            "cache_hit": True,
            "input": str(source),
            "output": str(output),
            "sha256": sha256_file(output),
        }
    ffmpeg = require_tool("ffmpeg")
    command = [
        ffmpeg,
        "-hide_banner",
        "-y" if overwrite else "-n",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(DEFAULT_ASR_SAMPLE_RATE),
        "-c:a",
        "pcm_s16le",
        str(output),
    ]
    run_command(command)
    return {
        "ok": True,
        "cache_hit": False,
        "input": str(source),
        "output": str(output),
        "sample_rate": DEFAULT_ASR_SAMPLE_RATE,
        "channels": 1,
        "sha256": sha256_file(output),
    }


def local_model_path(name: str) -> Path | None:
    hub = Path.home() / ".cache" / "modelscope" / "hub" / "models" / "iic"
    path = hub / name
    return path if path.exists() else None


def funasr_model_kwargs() -> dict[str, Any]:
    asr = local_model_path("speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch")
    vad = local_model_path("speech_fsmn_vad_zh-cn-16k-common-pytorch")
    punc = local_model_path("punc_ct-transformer_cn-en-common-vocab471067-large")
    return {
        "model": str(asr) if asr else "paraformer-zh",
        "vad_model": str(vad) if vad else "fsmn-vad",
        "punc_model": str(punc) if punc else "ct-punc",
        "disable_update": True,
    }


def extract_funasr_text(result: Any) -> str:
    if isinstance(result, list):
        parts = []
        for item in result:
            if isinstance(item, dict) and item.get("text"):
                parts.append(str(item.get("text") or "").strip())
        return "\n".join(part for part in parts if part)
    if isinstance(result, dict) and result.get("text"):
        return str(result.get("text") or "").strip()
    return ""


def transcribe_zh(source: Path, output: Path, work_root: Path, overwrite: bool, hotword: str = "") -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    json_output = output.with_suffix(output.suffix + ".json")
    if output.exists() and json_output.exists() and not overwrite:
        return {
            "ok": True,
            "cache_hit": True,
            "input": str(source),
            "output": str(output),
            "json_output": str(json_output),
            "text": output.read_text(encoding="utf-8", errors="replace"),
        }
    wav = default_asr_wav_path(source, work_root)
    convert_info = convert_to_asr_wav(source, wav, overwrite=False)
    try:
        from funasr import AutoModel  # type: ignore
    except Exception as exc:
        raise SystemExit(f"FunASR import failed: {exc}") from exc
    model = AutoModel(**funasr_model_kwargs())
    generate_kwargs: dict[str, Any] = {"input": str(wav), "batch_size_s": 300}
    if hotword.strip():
        generate_kwargs["hotword"] = hotword.strip()
    result = model.generate(**generate_kwargs)
    text = extract_funasr_text(result)
    output.write_text(text + ("\n" if text and not text.endswith("\n") else ""), encoding="utf-8")
    json_output.write_text(
        json.dumps(
            {
                "ok": True,
                "input": str(source),
                "asr_wav": str(wav),
                "output": str(output),
                "hotword": hotword,
                "convert": convert_info,
                "model": funasr_model_kwargs(),
                "result": result,
                "text": text,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "ok": True,
        "cache_hit": False,
        "input": str(source),
        "asr_wav": str(wav),
        "output": str(output),
        "json_output": str(json_output),
        "text": text,
    }


def lrc_timestamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    minutes = int(seconds // 60)
    rest = seconds - minutes * 60
    return f"{minutes:02d}:{rest:05.2f}"


def split_lyric_lines(text: str) -> list[str]:
    normalized = text.replace("\r", "\n").replace("。", "。\n").replace("？", "？\n").replace("！", "！\n")
    normalized = normalized.replace("，", "，\n")
    return [line.strip() for line in normalized.splitlines() if line.strip()]


def write_draft_lrc(text: str, output: Path, duration: float | None) -> None:
    lines = split_lyric_lines(text)
    if not lines:
        output.write_text("[00:00.00]\n", encoding="utf-8")
        return
    total = duration or float(len(lines) * 5)
    step = max(1.0, total / max(1, len(lines)))
    body = "\n".join(f"[{lrc_timestamp(index * step)}]{line}" for index, line in enumerate(lines))
    output.write_text(body + "\n", encoding="utf-8")


def strip_lrc_tags(line: str) -> str:
    return re.sub(r"\[[0-9]{1,3}:[0-9]{2}(?:\.[0-9]{1,3})?\]", "", line).strip()


def load_reference_lyrics(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    for raw in text.replace("\r", "\n").splitlines():
        line = strip_lrc_tags(raw).strip()
        if not line:
            continue
        if re.fullmatch(r"\[[a-zA-Z]+:.*\]", line):
            continue
        lines.append(line)
    return lines


def normalize_lyric_text(text: str) -> str:
    return "".join(ch for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fff").lower()


def lyric_information_score(text: str) -> float:
    normalized = normalize_lyric_text(text)
    if not normalized:
        return 0.0
    return len(set(normalized)) / max(1, len(normalized))


def timestamp_pairs_from_result(result: Any) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    if isinstance(result, list):
        for item in result:
            if isinstance(item, dict):
                timestamps = item.get("timestamp")
                if isinstance(timestamps, list):
                    for value in timestamps:
                        if (
                            isinstance(value, list)
                            and len(value) >= 2
                            and isinstance(value[0], (int, float))
                            and isinstance(value[1], (int, float))
                        ):
                            pairs.append((int(value[0]), int(value[1])))
    return pairs


def build_char_time_map(asr_text: str, pairs: list[tuple[int, int]]) -> list[tuple[str, float]]:
    chars = [ch for ch in asr_text if ch.strip()]
    usable = min(len(chars), len(pairs))
    return [(chars[index], pairs[index][0] / 1000.0) for index in range(usable)]


def best_reference_line_time(
    reference_line: str,
    char_times: list[tuple[str, float]],
    start_index: int,
) -> tuple[float | None, int, float]:
    target = normalize_lyric_text(reference_line)
    if not target or not char_times:
        return None, start_index, 0.0
    asr_chars = "".join(ch for ch, _time in char_times)
    search_start = max(0, min(start_index, len(asr_chars) - 1))
    search_end = min(len(asr_chars), search_start + max(80, len(target) * 8))
    window = asr_chars[search_start:search_end]
    best: tuple[float, int] = (0.0, search_start)
    min_size = min(3, len(target))
    for match in SequenceMatcher(None, target, window, autojunk=False).get_matching_blocks():
        if match.size < min_size:
            continue
        score = match.size / max(1, len(target))
        absolute = search_start + match.b
        if score > best[0]:
            best = (score, absolute)
    if best[0] <= 0:
        return None, start_index, 0.0
    next_index = min(len(char_times), best[1] + max(1, len(target) // 2))
    return char_times[best[1]][1], next_index, best[0]


def align_reference_lyrics_to_times(
    lines: list[str],
    asr_text: str,
    asr_timestamps: list[tuple[int, int]],
    duration: float | None,
) -> tuple[list[tuple[float, str]], dict[str, Any]]:
    char_times = build_char_time_map(asr_text, asr_timestamps)
    total = duration or (char_times[-1][1] if char_times else float(len(lines) * 5))
    fallback_step = max(1.0, total / max(1, len(lines)))
    aligned: list[tuple[float, str]] = []
    cursor = 0
    matched = 0
    scores: list[float] = []
    last_time = 0.0
    for index, line in enumerate(lines):
        guessed: float | None
        score: float
        if lyric_information_score(line) < 0.35:
            guessed, score = None, 0.0
        else:
            guessed, cursor, score = best_reference_line_time(line, char_times, cursor)
        if guessed is None:
            guessed = index * fallback_step
        else:
            matched += 1
            scores.append(score)
        if guessed < last_time:
            guessed = last_time + 0.5
        last_time = min(guessed, total)
        aligned.append((last_time, line))
    diagnostics = {
        "reference_lines": len(lines),
        "asr_chars_with_time": len(char_times),
        "matched_lines": matched,
        "average_match_score": (sum(scores) / len(scores)) if scores else 0.0,
        "duration_seconds": total,
        "method": "asr_timestamp_anchor_with_uniform_fallback",
    }
    return aligned, diagnostics


def write_lrc_lines(aligned: list[tuple[float, str]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"[{lrc_timestamp(seconds)}]{line}" for seconds, line in aligned)
    output.write_text(body + ("\n" if body else ""), encoding="utf-8")


def demucs_environment() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env["TORCH_HOME"] = str(default_model_root() / "torch")
    Path(env["TORCH_HOME"]).mkdir(parents=True, exist_ok=True)
    return env


def demucs_device_info() -> dict[str, Any]:
    try:
        import torch  # type: ignore

        cuda_available = bool(torch.cuda.is_available())
        return {
            "torch": getattr(torch, "__version__", "unknown"),
            "cuda_available": cuda_available,
            "cuda": getattr(torch.version, "cuda", None),
            "device_name": torch.cuda.get_device_name(0) if cuda_available else "",
        }
    except Exception as exc:
        return {"error": str(exc)}


def demucs_vocals(
    source: Path,
    work_root: Path,
    overwrite: bool,
    model_name: str = DEMUCS_MODEL_NAME,
    device: str = "auto",
    shifts: int = 1,
) -> dict[str, Any]:
    repo = default_model_root() / "demucs-local-repo"
    out_root = cache_dir_for(source, work_root, "demucs")
    expected = out_root / model_name / source.stem / "vocals.wav"
    if expected.exists() and not overwrite:
        return {
            "ok": True,
            "cache_hit": True,
            "vocals": str(expected),
            "output_root": str(out_root),
            "model": model_name,
            "device": device,
        }
    use_local_repo = model_name == DEMUCS_MODEL_NAME and repo.exists()
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "_demucs-separate",
        str(source),
        "--output-root",
        str(out_root),
        "--model-name",
        model_name,
    ]
    if use_local_repo:
        command.extend(["--repo", str(repo)])
    if device != "auto":
        command.extend(["--device", device])
    if shifts != 1:
        command.extend(["--shifts", str(shifts)])
    env = demucs_environment()
    try:
        run_command(command, env=env)
    except SystemExit as exc:
        return {
            "ok": False,
            "reason": str(exc),
            "command": command,
            "output_root": str(out_root),
            "model": model_name,
            "device": device,
            "torch_home": env.get("TORCH_HOME", ""),
        }
    return {
        "ok": expected.exists(),
        "cache_hit": False,
        "vocals": str(expected),
        "output_root": str(out_root),
        "command": command,
        "model": model_name,
        "device": device,
        "torch_home": env.get("TORCH_HOME", ""),
        "runtime": demucs_device_info(),
    }


def verify_trusted_demucs_weight(repo: Path) -> Path:
    weight = (repo / DEMUCS_MODEL_FILE).resolve()
    if not weight.is_file():
        raise SystemExit(f"Trusted Demucs weight not found: {weight}")
    actual = sha256_file(weight)
    if actual.lower() != DEMUCS_MODEL_SHA256:
        raise SystemExit(
            json.dumps(
                {
                    "ok": False,
                    "error": "Demucs weight SHA256 mismatch; refusing unsafe compatibility load",
                    "path": str(weight),
                    "expected_sha256": DEMUCS_MODEL_SHA256,
                    "actual_sha256": actual,
                },
                ensure_ascii=False,
            )
        )
    return weight


def patch_torch_load_for_trusted_demucs(weight: Path) -> None:
    import torch  # type: ignore

    original_load = torch.load
    trusted = weight.resolve()

    def patched_load(path_or_file: Any, *args: Any, **kwargs: Any) -> Any:
        target: Path | None = None
        if isinstance(path_or_file, (str, os.PathLike)):
            try:
                target = Path(path_or_file).resolve()
            except OSError:
                target = None
        if target == trusted and "weights_only" not in kwargs:
            kwargs["weights_only"] = False
        return original_load(path_or_file, *args, **kwargs)

    torch.load = patched_load  # type: ignore[assignment]


def patch_demucs_wav_save() -> None:
    import demucs.audio as demucs_audio  # type: ignore
    import demucs.separate as demucs_separate  # type: ignore
    import soundfile as sf  # type: ignore

    original_save_audio = demucs_audio.save_audio

    def patched_save_audio(wav: Any, path: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return original_save_audio(wav, path, *args, **kwargs)
        except Exception:
            target = Path(path)
            if target.suffix.lower() != ".wav":
                raise
            samplerate = kwargs.get("samplerate")
            if samplerate is None and args:
                samplerate = args[0]
            if samplerate is None:
                raise
            clip = kwargs.get("clip", "rescale")
            bits_per_sample = int(kwargs.get("bits_per_sample", 16))
            as_float = bool(kwargs.get("as_float", False))
            wav = demucs_audio.prevent_clip(wav, mode=clip)
            array = wav.detach().cpu().numpy()
            if array.ndim == 2:
                array = array.T
            subtype = "FLOAT" if as_float else ("PCM_24" if bits_per_sample == 24 else "PCM_16")
            sf.write(str(target), array, int(samplerate), subtype=subtype)
            return None

    demucs_audio.save_audio = patched_save_audio
    demucs_separate.save_audio = patched_save_audio


def cmd_demucs_separate(args: argparse.Namespace) -> None:
    source = ensure_input(args.input)
    out_root = Path(args.output_root).expanduser().resolve()
    repo = Path(args.repo).expanduser().resolve() if args.repo else None
    if repo is not None:
        weight = verify_trusted_demucs_weight(repo)
        patch_torch_load_for_trusted_demucs(weight)
    import demucs.separate as demucs_separate  # type: ignore

    patch_demucs_wav_save()
    command = [
        "-n",
        args.model_name,
        "--two-stems",
        "vocals",
        "-o",
        str(out_root),
    ]
    if repo is not None:
        command.extend(["--repo", str(repo)])
    if args.device != "auto":
        command.extend(["--device", args.device])
    if args.shifts != 1:
        command.extend(["--shifts", str(args.shifts)])
    command.append(str(source))
    demucs_separate.main(command)


def cmd_inspect(args: argparse.Namespace) -> None:
    source = ensure_input(args.input)
    data = ffprobe_data(source)
    print(json.dumps({"ok": True, "path": str(source), "ffprobe": data}, ensure_ascii=False, indent=2))


def cmd_validate(args: argparse.Namespace) -> int:
    tools: list[dict[str, Any]] = []
    issues: list[dict[str, str]] = []
    for name in ("ffmpeg", "ffprobe"):
        try:
            path = require_tool(name)
        except SystemExit as exc:
            path = ""
            issues.append({"code": "required_tool_missing", "tool": name, "message": str(exc)})
        tools.append({"name": name, "ok": bool(path), "path": path})
    payload = {
        "schema": "audio_toolkit.validate.v1",
        "ok": not issues,
        "read_only": True,
        "tools": tools,
        "optional_modules": {
            name: module_available(name)
            for name in ("mutagen", "librosa", "funasr", "demucs", "torch", "soundfile")
        },
        "issues": issues,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


def cmd_metadata(args: argparse.Namespace) -> None:
    source = ensure_input(args.input)
    try:
        from mutagen import File
    except Exception as exc:  # pragma: no cover - environment guard
        raise SystemExit(f"mutagen import failed: {exc}") from exc

    audio = File(str(source), easy=True)
    tags: dict[str, Any] = {}
    if audio is not None and audio.tags is not None:
        tags = {str(k): list(v) for k, v in audio.tags.items()}
    ffprobe_tags: dict[str, Any] = {}
    try:
        data = ffprobe_data(source)
        ffprobe_tags = data.get("format", {}).get("tags", {}) or {}
    except Exception:
        ffprobe_tags = {}
    print(
        json.dumps(
            {
                "ok": True,
                "path": str(source),
                "mutagen_tags": tags,
                "ffprobe_tags": ffprobe_tags,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_analyze(args: argparse.Namespace) -> None:
    source = ensure_input(args.input)
    try:
        import librosa
        import numpy as np
    except Exception as exc:  # pragma: no cover - environment guard
        raise SystemExit(f"audio analysis imports failed: {exc}") from exc

    y, sr = librosa.load(str(source), sr=None, mono=True, duration=args.max_seconds)
    if y.size == 0:
        raise SystemExit("No audio samples loaded")
    rms = librosa.feature.rms(y=y)[0]
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    zcr = librosa.feature.zero_crossing_rate(y)[0]
    tempo = None
    try:
        tempo_arr = librosa.feature.rhythm.tempo(y=y, sr=sr)
        tempo = float(tempo_arr[0]) if len(tempo_arr) else None
    except Exception:
        tempo = None
    result = {
        "ok": True,
        "path": str(source),
        "sample_rate": int(sr),
        "samples": int(y.size),
        "duration_seconds": float(y.size / sr),
        "rms_mean": float(np.mean(rms)),
        "rms_max": float(np.max(rms)),
        "spectral_centroid_mean": float(np.mean(centroid)),
        "zero_crossing_rate_mean": float(np.mean(zcr)),
        "estimated_tempo_bpm": tempo,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_convert(args: argparse.Namespace) -> None:
    source = ensure_input(args.input)
    output = ensure_output(args.output, args.overwrite)
    ffmpeg = require_tool("ffmpeg")
    command = [ffmpeg, "-hide_banner", "-y" if args.overwrite else "-n", "-i", str(source)]
    if args.audio_codec:
        command += ["-c:a", args.audio_codec]
    if args.bitrate:
        command += ["-b:a", args.bitrate]
    command.append(str(output))
    run_command(command)
    print(json.dumps({"ok": True, "output": str(output)}, ensure_ascii=False))


def cmd_convert_asr_wav(args: argparse.Namespace) -> None:
    source = ensure_input(args.input)
    work_root = ensure_work_root(args.work_root)
    output = Path(args.output).expanduser().resolve() if args.output else default_asr_wav_path(source, work_root)
    result = convert_to_asr_wav(source, output, overwrite=args.overwrite)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_transcribe_zh(args: argparse.Namespace) -> None:
    source = ensure_input(args.input)
    work_root = ensure_work_root(args.work_root)
    output = (
        Path(args.output).expanduser().resolve()
        if args.output
        else cache_dir_for(source, work_root, "transcripts") / f"{safe_stem(source)}.zh.txt"
    )
    result = transcribe_zh(source, output, work_root, overwrite=args.overwrite, hotword=args.hotword or "")
    print(json.dumps({key: value for key, value in result.items() if key != "text"}, ensure_ascii=False, indent=2))


def lyrics_with_demucs(
    args: argparse.Namespace,
    mode: str,
    text_suffix: str,
    lrc_suffix: str,
    manifest_name: str,
    note: str,
) -> None:
    source = ensure_input(args.input)
    work_root = ensure_work_root(args.work_root)
    output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else cache_dir_for(source, work_root, "lyrics") / safe_stem(source)
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    vocals_info = demucs_vocals(
        source,
        work_root,
        overwrite=args.overwrite,
        model_name=args.demucs_model,
        device=args.demucs_device,
        shifts=args.demucs_shifts,
    )
    asr_input = Path(vocals_info.get("vocals") or source) if vocals_info.get("ok") else source
    txt_output = output_dir / f"{safe_stem(source)}.{text_suffix}.txt"
    lrc_output = output_dir / f"{safe_stem(source)}.{lrc_suffix}.lrc"
    result = transcribe_zh(asr_input, txt_output, work_root, overwrite=args.overwrite, hotword=args.hotword or "")
    text = str(result.get("text") or "")
    write_draft_lrc(text, lrc_output, audio_duration_seconds(source))
    summary = {
        "ok": True,
        "mode": mode,
        "input": str(source),
        "asr_input": str(asr_input),
        "demucs": vocals_info,
        "text_output": str(txt_output),
        "lrc_output": str(lrc_output),
        "note": note,
    }
    (output_dir / manifest_name).write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def cmd_lyrics_draft_zh(args: argparse.Namespace) -> None:
    lyrics_with_demucs(
        args,
        mode="draft",
        text_suffix="lyrics-draft.zh",
        lrc_suffix="lyrics-draft.zh",
        manifest_name="manifest.json",
        note="ASR draft with Demucs vocals; verify manually before treating it as official lyrics.",
    )


def cmd_lyrics_ultra_zh(args: argparse.Namespace) -> None:
    lyrics_with_demucs(
        args,
        mode="ultra",
        text_suffix="lyrics-ultra.zh",
        lrc_suffix="lyrics-ultra.zh",
        manifest_name="manifest.ultra.json",
        note="Higher-quality ASR draft using htdemucs_ft vocals; slower and still requires manual lyric verification.",
    )


def cmd_lyrics_fast_zh(args: argparse.Namespace) -> None:
    source = ensure_input(args.input)
    work_root = ensure_work_root(args.work_root)
    output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else cache_dir_for(source, work_root, "lyrics-fast") / safe_stem(source)
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    txt_output = output_dir / f"{safe_stem(source)}.fast.lyrics.zh.txt"
    lrc_output = output_dir / f"{safe_stem(source)}.fast.lyrics.zh.lrc"
    result = transcribe_zh(source, txt_output, work_root, overwrite=args.overwrite, hotword=args.hotword or "")
    text = str(result.get("text") or "")
    write_draft_lrc(text, lrc_output, audio_duration_seconds(source))
    summary = {
        "ok": True,
        "mode": "fast",
        "input": str(source),
        "asr_input": str(source),
        "text_output": str(txt_output),
        "lrc_output": str(lrc_output),
        "note": "Fast ASR draft; skips vocal separation for speed.",
    }
    (output_dir / "manifest.fast.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def cmd_lyrics_align_zh(args: argparse.Namespace) -> None:
    source = ensure_input(args.input)
    reference = ensure_input(args.reference)
    work_root = ensure_work_root(args.work_root)
    output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else cache_dir_for(source, work_root, "lyrics-align") / safe_stem(source)
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    lines = load_reference_lyrics(reference)
    if not lines:
        raise SystemExit(f"Reference lyrics file has no lyric lines: {reference}")
    asr_source = source
    demucs_info: dict[str, Any] | None = None
    if args.use_demucs:
        demucs_info = demucs_vocals(
            source,
            work_root,
            overwrite=False,
            model_name=args.demucs_model,
            device=args.demucs_device,
            shifts=args.demucs_shifts,
        )
        if demucs_info.get("ok") and demucs_info.get("vocals"):
            asr_source = Path(str(demucs_info["vocals"]))
    txt_output = output_dir / f"{safe_stem(source)}.aligned-anchor.zh.txt"
    lrc_output = output_dir / f"{safe_stem(source)}.aligned.zh.lrc"
    result = transcribe_zh(asr_source, txt_output, work_root, overwrite=args.overwrite, hotword=args.hotword or "")
    json_output = Path(str(result.get("json_output") or txt_output.with_suffix(txt_output.suffix + ".json")))
    asr_payload = json.loads(json_output.read_text(encoding="utf-8", errors="replace"))
    asr_result = asr_payload.get("result")
    asr_text = str(asr_payload.get("text") or result.get("text") or "")
    aligned, diagnostics = align_reference_lyrics_to_times(
        lines,
        asr_text,
        timestamp_pairs_from_result(asr_result),
        audio_duration_seconds(source),
    )
    write_lrc_lines(aligned, lrc_output)
    summary = {
        "ok": True,
        "mode": "align",
        "input": str(source),
        "reference": str(reference),
        "asr_input": str(asr_source),
        "text_output": str(txt_output),
        "lrc_output": str(lrc_output),
        "demucs": demucs_info,
        "diagnostics": diagnostics,
        "note": "Reference lyrics text is preserved; ASR timestamps are used only as timing anchors.",
    }
    (output_dir / "manifest.align.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def cmd_trim(args: argparse.Namespace) -> None:
    source = ensure_input(args.input)
    output = ensure_output(args.output, args.overwrite)
    ffmpeg = require_tool("ffmpeg")
    command = [ffmpeg, "-hide_banner", "-y" if args.overwrite else "-n", "-ss", args.start, "-i", str(source)]
    if args.duration:
        command += ["-t", str(args.duration)]
    command += ["-c", "copy", str(output)]
    run_command(command)
    print(json.dumps({"ok": True, "output": str(output)}, ensure_ascii=False))


def cmd_normalize(args: argparse.Namespace) -> None:
    source = ensure_input(args.input)
    output = ensure_output(args.output, args.overwrite)
    ffmpeg = require_tool("ffmpeg")
    filt = f"loudnorm=I={args.integrated}:TP={args.true_peak}:LRA={args.lra}"
    command = [
        ffmpeg,
        "-hide_banner",
        "-y" if args.overwrite else "-n",
        "-i",
        str(source),
        "-af",
        filt,
        str(output),
    ]
    run_command(command)
    print(json.dumps({"ok": True, "output": str(output), "filter": filt}, ensure_ascii=False))


def cmd_silence_detect(args: argparse.Namespace) -> None:
    source = ensure_input(args.input)
    ffmpeg = require_tool("ffmpeg")
    filt = f"silencedetect=noise={args.noise}:d={args.duration}"
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(source), "-af", filt, "-f", "null", "-"],
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise SystemExit(
            json.dumps(
                {"ok": False, "returncode": proc.returncode, "stderr": proc.stderr[-4000:]},
                ensure_ascii=False,
            )
        )
    lines = [
        line.strip()
        for line in proc.stderr.splitlines()
        if "silence_start" in line or "silence_end" in line
    ]
    print(json.dumps({"ok": True, "path": str(source), "filter": filt, "events": lines}, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audio inspection and editing toolkit")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate", help="Validate core audio tooling and emit a read-only JSON receipt")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("inspect", help="Inspect streams and format via ffprobe")
    p.add_argument("input")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("metadata", help="Read audio tags via mutagen")
    p.add_argument("input")
    p.set_defaults(func=cmd_metadata)

    p = sub.add_parser("analyze", help="Compute basic audio features via librosa")
    p.add_argument("input")
    p.add_argument("--max-seconds", type=float, default=120.0)
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("convert", help="Convert audio with ffmpeg")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--audio-codec")
    p.add_argument("--bitrate")
    p.add_argument("--overwrite", action="store_true")
    p.set_defaults(func=cmd_convert)

    p = sub.add_parser("convert-asr-wav", help="Convert audio to cached 16 kHz mono WAV for ASR")
    p.add_argument("input")
    p.add_argument("output", nargs="?")
    p.add_argument("--work-root", default="")
    p.add_argument("--overwrite", action="store_true")
    p.set_defaults(func=cmd_convert_asr_wav)

    p = sub.add_parser("transcribe-zh", help="Transcribe an explicitly requested Chinese audio file with FunASR")
    p.add_argument("input")
    p.add_argument("--output", default="")
    p.add_argument("--work-root", default="")
    p.add_argument("--hotword", default="")
    p.add_argument("--overwrite", action="store_true")
    p.set_defaults(func=cmd_transcribe_zh)

    p = sub.add_parser("lyrics-draft-zh", help="Create a Chinese lyrics draft and draft LRC from an explicitly requested song file")
    p.add_argument("input")
    p.add_argument("--output-dir", default="")
    p.add_argument("--work-root", default="")
    p.add_argument("--hotword", default="")
    p.add_argument("--demucs-model", default=DEMUCS_MODEL_NAME)
    p.add_argument("--demucs-device", default="auto", choices=["auto", "cuda", "cpu"])
    p.add_argument("--demucs-shifts", type=int, default=1)
    p.add_argument("--overwrite", action="store_true")
    p.set_defaults(func=cmd_lyrics_draft_zh)

    p = sub.add_parser("lyrics-ultra-zh", help="Create a slower higher-quality Chinese lyrics draft with htdemucs_ft")
    p.add_argument("input")
    p.add_argument("--output-dir", default="")
    p.add_argument("--work-root", default="")
    p.add_argument("--hotword", default="")
    p.add_argument("--demucs-model", default=DEMUCS_ULTRA_MODEL_NAME)
    p.add_argument("--demucs-device", default="auto", choices=["auto", "cuda", "cpu"])
    p.add_argument("--demucs-shifts", type=int, default=1)
    p.add_argument("--overwrite", action="store_true")
    p.set_defaults(func=cmd_lyrics_ultra_zh)

    p = sub.add_parser("lyrics-fast-zh", help="Create a fast Chinese lyrics draft and LRC without vocal separation")
    p.add_argument("input")
    p.add_argument("--output-dir", default="")
    p.add_argument("--work-root", default="")
    p.add_argument("--hotword", default="")
    p.add_argument("--overwrite", action="store_true")
    p.set_defaults(func=cmd_lyrics_fast_zh)

    p = sub.add_parser("lyrics-align-zh", help="Align reference Chinese lyrics to an audio file and write LRC")
    p.add_argument("input")
    p.add_argument("--reference", required=True)
    p.add_argument("--output-dir", default="")
    p.add_argument("--work-root", default="")
    p.add_argument("--hotword", default="")
    p.add_argument("--use-demucs", action="store_true")
    p.add_argument("--demucs-model", default=DEMUCS_ULTRA_MODEL_NAME)
    p.add_argument("--demucs-device", default="auto", choices=["auto", "cuda", "cpu"])
    p.add_argument("--demucs-shifts", type=int, default=1)
    p.add_argument("--overwrite", action="store_true")
    p.set_defaults(func=cmd_lyrics_align_zh)

    p = sub.add_parser("trim", help="Trim audio with ffmpeg stream copy")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--start", required=True)
    p.add_argument("--duration")
    p.add_argument("--overwrite", action="store_true")
    p.set_defaults(func=cmd_trim)

    p = sub.add_parser("normalize", help="Normalize loudness with ffmpeg loudnorm")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--integrated", default="-16")
    p.add_argument("--true-peak", default="-1.5")
    p.add_argument("--lra", default="11")
    p.add_argument("--overwrite", action="store_true")
    p.set_defaults(func=cmd_normalize)

    p = sub.add_parser("silence-detect", help="Detect silence with ffmpeg")
    p.add_argument("input")
    p.add_argument("--noise", default="-35dB")
    p.add_argument("--duration", default="0.5")
    p.set_defaults(func=cmd_silence_detect)

    return parser


def run_internal_command(argv: list[str]) -> bool:
    if not argv or argv[0] != "_demucs-separate":
        return False
    parser = argparse.ArgumentParser(description="Internal trusted Demucs wrapper")
    parser.add_argument("command")
    parser.add_argument("input")
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--model-name", default=DEMUCS_MODEL_NAME)
    parser.add_argument("--repo", default="")
    parser.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    parser.add_argument("--shifts", type=int, default=1)
    args = parser.parse_args(argv)
    cmd_demucs_separate(args)
    return True


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if run_internal_command(argv):
        return 0
    if argv:
        maybe_reroute_heavy_command(argv[0], argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    result = args.func(args)
    return int(result or 0)


if __name__ == "__main__":
    raise SystemExit(main())
