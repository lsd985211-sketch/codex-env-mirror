from __future__ import annotations

import ctypes
import json
import os
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pyautogui
import pyperclip
from PIL import ImageChops, ImageGrab


USER32 = ctypes.windll.user32
SW_RESTORE = 9
WM_CLOSE = 0x0010

WEIXIN_EXE_CANDIDATES = (
    Path(r"C:\Program Files\Tencent\Weixin\Weixin.exe"),
    Path(r"C:\Program Files (x86)\Tencent\Weixin\Weixin.exe"),
    Path(r"C:\Program Files\Tencent\WeChat\WeChat.exe"),
    Path(r"C:\Program Files (x86)\Tencent\WeChat\WeChat.exe"),
    Path.home() / "AppData" / "Roaming" / "Tencent" / "Weixin" / "Weixin.exe",
    Path.home() / "AppData" / "Roaming" / "Tencent" / "WeChat" / "WeChat.exe",
)


@dataclass
class WindowInfo:
    title: str
    left: int
    top: int
    width: int
    height: int
    handle: int | None = None
    active: bool = False
    minimized_or_offscreen: bool = False

    @property
    def area(self) -> int:
        return max(self.width, 0) * max(self.height, 0)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"area": self.area}


def _candidate_windows() -> list[WindowInfo]:
    windows: list[WindowInfo] = []
    for win in pyautogui.getAllWindows():
        title = str(getattr(win, "title", "") or "")
        if "微信" not in title and "Weixin" not in title and "WeChat" not in title:
            continue
        width = int(getattr(win, "width", 0) or 0)
        height = int(getattr(win, "height", 0) or 0)
        if width <= 0 or height <= 0:
            continue
        handle = getattr(win, "_hWnd", None)
        windows.append(
            WindowInfo(
                title=title,
                left=int(getattr(win, "left", 0) or 0),
                top=int(getattr(win, "top", 0) or 0),
                width=width,
                height=height,
                handle=int(handle) if handle else None,
                active=bool(getattr(win, "isActive", False)),
                minimized_or_offscreen=(
                    bool(getattr(win, "isMinimized", False))
                    or int(getattr(win, "left", 0) or 0) < -1000
                    or int(getattr(win, "top", 0) or 0) < -1000
                ),
            )
        )
    windows.sort(key=lambda item: item.area, reverse=True)
    return windows


def list_windows() -> list[dict[str, Any]]:
    return [item.to_dict() for item in _candidate_windows()]


def discover_executable() -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for path in WEIXIN_EXE_CANDIDATES:
        candidates.append({"path": str(path), "exists": path.exists(), "source": "known_path"})
        if path.exists():
            return {"ok": True, "path": str(path), "source": "known_path", "candidates": candidates}

    for name in ("Weixin.exe", "WeChat.exe"):
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            if not directory:
                continue
            path = Path(directory) / name
            candidates.append({"path": str(path), "exists": path.exists(), "source": "PATH"})
            if path.exists():
                return {"ok": True, "path": str(path), "source": "PATH", "candidates": candidates}
    return {"ok": False, "path": "", "source": "", "candidates": candidates}


def best_window() -> WindowInfo:
    candidates = _candidate_windows()
    if not candidates:
        raise RuntimeError("No visible Weixin/WeChat desktop window was found.")
    return candidates[0]


def activate_window(wait: float = 0.3) -> dict[str, Any]:
    info = best_window()
    if info.handle:
        USER32.ShowWindow(int(info.handle), SW_RESTORE)
        USER32.SetForegroundWindow(int(info.handle))
    else:
        for win in pyautogui.getAllWindows():
            if getattr(win, "title", "") == info.title:
                win.activate()
                break
    time.sleep(wait)
    refreshed = best_window()
    return {"activated": refreshed.to_dict()}


