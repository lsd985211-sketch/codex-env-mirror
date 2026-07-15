---
name: windows-audio-ops
description: Windows audio playback, audio-file operations, and local audio transcription workflows for Codex work. Use when diagnosing or controlling current music/playback, checking which app is using speakers, fixing app mute/volume state, transcribing Chinese audio or songs, separating vocals, using FFmpeg/SoX/librosa/FunASR/Demucs tools, or handling local audio player behavior such as Microsoft Media Player, Windows Media Player Legacy, KuGou, QQ Music, NetEase Cloud Music, VLC, or similar Windows players.
---

# Windows Audio Ops

Use this skill for Windows audio tasks where mistakes can disturb the user's
desktop state. Prefer evidence from Core Audio sessions over guesses from
window titles or process names.

## Workflow

1. Query memory for current local audio facts if the task is not trivial.
2. Inspect actual audio output with Core Audio/WASAPI session data:
   process id, process name, peak value, mute state, volume, and render role.
3. For "stop current music", target only the process with nonzero audio peak.
4. Send non-destructive playback controls first. Do not close windows or kill
   processes unless the user explicitly asks.
5. Verify after action with audio-session peak and mute/volume checks.

## Rules

- Do not infer the active player from visible windows alone.
- Do not use app-specific hotkeys as the first route; Chinese music players
  commonly allow user-configured hotkeys.
- Do not use audio-session mute as the normal stop route.
- If mute is used as temporary fallback, restore and verify all roles:
  `eConsole`, `eMultimedia`, and `eCommunications`.
- UWP/modern Media Player may expose child windows while `MainWindowHandle`
  is empty or stale. Enumerate windows by PID before sending app commands.
- `ffmpeg` and `ffprobe` may be installed through WinGet but absent from the
  current Codex process PATH. Check WinGet Links/Packages if needed.

## Local Tools

- Audio file toolkit:
  `C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager\_bridge\audio_toolkit\audio_toolkit.py`
- Supported operations: inspect, metadata, analyze, convert, trim, normalize,
  silence-detect, explicit Chinese audio transcription, Chinese lyrics draft
  generation, and higher-quality CUDA Demucs lyrics generation.
- Only run transcription, vocal separation, or lyrics generation when the user
  explicitly asks to process a local audio file or audio attachment.
- Direct-use GUI:
  `C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager\_bridge\audio_toolkit\audio_toolkit_gui.py`
  or the desktop shortcut `本地音频工具箱`.
- Keep GUI previews content-focused: show reference lyrics immediately for
  alignment tasks, keep subprocess logs in the log pane, and bound generated
  text/LRC previews instead of loading very large outputs into `ScrolledText`.

## Transcription And Lyrics

- Treat ordinary chat text as text. Only process local audio files or audio
  attachments when the user explicitly asks.
- Use `transcribe-zh` for Chinese speech-to-text.
- Use `lyrics-fast-zh` for fastest song lyric drafts without vocal separation.
- Use `lyrics-draft-zh` for normal Demucs vocal separation with `htdemucs`.
- Use `lyrics-ultra-zh` or GUI `精修歌词/LRC` for slower higher-quality lyric
  drafts with Demucs `htdemucs_ft`.
- Use `lyrics-align-zh` or GUI `参考歌词/LRC` when accurate lyric text matters:
  preserve the supplied lyric text and use local ASR timestamps only as timing
  anchors.
- Before assuming CUDA is active, verify with:
  `.tools\whisper-venv\Scripts\python.exe -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"`.
- Demucs/PyTorch model downloads launched by the toolkit should cache under
  `.tools\models\torch\`; prefer resumable `curl.exe --continue-at - --retry`
  downloads when model fetching is slow or flaky.
- On Windows, direct `python -m demucs.separate` can fail while saving WAV due
  to torchcodec/torchaudio DLL loading. Prefer the toolkit's internal
  `_demucs-separate` wrapper, which patches WAV saving and records CUDA/model
  details in the manifest.
- Verify real success from `manifest*.json`: `demucs.ok` must be true and
  `asr_input` should point to the separated `vocals.wav` for Demucs modes.
- For reference-lyric alignment, verify `manifest.align.json` and the generated
  `*.aligned.zh.lrc`; changing the reference lyric file should regenerate the
  LRC instead of reusing a cached GUI result.

## References

Read `references/core.md` for local player facts, installed tool versions, and
safe command patterns.

## Preflight
- Confirm the skill matches the task before using it.
- Keep the scope tight and avoid unrelated changes.
## Output Contract
- State the result, blockers, and any key tradeoffs.
- Keep it concise and actionable.
