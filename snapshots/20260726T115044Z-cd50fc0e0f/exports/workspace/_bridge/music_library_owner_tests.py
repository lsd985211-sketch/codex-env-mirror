from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BRIDGE = Path(__file__).resolve().parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

import music_library_owner as owner  # noqa: E402
import music_library_planner as planner  # noqa: E402
import music_library_transaction as transaction  # noqa: E402


FINGERPRINT = "a" * 64


def hardware(fingerprint: str = FINGERPRINT, *, safe: bool = True) -> dict[str, object]:
    return {
        "stable_fingerprint": fingerprint,
        "drive_letter": "C",
        "safe_for_content_mutation": safe,
        "issues": [] if safe else [{"code": "storage_unhealthy"}],
    }


def write_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def metadata(path: Path) -> dict[str, str]:
    values = {
        "One.wav": {"title": "One", "artist": "Artist", "album_artist": "Artist", "album": "Album", "track": "1"},
        "Two.wav": {"title": "Two", "artist": "Artist", "album_artist": "Artist", "album": "Album", "track": "2"},
    }
    return values.get(path.name, {"title": path.stem, "artist": "Artist", "album_artist": "Artist"})


def make_plan(root: Path, names: tuple[str, ...] = ("One.wav",)) -> dict[str, object]:
    for index, name in enumerate(names, start=1):
        write_file(root / name, (f"audio-{index}-" * 20).encode("ascii"))
    return planner.build_plan(
        root,
        corrections={"schema": f"{planner.SCHEMA}.corrections", "files": {}, "album_years": {}},
        hardware_binding=hardware(),
        metadata_reader=metadata,
    )


