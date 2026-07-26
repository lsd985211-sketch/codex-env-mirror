from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from mcp_lazy_stdio_proxy import cache_path, write_cache
from mcp_profile_launcher import profile_command
from mcp_profile_launcher_process import run_profile_process
from resource_process_doctor import matched_processes_and_groups, resource_process_issues, resource_process_summary


ROOT = Path(__file__).resolve().parents[1]
PROXY = ROOT / "_bridge" / "mcp_lazy_stdio_proxy.py"


FAKE_CHILD = r'''
import json
import sys
from pathlib import Path

marker = Path(sys.argv[1])
marker.write_text("started", encoding="utf-8")

for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    message_id = request.get("id")
    if method == "initialize":
        result = {
            "protocolVersion": request.get("params", {}).get("protocolVersion", "2025-11-25"),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "fake-child", "version": "1.0"},
        }
    elif method == "tools/list":
        result = {"tools": [{"name": "fake_tool", "description": "fake", "inputSchema": {"type": "object"}}]}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": "called"}], "isError": False}
    elif method == "ping":
        result = {}
    else:
        continue
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": message_id, "result": result}, separators=(",", ":")) + "\n")
    sys.stdout.flush()
'''


class ProxyClient:
    def __init__(self, proc: subprocess.Popen[str]) -> None:
        self.proc = proc
        self.messages: queue.Queue[dict | None] = queue.Queue()

        def pump() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                try:
                    self.messages.put(json.loads(line))
                except json.JSONDecodeError:
                    continue
            self.messages.put(None)

        threading.Thread(target=pump, daemon=True).start()

    def send(self, payload: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
        self.proc.stdin.flush()

    def wait_id(self, message_id: int, timeout: float = 8.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            item = self.messages.get(timeout=max(0.05, deadline - time.monotonic()))
            if item is None:
                break
            if item.get("id") == message_id:
                return item
        raise AssertionError(f"response id={message_id} not received")

    def close(self) -> tuple[str, str]:
        if self.proc.stdin is not None:
            self.proc.stdin.close()
        self.proc.wait(timeout=12)
        stdout = self.proc.stdout.read() if self.proc.stdout is not None else ""
        stderr = self.proc.stderr.read() if self.proc.stderr is not None else ""
        if self.proc.stdout is not None:
            self.proc.stdout.close()
        if self.proc.stderr is not None:
            self.proc.stderr.close()
        return stdout, stderr


class McpLazyStdioProxyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cache_dir = self.root / "cache"
        self.marker = self.root / "child-started.txt"
        self.fake_child = self.root / "fake_child.py"
        self.fake_child.write_text(FAKE_CHILD, encoding="utf-8")
        self.child_command = [sys.executable, str(self.fake_child), str(self.marker)]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def seed_cache(self) -> None:
        write_cache(
            cache_dir=self.cache_dir,
            profile="fake",
            command=self.child_command,
            child_cwd=str(self.root),
            initialize_result={
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "fake-child", "version": "1.0"},
            },
            tools_result={"tools": [{"name": "fake_tool", "description": "fake", "inputSchema": {"type": "object"}}]},
        )

    def start_proxy(self) -> ProxyClient:
        command = [
            sys.executable,
            str(PROXY),
            "--profile",
            "fake",
            "--cache-dir",
            str(self.cache_dir),
            "--child-cwd",
            str(self.root),
            "--child-timeout-seconds",
            "8",
            "--",
            *self.child_command,
        ]
        proc = subprocess.Popen(
            command,
            cwd=str(ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return ProxyClient(proc)

    @staticmethod
    def initialize(client: ProxyClient) -> None:
        client.send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
            }
        )
        client.wait_id(1)
        client.send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def test_cached_initialize_and_tools_list_do_not_start_child(self) -> None:
        self.seed_cache()
        client = self.start_proxy()
        self.initialize(client)
        client.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        response = client.wait_id(2)
        self.assertEqual(response["result"]["tools"][0]["name"], "fake_tool")
        self.assertFalse(self.marker.exists())
        client.close()

    def test_first_tool_call_starts_one_child_and_relays_result(self) -> None:
        self.seed_cache()
        client = self.start_proxy()
        self.initialize(client)
        client.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        client.wait_id(2)
        self.assertFalse(self.marker.exists())
        client.send({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "fake_tool", "arguments": {}}})
        response = client.wait_id(3)
        self.assertEqual(response["result"]["content"][0]["text"], "called")
        self.assertTrue(self.marker.exists())
        client.close()

    def test_cache_miss_warms_catalog_once(self) -> None:
        client = self.start_proxy()
        self.initialize(client)
        client.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        response = client.wait_id(2)
        self.assertEqual(response["result"]["tools"][0]["name"], "fake_tool")
        self.assertTrue(self.marker.exists())
        self.assertTrue(cache_path(self.cache_dir, "fake").exists())
        client.close()

    def test_profile_launcher_wraps_only_selected_stateful_profiles(self) -> None:
        _, lazy_command = profile_command("cdev")
        _, eager_command = profile_command("cdev", lazy=False)
        self.assertTrue(any("mcp_lazy_stdio_proxy.py" in item for item in lazy_command))
        self.assertFalse(any("mcp_lazy_stdio_proxy.py" in item for item in eager_command))

    def test_profile_launcher_enters_lazy_proxy_in_current_process(self) -> None:
        observed: dict[str, object] = {}
        env_key = "CODEX_TEST_LAZY_PROFILE_ENV"

        def entrypoint(argv: list[str] | None) -> int:
            observed["argv"] = argv
            observed["cwd"] = str(Path.cwd())
            observed["env"] = os.environ.get(env_key)
            return 7

        command = [sys.executable, str(PROXY), "--profile", "fake", "--", *self.child_command]
        previous_cwd = Path.cwd()
        self.assertNotIn(env_key, os.environ)

        result = run_profile_process(
            command,
            extra_env={env_key: "enabled"},
            cwd=self.root,
            lazy_proxy=PROXY,
            lazy_entrypoint=entrypoint,
        )

        self.assertEqual(result, 7)
        self.assertEqual(observed["argv"], command[2:])
        self.assertEqual(observed["cwd"], str(self.root))
        self.assertEqual(observed["env"], "enabled")
        self.assertEqual(Path.cwd(), previous_cwd)
        self.assertNotIn(env_key, os.environ)

    def test_non_lazy_profile_keeps_guarded_subprocess_path(self) -> None:
        command = [sys.executable, str(self.fake_child), str(self.marker)]
        completed = mock.Mock(returncode=9)

        with mock.patch("mcp_profile_launcher_process.subprocess.run", return_value=completed) as run:
            result = run_profile_process(
                command,
                extra_env={"CODEX_TEST_NON_LAZY": "enabled"},
                cwd=self.root,
                lazy_proxy=PROXY,
            )

        self.assertEqual(result, 9)
        run.assert_called_once()
        self.assertEqual(run.call_args.kwargs["cwd"], str(self.root))
        self.assertEqual(run.call_args.kwargs["env"]["CODEX_TEST_NON_LAZY"], "enabled")

    def test_process_doctor_classifies_proxy_before_embedded_child_command(self) -> None:
        matched, grouped = matched_processes_and_groups(
            [
                {
                    "pid": 101,
                    "parent_pid": 1,
                    "name": "python.exe",
                    "command_line": "python mcp_lazy_stdio_proxy.py --profile cdev -- node chrome-devtools-mcp@1.4.0",
                    "working_set_mb": 15.0,
                    "cpu_seconds": 0.1,
                    "start_time": "2026-07-15T00:00:00+00:00",
                }
            ]
        )
        self.assertEqual(matched[0]["group"], "mcp_lazy_stdio_proxy")
        self.assertIn("mcp_lazy_stdio_proxy", grouped)
        self.assertNotIn("chrome-devtools", grouped)

    def test_lazy_proxy_roots_do_not_consume_heavy_mcp_pressure_budget(self) -> None:
        groups = [
            {
                "group": "mcp_lazy_stdio_proxy",
                "category": "mcp_lazy_proxy",
                "count": 20,
                "root_instance_count": 20,
                "working_set_mb": 300.0,
                "effective_expected_max": 64,
            }
        ]
        issues = resource_process_issues(groups, {"state": "ok", "observations": []}, {})
        summary = resource_process_summary(groups=groups, issues=issues, owner={"ok": True}, observations={})
        self.assertFalse(any(item.get("code") == "mcp_session_multiplication_pressure" for item in issues))
        self.assertEqual(summary["mcp_root_instance_count"], 0)


if __name__ == "__main__":
    unittest.main()