def open_weixin(*, wait_seconds: float = 8.0) -> dict[str, Any]:
    before = list_windows()
    if before:
        activated = activate_window()
        return {
            "ok": True,
            "action": "open_weixin",
            "mode": "activated_existing_window",
            "before": before,
            "after": list_windows(),
            "activated": activated.get("activated"),
        }

    discovered = discover_executable()
    if not discovered.get("ok"):
        return {
            "ok": False,
            "action": "open_weixin",
            "reason": "weixin_executable_not_found",
            "discovery": discovered,
        }
    exe = str(discovered["path"])
    proc = subprocess.Popen([exe], cwd=str(Path(exe).parent), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + max(0.5, float(wait_seconds))
    after: list[dict[str, Any]] = []
    while time.time() < deadline:
        after = list_windows()
        if after:
            break
        time.sleep(0.25)
    activated: dict[str, Any] | None = None
    if after:
        activated = activate_window().get("activated")
    return {
        "ok": bool(after),
        "action": "open_weixin",
        "mode": "launched_process",
        "pid": proc.pid,
        "executable": exe,
        "discovery": discovered,
        "after": after,
        "activated": activated,
        "reason": "" if after else "process_started_but_window_not_found",
    }


def close_weixin(*, confirm_close: str, wait_seconds: float = 3.0) -> dict[str, Any]:
    if confirm_close != "CLOSE":
        raise RuntimeError("Refusing to close Weixin: pass --confirm-close CLOSE for explicit approval.")
    before = list_windows()
    if not before:
        return {"ok": True, "action": "close_weixin", "mode": "already_closed", "before": [], "after": []}
    info = best_window()
    if not info.handle:
        raise RuntimeError("Cannot close Weixin: no window handle found.")
    USER32.PostMessageW(int(info.handle), WM_CLOSE, 0, 0)
    deadline = time.time() + max(0.5, float(wait_seconds))
    after = list_windows()
    while time.time() < deadline:
        after = list_windows()
        if not after:
            break
        time.sleep(0.2)
    return {
        "ok": True,
        "action": "close_weixin",
        "mode": "posted_wm_close",
        "handle": info.handle,
        "before": before,
        "after": after,
        "window_gone": not bool(after),
    }


def screenshot(output: str | Path | None = None) -> dict[str, Any]:
    info = best_window()
    if info.minimized_or_offscreen:
        activate_window()
        info = best_window()
    bbox = (info.left, info.top, info.left + info.width, info.top + info.height)
    image = ImageGrab.grab(bbox=bbox)
    if output is None:
        root = Path.cwd() / "_bridge" / "runtime" / "cli_anything_weixin"
        root.mkdir(parents=True, exist_ok=True)
        output = root / f"weixin_window_{int(time.time())}.png"
    out_path = Path(output).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path)
    return {
        "path": str(out_path),
        "window": info.to_dict(),
        "size": {"width": image.width, "height": image.height},
    }


def _runtime_dir(prefix: str) -> Path:
    root = Path.cwd() / "_bridge" / "runtime" / "cli_anything_weixin" / f"{prefix}_{int(time.time())}"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _click_relative(info: WindowInfo, *, x_ratio: float, y_ratio: float) -> dict[str, Any]:
    x_ratio = min(max(float(x_ratio), 0.0), 1.0)
    y_ratio = min(max(float(y_ratio), 0.0), 1.0)
    x = int(info.left + info.width * x_ratio)
    y = int(info.top + info.height * y_ratio)
    pyautogui.click(x, y)
    return {"screen_x": x, "screen_y": y, "x_ratio": x_ratio, "y_ratio": y_ratio}


def dialog_windows() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for win in pyautogui.getAllWindows():
        title = str(getattr(win, "title", "") or "")
        if not title:
            continue
        if not any(marker in title for marker in ("微信", "Weixin", "WeChat", "选择文件", "打开")):
            continue
        width = int(getattr(win, "width", 0) or 0)
        height = int(getattr(win, "height", 0) or 0)
        if width <= 0 or height <= 0:
            continue
        handle = getattr(win, "_hWnd", None)
        items.append(
            {
                "title": title,
                "left": int(getattr(win, "left", 0) or 0),
                "top": int(getattr(win, "top", 0) or 0),
                "width": width,
                "height": height,
                "handle": int(handle) if handle else None,
                "active": bool(getattr(win, "isActive", False)),
                "minimized_or_offscreen": (
                    bool(getattr(win, "isMinimized", False))
                    or int(getattr(win, "left", 0) or 0) < -1000
                    or int(getattr(win, "top", 0) or 0) < -1000
                ),
            }
        )
    items.sort(key=lambda item: item["width"] * item["height"], reverse=True)
    return items


