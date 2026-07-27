#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BRIDGE = Path(__file__).resolve().parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))
import state_write_authority as authority  # noqa: E402


class StateWriteAuthorityTests(unittest.TestCase):
    def contract(self, root: Path) -> Path:
        path = root / "authorities.json"
        path.write_text(json.dumps({"schema": "state_write_authorities.v1", "domains": [{"domain_id": "codex_config", "targets": ["config"], "allowed_writers": ["state_write_authority", "codex_environment_mirror"]}]}), encoding="utf-8")
        return path

    def test_lease_is_single_writer_and_generation_is_monotonic(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            contract = self.contract(root)
            first = authority.try_acquire_state_write_lease("codex_config", "state_write_authority", state_root=root, contract_path=contract)
            assert first is not None
            self.assertIsNone(authority.try_acquire_state_write_lease("codex_config", "state_write_authority", state_root=root, timeout_seconds=0, contract_path=contract))
            first.assert_current()
            generation = first.generation
            first.release()
            second = authority.try_acquire_state_write_lease("codex_config", "state_write_authority", state_root=root, contract_path=contract)
            assert second is not None
            self.assertGreater(second.generation, generation)
            second.release()

    def test_stale_lease_is_fenced_after_takeover(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            contract = self.contract(root)
            first = authority.try_acquire_state_write_lease("codex_config", "state_write_authority", state_root=root, contract_path=contract)
            assert first is not None
            owner = json.loads(first.owner_path().read_text(encoding="utf-8"))
            owner["expires_at_epoch"] = 1
            first.owner_path().write_text(json.dumps(owner), encoding="utf-8")
            second = authority.try_acquire_state_write_lease("codex_config", "state_write_authority", state_root=root, contract_path=contract)
            assert second is not None
            with self.assertRaisesRegex(RuntimeError, "state_write_fenced"):
                first.assert_current()
            second.release()

    def test_deferred_generation_advances_only_before_first_write(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            contract = self.contract(root)
            lease = authority.try_acquire_state_write_lease(
                "codex_config",
                "state_write_authority",
                state_root=root,
                contract_path=contract,
                advance_generation_on_acquire=False,
            )
            assert lease is not None
            self.assertEqual(lease.generation, 0)
            self.assertFalse((lease.root / "codex_config.generation.json").exists())
            self.assertEqual(lease.advance_generation(), 1)
            self.assertEqual(lease.advance_generation(), 1)
            lease.release()

    def test_zero_generation_publication_barrier_releases_for_next_writer(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            contract = self.contract(root)
            barrier = authority.try_acquire_state_write_lease(
                "codex_config",
                "codex_environment_mirror",
                state_root=root,
                contract_path=contract,
                advance_generation_on_acquire=False,
            )
            assert barrier is not None
            self.assertEqual(barrier.generation, 0)
            barrier.release()
            writer = authority.try_acquire_state_write_lease(
                "codex_config",
                "state_write_authority",
                state_root=root,
                contract_path=contract,
                timeout_seconds=0,
                advance_generation_on_acquire=False,
            )
            self.assertIsNotNone(writer)
            assert writer is not None
            writer.release()

    def test_duplicate_state_target_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "authorities.json"
            path.write_text(json.dumps({"schema": "state_write_authorities.v1", "domains": [{"domain_id": "a", "targets": ["same"], "allowed_writers": ["state_write_authority"]}, {"domain_id": "b", "targets": ["same"], "allowed_writers": ["state_write_authority"]}]}), encoding="utf-8")
            result = authority.snapshot(path)
            self.assertFalse(result["ok"])

    def test_codex_config_requires_unique_external_runtime_fields(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path = self.contract(root)
            missing = authority.snapshot(path)
            self.assertFalse(missing["ok"])
            payload = json.loads(path.read_text(encoding="utf-8"))
            field = {
                "target": "config",
                "path": ["marketplaces", "bundled", "source"],
                "authority": "product_runtime",
                "policy": "preserve_live_exclude_from_recovery_ledger_and_publication_signature",
            }
            payload["domains"][0]["external_runtime_fields"] = [field, field]
            path.write_text(json.dumps(payload), encoding="utf-8")
            duplicate = authority.snapshot(path)
            self.assertFalse(duplicate["ok"])

    def test_pre_publish_requires_stable_observed_signature(self) -> None:
        stable = {"signature": "same", "coordination": {"codex_config": {"generation": 4, "active_lease": False}}, "work_git_head": "a", "windows_bare_head": "a"}
        barrier = mock.Mock(spec=authority.StateWriteLease)
        barrier.writer_id = "codex_environment_mirror"
        barrier.generation = 4
        with mock.patch.object(authority, "snapshot", return_value={"ok": True}), mock.patch.object(authority, "observed_state_signature", return_value=stable):
            result = authority.pre_publish_gate(stability_seconds=0, held_barrier=barrier)
        self.assertTrue(result["ok"])
        barrier.assert_current.assert_called()

    def test_pre_publish_blocks_active_writer_even_when_signature_is_stable(self) -> None:
        active = {"signature": "same", "coordination": {"codex_config": {"generation": 5, "active_lease": True, "writer_id": "codex_state_repair"}}, "work_git_head": "a", "windows_bare_head": "a"}
        with mock.patch.object(authority, "snapshot", return_value={"ok": True}), mock.patch.object(authority, "observed_state_signature", return_value=active):
            result = authority.pre_publish_gate(stability_seconds=0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "active_state_writer")

    def test_pre_publish_blocks_generation_change_without_file_change(self) -> None:
        before = {"signature": "generation-5", "coordination": {"codex_config": {"generation": 5, "active_lease": False}}, "work_git_head": "a", "windows_bare_head": "a"}
        after = {"signature": "generation-6", "coordination": {"codex_config": {"generation": 6, "active_lease": False}}, "work_git_head": "a", "windows_bare_head": "a"}
        with mock.patch.object(authority, "snapshot", return_value={"ok": True}), mock.patch.object(authority, "observed_state_signature", side_effect=[before, after]):
            result = authority.pre_publish_gate(stability_seconds=0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "state_changed_during_publish_preflight")

    def test_semantic_config_digest_ignores_only_declared_external_runtime_fields(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "config.toml"
            path.write_text(
                'sandbox_mode = "danger-full-access"\n'
                '[marketplaces.openai-bundled]\n'
                'source = "C:\\\\first"\n'
                'last_updated = "first"\n',
                encoding="utf-8",
            )
            external = [
                ("marketplaces", "openai-bundled", "source"),
                ("marketplaces", "openai-bundled", "last_updated"),
            ]
            with mock.patch.object(authority, "_external_runtime_field_paths", return_value=external):
                first = authority._semantic_config_digest(path, target="windows_codex_config")
                path.write_text(
                    'sandbox_mode = "danger-full-access"\n'
                    '[marketplaces.openai-bundled]\n'
                    'source = "C:\\\\second"\n'
                    'last_updated = "second"\n',
                    encoding="utf-8",
                )
                self.assertEqual(first, authority._semantic_config_digest(path, target="windows_codex_config"))
                path.write_text(
                    'sandbox_mode = "read-only"\n'
                    '[marketplaces.openai-bundled]\n'
                    'source = "C:\\\\second"\n'
                    'last_updated = "second"\n',
                    encoding="utf-8",
                )
                self.assertNotEqual(first, authority._semantic_config_digest(path, target="windows_codex_config"))


if __name__ == "__main__":
    unittest.main()
