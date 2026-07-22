#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import ctypes
import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from shared.windows_runtime_assets import gui_ocr_python_paths

SUPPORTED_PROTOCOL_VERSIONS = {"2025-06-18", "2025-03-26", "2024-11-05"}
DEFAULT_PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "gui-automation"
SERVER_VERSION = "0.1.0"
ROOT = Path(__file__).resolve().parent
TMP_ROOT = ROOT / "tmp" / "gui_mcp"
OCR_PYTHON, OCR_FALLBACK_PYTHON = gui_ocr_python_paths()
OCR_RUNNER = Path(os.environ.get("GUI_OCR_RUNNER", str(ROOT / "gui_ocr_paddle_runner.py")))
MAX_WINDOWS = 100
MAX_ELEMENTS = 120
MAX_TREE_DEPTH = 6
MAX_FAILURES_PER_ACTION = 2
MAX_FAILURES_PER_SESSION = 3
DEFAULT_WAIT_TIMEOUT = 15.0
DEFAULT_OCR_TIMEOUT = 45.0
DEFAULT_UIA_CACHE_TTL_MS = 1200
OCR_WORKER_ENABLED = os.environ.get("GUI_OCR_WORKER", "1") != "0"
FULL_WINDOW_OCR_WARN_PIXELS = int(os.environ.get("GUI_FULL_WINDOW_OCR_WARN_PIXELS", "1000000"))
RECOVERABLE_ACTION_ERRORS = (
    "bound window no longer exists",
    "bound window is gone",
    "window handle is invalid",
    "target window not found",
    "session has no bound window",
    "window not found",
    "invalid window handle",
    "cannot set foreground",
)

DEPENDENCY_ERRORS: list[str] = []

try:
    import win32con
    import win32gui
    import win32process
except Exception as exc:  # pragma: no cover - environment-specific
    DEPENDENCY_ERRORS.append(f"pywin32 import failed: {exc}")
    win32con = None
    win32gui = None
    win32process = None

try:
    from PIL import ImageGrab
except Exception as exc:  # pragma: no cover - environment-specific
    DEPENDENCY_ERRORS.append(f"Pillow ImageGrab import failed: {exc}")
    ImageGrab = None

try:
    from pywinauto import mouse
    from pywinauto.keyboard import send_keys
except Exception as exc:  # pragma: no cover - environment-specific
    DEPENDENCY_ERRORS.append(f"pywinauto import failed: {exc}")
    mouse = None
    send_keys = None

try:
    import uiautomation as auto
except Exception as exc:  # pragma: no cover - environment-specific
    DEPENDENCY_ERRORS.append(f"uiautomation import failed: {exc}")
    auto = None


@dataclass
class SessionState:
    session_id: str
    created_at: str
    updated_at: str
    status: str = "created"
    title_pattern: str = ""
    process_name: str = ""
    hwnd: int = 0
    pid: int = 0
    last_rect: tuple[int, int, int, int] | None = None
    last_screenshot_path: str = ""
    last_tree_summary: list[dict[str, Any]] | None = None
    last_elements: dict[str, dict[str, Any]] | None = None
    last_tree_at: float = 0.0
    last_action: dict[str, Any] | None = None
    last_error: str = ""
    action_fail_count: int = 0
    session_fail_count: int = 0
    recovery_fail_count: int = 0
    last_checkpoint: dict[str, Any] | None = None
    last_verification: dict[str, Any] | None = None
    ocr_backend_ready: bool = False

    def as_dict(self, detail: str = "compact") -> dict[str, Any]:
        data = {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
            "title_pattern": self.title_pattern,
            "process_name": self.process_name,
            "hwnd": self.hwnd,
            "pid": self.pid,
            "last_rect": list(self.last_rect) if self.last_rect else None,
            "last_screenshot_path": self.last_screenshot_path,
            "last_action": self.last_action,
            "last_error": self.last_error,
            "action_fail_count": self.action_fail_count,
            "session_fail_count": self.session_fail_count,
            "recovery_fail_count": self.recovery_fail_count,
            "last_checkpoint": self.last_checkpoint,
            "last_verification": self.last_verification,
            "ocr_backend_ready": self.ocr_backend_ready,
        }
        if detail == "full":
            data["last_tree_summary"] = self.last_tree_summary
            data["last_elements"] = self.last_elements
        else:
            tree = self.last_tree_summary or []
            elements = self.last_elements or {}
            data["last_tree_count"] = len(tree)
            data["last_element_count"] = len(elements)
            data["last_tree_preview"] = [
                item
                for item in tree
                if item.get("name") or item.get("automation_id") or item.get("control_type")
            ][:12]
        return data


SESSIONS: dict[str, SessionState] = {}
OCR_WORKERS: dict[str, subprocess.Popen[str]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(content: Any, is_error: bool = False) -> dict[str, Any]:
    payload = {
        "content": [{"type": "text", "text": json.dumps(content, ensure_ascii=False)}],
    }
    if is_error:
        payload["isError"] = True
    return payload


def _ok(**kwargs: Any) -> dict[str, Any]:
    return {"ok": True, **kwargs}


def _err(message: str, **kwargs: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, **kwargs}


def _require_runtime() -> None:
    if DEPENDENCY_ERRORS:
        raise RuntimeError("; ".join(DEPENDENCY_ERRORS))


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _sanitize_filename(text: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_.-]+", "-", text.strip())
    return cleaned.strip("-")[:80] or "capture"


def _session_dir(session_id: str) -> Path:
    target = TMP_ROOT / session_id
    target.mkdir(parents=True, exist_ok=True)
    return target


def _save_screenshot(session_id: str, image: Any, prefix: str = "capture") -> str:
    out_dir = _session_dir(session_id)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    path = out_dir / f"{_sanitize_filename(prefix)}-{stamp}.png"
    image.save(path)
    return str(path)


def _kernel32() -> Any:
    return ctypes.windll.kernel32


def _query_process_image_name(pid: int) -> str:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    buf_len = 32768
    buf = ctypes.create_unicode_buffer(buf_len)
    size = ctypes.c_ulong(buf_len)
    handle = _kernel32().OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        if not ok:
            return ""
        return buf.value
    finally:
        _kernel32().CloseHandle(handle)


def _process_name_from_pid(pid: int) -> str:
    image = _query_process_image_name(pid)
    return Path(image).name if image else ""


def _list_process_names() -> set[str]:
    if win32process is None:
        return set()
    names: set[str] = set()
    try:
        pids = win32process.EnumProcesses()
    except Exception:
        return set()
    for pid in pids:
        name = _process_name_from_pid(int(pid)).casefold().strip()
        if name:
            names.add(name)
    return names


def _is_window_candidate(hwnd: int) -> bool:
    try:
        return bool(win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd))
    except Exception:
        return False


def _window_rect(hwnd: int) -> tuple[int, int, int, int]:
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    return left, top, right, bottom


def _window_exists(hwnd: int) -> bool:
    try:
        return bool(hwnd and win32gui.IsWindow(hwnd))
    except Exception:
        return False


def _window_is_minimized(hwnd: int) -> bool:
    try:
        return bool(win32gui.IsIconic(hwnd))
    except Exception:
        return False


def _window_info(hwnd: int) -> dict[str, Any]:
    title = win32gui.GetWindowText(hwnd)
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    rect = _window_rect(hwnd)
    return {
        "hwnd": int(hwnd),
        "title": title,
        "pid": int(pid),
        "process_name": _process_name_from_pid(pid),
        "foreground": int(win32gui.GetForegroundWindow() or 0) == int(hwnd),
        "minimized": _window_is_minimized(hwnd),
        "rect": list(rect),
        "width": max(0, rect[2] - rect[0]),
        "height": max(0, rect[3] - rect[1]),
    }


def _enumerate_windows() -> list[dict[str, Any]]:
    _require_runtime()
    items: list[dict[str, Any]] = []

    def callback(hwnd: int, _: Any) -> bool:
        if _is_window_candidate(hwnd):
            items.append(_window_info(hwnd))
        return True

    win32gui.EnumWindows(callback, None)
    items.sort(key=lambda item: (not item["foreground"], item["title"].casefold()))
    return items[:MAX_WINDOWS]


def _match_window(
    title_pattern: str = "",
    process_name: str = "",
    hwnd: int | None = None,
) -> dict[str, Any] | None:
    windows = _enumerate_windows()
    if hwnd:
        for item in windows:
            if int(item["hwnd"]) == int(hwnd):
                return item

    title_re = re.compile(title_pattern, re.I) if title_pattern else None
    process_name = process_name.casefold().strip()
    if title_re is None and not process_name:
        return None
    for item in windows:
        title_ok = True if title_re is None else bool(title_re.search(item["title"]))
        proc_ok = True if not process_name else item["process_name"].casefold() == process_name
        if title_ok and proc_ok:
            return item
    return None


def _activate_window(hwnd: int) -> None:
    _require_runtime()
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    time.sleep(0.08)
    if int(win32gui.GetForegroundWindow() or 0) == int(hwnd):
        return
    last_error: Exception | None = None
    try:
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.08)
    except Exception as exc:
        last_error = exc
    if int(win32gui.GetForegroundWindow() or 0) == int(hwnd):
        return

    try:
        target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)
        foreground = win32gui.GetForegroundWindow()
        foreground_thread = win32process.GetWindowThreadProcessId(foreground)[0] if foreground else 0
        current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
        for thread_id in {int(target_thread), int(foreground_thread), int(current_thread)}:
            if thread_id:
                ctypes.windll.user32.AttachThreadInput(int(current_thread), thread_id, True)
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.08)
    except Exception as exc:
        last_error = exc
    finally:
        with contextlib.suppress(Exception):
            current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
            target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)
            foreground = win32gui.GetForegroundWindow()
            foreground_thread = win32process.GetWindowThreadProcessId(foreground)[0] if foreground else 0
            for thread_id in {int(target_thread), int(foreground_thread), int(current_thread)}:
                if thread_id:
                    ctypes.windll.user32.AttachThreadInput(int(current_thread), thread_id, False)
    if int(win32gui.GetForegroundWindow() or 0) == int(hwnd):
        return

    try:
        shell = ctypes.OleDLL("ole32")
        _ = shell
    except Exception:
        pass
    try:
        import win32com.client  # type: ignore

        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        win32com.client.Dispatch("WScript.Shell").AppActivate(int(pid))
        time.sleep(0.15)
    except Exception as exc:
        last_error = exc
    if int(win32gui.GetForegroundWindow() or 0) == int(hwnd):
        return

    try:
        left, top, right, bottom = _window_rect(hwnd)
        x = left + max(20, min(80, max(1, right - left - 20)))
        y = top + max(20, min(40, max(1, bottom - top - 20)))
        if mouse is not None:
            mouse.click(button="left", coords=(x, y))
            time.sleep(0.12)
    except Exception as exc:
        last_error = exc
    if int(win32gui.GetForegroundWindow() or 0) != int(hwnd):
        if last_error:
            raise last_error
        raise RuntimeError("cannot set foreground")


def _capture_bbox(bbox: tuple[int, int, int, int]) -> Any:
    if ImageGrab is None:
        raise RuntimeError("Pillow ImageGrab is unavailable")
    return ImageGrab.grab(bbox=bbox, all_screens=True)


def _capture_window(hwnd: int, session_id: str, prefix: str = "window") -> dict[str, Any]:
    rect = _window_rect(hwnd)
    image = _capture_bbox(rect)
    path = _save_screenshot(session_id, image, prefix=prefix)
    return {
        "path": path,
        "rect": list(rect),
        "width": max(0, rect[2] - rect[0]),
        "height": max(0, rect[3] - rect[1]),
    }


def _capture_region(
    state: SessionState,
    region: str,
    *,
    prefix: str = "region",
) -> dict[str, Any]:
    match = re.fullmatch(r"\s*<?\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*>?\s*", region)
    if not match:
        raise ValueError("region must be <x,y,width,height>")
    x, y, w, h = [int(match.group(i)) for i in range(1, 5)]
    if state.hwnd:
        left, top, _, _ = _window_rect(state.hwnd)
        bbox = (left + x, top + y, left + x + w, top + y + h)
    else:
        bbox = (x, y, x + w, y + h)
    image = _capture_bbox(bbox)
    path = _save_screenshot(state.session_id, image, prefix=prefix)
    return {"path": path, "rect": list(bbox), "width": w, "height": h, "region": region}


def _rect_center(rect: tuple[int, int, int, int] | list[int]) -> tuple[int, int]:
    left, top, right, bottom = [int(v) for v in rect]
    return left + (right - left) // 2, top + (bottom - top) // 2


def _uia_control_from_hwnd(hwnd: int) -> Any:
    if auto is None:
        raise RuntimeError("uiautomation is unavailable")
    return auto.ControlFromHandle(hwnd)