def _input_region_diff(
    before_path: str | Path,
    after_path: str | Path,
    *,
    x_ratio: float,
    y_ratio: float,
) -> dict[str, Any]:
    from PIL import Image

    img_before = Image.open(before_path).convert("RGB")
    img_after = Image.open(after_path).convert("RGB")
    width, height = img_before.size
    cx = int(width * min(max(float(x_ratio), 0.0), 1.0))
    cy = int(height * min(max(float(y_ratio), 0.0), 1.0))
    box = (
        max(0, cx - 260),
        max(0, cy - 70),
        min(width, cx + 360),
        min(height, cy + 70),
    )
    crop_before = img_before.crop(box)
    crop_after = img_after.crop(box)
    diff = ImageChops.difference(crop_before, crop_after)
    changed = 0
    for pixel in diff.getdata():
        if max(pixel) > 12:
            changed += 1
    total = max(diff.width * diff.height, 1)
    ratio = changed / total
    return {
        "box": {"x1": box[0], "y1": box[1], "x2": box[2], "y2": box[3]},
        "changed_pixels": changed,
        "total_pixels": total,
        "changed_ratio": ratio,
        "visible_change": ratio >= 0.002,
    }


def focus_input(*, x_ratio: float = 0.55, y_ratio: float = 0.90) -> dict[str, Any]:
    activated = activate_window()
    info = best_window()
    x_ratio = min(max(float(x_ratio), 0.0), 1.0)
    y_ratio = min(max(float(y_ratio), 0.0), 1.0)
    x = int(info.left + info.width * x_ratio)
    y = int(info.top + info.height * y_ratio)
    pyautogui.click(x, y)
    time.sleep(0.15)
    return {
        "ok": True,
        "action": "focus_input",
        "click": {"screen_x": x, "screen_y": y, "x_ratio": x_ratio, "y_ratio": y_ratio},
        "window": info.to_dict(),
        "activated": activated.get("activated"),
    }


def select_chat_row(
    *,
    index: int = 3,
    x: int = 180,
    first_y: int = 135,
    row_height: int = 82,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    if index < 1:
        raise RuntimeError("Chat row index must be >= 1.")
    if output_dir is None:
        output_dir = Path.cwd() / "_bridge" / "runtime" / "cli_anything_weixin" / f"select_row_{int(time.time())}"
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    before = screenshot(out_dir / "01_before.png")
    info = best_window()
    rel_y = int(first_y + (index - 1) * row_height)
    screen_x = int(info.left + x)
    screen_y = int(info.top + rel_y)
    pyautogui.click(screen_x, screen_y)
    time.sleep(0.4)
    after = screenshot(out_dir / "02_after.png")
    return {
        "ok": True,
        "action": "select_chat_row",
        "index": index,
        "click": {
            "window_relative_x": x,
            "window_relative_y": rel_y,
            "screen_x": screen_x,
            "screen_y": screen_y,
        },
        "output_dir": str(out_dir),
        "before": before,
        "after": after,
    }


def search_chat(
    query: str,
    *,
    select_first: bool = False,
    output_dir: str | Path | None = None,
    search_x_ratio: float = 0.18,
    search_y_ratio: float = 0.085,
    first_result_x_ratio: float = 0.18,
    first_result_y_ratio: float = 0.18,
) -> dict[str, Any]:
    query = str(query or "").strip()
    if not query:
        raise RuntimeError("Search query must not be empty.")
    out_dir = Path(output_dir).resolve() if output_dir else _runtime_dir("search_chat")
    out_dir.mkdir(parents=True, exist_ok=True)
    before = screenshot(out_dir / "01_before.png")
    activate_window()
    info = best_window()
    search_click = _click_relative(info, x_ratio=search_x_ratio, y_ratio=search_y_ratio)
    time.sleep(0.1)
    pyautogui.hotkey("ctrl", "a")
    pyautogui.press("backspace")
    pyperclip.copy(query)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.5)
    after_search = screenshot(out_dir / "02_after_search.png")
    selected: dict[str, Any] | None = None
    if select_first:
        info = best_window()
        result_click = _click_relative(info, x_ratio=first_result_x_ratio, y_ratio=first_result_y_ratio)
        time.sleep(0.5)
        selected = {"click": result_click, "after_select": screenshot(out_dir / "03_after_select.png")}
    return {
        "ok": True,
        "action": "search_chat",
        "query_chars": len(query),
        "select_first": select_first,
        "output_dir": str(out_dir),
        "search_click": search_click,
        "before": before,
        "after_search": after_search,
        "selected": selected,
    }


def clear_search(*, output_dir: str | Path | None = None) -> dict[str, Any]:
    out_dir = Path(output_dir).resolve() if output_dir else _runtime_dir("clear_search")
    out_dir.mkdir(parents=True, exist_ok=True)
    before = screenshot(out_dir / "01_before.png")
    activate_window()
    pyautogui.hotkey("ctrl", "a")
    pyautogui.press("backspace")
    time.sleep(0.1)
    pyautogui.press("escape")
    time.sleep(0.2)
    after = screenshot(out_dir / "02_after.png")
    return {"ok": True, "action": "clear_search", "output_dir": str(out_dir), "before": before, "after": after}


