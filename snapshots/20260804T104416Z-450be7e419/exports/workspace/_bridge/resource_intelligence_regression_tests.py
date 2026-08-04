from __future__ import annotations

import datetime as dt
import tempfile
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest import mock


BRIDGE = Path(__file__).resolve().parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

import network_doctor  # noqa: E402
import resource_broker  # noqa: E402
import resource_owner_executor  # noqa: E402
import resource_scheduler  # noqa: E402
import resource_source_strategy  # noqa: E402


class ResourceIntelligenceRegressionTests(unittest.TestCase):
    def test_academic_documentation_lookup_bypasses_context7(self) -> None:
        plan = resource_source_strategy.candidate_source_plan(
            {
                "task": "检索 retrieval practice 和 spacing 的医学教育论文证据",
                "target": "adaptive medical education journal paper DOI",
                "intent": "documentation_lookup",
            },
            {"primary_tool": "context7", "intent": "documentation_lookup"},
        )
        self.assertEqual(plan["resource_kind"], "academic_paper")
        self.assertEqual(plan["execution_capability"]["registered_owner_adapter"], "academic_search")
        self.assertEqual([item["owner_tool"] for item in plan["candidates"][:2]], ["academic_search", "academic_search"])

    def test_academic_owner_preserves_doi_and_landing_urls(self) -> None:
        payloads = [
            {
                "results": [
                    {
                        "id": "https://openalex.org/W1",
                        "doi": "https://doi.org/10.48550/arxiv.1506.05908",
                        "title": "Deep Knowledge Tracing",
                        "publication_year": 2015,
                        "type": "article",
                        "primary_location": {"landing_page_url": "https://arxiv.org/abs/1506.05908", "pdf_url": "https://arxiv.org/pdf/1506.05908"},
                        "open_access": {"oa_url": "https://arxiv.org/pdf/1506.05908"},
                    }
                ]
            },
            {
                "message": {
                    "items": [
                        {
                            "DOI": "10.1177/1529100612453266",
                            "title": ["Strengthening the Student Toolbox"],
                            "URL": "https://doi.org/10.1177/1529100612453266",
                            "published": {"date-parts": [[2013, 1, 1]]},
                        }
                    ]
                }
            },
        ]
        with mock.patch.object(resource_owner_executor, "_open_json_url", side_effect=payloads):
            result = resource_owner_executor.execute_academic_search(
                {"task": "Deep Knowledge Tracing paper", "metadata": {"max_results": 5}},
                {"ok": True, "plan": {"route_mode": "probe_selected_direct", "target_kind": "paper"}},
                10,
            )
        self.assertTrue(result["ok"], result)
        by_source = {item["source"]: item for item in result["candidates"]}
        self.assertEqual(by_source["openalex"]["doi"], "10.48550/arxiv.1506.05908")
        self.assertEqual(by_source["openalex"]["open_access_url"], "https://arxiv.org/pdf/1506.05908")
        self.assertEqual(by_source["crossref"]["doi"], "10.1177/1529100612453266")
        self.assertEqual(by_source["crossref"]["landing_url"], "https://doi.org/10.1177/1529100612453266")

    def test_generic_owner_preserves_backend_unavailable(self) -> None:
        with mock.patch.object(
            resource_owner_executor,
            "call_hub_tool",
            return_value={"ok": False, "status": "backend_unavailable", "error_class": "search_backends_unavailable", "reason": "backend offline"},
        ):
            result = resource_owner_executor.execute_generic_search(
                {"task": "adaptive learning evidence", "metadata": {}},
                {"ok": True, "plan": {"route_mode": "probe_selected_direct"}},
                5,
            )
        self.assertEqual(result["status"], "backend_unavailable")
        self.assertEqual(result["error_class"], "search_backends_unavailable")

    def test_academic_scheduler_limits_concurrency_and_opens_failure_circuit(self) -> None:
        requests = [
            resource_broker.ResourceBrokerRequest(
                task="检索 adaptive teaching 的医学教育论文", target=f"academic paper {index}",
                intent="documentation_lookup", auto_owner=True,
            )
            for index in range(4)
        ]
        calls = {"count": 0}

        def unavailable(_request, **_kwargs):
            calls["count"] += 1
            return SimpleNamespace(
                ok=False, status="backend_unavailable", request_id="res_unavailable", result_kind="academic_paper_metadata_search",
                manifest_path="", artifact_path="", next_action="refresh_network_route_and_retry",
                error_class="academic_backends_unavailable", network_summary={}, satisfaction={"satisfied": False},
                attempts=[{"tool": "academic_search", "result": {"ok": False, "source": "academic_search"}}], execution_budget={},
            )

        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.object(
            resource_scheduler, "precompute_network_plans", side_effect=lambda scheduled, **_kwargs: (scheduled, {"ok": True, "skipped": True})
        ), mock.patch.object(resource_scheduler, "handle_request", side_effect=unavailable):
            batch = resource_scheduler.execute_batch(
                requests,
                config=resource_scheduler.ResourceBatchConfig(max_active=4, per_host_limit=4),
                event_log=Path(temp_dir) / "events.jsonl",
                receipt_log=Path(temp_dir) / "receipts.jsonl",
                store_root=Path(temp_dir) / "store",
            )
        self.assertEqual([item["queue_class"] for item in batch["planned"]], ["academic_metadata"] * 4)
        self.assertEqual([item["host_key"] for item in batch["planned"]], ["academic:metadata"] * 4)
        self.assertEqual(calls["count"], 1)
        self.assertEqual(sum(item.get("error_class") == "academic_owner_circuit_open" for item in batch["results"]), 3)

    def test_github_query_limit_uses_encoded_bytes(self) -> None:
        query = "远程设置" * 80
        self.assertGreater(resource_owner_executor._encoded_github_query_bytes(query), 512)
        with self.assertRaises(ValueError):
            resource_owner_executor._bounded_github_query(query, max_encoded_bytes=512)

    def test_github_hub_payload_obeys_response_byte_budget(self) -> None:
        with mock.patch.object(
            resource_owner_executor,
            "call_hub_tool",
            return_value={"ok": True, "result": {"items": [{"body": "x" * 20_000}]}},
        ):
            result = resource_owner_executor._github_api_read(
                "/search/issues",
                {"q": "repo:openai/codex reasoning"},
                {},
                {},
                5,
                max_response_bytes=4_096,
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_class"], "github_response_budget_exceeded")

    def test_network_probe_selects_native_curl_for_each_platform(self) -> None:
        with mock.patch.object(network_doctor.shutil, "which", side_effect=lambda name: f"/resolved/{name}"):
            self.assertEqual(network_doctor.curl_probe_command("nt"), ("/resolved/curl.exe", "NUL"))
            self.assertEqual(network_doctor.curl_probe_command("posix"), ("/resolved/curl", "/dev/null"))

    def test_terminal_generic_search_failure_keeps_independent_github_lane(self) -> None:
        attempt = resource_broker.ResourceAttempt(
            index=1,
            tool="generic_search",
            stage="search",
            status="failed",
            executable=True,
            started_at="",
            finished_at="",
            result={"ok": False},
            error_class="FileNotFoundError",
            reason="curl executable absent",
            next_action="surface_failure",
        )
        self.assertTrue(
            resource_broker.independent_owner_lane_remaining(
                ["generic_search", "github"], 0, "generic_search", attempt
            )
        )

    def test_scheduler_requires_allowed_source_freshness_and_deliverables(self) -> None:
        request = resource_broker.ResourceBrokerRequest(
            target="https://github.com/openai/codex",
            metadata={
                resource_scheduler.BATCH_ITEM_METADATA_KEY: {
                    "item_id": "official",
                    "required": True,
                    "acceptance": {
                        "allowed_sources": ["github"],
                        "maximum_age_hours": 24,
                        "required_deliverables": ["releases"],
                    },
                    "source": {},
                    "freshness": {},
                }
            },
        )
        finished = dt.datetime.now(dt.UTC).isoformat()
        receipt = SimpleNamespace(
            ok=True,
            status="completed",
            error_class="",
            result_kind="github_release_read",
            satisfaction={"satisfied": True},
            attempts=[
                {
                    "tool": "github",
                    "finished_at": finished,
                    "result": {
                        "source": "github",
                        "metadata": {
                            "completed_deliverables": ["releases"],
                            "html_url": "https://github.com/openai/codex/releases",
                        },
                    },
                }
            ],
        )
        accepted = resource_scheduler.evaluate_item_acceptance(request, receipt, 1)
        self.assertTrue(accepted["accepted"], accepted)
        self.assertEqual(accepted["source_id"], "github")
        self.assertFalse(accepted["missing_deliverables"])


if __name__ == "__main__":
    unittest.main()
