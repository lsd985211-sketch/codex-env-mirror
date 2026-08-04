#!/usr/bin/env python3
"""Focused bounded-output tests for the Hub read-only MCP adapter."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import local_mcp_hub_owner_mcp as owner


def large_gateway_payload() -> dict[str, object]:
    text = "documentation-row\n" * 4000
    return {
        "ok": True,
        "route": {"route": "fresh_stdio", "reason": "stable_default"},
        "result": {
            "ok": True,
            "tool_result_is_error": False,
            "result": {"content": [{"type": "text", "text": text}]},
        },
        "gateway_status": "gateway_tool_call_ok",
        "gateway_state_path": "/tmp/gateway-state.json",
        "transport_isolated_from_current_turn": True,
    }


class LocalMcpHubOwnerMcpTests(unittest.TestCase):
    def call(self, root: str, *, detail: str = "compact") -> dict[str, object]:
        with patch.dict(os.environ, {"CODEX_MCP_RESULT_ARTIFACT_ROOT": root}):
            return owner.call(
                {
                    "profile": "context7",
                    "tool": "query_docs",
                    "arguments": {"query": "context projection"},
                    "detail": detail,
                    "hub_ack": owner.HUB_READONLY_ACK,
                },
                lambda *_args, **_kwargs: large_gateway_payload(),
            )

    def test_large_result_is_artifact_backed_and_default_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = self.call(temp)
            artifact_path = Path(str(result["raw_result_ref"]).removeprefix("artifact:"))
            artifact_exists = artifact_path.is_file()
        encoded = json.dumps(result, ensure_ascii=False).encode("utf-8")
        self.assertTrue(result["ok"])
        self.assertEqual("default_bounded", result["output_mode"])
        self.assertLessEqual(len(encoded), 12 * 1024)
        self.assertTrue(artifact_exists)
        self.assertEqual("mcp_result", result["context_projection"]["source_kind"])
        self.assertFalse(result["context_projection"]["headroom_executed"])

    def test_full_is_richer_but_still_bounded_and_reuses_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            compact = self.call(temp)
            full = self.call(temp, detail="full")
        compact_bytes = len(json.dumps(compact, ensure_ascii=False).encode("utf-8"))
        full_bytes = len(json.dumps(full, ensure_ascii=False).encode("utf-8"))
        self.assertEqual("full_bounded", full["output_mode"])
        self.assertGreater(full_bytes, compact_bytes)
        self.assertLessEqual(full_bytes, 36 * 1024)
        self.assertEqual(compact["raw_result_ref"], full["raw_result_ref"])

    def test_small_result_stays_direct_without_artifact_write(self) -> None:
        def gateway(*_args: object, **_kwargs: object) -> dict[str, object]:
            payload = large_gateway_payload()
            payload["result"] = {
                "ok": True,
                "tool_result_is_error": False,
                "result": {"content": [{"type": "text", "text": "short"}]},
            }
            return payload

        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"CODEX_MCP_RESULT_ARTIFACT_ROOT": temp}
        ):
            result = owner.call(
                {
                    "profile": "context7",
                    "tool": "query_docs",
                    "hub_ack": owner.HUB_READONLY_ACK,
                },
                gateway,
            )
            artifacts = list(Path(temp).rglob("*.json"))
        self.assertEqual("direct", result["context_projection"]["mode"])
        self.assertEqual([], artifacts)


if __name__ == "__main__":
    unittest.main()
