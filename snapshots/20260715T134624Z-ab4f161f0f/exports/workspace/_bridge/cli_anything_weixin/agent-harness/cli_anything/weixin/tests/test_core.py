import json

from click.testing import CliRunner

from cli_anything.weixin.weixin_cli import cli


def test_help():
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Weixin" in result.output


def test_send_requires_confirmation():
    result = CliRunner().invoke(cli, ["--json", "draft", "send-current"])
    assert result.exit_code != 0
    assert "Refusing to send" in result.output


def test_close_requires_confirmation():
    result = CliRunner().invoke(cli, ["--json", "close"])
    assert result.exit_code != 0
    assert "Refusing to close" in result.output


def test_smoke_requires_confirmation():
    result = CliRunner().invoke(cli, ["--json", "draft", "smoke", "test"])
    assert result.exit_code != 0
    assert "Refusing draft smoke" in result.output


def test_select_row_rejects_invalid_index():
    result = CliRunner().invoke(cli, ["--json", "chat", "select-row", "--index", "0"])
    assert result.exit_code != 0
    assert "Chat row index" in result.output


def test_search_rejects_empty_query():
    result = CliRunner().invoke(cli, ["--json", "chat", "search", ""])
    assert result.exit_code != 0
    assert "Search query" in result.output


def test_emoji_smoke_requires_confirmation():
    result = CliRunner().invoke(cli, ["--json", "panel", "emoji-smoke"])
    assert result.exit_code != 0
    assert "Refusing emoji smoke" in result.output


def test_file_picker_smoke_requires_confirmation():
    result = CliRunner().invoke(cli, ["--json", "file", "picker-smoke"])
    assert result.exit_code != 0
    assert "Refusing file picker smoke" in result.output


def test_message_prepare_requires_confirmation():
    result = CliRunner().invoke(cli, ["--json", "message", "prepare", "hello"])
    assert result.exit_code != 0
    assert "Refusing message prepare" in result.output


def test_message_send_requires_confirmation():
    result = CliRunner().invoke(cli, ["--json", "message", "send-text", "hello"])
    assert result.exit_code != 0
    assert "Refusing text send" in result.output
