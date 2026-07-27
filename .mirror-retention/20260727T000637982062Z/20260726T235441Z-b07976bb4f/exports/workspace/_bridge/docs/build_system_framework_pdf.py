from __future__ import annotations

import base64
import html
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import markdown
from pypdf import PdfReader, PdfWriter


ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "_bridge" / "docs"
SOURCE_MD = DOCS / "system_framework_overview.md"
BUILD_DIR = DOCS / "_build" / "system_framework_pdf"
ASSET_DIR = BUILD_DIR / "assets"
HTML_OUT = BUILD_DIR / "system_framework_overview_print.html"
PDF_OUT = DOCS / "system_framework_architecture_whitepaper.pdf"


MERMAID_BLOCK_RE = re.compile(r"```mermaid\s*\n(.*?)\n```", re.DOTALL)


def run(cmd: list[str], *, env: dict[str, str] | None = None) -> None:
    result = subprocess.run(cmd, cwd=ROOT, env=env, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(
            "Command failed:\n"
            + " ".join(cmd)
            + "\nSTDOUT:\n"
            + result.stdout
            + "\nSTDERR:\n"
            + result.stderr
        )


def find_chrome() -> str:
    candidates = [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    raise RuntimeError("Chrome/Edge executable not found for PDF rendering.")


def mmdc_command() -> list[str]:
    npx = Path(r"C:\Program Files\nodejs\npx.cmd")
    if npx.exists():
        return [str(npx), "mmdc"]
    return ["mmdc"]


def render_mermaid(md_text: str) -> str:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    chrome = find_chrome()
    env = os.environ.copy()
    env["PUPPETEER_EXECUTABLE_PATH"] = chrome

    def replace(match: re.Match[str]) -> str:
        index = len(list(ASSET_DIR.glob("diagram-*.svg"))) + 1
        source = match.group(1).strip() + "\n"
        mmd_path = ASSET_DIR / f"diagram-{index:02d}.mmd"
        svg_path = ASSET_DIR / f"diagram-{index:02d}.svg"
        png_path = ASSET_DIR / f"diagram-{index:02d}.png"
        mmd_path.write_text(source, encoding="utf-8")
        run(mmdc_command() + ["-i", str(mmd_path), "-o", str(svg_path), "--backgroundColor", "transparent"], env=env)
        run(
            mmdc_command()
            + [
                "-i",
                str(mmd_path),
                "-o",
                str(png_path),
                "--backgroundColor",
                "white",
                "--scale",
                "2",
            ],
            env=env,
        )
        return f"\n\n![图表 {index}]({svg_path.as_posix()})\n\n"

    for old in ASSET_DIR.glob("diagram-*.*"):
        old.unlink()
    return MERMAID_BLOCK_RE.sub(replace, md_text)


def build_html(md_text: str) -> str:
    body = markdown.markdown(
        md_text,
        extensions=["extra", "toc", "sane_lists", "smarty"],
        output_format="html5",
    )
    css = """
    @page { size: A4; margin: 15mm 14mm 17mm; }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: #202124;
      font-family: "Noto Sans SC", "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
      font-size: 10.8pt;
      line-height: 1.58;
      background: #fff;
    }
    h1 {
      min-height: 78vh;
      display: flex;
      align-items: center;
      border-bottom: 4px solid #2457a6;
      font-size: 30pt;
      line-height: 1.2;
      color: #17233c;
      page-break-after: always;
      margin: 0 0 20px;
    }
    h2 {
      font-size: 17pt;
      color: #17233c;
      border-top: 1px solid #d8dee8;
      padding-top: 13px;
      margin: 22px 0 9px;
      page-break-after: avoid;
    }
    h3 {
      font-size: 13pt;
      color: #26364f;
      margin: 16px 0 8px;
      page-break-after: avoid;
    }
    p { margin: 0 0 8px; }
    a { color: #2457a6; text-decoration: none; word-break: break-word; }
    code {
      font-family: Consolas, "Cascadia Mono", monospace;
      font-size: 9.2pt;
      background: #f4f6f8;
      padding: 1px 4px;
      border-radius: 4px;
    }
    pre {
      background: #f4f6f8;
      border: 1px solid #d8dee8;
      border-radius: 8px;
      padding: 9px 10px;
      white-space: pre-wrap;
      word-break: break-word;
      page-break-inside: avoid;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      margin: 9px 0 13px;
      page-break-inside: avoid;
    }
    th, td {
      border: 1px solid #d8dee8;
      padding: 6px 7px;
      vertical-align: top;
    }
    th {
      background: #f7f9fc;
      color: #26364f;
      text-align: left;
      font-weight: 700;
    }
    ul, ol { margin: 5px 0 11px 21px; padding: 0; }
    li { margin: 3px 0; }
    blockquote {
      border-left: 4px solid #2457a6;
      background: #e8f0fe;
      margin: 10px 0 14px;
      padding: 9px 12px;
    }
    img {
      display: block;
      max-width: 100%;
      max-height: 185mm;
      margin: 10px auto 16px;
      border: 1px solid #d8dee8;
      border-radius: 8px;
      padding: 8px;
      background: #fff;
      page-break-inside: avoid;
    }
    hr { border: 0; border-top: 1px solid #d8dee8; margin: 18px 0; }
    """
    return (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<title>Codex 本机工作框架总览</title>"
        f"<style>{css}</style></head><body>{body}</body></html>"
    )


def write_pdf(html_text: str) -> None:
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    HTML_OUT.write_text(html_text, encoding="utf-8")
    chrome = find_chrome()
    html_uri = HTML_OUT.resolve().as_uri()
    run(
        [
            chrome,
            "--headless",
            "--disable-gpu",
            "--no-first-run",
            "--disable-extensions",
            "--no-pdf-header-footer",
            f"--print-to-pdf={PDF_OUT}",
            html_uri,
        ]
    )
    wait_for_pdf_stable(PDF_OUT)
    normalize_pdf_metadata()


def wait_for_pdf_stable(path: Path, *, attempts: int = 20, delay: float = 0.25) -> None:
    last_size = -1
    stable_count = 0
    for _ in range(attempts):
        if path.exists():
            size = path.stat().st_size
            if size == last_size and size > 0:
                stable_count += 1
                if stable_count >= 2:
                    return
            else:
                stable_count = 0
                last_size = size
        time.sleep(delay)
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError(f"PDF was not produced or is empty: {path}")


def normalize_pdf_metadata() -> None:
    reader = PdfReader(str(PDF_OUT))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.add_metadata(
        {
            "/Title": "Codex Local Framework Overview",
            "/Author": "Codex",
            "/Subject": "Local Codex architecture, operating model, tool layer, and maintenance surface",
            "/Creator": "build_system_framework_pdf.py",
            "/Producer": "Chrome headless + pypdf",
        }
    )
    tmp_path = PDF_OUT.with_suffix(".tmp.pdf")
    with tmp_path.open("wb") as f:
        writer.write(f)
    tmp_path.replace(PDF_OUT)


def main() -> int:
    md_text = SOURCE_MD.read_text(encoding="utf-8")
    md_with_svg = render_mermaid(md_text)
    html_text = build_html(md_with_svg)
    write_pdf(html_text)
    print(
        {
            "ok": True,
            "source": str(SOURCE_MD),
            "html": str(HTML_OUT),
            "pdf": str(PDF_OUT),
            "diagram_count": len(list(ASSET_DIR.glob("diagram-*.svg"))),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
