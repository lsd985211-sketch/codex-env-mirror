#!/usr/bin/env python3
"""Small Windows GUI wrapper around audio_toolkit.py."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from tkinter import (
    BOTH,
    END,
    LEFT,
    RIGHT,
    X,
    BooleanVar,
    Button,
    Checkbutton,
    Entry,
    Frame,
    Label,
    LabelFrame,
    Listbox,
    Menu,
    StringVar,
    Tk,
    filedialog,
    messagebox,
    ttk,
)
from tkinter.scrolledtext import ScrolledText


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOLKIT = PROJECT_ROOT / "_bridge" / "audio_toolkit" / "audio_toolkit.py"
WORK_ROOT = PROJECT_ROOT / ".tools" / "audio-work"
GUI_OUTPUT = WORK_ROOT / "gui-output"
PREVIEW_MAX_CHARS = 12000
PREVIEW_MAX_LINES = 300
LIVE_PREVIEW_INTERVAL_SECONDS = 0.6
AUDIO_EXTENSIONS = {
    ".aac",
    ".aiff",
    ".ape",
    ".flac",
    ".m4a",
    ".mp3",
    ".ogg",
    ".opus",
    ".wav",
    ".wma",
}


def safe_stem(path: Path) -> str:
    value = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in path.stem)
    return value[:80] or "audio"


def open_path(path: Path) -> None:
    target = path if path.is_dir() else path.parent
    os.startfile(str(target))  # type: ignore[attr-defined]


class AudioToolkitGui:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("本地音频工具箱")
        self.root.geometry("980x700")
        self.root.minsize(860, 600)
        self.root.configure(bg="#f5f6f8")
        self.files: list[Path] = []
        self.current_output: Path | None = None
        self.current_result_file: Path | None = None
        self.running = False

        self.status = StringVar(value="选择或拖入音频文件，然后点击操作。")
        self.hotword = StringVar(value="")
        self.reference_lyrics: Path | None = None
        self.convert_format = StringVar(value="mp3")
        self.trim_start = StringVar(value="00:00:00")
        self.trim_duration = StringVar(value="30")
        self.overwrite = BooleanVar(value=False)

        self._build_ui()
        self._bind_drag_drop_notice()

    def _build_ui(self) -> None:
        top = Frame(self.root, bg="#f5f6f8", padx=14, pady=12)
        top.pack(fill=X)

        Label(top, text="本地音频工具箱", bg="#f5f6f8", fg="#20242a", font=("Microsoft YaHei UI", 16, "bold")).pack(
            side=LEFT
        )
        Button(top, text="选择音频", command=self.add_files, width=12).pack(side=RIGHT, padx=(8, 0))
        Button(top, text="清空", command=self.clear_files, width=8).pack(side=RIGHT)

        main = Frame(self.root, bg="#f5f6f8", padx=14)
        main.pack(fill=BOTH, expand=True)

        left = LabelFrame(main, text="文件", padx=8, pady=8, bg="#f5f6f8")
        left.pack(side=LEFT, fill=BOTH, expand=False, padx=(0, 10))
        self.file_list = Listbox(left, width=48, height=24, activestyle="dotbox")
        self.file_list.pack(fill=BOTH, expand=True)
        self.file_list.bind("<Delete>", lambda _event: self.remove_selected())
        Button(left, text="移除选中", command=self.remove_selected).pack(fill=X, pady=(8, 0))

        right = Frame(main, bg="#f5f6f8")
        right.pack(side=LEFT, fill=BOTH, expand=True)

        ops = LabelFrame(right, text="操作", padx=10, pady=10, bg="#f5f6f8")
        ops.pack(fill=X)

        row1 = Frame(ops, bg="#f5f6f8")
        row1.pack(fill=X, pady=3)
        Button(row1, text="检查信息", command=lambda: self.run_for_files("inspect"), width=12).pack(side=LEFT, padx=3)
        Button(row1, text="读取标签", command=lambda: self.run_for_files("metadata"), width=12).pack(side=LEFT, padx=3)
        Button(row1, text="基础分析", command=lambda: self.run_for_files("analyze"), width=12).pack(side=LEFT, padx=3)
        Button(row1, text="检测静音", command=lambda: self.run_for_files("silence-detect"), width=12).pack(side=LEFT, padx=3)

        row2 = Frame(ops, bg="#f5f6f8")
        row2.pack(fill=X, pady=3)
        Button(row2, text="中文转文字", command=lambda: self.run_for_files("transcribe-zh"), width=12).pack(
            side=LEFT, padx=3
        )
        Button(row2, text="快速歌词/LRC", command=lambda: self.run_for_files("lyrics-fast-zh"), width=12).pack(
            side=LEFT, padx=3
        )
        Button(row2, text="高质量歌词/LRC", command=lambda: self.run_for_files("lyrics-draft-zh"), width=14).pack(
            side=LEFT, padx=3
        )
        Button(row2, text="精修歌词/LRC", command=lambda: self.run_for_files("lyrics-ultra-zh"), width=14).pack(
            side=LEFT, padx=3
        )

        row3 = Frame(ops, bg="#f5f6f8")
        row3.pack(fill=X, pady=3)
        Button(row3, text="参考歌词/LRC", command=self.run_reference_lyrics_align, width=14).pack(
            side=LEFT, padx=3
        )
        Button(row3, text="ASR WAV", command=lambda: self.run_for_files("convert-asr-wav"), width=12).pack(side=LEFT, padx=3)
        Button(row3, text="规范响度", command=lambda: self.run_for_files("normalize"), width=12).pack(side=LEFT, padx=3)

        opts = LabelFrame(right, text="参数", padx=10, pady=8, bg="#f5f6f8")
        opts.pack(fill=X, pady=(10, 0))

        hot = Frame(opts, bg="#f5f6f8")
        hot.pack(fill=X, pady=2)
        Label(hot, text="热词", width=8, anchor="w", bg="#f5f6f8").pack(side=LEFT)
        Entry(hot, textvariable=self.hotword).pack(side=LEFT, fill=X, expand=True)

        conv = Frame(opts, bg="#f5f6f8")
        conv.pack(fill=X, pady=2)
        Label(conv, text="转格式", width=8, anchor="w", bg="#f5f6f8").pack(side=LEFT)
        ttk.Combobox(conv, textvariable=self.convert_format, values=["mp3", "wav", "flac", "m4a"], width=8, state="readonly").pack(
            side=LEFT
        )
        Button(conv, text="执行转换", command=lambda: self.run_for_files("convert"), width=12).pack(side=LEFT, padx=8)
        Checkbutton(conv, text="覆盖输出", variable=self.overwrite, bg="#f5f6f8").pack(side=LEFT)

        trim = Frame(opts, bg="#f5f6f8")
        trim.pack(fill=X, pady=2)
        Label(trim, text="裁剪", width=8, anchor="w", bg="#f5f6f8").pack(side=LEFT)
        Label(trim, text="开始", bg="#f5f6f8").pack(side=LEFT)
        Entry(trim, textvariable=self.trim_start, width=10).pack(side=LEFT, padx=(4, 8))
        Label(trim, text="时长秒", bg="#f5f6f8").pack(side=LEFT)
        Entry(trim, textvariable=self.trim_duration, width=8).pack(side=LEFT, padx=(4, 8))
        Button(trim, text="执行裁剪", command=lambda: self.run_for_files("trim"), width=12).pack(side=LEFT)

        result_box = LabelFrame(right, text="结果预览", padx=8, pady=8, bg="#f5f6f8")
        result_box.pack(fill=BOTH, expand=True, pady=(10, 0))
        self.result = ScrolledText(result_box, height=9, wrap="word")
        self.result.pack(fill=BOTH, expand=True)

        log_box = LabelFrame(right, text="日志", padx=8, pady=8, bg="#f5f6f8")
        log_box.pack(fill=BOTH, expand=True, pady=(10, 0))
        self.log = ScrolledText(log_box, height=10, wrap="word")
        self.log.pack(fill=BOTH, expand=True)

        bottom = Frame(self.root, bg="#f5f6f8", padx=14, pady=10)
        bottom.pack(fill=X)
        Label(bottom, textvariable=self.status, bg="#f5f6f8", anchor="w").pack(side=LEFT, fill=X, expand=True)
        Button(bottom, text="打开结果文件", command=self.open_result_file, width=14).pack(side=RIGHT, padx=(8, 0))
        Button(bottom, text="打开输出目录", command=self.open_output, width=14).pack(side=RIGHT)

        menu = Menu(self.root)
        file_menu = Menu(menu, tearoff=0)
        file_menu.add_command(label="选择音频", command=self.add_files)
        file_menu.add_command(label="打开结果文件", command=self.open_result_file)
        file_menu.add_command(label="打开输出目录", command=self.open_output)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.root.destroy)
        menu.add_cascade(label="文件", menu=file_menu)
        self.root.config(menu=menu)

    def _bind_drag_drop_notice(self) -> None:
        # Tkinter's stock Windows build does not support Explorer drag-and-drop.
        self.root.bind("<Control-o>", lambda _event: self.add_files())

    def add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="选择音频文件",
            filetypes=[
                ("音频文件", "*.wav *.mp3 *.flac *.m4a *.aac *.ogg *.opus *.wma *.aiff *.ape"),
                ("所有文件", "*.*"),
            ],
        )
        self._append_files(Path(p) for p in paths)

    def _append_files(self, paths: object) -> None:
        added = 0
        for path in paths:
            p = Path(path).expanduser().resolve()
            if not p.is_file() or p in self.files:
                continue
            self.files.append(p)
            self.file_list.insert(END, str(p))
            added += 1
        if added:
            self.status.set(f"已添加 {added} 个文件。")

    def clear_files(self) -> None:
        self.files.clear()
        self.file_list.delete(0, END)
        self.status.set("文件列表已清空。")

    def remove_selected(self) -> None:
        indexes = list(self.file_list.curselection())
        for index in reversed(indexes):
            del self.files[index]
            self.file_list.delete(index)
        if indexes:
            self.status.set(f"已移除 {len(indexes)} 个文件。")

    def selected_files(self) -> list[Path]:
        indexes = list(self.file_list.curselection())
        if indexes:
            return [self.files[index] for index in indexes]
        return list(self.files)

    def append_log(self, text: str) -> None:
        self.log.insert(END, text)
        self.log.see(END)

    def show_result(self, text: str) -> None:
        self.result.delete("1.0", END)
        self.result.insert(END, text)
        self.result.see("1.0")

    def show_live_preview(self, operation: str, source: Path, result_file: Path | None) -> None:
        parts = [f"文件：{source}"]
        if result_file:
            if result_file.exists():
                self.show_result(self.preview_text(result_file))
                return
            parts.append(f"结果文件尚未生成：{result_file}")
        if operation == "lyrics-align-zh" and self.reference_lyrics and self.reference_lyrics.exists():
            parts.extend(["", "参考歌词已加载，正在生成时间轴。", "", self.preview_text(self.reference_lyrics)])
        elif operation in {"lyrics-fast-zh", "lyrics-draft-zh", "lyrics-ultra-zh"}:
            parts.extend(["", "歌词尚未生成，正在识别音频。完成后这里会显示 LRC。"])
        elif operation == "transcribe-zh":
            parts.extend(["", "文字尚未生成，正在识别音频。完成后这里会显示转写结果。"])
        else:
            parts.extend(["", "结果尚未生成，完成后这里会显示内容预览。"])
        self.show_result("\n".join(parts))

    def set_running(self, running: bool, status: str) -> None:
        self.running = running
        self.status.set(status)

    def run_for_files(self, operation: str) -> None:
        if self.running:
            messagebox.showinfo("任务正在运行", "请等待当前任务完成。")
            return
        files = self.selected_files()
        if not files:
            messagebox.showinfo("没有文件", "请先选择音频文件。")
            return
        GUI_OUTPUT.mkdir(parents=True, exist_ok=True)
        self.log.delete("1.0", END)
        self.result.delete("1.0", END)
        self.current_result_file = None
        self.set_running(True, f"正在执行 {operation}...")
        thread = threading.Thread(target=self._worker, args=(operation, files), daemon=True)
        thread.start()

    def run_reference_lyrics_align(self) -> None:
        path = filedialog.askopenfilename(
            title="选择参考歌词文件",
            filetypes=[
                ("歌词或文本", "*.lrc *.txt"),
                ("所有文件", "*.*"),
            ],
        )
        if not path:
            return
        self.reference_lyrics = Path(path).expanduser().resolve()
        self.run_for_files("lyrics-align-zh")

    def _worker(self, operation: str, files: list[Path]) -> None:
        ok = True
        last_output: Path | None = None
        for source in files:
            self.root.after(0, self.append_log, f"\n=== {operation}: {source} ===\n")
            command, output = self.build_command(operation, source)
            last_output = output or last_output
            log_dir = output if output and output.suffix == "" else (output.parent if output else GUI_OUTPUT / safe_stem(source))
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"{operation}.log"
            cached_result = self.result_file_for(operation, output, source)
            result_file = cached_result
            self.root.after(0, self.show_live_preview, operation, source, result_file)
            cache_allowed = operation != "lyrics-align-zh"
            if cached_result and cached_result.exists() and cache_allowed and not self.overwrite.get():
                self.current_output = output or cached_result
                self.current_result_file = cached_result
                message = f"复用已有结果：{cached_result}\n"
                self.root.after(0, self.append_log, message)
                self.root.after(0, self.status.set, "已复用已有结果。")
                preview = self.preview_text(cached_result)
                if preview:
                    self.root.after(0, self.show_result, preview)
                log_path.write_text(
                    f"cached: true\noperation: {operation}\ninput: {source}\noutput_file: {cached_result}\n",
                    encoding="utf-8",
                    errors="replace",
                )
                continue
            if output and output.suffix:
                output.parent.mkdir(parents=True, exist_ok=True)
            lines: list[str] = [f"command: {' '.join(command)}\n\n"]
            proc = subprocess.Popen(
                command,
                cwd=str(PROJECT_ROOT),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding="utf-8",
                errors="replace",
            )
            assert proc.stdout is not None
            last_preview = 0.0
            for line in proc.stdout:
                lines.append(line)
                self.root.after(0, self.append_log, line)
                now = time.monotonic()
                if now - last_preview >= LIVE_PREVIEW_INTERVAL_SECONDS:
                    last_preview = now
                    self.root.after(0, self.show_live_preview, operation, source, result_file)
            returncode = proc.wait()
            result_file = self.result_file_for(operation, output, source)
            if result_file and result_file.exists():
                self.current_result_file = result_file
                lines.append(f"\noutput_file: {result_file}\n")
                self.root.after(0, self.append_log, f"\n输出文件：{result_file}\n")
                preview = self.preview_text(result_file)
                if preview:
                    self.root.after(0, self.show_result, preview)
            if returncode != 0:
                ok = False
                lines.append(f"\nfailed: returncode={returncode}\n")
                self.root.after(0, self.append_log, f"\n失败，退出码 {returncode}\n")
            else:
                lines.append("\ncompleted\n")
                self.root.after(0, self.append_log, "\n完成。\n")
            log_path.write_text("".join(lines), encoding="utf-8", errors="replace")
        self.current_output = last_output or GUI_OUTPUT
        final = "任务完成。" if ok else "任务结束，但有失败项。"
        self.root.after(0, self.set_running, False, final)

    def result_file_for(self, operation: str, output: Path | None, source: Path) -> Path | None:
        if output is None:
            return None
        if operation == "transcribe-zh":
            return output
        if operation == "lyrics-fast-zh":
            stem = safe_stem(source)
            return output / f"{stem}.fast.lyrics.zh.lrc"
        if operation == "lyrics-draft-zh":
            stem = safe_stem(source)
            return output / f"{stem}.lyrics-draft.zh.lrc"
        if operation == "lyrics-ultra-zh":
            stem = safe_stem(source)
            return output / f"{stem}.lyrics-ultra.zh.lrc"
        if operation == "lyrics-align-zh":
            stem = safe_stem(source)
            return output / f"{stem}.aligned.zh.lrc"
        if operation in {"inspect", "metadata", "analyze", "silence-detect", "convert-asr-wav"}:
            return output / f"{operation}.log"
        return output

    def preview_text(self, path: Path) -> str:
        if path.suffix.lower() not in {".txt", ".log", ".json", ".lrc"}:
            return f"已生成文件：{path}"
        text, truncated = self.read_preview_text(path)
        text = text.strip()
        if not text:
            return f"结果文件为空：{path}"
        if path.suffix.lower() == ".lrc":
            text = self.format_lrc_preview(text)
        suffix = "\n\n……内容较长，已截断预览。请打开结果文件查看完整内容。" if truncated else ""
        return f"文件：{path}\n\n{text}{suffix}"

    def read_preview_text(self, path: Path) -> tuple[str, bool]:
        chunks: list[str] = []
        total = 0
        truncated = False
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for index, line in enumerate(fh):
                if index >= PREVIEW_MAX_LINES:
                    truncated = True
                    break
                remaining = PREVIEW_MAX_CHARS - total
                if remaining <= 0:
                    truncated = True
                    break
                if len(line) > remaining:
                    chunks.append(line[:remaining])
                    truncated = True
                    break
                chunks.append(line)
                total += len(line)
        return "".join(chunks), truncated

    def format_lrc_preview(self, text: str) -> str:
        lines: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            match = re.match(r"^(\[[0-9]{2,}:[0-9]{2}(?:\.[0-9]{1,3})?\])(.*)$", line)
            if not match:
                lines.append(line)
                continue
            timestamp, lyric = match.groups()
            lyric = lyric.strip()
            if not lyric:
                lines.append(timestamp)
                continue
            parts = self.split_preview_sentences(lyric)
            for index, part in enumerate(parts):
                prefix = timestamp if index == 0 else " " * len(timestamp)
                lines.append(f"{prefix} {part}")
        return "\n".join(lines)

    def split_preview_sentences(self, text: str) -> list[str]:
        parts = re.split(r"(?<=[。！？!?；;])", text)
        refined: list[str] = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if len(part) <= 42:
                refined.append(part)
                continue
            comma_parts = [item.strip() for item in re.split(r"(?<=[，,、])", part) if item.strip()]
            if len(comma_parts) > 1:
                refined.extend(comma_parts)
            else:
                refined.append(part)
        return refined

    def build_command(self, operation: str, source: Path) -> tuple[list[str], Path | None]:
        base = [sys.executable, str(TOOLKIT), operation, str(source)]
        stem = safe_stem(source)
        output_dir = GUI_OUTPUT / stem
        overwrite = ["--overwrite"] if self.overwrite.get() else []
        hotword = self.hotword.get().strip()

        if operation in {"inspect", "metadata", "analyze", "silence-detect", "convert-asr-wav"}:
            return base, output_dir
        if operation == "transcribe-zh":
            output = output_dir / f"{stem}.zh.txt"
            command = base + ["--output", str(output), *overwrite]
            if hotword:
                command += ["--hotword", hotword]
            return command, output
        if operation in {"lyrics-fast-zh", "lyrics-draft-zh", "lyrics-ultra-zh"}:
            command = base + ["--output-dir", str(output_dir), *overwrite]
            if operation == "lyrics-ultra-zh":
                command += ["--demucs-device", "cuda"]
            if hotword:
                command += ["--hotword", hotword]
            return command, output_dir
        if operation == "lyrics-align-zh":
            if self.reference_lyrics is None:
                raise ValueError("Reference lyrics file is required")
            command = base + [
                "--reference",
                str(self.reference_lyrics),
                "--output-dir",
                str(output_dir),
                "--use-demucs",
                "--demucs-device",
                "cuda",
                "--overwrite",
            ]
            if hotword:
                command += ["--hotword", hotword]
            return command, output_dir
        if operation == "convert":
            ext = self.convert_format.get().strip(".").lower() or "mp3"
            output = output_dir / f"{stem}.{ext}"
            return base + [str(output), *overwrite], output
        if operation == "trim":
            output = output_dir / f"{stem}.trim.wav"
            return base + [str(output), "--start", self.trim_start.get(), "--duration", self.trim_duration.get(), *overwrite], output
        if operation == "normalize":
            output = output_dir / f"{stem}.normalized.wav"
            return base + [str(output), *overwrite], output
        raise ValueError(f"Unsupported operation: {operation}")

    def open_output(self) -> None:
        target = self.current_output or GUI_OUTPUT
        target.mkdir(parents=True, exist_ok=True)
        open_path(target)

    def open_result_file(self) -> None:
        if not self.current_result_file or not self.current_result_file.exists():
            messagebox.showinfo("没有结果文件", "还没有可打开的结果文件。")
            return
        os.startfile(str(self.current_result_file))  # type: ignore[attr-defined]


def main(argv: list[str] | None = None) -> int:
    root = Tk()
    app = AudioToolkitGui(root)
    if argv is None:
        argv = sys.argv[1:]
    files = [Path(arg) for arg in argv if Path(arg).suffix.lower() in AUDIO_EXTENSIONS or Path(arg).is_file()]
    if files:
        app._append_files(files)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
