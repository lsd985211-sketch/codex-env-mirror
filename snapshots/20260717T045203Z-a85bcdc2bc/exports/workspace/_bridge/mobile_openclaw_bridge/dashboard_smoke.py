#!/usr/bin/env python3
"""Playwright smoke test for the read-only mobile bridge dashboard."""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parent
DEFAULT_SCREENSHOT = ROOT / "logs" / "mobile-dashboard-smoke.png"
DEFAULT_EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
DEFAULT_CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")


def browser_path(explicit: str = "") -> str:
    if explicit:
        path = Path(explicit)
        if not path.exists():
            raise FileNotFoundError(f"browser not found: {path}")
        return str(path)
    for path in (DEFAULT_EDGE, DEFAULT_CHROME):
        if path.exists():
            return str(path)
    raise FileNotFoundError("Neither Microsoft Edge nor Google Chrome was found.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test the mobile bridge dashboard with system Edge/Chrome.")
    parser.add_argument("--url", default="http://127.0.0.1:18808/", help="dashboard URL")
    parser.add_argument("--browser", default="", help="explicit browser executable path")
    parser.add_argument("--screenshot", type=Path, default=DEFAULT_SCREENSHOT, help="screenshot output path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    executable = browser_path(args.browser)
    args.screenshot.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=executable)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        console_errors: list[str] = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.goto(args.url, wait_until="networkidle")
        page.wait_for_selector("#chat .turn", timeout=8000)
        task_count = page.locator("#chat .task-chip").count()
        user_count = page.locator("#users .thread").count()
        users_text = page.locator("#users").inner_text()
        health = page.locator("#health").inner_text()
        live_text = page.locator("#live").inner_text()
        live_api = page.evaluate("async () => await (await fetch('/api/live', {cache: 'no-store'})).json()")
        refresh_profile_visible = page.locator("#refreshProfile").is_visible()
        refresh_profile_value = page.locator("#refreshProfile").input_value()
        refresh_config = page.evaluate("() => ({profile: localStorage.getItem('mobileDashboard.refreshProfile') || DASHBOARD_CONFIG.defaultRefreshProfile, profiles: DASHBOARD_CONFIG.refreshProfiles})")
        page.locator("#chat .task-chip").first.click()
        page.wait_for_function("() => document.querySelectorAll('#detail .event').length > 0", timeout=5000)
        event_count_before_refresh = page.locator("#detail .event").count()
        raw_event_count = page.locator("#detail details.raw-event").count()
        bubble_tool_count = page.locator("#chat .bubble-tool").count()
        copy_text_count = page.locator("#chat [data-copy-text]").count()
        copy_result_count = page.locator("#chat [data-copy-result]").count()
        selected_task = page.locator("#chat [data-toggle-flow]").first.get_attribute("data-toggle-flow")
        page.locator(f"#chat [data-toggle-flow='{selected_task}']").first.click()
        page.wait_for_selector("#chat .flow-panel", timeout=5000)
        flow_panel_text = page.locator(f"#chat [data-flow-panel='{selected_task}']").first.inner_text()
        scroll_before = page.locator("#chat").evaluate("(el) => { el.scrollTop = Math.floor(el.scrollHeight / 2); return el.scrollTop; }")
        composer_visible = page.locator("#composerText").is_visible()
        send_label = page.locator("#sendButton").inner_text()
        action_count = page.locator("#detail .detail-actions button").count()
        detail_before_refresh = page.locator("#detail").inner_text()
        page.wait_for_timeout(2600)
        scroll_after = page.locator("#chat").evaluate("(el) => el.scrollTop")
        event_count_after_refresh = page.locator("#detail .event").count()
        detail_after_refresh = page.locator("#detail").inner_text()
        detail_preview = page.locator("#detail").inner_text()[:300]
        page.screenshot(path=str(args.screenshot), full_page=False)
        browser.close()

    ok = (
        task_count > 0
        and user_count > 0
        and "unknown" not in users_text.lower()
        and "health ok" in health.lower()
        and "实时观察" in live_text
        and isinstance(live_api, dict)
        and live_api.get("mode") == "codex-app-server-live-watch"
        and event_count_before_refresh > 0
        and event_count_after_refresh > 0
        and raw_event_count == event_count_before_refresh
        and refresh_profile_visible
        and refresh_profile_value in {"resource", "balanced", "realtime"}
        and refresh_config.get("profiles", {}).get("resource", {}).get("refreshMs") == 5000
        and bubble_tool_count >= 2
        and copy_text_count >= 1
        and copy_result_count >= 1
        and "桥接诊断流程" not in flow_panel_text
        and "处理步骤" in detail_before_refresh
        and abs(scroll_after - scroll_before) < 120
        and composer_visible
        and "代该用户发送" in send_label
        and action_count >= 3
        and detail_before_refresh.strip()
        and detail_after_refresh.strip()
        and not console_errors
    )
    print(
        {
            "ok": ok,
            "url": args.url,
            "browser": executable,
            "task_count": task_count,
            "user_count": user_count,
            "users_preview": users_text[:300],
            "health": health,
            "live": live_text,
            "live_ok": live_api.get("ok"),
            "live_connected": live_api.get("connected"),
            "refresh_profile_visible": refresh_profile_visible,
            "refresh_profile_value": refresh_profile_value,
            "refresh_config": refresh_config,
            "event_count_before_refresh": event_count_before_refresh,
            "event_count_after_refresh": event_count_after_refresh,
            "raw_event_count": raw_event_count,
            "bubble_tool_count": bubble_tool_count,
            "copy_text_count": copy_text_count,
            "copy_result_count": copy_result_count,
            "scroll_before": scroll_before,
            "scroll_after": scroll_after,
            "composer_visible": composer_visible,
            "send_label": send_label,
            "action_count": action_count,
            "detail_preview": detail_preview,
            "console_errors": console_errors,
            "screenshot": str(args.screenshot.resolve()),
        }
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