def _uia_children(control: Any) -> list[Any]:
    try:
        return list(control.GetChildren())
    except Exception:
        return []


def _uia_element_summary(control: Any, path: str) -> dict[str, Any]:
    rect = None
    try:
        bound = control.BoundingRectangle
        rect = [bound.left, bound.top, bound.right, bound.bottom]
    except Exception:
        rect = None
    name = ""
    auto_id = ""
    control_type = ""
    try:
        name = str(control.Name or "")
    except Exception:
        pass
    try:
        auto_id = str(control.AutomationId or "")
    except Exception:
        pass
    try:
        control_type = str(control.ControlTypeName or "")
    except Exception:
        pass
    return {
        "path": path,
        "name": name,
        "automation_id": auto_id,
        "control_type": control_type,
        "rect": rect,
    }


def _build_uia_tree(hwnd: int) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    root = _uia_control_from_hwnd(hwnd)
    elements: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}

    def walk(control: Any, depth: int, path: str) -> None:
        if len(elements) >= MAX_ELEMENTS or depth > MAX_TREE_DEPTH:
            return
        item = _uia_element_summary(control, path)
        element_id = f"e{len(elements) + 1}"
        item["element_id"] = element_id
        item["depth"] = depth
        elements.append(item)
        by_id[element_id] = item
        for idx, child in enumerate(_uia_children(control), start=1):
            if len(elements) >= MAX_ELEMENTS:
                return
            walk(child, depth + 1, f"{path}.{idx}")

    walk(root, 0, "0")
    return elements, by_id


def _ensure_uia_tree(state: SessionState, *, cache_ttl_ms: int = DEFAULT_UIA_CACHE_TTL_MS, force: bool = False) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    now = time.monotonic()
    ttl_sec = max(0, cache_ttl_ms) / 1000.0
    if (
        not force
        and state.last_tree_summary is not None
        and state.last_elements is not None
        and ttl_sec > 0
        and now - state.last_tree_at <= ttl_sec
    ):
        return state.last_tree_summary, state.last_elements
    elements, by_id = _build_uia_tree(state.hwnd)
    state.last_tree_summary = elements
    state.last_elements = by_id
    state.last_tree_at = now
    return elements, by_id


def _session(session_id: str) -> SessionState:
    if session_id not in SESSIONS:
        now = _now_iso()
        SESSIONS[session_id] = SessionState(session_id=session_id, created_at=now, updated_at=now)
    return SESSIONS[session_id]


def _new_session(title_pattern: str = "", process_name: str = "") -> SessionState:
    sid = uuid.uuid4().hex[:16]
    now = _now_iso()
    state = SessionState(
        session_id=sid,
        created_at=now,
        updated_at=now,
        title_pattern=title_pattern,
        process_name=process_name,
    )
    SESSIONS[sid] = state
    return state


def _mark_failure(state: SessionState, message: str) -> None:
    state.last_error = message
    state.action_fail_count += 1
    state.session_fail_count += 1
    state.updated_at = _now_iso()
    if state.session_fail_count >= MAX_FAILURES_PER_SESSION:
        state.status = "aborted"
    elif state.action_fail_count > MAX_FAILURES_PER_ACTION:
        state.status = "recovering"


def _mark_success(state: SessionState, status: str = "verifying") -> None:
    state.status = status
    state.action_fail_count = 0
    state.updated_at = _now_iso()


def _classify_failure(message: str) -> dict[str, Any]:
    text = str(message or "").casefold()
    rules: list[tuple[str, tuple[str, ...], str, str]] = [
        (
            "human-intervention",
            ("captcha", "verification code", "permission", "elevat", "uac", "login"),
            "pause_for_human",
            "Human confirmation, login, captcha, or elevated permission is required before continuing.",
        ),
        (
            "window-routing",
            ("window", "hwnd", "foreground", "targetable", "bound", "selector", "handle"),
            "recover_or_rebind",
            "Refresh the window list, rebind by title/process, or recover from the last checkpoint.",
        ),
        (
            "control-selection",
            ("control not found", "element", "automation", "bounding rect", "selector", "button", "menu"),
            "inspect_and_find_control",
            "Refresh the UIA tree and select by exact name, AutomationId, or scoped OCR text before retrying.",
        ),
        (
            "ocr-vision",
            ("ocr", "text not found", "recognition", "paddle", "image", "screenshot"),
            "refresh_screenshot_and_ocr",
            "Capture a fresh full-window screenshot and retry OCR, falling back to UIA or CPU OCR if needed.",
        ),
        (
            "output-verification",
            ("condition timed out", "file_exists", "text_present", "element_exists", "verified", "verification"),
            "verify_preconditions_or_result",
            "Check whether the action actually changed state and choose a stronger durable verification signal.",
        ),
        (
            "input-flow",
            ("invalid point", "send_keys", "hotkey", "type", "click", "drag", "scroll", "dropdown"),
            "repeat_with_fresh_focus",
            "Re-activate the target window and keep focus plus input inside one bounded action.",
        ),
        (
            "stability",
            ("timeout", "timed out", "crash", "aborted", "recovering", "stale", "disappeared"),
            "checkpoint_recover_or_abort",
            "Use the last checkpoint for one recovery attempt, then abort and preserve evidence if state is unchanged.",
        ),
    ]
    for category, needles, action, explanation in rules:
        if any(needle in text for needle in needles):
            return {
                "category": category,
                "recommended_action": action,
                "ledger_recommendation": "failed-or-avoid" if category != "output-verification" else "candidate-unverified",
                "explanation": explanation,
            }
    return {
        "category": "unknown",
        "recommended_action": "inspect_before_retry",
        "ledger_recommendation": "candidate-unverified",
        "explanation": "The failure is not specific enough; refresh evidence before retrying or recording a skill rule.",
    }


def _failure_suggestions(classification: dict[str, Any]) -> list[str]:
    category = str(classification.get("category") or "unknown")
    common = [
        "call gui_failure_report before retrying so the next attempt uses current evidence",
        "do not repeat the same click or hotkey without a changed screenshot, UIA tree, or verification signal",
    ]
    by_category: dict[str, list[str]] = {
        "human-intervention": [
            "pause and ask the user to handle captcha, login, UAC, or destructive confirmation",
            "after the user confirms, call gui_resume_session and recapture evidence",
        ],
        "window-routing": [
            "call gui_list_windows, then gui_rebind_session or gui_switch_window with the intended title/process",
            "use gui_recover_session only when the checkpoint still describes the intended app window",
        ],
        "control-selection": [
            "call gui_inspect_window with include_tree=true and choose the most specific UIA selector",
            "fall back to gui_find_text_ocr only after screenshot evidence shows the target text is visible",
        ],
        "ocr-vision": [
            "call gui_capture with include_ocr=true on a fresh full-window screenshot",
            "retry with the configured GPU OCR first and CPU fallback if the OCR worker is unavailable",
        ],
        "output-verification": [
            "verify the result through disk readback, text_present, element_exists, or file_exists",
            "keep the workflow in candidate-unverified until durable output evidence exists",
        ],
        "input-flow": [
            "combine window activation, control focus, and typing/hotkey in one bounded action",
            "prefer element_id or exact UIA focus before coordinate input",
        ],
        "stability": [
            "checkpoint, recover once, then abort after repeated unchanged failures",
            "preserve screenshots and UIA summaries before any process restart",
        ],
        "unknown": [
            "refresh screenshot and UIA evidence before choosing a retry path",
            "record as candidate-unverified rather than verified success",
        ],
    }
    return by_category.get(category, by_category["unknown"]) + common


def _bind_window(state: SessionState, info: dict[str, Any]) -> None:
    state.hwnd = int(info["hwnd"])
    state.pid = int(info["pid"])
    state.last_rect = tuple(int(v) for v in info["rect"])
    state.updated_at = _now_iso()
    state.last_checkpoint = {
        "hwnd": state.hwnd,
        "pid": state.pid,
        "rect": list(state.last_rect),
        "title": info["title"],
        "process_name": info["process_name"],
        "updated_at": state.updated_at,
    }


def _window_selector_from_state(state: SessionState) -> dict[str, str]:
    return {
        "title_pattern": state.title_pattern,
        "process_name": state.process_name,
    }


def _best_recovery_selector(state: SessionState) -> dict[str, str]:
    if state.last_checkpoint:
        return {
            "title_pattern": str(state.last_checkpoint.get("title_pattern") or state.title_pattern or ""),
            "process_name": str(state.last_checkpoint.get("process_name") or state.process_name or ""),
        }
    return _window_selector_from_state(state)


def _is_recoverable_action_error(exc: Exception) -> bool:
    message = str(exc).casefold()
    return any(fragment in message for fragment in RECOVERABLE_ACTION_ERRORS)


def _public_action_args(args: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in args.items() if not str(key).startswith("_")}


def _detail_level(args: dict[str, Any]) -> str:
    value = str(args.get("detail", "compact") or "compact").strip().casefold()
    return "full" if value == "full" else "compact"


def _compact_elements(elements: list[dict[str, Any]], limit: int = 20) -> dict[str, Any]:
    visible = [
        item
        for item in elements
        if item.get("name") or item.get("automation_id") or item.get("control_type")
    ]
    return {
        "count": len(elements),
        "shown": min(len(visible), limit),
        "truncated": len(visible) > limit,
        "items": visible[:limit],
    }


def _normalise_match_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").casefold())


def _text_matches(text: str, query: str, mode: str = "contains") -> bool:
    text_cf = str(text or "").casefold()
    query_cf = str(query or "").casefold().strip()
    if not query_cf:
        return False
    if mode == "exact":
        return text_cf == query_cf
    if mode == "normalized":
        return _normalise_match_text(query_cf) in _normalise_match_text(text_cf)
    return query_cf in text_cf


def _score_uia_candidate(item: dict[str, Any], query: str, mode: str = "auto") -> tuple[int, list[str]]:
    query_cf = query.casefold().strip()
    name = str(item.get("name") or "")
    automation_id = str(item.get("automation_id") or "")
    control_type = str(item.get("control_type") or "")
    score = 0
    reasons: list[str] = []
    if not query_cf:
        return 0, reasons
    if query_cf == automation_id.casefold():
        score += 120
        reasons.append("automation_id_exact")
    if query_cf == name.casefold():
        score += 100
        reasons.append("name_exact")
    if _normalise_match_text(query_cf) == _normalise_match_text(name):
        score += 85
        reasons.append("name_normalized_exact")
    if query_cf in name.casefold():
        score += 60
        reasons.append("name_contains")
    if _normalise_match_text(query_cf) in _normalise_match_text(name):
        score += 45
        reasons.append("name_normalized_contains")
    if query_cf in automation_id.casefold():
        score += 45
        reasons.append("automation_id_contains")
    if mode == "control_type" and query_cf == control_type.casefold():
        score += 70
        reasons.append("control_type_exact")
    if item.get("rect"):
        score += 5
        reasons.append("has_rect")
    depth = _coerce_int(item.get("depth", 0), 0)
    if depth <= 4:
        score += max(0, 4 - depth)
    return score, reasons


def _compact_ocr(ocr: dict[str, Any], limit: int = 20) -> dict[str, Any]:
    items = ocr.get("items") if isinstance(ocr, dict) else []
    if not isinstance(items, list):
        items = []
    return {
        "ready": bool(ocr.get("ready")) if isinstance(ocr, dict) else False,
        "backend": ocr.get("backend") if isinstance(ocr, dict) else "",
        "device": ocr.get("device") if isinstance(ocr, dict) else "",
        "item_count": len(items),
        "shown": min(len(items), limit),
        "truncated": len(items) > limit,
        "items": items[:limit],
        "fallback_from": ocr.get("fallback_from") if isinstance(ocr, dict) else None,
        "worker": bool(ocr.get("worker")) if isinstance(ocr, dict) else False,
        "elapsed_ms": ocr.get("elapsed_ms") if isinstance(ocr, dict) else None,
        "error": ocr.get("error") if isinstance(ocr, dict) else None,
    }


def _ocr_strategy(capture: dict[str, Any], *, region: str = "") -> dict[str, Any]:
    width = _coerce_int(capture.get("width", 0), 0)
    height = _coerce_int(capture.get("height", 0), 0)
    pixels = max(0, width * height)
    full_window = not bool(region)
    warnings: list[str] = []
    if full_window and pixels >= FULL_WINDOW_OCR_WARN_PIXELS:
        warnings.append("full_window_ocr_large_capture")
    return {
        "identification_order": ["uia", "dom_or_app_specific", "ocr_region", "ocr_full_window", "coordinates"],
        "capture_scope": "region" if region else "full_window",
        "capture_pixels": pixels,
        "worker_reuse_enabled": OCR_WORKER_ENABLED,
        "prefer_region_when_possible": True,
        "warnings": warnings,
    }


