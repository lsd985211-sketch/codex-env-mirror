#!/usr/bin/env python3
"""Focused tests for durable Hub read-only MCP result artifacts."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import local_mcp_hub_result_artifact as artifact_owner


class LocalMcpHubResultArtifactTests(unittest.TestCase):
    def test_persist_is_hash_stable_private_and_exactly_recoverable(self) -> None:
        payload = {"content": [{"type": "text", "text": "x" * 20000}]}
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"CODEX_MCP_RESULT_ARTIFACT_ROOT": temp}
        ):
            first = artifact_owner.persist_result(payload, profile="context7", tool="query_docs")
            second = artifact_owner.persist_result(payload, profile="context7", tool="query_docs")
            path = Path(first["artifact_path"])
            restored = json.loads(path.read_text(encoding="utf-8"))
            mode = path.stat().st_mode & 0o777
            parent_modes = [directory.stat().st_mode & 0o777 for directory in path.parents[:3]]
        self.assertTrue(first["ok"])
        self.assertEqual(first["artifact_ref"], second["artifact_ref"])
        self.assertTrue(second["reused"])
        self.assertEqual(payload, restored["result"])
        self.assertEqual(0o600, mode)
        self.assertEqual([0o700, 0o700, 0o700], parent_modes)
        self.assertEqual(first["source_sha256"], artifact_owner.payload_sha256(payload))

    def test_same_result_from_different_tool_keeps_distinct_provenance(self) -> None:
        payload = {"content": [{"type": "text", "text": "same"}]}
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"CODEX_MCP_RESULT_ARTIFACT_ROOT": temp}
        ):
            first = artifact_owner.persist_result(payload, profile="context7", tool="query_docs")
            second = artifact_owner.persist_result(payload, profile="openai-docs", tool="fetch_openai_doc")
            first_wrapper = json.loads(Path(first["artifact_path"]).read_text(encoding="utf-8"))
            second_wrapper = json.loads(Path(second["artifact_path"]).read_text(encoding="utf-8"))
        self.assertEqual(first["source_sha256"], second["source_sha256"])
        self.assertNotEqual(first["artifact_ref"], second["artifact_ref"])
        self.assertEqual("context7", first_wrapper["profile"])
        self.assertEqual("openai-docs", second_wrapper["profile"])


if __name__ == "__main__":
    unittest.main()
