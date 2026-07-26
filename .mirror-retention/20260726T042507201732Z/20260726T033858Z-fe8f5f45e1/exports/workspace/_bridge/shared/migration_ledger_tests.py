#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import migration_ledger


class MigrationLedgerTests(unittest.TestCase):
    def test_terminal_event_hashes_satisfy_evidence_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "ledger.sqlite"
            created = migration_ledger.create_operation(
                domain="test",
                owner="test-owner",
                source_path="source",
                target_path="target",
                reason="test migration",
                source_sha256="source-hash",
                rollback_action="restore target to source",
                db_path=db_path,
            )
            migration_ledger.append_event(
                created["migration_id"],
                "verified",
                source_sha256="source-hash",
                target_sha256="target-hash",
                db_path=db_path,
            )

            result = migration_ledger.validate(db_path=db_path)

            self.assertTrue(result["ok"])
            snap = migration_ledger.snapshot(db_path=db_path)
            self.assertEqual(snap["operations"][0]["effective_target_sha256"], "target-hash")

    def test_verified_migration_without_target_hash_fails_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "ledger.sqlite"
            created = migration_ledger.create_operation(
                domain="test",
                owner="test-owner",
                source_path="source",
                target_path="target",
                reason="missing evidence",
                source_sha256="source-hash",
                rollback_action="restore target to source",
                db_path=db_path,
            )
            migration_ledger.append_event(created["migration_id"], "verified", db_path=db_path)

            result = migration_ledger.validate(db_path=db_path)

            self.assertFalse(result["ok"])
            evidence = next(
                item for item in result["checks"] if item["name"] == "terminal_migrations_have_verifiable_evidence"
            )
            self.assertIn("target_sha256", evidence["detail"][0]["missing"])


if __name__ == "__main__":
    unittest.main()
