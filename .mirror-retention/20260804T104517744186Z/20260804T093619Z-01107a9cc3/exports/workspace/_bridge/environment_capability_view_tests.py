#!/usr/bin/env python3
from __future__ import annotations

import unittest
import json

from environment_capability_view import build_capability_view, validate
from skill_orchestrator import capability_matches


class EnvironmentCapabilityViewTests(unittest.TestCase):
    def test_validate_contract(self) -> None:
        result = validate()
        self.assertTrue(result["ok"], result)

    def test_ocr_description_match_is_visible_without_execution_admission(self) -> None:
        result = build_capability_view(
            "识别本机 OCR 能力并选择扫描 PDF 降级路径",
            skills=[],
            owners=[{"name": "pdf_owner", "owner": "pdf_owner", "validator": "pdf.validate"}],
            tools=[],
            primary_owner="pdf_owner",
        )
        ocr = next((row for row in result["candidates"] if row["capability_id"] == "skill:ppocrv5"), None)
        self.assertIsNotNone(ocr, result)
        self.assertFalse(ocr["admitted"])
        self.assertFalse(ocr["callable"])
        self.assertIn("unregistered", ocr["risk_hints"])

    def test_primary_owner_precedes_supporting_callable_tool(self) -> None:
        result = build_capability_view(
            "处理扫描 PDF",
            skills=[],
            owners=[{"name": "pdf_owner", "owner": "pdf_owner", "validator": "pdf.validate"}],
            tools=[{"name": "gui-ocr", "owner": "gui_owner", "current_turn_callable": True, "healthy": True}],
            primary_owner="pdf_owner",
        )
        self.assertEqual(result["primary_owner"], "pdf_owner")
        self.assertIn(result["decision_mode"], {"codex_select", "auto_fast_path", "auto_fallback"})

    def test_candidates_expose_reference_only_session_fields(self) -> None:
        result = build_capability_view(
            "处理扫描 PDF",
            owners=[{"name": "pdf_owner", "owner": "pdf_owner", "entry_point": "owner:pdf", "validator": "pdf.validate"}],
            primary_owner="pdf_owner",
        )
        candidate = next(row for row in result["candidates"] if row["capability_id"] == "owner:pdf_owner")
        self.assertEqual(candidate["candidate_id"], candidate["capability_id"])
        self.assertTrue(candidate["authority_ref"])
        self.assertTrue(candidate["source_ref"])
        self.assertTrue(candidate["entry_ref"])
        self.assertIn(candidate["availability"], {"ready", "unknown", "unavailable"})
        self.assertTrue(candidate["content_sha256"])
        self.assertIsNone(candidate["admitted"])
        self.assertEqual(candidate["permission_state"], "unknown")

    def test_stale_index_is_preserved_as_issue(self) -> None:
        result = build_capability_view(
            "查询 OCR 维护能力",
            skills=[{"name": "ppocrv5", "path": "/skills/ppocrv5/SKILL.md", "admission_state": "unregistered"}],
            environment_context={"index_status": "index_stale"},
        )
        self.assertEqual(result["index_status"], "index_stale")
        self.assertTrue(result["candidates"])

    def test_stale_owner_index_cannot_promote_candidate_to_active(self) -> None:
        result = build_capability_view(
            "重启已有运行时",
            owners=[{
                "capability_id": "owner:runtime",
                "name": "runtime",
                "owner": "runtime_owner",
                "admitted": True,
                "callable": True,
                "healthy": True,
                "contract_signature": "a" * 64,
            }],
            environment_context={"index_status": "index_stale"},
            primary_owner="runtime_owner",
        )
        candidate = next(row for row in result["candidates"] if row["capability_id"] == "owner:runtime")
        self.assertEqual(candidate["state"], "unknown")
        self.assertIsNone(candidate["callable"])
        self.assertIn("stale_source_evidence", candidate["risk_hints"])
        self.assertNotEqual(result["decision_mode"], "auto_fast_path")

    def test_duplicate_owner_or_contract_drift_fails_closed(self) -> None:
        result = build_capability_view(
            "调用 OCR owner",
            owners=[
                {
                    "capability_id": "owner:ocr",
                    "name": "ocr",
                    "owner": "ocr_owner",
                    "owner_ref": "_bridge/ocr_owner.py",
                    "contract_signature": "a" * 64,
                    "admitted": True,
                    "callable": True,
                    "healthy": True,
                },
                {
                    "capability_id": "owner:ocr",
                    "name": "ocr",
                    "owner": "ocr_owner",
                    "owner_ref": "_bridge/other_ocr_owner.py",
                    "contract_signature": "b" * 64,
                    "admitted": True,
                    "callable": True,
                    "healthy": True,
                },
            ],
            primary_owner="ocr_owner",
        )
        candidate = next(row for row in result["candidates"] if row["capability_id"] == "owner:ocr")
        self.assertFalse(candidate["admitted"])
        self.assertFalse(candidate["callable"])
        self.assertEqual(candidate["admission_state"], "conflicted")
        self.assertIn("contract_signature_conflict", candidate["risk_hints"])
        self.assertIn("duplicate_owner_conflict", candidate["risk_hints"])

    def test_projection_preserves_contract_refs_and_drops_sensitive_fields(self) -> None:
        result = build_capability_view(
            "查询 OCR 能力",
            owners=[{
                "capability_id": "owner:ocr",
                "name": "ocr",
                "owner": "ocr_owner",
                "owner_ref": "_bridge/ocr_owner.py",
                "contract_ref": "registry:ocr:abc",
                "contract_signature": "c" * 64,
                "risk_class": "read_only",
                "missing_requirements": ["runtime_probe"],
                "authorization_token": "must-not-project",
                "command_body": "secret command",
            }],
        )
        candidate = next(row for row in result["candidates"] if row["capability_id"] == "owner:ocr")
        self.assertEqual(candidate["contract_ref"], "registry:ocr:abc")
        self.assertEqual(candidate["contract_signature"], "c" * 64)
        self.assertEqual(candidate["risk_class"], "read_only")
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("must-not-project", serialized)
        self.assertNotIn("secret command", serialized)

    def test_bge_candidate_is_visible_but_requires_owner_probe(self) -> None:
        result = build_capability_view(
            "用 BGE-M3 做语义检索",
            environment_context={
                "semantic_capabilities": [{
                    "capability_id": "model:bge-m3",
                    "name": "BGE-M3",
                    "capability_class": "semantic_model",
                    "source_authority": "semantic_capability_owner",
                    "owner": "semantic_capability_owner",
                    "entry_point": "medical_textbook_mcp.py vector-status; medical_search",
                    "callable": None,
                    "healthy": None,
                    "admitted": True,
                    "fallback_route": "source-bound FTS + graph",
                    "business_scope": "textbook knowledge owner",
                    "usage_policy": "retain citations",
                    "business_results": {"semantic_modes": ["concept_normalization"]},
                }]
            },
        )
        bge = next(row for row in result["candidates"] if row["capability_id"] == "model:bge-m3")
        self.assertEqual(bge["state"], "unknown")
        self.assertIsNone(bge["callable"])
        self.assertEqual(bge["business_scope"], "textbook knowledge owner")
        self.assertEqual(bge["business_results"]["semantic_modes"], ["concept_normalization"])

    def test_shared_semantic_owner_is_not_textbook_only(self) -> None:
        result = build_capability_view(
            "查找工作环境中的相似能力",
            environment_context={"semantic_capabilities": [{
                "capability_id": "model:bge-m3",
                "name": "BGE-M3",
                "capability_class": "semantic_model",
                "source_authority": "semantic_capability_owner",
                "owner": "semantic_capability_owner",
                "entry_point": "python _bridge/semantic_capability_owner.py status --probe",
                "platform_scope": ["workspace_semantic_retrieval", "textbook_runtime"],
                "admitted": True,
                "business_scope": "通用语义能力优先",
            }]},
        )
        bge = next(row for row in result["candidates"] if row["capability_id"] == "model:bge-m3")
        self.assertEqual(bge["owner"], "semantic_capability_owner")
        self.assertIn("workspace_semantic_retrieval", bge["platform_scope"])

    def test_capability_matches_is_bounded_and_does_not_mutate_inventory(self) -> None:
        inventory = {
            "ppocrv5": {
                "name": "ppocrv5",
                "description": "Route OCR tasks across local capabilities",
                "path": "/skills/ppocrv5/SKILL.md",
                "source": "test",
            },
            "pdf": {
                "name": "pdf",
                "description": "Read and inspect PDF documents",
                "path": "/skills/pdf/SKILL.md",
                "source": "test",
            },
        }
        result = capability_matches("识别 OCR 能力", inventory=inventory, limit=1)
        self.assertEqual([row["name"] for row in result], ["ppocrv5"])
        self.assertEqual(len(inventory), 2)


if __name__ == "__main__":
    unittest.main()
