#!/usr/bin/env python3

from __future__ import annotations

import json
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from codex_node_repl_wsl_proxy import (
    MINIMUM_NODE_VERSION,
    copy_stream,
    iter_input_lines,
    project_json_line,
    select_node_runtime,
    windows_file_uri,
    windows_path,
)


class NodeReplWslProxyTests(unittest.TestCase):
    def test_parent_input_frames_short_mcp_message_without_waiting_for_eof(self) -> None:
        read_fd, write_fd = os.pipe()
        try:
            with os.fdopen(read_fd, "rb", buffering=0) as source:
                os.write(write_fd, b'{"jsonrpc":"2.0"}\n')
                self.assertEqual(b'{"jsonrpc":"2.0"}\n', next(iter_input_lines(source)))
        finally:
            os.close(write_fd)

    def test_stdio_proxy_forwards_short_messages_line_by_line(self) -> None:
        class ShortMessageSource:
            def __init__(self) -> None:
                self.lines = iter((b'{"jsonrpc":"2.0"}\n', b""))

            def readline(self) -> bytes:
                return next(self.lines)

            def read1(self, _size: int) -> bytes:
                raise AssertionError("large buffered reads must not hold short MCP messages")

        destination = io.BytesIO()
        copy_stream(ShortMessageSource(), destination)
        self.assertEqual(b'{"jsonrpc":"2.0"}\n', destination.getvalue())

    def test_selects_valid_explicit_node_before_managed_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            windows_home = Path(raw) / "Users" / "tester"
            explicit = windows_home / "explicit" / "node.exe"
            managed = windows_home / "AppData" / "Local" / "OpenAI" / "Codex" / "runtimes" / "cua_node" / "current" / "bin" / "node.exe"
            explicit.parent.mkdir(parents=True)
            managed.parent.mkdir(parents=True)
            explicit.touch()
            managed.touch()
            selected = select_node_runtime(
                windows_home=windows_home,
                environ={"NODE_REPL_NODE_PATH": str(explicit)},
                version_reader=lambda path: (24, 1, 0),
            )
            self.assertEqual(explicit, selected)

    def test_rejects_old_explicit_and_ambient_path_then_selects_managed_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            windows_home = Path(raw) / "Users" / "tester"
            old = windows_home / "Downloads" / "project" / "node.exe"
            managed = windows_home / "AppData" / "Local" / "OpenAI" / "Codex" / "runtimes" / "cua_node" / "current" / "bin" / "node.exe"
            old.parent.mkdir(parents=True)
            managed.parent.mkdir(parents=True)
            old.touch()
            managed.touch()
            selected = select_node_runtime(
                windows_home=windows_home,
                environ={"NODE_REPL_NODE_PATH": str(old), "PATH": str(old.parent)},
                version_reader=lambda path: (22, 11, 0) if path == old else MINIMUM_NODE_VERSION,
            )
            self.assertEqual(managed, selected)

    def test_projects_selected_wsl_node_path_back_to_windows(self) -> None:
        self.assertEqual(
            r"C:\Users\tester\AppData\Local\OpenAI\Codex\runtimes\cua_node\current\bin\node.exe",
            windows_path(Path("/mnt/c/Users/tester/AppData/Local/OpenAI/Codex/runtimes/cua_node/current/bin/node.exe")),
        )

    def test_proxy_derives_windows_home_and_injects_selected_runtime(self) -> None:
        node_repl = Path("/mnt/c/Users/tester/.local/bin/node_repl.exe")
        selected = Path("/mnt/c/Users/tester/AppData/Local/OpenAI/Codex/runtimes/cua_node/current/bin/node.exe")
        child = mock.Mock()
        child.stdin = mock.Mock()
        child.stdout = mock.Mock()
        child.stderr = mock.Mock()
        child.wait.return_value = 0
        with (
            mock.patch("codex_node_repl_wsl_proxy.select_node_runtime", return_value=selected) as select,
            mock.patch("codex_node_repl_wsl_proxy.subprocess.Popen", return_value=child) as popen,
            mock.patch("codex_node_repl_wsl_proxy.threading.Thread") as thread,
            mock.patch("codex_node_repl_wsl_proxy.iter_input_lines", return_value=iter((b'{"jsonrpc":"2.0"}\n',))),
        ):
            from codex_node_repl_wsl_proxy import run_proxy

            self.assertEqual(0, run_proxy(node_repl=node_repl, distribution="Codex-Wsl-Lab"))
        select.assert_called_once_with(windows_home=Path("/mnt/c/Users/tester"))
        self.assertEqual(
            r"C:\Users\tester\AppData\Local\OpenAI\Codex\runtimes\cua_node\current\bin\node.exe",
            popen.call_args.kwargs["env"]["NODE_REPL_NODE_PATH"],
        )
        self.assertEqual(2, thread.call_count)
        child.stdin.write.assert_called_once_with(b'{"jsonrpc":"2.0"}\n')
        child.stdin.flush.assert_called_once_with()

    def test_proxy_does_not_start_windows_child_before_first_request(self) -> None:
        with (
            mock.patch("codex_node_repl_wsl_proxy.select_node_runtime", return_value=Path("/mnt/c/node.exe")),
            mock.patch("codex_node_repl_wsl_proxy.iter_input_lines", return_value=iter(())),
            mock.patch("codex_node_repl_wsl_proxy.subprocess.Popen") as popen,
        ):
            from codex_node_repl_wsl_proxy import run_proxy

            self.assertEqual(0, run_proxy(node_repl=Path("/mnt/c/Users/tester/.local/bin/node_repl.exe"), distribution="Codex-Wsl-Lab"))
        popen.assert_not_called()

    def test_node_version_probe_never_inherits_mcp_stdin(self) -> None:
        from codex_node_repl_wsl_proxy import read_node_version

        with mock.patch("codex_node_repl_wsl_proxy.subprocess.run") as run:
            run.return_value = SimpleNamespace(returncode=0, stdout="v24.14.0\n", stderr="")
            self.assertEqual((24, 14, 0), read_node_version(Path("/mnt/c/node.exe")))
        self.assertIs(subprocess.DEVNULL, run.call_args.kwargs["stdin"])

    def test_projects_linux_workspace_uri_to_wsl_unc_uri(self) -> None:
        self.assertEqual(
            windows_file_uri("file:///home/codexlab/work/codex workspace/中文", distribution="Codex-Wsl-Lab"),
            "file://wsl.localhost/Codex-Wsl-Lab/home/codexlab/work/codex%20workspace/%E4%B8%AD%E6%96%87",
        )

    def test_projects_mounted_windows_path_to_drive_uri(self) -> None:
        self.assertEqual(
            windows_file_uri("file:///mnt/c/Users/45543/Desktop", distribution="Codex-Wsl-Lab"),
            "file:///C:/Users/45543/Desktop",
        )

    def test_rewrites_only_sandbox_metadata(self) -> None:
        message = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "arguments": {"sandboxCwd": "file:///home/unchanged"},
                "_meta": {
                    "codex/sandbox-state-meta": {
                        "sandboxCwd": "file:///home/codexlab/work/codex-workspace",
                        "permissionProfile": "workspace-write",
                    }
                },
            },
        }
        projected = json.loads(project_json_line((json.dumps(message) + "\n").encode(), distribution="Codex-Wsl-Lab"))
        self.assertEqual(projected["params"]["arguments"]["sandboxCwd"], "file:///home/unchanged")
        self.assertEqual(
            projected["params"]["_meta"]["codex/sandbox-state-meta"]["sandboxCwd"],
            "file://wsl.localhost/Codex-Wsl-Lab/home/codexlab/work/codex-workspace",
        )

    def test_preserves_non_json_bytes(self) -> None:
        self.assertEqual(project_json_line(b"not-json\n", distribution="Codex-Wsl-Lab"), b"not-json\n")


if __name__ == "__main__":
    unittest.main()
