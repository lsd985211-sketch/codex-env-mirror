"""Unit tests for path validation, CLI shape, and preview metadata."""

from __future__ import annotations

import json

from click.testing import CliRunner

from cli_anything.microsoft_office.core.paths import output_file
from cli_anything.microsoft_office.core.operations import normalize_operations
from cli_anything.microsoft_office.core.preview import recipes
from cli_anything.microsoft_office.microsoft_office_cli import cli


def test_help_lists_command_groups() -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "word" in result.output
    assert "excel" in result.output
    assert "powerpoint" in result.output


def test_system_status_can_be_invoked_in_json_mode(monkeypatch) -> None:
    monkeypatch.setattr(
        "cli_anything.microsoft_office.core.documents.system_status",
        lambda timeout=30.0: {"ok": True, "action": "system.status", "apps": []},
    )
    result = CliRunner().invoke(cli, ["--json", "system", "status"])
    assert result.exit_code == 0
    assert json.loads(result.output)["action"] == "system.status"


def test_dry_run_does_not_create_output(tmp_path) -> None:
    output = tmp_path / "draft.docx"
    result = CliRunner().invoke(cli, ["--json", "--dry-run", "word", "create", str(output)])
    assert result.exit_code == 0
    assert json.loads(result.output)["dry_run"] is True
    assert not output.exists()


def test_overwrite_is_required(tmp_path) -> None:
    output = tmp_path / "draft.docx"
    output.write_bytes(b"existing")
    result = CliRunner().invoke(cli, ["--json", "--dry-run", "word", "create", str(output)])
    assert result.exit_code != 0
    assert "overwrite" in result.output.lower()


def test_preview_recipes_are_machine_readable() -> None:
    payload = recipes()
    assert payload["ok"] is True
    assert payload["recipes"][0]["name"] == "office-pdf"


def test_output_extension_is_checked(tmp_path) -> None:
    try:
        output_file(tmp_path / "bad.txt", extension=".docx", overwrite=False, dry_run=True)
    except ValueError as exc:
        assert ".docx" in str(exc)
    else:
        raise AssertionError("expected extension validation failure")


def test_operation_schema_rejects_unknown_operation() -> None:
    try:
        normalize_operations("word", [{"op": "run_macro", "name": "unsafe"}])
    except ValueError as exc:
        assert "unsupported" in str(exc)
    else:
        raise AssertionError("expected unknown operation rejection")


def test_operation_schema_rejects_unknown_fields() -> None:
    try:
        normalize_operations("excel", [{"op": "set_cell", "sheet": "Sheet1", "cell": "A1", "value": 1, "com_method": "Invoke"}])
    except ValueError as exc:
        assert "unknown fields" in str(exc)
    else:
        raise AssertionError("expected unknown field rejection")


def test_edit_dry_run_validates_without_invoking_backend(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source.docx"
    source.write_bytes(b"placeholder")
    output = tmp_path / "edited.docx"
    monkeypatch.setattr(
        "cli_anything.microsoft_office.core.documents.invoke",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("backend should not run")),
    )
    result = CliRunner().invoke(
        cli,
        ["--json", "--dry-run", "word", "edit", str(source), str(output), "--operations-json", '[{"op":"append_text","text":"hello"}]'],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["operation_count"] == 1
    assert not output.exists()


def test_edit_requires_distinct_source_and_output(tmp_path) -> None:
    source = tmp_path / "source.docx"
    source.write_bytes(b"placeholder")
    result = CliRunner().invoke(
        cli,
        ["--json", "--dry-run", "--overwrite", "word", "edit", str(source), str(source), "--operations-json", '[{"op":"append_text","text":"hello"}]'],
    )
    assert result.exit_code != 0
    assert "differ" in result.output


def test_operations_command_is_machine_readable() -> None:
    result = CliRunner().invoke(cli, ["--json", "powerpoint", "operations"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "add_slide" in payload["operations"]
