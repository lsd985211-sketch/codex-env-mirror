#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from execution_route_pack import build_execution_route_pack
from shared.resource_event_store import ensure_schema, rebuild_from_manifests, strategy_entries
from workflow_orchestrator import build_plan


class ResourceEventStoreTests(unittest.TestCase):
    def test_manifest_rebuild_populates_request_and_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = root / "resources" / "_requests" / "res_test" / "manifest.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(
                json.dumps(
                    {
                        "request_id": "res_test",
                        "request": {"intent": "documentation_lookup", "metadata": {"resource_kind": "document"}},
                        "receipt": {
                            "ok": False,
                            "status": "handoff_required",
                            "route": {"intent": "documentation_lookup", "primary_tool": "context7"},
                            "attempts": [{"tool": "context7"}],
                            "error_class": "owner_tool_required",
                            "next_action": "attach_owner_result",
                            "satisfaction": {"satisfied": False, "reason": "owner_tool_required"},
                            "progress_events": [{"time": "2026-07-12T00:00:00Z"}],
                        },
                        "events": [
                            {
                                "schema": "resource_broker.event.v1",
                                "request_id": "res_test",
                                "time": "2026-07-12T00:00:00Z",
                                "stage": "reported",
                                "status": "handoff_required",
                                "message": "receipt produced",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            db_path = root / "record_store.sqlite"
            conn = sqlite3.connect(db_path)
            try:
                ensure_schema(conn)
                counts = rebuild_from_manifests(conn, store_root=root / "resources")
                conn.commit()
            finally:
                conn.close()
            entries = strategy_entries(limit=10, db_path=db_path)
            self.assertEqual(counts, {"requests": 1, "events": 1})
            self.assertEqual(entries[0]["intent"], "documentation_lookup")
            self.assertEqual(entries[0]["decision"], "handoff_required")

    def test_structured_route_prevents_resource_subsystem_governance_false_positive(self) -> None:
        plan = build_plan(
            "统一资源事件与路由决策，修复资源层状态口径，执行本地代码治理",
            detail="full",
        )
        pack = build_execution_route_pack(plan)
        self.assertEqual(pack["resource_gate"]["decision_source"], "structured_route_contract")
        self.assertFalse(pack["resource_gate"]["enabled"])
        self.assertEqual(pack["route_decision"]["task_contract_source"], "structured_route_contract")


if __name__ == "__main__":
    unittest.main()
