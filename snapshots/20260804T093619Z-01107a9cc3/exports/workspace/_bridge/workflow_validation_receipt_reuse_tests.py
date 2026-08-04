#!/usr/bin/env python3

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from workflow_validation_receipt_reuse import (
    ACCEPTANCE_PREDICATE,
    attach_reuse_projection,
    context_signature,
    load_receipt,
    store_receipt,
)


def passing_result() -> dict:
    return {"schema": "workflow_orchestrator.validate.v1", "ok": True, "checks": [{"name": "one", "ok": True}]}


class ValidationReceiptReuseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 31, 11, 0, tzinfo=timezone.utc)
        self.signature = context_signature(
            workflow_source_signature="workflow-v1",
            skill_context_signature="skills-v1",
            registry_source_signature="registry-v1",
        )

    def test_first_passing_run_stores_and_exact_context_reuses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stored = store_receipt(passing_result(), self.signature, state_root=root, now=self.now)
            self.assertTrue(stored["stored"])
            reused = load_receipt(self.signature, state_root=root, now=self.now + timedelta(seconds=1))
            self.assertTrue(reused["ok"], reused)
            self.assertEqual("receipt_reused_passed", reused["status"])
            self.assertEqual("one", reused["payload"]["checks"][0]["name"])

    def test_any_context_change_or_expiry_forces_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store_receipt(passing_result(), self.signature, state_root=root, ttl_seconds=60, now=self.now)
            changed = context_signature(
                workflow_source_signature="workflow-v2",
                skill_context_signature="skills-v1",
                registry_source_signature="registry-v1",
            )
            self.assertEqual("validation_receipt_signature_mismatch", load_receipt(changed, state_root=root, now=self.now)["reason"])
            self.assertEqual("validation_receipt_expired", load_receipt(self.signature, state_root=root, now=self.now + timedelta(seconds=61))["reason"])

    def test_failed_or_partial_result_never_writes_reusable_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            failed = store_receipt({"ok": False, "checks": [{"name": "one", "ok": False}]}, self.signature, state_root=root, now=self.now)
            partial = store_receipt({"ok": True, "checks": [{"name": "one", "ok": True}, {"name": "two", "ok": False}]}, self.signature, state_root=root, now=self.now)
            self.assertFalse(failed["stored"])
            self.assertFalse(partial["stored"])
            self.assertEqual("validation_receipt_missing", load_receipt(self.signature, state_root=root, now=self.now)["reason"])

    def test_projection_distinguishes_execution_from_reuse(self) -> None:
        executed = attach_reuse_projection(passing_result(), reused=False)
        failed = attach_reuse_projection({"ok": False, "checks": []}, reused=False)
        reused = attach_reuse_projection(passing_result(), reused=True, receipt_ref="receipt:one")
        self.assertFalse(executed["validation_receipt_reuse"])
        self.assertEqual("executed_passed", executed["validation_receipt_reuse_status"])
        self.assertEqual("executed_failed", failed["validation_receipt_reuse_status"])
        self.assertTrue(reused["validation_receipt_reuse"])
        self.assertEqual("receipt_reused_passed", reused["validation_receipt_reuse_status"])
        self.assertEqual("receipt:one", reused["validation_receipt_ref"])
        self.assertEqual(ACCEPTANCE_PREDICATE, reused.get("validation_receipt_acceptance", ACCEPTANCE_PREDICATE))


if __name__ == "__main__":
    unittest.main()
