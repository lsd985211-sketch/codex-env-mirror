from __future__ import annotations

import datetime as dt
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


class ResourceIntelligenceRegressionTests(unittest.TestCase):
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