class MusicLibraryOwnerTests(unittest.TestCase):
    def test_generated_plan_has_integrity_bound_id_and_unique_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            plan = make_plan(Path(temp), ("One.wav", "Two.wav"))

        self.assertEqual(planner.validate_plan_structure(plan), [])
        self.assertEqual(plan["plan_id"], planner.calculate_plan_id(plan))
        targets = [row["target"].casefold() for row in plan["entries"]]
        self.assertEqual(len(targets), len(set(targets)))

    def test_path_traversal_and_plan_tampering_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            plan = make_plan(Path(temp))
        plan["entries"][0]["target"] = "../outside.wav"

        codes = {item["code"] for item in planner.validate_plan_structure(plan)}

        self.assertIn("unsafe_relative_path", codes)
        self.assertIn("plan_id_integrity_failed", codes)

    def test_corrections_cannot_inject_target_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "corrections.json"
            path.write_text(
                json.dumps(
                    {
                        "schema": f"{planner.SCHEMA}.corrections",
                        "files": {"One.wav": {"target": "../escape.wav"}},
                        "album_years": {},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unsupported_correction_fields"):
                planner.load_corrections(path)

    def test_hash_change_blocks_apply_without_moving_any_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = make_plan(root, ("One.wav", "Two.wav"))
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            (root / "Two.wav").write_bytes(b"changed")

            result = transaction.apply_plan(
                plan,
                plan_path=plan_path,
                confirm_plan_id=str(plan["plan_id"]),
                fresh_hardware=hardware(),
                journal_path=root / "整理记录" / "journal.jsonl",
            )

            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "preflight_failed")
            self.assertTrue((root / "One.wav").is_file())

    def test_existing_target_blocks_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = make_plan(root)
            target = root / Path(plan["entries"][0]["target"])
            write_file(target, b"occupied")
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")

            result = transaction.apply_plan(
                plan,
                plan_path=plan_path,
                confirm_plan_id=str(plan["plan_id"]),
                fresh_hardware=hardware(),
                journal_path=root / "整理记录" / "journal.jsonl",
            )

            self.assertFalse(result["ok"])
            self.assertTrue(any(row["code"] == "target_already_exists" for row in result["issues"]))

    def test_interrupted_apply_resumes_and_full_hash_validation_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = make_plan(root, ("One.wav", "Two.wav"))
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            journal = root / "整理记录" / "journal.jsonl"
            real_rename = transaction.os.rename
            calls = 0

            def interrupt_second(source: Path, target: Path) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("simulated interruption")
                real_rename(source, target)

            with patch.object(transaction.os, "rename", side_effect=interrupt_second):
                with self.assertRaisesRegex(OSError, "simulated interruption"):
                    transaction.apply_plan(
                        plan,
                        plan_path=plan_path,
                        confirm_plan_id=str(plan["plan_id"]),
                        fresh_hardware=hardware(),
                        journal_path=journal,
                    )

            result = transaction.apply_plan(
                plan,
                plan_path=plan_path,
                confirm_plan_id=str(plan["plan_id"]),
                fresh_hardware=hardware(),
                journal_path=journal,
            )

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["resumed_count"], 1)
            self.assertTrue(result["validation"]["ok"])

    def test_apply_then_rollback_restores_original_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = make_plan(root, ("One.wav", "Two.wav"))
            original_hashes = {row["source"]: row["sha256"] for row in plan["entries"]}
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            journal = root / "整理记录" / "journal.jsonl"

            applied = transaction.apply_plan(
                plan,
                plan_path=plan_path,
                confirm_plan_id=str(plan["plan_id"]),
                fresh_hardware=hardware(),
                journal_path=journal,
            )
            rolled_back = transaction.rollback_plan(
                plan,
                confirm_plan_id=str(plan["plan_id"]),
                fresh_hardware=hardware(),
                journal_path=journal,
            )

            self.assertTrue(applied["ok"], applied)
            self.assertTrue(rolled_back["ok"], rolled_back)
            for relative, expected in original_hashes.items():
                self.assertEqual(hashlib.sha256((root / relative).read_bytes()).hexdigest(), expected)

    def test_hardware_fingerprint_drift_blocks_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = make_plan(root)
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")

            result = transaction.apply_plan(
                plan,
                plan_path=plan_path,
                confirm_plan_id=str(plan["plan_id"]),
                fresh_hardware=hardware("b" * 64),
                journal_path=root / "整理记录" / "journal.jsonl",
            )

            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "hardware_binding_failed")

    def test_lyrics_and_album_cover_follow_audio_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_file(root / "One.wav", b"audio")
            write_file(root / "One.lrc", b"lyrics")
            write_file(root / "Album.jpg", b"image")
            plan = planner.build_plan(
                root,
                corrections={"schema": f"{planner.SCHEMA}.corrections", "files": {}, "album_years": {}},
                hardware_binding=hardware(),
                metadata_reader=metadata,
            )

            by_kind = {row["kind"]: row for row in plan["entries"]}
            self.assertEqual(Path(by_kind["lyrics"]["target"]).parent, Path(by_kind["audio"]["target"]).parent)
            self.assertEqual(Path(by_kind["lyrics"]["target"]).stem, Path(by_kind["audio"]["target"]).stem)
            self.assertEqual(Path(by_kind["image"]["target"]).name, "cover.jpg")

    def test_duplicate_tag_parser_prefers_first_valid_unicode_value(self) -> None:
        raw = b'{"format":{"tags":{"title":"\xef\xbf\xbd\xef\xbf\xbd","title":"\xe5\xa4\xa9\xe4\xbd\xbf","artist":"Artist"}}}'

        tags = planner.parse_ffprobe_tags(raw)

        self.assertEqual(tags["title"], "天使")
        self.assertEqual(tags["artist"], "Artist")

    def test_owner_rejects_unhealthy_hardware_for_mutation(self) -> None:
        with self.assertRaisesRegex(ValueError, "hardware_not_safe_for_content_mutation"):
            owner.require_mutation_safe(hardware(safe=False))

    def test_correction_replan_reuses_fresh_inventory_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = make_plan(root, ("One.wav", "Two.wav"))
            rows = owner.reusable_inventory_rows(first, root)
            corrections = {
                "schema": f"{planner.SCHEMA}.corrections",
                "files": {"One.wav": {"title": "Renamed"}},
                "album_years": {},
            }

            second = planner.build_plan(
                root,
                corrections=corrections,
                hardware_binding=hardware(),
                inventory_rows=rows,
                hash_reader=lambda _: self.fail("replan must not rehash the inventory"),
            )

            renamed = next(row for row in second["entries"] if row["source"] == "One.wav")
            self.assertIn("Renamed", renamed["target"])
            self.assertNotEqual(first["plan_id"], second["plan_id"])

    def test_inventory_reuse_rejects_added_or_size_changed_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = make_plan(root)
            write_file(root / "New.wav", b"new")
            with self.assertRaisesRegex(ValueError, "inventory_snapshot_stale"):
                owner.reusable_inventory_rows(plan, root)

    def test_live_demo_and_piano_versions_stay_out_of_studio_album_folder(self) -> None:
        root = Path("C:/Music")
        years = {"Artist|Album": "2020"}
        for version in ("Live", "Demo", "钢琴版"):
            target, disposition = planner.audio_target(
                {
                    "title": "Song",
                    "artist": "Artist",
                    "album_artist": "Artist",
                    "album": "Album",
                    "year": "2020",
                    "track": "1",
                    "version": version,
                },
                root / f"Song-{version}.wav",
                root,
                years,
            )
            self.assertEqual(disposition, "active")
            self.assertIn("现场与特别版本", target.parts)
            self.assertNotIn("2020 - Album", target.parts)


if __name__ == "__main__":
    unittest.main()
