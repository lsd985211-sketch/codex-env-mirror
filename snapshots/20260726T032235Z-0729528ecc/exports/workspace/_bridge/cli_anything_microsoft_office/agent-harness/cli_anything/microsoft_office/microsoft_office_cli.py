"""Click entry point for the Microsoft Office CLI-Anything harness.

The CLI is intentionally a thin adapter: validation and Office ownership live
in ``core`` and the real Office COM work lives in ``utils.office_backend.ps1``.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any, Callable

import click

from cli_anything.microsoft_office.core import documents, preview
from cli_anything.microsoft_office.utils.backend import OfficeBackendError
from cli_anything.microsoft_office.utils.repl_skin import ReplSkin


def _load_operations(operations_json: str | None, operations_file: str | None) -> list[dict[str, Any]]:
    if bool(operations_json) == bool(operations_file):
        raise click.ClickException("provide exactly one of --operations-json or --operations-file")
    try:
        text = operations_json if operations_json is not None else Path(str(operations_file)).read_text(encoding="utf-8")
        payload = json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        raise click.ClickException(f"invalid operations JSON: {exc}") from exc
    if not isinstance(payload, list):
        raise click.ClickException("operations JSON must be an array")
    return payload


def _emit(payload: dict[str, Any], *, json_mode: bool) -> None:
    if json_mode:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if payload.get("ok"):
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        click.echo(f"error: {payload.get('error', 'operation failed')}", err=True)


def _run(operation: Callable[[], dict[str, Any]], *, json_mode: bool) -> None:
    try:
        _emit(operation(), json_mode=json_mode)
    except (ValueError, OfficeBackendError, OSError) as exc:
        payload = {"ok": False, "error": str(exc), "error_type": type(exc).__name__}
        _emit(payload, json_mode=json_mode)
        raise click.ClickException(str(exc)) from exc


@click.group(invoke_without_command=True)
@click.option("--json", "json_mode", is_flag=True, help="Emit machine-readable JSON.")
@click.option("--dry-run", is_flag=True, help="Validate a mutation without writing or invoking Office.")
@click.option("--overwrite", is_flag=True, help="Allow replacing an existing output file.")
@click.option("--timeout", type=click.FloatRange(min=1), default=120.0, show_default=True)
@click.pass_context
def cli(ctx: click.Context, json_mode: bool, dry_run: bool, overwrite: bool, timeout: float) -> None:
    """Operate installed Microsoft Word, Excel, and PowerPoint through COM."""
    ctx.ensure_object(dict)
    ctx.obj.update(json_mode=json_mode, dry_run=dry_run, overwrite=overwrite, timeout=timeout)
    if ctx.invoked_subcommand is None:
        _repl(ctx)


@cli.group("system")
@click.pass_context
def system_group(ctx: click.Context) -> None:
    """Inspect Office installation and COM registration."""


@system_group.command("status")
@click.pass_context
def system_status(ctx: click.Context) -> None:
    _run(lambda: documents.system_status(timeout=ctx.obj["timeout"]), json_mode=ctx.obj["json_mode"])


@cli.group("word")
def word_group() -> None:
    """Word document operations."""


@word_group.command("create")
@click.argument("output", type=click.Path())
@click.option("--title", default="")
@click.option("--body", default="")
@click.pass_context
def word_create(ctx: click.Context, output: str, title: str, body: str) -> None:
    _run(lambda: documents.create_word(output, title=title, body=body,
                                       overwrite=ctx.obj["overwrite"], dry_run=ctx.obj["dry_run"],
                                       timeout=ctx.obj["timeout"]), json_mode=ctx.obj["json_mode"])


@word_group.command("info")
@click.argument("input", type=click.Path(exists=True, dir_okay=False))
@click.pass_context
def word_info(ctx: click.Context, input: str) -> None:
    _run(lambda: documents.word_info(input, timeout=ctx.obj["timeout"]), json_mode=ctx.obj["json_mode"])


@word_group.command("inspect")
@click.argument("input", type=click.Path(exists=True, dir_okay=False))
@click.pass_context
def word_inspect(ctx: click.Context, input: str) -> None:
    _run(lambda: documents.inspect("word", input, timeout=ctx.obj["timeout"]), json_mode=ctx.obj["json_mode"])


@word_group.command("operations")
@click.pass_context
def word_operations(ctx: click.Context) -> None:
    _run(lambda: documents.operation_schema("word"), json_mode=ctx.obj["json_mode"])


@word_group.command("edit")
@click.argument("input", type=click.Path(exists=True, dir_okay=False))
@click.argument("output", type=click.Path())
@click.option("--operations-json", default=None)
@click.option("--operations-file", type=click.Path(exists=True, dir_okay=False), default=None)
@click.pass_context
def word_edit(ctx: click.Context, input: str, output: str, operations_json: str | None, operations_file: str | None) -> None:
    operations = _load_operations(operations_json, operations_file)
    _run(lambda: documents.edit("word", input, output, operations, overwrite=ctx.obj["overwrite"],
                                dry_run=ctx.obj["dry_run"], timeout=ctx.obj["timeout"]),
         json_mode=ctx.obj["json_mode"])


@word_group.command("export-pdf")
@click.argument("input", type=click.Path(exists=True, dir_okay=False))
@click.argument("output", type=click.Path())
@click.pass_context
def word_export_pdf(ctx: click.Context, input: str, output: str) -> None:
    _run(lambda: documents.export_pdf("word", input, output, overwrite=ctx.obj["overwrite"],
                                      dry_run=ctx.obj["dry_run"], timeout=ctx.obj["timeout"]),
         json_mode=ctx.obj["json_mode"])


@cli.group("excel")
def excel_group() -> None:
    """Excel workbook operations."""


@excel_group.command("create")
@click.argument("output", type=click.Path())
@click.option("--sheet", default="Sheet1")
@click.option("--data-json", default="[]", help="JSON two-dimensional array of cell values.")
@click.pass_context
def excel_create(ctx: click.Context, output: str, sheet: str, data_json: str) -> None:
    try:
        rows = json.loads(data_json)
        if not isinstance(rows, list) or any(not isinstance(row, list) for row in rows):
            raise ValueError("--data-json must be a JSON two-dimensional array")
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"invalid --data-json: {exc}") from exc
    _run(lambda: documents.create_excel(output, sheet=sheet, rows=rows,
                                        overwrite=ctx.obj["overwrite"], dry_run=ctx.obj["dry_run"],
                                        timeout=ctx.obj["timeout"]), json_mode=ctx.obj["json_mode"])


@excel_group.command("info")
@click.argument("input", type=click.Path(exists=True, dir_okay=False))
@click.pass_context
def excel_info(ctx: click.Context, input: str) -> None:
    _run(lambda: documents.excel_info(input, timeout=ctx.obj["timeout"]), json_mode=ctx.obj["json_mode"])


@excel_group.command("inspect")
@click.argument("input", type=click.Path(exists=True, dir_okay=False))
@click.pass_context
def excel_inspect(ctx: click.Context, input: str) -> None:
    _run(lambda: documents.inspect("excel", input, timeout=ctx.obj["timeout"]), json_mode=ctx.obj["json_mode"])


@excel_group.command("operations")
@click.pass_context
def excel_operations(ctx: click.Context) -> None:
    _run(lambda: documents.operation_schema("excel"), json_mode=ctx.obj["json_mode"])


@excel_group.command("edit")
@click.argument("input", type=click.Path(exists=True, dir_okay=False))
@click.argument("output", type=click.Path())
@click.option("--operations-json", default=None)
@click.option("--operations-file", type=click.Path(exists=True, dir_okay=False), default=None)
@click.pass_context
def excel_edit(ctx: click.Context, input: str, output: str, operations_json: str | None, operations_file: str | None) -> None:
    operations = _load_operations(operations_json, operations_file)
    _run(lambda: documents.edit("excel", input, output, operations, overwrite=ctx.obj["overwrite"],
                                dry_run=ctx.obj["dry_run"], timeout=ctx.obj["timeout"]),
         json_mode=ctx.obj["json_mode"])


@excel_group.command("export-pdf")
@click.argument("input", type=click.Path(exists=True, dir_okay=False))
@click.argument("output", type=click.Path())
@click.pass_context
def excel_export_pdf(ctx: click.Context, input: str, output: str) -> None:
    _run(lambda: documents.export_pdf("excel", input, output, overwrite=ctx.obj["overwrite"],
                                      dry_run=ctx.obj["dry_run"], timeout=ctx.obj["timeout"]),
         json_mode=ctx.obj["json_mode"])


@cli.group("powerpoint")
def powerpoint_group() -> None:
    """PowerPoint presentation operations."""


@powerpoint_group.command("create")
@click.argument("output", type=click.Path())
@click.option("--title", default="")
@click.option("--subtitle", default="")
@click.pass_context
def powerpoint_create(ctx: click.Context, output: str, title: str, subtitle: str) -> None:
    _run(lambda: documents.create_powerpoint(output, title=title, subtitle=subtitle,
                                              overwrite=ctx.obj["overwrite"], dry_run=ctx.obj["dry_run"],
                                              timeout=ctx.obj["timeout"]), json_mode=ctx.obj["json_mode"])


@powerpoint_group.command("info")
@click.argument("input", type=click.Path(exists=True, dir_okay=False))
@click.pass_context
def powerpoint_info(ctx: click.Context, input: str) -> None:
    _run(lambda: documents.powerpoint_info(input, timeout=ctx.obj["timeout"]), json_mode=ctx.obj["json_mode"])


@powerpoint_group.command("inspect")
@click.argument("input", type=click.Path(exists=True, dir_okay=False))
@click.pass_context
def powerpoint_inspect(ctx: click.Context, input: str) -> None:
    _run(lambda: documents.inspect("powerpoint", input, timeout=ctx.obj["timeout"]), json_mode=ctx.obj["json_mode"])


@powerpoint_group.command("operations")
@click.pass_context
def powerpoint_operations(ctx: click.Context) -> None:
    _run(lambda: documents.operation_schema("powerpoint"), json_mode=ctx.obj["json_mode"])


@powerpoint_group.command("edit")
@click.argument("input", type=click.Path(exists=True, dir_okay=False))
@click.argument("output", type=click.Path())
@click.option("--operations-json", default=None)
@click.option("--operations-file", type=click.Path(exists=True, dir_okay=False), default=None)
@click.pass_context
def powerpoint_edit(ctx: click.Context, input: str, output: str, operations_json: str | None, operations_file: str | None) -> None:
    operations = _load_operations(operations_json, operations_file)
    _run(lambda: documents.edit("powerpoint", input, output, operations, overwrite=ctx.obj["overwrite"],
                                dry_run=ctx.obj["dry_run"], timeout=ctx.obj["timeout"]),
         json_mode=ctx.obj["json_mode"])


@powerpoint_group.command("export-pdf")
@click.argument("input", type=click.Path(exists=True, dir_okay=False))
@click.argument("output", type=click.Path())
@click.pass_context
def powerpoint_export_pdf(ctx: click.Context, input: str, output: str) -> None:
    _run(lambda: documents.export_pdf("powerpoint", input, output, overwrite=ctx.obj["overwrite"],
                                      dry_run=ctx.obj["dry_run"], timeout=ctx.obj["timeout"]),
         json_mode=ctx.obj["json_mode"])


@cli.group("preview")
def preview_group() -> None:
    """Produce and inspect preview bundles."""


@preview_group.command("recipes")
@click.pass_context
def preview_recipes(ctx: click.Context) -> None:
    _run(preview.recipes, json_mode=ctx.obj["json_mode"])


@preview_group.command("capture")
@click.argument("input", type=click.Path(exists=True, dir_okay=False))
@click.option("--output-root", type=click.Path(), default=None)
@click.pass_context
def preview_capture(ctx: click.Context, input: str, output_root: str | None) -> None:
    _run(lambda: preview.capture(input, output_root=output_root, timeout=ctx.obj["timeout"]),
         json_mode=ctx.obj["json_mode"])


@preview_group.command("latest")
@click.argument("input", type=click.Path(exists=True, dir_okay=False))
@click.option("--output-root", type=click.Path(), default=None)
@click.pass_context
def preview_latest(ctx: click.Context, input: str, output_root: str | None) -> None:
    _run(lambda: preview.latest(input, output_root=output_root), json_mode=ctx.obj["json_mode"])


def _repl(ctx: click.Context) -> None:
    """Run a small command REPL without creating a long-lived Office session."""
    skin = ReplSkin("microsoft-office", version="0.2.0")
    skin.print_banner()
    click.echo("Type 'help' for commands or 'exit' to quit.")
    while True:
        try:
            line = input("office> ").strip()
        except (EOFError, KeyboardInterrupt):
            click.echo()
            break
        if not line:
            continue
        if line.lower() in {"exit", "quit"}:
            break
        if line.lower() == "help":
            click.echo(cli.get_help(ctx))
            continue
        try:
            args = shlex.split(line, posix=False)
            cli.main(args=args, prog_name="cli-anything-microsoft-office", standalone_mode=False)
        except SystemExit:
            pass
        except click.ClickException as exc:
            exc.show()
    skin.print_goodbye()


main = cli