def emoji_smoke(
    *,
    confirm_smoke: str,
    output_dir: str | Path | None = None,
    x_ratio: float = 0.377,
    y_ratio: float = 0.937,
) -> dict[str, Any]:
    if confirm_smoke != "PANEL":
        raise RuntimeError("Refusing emoji smoke: pass --confirm-smoke PANEL for explicit approval.")
    out_dir = Path(output_dir).resolve() if output_dir else _runtime_dir("emoji_smoke")
    out_dir.mkdir(parents=True, exist_ok=True)
    before = screenshot(out_dir / "01_before.png")
    info = best_window()
    click = _click_relative(info, x_ratio=x_ratio, y_ratio=y_ratio)
    time.sleep(0.4)
    opened = screenshot(out_dir / "02_opened.png")
    pyautogui.press("escape")
    time.sleep(0.2)
    closed = screenshot(out_dir / "03_closed.png")
    open_diff = _input_region_diff(before["path"], opened["path"], x_ratio=x_ratio, y_ratio=y_ratio)
    close_diff = _input_region_diff(opened["path"], closed["path"], x_ratio=x_ratio, y_ratio=y_ratio)
    verified = bool(open_diff["visible_change"] and close_diff["visible_change"])
    return {
        "ok": verified,
        "action": "emoji_smoke",
        "verified": verified,
        "reason": "panel_opened_then_closed" if verified else "no_visible_panel_change",
        "click": click,
        "output_dir": str(out_dir),
        "before": before,
        "opened": opened,
        "closed": closed,
        "open_diff": open_diff,
        "close_diff": close_diff,
    }


def file_picker_smoke(
    *,
    confirm_smoke: str,
    output_dir: str | Path | None = None,
    x_ratio: float = 0.458,
    y_ratio: float = 0.937,
) -> dict[str, Any]:
    if confirm_smoke != "PICKER":
        raise RuntimeError("Refusing file picker smoke: pass --confirm-smoke PICKER for explicit approval.")
    out_dir = Path(output_dir).resolve() if output_dir else _runtime_dir("file_picker_smoke")
    out_dir.mkdir(parents=True, exist_ok=True)
    before = screenshot(out_dir / "01_before.png")
    info = best_window()
    main_handle = info.handle
    click = _click_relative(info, x_ratio=x_ratio, y_ratio=y_ratio)
    time.sleep(0.8)
    dialogs_open = dialog_windows()
    pyautogui.press("escape")
    time.sleep(0.4)
    after_cancel = screenshot(out_dir / "02_after_cancel.png")
    dialogs_after = dialog_windows()
    def is_picker_like(item: dict[str, Any]) -> bool:
        title = str(item.get("title") or "")
        if "选择文件" in title or "打开" in title:
            return True
        return bool(item.get("handle") != main_handle and item.get("active") and item.get("width", 0) >= 500)

    had_picker = any(is_picker_like(item) for item in dialogs_open)
    still_picker = any(is_picker_like(item) for item in dialogs_after)
    verified = bool(had_picker and not still_picker)
    return {
        "ok": verified,
        "action": "file_picker_smoke",
        "verified": verified,
        "reason": "picker_opened_then_cancelled" if verified else "picker_open_or_cancel_not_verified",
        "click": click,
        "output_dir": str(out_dir),
        "before": before,
        "dialogs_open": dialogs_open,
        "after_cancel": after_cancel,
        "dialogs_after": dialogs_after,
    }


def paste_draft(text: str, *, activate: bool = True) -> dict[str, Any]:
    if activate:
        focus_input()
    pyperclip.copy(text)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.2)
    return {"ok": True, "action": "paste_draft", "chars": len(text), "sent": False}


def clear_input(*, activate: bool = True) -> dict[str, Any]:
    if activate:
        activate_window()
    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.05)
    pyautogui.press("backspace")
    time.sleep(0.1)
    return {"ok": True, "action": "clear_input"}


def send_current(*, confirm_send: str) -> dict[str, Any]:
    if confirm_send != "SEND":
        raise RuntimeError("Refusing to send: pass --confirm-send SEND for explicit approval.")
    activate_window()
    pyautogui.press("enter")
    time.sleep(0.2)
    return {"ok": True, "action": "send_current", "confirmation": "SEND"}


