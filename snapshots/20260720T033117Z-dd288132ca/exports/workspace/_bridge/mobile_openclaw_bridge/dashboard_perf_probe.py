#!/usr/bin/env python3
"""Measure dashboard user switching and scroll responsiveness."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parent
DEFAULT_EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
DEFAULT_CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")


def browser_path(explicit: str = "") -> str:
    if explicit:
        return explicit
    for path in (DEFAULT_EDGE, DEFAULT_CHROME):
        if path.exists():
            return str(path)
    return ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe mobile dashboard UI responsiveness.")
    parser.add_argument("--url", default="http://127.0.0.1:18808/")
    parser.add_argument("--browser", default="")
    parser.add_argument("--iterations", type=int, default=8)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    executable = browser_path(args.browser)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=executable or None)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(args.url, wait_until="networkidle")
        page.wait_for_selector("#users .thread", timeout=8000)
        page.wait_for_selector("#chat .task-chip", timeout=8000)
        metrics = page.evaluate(
            """async ({iterations}) => {
              const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
              const threads = Array.from(document.querySelectorAll("#users .thread"));
              const chat = document.querySelector("#chat");
              const detail = document.querySelector("#detail");
              const samples = [];
              for (let i = 0; i < iterations; i += 1) {
                const target = threads[i % threads.length];
                const t0 = performance.now();
                target.click();
                await sleep(80);
                const t1 = performance.now();
                for (let y = 0; y < 2400; y += 300) {
                  chat.scrollTop = y;
                  chat.dispatchEvent(new Event("scroll"));
                  await sleep(8);
                }
                const t2 = performance.now();
                samples.push({
                  switch_ms: Math.round(t1 - t0),
                  scroll_ms: Math.round(t2 - t1),
                  chat_nodes: chat.querySelectorAll("*").length,
                  detail_nodes: detail.querySelectorAll("*").length,
                  detail_html_chars: detail.innerHTML.length,
                  raw_event_nodes: detail.querySelectorAll(".raw-event .pre").length,
                  raw_loaded_nodes: detail.querySelectorAll(".raw-event .pre[data-loaded='1']").length,
                  task_count: document.querySelectorAll("#chat .task-chip").length
                });
              }
              return samples;
            }""",
            {"iterations": args.iterations},
        )
        browser.close()
    averages = {
        key: round(sum(item[key] for item in metrics) / len(metrics), 1)
        for key in metrics[0]
        if isinstance(metrics[0].get(key), (int, float))
    }
    result = {"ok": True, "samples": metrics, "averages": averages}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
