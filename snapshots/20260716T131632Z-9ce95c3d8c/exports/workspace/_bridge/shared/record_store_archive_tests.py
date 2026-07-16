#!/usr/bin/env python3
from __future__ import annotations

import gzip
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import record_store_maintenance as store


class RecordStoreArchiveTests(unittest.TestCase):
    def test_legacy_oversized_record_becomes_archive_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            records = root / "records"
            record = records / "legacy.json"
            records.mkdir(parents=True)
            record.write_bytes(b"x" * (store.LEGACY_OVERSIZED_ARCHIVE_BYTES + 1))
            old = time.time() - ((store.HOT_DAYS + 1) * 86400)
            os.utime(record, (old, old))
            definition = store.RecordRoot(
                key="test_records",
                area="test",
                kind="execution_record",
                path=records,
                owner="test",
                notes="test",
            )
            with (
                mock.patch.object(store, "RECORD_ROOTS", (definition,)),
                mock.patch.object(store, "ARCHIVE_ROOT", root / "archive"),
            ):
                candidates = store.cold_archive_candidates(store.now_utc())
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0]["candidate_reason"], "legacy_oversized")
            self.assertTrue(candidates[0]["archive_path"].endswith(".json.gz"))

    def test_gzip_archive_is_verified_and_reversible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.json"
            target = root / "archive" / "source.json.gz"
            original = (b'{"value":"' + (b"abc" * 10000) + b'"}')
            source.write_bytes(original)
            result = store.gzip_archive_file(source, target)
            self.assertTrue(source.exists())
            self.assertTrue(target.exists())
            with gzip.open(target, "rb") as handle:
                self.assertEqual(handle.read(), original)
            self.assertLess(result["archive_bytes"], result["source_bytes"])


if __name__ == "__main__":
    unittest.main()
