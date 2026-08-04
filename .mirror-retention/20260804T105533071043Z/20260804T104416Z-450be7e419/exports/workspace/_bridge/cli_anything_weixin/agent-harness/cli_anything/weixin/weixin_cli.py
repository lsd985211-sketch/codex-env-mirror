from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from cli_anything.weixin import __version__
from cli_anything.weixin.core import windows


def emit(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        click.echo(payload.get("message") or json.dumps(payload, ensure_ascii=False, indent=2))


def fail(message: str, *, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps({"ok": False, "error": message}, ensure_ascii=False, indent=2), err=True)
        raise click.exceptions.Exit(1)
    raise click.ClickException(message)


@click.group()
@click.option("--json-output", "--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.version_option(__version__)
@click.pass_context
def cli(ctx: click.Context, as_json: bool) -> None:
    """Operate Windows Weixin desktop through a conservative CLI harness."""
    ctx.ensure_object(dict)
    ctx.obj["json"] = as_json


@cli.command("status")
@click.pass_context
def status_cmd(ctx: click.Context) -> None:
    """List visible Weixin windows and pick the largest candidate."""
    items = windows.list_windows()
    payload = {
        "ok": bool(items),
        "window_count": len(items),
        "windows": items,
        "best": items[0] if items else None,
    }
    emit(payload, as_json=ctx.obj["json"])


@cli.command("activate")
@click.pass_context
def activate_cmd(ctx: click.Context) -> None:
    """Activate the largest visible Weixin window."""
    payload = {"ok": True, **windows.activate_window()}
    emit(payload, as_json=ctx.obj["json"])


@cli.command("open")
@click.option("--wait-seconds", default=8.0, show_default=True, type=float, help="Seconds to wait for a Weixin window.")
@click.pass_context
def open_cmd(ctx: click.Context, wait_seconds: float) -> None:
    """Open Weixin or activate an existing Weixin window."""
    payload = windows.open_weixin(wait_seconds=wait_seconds)
    emit(payload, as_json=ctx.obj["json"])


@cli.command("close")
@click.option("--confirm-close", default="", help="Must be exactly CLOSE.")
@click.option("--wait-seconds", default=3.0, show_default=True, type=float, help="Seconds to wait for the window to close.")
@click.pass_context
def close_cmd(ctx: click.Context, confirm_close: str, wait_seconds: float) -> None:
    """Close the current Weixin window. Requires explicit confirmation."""
    try:
        payload = windows.close_weixin(confirm_close=confirm_close, wait_seconds=wait_seconds)
    except RuntimeError as exc:
        fail(str(exc), as_json=ctx.obj["json"])
        return
    emit(payload, as_json=ctx.obj["json"])


@cli.command("screenshot")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None)
@click.pass_context
def screenshot_cmd(ctx: click.Context, output: Path | None) -> None:
    """Capture the current Weixin window to a PNG file."""
    payload = {"ok": True, **windows.screenshot(output)}
    emit(payload, as_json=ctx.obj["json"])


@cli.group("chat")
def chat_group() -> None:
    """Operate visible chat list selection without sending messages."""


@chat_group.command("select-row")
@click.option("--index", default=3, show_default=True, type=int, help="Visible chat row index, 1-based.")
@click.option("--x", "x_coord", default=180, show_default=True, type=int, help="Window-relative X coordinate in the chat list.")
@click.option("--first-y", default=135, show_default=True, type=int, help="Window-relative Y center of the first visible row.")
@click.option("--row-height", default=82, show_default=True, type=int, help="Approximate visible row height.")
@click.option("--output-dir", type=click.Path(path_type=Path), default=None)
@click.pass_context
def chat_select_row_cmd(
    ctx: click.Context,
    index: int,
    x_coord: int,
    first_y: int,
    row_height: int,
    output_dir: Path | None,
) -> None:
    """Select a visible chat row by approximate row geometry."""
    try:
        payload = windows.select_chat_row(
            index=index,
            x=x_coord,
            first_y=first_y,
            row_height=row_height,
            output_dir=output_dir,
        )
    except RuntimeError as exc:
        fail(str(exc), as_json=ctx.obj["json"])
        return
    emit(payload, as_json=ctx.obj["json"])


@chat_group.command("search")
@click.argument("query")
@click.option("--select-first", is_flag=True, help="Click the first visible result after searching.")
@click.option("--output-dir", type=click.Path(path_type=Path), default=None)
@click.pass_context
def chat_search_cmd(ctx: click.Context, query: str, select_first: bool, output_dir: Path | None) -> None:
    """Search chats/contacts and optionally select the first visible result."""
    try:
        payload = windows.search_chat(query, select_first=select_first, output_dir=output_dir)
    except RuntimeError as exc:
        fail(str(exc), as_json=ctx.obj["json"])
        return
    emit(payload, as_json=ctx.obj["json"])


@chat_group.command("clear-search")
@click.option("--output-dir", type=click.Path(path_type=Path), default=None)
@click.pass_context
def chat_clear_search_cmd(ctx: click.Context, output_dir: Path | None) -> None:
    """Clear the Weixin search box and exit search state."""
    payload = windows.clear_search(output_dir=output_dir)
    emit(payload, as_json=ctx.obj["json"])


@cli.group("panel")
def panel_group() -> None:
    """Open and close safe Weixin panels without inserting content."""


@panel_group.command("emoji-smoke")
@click.option("--confirm-smoke", default="", help="Must be exactly PANEL.")
@click.option("--output-dir", type=click.Path(path_type=Path), default=None)
@click.pass_context
def panel_emoji_smoke_cmd(ctx: click.Context, confirm_smoke: str, output_dir: Path | None) -> None:
    """Open the emoji panel, close it with Escape, and verify screenshots."""
    try:
        payload = windows.emoji_smoke(confirm_smoke=confirm_smoke, output_dir=output_dir)
    except RuntimeError as exc:
        fail(str(exc), as_json=ctx.obj["json"])
        return
    emit(payload, as_json=ctx.obj["json"])


@cli.group("file")
def file_group() -> None:
    """Operate Weixin file picker safely."""


@file_group.command("picker-smoke")
@click.option("--confirm-smoke", default="", help="Must be exactly PICKER.")
@click.option("--output-dir", type=click.Path(path_type=Path), default=None)
@click.pass_context
def file_picker_smoke_cmd(ctx: click.Context, confirm_smoke: str, output_dir: Path | None) -> None:
    """Open the file picker, cancel it, and verify no picker remains."""
    try:
        payload = windows.file_picker_smoke(confirm_smoke=confirm_smoke, output_dir=output_dir)
    except RuntimeError as exc:
        fail(str(exc), as_json=ctx.obj["json"])
        return
    emit(payload, as_json=ctx.obj["json"])


@cli.group("draft")
def draft_group() -> None:
    """Operate the current Weixin input field without sending by default."""


@draft_group.command("paste")
@click.argument("text")
@click.option("--no-activate", is_flag=True, help="Do not activate Weixin before pasting.")
@click.pass_context
def draft_paste_cmd(ctx: click.Context, text: str, no_activate: bool) -> None:
    """Paste TEXT into the currently focused Weixin input field."""
    payload = windows.paste_draft(text, activate=not no_activate)
    emit(payload, as_json=ctx.obj["json"])


@draft_group.command("focus-input")
@click.option("--x-ratio", default=0.55, show_default=True, type=float, help="Window-relative input click X ratio.")
@click.option("--y-ratio", default=0.90, show_default=True, type=float, help="Window-relative input click Y ratio.")
@click.pass_context
def draft_focus_input_cmd(ctx: click.Context, x_ratio: float, y_ratio: float) -> None:
    """Click the expected current-chat input area."""
    payload = windows.focus_input(x_ratio=x_ratio, y_ratio=y_ratio)
    emit(payload, as_json=ctx.obj["json"])


@draft_group.command("clear")
@click.option("--no-activate", is_flag=True, help="Do not activate Weixin before clearing.")
@click.pass_context
def draft_clear_cmd(ctx: click.Context, no_activate: bool) -> None:
    """Clear the currently focused Weixin input field."""
    payload = windows.clear_input(activate=not no_activate)
    emit(payload, as_json=ctx.obj["json"])


@draft_group.command("smoke")
@click.argument("text", required=False, default="")
@click.option("--confirm-smoke", default="", help="Must be exactly DRAFT.")
@click.option("--output-dir", type=click.Path(path_type=Path), default=None)
@click.option("--x-ratio", default=0.55, show_default=True, type=float, help="Window-relative input click X ratio.")
@click.option("--y-ratio", default=0.90, show_default=True, type=float, help="Window-relative input click Y ratio.")
@click.pass_context
def draft_smoke_cmd(
    ctx: click.Context,
    text: str,
    confirm_smoke: str,
    output_dir: Path | None,
    x_ratio: float,
    y_ratio: float,
) -> None:
    """Paste a draft marker, screenshot it, then clear it without sending."""
    marker = text or "CLI_WEIXIN_DRAFT_TEST_DO_NOT_SEND"
    try:
        payload = windows.draft_smoke(
            marker,
            confirm_smoke=confirm_smoke,
            output_dir=output_dir,
            x_ratio=x_ratio,
            y_ratio=y_ratio,
        )
    except RuntimeError as exc:
        fail(str(exc), as_json=ctx.obj["json"])
        return
    emit(payload, as_json=ctx.obj["json"])


@draft_group.command("send-current")
@click.option("--confirm-send", default="", help="Must be exactly SEND.")
@click.pass_context
def draft_send_current_cmd(ctx: click.Context, confirm_send: str) -> None:
    """Send the current Weixin input. Requires explicit confirmation."""
    try:
        payload = windows.send_current(confirm_send=confirm_send)
    except RuntimeError as exc:
        fail(str(exc), as_json=ctx.obj["json"])
        return
    emit(payload, as_json=ctx.obj["json"])


@cli.group("message")
def message_group() -> None:
    """Prepare or send text messages through the current Weixin chat."""


@message_group.command("prepare")
@click.argument("text")
@click.option("--confirm-prepare", default="", help="Must be exactly DRAFT.")
@click.option("--output-dir", type=click.Path(path_type=Path), default=None)
@click.pass_context
def message_prepare_cmd(ctx: click.Context, text: str, confirm_prepare: str, output_dir: Path | None) -> None:
    """Prepare a draft message and leave it visible for user/agent verification."""
    try:
        payload = windows.prepare_message(text, confirm_prepare=confirm_prepare, output_dir=output_dir)
    except RuntimeError as exc:
        fail(str(exc), as_json=ctx.obj["json"])
        return
    emit(payload, as_json=ctx.obj["json"])


@message_group.command("send-text")
@click.argument("text")
@click.option("--confirm-send", default="", help="Must be exactly SEND.")
@click.option("--output-dir", type=click.Path(path_type=Path), default=None)
@click.pass_context
def message_send_text_cmd(ctx: click.Context, text: str, confirm_send: str, output_dir: Path | None) -> None:
    """Prepare and send TEXT in the current chat. Requires explicit confirmation."""
    try:
        payload = windows.send_text(text, confirm_send=confirm_send, output_dir=output_dir)
    except RuntimeError as exc:
        fail(str(exc), as_json=ctx.obj["json"])
        return
    emit(payload, as_json=ctx.obj["json"])


if __name__ == "__main__":
    cli()
