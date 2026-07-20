"""Real Office E2E tests.

These tests are opt-in because they start hidden Word, Excel, and PowerPoint
COM instances. Set ``CLI_ANYTHING_OFFICE_E2E=1`` to run them.
"""

from __future__ import annotations

import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest


def _resolve_cli(name: str) -> str:
    forced = os.environ.get("CLI_ANYTHING_FORCE_INSTALLED")
    if forced:
        from shutil import which

        command = which(name)
        if not command:
            raise RuntimeError(f"installed CLI not found: {name}")
        print(f"[_resolve_cli] Using installed command: {command}")
        return command
    from shutil import which

    command = which(name)
    if not command:
        raise RuntimeError(f"CLI not found: {name}")
    print(f"[_resolve_cli] Using installed command: {command}")
    return command


def _run(command: str, *args: str) -> dict:
    completed = subprocess.run([command, "--json", *args], capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr or completed.stdout
    return json.loads(completed.stdout)


def _office_process_counts() -> dict[str, int]:
    completed = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, check=False)
    text = completed.stdout.upper()
    return {name: text.count(f'"{name}.EXE"') for name in ("WINWORD", "EXCEL", "POWERPNT")}


pytestmark = pytest.mark.skipif(
    os.environ.get("CLI_ANYTHING_OFFICE_E2E") != "1",
    reason="set CLI_ANYTHING_OFFICE_E2E=1 to start real Office COM instances",
)


def test_word_excel_powerpoint_roundtrip(tmp_path: Path) -> None:
    cli = _resolve_cli("cli-anything-microsoft-office")
    process_baseline = _office_process_counts()
    word = tmp_path / "sample.docx"
    excel = tmp_path / "sample.xlsx"
    deck = tmp_path / "sample.pptx"
    edited_word = tmp_path / "edited.docx"
    edited_excel = tmp_path / "edited.xlsx"
    edited_deck = tmp_path / "edited.pptx"
    word_pdf = tmp_path / "sample-word.pdf"
    excel_pdf = tmp_path / "sample-excel.pdf"
    deck_pdf = tmp_path / "sample-deck.pdf"

    assert _run(cli, "word", "create", str(word), "--title", "Title", "--body", "Body")["ok"]
    assert _run(cli, "excel", "create", str(excel), "--data-json", '[["A",1],["B",2]]')["ok"]
    assert _run(cli, "powerpoint", "create", str(deck), "--title", "Deck")["ok"]
    assert _run(cli, "word", "edit", str(word), str(edited_word), "--operations-json", '[{"op":"replace_text","find":"Body","replace":"Updated"},{"op":"add_heading","text":"Section","level":2},{"op":"add_table","rows":[["K","V"],["A","1"]]}]')["operation_count"] == 3
    assert _run(cli, "excel", "edit", str(excel), str(edited_excel), "--operations-json", '[{"op":"set_cell","sheet":"Sheet1","cell":"B2","value":9},{"op":"set_formula","sheet":"Sheet1","range":"B3","formula":"=SUM(B1:B2)"}]')["operation_count"] == 2
    assert _run(cli, "powerpoint", "edit", str(deck), str(edited_deck), "--operations-json", '[{"op":"add_slide","layout":12},{"op":"add_textbox","index":2,"text":"Edited","left":20,"top":20,"width":300,"height":80,"name":"BodyBox"}]')["operation_count"] == 2

    word_inspect = _run(cli, "word", "inspect", str(edited_word))
    excel_inspect = _run(cli, "excel", "inspect", str(edited_excel))
    deck_inspect = _run(cli, "powerpoint", "inspect", str(edited_deck))
    assert any(item["text"] == "Updated" for item in word_inspect["paragraphs"])
    assert any(item["text"] == "Section" for item in word_inspect["paragraphs"])
    assert word_inspect["tables"][0]["rows"] == 2
    assert excel_inspect["worksheets"][0]["sample"][1][1] == 9
    assert any(shape["text"] == "Edited" for shape in deck_inspect["slides"][1]["shapes"])

    assert _run(cli, "word", "export-pdf", str(edited_word), str(word_pdf))["ok"]
    assert _run(cli, "excel", "export-pdf", str(edited_excel), str(excel_pdf))["ok"]
    assert _run(cli, "powerpoint", "export-pdf", str(edited_deck), str(deck_pdf))["ok"]

    for office_file, member in [(edited_word, "word/document.xml"), (edited_excel, "xl/workbook.xml"), (edited_deck, "ppt/presentation.xml")]:
        assert zipfile.is_zipfile(office_file)
        with zipfile.ZipFile(office_file) as archive:
            assert member in archive.namelist()
    for pdf in (word_pdf, excel_pdf, deck_pdf):
        assert pdf.read_bytes().startswith(b"%PDF-")
    assert _office_process_counts() == process_baseline


def test_installed_help() -> None:
    cli = _resolve_cli("cli-anything-microsoft-office")
    completed = subprocess.run([cli, "--help"], capture_output=True, text=True, check=False)
    assert completed.returncode == 0
    assert "Microsoft Word" in completed.stdout
