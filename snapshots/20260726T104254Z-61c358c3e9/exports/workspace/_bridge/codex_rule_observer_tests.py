from __future__ import annotations

import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from codex_rule_observer import (
    admission_output,
    build_tool_event,
    closeout_facts,
    read_events,
    stop_output,
    write_event,
)
from codex_workflow_entry import closeout
from resource_broker import ResourceAttempt, ResourceBrokerRequest, receipt_from_attempts, route_for_request


class _Satisfied:
    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "resource_satisfaction.v1",
            "satisfied": True,
            "result_kind": "owner_result",
            "reason": "completion_predicate_satisfied",
            "next_action": "consume_resource",
            "relevance": {"ok": True},
            "sufficiency": {"ok": True},
        }


class CodexRuleObserverTests(unittest.TestCase):
    def test_implicit_external_knowledge_is_a_nonblocking_hint(self) -> None:
        output = admission_output(
            {"session_id": "test-session", "turn_id": "turn-1", "prompt": "推荐当前可用的 USB 诊断工具"}
        )
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("external_knowledge_candidate", context)
        self.assertIn("does not authorize, deny, or execute", context)

    def test_post_tool_event_does_not_store_full_payload(self) -> None:
        event = build_tool_event(
            {
                "session_id": "s",
                "turn_id": "t",
                "tool_use_id": "call",
                "tool_name": "web.run",
                "tool_input": {"query": "private prompt", "path": "C:/stable/file.md"},
                "tool_response": {"ok": True, "content": "large private output", "request_id": "res_0123456789abcdef"},
            }
        )
        serialized = json.dumps(event, ensure_ascii=False)
        self.assertNotIn("private prompt", serialized)
        self.assertNotIn("large private output", serialized)
        self.assertIn("res_0123456789abcdef", serialized)

    def test_corrupt_or_missing_journal_is_nonblocking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad = root / "session" / "turn"
            bad.mkdir(parents=True)
            (bad / "bad.json").write_text("{not json", encoding="utf-8")
            self.assertEqual(read_events(session_id="session", runtime_root=root), [])
            self.assertEqual(closeout_facts(session_id="missing", runtime_root=root)["event_count"], 0)

    def test_parallel_events_are_isolated_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = [
                build_tool_event(
                    {
                        "session_id": "session",
                        "turn_id": "turn",
                        "tool_use_id": f"call-{index}",
                        "tool_name": "functions.shell_command",
                        "tool_input": {"command": "python test.py validate"},
                        "tool_response": {"ok": True},
                    }
                )
                for index in range(24)
            ]
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda item: write_event(item, root), events))
            self.assertEqual(len(read_events(session_id="session", runtime_root=root)), 24)

    def test_session_isolation_prevents_old_receipt_leakage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_event(
                build_tool_event(
                    {
                        "session_id": "old",
                        "turn_id": "1",
                        "tool_use_id": "call-old",
                        "tool_name": "web.run",
                        "tool_input": {},
                        "tool_response": {"request_id": "res_aaaaaaaaaaaaaaaa", "status": "completed"},
                    }
                ),
                root,
            )
            self.assertEqual(closeout_facts(session_id="new", runtime_root=root)["resource_request_id"], "")

    def test_stop_reports_but_never_blocks(self) -> None:
        with patch("codex_rule_observer.closeout_facts") as facts:
            facts.return_value = {"violations": [{"code": "local_write_without_observed_validation"}]}
            output = stop_output({"session_id": "s"})
        self.assertIn("systemMessage", output)
        self.assertNotIn("continue", output)
        self.assertNotIn("decision", output)

    def test_closeout_consumes_observed_tool_facts(self) -> None:
        observed = {
            "schema": "codex_rule_observer.closeout_facts.v1",
            "ok": True,
            "event_count": 2,
            "web_search_used": True,
            "resource_layer_used": True,
            "owner_mcp_used": ["mcp__local_mcp_hub__resource_request"],
            "resource_request_id": "res_0123456789abcdef",
            "resource_status": "completed",
            "violations": [],
            "blocking": False,
        }
        with patch("codex_workflow_entry.observed_closeout_facts", return_value=observed):
            package = closeout(outcome="ok")
        external = package["tool_evidence"]["external_research"]
        self.assertTrue(external["web_search_used"])
        self.assertEqual(external["resource_request_id"], "res_0123456789abcdef")
        self.assertEqual(package["tool_evidence"]["observer"], observed)

    def test_satisfied_owner_result_supersedes_earlier_handoff(self) -> None:
        request = ResourceBrokerRequest(task="official docs lookup", target="Codex hooks")
        route = route_for_request(request)
        successful = ResourceAttempt(
            index=1,
            tool="context7",
            stage="discover",
            status="completed",
            executable=True,
            started_at="2026-07-17T00:00:00+00:00",
            finished_at="2026-07-17T00:00:01+00:00",
            result={"ok": True, "result_kind": "owner_result", "content": "Useful official result"},
        )
        handoff = ResourceAttempt(
            index=2,
            tool="openai-docs",
            stage="discover",
            status="handoff_required",
            executable=False,
            started_at="2026-07-17T00:00:01+00:00",
            finished_at="2026-07-17T00:00:01+00:00",
            error_class="handoff_required_for_owner_tool",
            next_action="call owner tool",
        )
        with patch("resource_broker.resource_result_satisfaction", return_value=_Satisfied()):
            receipt = receipt_from_attempts("res_0123456789abcdef", request, route, [successful, handoff], [], {})
        self.assertTrue(receipt.ok)
        self.assertEqual(receipt.status, "completed")
        self.assertEqual(receipt.result_kind, "owner_result")


if __name__ == "__main__":
    unittest.main()
