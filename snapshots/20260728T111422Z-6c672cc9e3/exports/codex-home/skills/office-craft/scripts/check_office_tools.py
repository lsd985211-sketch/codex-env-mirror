"""Probe optional external office tools with route-ready status labels."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TOOLS = [
    ("soffice", ["--version"], ["LibreOffice\\program\\soffice.exe"], "convert"),
    ("libreoffice", ["--version"], ["LibreOffice\\program\\soffice.exe"], "convert"),
    ("pandoc", ["--version"], ["Pandoc\\pandoc.exe"], "semantic-convert"),
    ("tesseract", ["--version"], ["Tesseract-OCR\\tesseract.exe"], "ocr"),
    ("pdftotext", ["-v"], ["Git\\mingw64\\bin\\pdftotext.exe"], "pdf-text"),
    ("qpdf", ["--version"], ["qpdf 12.3.2\\bin\\qpdf.exe"], "pdf-structure"),
    ("mutool", ["-v"], [], "pdf-render"),
    ("magick", ["-version"], ["ImageMagick-7.1.2-Q16-HDRI\\magick.exe"], "image-convert"),
]


def hidden_subprocess_kwargs() -> dict[str, object]:
    startupinfo = None
    creationflags = 0
    if sys.platform.startswith("win"):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        creationflags = subprocess.CREATE_NO_WINDOW
    return {"startupinfo": startupinfo, "creationflags": creationflags}


def candidate_paths(name: str, relative_paths: list[str]) -> list[str]:
    paths: list[str] = []
    found = shutil.which(name)
    if found:
        paths.append(found)

    roots = [
        Path("C:/Program Files"),
        Path("C:/Program Files (x86)"),
        Path.home() / "AppData/Local/Programs",
        Path.home() / "AppData/Local/Microsoft/WinGet/Packages",
    ]
    for root in roots:
        for rel in relative_paths:
            path = root / rel
            if path.exists():
                paths.append(str(path))

    winget_root = Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
    if winget_root.exists():
        exe = f"{name}.exe"
        for path in winget_root.rglob(exe):
            paths.append(str(path))

    deduped: list[str] = []
    seen: set[str] = set()
    for path in paths:
        key = str(Path(path)).lower()
        if key not in seen:
            seen.add(key)
            deduped.append(path)
    return deduped


def probe(name: str, args: list[str], relative_paths: list[str], role: str) -> str:
    paths = candidate_paths(name, relative_paths)
    if not paths:
        return f"{name}=missing role={role}"
    path = paths[0]
    try:
        proc = subprocess.run(
            [path, *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=8,
            check=False,
            **hidden_subprocess_kwargs(),
        )
        first = (proc.stdout or "").splitlines()[0:1]
        detail = first[0].strip() if first else "found"
        return f"{name}=ok role={role} path={path} :: {detail}"
    except subprocess.TimeoutExpired:
        if name in {"soffice", "libreoffice"}:
            smoke = libreoffice_smoke(path)
            if smoke:
                return f"{name}=ok role={role} path={path} :: {smoke}"
        return f"{name}=present-timeout role={role} path={path} :: command timed out"
    except Exception as exc:  # pragma: no cover - diagnostic output
        return f"{name}=error role={role} path={path} :: {type(exc).__name__}: {exc}"


def libreoffice_smoke(path: str) -> str | None:
    with tempfile.TemporaryDirectory(prefix="office-craft-lo-") as tmp:
        tmp_path = Path(tmp)
        source = tmp_path / "smoke.html"
        source.write_text("<html><body><p>office-craft smoke</p></body></html>", encoding="utf-8")
        proc = subprocess.run(
            [
                path,
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(tmp_path),
                str(source),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
            **hidden_subprocess_kwargs(),
        )
        output = tmp_path / "smoke.pdf"
        if proc.returncode == 0 and output.exists() and output.stat().st_size > 0:
            return "headless HTML-to-PDF smoke ok"
    return None


def main() -> int:
    for name, args, relative_paths, role in TOOLS:
        print(probe(name, args, relative_paths, role))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