def _ocr_item_screen_rect(state: SessionState, item: dict[str, Any], capture_rect: list[int] | tuple[int, int, int, int] | None) -> list[int] | None:
    bbox = item.get("bbox")
    if not bbox or len(bbox) != 4:
        return None
    try:
        x1, y1, x2, y2 = [float(v) for v in bbox]
        if capture_rect and len(capture_rect) == 4:
            left, top = int(capture_rect[0]), int(capture_rect[1])
        elif state.last_rect:
            left, top = int(state.last_rect[0]), int(state.last_rect[1])
        else:
            left, top = 0, 0
        return [int(round(left + x1)), int(round(top + y1)), int(round(left + x2)), int(round(top + y2))]
    except Exception:
        return None


def _find_ocr_text_matches(
    state: SessionState,
    *,
    query: str,
    match_mode: str = "contains",
    limit: int = 10,
    lang: str = "ch",
    device: str = "",
    timeout_sec: float = DEFAULT_OCR_TIMEOUT,
    region: str = "",
) -> dict[str, Any]:
    if not state.hwnd:
        return _err("session has no bound window", session=state.as_dict())
    _refresh_bound_window(state)
    shot = _capture_region(state, region, prefix="ocr-text-region") if region else _capture_window(state.hwnd, state.session_id, prefix="ocr-text")
    state.last_screenshot_path = shot["path"]
    started_at = time.perf_counter()
    ocr = _run_ocr(str(shot["path"]), timeout_sec=timeout_sec, lang=lang, device=device)
    ocr["elapsed_ms"] = int(round((time.perf_counter() - started_at) * 1000))
    state.ocr_backend_ready = bool(ocr.get("ready"))
    if ocr.get("ready") is not True:
        return _err("ocr failed", session=state.as_dict(), capture=shot, ocr=_compact_ocr(ocr), strategy=_ocr_strategy(shot, region=region))
    matches: list[dict[str, Any]] = []
    for item in ocr.get("items") or []:
        text = str(item.get("text") or "")
        if not _text_matches(text, query, match_mode):
            continue
        rect = _ocr_item_screen_rect(state, item, shot.get("rect"))
        match = dict(item)
        match["rect"] = rect
        match["center"] = list(_rect_center(rect)) if rect else None
        match["score"] = int(round(float(item.get("confidence") or 0) * 100))
        match["match_reason"] = f"ocr_text_{match_mode}"
        matches.append(match)
    matches.sort(key=lambda item: (-_coerce_int(item.get("score", 0), 0), str(item.get("text") or "")))
    return _ok(session=state.as_dict(), capture=shot, ocr=_compact_ocr(ocr), strategy=_ocr_strategy(shot, region=region), query=query, region=region, matches=matches[: max(1, min(limit, 30))])


def _failure_report(state: SessionState, detail: str = "compact") -> dict[str, Any]:
    tree = state.last_tree_summary or []
    elements = state.last_elements or {}
    classification = _classify_failure(state.last_error)
    actionable = [
        item
        for item in tree
        if item.get("name") or item.get("automation_id") or item.get("control_type")
    ][:30]
    return {
        "session": state.as_dict(detail=detail),
        "last_error": state.last_error,
        "status": state.status,
        "last_screenshot_path": state.last_screenshot_path,
        "last_checkpoint": state.last_checkpoint,
        "last_verification": state.last_verification,
        "last_action": state.last_action,
        "failure_category": classification.get("category"),
        "failure_classification": classification,
        "ledger_recommendation": classification.get("ledger_recommendation"),
        "candidate_counts": {
            "uia_tree": len(tree),
            "uia_elements": len(elements),
        },
        "top_uia_candidates": actionable,
        "suggested_next_steps": _failure_suggestions(classification),
    }


def _extract_json_from_stdout(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"raw": parsed}
    except Exception:
        decoder = json.JSONDecoder()
        for match in re.finditer(r"\{", text):
            try:
                parsed, _ = decoder.raw_decode(text[match.start() :])
                return parsed if isinstance(parsed, dict) else {"raw": parsed}
            except Exception:
                continue
    return {"ready": False, "backend": "paddleocr-subprocess", "error": "OCR runner returned non-JSON stdout", "stdout_tail": text[-500:]}


def _ocr_runner_status(timeout_sec: float = 10.0, python_path: Path | None = None) -> dict[str, Any]:
    python_path = python_path or OCR_PYTHON
    if not python_path.exists():
        return {"ready": False, "backend": "paddleocr-subprocess", "error": f"OCR Python not found: {python_path}"}
    if not OCR_RUNNER.exists():
        return {"ready": False, "backend": "paddleocr-subprocess", "error": f"OCR runner not found: {OCR_RUNNER}"}
    try:
        proc = subprocess.run(
            [str(python_path), str(OCR_RUNNER), "--status"],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            timeout=max(1.0, timeout_sec),
        )
    except subprocess.TimeoutExpired:
        return {"ready": False, "backend": "paddleocr-subprocess", "error": f"OCR status timed out after {timeout_sec}s"}
    except Exception as exc:
        return {"ready": False, "backend": "paddleocr-subprocess", "error": str(exc), "exception_type": type(exc).__name__}
    payload = _extract_json_from_stdout(proc.stdout)
    if proc.returncode != 0 and payload.get("ready") is not True:
        payload.setdefault("ready", False)
        payload.setdefault("backend", "paddleocr-subprocess")
        payload.setdefault("error", f"OCR status failed with exit code {proc.returncode}")
    if proc.stderr and payload.get("ready") is not True:
        payload["stderr_tail"] = proc.stderr[-800:]
    return payload


def _ocr_worker_key(python_path: Path) -> str:
    return str(python_path.resolve())


def _readline_with_timeout(proc: subprocess.Popen[str], timeout_sec: float) -> str:
    result: dict[str, str] = {"line": ""}

    def target() -> None:
        if proc.stdout is not None:
            result["line"] = proc.stdout.readline()

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(max(1.0, timeout_sec))
    if thread.is_alive():
        raise TimeoutError(f"OCR worker timed out after {timeout_sec}s")
    return result["line"]


def _stop_ocr_worker(key: str) -> None:
    proc = OCR_WORKERS.pop(key, None)
    if proc is None:
        return
    try:
        if proc.poll() is None and proc.stdin is not None:
            proc.stdin.write(json.dumps({"cmd": "exit"}) + "\n")
            proc.stdin.flush()
            try:
                proc.wait(timeout=2)
            except Exception:
                proc.kill()
    except Exception:
        with contextlib.suppress(Exception):
            proc.kill()