def prepare_message(
    text: str,
    *,
    confirm_prepare: str,
    output_dir: str | Path | None = None,
    x_ratio: float = 0.55,
    y_ratio: float = 0.90,
) -> dict[str, Any]:
    if confirm_prepare != "DRAFT":
        raise RuntimeError("Refusing message prepare: pass --confirm-prepare DRAFT for explicit approval.")
    text = str(text or "")
    if not text:
        raise RuntimeError("Message text must not be empty.")
    out_dir = Path(output_dir).resolve() if output_dir else _runtime_dir("prepare_message")
    out_dir.mkdir(parents=True, exist_ok=True)
    before = screenshot(out_dir / "01_before.png")
    focus = focus_input(x_ratio=x_ratio, y_ratio=y_ratio)
    clear_input(activate=False)
    paste = paste_draft(text, activate=False)
    after_prepare = screenshot(out_dir / "02_after_prepare.png")
    diff = _input_region_diff(before["path"], after_prepare["path"], x_ratio=x_ratio, y_ratio=y_ratio)
    verified = bool(diff["visible_change"])
    return {
        "ok": verified,
        "action": "prepare_message",
        "sent": False,
        "verified": verified,
        "reason": "draft_prepared" if verified else "draft_not_visible_in_expected_input_region",
        "text_chars": len(text),
        "output_dir": str(out_dir),
        "before": before,
        "focus": focus,
        "paste": paste,
        "after_prepare": after_prepare,
        "diff": diff,
    }


def send_text(
    text: str,
    *,
    confirm_send: str,
    output_dir: str | Path | None = None,
    x_ratio: float = 0.55,
    y_ratio: float = 0.90,
) -> dict[str, Any]:
    if confirm_send != "SEND":
        raise RuntimeError("Refusing text send: pass --confirm-send SEND for explicit approval.")
    prepared = prepare_message(
        text,
        confirm_prepare="DRAFT",
        output_dir=output_dir,
        x_ratio=x_ratio,
        y_ratio=y_ratio,
    )
    if not prepared.get("verified"):
        return prepared | {"ok": False, "action": "send_text", "sent": False, "reason": "prepare_failed_before_send"}
    pyautogui.press("enter")
    time.sleep(0.5)
    out_dir = Path(prepared["output_dir"])
    after_send = screenshot(out_dir / "03_after_send.png")
    clear_diff = _input_region_diff(prepared["after_prepare"]["path"], after_send["path"], x_ratio=x_ratio, y_ratio=y_ratio)
    return {
        "ok": True,
        "action": "send_text",
        "sent": True,
        "text_chars": len(text),
        "output_dir": str(out_dir),
        "prepared": prepared,
        "after_send": after_send,
        "post_send_diff": clear_diff,
    }


def draft_smoke(
    text: str,
    *,
    confirm_smoke: str,
    output_dir: str | Path | None = None,
    x_ratio: float = 0.55,
    y_ratio: float = 0.90,
) -> dict[str, Any]:
    if confirm_smoke != "DRAFT":
        raise RuntimeError("Refusing draft smoke: pass --confirm-smoke DRAFT for explicit approval.")
    if output_dir is None:
        output_dir = Path.cwd() / "_bridge" / "runtime" / "cli_anything_weixin" / f"draft_smoke_{int(time.time())}"
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    before = screenshot(out_dir / "01_before.png")
    focus = focus_input(x_ratio=x_ratio, y_ratio=y_ratio)
    paste = paste_draft(text, activate=False)
    after_paste = screenshot(out_dir / "02_after_paste.png")
    clear = clear_input(activate=False)
    after_clear = screenshot(out_dir / "03_after_clear.png")
    paste_diff = _input_region_diff(before["path"], after_paste["path"], x_ratio=x_ratio, y_ratio=y_ratio)
    clear_diff = _input_region_diff(after_paste["path"], after_clear["path"], x_ratio=x_ratio, y_ratio=y_ratio)
    verified = bool(paste_diff["visible_change"] and clear_diff["visible_change"])
    return {
        "ok": verified,
        "action": "draft_smoke",
        "sent": False,
        "verified": verified,
        "reason": "draft_visible_then_cleared" if verified else "no_visible_draft_change_in_expected_input_region",
        "text_chars": len(text),
        "output_dir": str(out_dir),
        "before": before,
        "focus": focus,
        "paste": paste,
        "after_paste": after_paste,
        "paste_diff": paste_diff,
        "clear": clear,
        "after_clear": after_clear,
        "clear_diff": clear_diff,
    }


def dump_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)
