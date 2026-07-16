# Audio Toolkit

Purpose: provide repeatable local audio-file operations and Windows playback
diagnostics for Codex work in this workspace. Audio-file processing is explicit:
run these commands only when the user asks to process a local audio file or
audio attachment.

## Installed Baseline

- FFmpeg / FFprobe: Gyan.FFmpeg 8.1.1 via winget
- SoX: ChrisBagwell.SoX 14.4.2 via winget
- Python packages: pydub, mutagen, soundfile, scipy, librosa, numpy

## Commands

Run from the workspace root:

```powershell
python _bridge\audio_toolkit\audio_toolkit.py inspect <audio-file>
python _bridge\audio_toolkit\audio_toolkit.py metadata <audio-file>
python _bridge\audio_toolkit\audio_toolkit.py analyze <audio-file>
python _bridge\audio_toolkit\audio_toolkit.py convert <input> <output>
python _bridge\audio_toolkit\audio_toolkit.py convert-asr-wav <input>
python _bridge\audio_toolkit\audio_toolkit.py transcribe-zh <input> --output .tools\audio-work\transcripts\out.txt
python _bridge\audio_toolkit\audio_toolkit.py lyrics-fast-zh <input> --output-dir .tools\audio-work\lyrics-fast\song
python _bridge\audio_toolkit\audio_toolkit.py lyrics-draft-zh <input> --output-dir .tools\audio-work\lyrics\song
python _bridge\audio_toolkit\audio_toolkit.py lyrics-ultra-zh <input> --output-dir .tools\audio-work\lyrics-ultra\song
python _bridge\audio_toolkit\audio_toolkit.py lyrics-align-zh <input> --reference lyrics.txt --output-dir .tools\audio-work\lyrics-align\song
python _bridge\audio_toolkit\audio_toolkit.py trim <input> <output> --start 00:00:10 --duration 30
python _bridge\audio_toolkit\audio_toolkit.py normalize <input> <output>
python _bridge\audio_toolkit\audio_toolkit.py silence-detect <input>
```

## Desktop GUI

For direct use without Codex or command lines, launch:

```text
_bridge\audio_toolkit\启动音频工具箱.bat
```

The GUI wraps the same `audio_toolkit.py` commands and writes outputs under:

```text
.tools\audio-work\gui-output\
```

Use the desktop shortcut named `本地音频工具箱` if it has been installed.
The preview area is content-focused: reference-lyric alignment shows the source
lyric immediately, and generated text/LRC previews are bounded for responsiveness.
Use `打开结果文件` for the full output.

The tool writes JSON to stdout for inspection commands. Editing commands call
FFmpeg and fail if the output file already exists unless `--overwrite` is set.

## Output And Cache Layout

The default work root is:

```text
.tools\audio-work\
```

Cached and generated files are grouped by input file SHA-256:

- `asr-cache\<sha256>\asr-16k-mono.wav`
- `transcripts\<sha256>\*.zh.txt`
- `lyrics\<sha256>\...\*.lyrics-draft.zh.txt`
- `lyrics\<sha256>\...\*.lyrics-draft.zh.lrc`

`transcribe-zh` loads FunASR only when called. `lyrics-fast-zh` skips vocal
separation for speed; the first run still loads the ASR model, while later runs
can reuse existing output when `--overwrite` is not passed. `lyrics-draft-zh`
may run Demucs first for cleaner vocals and marks output as an ASR draft, not
official lyrics.

`lyrics-ultra-zh` uses Demucs `htdemucs_ft` for a slower higher-quality vocal
separation pass. CUDA is used automatically by Demucs when available, and the
GUI forces CUDA for the `精修歌词/LRC` button. Demucs/PyTorch downloads are cached
under `.tools\models\torch\` when this toolkit launches them.

`lyrics-align-zh` preserves a user-provided reference lyric text and uses local
ASR timestamps only as timing anchors. In the GUI, use `参考歌词/LRC` when the
words matter more than fully automatic recognition.

## Playback Control Rules

For "stop current music" on Windows, do not infer from visible windows or
process lists. The correct signal is the default render device's Core Audio
sessions: process id plus audio peak value.

Default behavior must be non-destructive:

1. Detect the process currently producing speaker output.
2. Send direct playback pause/stop commands to that process window/session.
3. Verify the audio peak falls near zero.
4. Do not close or kill the player unless the user explicitly asks.
5. Do not use audio-session mute as the normal route. If mute is used as a
   temporary fallback, restore all roles: eConsole, eMultimedia, and
   eCommunications.

## Chinese Desktop Player Notes

Expected Windows desktop players to handle conservatively:

- Microsoft Media Player: may expose UWP/WinUI child windows and unstable main
  handles. Enumerate child windows by PID before sending app commands.
- KuGou Music: can have multiple helper processes. Use audio session peak value
  to find whether it is actually playing.
- QQ Music, NetEase Cloud Music, Kuwo Music, Migu Music: treat as ordinary
  desktop players first. Prefer Core Audio session detection and Windows media
  commands over app-specific assumptions.

Hotkeys and app-specific controls vary by version and user settings. Do not rely
on a player-specific shortcut unless it has been verified on this machine.

## Sources

- Microsoft Core Audio APIs and audio sessions
- Microsoft WM_APPCOMMAND media commands
- FFmpeg official documentation and filters
- SoX manual
- Mutagen and librosa documentation
