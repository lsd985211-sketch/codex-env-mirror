import json
import sys
import unittest
from unittest.mock import patch

from pathlib import Path

BRIDGE = Path(__file__).resolve().parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

import semantic_capability_owner as owner  # noqa: E402


class SemanticCapabilityOwnerTests(unittest.TestCase):
    def test_status_is_non_probe_and_declares_fallback(self):
        result = owner.status()
        self.assertTrue(result["ok"])
        self.assertIsNone(result["callable"])
        self.assertEqual(result["fallback"], "structured_fts_graph")
        self.assertEqual(result["persistence"], "none")
        self.assertEqual(result["model_capabilities"]["active_transport_modes"], ["dense"])
        self.assertIn("sparse", result["model_capabilities"]["inactive_transport_modes"])
        self.assertFalse(result["model_capabilities"]["silent_truncation"])

    def test_non_loopback_endpoint_is_rejected(self):
        result = owner.status(endpoint="https://example.com/api/embed")
        self.assertFalse(result["ok"])
        self.assertEqual(result["vector_reason"], "endpoint_not_loopback")

    def test_probe_failure_is_explicit_and_fail_closed(self):
        with patch.object(owner, "_request_embeddings", side_effect=OSError("offline")):
            result = owner.status(probe=True)
        self.assertFalse(result["callable"])
        self.assertFalse(result["healthy"])
        self.assertTrue(result["vector_reason"].startswith("probe_failed:"))

    def test_status_probe_uses_the_same_bounded_default_as_cli(self):
        with patch.object(owner, "_request_embeddings", return_value=[[1.0]]) as request:
            result = owner.status(probe=True)
        self.assertTrue(result["healthy"])
        self.assertEqual(owner.DEFAULT_PROBE_TIMEOUT, request.call_args.kwargs["timeout"])

    def test_embed_batches_input_and_preserves_vectors(self):
        with patch.object(owner, "_request_embeddings", return_value=[[1.0, 0.0], [0.0, 1.0]]) as request:
            result = owner.embed(["alpha", "beta"])
        request.assert_called_once()
        self.assertTrue(result["vector_used"])
        self.assertEqual(result["dimensions"], 2)
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["coverage"], 1.0)

    def test_embed_reuses_duplicate_content_inside_batch(self):
        with patch.object(owner, "_request_embeddings", return_value=[[1.0, 0.0]]) as request:
            result = owner.embed(["same", "same"])
        self.assertEqual(request.call_args.args[0], ["same"])
        self.assertEqual(result["unique_count"], 1)
        self.assertEqual(result["in_batch_reused"], 1)
        self.assertEqual(result["embeddings"], [[1.0, 0.0], [1.0, 0.0]])

    def test_rerank_is_bounded_and_deterministic(self):
        with patch.object(owner, "_request_embeddings", return_value=[[1.0, 0.0], [0.8, 0.2], [0.0, 1.0]]):
            result = owner.rerank("query", ["near", "far"])
        self.assertEqual([row["index"] for row in result["results"]], [0, 1])
        self.assertGreater(result["results"][0]["score"], result["results"][1]["score"])

    def test_limits_input_without_network(self):
        result = owner.embed(["x"] * (owner.MAX_ITEMS + 1))
        self.assertFalse(result["ok"])
        self.assertEqual(result["vector_reason"], "input_count_exceeded")

    def test_hybrid_rerank_preserves_source_anchors_and_fuses_owner_ranks(self):
        vectors = [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]]
        candidates = [
            {"id": "dense", "text": "dense match", "source_ref": "owner:a", "content_sha256": "sha-a", "lexical_rank": 2},
            {"id": "lexical", "text": "lexical match", "source_ref": "owner:b", "content_sha256": "sha-b", "lexical_rank": 1},
        ]
        with patch.object(owner, "_request_embeddings", return_value=vectors):
            result = owner.hybrid_rerank("query", candidates, rank_constant=60)
        self.assertTrue(result["ok"])
        self.assertEqual(result["retrievers"], ["dense", "lexical"])
        self.assertEqual({row["id"] for row in result["results"]}, {"dense", "lexical"})
        self.assertTrue(all(row["source_ref"].startswith("owner:") for row in result["results"]))
        self.assertTrue(all(row["content_sha256"].startswith("sha-") for row in result["results"]))

    def test_hybrid_rerank_falls_back_to_caller_ranks_when_model_is_unavailable(self):
        candidates = [
            {"id": "second", "text": "second", "lexical_rank": 2},
            {"id": "first", "text": "first", "lexical_rank": 1},
        ]
        with patch.object(owner, "_request_embeddings", side_effect=OSError("offline")):
            result = owner.hybrid_rerank("query", candidates)
        self.assertFalse(result["ok"])
        self.assertTrue(result["fallback_used"])
        self.assertEqual([row["id"] for row in result["results"]], ["first", "second"])

    def test_select_keeps_close_scores_for_codex_judgment(self):
        ranked = {
            "ok": True,
            "results": [{"id": "a", "rrf_score": 0.1}, {"id": "b", "rrf_score": 0.0995}],
        }
        with patch.object(owner, "hybrid_rerank", return_value=ranked):
            result = owner.select("query", [{"id": "a", "text": "a"}, {"id": "b", "text": "b"}], margin=0.001)
        self.assertTrue(result["ambiguous"])
        self.assertIsNone(result["selected"])
        self.assertEqual(result["selection_reason"], "codex_judgment_required")

    def test_cli_status_is_machine_readable(self):
        with patch.object(owner, "status", return_value={"ok": True, "schema": owner.SCHEMA}):
            with patch("builtins.print") as output:
                self.assertEqual(owner.main(["status"]), 0)
        json.loads(output.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
