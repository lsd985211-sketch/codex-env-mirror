#!/usr/bin/env python3
"""Small local GUI for updating the Reasonix DeepSeek API key."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from tkinter import BOTH, END, LEFT, RIGHT, X, Button, Entry, Frame, Label, Menu, StringVar, Tk, messagebox

sys.path.insert(0, str(Path(__file__).resolve().parent))

from reasonix_key_store import (
    ReasonixKeyStoreError,
    check_credentials_target,
    get_credentials_file,
    update_deepseek_api_key,
)


WINDOW_TITLE = "Reasonix 密钥工具"
WINDOW_SIZE = "700x280"


class ReasonixKeyToolGui:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title(WINDOW_TITLE)
        self.root.geometry(WINDOW_SIZE)
        self.root.minsize(660, 260)
        self.root.maxsize(900, 360)

        self.key_value = StringVar(value="")
        self.status = StringVar(value="输入新的 DeepSeek 密钥，然后点击更新。")
        self.context_menu: Menu | None = None
        self.target_file = get_credentials_file()
        self.show_button: Button | None = None
        self.copy_path_button: Button | None = None
        self.open_dir_button: Button | None = None
        self.check_button: Button | None = None
        self.clear_button: Button | None = None
        self.update_button: Button | None = None

        self._build_ui()

    def _build_ui(self) -> None:
        container = Frame(self.root, padx=16, pady=14)
        container.pack(fill=BOTH, expand=True)

        Label(
            container,
            text="更新 Reasonix 的 DEEPSEEK_API_KEY",
            font=("Microsoft YaHei UI", 13, "bold"),
            anchor="w",
        ).pack(fill=X)

        Label(
            container,
            text="该工具不会显示旧密钥，只会将你输入的新值写入凭据文件。",
            anchor="w",
            pady=8,
        ).pack(fill=X)

        path_row = Frame(container)
        path_row.pack(fill=X)

        Label(
            path_row,
            text=f"目标文件：{self._summarize_path(self.target_file)}",
            anchor="w",
            justify="left",
        ).pack(side=LEFT, fill=X, expand=True)

        self.copy_path_button = Button(path_row, text="复制路径", width=10, command=self.copy_target_path)
        self.copy_path_button.pack(side=RIGHT)

        tools_row = Frame(container)
        tools_row.pack(fill=X, pady=(8, 0))

        self.open_dir_button = Button(tools_row, text="打开目录", width=10, command=self.open_target_directory)
        self.open_dir_button.pack(side=LEFT)

        self.check_button = Button(tools_row, text="检查目标文件", width=12, command=self.check_target_file)
        self.check_button.pack(side=LEFT, padx=(10, 0))

        entry_row = Frame(container)
        entry_row.pack(fill=X, pady=(10, 10))

        self.entry = Entry(entry_row, textvariable=self.key_value, show="*", width=54)
        self.entry.pack(side=LEFT, fill=X, expand=True)
        self.entry.focus_set()
        self._bind_entry_shortcuts()

        self.clear_button = Button(entry_row, text="清空", width=8, command=self.clear_input)
        self.clear_button.pack(side=RIGHT, padx=(10, 0))

        self.show_button = Button(entry_row, text="按住显示", width=10)
        self.show_button.pack(side=RIGHT, padx=(10, 0))
        self.show_button.bind("<ButtonPress-1>", self._show_key)
        self.show_button.bind("<ButtonRelease-1>", self._hide_key)
        self.show_button.bind("<Leave>", self._hide_key)

        action_row = Frame(container)
        action_row.pack(fill=X, pady=(4, 10))

        self.update_button = Button(action_row, text="更新", width=10, command=self.update_key)
        self.update_button.pack(side=LEFT)

        Label(container, textvariable=self.status, anchor="w").pack(fill=X, pady=(12, 0))

    def clear_input(self) -> None:
        self.entry.delete(0, END)
        self.status.set("输入框已清空。")
        self.entry.focus_set()

    def _show_key(self, _event=None) -> str:
        self.entry.configure(show="")
        return "break"

    def _hide_key(self, _event=None) -> str:
        self.entry.configure(show="*")
        return "break"

    def _summarize_path(self, path: Path) -> str:
        value = str(path)
        if len(value) <= 64:
            return value
        return f"...\\{path.parent.name}\\{path.name}"

    def copy_target_path(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(str(self.target_file))
        self.status.set("目标文件路径已复制。")
        self.entry.focus_set()

    def open_target_directory(self) -> None:
        os.startfile(str(self.target_file.parent))  # type: ignore[attr-defined]
        self.status.set("已打开目标目录。")
        self.entry.focus_set()

    def check_target_file(self) -> None:
        result = check_credentials_target()
        lines = [
            f"目标文件：{result.credentials_path}",
            f"目录存在：{'是' if result.parent_exists else '否'}",
            f"文件存在：{'是' if result.exists else '否'}",
            f"包含 DEEPSEEK_API_KEY：{'是' if result.contains_target_key else '否'}",
            f"当前可写：{'是' if result.writable else '否'}",
        ]
        self.status.set("目标文件检查完成。")
        messagebox.showinfo("检查结果", "\n".join(lines))
        self.entry.focus_set()

    def set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        if self.update_button is not None:
            self.update_button.configure(state=state)
        if self.clear_button is not None:
            self.clear_button.configure(state=state)
        if self.copy_path_button is not None:
            self.copy_path_button.configure(state=state)
        if self.open_dir_button is not None:
            self.open_dir_button.configure(state=state)
        if self.check_button is not None:
            self.check_button.configure(state=state)

    def _bind_entry_shortcuts(self) -> None:
        self.entry.bind("<Control-v>", self._paste_from_clipboard)
        self.entry.bind("<Control-V>", self._paste_from_clipboard)
        self.entry.bind("<Shift-Insert>", self._paste_from_clipboard)
        self.entry.bind("<<Paste>>", self._paste_from_clipboard)
        self.entry.bind("<Button-3>", self._show_context_menu)
        self.entry.bind("<Button-2>", self._show_context_menu)

        self.context_menu = Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="粘贴", command=self.paste_from_clipboard)
        self.context_menu.add_command(label="清空", command=self.clear_input)

    def _show_context_menu(self, event) -> str:
        if self.context_menu is not None:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def paste_from_clipboard(self) -> None:
        try:
            text = self.root.clipboard_get()
        except Exception:
            self.status.set("剪贴板中没有可粘贴的文本。")
            self.entry.focus_set()
            return

        self.entry.focus_set()
        self.entry.delete(0, END)
        self.entry.insert(0, text)
        self.status.set("已从剪贴板粘贴密钥。")

    def _paste_from_clipboard(self, _event=None) -> str:
        self.paste_from_clipboard()
        return "break"

    def update_key(self) -> None:
        submitted = self.key_value.get()
        self.set_busy(True)
        self.status.set("正在更新凭据文件...")
        self.root.update_idletasks()

        try:
            result = update_deepseek_api_key(submitted)
        except ReasonixKeyStoreError as exc:
            self.set_busy(False)
            self.status.set("更新失败。")
            messagebox.showerror("更新失败", str(exc))
            self.entry.focus_set()
            return
        except Exception:
            self.set_busy(False)
            self.status.set("更新失败。")
            messagebox.showerror("更新失败", "发生了未预期错误，原文件已保留。")
            self.entry.focus_set()
            return

        self.set_busy(False)
        self.key_value.set("")
        mode_text = "已替换原有密钥。" if result.updated_existing_line else "原文件中没有该项，已追加新密钥。"
        self.status.set("更新成功。")
        messagebox.showinfo(
            "更新成功",
            "\n".join(
                [
                    "Reasonix 密钥已更新。",
                    mode_text,
                    f"目标文件：{result.credentials_path}",
                    f"备份文件：{result.backup_path}",
                    "如果 Reasonix 当前已打开，建议重新打开相关会话后再使用新密钥。",
                ]
            ),
        )
        self.entry.focus_set()


def main() -> int:
    root = Tk()
    ReasonixKeyToolGui(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
