# Windows Audio Ops Reference

## Verified Local Baseline

- FFmpeg / FFprobe / FFplay: Gyan.FFmpeg 8.1.1 via WinGet.
- SoX: ChrisBagwell.SoX 14.4.2 via WinGet.
- Python audio stack: pydub, mutagen, soundfile, scipy, librosa, numpy.
- Audio toolkit path:
  `C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager\_bridge\audio_toolkit\audio_toolkit.py`

## Local Players

- KuGou Music 20.1.11.27745:
  `C:\Program Files\KuGou\KGMusic\KuGou.exe`
- NetEase CloudMusic 3.1.33.205244:
  `C:\Program Files\Netease\CloudMusic\cloudmusic.exe`
- QQ Music 22.16:
  `C:\Program Files (x86)\Tencent\QQMusic\QQMusic.exe`
- VLC 3.0.20:
  `C:\Program Files\VideoLAN\VLC\vlc.exe`
- Microsoft modern Media Player AppX:
  `Microsoft.ZuneMusic_11.2604.10.0_x64`
- Windows Media Player Legacy:
  `C:\Program Files (x86)\Windows Media Player\wmplayer.exe`

Traditional associations for `.mp3`, `.flac`, `.wav`, `.m4a`, and `.aac`
point to `WMP11.AssocFile.*` classes and Windows Media Player Legacy.

## Stop Playback Procedure

1. Enumerate Core Audio render sessions for all roles.
2. Pick sessions with actual peak output, not just active windows.
3. Map PID to process and enumerate top-level plus child windows.
4. Send direct media app commands:
   `APPCOMMAND_MEDIA_PAUSE` and/or `APPCOMMAND_MEDIA_STOP`.
5. Re-sample peak values. Success means peak is effectively zero while
   `Muted=false` and volume remains normal.

## File Operations

Use the audio toolkit rather than rewriting FFmpeg commands:

```powershell
python _bridge\audio_toolkit\audio_toolkit.py inspect song.mp3
python _bridge\audio_toolkit\audio_toolkit.py metadata song.mp3
python _bridge\audio_toolkit\audio_toolkit.py analyze song.mp3
python _bridge\audio_toolkit\audio_toolkit.py convert-asr-wav recording.m4a
python _bridge\audio_toolkit\audio_toolkit.py transcribe-zh recording.m4a --output .tools\audio-work\transcripts\recording.zh.txt
python _bridge\audio_toolkit\audio_toolkit.py lyrics-draft-zh song.wav --output-dir .tools\audio-work\lyrics\song
python _bridge\audio_toolkit\audio_toolkit.py trim in.mp3 out.mp3 --start 00:00:10 --duration 30
python _bridge\audio_toolkit\audio_toolkit.py normalize in.mp3 out.wav
python _bridge\audio_toolkit\audio_toolkit.py silence-detect in.wav
```

For destructive operations such as deleting duplicate audio files, generate a
candidate report first and ask for confirmation before deleting.

Transcription and lyrics commands are explicit local audio-file operations. Do
not run them unless the user asks to process an audio file or audio attachment.

## Chinese Song Transcription

For Chinese songs with accompaniment, prefer this local pipeline:

1. Inspect the source with FFprobe, then create a 16 kHz mono WAV for ASR.
2. Separate vocals with Demucs before ASR when the goal is lyrics.
3. Prefer ModelScope/FunASR for Chinese vocals. Use Whisper as a cross-check,
   not as the primary result, unless a stronger Whisper model is already local.
4. Add hotwords for song title, artist, and likely lyric phrases. Mark output
   as an ASR draft, not official lyrics.
5. Do not paste full commercial-song lyrics into chat. Save local draft files
   and summarize quality or short excerpts only.

Demucs local workflow used successfully in this workspace:

```powershell
# Download official htdemucs weight with parallel HTTP Range chunks.
# Full URL:
# https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/955717e8-8726e21a.th
# Expected size: 84141911
# Expected SHA256 prefix from filename: 8726e21a
```

Store verified weights under:

```text
C:\Users\45543\AppData\Local\Codex\audio\models\demucs-local-repo\
```

The local repo needs both:

```text
955717e8-8726e21a.th
htdemucs.yaml
```

with `htdemucs.yaml` containing:

```yaml
models: ['955717e8']
```

On modern PyTorch, older Demucs checkpoints may fail because `torch.load`
defaults to `weights_only=True`. Only for trusted, hash-verified Demucs weights,
run Demucs through a small wrapper that sets `weights_only=False`.

On Windows, `torchaudio.save` can fail because TorchCodec cannot load FFmpeg
DLLs. If that happens, monkey-patch `demucs.audio.save_audio` and
`demucs.separate.save_audio` to write WAV via `soundfile.write`.

Known good outputs from the 2026-06-22 run:

```text
.tools\whisper-output\demucs\htdemucs\132856643674-自娱自乐\vocals.wav
.tools\whisper-output\demucs-vocals-16k-ziyu.wav
.tools\whisper-output\funasr-demucs-vocals-ziyu-20260622.txt
```
