#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import context_artifact_selector as selector


class ContextArtifactSelectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_json_field_and_object_id_read_local_artifact(self) -> None:
        path = self.root / "result.json"
        path.write_text(json.dumps({"status": "failed", "items": [{"id": "row-1", "reason": "root cause"}]}), encoding="utf-8")
        field = selector.select_artifact(str(path), {"kind": "json_field", "path": ["status"]}, allowed_roots=[self.root])
        row = selector.select_artifact(str(path), {"kind": "object_id", "id": "row-1"}, allowed_roots=[self.root])
        self.assertEqual(field["value"], "failed")
        self.assertEqual(row["value"]["reason"], "root cause")

    def test_text_line_byte_and_query_selectors_are_bounded(self) -> None:
        path = self.root / "output.txt"
        path.write_text("alpha\nbeta root\ngamma\ndelta root\n", encoding="utf-8")
        lines = selector.select_artifact(str(path), {"kind": "text_lines", "start": 2, "end": 3}, allowed_roots=[self.root])
        chunk = selector.select_artifact(str(path), {"kind": "byte_range", "start": 0, "end": 5}, allowed_roots=[self.root])
        hits = selector.select_artifact(str(path), {"kind": "query_hit", "query": "root", "context_lines": 0, "max_hits": 1}, allowed_roots=[self.root])
        self.assertEqual(lines["text"], "beta root\ngamma")
        self.assertEqual(chunk["content_base64"], "YWxwaGE=")
        json.dumps(chunk)
        self.assertEqual(len(hits["hits"]), 1)

    def test_text_selectors_do_not_use_path_read_bytes_or_read_text(self) -> None:
        path = self.root / "large.txt"
        path.write_text(("noise\n" * 10000) + "needle\n", encoding="utf-8")
        original_read_bytes = Path.read_bytes
        original_read_text = Path.read_text
        try:
            Path.read_bytes = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("whole-file byte read"))
            Path.read_text = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("whole-file text read"))
            result = selector.select_artifact(
                str(path), {"kind": "query_hit", "query": "needle", "context_lines": 1}, allowed_roots=[self.root]
            )
        finally:
            Path.read_bytes = original_read_bytes
            Path.read_text = original_read_text
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["hits"][0]["line"], 10001)

    def test_hash_mismatch_and_outside_root_fail_closed(self) -> None:
        path = self.root / "result.txt"
        path.write_text("stable", encoding="utf-8")
        mismatch = selector.select_artifact(str(path), {"kind": "text_lines", "start": 1, "end": 1}, expected_sha256="0" * 64, allowed_roots=[self.root])
        outside = selector.select_artifact("/etc/hosts", {"kind": "text_lines", "start": 1, "end": 1}, allowed_roots=[self.root])
        self.assertEqual(mismatch["reason"], "artifact_hash_mismatch")
        self.assertEqual(outside["reason"], "artifact_outside_allowed_roots")

    def test_receipt_section_reuses_json_authority(self) -> None:
        path = self.root / "receipt.json"
        raw = json.dumps({"receipt": {"finalization": {"ok": True}}})
        path.write_text(raw, encoding="utf-8")
        digest = hashlib.sha256(raw.encode()).hexdigest()
        result = selector.select_artifact(str(path), {"kind": "receipt_section", "section": "receipt.finalization"}, expected_sha256=digest, allowed_roots=[self.root])
        self.assertEqual(result["value"], {"ok": True})
        self.assertEqual(result["source_sha256"], digest)


if __name__ == "__main__":
    unittest.main()
