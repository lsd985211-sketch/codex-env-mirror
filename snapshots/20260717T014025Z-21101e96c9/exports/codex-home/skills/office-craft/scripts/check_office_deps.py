"""Check bundled Python office-file dependencies."""

import importlib

MODULES = [
    "docx",
    "docxtpl",
    "mammoth",
    "openpyxl",
    "pandas",
    "xlsxwriter",
    "pypdf",
    "pdfplumber",
    "reportlab",
    "matplotlib",
    "markdownify",
    "bs4",
    "tabulate",
    "pptx",
]


def main() -> int:
    failed = False
    for module in MODULES:
        try:
            importlib.import_module(module)
            print(f"{module}=ok")
        except Exception as exc:  # pragma: no cover - diagnostic output
            failed = True
            print(f"{module}=missing:{type(exc).__name__}:{exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
