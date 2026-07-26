from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


BRIDGE = Path(__file__).resolve().parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

import dependency_change_intelligence_process as intelligence  # noqa: E402


class DependencyChangeIntelligenceTests(unittest.TestCase):
    def fingerprint(self, version: str = "1.0.0", captured_at: str = "first") -> dict:
        return {
            "schema": "codex_local_version_fingerprint.v1",
            "captured_at": captured_at,
            "host": {"identity": "test-host"},
            "components": {
                "desktop_package": {
                    "status": "supported",
                    "product_surface": "desktop",
                    "host_id": "windows_local",
                    "version": version,
                    "duration_ms": 10,
                }
            },
        }

    def test_fingerprint_excludes_volatile_fields(self) -> None:
        first = intelligence.normalize_fingerprint(self.fingerprint(captured_at="first"))
        second_payload = self.fingerprint(captured_at="second")
        second_payload["components"]["desktop_package"]["duration_ms"] = 999
        second = intelligence.normalize_fingerprint(second_payload)
        self.assertEqual(first["digest"], second["digest"])
        self.assertEqual(first["components"]["desktop_package"]["digest"], second["components"]["desktop_package"]["digest"])

    def test_ingest_creates_one_event_and_never_advances_validated_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            trigger = root / "trigger.json"
            trigger.write_text(json.dumps({"fingerprint": self.fingerprint()}), encoding="utf-8")

            first = intelligence.ingest_trigger("codex", trigger, root / "state")
            second = intelligence.ingest_trigger("codex", trigger, root / "state")

            self.assertTrue(first["event_created"])
            self.assertFalse(second["event_created"])
            self.assertEqual(first["event_id"], second["event_id"])
            self.assertFalse((root / "state" / "codex" / "last_validated.json").exists())
            self.assertFalse(first["validated_baseline_advanced"])

    def test_matching_validated_fingerprint_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            state = root / "state" / "codex"
            state.mkdir(parents=True)
            normalized = intelligence.normalize_fingerprint(self.fingerprint())
            intelligence.atomic_write_json(state / "last_validated.json", normalized)
            trigger = root / "trigger.json"
            trigger.write_text(json.dumps({"fingerprint": self.fingerprint(captured_at="later")}), encoding="utf-8")

            result = intelligence.ingest_trigger("codex", trigger, root / "state")

            self.assertEqual(result["status"], "unchanged")
            self.assertFalse(result["event_created"])

    def test_launcher_collector_digest_and_owner_event_id_remain_linked(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            payload = self.fingerprint("3.0.0")
            payload["digest"] = "collector-digest"
            expected_event_id, _ = intelligence.event_identity("codex", "", "collector-digest", "launcher")
            trigger = root / "trigger.json"
            trigger.write_text(
                json.dumps({"event_id": expected_event_id, "fingerprint": payload}),
                encoding="utf-8",
            )

            result = intelligence.ingest_trigger("codex", trigger, root / "state")

            self.assertEqual(result["event_id"], expected_event_id)
            self.assertEqual(result["current_digest"], "collector-digest")

    def test_profile_keeps_full_menu_and_shortcut_ultra_separate(self) -> None:
        profile, issues = intelligence.load_profile("codex")
        ids = {item["capability_id"] for item in profile["capability_contracts"]}
        self.assertFalse(issues)
        self.assertIn("model.ultra_full_menu", ids)
        self.assertIn("model.ultra_shortcut_slider", ids)
        self.assertIn("mcp.remote_host_authority", ids)
        encoded = json.dumps(profile, ensure_ascii=False)
        self.assertNotIn('"expected_mcp_count"', encoded)
        self.assertNotIn('"reasoning_effort_count"', encoded)

    def test_proposal_is_chinese_first_and_requires_explicit_approval(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            trigger = root / "trigger.json"
            trigger.write_text(json.dumps({"fingerprint": self.fingerprint("2.0.0")}), encoding="utf-8")
            ingest = intelligence.ingest_trigger("codex", trigger, root / "state")

            result = intelligence.propose_event(ingest["event_id"], root / "state")
            proposal = result["proposal"]

            self.assertTrue(result["ok"])
            self.assertRegex(proposal["title"], r"[\u4e00-\u9fff]")
            self.assertRegex(proposal["summary"], r"[\u4e00-\u9fff]")
            self.assertTrue(proposal["approval"]["required"])
            self.assertFalse(proposal["approval"]["auto_apply"])
            self.assertIn("proposal_id_and_input_signature", proposal["approval"]["scope"])
            self.assertNotEqual(proposal["validated_baseline_advance"], "automatic")
            current = intelligence.status(root / "state", pending_only=True)
            self.assertEqual(current["proposal_count"], 1)

    def test_repeated_probe_failure_maps_to_one_incident_family(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            event = {"event_id": "one", "profile_id": "codex"}
            probe = {"capability_id": "protocol.thread_branching", "status": "degraded"}
            first = intelligence._incident_handoff(event, probe, root)
            second = intelligence._incident_handoff({**event, "event_id": "two"}, probe, root)
            self.assertEqual(first["incident_family_id"], second["incident_family_id"])

    def test_periodic_scan_generates_proposal_and_persists_final_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            state_root = root / "state"
            scheduler_receipt = {
                "ok": True,
                "status": "completed",
                "accepted_count": 1,
                "unmet_required_count": 0,
                "manifest_path": str(root / "batch-manifest.json"),
                "results": [
                    {
                        "item_id": "official-docs",
                        "manifest_path": str(root / "source-manifest.json"),
                        "acceptance": {"accepted": True, "source_id": "openai_docs"},
                    }
                ],
            }

            def create_proposal(event_id: str, state: Path) -> dict:
                proposal = {
                    "proposal_id": "proposal-one",
                    "event_id": event_id,
                    "status": "proposed",
                    "approval": {"required": True, "auto_apply": False},
                }
                intelligence.atomic_write_json(state / "codex" / "proposals" / "proposal-one.json", proposal)
                return {"ok": True, "status": "created", "proposal": proposal}

            with (
                mock.patch.object(
                    intelligence.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0, json.dumps(scheduler_receipt), ""),
                ),
                mock.patch.object(
                    intelligence,
                    "probe_event",
                    return_value={"ok": False, "status": "degraded", "event_id": "event"},
                ) as probe,
                mock.patch.object(intelligence, "propose_event", side_effect=create_proposal) as propose,
            ):
                result = intelligence.scan_profile(
                    "codex",
                    "periodic",
                    state_root,
                    plan_only=False,
                    timeout_seconds=30,
                )

            self.assertTrue(result["event_created"])
            self.assertEqual(1, result["periodic_review"]["proposal_count"])
            self.assertFalse(result["periodic_review"]["auto_apply"])
            self.assertFalse(result["validated_baseline_advanced"])
            self.assertFalse((state_root / "codex" / "last_validated.json").exists())
            probe.assert_called_once()
            propose.assert_called_once()
            receipt = intelligence.read_json_object(
                state_root / "codex" / "scans" / result["scan_id"] / "scan-receipt.json"
            )
            self.assertEqual(result["event_id"], receipt["event_id"])
            self.assertEqual("proposal-one", receipt["periodic_review"]["results"][0]["proposal_id"])
            repeated = intelligence.run_periodic_review("codex", state_root)
            self.assertEqual(0, repeated["reviewed_count"])
            probe.assert_called_once()
            propose.assert_called_once()

    def test_default_state_routes_incident_feedback_through_existing_reporter(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            event = {"event_id": "one", "profile_id": "codex"}
            probe = {"capability_id": "desktop.capability_discovery", "status": "degraded"}
            with (
                mock.patch.object(intelligence, "DEFAULT_STATE_ROOT", root),
                mock.patch(
                    "shared.codex_reporter.enqueue_report",
                    return_value={"ok": True, "queued": True, "request_id": "report-one"},
                ) as enqueue,
            ):
                result = intelligence._incident_handoff(event, probe, root)
            enqueue.assert_called_once()
            self.assertTrue(result["reporter_receipt"]["queued"])

    def test_validate_has_no_profile_or_schema_issues(self) -> None:
        result = intelligence.validate()
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["validated_baseline_auto_advance"])


if __name__ == "__main__":
    unittest.main()
