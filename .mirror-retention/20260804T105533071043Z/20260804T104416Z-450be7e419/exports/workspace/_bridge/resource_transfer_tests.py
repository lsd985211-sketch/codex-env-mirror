#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import resource_transfer_owner as owner
import scoped_authorization
from resource_transfer_contract import build_plan, disk_preflight, resume_decision
from shared.resource_event_store import (
    claim_transfer,
    get_transfer,
    submit_transfer,
    transfer_fence,
    transition_transfer,
)


PAYLOAD = (b"durable-resource-transfer-" * 32768) + b"done"
ETAG = '"fixture-v1"'
LAST_MODIFIED = "Tue, 28 Jul 2026 10:00:00 GMT"


class RangeHandler(BaseHTTPRequestHandler):
    payload = PAYLOAD
    etag = ETAG
    slow = False
    requests = 0

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _headers(self, status: int, length: int, content_range: str = "") -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("ETag", self.etag)
        self.send_header("Last-Modified", LAST_MODIFIED)
        if content_range:
            self.send_header("Content-Range", content_range)
        self.end_headers()

    def do_HEAD(self) -> None:
        self._headers(200, len(self.payload))

    def do_GET(self) -> None:
        type(self).requests += 1
        start = 0
        raw = self.headers.get("Range", "")
        if raw.startswith("bytes="):
            start = int(raw.split("=", 1)[1].split("-", 1)[0])
        body = self.payload[start:]
        self._headers(
            206 if start else 200,
            len(body),
            f"bytes {start}-{len(self.payload) - 1}/{len(self.payload)}"
            if start
            else "",
        )
        for offset in range(0, len(body), 32768):
            try:
                self.wfile.write(body[offset : offset + 32768])
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                break
            if self.slow:
                time.sleep(0.01)


class ResourceTransferTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "record_store.sqlite"
        self.receipts = self.root / "receipts"
        RangeHandler.payload = PAYLOAD
        RangeHandler.etag = ETAG
        RangeHandler.slow = False
        RangeHandler.requests = 0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/fixture.bin"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def plan(self, name: str = "artifact.bin") -> dict:
        return build_plan(
            request_id="res_fixture",
            operation_id="materialize",
            source_url=self.url,
            target_path=str(self.root / name),
            authorization={"active": True, "generation": 1, "fixture_only": True},
            expected_sha256=hashlib.sha256(PAYLOAD).hexdigest(),
            expected_length=len(PAYLOAD),
            allowlist=True,
        )

    def test_real_direct_url_requires_scoped_permit_but_loopback_fixture_is_bounded(
        self,
    ) -> None:
        denied = build_plan(
            request_id="r",
            operation_id="o",
            source_url="https://example.test/file",
            target_path=str(self.root / "x"),
            authorization={"active": True, "generation": 1},
            allowlist=True,
        )
        self.assertEqual("scoped_authorization_permit_required", denied["reason"])
        self.assertTrue(self.plan()["ok"])

    def test_consumed_permit_can_resume_same_operation_until_intent_is_revoked(
        self,
    ) -> None:
        auth_root = self.root / "authorizations"
        scope = scoped_authorization.build_scope(
            thread_id="thread-transfer",
            action="resource.transfer",
            target={"request_id": "res_auth", "operation_id": "materialize-auth"},
            risk="medium",
            phase="transfer",
            source_signature="source-v1",
            requested_by_owner=owner.OWNER,
        )
        intent = scoped_authorization.create_intent(
            scope,
            intent_type="one_time",
            authorizer="user",
            subject="user",
            evidence_ref="rollout:turn:approved",
            turn_id="turn-transfer",
            allowed_actor_classes=["codex"],
            state_root=auth_root,
        )
        permit = scoped_authorization.issue_permit(
            intent["intent_ref"],
            scope,
            actor_chain=[{"class": "codex", "id": "root"}],
            executor=owner.OWNER,
            audience=owner.OWNER,
            operation_id="materialize-auth",
            workflow_semantic_hash="resource-transfer-v1",
            authorization_turn="turn-transfer",
            state_root=auth_root,
        )
        plan = build_plan(
            request_id="res_auth",
            operation_id="materialize-auth",
            source_url=self.url,
            target_path=str(self.root / "authorized.bin"),
            authorization={
                "active": True,
                "generation": 1,
                "permit_ref": permit["permit_ref"],
            },
            expected_length=len(PAYLOAD),
            allowlist=True,
        )
        self.assertTrue(owner.submit(plan, db_path=self.db, state_root=auth_root)["ok"])
        consumed = scoped_authorization.consume_permit(
            permit["permit_ref"],
            executor=owner.OWNER,
            operation_id="materialize-auth",
            idempotency_key=plan["execution_signature"],
            state_root=auth_root,
        )
        self.assertTrue(consumed["ok"], consumed)
        resumed = owner._authorization_active(plan, state_root=auth_root)
        self.assertTrue(resumed["ok"], resumed)
        self.assertTrue(resumed["continuation"])
        revoked = scoped_authorization.transition_intent(
            intent["intent_ref"],
            action="revoke",
            reason="user withdrew",
            state_root=auth_root,
        )
        self.assertTrue(revoked["ok"], revoked)
        fenced = owner._authorization_active(plan, state_root=auth_root)
        self.assertFalse(fenced["ok"])
        self.assertEqual("authorization_generation_fenced", fenced["reason"])

    def test_submit_is_idempotent_and_end_to_end_receipt_is_reused(self) -> None:
        plan = self.plan()
        first = owner.submit(plan, db_path=self.db)
        replay = owner.submit(plan, db_path=self.db)
        self.assertFalse(first["reused"])
        self.assertTrue(replay["reused"])
        with patch.dict(
            os.environ, {"CODEX_LONG_COMMAND_RECEIPT_ROOT": str(self.receipts)}
        ):
            result = owner.converge(
                plan["transfer_id"], db_path=self.db, timeout_seconds=20
            )
            again = owner.converge(
                plan["transfer_id"], db_path=self.db, timeout_seconds=20
            )
        self.assertTrue(result["ok"], result)
        self.assertTrue(again["reused"])
        self.assertEqual(PAYLOAD, Path(plan["target_path"]).read_bytes())
        self.assertEqual(1, RangeHandler.requests)

    def test_validated_range_resumes_from_existing_partial(self) -> None:
        plan = self.plan("resume.bin")
        split = len(PAYLOAD) // 3
        Path(plan["partial_path"]).write_bytes(PAYLOAD[:split])
        Path(plan["partial_metadata_path"]).write_text(
            json.dumps(
                {
                    "etag": ETAG,
                    "last_modified": LAST_MODIFIED,
                    "expected_length": len(PAYLOAD),
                }
            ),
            encoding="utf-8",
        )
        owner.submit(plan, db_path=self.db)
        with patch.dict(
            os.environ, {"CODEX_LONG_COMMAND_RECEIPT_ROOT": str(self.receipts)}
        ):
            result = owner.converge(
                plan["transfer_id"], db_path=self.db, timeout_seconds=20
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(PAYLOAD, Path(plan["target_path"]).read_bytes())

    def test_validator_change_refuses_to_append_old_partial(self) -> None:
        plan = self.plan("changed.bin")
        split = 1000
        Path(plan["partial_path"]).write_bytes(PAYLOAD[:split])
        Path(plan["partial_metadata_path"]).write_text(
            json.dumps({"etag": '"old"', "last_modified": LAST_MODIFIED}),
            encoding="utf-8",
        )
        owner.submit(plan, db_path=self.db)
        with patch.dict(
            os.environ, {"CODEX_LONG_COMMAND_RECEIPT_ROOT": str(self.receipts)}
        ):
            result = owner.converge(
                plan["transfer_id"], db_path=self.db, timeout_seconds=20
            )
        self.assertFalse(result["ok"])
        self.assertFalse(Path(plan["target_path"]).exists())
        self.assertFalse(Path(plan["partial_path"]).exists())

    def test_generation_fence_and_lifecycle_controls_are_monotonic(self) -> None:
        plan = self.plan("lifecycle.bin")
        submit_transfer(plan, db_path=self.db)
        claimed = claim_transfer(
            plan["transfer_id"], lease_owner="test", db_path=self.db
        )["transfer"]
        running = transition_transfer(
            plan["transfer_id"],
            expected={"leased"},
            state="running",
            expected_generation=claimed["generation"],
            db_path=self.db,
        )["transfer"]
        self.assertTrue(
            transfer_fence(
                plan["transfer_id"], generation=running["generation"], db_path=self.db
            )["active"]
        )
        paused = owner.pause(plan["transfer_id"], db_path=self.db)
        self.assertEqual("paused", paused["transfer"]["state"])
        self.assertFalse(
            transfer_fence(
                plan["transfer_id"], generation=running["generation"], db_path=self.db
            )["active"]
        )
        self.assertEqual(
            "queued",
            owner.resume(plan["transfer_id"], db_path=self.db)["transfer"]["state"],
        )
        self.assertEqual(
            "cancelled",
            owner.cancel(plan["transfer_id"], db_path=self.db)["transfer"]["state"],
        )

    def _wait_for_state(self, transfer_id: str, state: str) -> dict:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            current = get_transfer(transfer_id, db_path=self.db) or {}
            if current.get("state") == state:
                return current
            time.sleep(0.01)
        self.fail(f"transfer did not reach {state}")

    def _wait_for_partial(self, plan: dict) -> int:
        path = Path(plan["partial_path"])
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if path.exists() and path.stat().st_size > 0:
                return path.stat().st_size
            time.sleep(0.01)
        self.fail("worker did not materialize a partial before lifecycle control")

    def test_running_pause_fences_old_worker_and_resume_uses_new_generation(
        self,
    ) -> None:
        RangeHandler.slow = True
        plan = self.plan("pause-resume.bin")
        owner.submit(plan, db_path=self.db)
        results: list[dict] = []

        def run() -> None:
            results.append(
                owner.converge(plan["transfer_id"], db_path=self.db, timeout_seconds=20)
            )

        with patch.dict(
            os.environ, {"CODEX_LONG_COMMAND_RECEIPT_ROOT": str(self.receipts)}
        ):
            thread = threading.Thread(target=run)
            thread.start()
            running = self._wait_for_state(plan["transfer_id"], "running")
            partial_size = self._wait_for_partial(plan)
            paused = owner.pause(plan["transfer_id"], db_path=self.db)
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
            self.assertTrue(results[0].get("paused"), results[0])
            self.assertGreater(paused["transfer"]["generation"], running["generation"])
            self.assertGreaterEqual(
                Path(plan["partial_path"]).stat().st_size, partial_size
            )
            resumed = owner.resume(plan["transfer_id"], db_path=self.db)
            completed = owner.converge(
                plan["transfer_id"], db_path=self.db, timeout_seconds=20
            )

        self.assertGreater(
            completed["transfer"]["generation"], resumed["transfer"]["generation"]
        )
        self.assertTrue(completed["ok"], completed)
        self.assertEqual(PAYLOAD, Path(plan["target_path"]).read_bytes())
        self.assertEqual(2, RangeHandler.requests)

    def test_running_cancel_fences_worker_and_never_places_artifact(self) -> None:
        RangeHandler.slow = True
        plan = self.plan("cancel.bin")
        owner.submit(plan, db_path=self.db)
        results: list[dict] = []

        def run() -> None:
            results.append(
                owner.converge(plan["transfer_id"], db_path=self.db, timeout_seconds=20)
            )

        with patch.dict(
            os.environ, {"CODEX_LONG_COMMAND_RECEIPT_ROOT": str(self.receipts)}
        ):
            thread = threading.Thread(target=run)
            thread.start()
            self._wait_for_state(plan["transfer_id"], "running")
            partial_size = self._wait_for_partial(plan)
            owner.cancel(plan["transfer_id"], db_path=self.db)
            thread.join(timeout=10)

        self.assertFalse(thread.is_alive())
        self.assertEqual("cancelled", results[0]["transfer"]["state"])
        self.assertFalse(Path(plan["target_path"]).exists())
        self.assertGreaterEqual(Path(plan["partial_path"]).stat().st_size, partial_size)

    def test_disk_preflight_fails_closed(self) -> None:
        result = disk_preflight(
            target_path=str(self.root / "huge.bin"), expected_length=10**30
        )
        self.assertFalse(result["ok"])
        self.assertEqual("insufficient_disk_space", result["reason"])

    def test_resume_contract_rejects_200_or_changed_validator(self) -> None:
        self.assertFalse(
            resume_decision(
                partial_size=10,
                stored_etag='"a"',
                stored_last_modified="",
                response_status=200,
                response_etag='"a"',
                response_last_modified="",
                content_range="",
            )["ok"]
        )
        self.assertFalse(
            resume_decision(
                partial_size=10,
                stored_etag='"a"',
                stored_last_modified="",
                response_status=206,
                response_etag='"b"',
                response_last_modified="",
                content_range="bytes 10-19/20",
            )["ok"]
        )


if __name__ == "__main__":
    unittest.main()