def _ocr_worker_request(
    python_path: Path,
    payload: dict[str, Any],
    *,
    timeout_sec: float,
    device: str = "",
) -> dict[str, Any]:
    key = _ocr_worker_key(python_path)
    proc = OCR_WORKERS.get(key)
    if proc is None or proc.poll() is not None or proc.stdin is None or proc.stdout is None:
        env = os.environ.copy()
        env["GUI_OCR_DEVICE"] = device or ""
        proc = subprocess.Popen(
            [str(python_path), str(OCR_RUNNER), "--serve"],
            cwd=str(ROOT),
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        OCR_WORKERS[key] = proc
    try:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        proc.stdin.flush()
        line = _readline_with_timeout(proc, timeout_sec)
        parsed = _extract_json_from_stdout(line)
        parsed["worker"] = True
        return parsed
    except Exception as exc:
        _stop_ocr_worker(key)
        return {"ready": False, "backend": "paddleocr-subprocess", "worker": True, "error": str(exc), "exception_type": type(exc).__name__}


def _run_ocr_with_python(
    image_path: str,
    *,
    python_path: Path,
    timeout_sec: float = DEFAULT_OCR_TIMEOUT,
    lang: str = "ch",
    max_items: int = 40,
    device: str = "",
) -> dict[str, Any]:
    if not image_path:
        return {"ready": False, "backend": "paddleocr-subprocess", "error": "image_path is required"}
    if not Path(image_path).exists():
        return {"ready": False, "backend": "paddleocr-subprocess", "error": f"image not found: {image_path}"}
    status = _ocr_runner_status(timeout_sec=min(10.0, max(1.0, timeout_sec)), python_path=python_path)
    if status.get("ready") is not True:
        return status
    if OCR_WORKER_ENABLED:
        worker_payload = {
            "cmd": "recognize",
            "image": image_path,
            "lang": lang or "ch",
            "max_items": max(1, max_items),
            "device": device or "",
        }
        worker_result = _ocr_worker_request(python_path, worker_payload, timeout_sec=max(5.0, timeout_sec), device=device)
        if worker_result.get("ready") is True:
            return worker_result
    try:
        env = os.environ.copy()
        env["GUI_OCR_DEVICE"] = device or ""
        proc = subprocess.run(
            [
                str(python_path),
                str(OCR_RUNNER),
                "--image",
                image_path,
                "--lang",
                lang or "ch",
                "--max-items",
                str(max(1, max_items)),
                "--device",
                device or "",
            ],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            timeout=max(5.0, timeout_sec),
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {"ready": False, "backend": "paddleocr-subprocess", "error": f"OCR timed out after {timeout_sec}s", "image_path": image_path}
    except Exception as exc:
        return {"ready": False, "backend": "paddleocr-subprocess", "error": str(exc), "exception_type": type(exc).__name__, "image_path": image_path}
    payload = _extract_json_from_stdout(proc.stdout)
    if proc.returncode != 0 and payload.get("ready") is not True:
        payload.setdefault("ready", False)
        payload.setdefault("backend", "paddleocr-subprocess")
        payload.setdefault("error", f"OCR failed with exit code {proc.returncode}")
    if proc.stderr and payload.get("ready") is not True:
        payload["stderr_tail"] = proc.stderr[-800:]
    return payload


def _run_ocr(
    image_path: str,
    *,
    timeout_sec: float = DEFAULT_OCR_TIMEOUT,
    lang: str = "ch",
    max_items: int = 40,
    device: str = "",
) -> dict[str, Any]:
    primary = _run_ocr_with_python(
        image_path,
        python_path=OCR_PYTHON,
        timeout_sec=timeout_sec,
        lang=lang,
        max_items=max_items,
        device=device,
    )
    if primary.get("ready") is True or not device or OCR_FALLBACK_PYTHON == OCR_PYTHON:
        return primary
    fallback = _run_ocr_with_python(
        image_path,
        python_path=OCR_FALLBACK_PYTHON,
        timeout_sec=timeout_sec,
        lang=lang,
        max_items=max_items,
        device="",
    )
    fallback["fallback_from"] = {
        "device": device,
        "python": str(OCR_PYTHON),
        "error": primary.get("error"),
        "exception_type": primary.get("exception_type"),
    }
    return fallback


def _pause_for_human(state: SessionState, message: str) -> None:
    state.status = "paused_for_human"
    state.last_error = message
    state.updated_at = _now_iso()


def _session_guard(state: SessionState) -> None:
    if state.status == "aborted":
        raise RuntimeError("session is aborted")
    if not state.hwnd:
        raise RuntimeError("session has no bound window")
    if not _window_exists(state.hwnd):
        state.status = "recovering"
        raise RuntimeError("bound window no longer exists")


def _refresh_bound_window(state: SessionState) -> dict[str, Any]:
    _session_guard(state)
    info = _match_window(
        title_pattern=state.title_pattern,
        process_name=state.process_name,
        hwnd=state.hwnd,
    )
    if info is None:
        state.status = "recovering"
        raise RuntimeError("bound window is no longer targetable")
    _bind_window(state, info)
    return info


def _capture_evidence(
    state: SessionState,
    *,
    prefix: str,
    include_tree: bool = False,
    include_screenshot: bool = True,
    detail: str = "compact",
    cache_ttl_ms: int = DEFAULT_UIA_CACHE_TTL_MS,
) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    if include_tree:
        elements, by_id = _ensure_uia_tree(state, cache_ttl_ms=cache_ttl_ms)
        if detail == "full":
            evidence["elements"] = elements
        else:
            evidence["elements_summary"] = _compact_elements(elements)
    if include_screenshot:
        shot = _capture_window(state.hwnd, state.session_id, prefix=prefix)
        state.last_screenshot_path = shot["path"]
        evidence["screenshot"] = shot
    evidence["window"] = _window_info(state.hwnd)
    evidence["session"] = state.as_dict(detail=detail)
    return evidence


def _evaluate_condition(state: SessionState, condition: str, value: str) -> bool:
    if condition == "window_exists":
        return _match_window(title_pattern=value, process_name="") is not None
    if condition == "window_gone":
        return _match_window(title_pattern=value, process_name="") is None
    if condition == "file_exists":
        return Path(value).exists()
    if condition == "process_exists":
        target = value.casefold()
        return target in _list_process_names()
    if condition == "text_present":
        if not state.hwnd:
            return False
        elements, by_id = _ensure_uia_tree(state, cache_ttl_ms=0, force=True)
        needle = value.casefold()
        return any(needle in str(item.get("name") or "").casefold() for item in elements)
    if condition == "element_exists":
        if not state.hwnd:
            return False
        return bool(_find_elements(state, query=value, limit=1, cache_ttl_ms=0))
    raise ValueError(f"unsupported condition: {condition}")


def _verify_state(
    state: SessionState,
    *,
    condition: str,
    value: str,
    timeout_sec: float,
    poll_interval: float,
) -> dict[str, Any]:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if _evaluate_condition(state, condition, value):
            verification = {
                "ok": True,
                "condition": condition,
                "value": value,
                "verified_at": _now_iso(),
            }
            state.last_verification = verification
            return verification
        time.sleep(poll_interval)
    verification = {
        "ok": False,
        "condition": condition,
        "value": value,
        "verified_at": _now_iso(),
    }
    state.last_verification = verification
    raise RuntimeError(f"condition timed out: {condition}")


def gui_list_windows(_: dict[str, Any]) -> dict[str, Any]:
    windows = _enumerate_windows()
    return _ok(count=len(windows), windows=windows)


def gui_open_app(args: dict[str, Any]) -> dict[str, Any]:
    app_path = str(args.get("app_path", "") or "").strip()
    launch_args = args.get("launch_args") or []
    wait_title_pattern = str(args.get("wait_title_pattern", "") or "").strip()
    wait_process_name = str(args.get("wait_process_name", "") or "").strip()
    timeout_sec = max(1.0, min(float(args.get("timeout_sec", DEFAULT_WAIT_TIMEOUT)), 120.0))
    if not app_path:
        return _err("app_path is required")
    try:
        subprocess.Popen([app_path, *[str(item) for item in launch_args]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        return _err("app launch failed", detail=str(exc), app_path=app_path)
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        match = _match_window(title_pattern=wait_title_pattern, process_name=wait_process_name)
        if match is not None:
            state = _new_session(title_pattern=wait_title_pattern, process_name=wait_process_name)
            _bind_window(state, match)
            _activate_window(state.hwnd)
            evidence = _capture_evidence(state, prefix="open-app", include_tree=True, include_screenshot=True)
            _mark_success(state, status="observing")
            return _ok(session=state.as_dict(), window=match, evidence=evidence)
        time.sleep(0.4)
    return _err(
        "app opened but target window did not appear in time",
        app_path=app_path,
        searched={"wait_title_pattern": wait_title_pattern, "wait_process_name": wait_process_name},
    )


def gui_ensure_window(args: dict[str, Any]) -> dict[str, Any]:
    title_pattern = str(args.get("title_pattern", "") or "").strip()
    process_name = str(args.get("process_name", "") or "").strip()
    app_path = str(args.get("app_path", "") or "").strip()
    launch_args = args.get("launch_args") or []
    timeout_sec = max(1.0, min(float(args.get("timeout_sec", DEFAULT_WAIT_TIMEOUT)), 60.0))
    session_id = str(args.get("session_id", "") or "").strip()
    state = _session(session_id) if session_id else _new_session(title_pattern=title_pattern, process_name=process_name)
    state.status = "prechecking"
    if title_pattern:
        state.title_pattern = title_pattern
    if process_name:
        state.process_name = process_name

    if not title_pattern and not process_name and not app_path and not state.hwnd:
        _mark_failure(state, "window selector is required")
        return _err(
            "window selector is required",
            session=state.as_dict(),
            searched={"title_pattern": title_pattern, "process_name": process_name, "app_path": app_path},
        )

    match = _match_window(title_pattern=title_pattern, process_name=process_name, hwnd=state.hwnd or None)
    if match is None and app_path:
        argv = [app_path, *[str(item) for item in launch_args]]
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            time.sleep(0.4)
            match = _match_window(title_pattern=title_pattern, process_name=process_name)
            if match:
                break

    if match is None:
        _mark_failure(state, "target window not found")
        return _err(
            "target window not found",
            session=state.as_dict(),
            searched={"title_pattern": title_pattern, "process_name": process_name, "app_path": app_path},
        )

    _bind_window(state, match)
    _activate_window(state.hwnd)
    state.status = "observing"
    return _ok(session=state.as_dict(), window=match)


def gui_inspect_window(args: dict[str, Any]) -> dict[str, Any]:
    session_id = str(args.get("session_id", "") or "").strip()
    include_screenshot = bool(args.get("include_screenshot", True))
    include_tree = bool(args.get("include_tree", True))
    include_ocr = bool(args.get("include_ocr", False))
    ocr_timeout_sec = float(args.get("ocr_timeout_sec", DEFAULT_OCR_TIMEOUT) or DEFAULT_OCR_TIMEOUT)
    ocr_lang = str(args.get("ocr_lang", "ch") or "ch")
    ocr_device = str(args.get("ocr_device", os.environ.get("GUI_OCR_DEVICE", "")) or "")
    region = str(args.get("region", "") or "").strip()
    cache_ttl_ms = _coerce_int(args.get("cache_ttl_ms", DEFAULT_UIA_CACHE_TTL_MS), DEFAULT_UIA_CACHE_TTL_MS)
    detail = _detail_level(args)
    title_pattern = str(args.get("title_pattern", "") or "").strip()
    process_name = str(args.get("process_name", "") or "").strip()
    hwnd = _coerce_int(args.get("hwnd", 0), 0)
    state = _session(session_id) if session_id else _new_session(title_pattern=title_pattern, process_name=process_name)
    state.status = "observing"

    info = _match_window(title_pattern=title_pattern, process_name=process_name, hwnd=hwnd or state.hwnd or None)
    if info is None:
        _mark_failure(state, "window not found for inspection")
        return _err("window not found for inspection", session=state.as_dict())

    _bind_window(state, info)
    _activate_window(state.hwnd)
    result: dict[str, Any] = _capture_evidence(
        state,
        prefix="inspect",
        include_tree=include_tree,
        include_screenshot=include_screenshot,
        detail=detail,
        cache_ttl_ms=cache_ttl_ms,
    )

    if include_ocr:
        shot = result.get("screenshot") or {}
        image_path = str(shot.get("path") or state.last_screenshot_path or "")
        result["ocr"] = _run_ocr(image_path, timeout_sec=ocr_timeout_sec, lang=ocr_lang, device=ocr_device)
        state.ocr_backend_ready = bool(result["ocr"].get("ready"))

    _mark_success(state, status="observing")
    result["session"] = state.as_dict(detail=detail)
    return _ok(**result)


def gui_switch_window(args: dict[str, Any]) -> dict[str, Any]:
    session_id = str(args.get("session_id", "") or "").strip()
    title_pattern = str(args.get("title_pattern", "") or "").strip()
    process_name = str(args.get("process_name", "") or "").strip()
    hwnd = _coerce_int(args.get("hwnd", 0), 0)
    detail = _detail_level(args)
    if not session_id:
        return _err("session_id is required")
    state = _session(session_id)
    info = _match_window(title_pattern=title_pattern or state.title_pattern, process_name=process_name or state.process_name, hwnd=hwnd or None)
    if info is None:
        _mark_failure(state, "target window not found for switch")
        return _err("target window not found for switch", session=state.as_dict(), searched={"title_pattern": title_pattern, "process_name": process_name, "hwnd": hwnd})
    _bind_window(state, info)
    _activate_window(state.hwnd)
    state.last_checkpoint = {
        "hwnd": state.hwnd,
        "pid": state.pid,
        "rect": list(state.last_rect) if state.last_rect else None,
        "title_pattern": state.title_pattern,
        "process_name": state.process_name,
        "updated_at": _now_iso(),
    }
    _mark_success(state, status="observing")
    evidence = _capture_evidence(state, prefix="switch-window", include_tree=True, include_screenshot=True, detail=detail)
    return _ok(session=state.as_dict(detail=detail), window=info, evidence=evidence)


def gui_focus_control(args: dict[str, Any]) -> dict[str, Any]:
    session_id = str(args.get("session_id", "") or "").strip()
    query = str(args.get("query", "") or "").strip()
    mode = str(args.get("mode", "auto") or "auto").strip()
    cache_ttl_ms = _coerce_int(args.get("cache_ttl_ms", DEFAULT_UIA_CACHE_TTL_MS), DEFAULT_UIA_CACHE_TTL_MS)
    detail = _detail_level(args)
    if not session_id:
        return _err("session_id is required")
    if not query:
        return _err("query is required")
    state = _session(session_id)
    try:
        _refresh_bound_window(state)
        matches = _find_elements(state, query=query, mode=mode, limit=1, cache_ttl_ms=cache_ttl_ms)
        if not matches:
            _mark_failure(state, f"control not found: {query}")
            return _err("control not found", session=state.as_dict(detail=detail), query=query)
        match = matches[0]
        rect = match.get("rect")
        if not rect:
            raise RuntimeError("matched control has no bounding rect")
        _activate_window(state.hwnd)
        _click_rect_center(rect)
        evidence = _capture_evidence(state, prefix="focus-control", include_tree=True, include_screenshot=True, detail=detail)
        state.last_checkpoint = {
            "hwnd": state.hwnd,
            "pid": state.pid,
            "rect": list(state.last_rect) if state.last_rect else None,
            "title_pattern": state.title_pattern,
            "process_name": state.process_name,
            "focused_query": query,
            "updated_at": _now_iso(),
        }
        _mark_success(state, status="verifying")
        return _ok(session=state.as_dict(detail=detail), matched=match, evidence=evidence)
    except Exception as exc:
        if any(keyword in str(exc).casefold() for keyword in ("captcha", "verification code", "permission", "elevat")):
            _pause_for_human(state, str(exc))
        else:
            _mark_failure(state, str(exc))
        return _err("focus control failed", session=state.as_dict(detail=detail), detail=str(exc))


def _find_elements(state: SessionState, query: str, mode: str = "auto", limit: int = 10, cache_ttl_ms: int = DEFAULT_UIA_CACHE_TTL_MS) -> list[dict[str, Any]]:
    _ensure_uia_tree(state, cache_ttl_ms=cache_ttl_ms)
    matches: list[dict[str, Any]] = []
    for item in state.last_tree_summary or []:
        score, reasons = _score_uia_candidate(item, query, mode=mode)
        if score > 0:
            match = dict(item)
            match["score"] = score
            match["match_reasons"] = reasons
            match["selector"] = {
                "automation_id": match.get("automation_id") or "",
                "name": match.get("name") or "",
                "control_type": match.get("control_type") or "",
                "path": match.get("path") or "",
            }
            matches.append(match)
    matches.sort(key=lambda item: (-int(item["score"]), int(item.get("depth", 0))))
    return matches[: max(1, min(limit, 20))]


def gui_find_element(args: dict[str, Any]) -> dict[str, Any]:
    session_id = str(args.get("session_id", "") or "").strip()
    query = str(args.get("query", "") or "").strip()
    mode = str(args.get("mode", "auto") or "auto").strip()
    limit = _coerce_int(args.get("limit", 10), 10)
    cache_ttl_ms = _coerce_int(args.get("cache_ttl_ms", DEFAULT_UIA_CACHE_TTL_MS), DEFAULT_UIA_CACHE_TTL_MS)
    if not session_id:
        return _err("session_id is required")
    if not query:
        return _err("query is required")
    state = _session(session_id)
    if not state.hwnd:
        return _err("session has no bound window", session=state.as_dict())
    state.status = "observing"
    matches = _find_elements(state, query=query, mode=mode, limit=limit, cache_ttl_ms=cache_ttl_ms)
    _mark_success(state, status="observing")
    return _ok(session=state.as_dict(), query=query, matches=matches)


def gui_find_text_ocr(args: dict[str, Any]) -> dict[str, Any]:
    session_id = str(args.get("session_id", "") or "").strip()
    query = str(args.get("query", "") or "").strip()
    match_mode = str(args.get("match_mode", "contains") or "contains").strip()
    limit = _coerce_int(args.get("limit", 10), 10)
    ocr_timeout_sec = float(args.get("ocr_timeout_sec", DEFAULT_OCR_TIMEOUT) or DEFAULT_OCR_TIMEOUT)
    ocr_lang = str(args.get("ocr_lang", "ch") or "ch")
    ocr_device = str(args.get("ocr_device", os.environ.get("GUI_OCR_DEVICE", "")) or "")
    region = str(args.get("region", "") or "").strip()
    if not session_id:
        return _err("session_id is required")
    if not query:
        return _err("query is required")
    state = _session(session_id)
    try:
        result = _find_ocr_text_matches(
            state,
            query=query,
            match_mode=match_mode,
            limit=limit,
            lang=ocr_lang,
            device=ocr_device,
            timeout_sec=ocr_timeout_sec,
            region=region,
        )
        if result.get("ok"):
            _mark_success(state, status="observing")
            result["session"] = state.as_dict()
        else:
            _mark_failure(state, str(result.get("error") or "ocr text lookup failed"))
        return result
    except Exception as exc:
        _mark_failure(state, str(exc))
        return _err("ocr text lookup failed", session=state.as_dict(), detail=str(exc))


def gui_click_text(args: dict[str, Any]) -> dict[str, Any]:
    session_id = str(args.get("session_id", "") or "").strip()
    query = str(args.get("query", "") or "").strip()
    match_mode = str(args.get("match_mode", "contains") or "contains").strip()
    button = str(args.get("button", "left") or "left").strip()
    double = bool(args.get("double", False))
    capture_after = bool(args.get("capture_after", True))
    prefer_uia = bool(args.get("prefer_uia", True))
    cache_ttl_ms = _coerce_int(args.get("cache_ttl_ms", DEFAULT_UIA_CACHE_TTL_MS), DEFAULT_UIA_CACHE_TTL_MS)
    ocr_timeout_sec = float(args.get("ocr_timeout_sec", DEFAULT_OCR_TIMEOUT) or DEFAULT_OCR_TIMEOUT)
    ocr_lang = str(args.get("ocr_lang", "ch") or "ch")
    ocr_device = str(args.get("ocr_device", os.environ.get("GUI_OCR_DEVICE", "")) or "")
    region = str(args.get("region", "") or "").strip()
    detail = _detail_level(args)
    if not session_id:
        return _err("session_id is required")
    if not query:
        return _err("query is required")
    state = _session(session_id)
    try:
        if prefer_uia and not region:
            _refresh_bound_window(state)
            uia_matches = _find_elements(state, query=query, mode="auto", limit=1, cache_ttl_ms=cache_ttl_ms)
            if uia_matches:
                match = uia_matches[0]
                rect = match.get("rect")
                if rect:
                    _activate_window(state.hwnd)
                    _click_rect_center(rect, button=button, double=double)
                    state.last_action = {
                        "action": "double_click_text_uia" if double else "click_text_uia",
                        "query": query,
                        "match": match,
                        "performed_at": _now_iso(),
                    }
                    evidence = _capture_evidence(state, prefix="click-text-uia", include_tree=True, include_screenshot=capture_after, detail=detail) if capture_after else {}
                    _mark_success(state, status="verifying")
                    return _ok(
                        session=state.as_dict(detail=detail),
                        clicked=match,
                        lookup={"strategy": "uia_first", "ocr_skipped": True, "matches": uia_matches},
                        evidence=evidence,
                    )
        result = _find_ocr_text_matches(
            state,
            query=query,
            match_mode=match_mode,
            limit=1,
            lang=ocr_lang,
            device=ocr_device,
            timeout_sec=ocr_timeout_sec,
            region=region,
        )
        if not result.get("ok"):
            _mark_failure(state, str(result.get("error") or "ocr text lookup failed"))
            return result
        matches = result.get("matches") or []
        if not matches:
            _mark_failure(state, f"ocr text not found: {query}")
            return _err("ocr text not found", session=state.as_dict(detail=detail), query=query, lookup=result)
        match = matches[0]
        rect = match.get("rect")
        if not rect:
            raise RuntimeError("matched OCR text has no bounding rect")
        _activate_window(state.hwnd)
        _click_rect_center(rect, button=button, double=double)
        state.last_action = {
            "action": "double_click_text" if double else "click_text",
            "query": query,
            "match": match,
            "performed_at": _now_iso(),
        }
        evidence = _capture_evidence(state, prefix="click-text", include_tree=False, include_screenshot=capture_after, detail=detail) if capture_after else {}
        _mark_success(state, status="verifying")
        return _ok(session=state.as_dict(detail=detail), clicked=match, lookup=result, evidence=evidence, strategy="ocr_fallback")
    except Exception as exc:
        _mark_failure(state, str(exc))
        evidence = _capture_evidence(state, prefix="failure-click-text", include_tree=True, include_screenshot=True, detail=detail) if state.hwnd and _window_exists(state.hwnd) else {"session": state.as_dict(detail=detail)}
        return _err("click text failed", session=state.as_dict(detail=detail), detail=str(exc), evidence=evidence, failure_report=_failure_report(state, detail=detail))


def _parse_point(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"\s*<?\s*(-?\d+)\s*,\s*(-?\d+)\s*>?\s*", str(value or ""))
    if not match:
        raise ValueError(f"invalid point: {value!r}")
    return int(match.group(1)), int(match.group(2))


def _window_relative_to_screen(hwnd: int, point: tuple[int, int], point_mode: str = "window") -> tuple[int, int]:
    if point_mode == "screen":
        return point
    left, top, _, _ = _window_rect(hwnd)
    return left + point[0], top + point[1]


def _wait_for_window(
    *,
    title_pattern: str = "",
    process_name: str = "",
    hwnd: int | None = None,
    timeout_sec: float = DEFAULT_WAIT_TIMEOUT,
    poll_interval: float = 0.2,
) -> dict[str, Any] | None:
    deadline = time.time() + max(0.1, timeout_sec)
    while time.time() < deadline:
        match = _match_window(title_pattern=title_pattern, process_name=process_name, hwnd=hwnd)
        if match:
            return match
        time.sleep(max(0.05, min(poll_interval, 2.0)))
    return None


def _click_rect_center(rect: list[int] | tuple[int, int, int, int], button: str = "left", double: bool = False) -> None:
    left, top, right, bottom = [int(v) for v in rect]
    if right <= left or bottom <= top:
        raise RuntimeError(f"element has invalid bounding rect: {list(rect)}")
    x, y = _rect_center(rect)
    if double:
        mouse.double_click(button=button, coords=(x, y))
    else:
        mouse.click(button=button, coords=(x, y))


def _hotkey_to_sendkeys(spec: str) -> str:
    special = {
        "enter": "{ENTER}",
        "tab": "{TAB}",
        "esc": "{ESC}",
        "escape": "{ESC}",
        "space": " ",
        "backspace": "{BACKSPACE}",
        "delete": "{DELETE}",
        "up": "{UP}",
        "down": "{DOWN}",
        "left": "{LEFT}",
        "right": "{RIGHT}",
        "home": "{HOME}",
        "end": "{END}",
        "pgup": "{PGUP}",
        "pgdn": "{PGDN}",
    }
    tokens = [tok.strip().casefold() for tok in re.split(r"[+\s]+", spec) if tok.strip()]
    if not tokens:
        raise ValueError("empty hotkey")
    prefix = ""
    while tokens and tokens[0] in {"ctrl", "control", "alt", "shift"}:
        token = tokens.pop(0)
        prefix += {"ctrl": "^", "control": "^", "alt": "%", "shift": "+"}[token]
    if not tokens:
        raise ValueError("hotkey has modifiers but no key")
    last = tokens.pop(0)
    base = special.get(last, last if len(last) == 1 else f"{{{last.upper()}}}")
    return prefix + base


def _session_element(state: SessionState, element_id: str) -> dict[str, Any]:
    if not state.last_elements or element_id not in state.last_elements:
        raise KeyError(f"unknown element_id: {element_id}")
    return state.last_elements[element_id]


def gui_act(args: dict[str, Any]) -> dict[str, Any]:
    session_id = str(args.get("session_id", "") or "").strip()
    action = str(args.get("action", "") or "").strip()
    verify_condition = str(args.get("verify_condition", "") or "").strip()
    verify_value = str(args.get("verify_value", "") or "").strip()
    verify_timeout_sec = max(0.5, min(float(args.get("verify_timeout_sec", 5.0)), 60.0))
    verify_poll_interval = max(0.2, min(float(args.get("verify_poll_interval", 0.5)), 5.0))
    capture_after = bool(args.get("capture_after", True))
    auto_recover = bool(args.get("auto_recover", False))
    recovered_once = bool(args.get("_recovered_once", False))
    detail = _detail_level(args)
    if not session_id:
        return _err("session_id is required")
    state = _session(session_id)

    state.status = "acting"

    try:
        _refresh_bound_window(state)
        _activate_window(state.hwnd)
        if action in {"click", "double_click", "right_click"}:
            element_id = str(args.get("element_id", "") or "").strip()
            if element_id:
                target = _session_element(state, element_id)
                rect = target.get("rect")
                if not rect:
                    raise RuntimeError(f"element {element_id} has no bounding rect")
                _click_rect_center(rect, button="right" if action == "right_click" else "left", double=action == "double_click")
            else:
                point = _parse_point(str(args.get("point", "")))
                mode = str(args.get("point_mode", "window") or "window")
                coords = _window_relative_to_screen(state.hwnd, point, point_mode=mode)
                if action == "double_click":
                    mouse.double_click(button="left", coords=coords)
                elif action == "right_click":
                    mouse.click(button="right", coords=coords)
                else:
                    mouse.click(button="left", coords=coords)
        elif action == "drag":
            start = _parse_point(str(args.get("start", "")))
            end = _parse_point(str(args.get("end", "")))
            mode = str(args.get("point_mode", "window") or "window")
            start_coords = _window_relative_to_screen(state.hwnd, start, point_mode=mode)
            end_coords = _window_relative_to_screen(state.hwnd, end, point_mode=mode)
            mouse.press(coords=start_coords)
            time.sleep(0.1)
            mouse.move(coords=end_coords)
            time.sleep(0.1)
            mouse.release(coords=end_coords)
        elif action == "hotkey":
            key = str(args.get("key", "") or "").strip()
            send_keys(_hotkey_to_sendkeys(key), pause=0.03)
        elif action == "type":
            text = str(args.get("text", "") or "")
            send_keys(text, with_spaces=True, pause=0.02, vk_packet=True)
        elif action == "scroll":
            direction = str(args.get("direction", "down") or "down").strip().lower()
            distance = max(1, _coerce_int(args.get("distance", 300), 300))
            left, top, right, bottom = _window_rect(state.hwnd)
            coords = (left + (right - left) // 2, top + (bottom - top) // 2)
            wheel_dist = distance if direction == "up" else -distance
            mouse.scroll(coords=coords, wheel_dist=wheel_dist)
        elif action == "select_dropdown":
            point = _parse_point(str(args.get("point", "")))
            option = str(args.get("option", "") or "").strip()
            coords = _window_relative_to_screen(state.hwnd, point, point_mode="window")
            mouse.click(button="left", coords=coords)
            time.sleep(0.2)
            send_keys(option, with_spaces=True, pause=0.02, vk_packet=True)
            time.sleep(0.2)
            send_keys("{ENTER}")
        elif action == "close_window":
            win32gui.PostMessage(state.hwnd, win32con.WM_CLOSE, 0, 0)
        else:
            raise ValueError(f"unsupported action: {action}")
        evidence = _capture_evidence(
            state,
            prefix=f"post-{action}",
            include_tree=bool(verify_condition in {"text_present", "element_exists"}),
            include_screenshot=capture_after,
            detail=detail,
        )
        verification = None
        if verify_condition:
            verification = _verify_state(
                state,
                condition=verify_condition,
                value=verify_value,
                timeout_sec=verify_timeout_sec,
                poll_interval=verify_poll_interval,
            )
    except Exception as exc:
        if any(keyword in str(exc).casefold() for keyword in ("captcha", "verification code", "permission", "elevat")):
            _pause_for_human(state, str(exc))
        else:
            _mark_failure(state, str(exc))
        if auto_recover and not recovered_once and _is_recoverable_action_error(exc):
            recovery = gui_recover_session({"session_id": session_id, "note": f"auto_recover before retrying {action}"})
            if recovery.get("ok"):
                retry_args = dict(args)
                retry_args["_recovered_once"] = True
                retried = gui_act(retry_args)
                retried["auto_recovery"] = {
                    "attempted": True,
                    "recovered": True,
                    "original_error": str(exc),
                    "recovery": recovery,
                }
                return retried
        try:
            evidence = _capture_evidence(state, prefix=f"failure-{action}", include_tree=True, include_screenshot=True, detail=detail) if state.hwnd and _window_exists(state.hwnd) else {"session": state.as_dict(detail=detail)}
        except Exception:
            evidence = {"session": state.as_dict(detail=detail)}
        state.last_action = {"action": action, "arguments": _public_action_args(args), "ok": False}
        return _err("action failed", session=state.as_dict(detail=detail), action=action, detail=str(exc), evidence=evidence, failure_report=_failure_report(state, detail=detail))

    state.last_action = {"action": action, "arguments": _public_action_args(args), "ok": True, "at": _now_iso()}
    _mark_success(state, status="verifying")
    result = {"session": state.as_dict(detail=detail), "action": state.last_action, "evidence": evidence}
    if verify_condition:
        result["verification"] = verification
    return _ok(**result)


def gui_attach_file_by_path(args: dict[str, Any]) -> dict[str, Any]:
    session_id = str(args.get("session_id", "") or "").strip()
    file_path = str(args.get("file_path", "") or "").strip().strip('"')
    file_button_point = str(args.get("file_button_point", "<513,758>") or "<513,758>")
    file_button_point_mode = str(args.get("file_button_point_mode", "window") or "window")
    dialog_title = str(args.get("dialog_title", "选择文件") or "选择文件")
    dialog_process_name = str(args.get("dialog_process_name", "") or "").strip()
    filename_point = str(args.get("filename_point", "<450,510>") or "<450,510>")
    open_button_point = str(args.get("open_button_point", "<735,553>") or "<735,553>")
    timeout_sec = max(1.0, min(float(args.get("timeout_sec", 10.0) or 10.0), 60.0))
    capture_before = bool(args.get("capture_before", False))
    capture_after = bool(args.get("capture_after", True))
    detail = _detail_level(args)

    started_at = time.perf_counter()
    timings: dict[str, float] = {}
    stage = "validate"
    if not session_id:
        return _err("session_id is required", stage=stage)
    if not file_path:
        return _err("file_path is required", stage=stage)
    path = Path(file_path).expanduser()
    if not path.is_absolute():
        return _err("file_path must be an absolute path", stage=stage, file_path=str(path))
    if not path.exists() or not path.is_file():
        return _err("file_path does not exist or is not a file", stage=stage, file_path=str(path))
    if mouse is None or send_keys is None:
        return _err("pywinauto mouse/keyboard backend is unavailable", stage=stage)

    state = _session(session_id)
    evidence: dict[str, Any] = {}

    def fail(message: str, detail_message: str = "") -> dict[str, Any]:
        _mark_failure(state, detail_message or message)
        failure_evidence: dict[str, Any] = {}
        with contextlib.suppress(Exception):
            if state.hwnd and _window_exists(state.hwnd):
                failure_evidence["parent"] = _capture_evidence(
                    state,
                    prefix=f"attach-file-failure-{stage}",
                    include_tree=False,
                    include_screenshot=True,
                    detail=detail,
                )
        return _err(
            message,
            stage=stage,
            detail=detail_message,
            session=state.as_dict(detail=detail),
            file_path=str(path),
            file_name=path.name,
            timings=timings,
            evidence={**evidence, **failure_evidence},
        )

    try:
        stage = "refresh_parent"
        parent = _refresh_bound_window(state)
        _activate_window(state.hwnd)
        if capture_before:
            evidence["before"] = _capture_evidence(
                state,
                prefix="attach-file-before",
                include_tree=False,
                include_screenshot=True,
                detail=detail,
            )
        timings[stage] = round(time.perf_counter() - started_at, 3)

        stage = "open_file_dialog"
        file_button = _parse_point(file_button_point)
        file_button_coords = _window_relative_to_screen(state.hwnd, file_button, point_mode=file_button_point_mode)
        mouse.click(button="left", coords=file_button_coords)
        dialog = _wait_for_window(
            title_pattern=dialog_title,
            process_name=dialog_process_name,
            timeout_sec=timeout_sec,
            poll_interval=0.15,
        )
        if not dialog:
            return fail("file dialog did not appear", f"searched title_pattern={dialog_title!r} process_name={dialog_process_name!r}")
        timings[stage] = round(time.perf_counter() - started_at, 3)

        stage = "fill_file_path"
        dialog_hwnd = int(dialog["hwnd"])
        _activate_window(dialog_hwnd)
        filename_coords = _window_relative_to_screen(dialog_hwnd, _parse_point(filename_point), point_mode="window")
        mouse.click(button="left", coords=filename_coords)
        time.sleep(0.05)
        send_keys("^a", pause=0.0)
        send_keys(str(path), with_spaces=True, pause=0.0, vk_packet=True)
        timings[stage] = round(time.perf_counter() - started_at, 3)

        stage = "confirm_dialog"
        open_coords = _window_relative_to_screen(dialog_hwnd, _parse_point(open_button_point), point_mode="window")
        mouse.click(button="left", coords=open_coords)
        deadline = time.time() + timeout_sec
        while time.time() < deadline and _window_exists(dialog_hwnd):
            time.sleep(0.15)
        if _window_exists(dialog_hwnd):
            evidence["dialog"] = _capture_window(dialog_hwnd, state.session_id, prefix="attach-file-dialog-still-open")
            return fail("file dialog did not close after opening file", "the path may be invalid for the dialog or the dialog is blocked")
        timings[stage] = round(time.perf_counter() - started_at, 3)

        stage = "return_parent"
        parent_after = _wait_for_window(hwnd=state.hwnd, timeout_sec=min(timeout_sec, 5.0), poll_interval=0.15)
        if not parent_after:
            parent_after = _match_window(title_pattern=state.title_pattern, process_name=state.process_name)
        if not parent_after:
            return fail("parent window not found after file dialog closed")
        _bind_window(state, parent_after)
        _activate_window(state.hwnd)
        if capture_after:
            evidence["after"] = _capture_evidence(
                state,
                prefix="attach-file-after",
                include_tree=False,
                include_screenshot=True,
                detail=detail,
            )
        timings[stage] = round(time.perf_counter() - started_at, 3)

        state.last_action = {
            "action": "attach_file_by_path",
            "file_name": path.name,
            "performed_at": _now_iso(),
            "dialog_title": dialog_title,
        }
        state.last_verification = {"expected_file_name": path.name, "capture_after": capture_after}
        _mark_success(state, status="verifying")
        return _ok(
            session=state.as_dict(detail=detail),
            parent_window=parent,
            dialog_window=dialog,
            file_path=str(path),
            file_name=path.name,
            evidence=evidence,
            timings=timings,
            note="File was selected through the native picker. Verify the target chat and file card before sending.",
        )
    except Exception as exc:
        return fail("attach file by path failed", str(exc))


def gui_wait_until(args: dict[str, Any]) -> dict[str, Any]:
    session_id = str(args.get("session_id", "") or "").strip()
    condition = str(args.get("condition", "") or "").strip()
    value = str(args.get("value", "") or "").strip()
    timeout_sec = max(0.5, min(float(args.get("timeout_sec", DEFAULT_WAIT_TIMEOUT)), 120.0))
    poll_interval = max(0.2, min(float(args.get("poll_interval", 0.5)), 5.0))
    state = _session(session_id) if session_id else _new_session()
    state.status = "verifying"
    deadline = time.time() + timeout_sec

    try:
        verification = _verify_state(
            state,
            condition=condition,
            value=value,
            timeout_sec=timeout_sec,
            poll_interval=poll_interval,
        )
        _mark_success(state, status="verifying")
        return _ok(session=state.as_dict(), condition=condition, value=value, satisfied=True, verification=verification)
    except Exception as exc:
        _mark_failure(state, str(exc))
        return _err("wait_until failed", session=state.as_dict(), condition=condition, detail=str(exc), failure_report=_failure_report(state))


def gui_capture(args: dict[str, Any]) -> dict[str, Any]:
    session_id = str(args.get("session_id", "") or "").strip()
    state = _session(session_id) if session_id else _new_session()
    hwnd = _coerce_int(args.get("hwnd", 0), 0) or state.hwnd
    point = str(args.get("region", "") or "").strip()
    include_ocr = bool(args.get("include_ocr", False))
    ocr_timeout_sec = float(args.get("ocr_timeout_sec", DEFAULT_OCR_TIMEOUT) or DEFAULT_OCR_TIMEOUT)
    ocr_lang = str(args.get("ocr_lang", "ch") or "ch")
    ocr_device = str(args.get("ocr_device", os.environ.get("GUI_OCR_DEVICE", "")) or "")
    detail = _detail_level(args)

    try:
        if point:
            match = re.fullmatch(r"\s*<?\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*>?\s*", point)
            if not match:
                raise ValueError("region must be <x,y,width,height>")
            x, y, w, h = [int(match.group(i)) for i in range(1, 5)]
            if hwnd:
                left, top, _, _ = _window_rect(hwnd)
                bbox = (left + x, top + y, left + x + w, top + y + h)
            else:
                bbox = (x, y, x + w, y + h)
            image = _capture_bbox(bbox)
            path = _save_screenshot(state.session_id, image, prefix="region")
            shot = {"path": path, "rect": list(bbox), "width": w, "height": h}
        else:
            if not hwnd:
                raise RuntimeError("no hwnd available for capture")
            shot = _capture_window(hwnd, state.session_id, prefix="window")
        state.last_screenshot_path = shot["path"]
        _mark_success(state, status="observing")
        result = {"session": state.as_dict(detail=detail), "capture": shot}
        if include_ocr:
            result["ocr"] = _run_ocr(str(shot.get("path") or ""), timeout_sec=ocr_timeout_sec, lang=ocr_lang, device=ocr_device)
            state.ocr_backend_ready = bool(result["ocr"].get("ready"))
        return _ok(**result)
    except Exception as exc:
        _mark_failure(state, str(exc))
        return _err("capture failed", session=state.as_dict(detail=detail), detail=str(exc))


def gui_run_flow_step(args: dict[str, Any]) -> dict[str, Any]:
    session_id = str(args.get("session_id", "") or "").strip()
    query = str(args.get("query", "") or "").strip()
    action = str(args.get("action", "click") or "click").strip()
    verify_condition = str(args.get("verify_condition", "") or "").strip()
    verify_value = str(args.get("verify_value", "") or "").strip()
    auto_recover = bool(args.get("auto_recover", False))
    detail = _detail_level(args)
    if not session_id:
        return _err("session_id is required")
    state = _session(session_id)
    if not state.hwnd:
        return _err("session has no bound window", session=state.as_dict())

    inspect = gui_inspect_window({"session_id": session_id, "include_tree": True, "include_screenshot": True, "include_ocr": False, "detail": detail})
    if not inspect.get("ok"):
        return inspect
    if query:
        found = gui_find_element({"session_id": session_id, "query": query, "limit": 1})
        if not found.get("ok") or not found.get("matches"):
            return _err("flow step could not find target element", session=state.as_dict(), query=query)
        match = found["matches"][0]
        act_args = {"session_id": session_id, "action": action or "click", "element_id": match["element_id"], "capture_after": True}
    else:
        act_args = {"session_id": session_id, "action": action, "capture_after": True}
    if auto_recover:
        act_args["auto_recover"] = True
    act_args["detail"] = detail
    if verify_condition:
        act_args["verify_condition"] = verify_condition
        act_args["verify_value"] = verify_value
    acted = gui_act(act_args)
    if not acted.get("ok"):
        return acted
    post = gui_inspect_window({"session_id": session_id, "include_tree": True, "include_screenshot": True, "include_ocr": False, "detail": detail})
    if not post.get("ok"):
        return post
    return _ok(
        session=_session(session_id).as_dict(detail=detail),
        inspect=inspect,
        acted=acted,
        post=post,
        note="gui_run_flow_step performs one observe-plan-act-verify unit only.",
    )


def gui_run_flow(args: dict[str, Any]) -> dict[str, Any]:
    session_id = str(args.get("session_id", "") or "").strip()
    steps = args.get("steps") or []
    detail = _detail_level(args)
    stop_after_failures = _coerce_int(args.get("stop_after_failures", MAX_FAILURES_PER_SESSION), MAX_FAILURES_PER_SESSION)
    if stop_after_failures <= 0:
        stop_after_failures = MAX_FAILURES_PER_SESSION
    if not session_id:
        return _err("session_id is required")
    if not isinstance(steps, list) or not steps:
        return _err("steps must be a non-empty array")
    state = _session(session_id)
    if not state.hwnd:
        return _err("session has no bound window", session=state.as_dict(detail=detail))

    records: list[dict[str, Any]] = []
    failures = 0
    for index, raw_step in enumerate(steps):
        if not isinstance(raw_step, dict):
            failures += 1
            records.append({"index": index, "ok": False, "error": "step must be an object"})
            if failures >= stop_after_failures:
                break
            continue
        kind = str(raw_step.get("kind", "flow_step") or "flow_step").strip()
        note = str(raw_step.get("note", f"flow step {index + 1}") or f"flow step {index + 1}")
        checkpoint = gui_checkpoint_session(
            {
                "session_id": session_id,
                "note": note,
                "include_tree": bool(raw_step.get("checkpoint_tree", False)),
                "include_screenshot": bool(raw_step.get("checkpoint_screenshot", True)),
                "detail": detail,
            }
        )
        record: dict[str, Any] = {"index": index, "kind": kind, "checkpoint": checkpoint}
        if not checkpoint.get("ok"):
            failures += 1
            record["ok"] = False
            record["error"] = "checkpoint failed"
            records.append(record)
            if failures >= stop_after_failures:
                break
            continue

        try:
            if kind == "wait_until":
                result = gui_wait_until(
                    {
                        "session_id": session_id,
                        "condition": raw_step.get("condition", ""),
                        "value": raw_step.get("value", ""),
                        "timeout_sec": raw_step.get("timeout_sec", DEFAULT_WAIT_TIMEOUT),
                        "poll_interval": raw_step.get("poll_interval", 0.5),
                    }
                )
            elif kind == "act":
                act_args = {**raw_step, "session_id": session_id, "detail": detail}
                act_args.pop("kind", None)
                result = gui_act(act_args)
            elif kind == "inspect":
                result = gui_inspect_window(
                    {
                        "session_id": session_id,
                        "include_tree": bool(raw_step.get("include_tree", True)),
                        "include_screenshot": bool(raw_step.get("include_screenshot", True)),
                        "include_ocr": bool(raw_step.get("include_ocr", False)),
                        "ocr_lang": raw_step.get("ocr_lang", "ch"),
                        "ocr_device": raw_step.get("ocr_device", ""),
                        "ocr_timeout_sec": raw_step.get("ocr_timeout_sec", DEFAULT_OCR_TIMEOUT),
                        "detail": detail,
                    }
                )
            elif kind == "find_text_ocr":
                step_args = {**raw_step, "session_id": session_id}
                step_args.pop("kind", None)
                result = gui_find_text_ocr(step_args)
            elif kind == "click_text":
                step_args = {**raw_step, "session_id": session_id, "detail": detail}
                step_args.pop("kind", None)
                result = gui_click_text(step_args)
            else:
                step_args = {**raw_step, "session_id": session_id, "detail": detail}
                step_args.pop("kind", None)
                result = gui_run_flow_step(step_args)
        except Exception as exc:
            result = _err(str(exc), exception_type=type(exc).__name__)

        record["result"] = result
        record["ok"] = bool(result.get("ok"))
        records.append(record)
        if result.get("ok"):
            failures = 0
        else:
            failures += 1
            if failures >= stop_after_failures:
                break

    completed = len(records) == len(steps) and all(item.get("ok") for item in records)
    if not completed:
        state.session_fail_count += 1
        state.last_error = "gui_run_flow stopped before completion"
        state.updated_at = _now_iso()
        return _err(
            "gui_run_flow stopped before completion",
            session=state.as_dict(detail=detail),
            completed=False,
            records=records,
            failure_count=failures,
        )
    _mark_success(state, status="observing")
    return _ok(session=state.as_dict(detail=detail), completed=True, records=records)


def gui_get_session(args: dict[str, Any]) -> dict[str, Any]:
    session_id = str(args.get("session_id", "") or "").strip()
    detail = _detail_level(args)
    if not session_id:
        return _err("session_id is required")
    state = _session(session_id)
    return _ok(session=state.as_dict(detail=detail))


def gui_ocr_status(args: dict[str, Any]) -> dict[str, Any]:
    timeout_sec = float(args.get("timeout_sec", 10.0) or 10.0)
    status = _ocr_runner_status(timeout_sec=timeout_sec)
    return _ok(ocr=status)


def gui_failure_report(args: dict[str, Any]) -> dict[str, Any]:
    session_id = str(args.get("session_id", "") or "").strip()
    detail = _detail_level(args)
    if not session_id:
        return _err("session_id is required")
    state = _session(session_id)
    return _ok(report=_failure_report(state, detail=detail))


def gui_verify_session(args: dict[str, Any]) -> dict[str, Any]:
    session_id = str(args.get("session_id", "") or "").strip()
    include_tree = bool(args.get("include_tree", False))
    include_screenshot = bool(args.get("include_screenshot", True))
    detail = _detail_level(args)
    if not session_id:
        return _err("session_id is required")
    state = _session(session_id)
    if not state.hwnd:
        return _err("session has no bound window", session=state.as_dict(detail=detail))
    try:
        info = _refresh_bound_window(state)
        evidence = _capture_evidence(
            state,
            prefix="verify-session",
            include_tree=include_tree,
            include_screenshot=include_screenshot,
            detail=detail,
        )
        verification = {
            "ok": True,
            "window_alive": True,
            "verified_at": _now_iso(),
        }
        state.last_verification = verification
        _mark_success(state, status="observing")
        return _ok(session=state.as_dict(detail=detail), window=info, verification=verification, evidence=evidence)
    except Exception as exc:
        _mark_failure(state, str(exc))
        return _err("session verification failed", session=state.as_dict(detail=detail), detail=str(exc))


def gui_checkpoint_session(args: dict[str, Any]) -> dict[str, Any]:
    session_id = str(args.get("session_id", "") or "").strip()
    note = str(args.get("note", "") or "").strip()
    include_tree = bool(args.get("include_tree", True))
    include_screenshot = bool(args.get("include_screenshot", True))
    detail = _detail_level(args)
    if not session_id:
        return _err("session_id is required")
    state = _session(session_id)
    if not state.hwnd:
        return _err("session has no bound window", session=state.as_dict(detail=detail))
    try:
        _refresh_bound_window(state)
        evidence = _capture_evidence(
            state,
            prefix="checkpoint",
            include_tree=include_tree,
            include_screenshot=include_screenshot,
            detail=detail,
        )
        checkpoint = {
            "hwnd": state.hwnd,
            "pid": state.pid,
            "rect": list(state.last_rect) if state.last_rect else None,
            "title_pattern": state.title_pattern,
            "process_name": state.process_name,
            "note": note,
            "updated_at": _now_iso(),
        }
        state.last_checkpoint = checkpoint
        _mark_success(state, status="observing")
        return _ok(session=state.as_dict(detail=detail), checkpoint=checkpoint, evidence=evidence)
    except Exception as exc:
        _mark_failure(state, str(exc))
        return _err("checkpoint failed", session=state.as_dict(detail=detail), detail=str(exc))


def gui_rebind_session(args: dict[str, Any]) -> dict[str, Any]:
    session_id = str(args.get("session_id", "") or "").strip()
    title_pattern = str(args.get("title_pattern", "") or "").strip()
    process_name = str(args.get("process_name", "") or "").strip()
    if not session_id:
        return _err("session_id is required")
    state = _session(session_id)
    if title_pattern:
        state.title_pattern = title_pattern
    if process_name:
        state.process_name = process_name
    info = _match_window(
        title_pattern=state.title_pattern,
        process_name=state.process_name,
        hwnd=state.hwnd or None,
    )
    if info is None:
        _mark_failure(state, "no matching window found for rebind")
        return _err(
            "no matching window found for rebind",
            session=state.as_dict(),
            searched={"title_pattern": state.title_pattern, "process_name": state.process_name},
        )
    _bind_window(state, info)
    _activate_window(state.hwnd)
    _mark_success(state, status="observing")
    return _ok(session=state.as_dict(), window=info)


def gui_resume_session(args: dict[str, Any]) -> dict[str, Any]:
    session_id = str(args.get("session_id", "") or "").strip()
    detail = _detail_level(args)
    if not session_id:
        return _err("session_id is required")
    state = _session(session_id)
    if not state.hwnd:
        return _err("session has no bound window", session=state.as_dict(detail=detail))
    if state.status not in {"paused_for_human", "recovering", "aborted"}:
        return _ok(session=state.as_dict(detail=detail), resumed=False, note="session did not require resume")
    if state.status == "aborted":
        state.session_fail_count = 0
        state.action_fail_count = 0
    try:
        info = _refresh_bound_window(state)
        state.last_error = ""
        evidence = _capture_evidence(state, prefix="resume-session", include_tree=True, include_screenshot=True, detail=detail)
        _mark_success(state, status="observing")
        return _ok(session=state.as_dict(detail=detail), resumed=True, window=info, evidence=evidence)
    except Exception as exc:
        _mark_failure(state, str(exc))
        return _err("session resume failed", session=state.as_dict(detail=detail), detail=str(exc))


def gui_recover_session(args: dict[str, Any]) -> dict[str, Any]:
    session_id = str(args.get("session_id", "") or "").strip()
    note = str(args.get("note", "") or "").strip()
    detail = _detail_level(args)
    if not session_id:
        return _err("session_id is required")
    state = _session(session_id)
    selector = _best_recovery_selector(state)
    if not selector["title_pattern"] and not selector["process_name"] and not state.hwnd:
        return _err("no recovery selector available", session=state.as_dict(detail=detail))
    state.status = "recovering"
    try:
        info = _match_window(
            title_pattern=selector["title_pattern"],
            process_name=selector["process_name"],
            hwnd=state.hwnd or None,
        )
        if info is None:
            return _err("recovery window not found", session=state.as_dict(detail=detail), searched=selector)
        _bind_window(state, info)
        _activate_window(state.hwnd)
        evidence = _capture_evidence(state, prefix="recover", include_tree=True, include_screenshot=True, detail=detail)
        recovery = {
            "ok": True,
            "note": note,
            "selector": selector,
            "recovered_at": _now_iso(),
        }
        state.last_checkpoint = {
            "hwnd": state.hwnd,
            "pid": state.pid,
            "rect": list(state.last_rect) if state.last_rect else None,
            "title_pattern": state.title_pattern,
            "process_name": state.process_name,
            "note": note,
            "updated_at": _now_iso(),
        }
        state.last_verification = recovery
        _mark_success(state, status="observing")
        return _ok(session=state.as_dict(detail=detail), window=info, recovery=recovery, evidence=evidence)
    except Exception as exc:
        _mark_failure(state, str(exc))
        return _err("session recovery failed", session=state.as_dict(detail=detail), detail=str(exc))


def gui_abort_session(args: dict[str, Any]) -> dict[str, Any]:
    session_id = str(args.get("session_id", "") or "").strip()
    if not session_id:
        return _err("session_id is required")
    state = _session(session_id)
    state.status = "aborted"
    state.updated_at = _now_iso()
    return _ok(session=state.as_dict())


TOOLS = [
    {
        "name": "gui_list_windows",
        "description": "List visible, targetable desktop windows. Read-only.",
        "annotations": {
            "title": "List Windows",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "gui_open_app",
        "description": "Launch a desktop app and wait for the target window to appear.",
        "annotations": {
            "title": "Open App",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "app_path": {"type": "string"},
                "launch_args": {"type": "array", "items": {"type": "string"}},
                "wait_title_pattern": {"type": "string"},
                "wait_process_name": {"type": "string"},
                "timeout_sec": {"type": "number", "default": 15},
            },
            "required": ["app_path"],
        },
    },
    {
        "name": "gui_ensure_window",
        "description": "Find a target window by title pattern or process name; optionally launch an app and wait for the window to appear.",
        "annotations": {
            "title": "Ensure Window",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "title_pattern": {"type": "string"},
                "process_name": {"type": "string"},
                "app_path": {"type": "string"},
                "launch_args": {"type": "array", "items": {"type": "string"}},
                "timeout_sec": {"type": "number", "default": 15},
            },
        },
    },
    {
        "name": "gui_inspect_window",
        "description": "Inspect a target window and return summary state, UIA elements, screenshot evidence, and optional OCR readiness notes. Defaults to compact output; pass detail='full' for full UIA tree.",
        "annotations": {
            "title": "Inspect Window",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "hwnd": {"type": "integer"},
                "title_pattern": {"type": "string"},
                "process_name": {"type": "string"},
                "include_screenshot": {"type": "boolean", "default": True},
                "include_tree": {"type": "boolean", "default": True},
                "include_ocr": {"type": "boolean", "default": False},
                "ocr_lang": {"type": "string", "default": "ch"},
                "ocr_device": {"type": "string", "description": "Optional PaddleOCR device, e.g. gpu; default uses the OCR venv default."},
                "ocr_timeout_sec": {"type": "number", "default": 45},
                "cache_ttl_ms": {"type": "integer", "default": 1200, "description": "Reuse cached UIA tree within this TTL when include_tree is true."},
                "detail": {"type": "string", "enum": ["compact", "full"], "default": "compact"},
            },
        },
    },
    {
        "name": "gui_find_element",
        "description": "Find likely UI elements in the current session by name, AutomationId, or text, returning scored selector candidates.",
        "annotations": {
            "title": "Find Element",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "query": {"type": "string"},
                "mode": {"type": "string", "default": "auto"},
                "limit": {"type": "integer", "default": 10},
                "cache_ttl_ms": {"type": "integer", "default": 1200},
            },
            "required": ["session_id", "query"],
        },
    },
    {
        "name": "gui_find_text_ocr",
        "description": "Find visible text by OCR in the bound window and return screen-space bounding boxes. Prefer passing region when the target area is known.",
        "annotations": {
            "title": "Find OCR Text",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "query": {"type": "string"},
                "match_mode": {"type": "string", "enum": ["contains", "exact", "normalized"], "default": "contains"},
                "limit": {"type": "integer", "default": 10},
                "region": {"type": "string", "description": "Optional <x,y,width,height> in window-relative coordinates. Limits OCR to a smaller region."},
                "ocr_lang": {"type": "string", "default": "ch"},
                "ocr_device": {"type": "string", "description": "Optional PaddleOCR device, e.g. gpu; default uses configured OCR device."},
                "ocr_timeout_sec": {"type": "number", "default": 45},
            },
            "required": ["session_id", "query"],
        },
    },
    {
        "name": "gui_click_text",
        "description": "Click visible text in the bound window. Uses UIA first by default, then OCR; pass region when OCR is needed for a known target area.",
        "annotations": {
            "title": "Click OCR Text",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "query": {"type": "string"},
                "match_mode": {"type": "string", "enum": ["contains", "exact", "normalized"], "default": "contains"},
                "button": {"type": "string", "default": "left"},
                "double": {"type": "boolean", "default": False},
                "capture_after": {"type": "boolean", "default": True},
                "prefer_uia": {"type": "boolean", "default": True, "description": "Try UI Automation matching before OCR when no region is supplied."},
                "cache_ttl_ms": {"type": "integer", "default": 1200},
                "region": {"type": "string", "description": "Optional <x,y,width,height> in window-relative coordinates. Limits OCR to a smaller region."},
                "ocr_lang": {"type": "string", "default": "ch"},
                "ocr_device": {"type": "string", "description": "Optional PaddleOCR device, e.g. gpu; default uses configured OCR device."},
                "ocr_timeout_sec": {"type": "number", "default": 45},
                "detail": {"type": "string", "enum": ["compact", "full"], "default": "compact"},
            },
            "required": ["session_id", "query"],
        },
    },
    {
        "name": "gui_act",
        "description": "Execute one GUI action inside a bound session. Supports click, double_click, right_click, drag, hotkey, type, scroll, select_dropdown, and close_window.",
        "annotations": {
            "title": "GUI Action",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "action": {"type": "string"},
                "element_id": {"type": "string"},
                "point": {"type": "string"},
                "point_mode": {"type": "string", "default": "window"},
                "start": {"type": "string"},
                "end": {"type": "string"},
                "key": {"type": "string"},
                "text": {"type": "string"},
                "direction": {"type": "string"},
                "distance": {"type": "integer"},
                "option": {"type": "string"},
                "auto_recover": {"type": "boolean", "default": False, "description": "If true, recover the bound window from the last checkpoint and retry this action once when the failure is window/session related."},
                "verify_condition": {"type": "string"},
                "verify_value": {"type": "string"},
                "verify_timeout_sec": {"type": "number", "default": 5},
                "verify_poll_interval": {"type": "number", "default": 0.5},
                "capture_after": {"type": "boolean", "default": True},
                "detail": {"type": "string", "enum": ["compact", "full"], "default": "compact"},
            },
            "required": ["session_id", "action"],
        },
    },
    {
        "name": "gui_wait_until",
        "description": "Wait until a desktop condition becomes true, such as window_exists, window_gone, element_exists, text_present, file_exists, or process_exists.",
        "annotations": {
            "title": "Wait Until",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "condition": {"type": "string"},
                "value": {"type": "string"},
                "timeout_sec": {"type": "number", "default": 15},
                "poll_interval": {"type": "number", "default": 0.5},
            },
            "required": ["condition", "value"],
        },
    },
    {
        "name": "gui_attach_file_by_path",
        "description": "Fast path for mature desktop workflows: open a native file picker from a bound parent window, enter an absolute file path, and return to the parent window for verification. Does not click Send.",
        "annotations": {
            "title": "Attach File By Path",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "file_path": {"type": "string", "description": "Absolute local path to the file to select in the native picker."},
                "file_button_point": {"type": "string", "default": "<513,758>", "description": "Window-relative point for the parent app's file/attach button."},
                "file_button_point_mode": {"type": "string", "enum": ["window", "screen"], "default": "window"},
                "dialog_title": {"type": "string", "default": "选择文件", "description": "Regex title pattern for the native file picker."},
                "dialog_process_name": {"type": "string", "description": "Optional process name filter for the dialog. Leave blank when the native picker process varies."},
                "filename_point": {"type": "string", "default": "<450,510>", "description": "Window-relative point inside the native file picker filename field."},
                "open_button_point": {"type": "string", "default": "<735,553>", "description": "Window-relative point on the native file picker Open button."},
                "timeout_sec": {"type": "number", "default": 10},
                "capture_before": {"type": "boolean", "default": False},
                "capture_after": {"type": "boolean", "default": True},
                "detail": {"type": "string", "enum": ["compact", "full"], "default": "compact"},
            },
            "required": ["session_id", "file_path"],
        },
    },
    {
        "name": "gui_capture",
        "description": "Capture a window or region screenshot and return the saved evidence path, with optional OCR readiness note.",
        "annotations": {
            "title": "Capture Screenshot",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "hwnd": {"type": "integer"},
                "region": {"type": "string", "description": "<x,y,width,height> in window-relative coords if hwnd/session present"},
                "include_ocr": {"type": "boolean", "default": False},
                "ocr_lang": {"type": "string", "default": "ch"},
                "ocr_device": {"type": "string", "description": "Optional PaddleOCR device, e.g. gpu; default uses the OCR venv default."},
                "ocr_timeout_sec": {"type": "number", "default": 45},
                "detail": {"type": "string", "enum": ["compact", "full"], "default": "compact"},
            },
        },
    },
    {
        "name": "gui_ocr_status",
        "description": "Check whether the isolated PaddleOCR subprocess backend is installed and callable. Does not run model recognition.",
        "annotations": {
            "title": "OCR Status",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "timeout_sec": {"type": "number", "default": 10},
            },
        },
    },
    {
        "name": "gui_run_flow_step",
        "description": "Run one observe-plan-act-verify step only. High-level helper for a single click-oriented step, not a full workflow engine.",
        "annotations": {
            "title": "Run Flow Step",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "query": {"type": "string"},
                "action": {"type": "string", "default": "click"},
                "auto_recover": {"type": "boolean", "default": False, "description": "If true, recover the session and retry the action once on window/session failures."},
                "verify_condition": {"type": "string"},
                "verify_value": {"type": "string"},
                "detail": {"type": "string", "enum": ["compact", "full"], "default": "compact"},
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "gui_run_flow",
        "description": "Run a bounded multi-step GUI flow with checkpoints before each step and stop-on-failure semantics.",
        "annotations": {
            "title": "Run GUI Flow",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": True,
                        "properties": {
                            "kind": {"type": "string", "description": "flow_step, act, wait_until, or inspect"},
                            "note": {"type": "string"},
                        },
                    },
                },
                "stop_after_failures": {"type": "integer", "default": 3},
                "detail": {"type": "string", "enum": ["compact", "full"], "default": "compact"},
            },
            "required": ["session_id", "steps"],
        },
    },
    {
        "name": "gui_get_session",
        "description": "Read the current GUI session state, including bound window, last evidence, failure counts, and latest error.",
        "annotations": {
            "title": "Get Session",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "detail": {"type": "string", "enum": ["compact", "full"], "default": "compact"},
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "gui_failure_report",
        "description": "Return a compact failure report for a GUI session, including last evidence, candidate controls, and suggested next steps.",
        "annotations": {
            "title": "Failure Report",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "detail": {"type": "string", "enum": ["compact", "full"], "default": "compact"},
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "gui_switch_window",
        "description": "Switch an existing session to another matching window or hwnd.",
        "annotations": {
            "title": "Switch Window",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "title_pattern": {"type": "string"},
                "process_name": {"type": "string"},
                "hwnd": {"type": "integer"},
                "detail": {"type": "string", "enum": ["compact", "full"], "default": "compact"},
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "gui_focus_control",
        "description": "Bring a control into focus by matching its name, AutomationId, or text, then capture evidence.",
        "annotations": {
            "title": "Focus Control",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "query": {"type": "string"},
                "mode": {"type": "string", "default": "auto"},
                "cache_ttl_ms": {"type": "integer", "default": 1200},
                "detail": {"type": "string", "enum": ["compact", "full"], "default": "compact"},
            },
            "required": ["session_id", "query"],
        },
    },
    {
        "name": "gui_verify_session",
        "description": "Verify that a session's bound window is still alive and targetable, with optional evidence capture.",
        "annotations": {
            "title": "Verify Session",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "include_tree": {"type": "boolean", "default": False},
                "include_screenshot": {"type": "boolean", "default": True},
                "detail": {"type": "string", "enum": ["compact", "full"], "default": "compact"},
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "gui_checkpoint_session",
        "description": "Persist a fresh session checkpoint with current bound window metadata and optional evidence.",
        "annotations": {
            "title": "Checkpoint Session",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "note": {"type": "string"},
                "include_tree": {"type": "boolean", "default": True},
                "include_screenshot": {"type": "boolean", "default": True},
                "detail": {"type": "string", "enum": ["compact", "full"], "default": "compact"},
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "gui_rebind_session",
        "description": "Rebind a session to a currently matching window using remembered or newly supplied selectors.",
        "annotations": {
            "title": "Rebind Session",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "title_pattern": {"type": "string"},
                "process_name": {"type": "string"},
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "gui_resume_session",
        "description": "Resume a session from paused_for_human, recovering, or aborted state after conditions are fixed.",
        "annotations": {
            "title": "Resume Session",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "detail": {"type": "string", "enum": ["compact", "full"], "default": "compact"},
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "gui_recover_session",
        "description": "Recover a session by reusing the last checkpoint or remembered selector and recapturing evidence.",
        "annotations": {
            "title": "Recover Session",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "note": {"type": "string"},
                "detail": {"type": "string", "enum": ["compact", "full"], "default": "compact"},
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "gui_abort_session",
        "description": "Abort a GUI session and preserve its last known evidence for later inspection.",
        "annotations": {
            "title": "Abort Session",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
        },
    },
]


TOOL_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "gui_list_windows": gui_list_windows,
    "gui_open_app": gui_open_app,
    "gui_ensure_window": gui_ensure_window,
    "gui_inspect_window": gui_inspect_window,
    "gui_find_element": gui_find_element,
    "gui_find_text_ocr": gui_find_text_ocr,
    "gui_click_text": gui_click_text,
    "gui_act": gui_act,
    "gui_attach_file_by_path": gui_attach_file_by_path,
    "gui_wait_until": gui_wait_until,
    "gui_capture": gui_capture,
    "gui_ocr_status": gui_ocr_status,
    "gui_run_flow_step": gui_run_flow_step,
    "gui_run_flow": gui_run_flow,
    "gui_get_session": gui_get_session,
    "gui_failure_report": gui_failure_report,
    "gui_switch_window": gui_switch_window,
    "gui_focus_control": gui_focus_control,
    "gui_verify_session": gui_verify_session,
    "gui_checkpoint_session": gui_checkpoint_session,
    "gui_rebind_session": gui_rebind_session,
    "gui_resume_session": gui_resume_session,
    "gui_recover_session": gui_recover_session,
    "gui_abort_session": gui_abort_session,
}


def handle(req: dict[str, Any]) -> dict[str, Any] | None:
    method = req.get("method")
    req_id = req.get("id")
    if method == "initialize":
        params = req.get("params") or {}
        version = params.get("protocolVersion")
        if version not in SUPPORTED_PROTOCOL_VERSIONS:
            version = DEFAULT_PROTOCOL_VERSION
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": (
                    "Use gui_ensure_window before GUI actions. "
                    "Use gui_inspect_window to refresh stale state. "
                    "This server exposes MCP JSON tools externally and uses a strict internal single-step GUI action protocol."
                ),
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = req.get("params") or {}
        name = str(params.get("name") or "")
        args = params.get("arguments") or {}
        try:
            handler = TOOL_HANDLERS.get(name)
            if handler is None:
                result = _err(f"unknown tool: {name}")
            else:
                result = handler(args)
            return {"jsonrpc": "2.0", "id": req_id, "result": _text(result, is_error=not result.get("ok", False))}
        except Exception as exc:
            result = _err(str(exc), exception_type=type(exc).__name__)
            return {"jsonrpc": "2.0", "id": req_id, "result": _text(result, is_error=True)}
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"unknown method: {method}"}}


def _run_stdio() -> None:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except json.JSONDecodeError as exc:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
            continue
        resp = handle(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


def _self_check() -> int:
    payload = {
        "ok": not DEPENDENCY_ERRORS,
        "server": SERVER_NAME,
        "version": SERVER_VERSION,
        "dependency_errors": DEPENDENCY_ERRORS,
        "window_count": len(_enumerate_windows()) if not DEPENDENCY_ERRORS else 0,
        "tmp_root": str(TMP_ROOT),
        "ocr_strategy": {
            "worker_reuse_enabled": OCR_WORKER_ENABLED,
            "full_window_warn_pixels": FULL_WINDOW_OCR_WARN_PIXELS,
            "click_text_prefers_uia": True,
            "prefer_region_ocr": True,
        },
        "tool_names": [item["name"] for item in TOOLS],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="GUI Automation MCP server")
    parser.add_argument("--self-check", action="store_true", help="Run environment self-check and exit")
    args = parser.parse_args()
    if args.self_check:
        return _self_check()
    _run_stdio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
