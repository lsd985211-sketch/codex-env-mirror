#!/usr/bin/env python3

import tempfile
import unittest
from unittest import mock
import contextlib
import io
import json
from pathlib import Path

from resource_content_store import adopt_content_artifacts, expand_content, persist_content
import resource_content_store
from resource_cli import main as resource_cli_main


class ResourceContentStoreTests(unittest.TestCase):
    def test_large_html_preview_is_bounded_and_tail_is_queryable_offline(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            marker = "CEDAR-FORBID-OVERRIDES-PERMIT"
            body = ("<p>prefix</p>" * 1200) + f"<section>{marker}</section>"
            persisted = persist_content(
                content_dir=root / "fetch",
                raw=body.encode("utf-8"),
                source_url="https://example.test/policy",
                content_type="text/html; charset=utf-8",
                preview_bytes=8192,
            )
            self.assertTrue(persisted["preview_truncated"])
            self.assertLessEqual(Path(persisted["preview_ref"]).stat().st_size, 8192)
            adopted = adopt_content_artifacts(request_dir=root / "request", metadata=persisted)
            expanded = expand_content(content_ref=Path(adopted["content_ref"]), query=marker)
            self.assertTrue(expanded["ok"])
            self.assertIn(marker, expanded["text"])
            self.assertFalse(expanded["network_used"])
            self.assertEqual(expanded["content_sha256"], adopted["content_sha256"])
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = resource_cli_main(["expand-content", "--content-ref", adopted["content_ref"], "--query", marker, "--json"])
            cli_payload = json.loads(stdout.getvalue())
            self.assertEqual(code, 0)
            self.assertIn(marker, cli_payload["text"])
            self.assertFalse(cli_payload["network_used"])

    def test_missing_query_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            persisted = persist_content(
                content_dir=root,
                raw=b"known text",
                source_url="https://example.test",
                content_type="text/plain",
            )
            expanded = expand_content(content_ref=Path(persisted["content_ref"]), query="missing")
            self.assertFalse(expanded["ok"])
            self.assertEqual(expanded["reason"], "query_not_found")

    def test_json_with_embedded_html_is_not_reclassified_as_html(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            persisted = persist_content(
                content_dir=Path(raw),
                raw=b'{"description":"<p>markup inside json</p>"}',
                source_url="https://example.test/package.json",
                content_type="application/json",
            )
            self.assertEqual(persisted["parser"], "declared_non_html_text")
            self.assertEqual(
                json.loads(Path(persisted["content_ref"]).read_text(encoding="utf-8"))["description"],
                "<p>markup inside json</p>",
            )

    def test_html_prefers_managed_beautifulsoup_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as raw, mock.patch.object(
            resource_content_store,
            "_managed_beautifulsoup_text",
            return_value=("managed visible text", "managed_beautifulsoup_html_parser"),
        ):
            persisted = persist_content(
                content_dir=Path(raw),
                raw=b"<html><body><p>visible</p></body></html>",
                source_url="https://example.test/page",
                content_type="text/html",
            )
            self.assertEqual(persisted["parser"], "managed_beautifulsoup_html_parser")
            self.assertEqual(Path(persisted["content_ref"]).read_text(encoding="utf-8"), "managed visible text")


if __name__ == "__main__":
    unittest.main()
