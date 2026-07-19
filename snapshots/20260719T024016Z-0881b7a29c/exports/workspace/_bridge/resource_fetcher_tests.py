#!/usr/bin/env python3
"""Regression tests for workspace resource acquisition."""

from __future__ import annotations

import contextlib
import hashlib
import http.server
import io
import json
import socketserver
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import resource_fetcher
import resource_broker
import resource_owner_executor
import resource_owner_hub_adapter
import resource_package_owner
import resource_python_package_installer
import resource_scheduler
import resource_source_executor
import resource_windows_package_manager
from codex_resource_delegation import build_delegation, build_delegation_from_envelope
from intent_resource_router import build_route as build_intent_resource_route
from resource_candidate_quality import filter_ranked_candidates, rank_candidates, validate as validate_candidate_quality
from resource_download_backends import availability as download_backend_availability
from resource_download_backends import validate as validate_download_backends
from resource_fetcher import (
    ResourceIntent,
    ResourceRequest,
    ResourceResult,
    ResourceStage,
    acquire_bytes_resource,
    acquire_local_resource,
    acquire_resource_with_policy,
    acquire_url_resource,
    network_attempts_for_url,
    preview_url_resource,
    probe_url_resource,
    suffix_from_content_type,
    url_resource_name,
)
from resource_cli import main as resource_cli_main
from resource_cli import classify_url_semantics
from resource_cli_resource import build_get_payload
from resource_collection_acquirer import collect_resources, validate as validate_collection_acquirer
from resource_library_paths import RESOURCE_LIBRARY_ROOT
from resource_router import route_resource
from resource_strategy_policy import (
    owner_result_relevance,
    owner_result_sufficiency,
    recovery_decision_for_attempt,
    recovery_decision_for_error,
    resource_result_satisfaction,
    validate as validate_strategy_policy,
)
from resource_strategy_review import build_resource_strategy_review, read_resource_log
from local_mcp_hub import LocalMcpHub
from local_mcp_hub_resource_search import resource_search_call
from resource_broker import (
    DEFAULT_STORE_ROOT,
    ResourceBrokerRequest,
    attach_result_to_request,
    handle_request as _handle_request,
    mark_request_consumed,
    route_for_request,
    strategy_plan_for_request,
)
from resource_owner_executor import supports_owner_execution, validate as validate_owner_executor
from resource_owner_hub_adapter import gateway_failure_is_recoverable, gateway_failure_reason
from resource_owner_result_disk_cache import validate as validate_owner_result_disk_cache
from resource_progress_view import progress_for_batch, progress_for_manifest, progress_for_request
from resource_request_runtime_cache import validate as validate_runtime_cache
from resource_scenario_smoke import run_scenario_smoke, validate as validate_scenario_smoke
from resource_scheduler import ResourceBatchConfig, batch_status_from_manifest, execute_batch, requests_from_payload
from resource_source_executor import execute_source_selection, validate as validate_source_executor
from resource_source_strategy import candidate_source_plan, source_execution_plan, validate as validate_source_strategy
from structured_task_envelope import normalize_resource_envelope
from shared.record_store_maintenance import RECORD_ROOTS
from shared.resource_event_store import rebuild_from_manifests, request_row


@contextlib.contextmanager
def local_http_server(root: Path):
    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

    class ReusableTCPServer(socketserver.TCPServer):
        allow_reuse_address = True

    previous = Path.cwd()
    try:
        import os

        os.chdir(root)
        with ReusableTCPServer(("127.0.0.1", 0), QuietHandler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                yield f"http://127.0.0.1:{server.server_address[1]}"
            finally:
                server.shutdown()
                thread.join(timeout=5)
    finally:
        import os

        os.chdir(previous)


@contextlib.contextmanager
def flaky_http_server(success_body: bytes = b"retry ok"):
    state = {"count": 0}

    class FlakyHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            state["count"] += 1
            if state["count"] == 1:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"temporary failure")
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(success_body)))
            self.end_headers()
            self.wfile.write(success_body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    class ReusableTCPServer(socketserver.TCPServer):
        allow_reuse_address = True

    with ReusableTCPServer(("127.0.0.1", 0), FlakyHandler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_address[1]}", state
        finally:
            server.shutdown()
            thread.join(timeout=5)


@contextlib.contextmanager
def staged_http_server():
    body = b"hello staged resource preview"

    class StagedHandler(http.server.BaseHTTPRequestHandler):
        def do_HEAD(self) -> None:
            if self.path == "/head-unsupported.txt":
                self.send_response(405)
                self.end_headers()
                return
            if self.path == "/missing.txt":
                self.send_response(404)
                self.end_headers()
                return
            if self.path == "/redirect.txt":
                self.send_response(302)
                self.send_header("Location", "/probe.txt")
                self.end_headers()
                return
            if self.path == "/large.bin":
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", "20")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()

        def do_GET(self) -> None:
            if self.path == "/missing.txt":
                self.send_response(404)
                self.end_headers()
                return
            if self.path == "/redirect.txt":
                self.send_response(302)
                self.send_header("Location", "/probe.txt")
                self.end_headers()
                return
            if self.path == "/large.bin":
                payload = b"01234567890123456789"
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    class ReusableTCPServer(socketserver.TCPServer):
        allow_reuse_address = True

    with ReusableTCPServer(("127.0.0.1", 0), StagedHandler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_address[1]}"
        finally:
            server.shutdown()
            thread.join(timeout=5)


def assert_ok(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def resource_request_tree_fingerprint(store_root: Path) -> tuple[tuple[str, str, int, int], ...]:
    """Capture request-tree identity without reading production payload contents."""
    request_root = store_root.expanduser().resolve() / "_requests"
    if not request_root.exists():
        return ()
    entries: list[tuple[str, str, int, int]] = []
    for path in request_root.rglob("*"):
        stat = path.stat()
        entries.append(
            (
                "directory" if path.is_dir() else "file",
                path.relative_to(request_root).as_posix(),
                stat.st_size,
                stat.st_mtime_ns,
            )
        )
    return tuple(sorted(entries))


def request_ids_below(root: Path) -> set[str]:
    request_ids: set[str] = set()
    for manifest_path in root.rglob("manifest.json"):
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        request_id = str(payload.get("request_id") or "").strip()
        if request_id.startswith("res_"):
            request_ids.add(request_id)
    return request_ids


def production_store_contamination(
    store_root: Path,
    before: tuple[tuple[str, str, int, int], ...],
    *,
    test_root: Path,
    test_request_ids: set[str],
) -> dict[str, object]:
    """Distinguish test leakage from legitimate concurrent production updates."""
    after = resource_request_tree_fingerprint(store_root)
    before_by_path = {entry[1]: entry for entry in before}
    after_by_path = {entry[1]: entry for entry in after}
    changed_paths = sorted(
        path
        for path in set(before_by_path) | set(after_by_path)
        if before_by_path.get(path) != after_by_path.get(path)
    )
    normalized_test_root = str(test_root.expanduser().resolve()).replace("\\", "/").lower()
    contaminated_paths: list[str] = []
    request_root = store_root.expanduser().resolve() / "_requests"
    for relative in changed_paths:
        parts = Path(relative).parts
        if parts and parts[0] in test_request_ids:
            contaminated_paths.append(relative)
            continue
        if not relative.endswith("manifest.json"):
            continue
        manifest_path = request_root / relative
        try:
            manifest_text = manifest_path.read_text(encoding="utf-8").replace("\\", "/").lower()
        except (OSError, UnicodeError):
            continue
        if normalized_test_root in manifest_text or "resource-fetcher-tests-" in manifest_text:
            contaminated_paths.append(relative)
    return {
        "changed_paths": changed_paths,
        "contaminated_paths": sorted(set(contaminated_paths)),
    }


def run_resource_fetcher_tests() -> dict[str, str]:
    results: dict[str, str] = {}
    production_store_before = resource_request_tree_fingerprint(DEFAULT_STORE_ROOT)
    with tempfile.TemporaryDirectory(prefix="resource-fetcher-tests-") as tmp:
        root = Path(tmp)
        cache = root / "cache"
        broker_store = root / "broker-store"

        def isolated_handle_request(request: ResourceBrokerRequest, **kwargs: object):
            kwargs.setdefault("store_root", broker_store)
            receipt = _handle_request(request, **kwargs)
            if receipt.manifest_path:
                manifest_path = Path(receipt.manifest_path).expanduser().resolve()
                assert_ok(
                    manifest_path.is_relative_to(Path(kwargs["store_root"]).expanduser().resolve()),
                    "resource broker test escaped its isolated store",
                )
            return receipt

        source = root / "sample.txt"
        source.write_text("hello resource layer", encoding="utf-8")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()

        structured_row = request_row(
            request_id="structured-projection",
            request={
                "intent": "external_dependency",
                "metadata": {
                    "task_envelope": {
                        "schema": "structured_task_envelope.v1",
                        "domain": "resource",
                        "action": "discover",
                        "target": "mcp gateway",
                        "resource": {"kind": "github_repository"},
                    }
                },
            },
            receipt={
                "status": "completed",
                "ok": True,
                "attempts": [
                    {
                        "tool": "github",
                        "result": {"ok": True, "metadata": {"network_route_mode": "probe_selected_proxy"}},
                    }
                ],
            },
            manifest_path=str(root / "structured-manifest.json"),
        )
        assert_ok(structured_row["resource_kind"] == "github_repository", "structured resource kind projection failed")
        assert_ok(structured_row["owner_tool"] == "github", "completed attempt owner projection failed")
        assert_ok(structured_row["route_mode"] == "probe_selected_proxy", "attempt route projection failed")
        legacy_row = request_row(
            request_id="legacy-projection",
            request={"metadata": {"resource_kind_hint": "image"}},
            receipt={"status": "completed", "attempts": [{"tool": "local_file", "executable": True, "result": {"ok": True}}]},
            manifest_path=str(root / "legacy-manifest.json"),
        )
        assert_ok(legacy_row["resource_kind"] == "image", "legacy resource kind hint projection failed")
        assert_ok(legacy_row["route_mode"] == "local_execution", "local execution route projection failed")
        projection_store = root / "projection-store"
        projection_manifest_dir = projection_store / "_requests" / "structured-projection"
        projection_manifest_dir.mkdir(parents=True, exist_ok=True)
        projection_manifest_path = projection_manifest_dir / "manifest.json"
        projection_manifest_path.write_text(
            json.dumps(
                {
                    "request_id": "structured-projection",
                    "request": {
                        "intent": "external_dependency",
                        "metadata": {
                            "task_envelope": {
                                "schema": "structured_task_envelope.v1",
                                "domain": "resource",
                                "action": "discover",
                                "target": "mcp gateway",
                                "resource": {"kind": "github_repository"},
                            }
                        },
                    },
                    "receipt": {
                        "request_id": "structured-projection",
                        "status": "completed",
                        "ok": True,
                        "result_kind": "github_repository_search",
                        "attempts": [
                            {
                                "tool": "github",
                                "result": {"ok": True, "metadata": {"network_route_mode": "probe_selected_proxy"}},
                            }
                        ],
                        "consumption": {
                            "schema": "resource_store.consumption.v1",
                            "satisfied": True,
                            "consumed_at": "2026-07-13T08:00:00+08:00",
                            "consumer": "codex",
                            "mode": "no_read_needed",
                            "consumed_path": "",
                            "no_read_needed_reason": "metadata result evaluated directly",
                        },
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        projection_db = root / "projection.sqlite3"
        projection_conn = sqlite3.connect(projection_db)
        try:
            rebuild_counts = rebuild_from_manifests(projection_conn, store_root=projection_store)
            rebuilt_row = projection_conn.execute(
                "SELECT resource_kind,owner_tool,route_mode,consumed,consumer,no_read_needed_reason "
                "FROM resource_requests WHERE request_id = ?",
                ("structured-projection",),
            ).fetchone()
        finally:
            projection_conn.close()
        assert_ok(rebuild_counts["requests"] == 1, "resource SQLite rebuild should index the structured manifest")
        assert_ok(
            rebuilt_row == (
                "github_repository",
                "github",
                "probe_selected_proxy",
                1,
                "codex",
                "metadata result evaluated directly",
            ),
            "resource SQLite rebuild should preserve structured projection and consumption fields",
        )
        results["structured_observability_projection"] = "ok"

        local_result = acquire_local_resource(
            ResourceRequest(source="test-local", target_dir=cache, name="sample.txt", local_path=source)
        )
        assert_ok(local_result.ok, local_result.error)
        assert_ok(Path(local_result.stored_path).exists(), "local stored file missing")
        assert_ok(local_result.sha256 == digest, "local sha256 mismatch")
        results["local_file"] = "ok"

        original_recommendation = resource_fetcher.recommendation_for_target
        try:
            resource_fetcher.recommendation_for_target = lambda _target, context="": SimpleNamespace(
                route="auto_fastest",
                proxy_url="http://127.0.0.1:7897",
                profile="package_auto",
                category="package",
                host="registry.npmjs.org",
                reason="test",
                warnings=(),
            )
            auto_routes = [
                item.route
                for item in network_attempts_for_url(
                    ResourceRequest(source="test-network-auto", target_dir=cache, url="https://registry.npmjs.org/")
                )
            ]
            resource_fetcher.recommendation_for_target = lambda _target, context="": SimpleNamespace(
                route="proxy_preferred",
                proxy_url="http://127.0.0.1:7897",
                profile="openai_proxy",
                category="openai",
                host="api.openai.com",
                reason="test",
                warnings=(),
            )
            proxy_routes = [
                item.route
                for item in network_attempts_for_url(
                    ResourceRequest(source="test-network-proxy", target_dir=cache, url="https://api.openai.com/v1/models")
                )
            ]
        finally:
            resource_fetcher.recommendation_for_target = original_recommendation
        assert_ok(auto_routes == ["direct", "proxy"], "auto_fastest should try direct before proxy")
        assert_ok(proxy_routes == ["proxy", "direct"], "proxy_preferred should try proxy before direct fallback")
        results["network_route_attempt_order"] = "ok"

        gateway_routes = [
            (item.route, item.proxy_url)
            for item in network_attempts_for_url(
                ResourceRequest(
                    source="test-gateway-network",
                    target_dir=cache,
                    url="https://example.com/resource.txt",
                    metadata={
                        "network_gateway_plan": {
                            "ok": True,
                            "plan": {
                                "route_mode": "current_proxy_env",
                                "proxy_url": "http://127.0.0.1:7897",
                                "env": {"HTTPS_PROXY": "http://127.0.0.1:7897"},
                            },
                        }
                    },
                )
            )
        ]
        assert_ok(gateway_routes == [("gateway_proxy", "http://127.0.0.1:7897")], "gateway plan should drive URL attempt")
        results["resource_gateway_network_attempt"] = "ok"

        policy_local = acquire_resource_with_policy(
            ResourceRequest(source="test-policy-local", target_dir=cache, name="sample.txt", local_path=source),
            intent=ResourceIntent.EXPLICIT_ATTACHMENT,
        )
        assert_ok(policy_local.ok, policy_local.error)
        assert_ok(policy_local.decision == "allowed", "policy local decision missing")
        assert_ok(policy_local.policy_name == "explicit_attachment_v1", "policy local name missing")
        assert_ok(policy_local.intent == ResourceIntent.EXPLICIT_ATTACHMENT, "policy local intent missing")
        assert_ok(policy_local.resource_kind == "local_file", "policy local resource kind missing")
        results["policy_explicit_attachment_local"] = "ok"

        bytes_result = acquire_bytes_resource(
            source="test-bytes",
            data=b"bytes payload",
            target_dir=cache,
            name="payload.bin",
        )
        assert_ok(bytes_result.ok, bytes_result.error)
        assert_ok(Path(bytes_result.stored_path).exists(), "bytes stored file missing")
        results["bytes_upload"] = "ok"

        (root / "web.txt").write_text("hello over http", encoding="utf-8")
        (root / "docs-page.html").write_text("<html><body>hello html</body></html>", encoding="utf-8")
        with local_http_server(root) as base_url:
            url_result = acquire_url_resource(
                ResourceRequest(source="test-url", target_dir=cache, name="web.txt", url=f"{base_url}/web.txt")
            )
            inferred_html_result = acquire_url_resource(
                ResourceRequest(source="test-inferred-html-url", target_dir=cache, url=f"{base_url}/docs-page.html")
            )
            curl_backend_result = acquire_url_resource(
                ResourceRequest(
                    source="test-curl-backend",
                    target_dir=cache,
                    name="curl-web.txt",
                    url=f"{base_url}/web.txt",
                    metadata={"download_backend": "curl", "resume_download": True},
                )
            )
            aria2_backend_result = acquire_url_resource(
                ResourceRequest(
                    source="test-aria2-backend",
                    target_dir=cache,
                    name="aria2-web.txt",
                    url=f"{base_url}/web.txt",
                    metadata={"download_backend": "aria2", "resume_download": True},
                )
            )
            policy_url = acquire_resource_with_policy(
                ResourceRequest(source="test-policy-url", target_dir=cache, name="web.txt", url=f"{base_url}/web.txt"),
                intent=ResourceIntent.EXPLICIT_ATTACHMENT,
            )
            inline_candidate = acquire_resource_with_policy(
                ResourceRequest(source="test-inline-url", target_dir=cache, name="web.txt", url=f"{base_url}/web.txt"),
                intent=ResourceIntent.INLINE_URL_CANDIDATE,
            )
            explicit_user_url = acquire_resource_with_policy(
                ResourceRequest(source="test-explicit-user-url", target_dir=cache, name="web.txt", url=f"{base_url}/web.txt"),
                intent=ResourceIntent.EXPLICIT_USER_URL,
            )
            documentation_lookup = acquire_resource_with_policy(
                ResourceRequest(source="test-doc-lookup", target_dir=cache, name="docs", url=f"{base_url}/web.txt"),
                intent=ResourceIntent.DOCUMENTATION_LOOKUP,
            )
            cli_acquire_stdout = io.StringIO()
            with contextlib.redirect_stdout(cli_acquire_stdout):
                acquire_code = resource_cli_main(
                    [
                        "acquire",
                        "--intent",
                        ResourceIntent.EXPLICIT_USER_URL,
                        "--url",
                        f"{base_url}/web.txt",
                        "--target-dir",
                        str(root / "cli-acquire-cache"),
                        "--name",
                        "web.txt",
                        "--json",
                        "--no-log",
                    ]
                )
            cli_inline_stdout = io.StringIO()
            with contextlib.redirect_stdout(cli_inline_stdout):
                inline_code = resource_cli_main(
                    [
                        "acquire",
                        "--intent",
                        ResourceIntent.INLINE_URL_CANDIDATE,
                        "--url",
                        f"{base_url}/web.txt",
                        "--target-dir",
                        str(root / "cli-inline-cache"),
                        "--json",
                        "--no-log",
                    ]
                )
            cli_url_stdout = io.StringIO()
            with contextlib.redirect_stdout(cli_url_stdout):
                code = resource_cli_main(
                    [
                        "fetch-url",
                        f"{base_url}/web.txt",
                        "--target-dir",
                        str(root / "cli-url-cache"),
                        "--name",
                        "web.txt",
                        "--json",
                        "--no-log",
                    ]
                )
        assert_ok(url_result.ok, url_result.error)
        assert_ok(Path(url_result.stored_path).read_text(encoding="utf-8") == "hello over http", "url data mismatch")
        assert_ok(suffix_from_content_type("text/html; charset=utf-8") == ".html", "html content-type suffix mismatch")
        assert_ok(
            url_resource_name(
                ResourceRequest(source="test-url-name", target_dir=cache, url="https://example.test/path/"),
                {"content_type": "text/html"},
            ).endswith(".html"),
            "content-type fallback name should use html suffix",
        )
        assert_ok(inferred_html_result.ok, inferred_html_result.error)
        assert_ok(Path(inferred_html_result.stored_path).suffix == ".html", "inferred HTML URL should keep html extension")
        backend_validation = validate_download_backends()
        assert_ok(backend_validation.get("ok"), "download backend validation failed")
        assert_ok(curl_backend_result.ok, curl_backend_result.error)
        assert_ok((curl_backend_result.metadata or {}).get("download_backend") == "curl", "curl backend metadata missing")
        assert_ok(
            ((curl_backend_result.metadata or {}).get("download_backend_selection") or {}).get("reason") == "explicit_backend_requested",
            "curl backend selection reason missing",
        )
        assert_ok(
            ((curl_backend_result.metadata or {}).get("download_backend_result") or {}).get("backend") == "curl",
            "curl backend result marker missing",
        )
        assert_ok(
            ((url_result.metadata or {}).get("download_backend_selection") or {}).get("next_action") == "use_builtin_http_download",
            "default URL materialization should record builtin backend decision",
        )
        if download_backend_availability().aria2c_path:
            assert_ok(aria2_backend_result.ok, aria2_backend_result.error)
            assert_ok((aria2_backend_result.metadata or {}).get("download_backend") == "aria2", "aria2 backend metadata missing")
        else:
            assert_ok(not aria2_backend_result.ok, "aria2 backend should fail closed when aria2c is unavailable")
            assert_ok((aria2_backend_result.metadata or {}).get("error_type") == "backend_unavailable", "aria2 unavailable error missing")
            assert_ok(
                ((aria2_backend_result.metadata or {}).get("resource_strategy") or {}).get("next_action")
                == "request_backend_install_or_choose_available_backend",
                "aria2 unavailable should report actionable backend next_action",
            )
        assert_ok(
            url_result.metadata and url_result.metadata.get("network", {}).get("execution_route") == "direct",
            "url resource should record direct network execution route",
        )
        network_meta = (url_result.metadata or {}).get("network", {})
        assert_ok(network_meta.get("health_score") == 100, "local URL should expose route health score")
        assert_ok(network_meta.get("retry_budget") == 2, "local URL should expose retry budget")
        assert_ok(network_meta.get("failover_policy") == "direct_only_no_proxy", "local URL failover policy mismatch")
        assert_ok("local_bypass" in network_meta.get("observability_tags", []), "local URL observability tag missing")
        assert_ok("oauth" in network_meta.get("excluded_permission_mechanisms", []), "network metadata should exclude complex auth mechanisms")
        assert_ok(policy_url.ok, policy_url.error)
        assert_ok(policy_url.decision == "allowed", "policy url decision missing")
        assert_ok(policy_url.resource_kind == "url", "policy url resource kind missing")
        assert_ok(inline_candidate.decision == "deferred", "inline URL candidate should be deferred")
        assert_ok(not inline_candidate.ok, "inline URL candidate should not auto-acquire")
        assert_ok(inline_candidate.policy_name == "inline_url_candidate_v1", "inline URL policy name missing")
        assert_ok(explicit_user_url.ok, explicit_user_url.error)
        assert_ok(explicit_user_url.policy_name == "explicit_user_url_v1", "explicit user URL policy missing")
        assert_ok(documentation_lookup.decision == "deferred", "documentation lookup should be deferred")
        assert_ok(documentation_lookup.policy_name == "documentation_lookup_v1", "documentation lookup policy missing")
        assert_ok(acquire_code == 0, "resource_cli acquire explicit URL failed")
        acquire_payload = json.loads(cli_acquire_stdout.getvalue())
        assert_ok(acquire_payload["ok"], "resource_cli acquire explicit URL JSON failed")
        assert_ok(acquire_payload["policy_name"] == "explicit_user_url_v1", "resource_cli acquire policy missing")
        assert_ok(inline_code == 0, "resource_cli acquire deferred inline URL should be policy-success")
        inline_payload = json.loads(cli_inline_stdout.getvalue())
        assert_ok(not inline_payload["ok"], "resource_cli inline URL should not download")
        assert_ok(inline_payload["decision"] == "deferred", "resource_cli inline URL decision missing")
        assert_ok(code == 0, "resource_cli fetch-url failed")
        cli_url_payload = json.loads(cli_url_stdout.getvalue())
        assert_ok(cli_url_payload["ok"], "resource_cli fetch-url JSON failed")
        assert_ok(cli_url_payload["intent"] == ResourceIntent.EXPLICIT_USER_URL, "legacy fetch-url intent missing")
        assert_ok(cli_url_payload["policy_name"] == "explicit_user_url_v1", "legacy fetch-url policy missing")
        assert_ok(cli_url_payload["resource_kind"] == "url", "legacy fetch-url kind missing")
        assert_ok((cli_url_payload["metadata"] or {}).get("legacy_command") is True, "legacy fetch-url marker missing")
        cli_url_inline_stdout = io.StringIO()
        with contextlib.redirect_stdout(cli_url_inline_stdout):
            inline_legacy_code = resource_cli_main(
                [
                    "fetch-url",
                    f"{base_url}/web.txt",
                    "--target-dir",
                    str(root / "cli-url-inline-cache"),
                    "--intent",
                    ResourceIntent.INLINE_URL_CANDIDATE,
                    "--json",
                    "--no-log",
                ]
            )
        inline_legacy_payload = json.loads(cli_url_inline_stdout.getvalue())
        assert_ok(inline_legacy_code == 0, "legacy fetch-url deferred inline URL should be policy-success")
        assert_ok(not inline_legacy_payload["ok"], "legacy fetch-url inline URL should not download")
        assert_ok(inline_legacy_payload["decision"] == "deferred", "legacy fetch-url inline URL decision missing")
        assert_ok(inline_legacy_payload["policy_name"] == "inline_url_candidate_v1", "legacy fetch-url inline URL policy missing")
        results["local_http_url"] = "ok"
        results["download_backends"] = "ok"
        results["policy_explicit_attachment_url"] = "ok"
        results["policy_explicit_user_url"] = "ok"
        results["policy_inline_url_deferred"] = "ok"
        results["policy_documentation_lookup_deferred"] = "ok"
        results["resource_cli_acquire"] = "ok"
        results["resource_cli_legacy_url_policy"] = "ok"

        with staged_http_server() as staged_url:
            probe = probe_url_resource(
                ResourceRequest(source="test-probe", target_dir=cache, name="probe.txt", url=f"{staged_url}/probe.txt")
            )
            head_fallback = probe_url_resource(
                ResourceRequest(source="test-head-fallback", target_dir=cache, name="head.txt", url=f"{staged_url}/head-unsupported.txt")
            )
            preview = preview_url_resource(
                ResourceRequest(source="test-preview", target_dir=cache, name="probe.txt", url=f"{staged_url}/probe.txt"),
                preview_bytes=8,
            )
            redirected = probe_url_resource(
                ResourceRequest(source="test-redirect", target_dir=cache, name="redirect.txt", url=f"{staged_url}/redirect.txt")
            )
            too_large_probe = probe_url_resource(
                ResourceRequest(source="test-probe-large", target_dir=cache, name="large.bin", url=f"{staged_url}/large.bin", max_bytes=5)
            )
            staged_policy_probe = acquire_resource_with_policy(
                ResourceRequest(source="test-policy-probe", target_dir=cache, name="probe.txt", url=f"{staged_url}/probe.txt"),
                intent=ResourceIntent.DOCUMENTATION_LOOKUP,
                stage=ResourceStage.PROBE,
            )
            staged_policy_preview = acquire_resource_with_policy(
                ResourceRequest(source="test-policy-preview", target_dir=cache, name="probe.txt", url=f"{staged_url}/probe.txt"),
                intent=ResourceIntent.EXPLICIT_USER_URL,
                stage=ResourceStage.PREVIEW,
            )
            cli_probe_stdout = io.StringIO()
            with contextlib.redirect_stdout(cli_probe_stdout):
                cli_probe_code = resource_cli_main(["probe-url", f"{staged_url}/probe.txt", "--json", "--no-log"])
            cli_preview_stdout = io.StringIO()
            with contextlib.redirect_stdout(cli_preview_stdout):
                cli_preview_code = resource_cli_main(
                    ["preview-url", f"{staged_url}/probe.txt", "--preview-bytes", "8", "--json", "--no-log"]
                )
            cli_stage_stdout = io.StringIO()
            with contextlib.redirect_stdout(cli_stage_stdout):
                cli_stage_code = resource_cli_main(
                    [
                        "acquire",
                        "--intent",
                        ResourceIntent.DOCUMENTATION_LOOKUP,
                        "--stage",
                        ResourceStage.PROBE,
                        "--url",
                        f"{staged_url}/probe.txt",
                        "--json",
                        "--no-log",
                    ]
                )
        assert_ok(probe.ok and probe.decision == "probed", probe.error)
        assert_ok((probe.metadata or {}).get("method") == "HEAD", "probe should use HEAD when supported")
        assert_ok((probe.metadata or {}).get("content_length") == len(b"hello staged resource preview"), "probe content length missing")
        assert_ok(head_fallback.ok, head_fallback.error)
        assert_ok((head_fallback.metadata or {}).get("method") == "GET", "probe should fall back to GET when HEAD unsupported")
        assert_ok(preview.ok and preview.decision == "previewed", preview.error)
        assert_ok((preview.metadata or {}).get("preview_text") == "hello st", "preview text mismatch")
        assert_ok((preview.metadata or {}).get("preview_truncated") is True, "preview truncation not recorded")
        assert_ok(redirected.ok and (redirected.metadata or {}).get("redirected") is True, "redirect not recorded")
        assert_ok(not too_large_probe.ok, "large content-length should fail before download")
        assert_ok((too_large_probe.metadata or {}).get("error_type") == "content_length_too_large", "large probe error type missing")
        assert_ok(staged_policy_probe.ok and staged_policy_probe.decision == "probed", staged_policy_probe.error)
        assert_ok(staged_policy_probe.policy_name == "documentation_lookup_v1", "staged policy probe missing policy")
        assert_ok(staged_policy_preview.ok and staged_policy_preview.decision == "previewed", staged_policy_preview.error)
        assert_ok(cli_probe_code == 0, "resource_cli probe-url failed")
        assert_ok(json.loads(cli_probe_stdout.getvalue())["decision"] == "probed", "resource_cli probe decision missing")
        assert_ok(cli_preview_code == 0, "resource_cli preview-url failed")
        assert_ok(json.loads(cli_preview_stdout.getvalue())["metadata"]["preview_truncated"], "resource_cli preview truncation missing")
        assert_ok(cli_stage_code == 0, "resource_cli acquire --stage probe failed")
        assert_ok(json.loads(cli_stage_stdout.getvalue())["policy_name"] == "documentation_lookup_v1", "resource_cli stage policy missing")
        results["url_probe"] = "ok"
        results["url_preview"] = "ok"
        results["url_probe_head_fallback"] = "ok"
        results["url_probe_redirect"] = "ok"
        results["url_probe_large_rejection"] = "ok"
        results["resource_cli_stages"] = "ok"

        blocked_scheme = acquire_resource_with_policy(
            ResourceRequest(source="test-file-url", target_dir=cache, name="blocked.txt", url="file:///not-allowed.txt"),
            intent=ResourceIntent.EXPLICIT_ATTACHMENT,
        )
        assert_ok(not blocked_scheme.ok, "file URL should be blocked")
        assert_ok(blocked_scheme.decision == "blocked", "file URL block decision missing")
        assert_ok(blocked_scheme.error == "unsupported_url_scheme", "file URL error mismatch")
        assert_ok("unsupported_scheme" in blocked_scheme.risk_flags, "file URL risk flag missing")
        results["policy_file_url_blocked"] = "ok"

        unknown = acquire_resource_with_policy(
            ResourceRequest(source="test-unknown", target_dir=cache, name="unknown"),
            intent=ResourceIntent.UNKNOWN,
        )
        assert_ok(not unknown.ok, "unknown resource should not be acquired")
        assert_ok(unknown.decision == "blocked", "unknown resource block decision missing")
        assert_ok(unknown.policy_name == "unknown_resource_v1", "unknown policy name missing")
        results["policy_unknown_blocked"] = "ok"

        package_dependency = acquire_resource_with_policy(
            ResourceRequest(source="test-package-dependency", target_dir=cache, name="package-dependency"),
            intent=ResourceIntent.PACKAGE_DEPENDENCY,
        )
        assert_ok(not package_dependency.ok, "package dependency should not auto-acquire")
        assert_ok(package_dependency.decision == "deferred", "package dependency should be deferred")
        assert_ok(package_dependency.policy_name == "package_dependency_v1", "package dependency policy missing")
        results["policy_package_dependency_deferred"] = "ok"

        external_dependency = acquire_resource_with_policy(
            ResourceRequest(source="test-external-dependency", target_dir=cache, name="external-dependency"),
            intent=ResourceIntent.EXTERNAL_DEPENDENCY,
        )
        assert_ok(not external_dependency.ok, "external dependency should not auto-acquire without classification")
        assert_ok(external_dependency.decision == "deferred", "external dependency should be deferred")
        assert_ok(external_dependency.policy_name == "external_dependency_v1", "external dependency policy missing")
        results["policy_external_dependency_deferred"] = "ok"

        with local_http_server(root) as base_url:
            not_found = acquire_url_resource(
                ResourceRequest(
                    source="test-404",
                    target_dir=cache,
                    name="missing.txt",
                    url=f"{base_url}/missing.txt",
                    retries=1,
                    retry_delay_seconds=0,
                )
            )
        assert_ok(not not_found.ok, "404 should fail")
        assert_ok((not_found.metadata or {}).get("error_type") == "http_status", "404 error type missing")
        assert_ok((not_found.metadata or {}).get("attempt") == 1, "terminal 404 should not retry")
        not_found_strategy = (not_found.metadata or {}).get("resource_strategy", {})
        assert_ok(not_found_strategy.get("failure_class") == "http_terminal", "404 should be terminal http failure")
        assert_ok(not_found_strategy.get("retry_allowed") is False, "terminal 404 should not allow retry")
        results["http_status_error"] = "ok"

        with flaky_http_server() as (base_url, state):
            retry_result = acquire_url_resource(
                ResourceRequest(
                    source="test-retry",
                    target_dir=cache,
                    name="retry.txt",
                    url=f"{base_url}/retry.txt",
                    retries=1,
                    retry_delay_seconds=0,
                )
            )
        assert_ok(retry_result.ok, retry_result.error)
        assert_ok(state["count"] == 2, "retry did not make second request")
        assert_ok((retry_result.metadata or {}).get("attempt") == 2, "retry success attempt not recorded")
        assert_ok((retry_result.metadata or {}).get("retry_budget") == 2, "retry success budget not recorded")
        assert_ok((retry_result.metadata or {}).get("retry_budget_exhausted") is True, "retry budget exhaustion marker missing")
        retry_health = (retry_result.metadata or {}).get("download_health", {})
        assert_ok(retry_health.get("bytes_read") == len(b"retry ok"), "download health should expose bytes read")
        assert_ok(retry_health.get("next_action") == "keep_selected_route", "normal download should keep route")
        results["retry_success"] = "ok"

        with staged_http_server() as base_url:
            slow_health_result = acquire_url_resource(
                ResourceRequest(
                    source="test-slow-health",
                    target_dir=cache,
                    name="slow.txt",
                    url=f"{base_url}/probe.txt",
                    retries=0,
                    retry_delay_seconds=0,
                    metadata={"min_speed_bytes_per_sec": 10**12, "slow_window_seconds": 0},
                )
            )
        assert_ok(slow_health_result.ok, slow_health_result.error)
        slow_health = (slow_health_result.metadata or {}).get("download_health", {})
        assert_ok(slow_health.get("slow") is True, "slow download should be detected by policy threshold")
        assert_ok(
            slow_health.get("next_action") == "try_faster_route_or_background_download",
            "slow download should suggest route/background recovery",
        )
        results["slow_download_health"] = "ok"

        too_large = acquire_bytes_resource(
            source="test-large",
            data=b"123456",
            target_dir=cache,
            name="large.bin",
            max_bytes=5,
        )
        assert_ok(not too_large.ok and "larger" in too_large.error, "large rejection failed")
        results["large_file_rejection"] = "ok"

        no_limit = acquire_bytes_resource(
            source="test-no-limit",
            data=b"123456",
            target_dir=cache,
            name="no-limit.bin",
        )
        assert_ok(no_limit.ok and no_limit.size == 6, "no max_bytes should allow resource")
        results["no_max_bytes_required"] = "ok"

        sha_mismatch = acquire_local_resource(
            ResourceRequest(
                source="test-sha",
                target_dir=cache,
                name="sample.txt",
                local_path=source,
                expected_sha256="0" * 64,
            )
        )
        assert_ok(not sha_mismatch.ok and sha_mismatch.error == "sha256 mismatch", "sha mismatch rejection failed")
        results["sha256_mismatch_rejection"] = "ok"

        second = acquire_local_resource(
            ResourceRequest(source="test-cache", target_dir=cache, name="sample.txt", local_path=source)
        )
        assert_ok(second.ok and second.cache_hit, "cache hit not detected")
        results["cache_hit"] = "ok"

        cli_cache = root / "cli-cache"
        before_stdout = io.StringIO()
        with contextlib.redirect_stdout(before_stdout):
            code = resource_cli_main(
                [
                    "--json",
                    "--no-log",
                    "fetch-file",
                    str(source),
                    "--target-dir",
                    str(cli_cache),
                    "--sha256",
                    digest,
                ]
            )
        assert_ok(code == 0, "resource_cli fetch-file failed")
        fetch_file_before_payload = json.loads(before_stdout.getvalue())
        assert_ok(fetch_file_before_payload["ok"], "resource_cli global --json failed")
        assert_ok(fetch_file_before_payload["intent"] == ResourceIntent.EXPLICIT_LOCAL_FILE, "legacy fetch-file intent missing")
        assert_ok(fetch_file_before_payload["policy_name"] == "explicit_local_file_v1", "legacy fetch-file policy missing")
        assert_ok(fetch_file_before_payload["resource_kind"] == "local_file", "legacy fetch-file kind missing")
        assert_ok((fetch_file_before_payload["metadata"] or {}).get("legacy_command") is True, "legacy fetch-file marker missing")
        after_stdout = io.StringIO()
        with contextlib.redirect_stdout(after_stdout):
            code = resource_cli_main(
                [
                    "fetch-file",
                    str(source),
                    "--target-dir",
                    str(cli_cache),
                    "--sha256",
                    digest,
                    "--json",
                    "--no-log",
                ]
            )
        assert_ok(code == 0, "resource_cli fetch-file trailing --json failed")
        assert_ok(json.loads(after_stdout.getvalue())["ok"], "resource_cli trailing --json failed")
        (cli_cache / "incomplete.part").write_text("partial", encoding="utf-8")
        (cli_cache / "keep.bin").write_text("complete", encoding="utf-8")
        inspect_stdout = io.StringIO()
        with contextlib.redirect_stdout(inspect_stdout):
            code = resource_cli_main(["--json", "--no-log", "inspect-cache", "--target-dir", str(cli_cache), "--limit", "1"])
        assert_ok(code == 0, "resource_cli inspect-cache failed")
        inspect_payload = json.loads(inspect_stdout.getvalue())
        assert_ok(inspect_payload["returned_count"] == 1 and inspect_payload["truncated"], "inspect-cache JSON budget missing")
        clean_stdout = io.StringIO()
        with contextlib.redirect_stdout(clean_stdout):
            code = resource_cli_main([
                "--json", "--no-log", "clean-cache", "--target-dir", str(cli_cache),
                "--older-than-days", "0", "--transient-only", "--limit", "1", "--dry-run",
            ])
        assert_ok(code == 0, "resource_cli clean-cache dry-run failed")
        clean_payload = json.loads(clean_stdout.getvalue())
        assert_ok(clean_payload["candidate_count"] == 1, "transient-only cleanup selected completed resources")
        assert_ok(clean_payload["candidates"][0]["relative_path"] == "incomplete.part", "transient cleanup candidate mismatch")
        results["resource_cli"] = "ok"

        strategy_log = root / "resource-strategy.jsonl"
        strategy_log.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "ok": False,
                            "source": "test",
                            "name": "inline",
                            "decision": "deferred",
                            "intent": ResourceIntent.INLINE_URL_CANDIDATE,
                            "resource_kind": "url",
                            "error": "resource acquisition is deferred by policy",
                            "metadata": {"stage": ResourceStage.DISCOVER},
                            "risk_flags": ["auto_acquire_disabled"],
                        },
                        sort_keys=True,
                    ),
                    json.dumps(
                        {
                            "ok": True,
                            "source": "test",
                            "name": "docs",
                            "decision": "probed",
                            "intent": ResourceIntent.DOCUMENTATION_LOOKUP,
                            "resource_kind": "url",
                            "metadata": {"stage": ResourceStage.PROBE, "http_status": 200},
                        },
                        sort_keys=True,
                    ),
                    json.dumps(
                        {
                            "ok": False,
                            "source": "test",
                            "name": "pkg",
                            "decision": "deferred",
                            "intent": ResourceIntent.PACKAGE_DEPENDENCY,
                            "resource_kind": "url",
                            "metadata": {"stage": ResourceStage.AUDIT},
                        },
                        sort_keys=True,
                    ),
                    json.dumps(
                        {
                            "ok": True,
                            "source": "test",
                            "name": "legacy",
                            "intent": "",
                            "resource_kind": "",
                            "metadata": {"cli_command": "fetch-url"},
                        },
                        sort_keys=True,
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        review = build_resource_strategy_review(read_resource_log(strategy_log, limit=20))
        assert_ok(review["ok"] and review["mode"] == "read_only", "strategy review is not read-only")
        assert_ok(review["writes_files"] is False and review["executes_tools"] is False, "strategy review side-effect flags changed")
        assert_ok(review["proposal_count"] >= 3, "strategy review did not produce expected proposals")
        assert_ok(any("no_tool_install" in boundary for boundary in review["safety_boundaries"]), "strategy review safety boundary missing")
        strategy_stdout = io.StringIO()
        with contextlib.redirect_stdout(strategy_stdout):
            code = resource_cli_main(["strategy-review", "--resource-log", str(strategy_log), "--limit", "20", "--json"])
        assert_ok(code == 0, "resource_cli strategy-review failed")
        cli_review = json.loads(strategy_stdout.getvalue())
        assert_ok(cli_review["mode"] == "read_only", "resource_cli strategy-review mode changed")
        strategy_filtered_stdout = io.StringIO()
        with contextlib.redirect_stdout(strategy_filtered_stdout):
            code = resource_cli_main(
                ["strategy-review", "--resource-log", str(strategy_log), "--limit", "20", "--hide-legacy", "--json"]
            )
        assert_ok(code == 0, "resource_cli strategy-review --hide-legacy failed")
        filtered_review = json.loads(strategy_filtered_stdout.getvalue())
        assert_ok(filtered_review["filters"]["hide_legacy"] is True, "strategy review filter metadata missing")
        assert_ok(
            "legacy_cli_fetch_url" not in filtered_review["summary"]["intents"],
            "strategy review hide-legacy did not filter legacy CLI entries",
        )
        docs_classification = classify_url_semantics("https://docs.python.org/3/library/json.html")
        assert_ok(docs_classification["recommended_intent"] == ResourceIntent.DOCUMENTATION_LOOKUP, "docs URL classification failed")
        pkg_classification = classify_url_semantics("https://pypi.org/project/ruff/")
        assert_ok(pkg_classification["recommended_intent"] == ResourceIntent.PACKAGE_DEPENDENCY, "package URL classification failed")
        inline_classification = classify_url_semantics("https://example.com/page", context="inline_text")
        assert_ok(inline_classification["recommended_intent"] == ResourceIntent.INLINE_URL_CANDIDATE, "inline URL classification failed")
        classify_stdout = io.StringIO()
        with contextlib.redirect_stdout(classify_stdout):
            code = resource_cli_main(["classify-url", "https://docs.python.org/3/library/json.html", "--json"])
        assert_ok(code == 0, "resource_cli classify-url failed")
        assert_ok(
            json.loads(classify_stdout.getvalue())["recommended_intent"] == ResourceIntent.DOCUMENTATION_LOOKUP,
            "resource_cli classify-url JSON failed",
        )
        docs_route = route_resource(url="https://docs.python.org/3/library/json.html", task="look up documentation")
        assert_ok(
            docs_route.read_only and docs_route.primary_tool == "markitdown" and docs_route.recommended_stage == ResourceStage.PREVIEW,
            "a known docs URL should return bounded page content instead of restarting library discovery",
        )
        assert_ok(docs_route.intent == ResourceIntent.DOCUMENTATION_LOOKUP, "docs route intent mismatch")
        microsoft_route = route_resource(url="https://learn.microsoft.com/powershell/", task="Microsoft docs")
        assert_ok(microsoft_route.primary_tool == "microsoftdocs", "Microsoft docs route should prefer microsoftdocs")
        github_route = route_resource(url="https://github.com/owner/repo/releases/tag/v1.0.0")
        assert_ok(github_route.primary_tool == "github", "GitHub route should prefer github MCP")
        assert_ok("resource_cli" in github_route.secondary_tools, "GitHub artifact route should keep resource_cli materialization fallback")
        browser_route = route_resource(url="https://example.com", task="capture screenshot and console evidence")
        assert_ok(browser_route.primary_tool == "playwright", "browser evidence route should prefer playwright")
        chrome_route = route_resource(url="https://example.com", task="inspect current Chrome DevTools page")
        assert_ok(chrome_route.primary_tool == "chrome-devtools", "explicit Chrome DevTools route should prefer chrome-devtools")
        markdown_plain_url_route = route_resource(url="https://example.com", task="convert page to markdown")
        assert_ok(markdown_plain_url_route.primary_tool == "markitdown", "plain URL markdown conversion should prefer markitdown")
        target_docs_route = route_resource(target="python", intent=ResourceIntent.DOCUMENTATION_LOOKUP, task="json module documentation")
        assert_ok(target_docs_route.primary_tool == "context7", "target-only documentation route should prefer context7")
        paper_route = route_resource(target="中国 人工智能 论文 PDF 开放获取", task="查找并下载一篇关于人工智能的中国区论文", need_materialization=True)
        assert_ok(paper_route.primary_tool == "resource_router", "academic paper discovery should stay in resource-layer source selection")
        assert_ok(paper_route.intent == ResourceIntent.EXTERNAL_DEPENDENCY, "academic paper discovery intent mismatch")
        assert_ok("academic_source_selection" in paper_route.risk_flags, "academic paper route should expose source-selection risk")
        assert_ok("package_manager" not in (paper_route.primary_tool, *paper_route.secondary_tools), "academic paper route must not use package manager")
        assert_ok("context7" not in (paper_route.primary_tool, *paper_route.secondary_tools), "academic paper route must not use Context7 docs lookup")
        wallpaper_route = route_resource(target="wallpaper pdf", task="download wallpaper pdf", need_materialization=True)
        assert_ok("academic_source_selection" not in wallpaper_route.risk_flags, "wallpaper must not trigger academic paper route")
        image_route = route_resource(
            target="华为总部 Huawei headquarters",
            task="下载十张关于华为总部的不同图片",
            intent=ResourceIntent.EXTERNAL_DEPENDENCY,
            need_materialization=True,
        )
        assert_ok(image_route.primary_tool == "resource_router", "image discovery without URL should stay in resource source selection")
        assert_ok("image_source_selection" in image_route.risk_flags, "image route should expose image source selection risk")
        assert_ok("package_manager" not in (image_route.primary_tool, *image_route.secondary_tools), "brand image route must not use package manager")
        generic_route = route_resource(url="https://example.com/page")
        assert_ok(generic_route.primary_tool == "resource_cli", "generic URL route should use resource_cli preview")
        assert_ok(generic_route.recommended_stage == ResourceStage.PREVIEW, "generic URL route should not materialize by default")
        materialize_route = route_resource(url="https://example.com/file.zip", need_materialization=True)
        assert_ok(materialize_route.primary_tool == "resource_cli", "explicit materialization route should use resource_cli")
        assert_ok(materialize_route.recommended_stage == ResourceStage.MATERIALIZE, "materialization route stage mismatch")
        generic_materialize_route = route_resource(url="https://example.com/page", need_materialization=True)
        assert_ok(
            generic_materialize_route.intent == ResourceIntent.EXPLICIT_USER_URL,
            "generic materialization should infer explicit_user_url intent",
        )
        ambiguous_route = route_resource(url="https://example.com", path=str(source))
        assert_ok(not ambiguous_route.ok and "ambiguous_reference" in ambiguous_route.risk_flags, "ambiguous route should be blocked")
        route_stdout = io.StringIO()
        with contextlib.redirect_stdout(route_stdout):
            code = resource_cli_main(["route", "--url", "https://docs.python.org/3/library/json.html", "--task", "docs", "--json"])
        assert_ok(code == 0, "resource_cli route failed")
        route_payload = json.loads(route_stdout.getvalue())
        assert_ok(route_payload["read_only"] is True, "resource_cli route must be read-only")
        assert_ok(
            route_payload["primary_tool"] == "markitdown" and route_payload["recommended_stage"] == ResourceStage.PREVIEW,
            "resource_cli known docs route should return bounded content",
        )
        route_target_stdout = io.StringIO()
        with contextlib.redirect_stdout(route_target_stdout):
            route_target_code = resource_cli_main(["route", "--target", "python", "--task", "json module documentation", "--intent", ResourceIntent.DOCUMENTATION_LOOKUP, "--json"])
        assert_ok(route_target_code == 0, "resource_cli route target failed")
        route_target_payload = json.loads(route_target_stdout.getvalue())
        assert_ok(route_target_payload["primary_tool"] == "context7", "resource_cli target route should prefer context7")
        paper_delegation = build_delegation(
            task="查找并下载一篇关于人工智能的中国区论文",
            target="中国 人工智能 论文 PDF 开放获取",
            need_materialization=True,
            allow_filesystem_write=True,
        )
        assert_ok(paper_delegation["request"]["intent"] == ResourceIntent.EXTERNAL_DEPENDENCY, "paper delegation intent mismatch")
        assert_ok(paper_delegation["route"]["primary_tool"] == "resource_router", "paper delegation should use source-selection route")
        assert_ok(
            paper_delegation["request"]["metadata"].get("resource_kind_hint") == "academic_paper",
            "paper delegation should expose academic_paper hint",
        )
        paper_source_plan = candidate_source_plan(paper_delegation["request"], paper_delegation["route"])
        assert_ok(paper_source_plan["resource_kind"] == "academic_paper", "paper source strategy should classify academic paper")
        assert_ok(paper_source_plan["candidates"][0]["id"] == "academic_arxiv", "paper source strategy should prefer arXiv first")
        assert_ok(
            any(item["id"] == "academic_openalex" for item in paper_source_plan["candidates"]),
            "paper source strategy should include metadata index fallback",
        )
        openai_docs_request = {
            "task": "Find OpenAI official documentation for Codex plugins and Sites; do not use Microsoft Docs",
            "target": "OpenAI Codex product documentation",
            "intent": ResourceIntent.DOCUMENTATION_LOOKUP,
            "metadata": {
                "resource_kind_hint": "documentation",
                "source_domains": ["openai.com", "help.openai.com", "developers.openai.com"],
            },
        }
        openai_docs_route = route_resource(
            target=openai_docs_request["target"],
            task=openai_docs_request["task"],
            intent=ResourceIntent.DOCUMENTATION_LOOKUP,
        )
        openai_docs_plan = candidate_source_plan(openai_docs_request, openai_docs_route.to_dict())
        assert_ok(openai_docs_plan["resource_kind"] == "documentation", "OpenAI docs should remain a documentation request")
        assert_ok(openai_docs_plan["candidates"][0]["owner_tool"] == "openai-docs", "OpenAI official docs should use the official Docs MCP owner")
        assert_ok(
            openai_docs_plan["classification_evidence"]["official_domains"] == ["openai.com", "help.openai.com", "developers.openai.com"],
            "OpenAI official domains should survive source planning",
        )
        assert_ok(
            openai_docs_plan["execution_capability"]["registered_owner_adapter"] == "openai-docs",
            "OpenAI docs should expose the dedicated official owner adapter",
        )
        assert_ok(
            [item["owner_tool"] for item in openai_docs_plan["candidates"]] == ["openai-docs", "generic_search"],
            "OpenAI docs must fall forward only to official-domain generic search",
        )
        microsoft_docs_plan = candidate_source_plan(
            {"task": "Microsoft PowerShell official documentation", "target": "learn.microsoft.com PowerShell docs", "intent": ResourceIntent.DOCUMENTATION_LOOKUP},
            {"primary_tool": "microsoftdocs", "intent": ResourceIntent.DOCUMENTATION_LOOKUP},
        )
        assert_ok(microsoft_docs_plan["candidates"][0]["owner_tool"] == "microsoftdocs", "Microsoft docs should retain the Microsoft owner")
        langchain_docs_plan = candidate_source_plan(
            {"task": "LangChain framework API documentation", "target": "LangChain SDK docs", "intent": ResourceIntent.DOCUMENTATION_LOOKUP},
            {"primary_tool": "context7", "intent": ResourceIntent.DOCUMENTATION_LOOKUP},
        )
        assert_ok(langchain_docs_plan["candidates"][0]["owner_tool"] == "context7", "library docs should retain Context7")
        stripe_docs_plan = candidate_source_plan(
            {
                "task": "Stripe official API documentation",
                "target": "docs.stripe.com API",
                "intent": ResourceIntent.DOCUMENTATION_LOOKUP,
                "metadata": {"resource_kind_hint": "documentation", "source_domains": ["docs.stripe.com"]},
            },
            {"primary_tool": "resource_router", "intent": ResourceIntent.DOCUMENTATION_LOOKUP},
        )
        assert_ok(stripe_docs_plan["candidates"][0]["owner_tool"] == "generic_search", "non-Microsoft vendor docs should use official-domain search")
        ambiguous_docs_plan = candidate_source_plan(
            {"task": "find product usage documentation", "target": "product usage docs", "intent": ResourceIntent.DOCUMENTATION_LOOKUP},
            {"primary_tool": "resource_router", "intent": ResourceIntent.DOCUMENTATION_LOOKUP},
        )
        assert_ok(ambiguous_docs_plan["candidates"][0]["owner_tool"] == "generic_search", "ambiguous product docs should not default to a vendor-specific owner")
        docs_source_result = execute_source_selection(openai_docs_request, openai_docs_route.to_dict(), timeout=5)
        assert_ok(docs_source_result["status"] == "degraded", "documentation source selection should yield a recoverable owner continuation")
        assert_ok(docs_source_result["available_owner_adapter"] == "openai-docs", "documentation source selection should name the dedicated OpenAI owner")
        assert_ok(docs_source_result["next_action"] == "continue_resource_layer_with_registered_documentation_owner", "documentation source selection should continue inside the resource layer")
        wallpaper_source_plan = candidate_source_plan(
            {"task": "download wallpaper pdf", "target": "wallpaper pdf", "need_materialization": True},
            wallpaper_route.to_dict(),
        )
        assert_ok(wallpaper_source_plan["resource_kind"] != "academic_paper", "wallpaper source strategy must not classify as paper")
        image_source_plan = candidate_source_plan(
            {"task": "下载十张关于华为总部的不同图片", "target": "华为总部 Huawei headquarters", "need_materialization": True},
            image_route.to_dict(),
        )
        assert_ok(image_source_plan["resource_kind"] == "image", "image source strategy should classify image requests")
        assert_ok(image_source_plan["candidates"][0]["id"] == "image_webpage_assets", "image source strategy should prefer source page parsing")
        image_page = root / "image-page.html"
        (root / "hero.jpg").write_bytes(b"fake image payload")
        (root / "logo.png").write_bytes(b"logo")
        image_page.write_text(
            '<html><head><title>Huawei Headquarters Gallery</title></head>'
            '<body><img src="/hero.jpg"><img src="/logo.png"><img src="/default_img.jpg"></body></html>',
            encoding="utf-8",
        )
        with local_http_server(root) as image_base_url:
            image_selection = execute_source_selection(
                {
                    "task": "下载华为总部图片候选",
                    "target": "华为总部 Huawei headquarters",
                    "url": f"{image_base_url}/image-page.html",
                    "need_materialization": True,
                    "metadata": {"source_selection_only": True, "candidate_review_before_materialization": True},
                },
                image_route.to_dict(),
                timeout=5,
            )
        assert_ok(image_selection["ok"] is True, "image source executor should return webpage image candidates")
        assert_ok(image_selection["resource_kind"] == "image", "image source executor should mark image kind")
        assert_ok(image_selection["candidate_review_required"] is True, "image source executor should require candidate review when requested")
        assert_ok(any(str(item.get("url", "")).endswith("/hero.jpg") for item in image_selection["candidates"]), "image source executor should include real content image")
        assert_ok(not any("logo" in str(item.get("url", "")).lower() for item in image_selection["candidates"]), "image source executor should filter logo noise")
        collection_page = root / "collection-page.html"
        (root / "collection-1.jpg").write_bytes(b"collection image 1")
        (root / "collection-2.jpg").write_bytes(b"collection image 2")
        collection_page.write_text(
            '<html><head><title>Huawei Headquarters Gallery</title></head><body>'
            '<img src="/aaa-missing.jpg"><img src="/collection-1.jpg"><img src="/collection-2.jpg">'
            '<img src="/logo.png">'
            '</body></html>',
            encoding="utf-8",
        )
        with local_http_server(root) as collection_base_url:
            collection_result = collect_resources(
                task="下载两张关于华为总部的不同图片",
                target="华为总部 Huawei headquarters",
                count=2,
                source_page=f"{collection_base_url}/collection-page.html",
                target_dir=str(root / "collection-output"),
                candidate_limit=4,
                batch_size=1,
                timeout=5,
                event_log=root / "collection-events.jsonl",
                receipt_log=root / "collection-receipts.jsonl",
                resource_log=root / "collection-resource.jsonl",
                store_root=root / "collection-store",
            )
        assert_ok(collection_result["ok"] is True, "resource collection should backfill failed image candidates")
        assert_ok(collection_result["completed_count"] == 2, "resource collection should satisfy requested count")
        assert_ok(collection_result["attempted_candidate_count"] >= 3, "resource collection should continue after a failed candidate")
        assert_ok(all(Path(item["artifact_path"]).exists() for item in collection_result["artifacts"]), "resource collection artifacts should exist")
        collection_validate = validate_collection_acquirer()
        assert_ok(collection_validate["ok"] is True, "resource collection validate should pass")
        document_page = root / "document-page.html"
        (root / "manual-1.pdf").write_bytes(b"%PDF-1.4 manual 1")
        (root / "manual-2.pdf").write_bytes(b"%PDF-1.4 manual 2")
        document_page.write_text(
            '<html><head><title>Manual Downloads</title></head><body>'
            '<a href="/aaa-manual-missing.pdf">manual missing</a><a href="/manual-1.pdf">manual 1</a><a href="/manual-2.pdf">manual 2</a>'
            '</body></html>',
            encoding="utf-8",
        )
        with local_http_server(root) as document_base_url:
            document_collection = collect_resources(
                task="下载两份 PDF 手册",
                target="manual pdf",
                count=2,
                resource_kind="document",
                source_page=f"{document_base_url}/document-page.html",
                target_dir=str(root / "document-output"),
                candidate_limit=4,
                batch_size=1,
                timeout=5,
                event_log=root / "document-events.jsonl",
                receipt_log=root / "document-receipts.jsonl",
                resource_log=root / "document-resource.jsonl",
                store_root=root / "document-store",
            )
        assert_ok(document_collection["ok"] is True, "resource collection should support non-image document assets")
        assert_ok(document_collection["resource_kind"] == "document", "document collection should preserve resource kind")
        assert_ok(document_collection["completed_count"] == 2, "document collection should satisfy requested count")
        assert_ok(document_collection["attempted_candidate_count"] >= 3, "document collection should backfill failed candidates")
        original_source_open_json = resource_source_executor._open_json
        try:
            def fake_platform_open_json(url, *, timeout=20):
                if "api.github.com/repos/example/tool/releases/latest" in url:
                    return {
                        "tag_name": "v1.0.0",
                        "html_url": "https://github.com/example/tool/releases/tag/v1.0.0",
                        "assets": [
                            {"name": "tool-windows.zip", "browser_download_url": "https://github.com/example/tool/releases/download/v1.0.0/tool-windows.zip"}
                        ],
                    }
                if "huggingface.co/api/datasets/example/dataset" in url:
                    return {
                        "id": "example/dataset",
                        "cardData": {"license": "mit"},
                        "siblings": [
                            {"rfilename": "data/train.parquet"},
                            {"rfilename": "README.md"},
                        ],
                    }
                if "zenodo.org/api/records/12345" in url:
                    return {
                        "metadata": {"title": "Example archive", "license": {"id": "cc-by-4.0"}},
                        "links": {"html": "https://zenodo.org/records/12345"},
                        "files": [
                            {"key": "dataset.csv", "links": {"self": "https://zenodo.org/api/records/12345/files/dataset.csv/content"}},
                        ],
                    }
                return original_source_open_json(url, timeout=timeout)

            resource_source_executor._open_json = fake_platform_open_json
            github_release_selection = execute_source_selection(
                {
                    "task": "下载 GitHub Release 资产",
                    "target": "https://github.com/example/tool/releases/latest",
                    "need_materialization": True,
                    "metadata": {"resource_kind_hint": "github_project", "source_selection_only": True},
                },
                {"primary_tool": "resource_router", "intent": "external_dependency", "source_kind": "unknown"},
                timeout=5,
            )
            hf_selection = execute_source_selection(
                {
                    "task": "下载 Hugging Face 数据集文件",
                    "target": "https://huggingface.co/datasets/example/dataset",
                    "need_materialization": True,
                    "metadata": {"resource_kind_hint": "dataset", "source_selection_only": True},
                },
                {"primary_tool": "resource_router", "intent": "external_dependency", "source_kind": "unknown"},
                timeout=5,
            )
            zenodo_selection = execute_source_selection(
                {
                    "task": "下载 Zenodo 数据集文件",
                    "target": "https://zenodo.org/records/12345",
                    "need_materialization": True,
                    "metadata": {"resource_kind_hint": "dataset", "source_selection_only": True},
                },
                {"primary_tool": "resource_router", "intent": "external_dependency", "source_kind": "unknown"},
                timeout=5,
            )
        finally:
            resource_source_executor._open_json = original_source_open_json
        assert_ok(github_release_selection["ok"] is True, "GitHub release source adapter should return asset candidates")
        assert_ok(github_release_selection["selected_source_id"] == "github_release_assets", "GitHub release source id mismatch")
        assert_ok(hf_selection["ok"] is True, "Hugging Face source adapter should return file candidates")
        assert_ok(any("train.parquet" in item["url"] for item in hf_selection["candidates"]), "Hugging Face candidate URL missing")
        assert_ok(zenodo_selection["ok"] is True, "Zenodo source adapter should return file candidates")
        assert_ok(zenodo_selection["selected_source_id"] == "zenodo_files", "Zenodo source id mismatch")
        candidate_quality_validate = validate_candidate_quality()
        assert_ok(candidate_quality_validate["ok"] is True, "candidate quality validate should pass")
        quality_ranked = rank_candidates(
            [
                {"source_id": "webpage_download_assets", "url": "https://example.test/file.pdf", "score": 0.9},
                {"source_id": "zenodo_files", "url": "https://example.test/file.pdf", "score": 0.6, "license_hint": "cc-by-4.0", "estimated_size": 100},
            ],
            resource_kind="document",
            constraints={"require_open_license": True, "max_bytes": 1000},
        )
        assert_ok(quality_ranked[0]["source_id"] == "zenodo_files", "trusted open licensed candidate should outrank generic unknown candidate")
        quality_usable, quality_skipped = filter_ranked_candidates(
            [
                {"source_id": "webpage_download_assets", "url": "https://example.test/video.mp4", "score": 1.0},
                {"source_id": "webpage_download_assets", "url": "https://example.test/manual.pdf", "score": 0.4},
                {"source_id": "github_release_assets", "url": "https://example.test/tool.zip", "score": 0.8, "estimated_size": 2000},
            ],
            resource_kind="document",
            constraints={"max_bytes": 1000},
        )
        assert_ok(any("manual.pdf" in item["url"] for item in quality_usable), "quality filtering should keep valid document candidate")
        assert_ok(len(quality_skipped) == 2, "quality filtering should skip wrong format and over-budget candidates")
        broker_events = root / "broker-events.jsonl"
        broker_receipts = root / "broker-receipts.jsonl"
        broker_cache = root / "broker-cache"
        broker_local = isolated_handle_request(
            ResourceBrokerRequest(
                path=str(source),
                task="copy local resource",
                name="sample.txt",
                intent=ResourceIntent.EXPLICIT_LOCAL_FILE,
                need_materialization=True,
                allow_filesystem_write=True,
                target_dir=str(broker_cache),
            ),
            event_log=broker_events,
            receipt_log=broker_receipts,
        )
        assert_ok(broker_local.ok, "resource broker local request failed")
        assert_ok(Path(broker_local.artifact_path).exists(), "resource broker artifact missing")
        assert_ok(Path(broker_local.manifest_path).exists(), "resource broker manifest missing")
        assert_ok(broker_local.saved_paths.get("artifact") == broker_local.artifact_path, "resource broker artifact save path mismatch")
        assert_ok(broker_local.strategy_plan and broker_local.strategy_plan[-1]["tool"] == "resource_cli", "resource broker strategy plan missing")
        assert_ok(broker_local.progress_events[-1]["stage"] == "reported", "resource broker reported event missing")
        broker_local_progress = progress_for_request(broker_local.request_id, receipt_log=broker_receipts)
        assert_ok(broker_local_progress["status"] == "completed", "resource progress should show completed local request")
        assert_ok(broker_local_progress["is_terminal"] is True, "completed resource progress should be terminal")
        assert_ok(broker_local_progress["status_summary"]["state"] == "completed", "resource progress should expose status summary state")
        assert_ok(broker_local_progress["progress"]["percent"] == 100, "completed resource progress should expose 100 percent")
        assert_ok(broker_local_progress["codex_next_action"] == "consume_resource", "completed progress should tell Codex to consume")
        assert_ok(broker_local_progress["paths"]["artifact"] == broker_local.artifact_path, "resource progress artifact path mismatch")
        with staged_http_server() as broker_staged_url:
            broker_preview = isolated_handle_request(
                ResourceBrokerRequest(
                    url=f"{broker_staged_url}/probe.txt",
                    task="preview a generic URL",
                    intent=ResourceIntent.EXPLICIT_USER_URL,
                ),
                event_log=broker_events,
                receipt_log=broker_receipts,
            )
        assert_ok(broker_preview.ok and broker_preview.result_kind == "preview", "resource broker preview request failed")
        assert_ok(Path(broker_preview.preview_path).exists(), "resource broker preview path missing")
        assert_ok(Path(broker_preview.manifest_path).exists(), "resource broker preview manifest missing")
        with staged_http_server() as broker_download_url:
            broker_download = isolated_handle_request(
                ResourceBrokerRequest(
                    url=f"{broker_download_url}/probe.txt",
                    task="save a generic URL",
                    need_materialization=True,
                    allow_filesystem_write=True,
                    target_dir=str(broker_cache),
                ),
                event_log=broker_events,
                receipt_log=broker_receipts,
            )
        assert_ok(broker_download.ok and broker_download.result_kind == "artifact", "resource broker generic URL materialization failed")
        assert_ok(Path(broker_download.artifact_path).exists(), "resource broker generic URL artifact missing")
        assert_ok(broker_download.route["intent"] == ResourceIntent.EXPLICIT_USER_URL, "broker generic URL intent inference mismatch")
        with staged_http_server() as reused_route_url:
            actual_url = f"{reused_route_url}/probe.txt"
            reused_probe_url = f"{reused_route_url}/other.txt"
            broker_reused_network = isolated_handle_request(
                ResourceBrokerRequest(
                    url=actual_url,
                    task="save URL with reused route evidence",
                    need_materialization=True,
                    allow_filesystem_write=True,
                    target_dir=str(broker_cache),
                    metadata={
                        "network_gateway_plan": {
                            "ok": True,
                            "plan": {
                                "route_mode": "probe_selected_direct",
                                "target_kind": "external",
                                "target": reused_probe_url,
                                "env": {"CODEX_NETWORK_CONTEXT": "test"},
                                "unset_env": ["HTTP_PROXY"],
                            },
                        }
                    },
                ),
                event_log=broker_events,
                receipt_log=broker_receipts,
            )
        assert_ok(broker_reused_network.ok, "broker reused network route request should complete")
        assert_ok(broker_reused_network.network_summary["target"] == actual_url, "network summary target should be request target")
        assert_ok(broker_reused_network.network_summary["route_probe_target"] == reused_probe_url, "network summary should keep route probe target")
        assert_ok(broker_reused_network.network_summary["route_evidence_reused"] is True, "network summary should mark reused route evidence")
        broker_paper_request = ResourceBrokerRequest(
            task="查找并下载一篇关于人工智能的中国区论文",
            target="中国 人工智能 论文 PDF 开放获取",
            intent=ResourceIntent.EXTERNAL_DEPENDENCY,
            need_materialization=True,
            allow_filesystem_write=True,
            metadata={"resource_kind_hint": "academic_paper", "validation_profile": "quick"},
        )
        broker_paper_plan = strategy_plan_for_request(broker_paper_request, route_for_request(broker_paper_request))
        source_strategy_step = broker_paper_plan[0]
        assert_ok(source_strategy_step["tool"] == "resource_source_strategy", "broker source-selection plan should start with source strategy")
        assert_ok(source_strategy_step["executable_by_broker"] is True, "source strategy should be broker-executable")
        assert_ok(
            source_strategy_step["source_strategy"]["candidates"][0]["id"] == "academic_arxiv",
            "broker source-selection plan should expose academic candidates",
        )
        with staged_http_server() as broker_selected_url:
            original_broker_source_selection = resource_broker.execute_source_selection
            try:
                def fake_source_selection(_request, _route, *, timeout=20):
                    return {
                        "ok": True,
                        "status": "completed",
                        "source": "resource_source_executor",
                        "result_kind": "source_selection",
                        "selected_url": f"{broker_selected_url}/probe.txt",
                        "selected_name": "selected-paper.txt",
                        "selected_source_id": "test_source",
                        "candidates": [{"url": f"{broker_selected_url}/probe.txt", "title": "selected-paper.txt"}],
                        "next_action": "materialize_selected_url",
                    }

                resource_broker.execute_source_selection = fake_source_selection
                broker_source_selected = isolated_handle_request(
                    broker_paper_request,
                    event_log=broker_events,
                    receipt_log=broker_receipts,
                    store_root=broker_cache,
                )
            finally:
                resource_broker.execute_source_selection = original_broker_source_selection
        assert_ok(broker_source_selected.ok and broker_source_selected.result_kind == "artifact", "source-selected request should materialize selected URL")
        assert_ok(Path(broker_source_selected.artifact_path).exists(), "source-selected artifact missing")
        assert_ok(
            broker_source_selected.attempts[0]["tool"] == "resource_source_strategy"
            and broker_source_selected.attempts[1]["tool"] == "resource_cli",
            "source-selected request should execute source strategy then resource_cli",
        )
        with staged_http_server() as broker_selected_url:
            original_broker_source_selection = resource_broker.execute_source_selection
            try:
                def fake_source_selection_only(_request, _route, *, timeout=20):
                    return {
                        "ok": True,
                        "status": "completed",
                        "source": "resource_source_executor",
                        "result_kind": "source_selection",
                        "selected_url": f"{broker_selected_url}/probe.txt",
                        "selected_name": "selected-paper.txt",
                        "selected_source_id": "test_source",
                        "candidates": [{"url": f"{broker_selected_url}/probe.txt", "title": "selected-paper.txt"}],
                        "next_action": "materialize_selected_url",
                    }

                resource_broker.execute_source_selection = fake_source_selection_only
                broker_source_selection_only = isolated_handle_request(
                    ResourceBrokerRequest(
                        task="查找一篇关于人工智能的中国区论文候选源",
                        target="中国 人工智能 论文 PDF 开放获取",
                        intent=ResourceIntent.EXTERNAL_DEPENDENCY,
                        need_materialization=True,
                        allow_filesystem_write=True,
                        metadata={
                            "resource_kind_hint": "academic_paper",
                            "validation_profile": "quick",
                            "source_selection_only": True,
                        },
                    ),
                    event_log=broker_events,
                    receipt_log=broker_receipts,
                    store_root=broker_cache,
                )
            finally:
                resource_broker.execute_source_selection = original_broker_source_selection
        assert_ok(
            broker_source_selection_only.ok and broker_source_selection_only.result_kind == "source_selection",
            "source_selection_only request should return source-selection receipt",
        )
        assert_ok(not broker_source_selection_only.artifact_path, "source_selection_only request should not materialize artifact")
        assert_ok(
            [attempt["tool"] for attempt in broker_source_selection_only.attempts] == ["resource_source_strategy"],
            "source_selection_only request should stop after source strategy",
        )
        source_guidance = broker_source_selection_only.codex_guidance
        assert_ok(
            source_guidance.get("codex_next_action") == "review_candidates_and_resubmit_selected_source",
            "source-selection receipt should guide Codex to review candidates and resubmit",
        )
        assert_ok(source_guidance.get("candidate_review_required") is True, "source-selection receipt should require candidate review")
        assert_ok(source_guidance.get("resource_need_satisfied") is False, "source selection alone should not satisfy resource need")
        assert_ok(
            source_guidance.get("refined_request_seed", {}).get("url") == f"{broker_selected_url}/probe.txt",
            "source-selection guidance should expose a selected URL seed for follow-up materialization",
        )
        assert_ok(
            source_guidance.get("refined_request_seed", {}).get("metadata", {}).get("source_selection_only") is None,
            "follow-up materialization seed must remove source_selection_only",
        )
        source_progress = progress_for_manifest(Path(broker_source_selection_only.manifest_path))
        assert_ok(
            source_progress["codex_next_action"] == "review_candidates_and_resubmit_selected_source",
            "source-selection progress should expose candidate-review Codex action",
        )
        assert_ok(
            source_progress["codex_guidance"]["candidate_review_required"] is True,
            "source-selection progress should include candidate guidance",
        )
        broker_docs = isolated_handle_request(
            ResourceBrokerRequest(
                target="python json module",
                task="look up documentation",
                intent=ResourceIntent.DOCUMENTATION_LOOKUP,
            ),
            event_log=broker_events,
            receipt_log=broker_receipts,
        )
        assert_ok(broker_docs.status == "handoff_required", "docs broker request should hand off to owner MCP")
        assert_ok(broker_docs.next_action == "use_codex_current_turn_owner_tool", "docs broker handoff next action mismatch")
        assert_ok(Path(broker_docs.manifest_path).exists(), "docs broker handoff manifest missing")
        assert_ok(broker_docs.network_summary.get("target_kind") == "docs", "docs broker receipt should summarize docs target kind")
        assert_ok(broker_docs.network_summary.get("route_mode"), "docs broker receipt should summarize route mode")
        assert_ok(broker_docs.owner_execution.get("owner_tool") == "context7", "docs broker should expose owner execution tool")
        assert_ok(
            broker_docs.owner_execution.get("permission_boundary") == "owner_tool_required",
            "owner execution contract should preserve owner boundary",
        )
        broker_docs_progress = progress_for_manifest(Path(broker_docs.manifest_path))
        assert_ok(broker_docs_progress["status"] == "handoff_required", "docs progress should expose handoff status")
        assert_ok(broker_docs_progress["owner"]["requires_codex_action"] is True, "docs progress should require Codex owner action")
        assert_ok(broker_docs_progress["owner"]["owner_tool"] == "context7", "docs progress owner tool mismatch")
        assert_ok(broker_docs_progress["network"]["target_kind"] == "docs", "docs progress network target mismatch")
        assert_ok(broker_docs_progress["next_action"] == "call_owner_tool_and_attach_result", "docs progress next action mismatch")
        assert_ok(
            broker_docs_progress["status_summary"]["codex_next_action"] == "call_owner_tool_and_attach_result",
            "docs progress status summary should expose Codex handoff action",
        )
        assert_ok(broker_docs_progress["resource_need_satisfied"] is False, "docs handoff should not satisfy resource need")
        assert_ok(broker_docs_progress["same_need_fetch_allowed"] is False, "docs handoff should not allow independent replacement fetch")
        assert_ok(
            broker_docs_progress["status_summary"]["same_need_fetch_policy"] == "continue_resource_layer_handoff_or_attach_result",
            "docs handoff should keep resource-layer ownership for same need",
        )
        assert_ok(broker_docs_progress["progress"]["percent"] == 100, "docs handoff progress should be resource-layer terminal")
        docs_handoff = next(item for item in broker_docs.attempts if item["status"] == "handoff_required")
        network_handoff = docs_handoff["result"].get("network_handoff", {})
        assert_ok(network_handoff.get("ok"), "docs broker handoff should include network execution package")
        assert_ok(network_handoff.get("route_summary", {}).get("target_kind") == "docs", "docs handoff should expose route summary")
        assert_ok("suggested_env" in network_handoff, "docs broker handoff should expose suggested network env")
        assert_ok(
            network_handoff.get("rule") == "network guidance only; owner tool permission boundary remains unchanged",
            "docs broker handoff should preserve owner permission boundary",
        )
        index_path = Path(broker_docs.manifest_path).parents[1] / "index.jsonl"
        index_lines = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines()]
        docs_index = next(item for item in reversed(index_lines) if item.get("request_id") == broker_docs.request_id)
        assert_ok(docs_index.get("network_target_kind") == "docs", "resource index should expose network target kind")
        assert_ok(docs_index.get("owner_tool") == "context7", "resource index should expose owner tool")
        package_request = isolated_handle_request(
            ResourceBrokerRequest(
                target="ruff",
                task="install python package dependency",
                intent=ResourceIntent.PACKAGE_DEPENDENCY,
            ),
            event_log=broker_events,
            receipt_log=broker_receipts,
            resource_log=root / "broker-resource-log.jsonl",
        )
        assert_ok(package_request.status == "handoff_required", "package resource request should require owner package manager")
        assert_ok(package_request.route["primary_tool"] == "package_manager", "package request should route to package_manager")
        assert_ok("install_side_effect" in package_request.route["risk_flags"], "package request should expose install side-effect risk")
        owner_validation = validate_owner_executor()
        assert_ok(owner_validation.get("ok"), "resource owner executor validate failed")
        strategy_policy_validation = validate_strategy_policy()
        assert_ok(strategy_policy_validation.get("ok"), "resource strategy policy validate failed")
        assert_ok(supports_owner_execution("package_manager"), "package manager should support read-only owner execution")
        assert_ok(supports_owner_execution("generic_search"), "generic search should support read-only owner execution")
        search_health = resource_search_call("resource_search.health", {}) or {}
        assert_ok(search_health.get("ok"), "Hub generic search dependency health failed")
        unicode_query = "systemd \u670d\u52a1\u91cd\u542f\u7b56\u7565 \u2713"
        unicode_worker_result = resource_search_call(
            "resource_search.text",
            {"query": unicode_query, "backend": "unsupported", "timeout_seconds": 3},
        ) or {}
        assert_ok(
            unicode_worker_result.get("error_class") == "unsupported_search_backend"
            and unicode_worker_result.get("query") == unicode_query,
            f"resource search worker did not preserve UTF-8 JSON output: {unicode_worker_result}",
        )
        generic_request = ResourceBrokerRequest(
            target="Python packaging build publish guide",
            task="locate authoritative packaging instructions",
            intent=ResourceIntent.EXTERNAL_DEPENDENCY,
            auto_owner=True,
            metadata={
                "resource_kind_hint": "generic_web",
                "validation_profile": "quick",
                "custom_delegation": {"constraints": {"site_or_domain": "packaging.python.org"}},
            },
        )
        generic_plan = strategy_plan_for_request(generic_request, route_for_request(generic_request))
        generic_plan_tools = [item["tool"] for item in generic_plan]
        assert_ok(
            generic_plan_tools[:2] == ["resource_source_strategy", "generic_search"],
            f"generic search continuation order mismatch: {generic_plan_tools}",
        )
        openai_broker_request = ResourceBrokerRequest(
            target="OpenAI official documentation for Codex plugins and Sites",
            task="Find first-party OpenAI product documentation; do not use Microsoft Docs or Context7",
            intent=ResourceIntent.DOCUMENTATION_LOOKUP,
            auto_owner=True,
            metadata={
                "resource_kind_hint": "documentation",
                "source_domains": ["openai.com", "help.openai.com", "developers.openai.com"],
                "validation_profile": "quick",
            },
        )
        openai_broker_plan = strategy_plan_for_request(openai_broker_request, route_for_request(openai_broker_request))
        openai_broker_tools = [item["tool"] for item in openai_broker_plan]
        assert_ok(
            openai_broker_tools[:2] == ["resource_source_strategy", "openai-docs"],
            f"official documentation owner should immediately follow source strategy: {openai_broker_tools}",
        )
        assert_ok(
            next(item for item in openai_broker_plan if item["tool"] == "openai-docs")["executable_by_broker"],
            "official OpenAI documentation owner should be executable by the broker",
        )
        openai_gateway_request = resource_broker.network_gateway_request_for_request(
            openai_broker_request,
            route_for_request(openai_broker_request),
        )
        assert_ok(
            openai_gateway_request.get("target") == "https://openai.com/",
            f"docs gateway must probe the structured source domain instead of Microsoft Learn: {openai_gateway_request}",
        )
        assert_ok(
            "https://learn.microsoft.com/" not in openai_gateway_request.get("probe_targets", []),
            "non-Microsoft docs request must not inherit the Microsoft Learn probe",
        )
        opa_gateway_request = resource_broker.network_gateway_request_for_request(
            ResourceBrokerRequest(
                target="OPA policy documentation",
                task="read official OPA documentation",
                intent=ResourceIntent.DOCUMENTATION_LOOKUP,
                metadata={"resource_kind_hint": "documentation", "source_domains": ["openpolicyagent.org"], "validation_profile": "quick"},
            ),
            route_for_request(
                ResourceBrokerRequest(
                    target="OPA policy documentation",
                    task="read official OPA documentation",
                    intent=ResourceIntent.DOCUMENTATION_LOOKUP,
                    metadata={"resource_kind_hint": "documentation", "source_domains": ["openpolicyagent.org"], "validation_profile": "quick"},
                )
            ),
        )
        assert_ok(
            opa_gateway_request.get("target") == "https://openpolicyagent.org/",
            f"OPA docs gateway target mismatch: {opa_gateway_request}",
        )
        mixed_domain_request = ResourceBrokerRequest(
            target="HP OMEN graphics switching and NVIDIA Optimus",
            task="Research official HP, Microsoft BitLocker, and NVIDIA guidance",
            intent=ResourceIntent.DOCUMENTATION_LOOKUP,
            auto_owner=True,
            metadata={
                "resource_kind_hint": "documentation",
                "source_domains": ["support.hp.com", "learn.microsoft.com", "nvidia.com", "h30434.www3.hp.com"],
                "validation_profile": "quick",
            },
        )
        mixed_domain_route = route_for_request(mixed_domain_request)
        mixed_domain_plan = strategy_plan_for_request(mixed_domain_request, mixed_domain_route)
        mixed_gateway_request = resource_broker.network_gateway_request_for_request(mixed_domain_request, mixed_domain_route)
        assert_ok(
            [item["tool"] for item in mixed_domain_plan[:2]] == ["resource_source_strategy", "generic_search"],
            f"mixed explicit domains must not collapse to Microsoft Docs: {mixed_domain_plan}",
        )
        assert_ok(mixed_gateway_request.get("owner_tool") == "generic_search", "mixed-domain gateway owner must be generic_search")
        assert_ok(
            mixed_gateway_request.get("probe_targets")
            == [
                "https://support.hp.com/",
                "https://learn.microsoft.com/",
                "https://nvidia.com/",
                "https://h30434.www3.hp.com/",
            ],
            f"mixed-domain gateway probes must preserve all structured domains: {mixed_gateway_request}",
        )
        captured_searches: list[dict[str, object]] = []
        original_generic_search_hub_call = resource_owner_executor.call_hub_tool
        try:
            def fake_generic_search_hub_call(tool: str, arguments: dict[str, object], *, timeout: int) -> dict[str, object]:
                captured_searches.append({"tool": tool, "arguments": arguments, "timeout": timeout})
                domain = str(arguments.get("site_or_domain") or "developers.openai.com")
                return {
                    "ok": True,
                    "results": [{"title": "OpenAI Codex", "href": f"https://{domain}/codex/", "body": "Official Codex documentation"}],
                    "backend": "test",
                }

            resource_owner_executor.call_hub_tool = fake_generic_search_hub_call
            official_search_result = resource_owner_executor.execute_generic_search(
                {
                    "task": openai_broker_request.task,
                    "target": openai_broker_request.target,
                    "metadata": openai_broker_request.metadata,
                },
                {"ok": True, "plan": {"route_mode": "direct", "target_kind": "docs", "target": "https://openai.com/"}},
                timeout=5,
            )
        finally:
            resource_owner_executor.call_hub_tool = original_generic_search_hub_call
        assert_ok(official_search_result["ok"] is True, "official documentation generic search adapter should return a usable result")
        assert_ok(
            [(item.get("arguments") or {}).get("site_or_domain") for item in captured_searches]
            == ["openai.com", "help.openai.com", "developers.openai.com"],
            f"all structured documentation domains must be searched: {captured_searches}",
        )
        try:
            resource_owner_executor.call_hub_tool = lambda *_args, **_kwargs: {"ok": True, "results": [], "backend": "test"}
            empty_mixed_search = resource_owner_executor.execute_generic_search(
                {
                    "task": mixed_domain_request.task,
                    "target": mixed_domain_request.target,
                    "metadata": mixed_domain_request.metadata,
                },
                {"ok": True, "plan": {"route_mode": "direct", "target_kind": "docs", "target": "https://support.hp.com/"}},
                timeout=5,
            )
        finally:
            resource_owner_executor.call_hub_tool = original_generic_search_hub_call
        assert_ok(empty_mixed_search["ok"] is False, "zero-source multi-domain search must not be completed")
        assert_ok(empty_mixed_search["error_class"] == "owner_no_results", "empty search must expose owner_no_results")
        assert_ok(
            len(empty_mixed_search.get("metadata", {}).get("domain_outcomes", [])) == 4,
            "empty multi-domain result must preserve one actionable outcome per searched domain",
        )
        original_source_selection = resource_broker.execute_source_selection
        original_execute_owner_tool_for_search = resource_broker.execute_owner_tool
        try:
            resource_broker.execute_source_selection = lambda *_args, **_kwargs: {
                "ok": False,
                "status": "degraded",
                "error_class": "curated_catalog_no_match",
                "reason": "test curated catalog miss",
                "next_action": "continue_resource_layer_with_registered_search_owner",
            }
            resource_broker.execute_owner_tool = lambda **_kwargs: {
                "ok": True,
                "status": "completed",
                "source": "generic_search",
                "result_kind": "generic_text_search",
                "content": "authoritative Python packaging build publish guide",
                "candidates": [
                    {
                        "title": "Python Packaging User Guide",
                        "url": "https://packaging.python.org/en/latest/",
                        "source_id": "https://packaging.python.org/en/latest/",
                    }
                ],
                "metadata": {"top_url": "https://packaging.python.org/en/latest/"},
                "next_action": "consume_resource",
            }
            generic_receipt = isolated_handle_request(
                generic_request,
                event_log=root / "generic-search-events.jsonl",
                receipt_log=root / "generic-search-receipts.jsonl",
                resource_log=root / "generic-search-resource-log.jsonl",
                store_root=root / "generic-search-store",
            )
        finally:
            resource_broker.execute_source_selection = original_source_selection
            resource_broker.execute_owner_tool = original_execute_owner_tool_for_search
        assert_ok(
            [item["tool"] for item in generic_receipt.attempts[:2]] == ["resource_source_strategy", "generic_search"],
            "broker execution must consume the dynamic strategy plan",
        )
        assert_ok(generic_receipt.status == "completed" and generic_receipt.result_kind == "generic_text_search", "generic search continuation should satisfy the request")
        original_source_selection = resource_broker.execute_source_selection
        original_execute_owner_tool_for_search = resource_broker.execute_owner_tool
        try:
            resource_broker.execute_source_selection = lambda *_args, **_kwargs: {
                "ok": False,
                "status": "degraded",
                "error_class": "documentation_owner_execution_required",
                "reason": "test documentation owner continuation",
                "available_owner_adapter": "openai-docs",
                "next_action": "continue_resource_layer_with_registered_documentation_owner",
            }
            resource_broker.execute_owner_tool = lambda **_kwargs: {
                "ok": True,
                "status": "completed",
                "source": "openai-docs",
                "result_kind": "openai_docs_fetch",
                "content": "OpenAI Codex plugins and Sites official documentation",
                "candidates": [{"title": "OpenAI Codex", "url": "https://developers.openai.com/codex/", "source_id": "https://developers.openai.com/codex/"}],
                "metadata": {"top_url": "https://developers.openai.com/codex/"},
                "next_action": "consume_resource",
            }
            openai_docs_receipt = isolated_handle_request(
                openai_broker_request,
                event_log=root / "openai-docs-events.jsonl",
                receipt_log=root / "openai-docs-receipts.jsonl",
                resource_log=root / "openai-docs-resource-log.jsonl",
                store_root=root / "openai-docs-store",
            )
        finally:
            resource_broker.execute_source_selection = original_source_selection
            resource_broker.execute_owner_tool = original_execute_owner_tool_for_search
        assert_ok(
            [item["tool"] for item in openai_docs_receipt.attempts[:2]] == ["resource_source_strategy", "openai-docs"],
            "OpenAI documentation request should stay inside the resource layer through owner completion",
        )
        assert_ok(openai_docs_receipt.status == "completed", "OpenAI documentation owner continuation should complete the broker request")
        auto_owner_request = ResourceBrokerRequest(
            target="ruff",
            task="inspect python package dependency",
            intent=ResourceIntent.PACKAGE_DEPENDENCY,
            auto_owner=True,
            metadata={"validation_profile": "quick"},
        )
        auto_owner_route = route_for_request(auto_owner_request)
        auto_owner_plan = strategy_plan_for_request(auto_owner_request, auto_owner_route)
        package_step = next(item for item in auto_owner_plan if item["tool"] == "package_manager")
        assert_ok(package_step["executable_by_broker"], "auto-owner package step should be executable by broker")
        original_owner_call_hub_tool = resource_owner_executor.call_hub_tool
        try:
            def fake_github_search_hub_tool(tool, arguments, *, timeout=30):
                if tool == "github.api" and arguments.get("path") == "/search/repositories":
                    return {
                        "ok": True,
                        "result": {
                            "total_count": 1,
                            "incomplete_results": False,
                            "items": [
                                {
                                    "full_name": "IBM/mcp-context-forge",
                                    "description": "MCP gateway test fixture",
                                    "html_url": "https://github.com/IBM/mcp-context-forge",
                                    "language": "Python",
                                    "stargazers_count": 4058,
                                    "forks_count": 741,
                                    "open_issues_count": 1055,
                                    "updated_at": "2026-07-08T22:07:29Z",
                                    "topics": ["mcp", "gateway"],
                                }
                            ],
                        },
                        "hub_transport": "test_hub",
                        "token_source": "test",
                    }
                return original_owner_call_hub_tool(tool, arguments, timeout=timeout)

            resource_owner_executor.call_hub_tool = fake_github_search_hub_tool
            github_search = isolated_handle_request(
                ResourceBrokerRequest(
                    target="mcp gateway resource layer",
                    task="search github repositories for a local MCP gateway",
                    intent=ResourceIntent.EXTERNAL_DEPENDENCY,
                    auto_owner=True,
                    metadata={"validation_profile": "quick"},
                ),
                event_log=broker_events,
                receipt_log=broker_receipts,
            )
        finally:
            resource_owner_executor.call_hub_tool = original_owner_call_hub_tool
        assert_ok(github_search.status == "completed", "resource layer should execute GitHub repository search without Codex handoff")
        assert_ok(github_search.result_kind == "github_repository_search", "GitHub search should expose search result kind")
        assert_ok("IBM/mcp-context-forge" in (github_search.content_ref and Path(github_search.content_ref).read_text(encoding="utf-8") or ""), "GitHub search content should include repository candidates")

        complex_envelope = normalize_resource_envelope(
            {
                "domain": "resource",
                "action": "inspect",
                "summary": "find a repository and inspect its implementation",
                "target": "resource execution fixture",
                "resource": {
                    "kind": "github_project",
                    "source_policy": {"domains": ["github.com"], "authority": "primary"},
                    "execution": {
                        "operations": ["repository_search", "repository_read"],
                        "selectors": {
                            "query": "resource execution fixture",
                            "paths": ["src/main.py"],
                            "issue_query": "routing",
                            "code_query": "execute_plan",
                            "include_archived": False,
                        },
                        "deliverables": ["candidates", "metadata", "readme", "tree", "files", "releases", "issues", "code_matches"],
                        "limits": {"candidate_count": 3, "repository_count": 1, "item_count": 5, "content_chars": 20000},
                        "acceptance": {
                            "required_deliverables": ["metadata", "readme", "tree", "files", "releases", "issues", "code_matches"],
                            "allow_partial": False,
                        },
                    },
                },
                "safety": {"allow_network": True, "allow_filesystem_write": False},
            }
        )
        complex_request = build_delegation_from_envelope(complex_envelope)["request"]
        complex_plan = source_execution_plan(complex_request, "github_project")
        assert_ok(
            complex_plan["operations"]
            == ["repository_search", "repository_metadata", "readme_read", "tree_read", "file_read", "release_read", "issue_search", "code_search"],
            "repository_read should compile into ordered GitHub content phases",
        )
        original_complex_hub = resource_owner_executor.call_hub_tool
        try:
            def fake_complex_github_hub(tool, arguments, *, timeout=30):
                if tool != "github.api":
                    return original_complex_hub(tool, arguments, timeout=timeout)
                path = arguments.get("path")
                if path == "/search/repositories":
                    result = {"total_count": 1, "items": [{"full_name": "example/resource-fixture", "html_url": "https://github.com/example/resource-fixture", "description": "resource execution fixture", "default_branch": "main"}]}
                elif path == "/repos/example/resource-fixture":
                    result = {"full_name": "example/resource-fixture", "html_url": "https://github.com/example/resource-fixture", "description": "resource execution fixture", "default_branch": "main", "stargazers_count": 12, "forks_count": 2, "open_issues_count": 1, "license": {"spdx_id": "MIT"}}
                elif path == "/repos/example/resource-fixture/readme":
                    import base64
                    result = {"encoding": "base64", "content": base64.b64encode(b"# Resource Fixture\nStructured execution plan").decode("ascii")}
                elif path == "/repos/example/resource-fixture/git/trees/main":
                    result = {"tree": [{"path": "src/main.py", "type": "blob", "size": 42}], "truncated": False}
                elif path == "/repos/example/resource-fixture/contents/src/main.py":
                    import base64
                    result = {"encoding": "base64", "content": base64.b64encode(b"def execute_plan():\n    return True\n").decode("ascii")}
                elif path == "/repos/example/resource-fixture/releases":
                    result = [{"tag_name": "v1.0.0", "name": "v1", "published_at": "2026-01-01T00:00:00Z", "html_url": "https://github.com/example/resource-fixture/releases/tag/v1.0.0", "body": "stable"}]
                elif path == "/search/issues":
                    result = {"items": [{"title": "Routing issue", "number": 1, "state": "open", "html_url": "https://github.com/example/resource-fixture/issues/1"}]}
                elif path == "/search/code":
                    result = {"items": [{"name": "main.py", "path": "src/main.py", "html_url": "https://github.com/example/resource-fixture/blob/main/src/main.py"}]}
                else:
                    return {"ok": False, "reason": f"unexpected_test_path:{path}"}
                return {"ok": True, "result": result, "hub_transport": "test_hub", "token_source": "test"}

            resource_owner_executor.call_hub_tool = fake_complex_github_hub
            complex_result = resource_owner_executor.execute_github_request(
                complex_request,
                {"ok": True, "plan": {"route_mode": "probe_selected_direct", "target_kind": "github", "env": {}, "unset_env": []}},
                20,
            )
        finally:
            resource_owner_executor.call_hub_tool = original_complex_hub
        assert_ok(complex_result.get("ok"), json.dumps(complex_result, ensure_ascii=False))
        completed_deliverables = set((complex_result.get("metadata") or {}).get("completed_deliverables") or [])
        assert_ok(
            {"metadata", "readme", "tree", "files", "releases", "issues", "code_matches"}.issubset(completed_deliverables),
            "complex GitHub execution should complete every required deliverable",
        )
        search_only_result = {
            "ok": True,
            "status": "completed",
            "source": "github",
            "result_kind": "github_repository_search",
            "content": "example/resource-fixture",
            "metadata": {
                "items": [{"full_name": "example/resource-fixture", "html_url": "https://github.com/example/resource-fixture"}],
                "completed_deliverables": ["candidates"],
            },
        }
        search_only_acceptance = resource_result_satisfaction(request=complex_request, tool="github", result=search_only_result)
        assert_ok(
            not search_only_acceptance.satisfied and search_only_acceptance.reason == "required_deliverables_not_met",
            "repository candidates must not satisfy structured content deliverables",
        )
        generic_phase_envelope = normalize_resource_envelope(
            {
                "domain": "resource",
                "action": "inspect",
                "target": "official architecture documentation",
                "resource": {
                    "kind": "documentation",
                    "execution": {
                        "phases": [
                            {"id": "discover-source", "operation": "search", "required": True},
                            {"id": "read-source", "operation": "read", "required": True, "depends_on": ["discover-source"]},
                        ],
                        "acceptance": {"required_phases": ["discover-source", "read-source"]},
                    },
                },
            }
        )
        generic_phase_request = build_delegation_from_envelope(generic_phase_envelope)["request"]
        generic_phase_result = {
            "ok": True,
            "status": "completed",
            "source": "generic_search",
            "result_kind": "content",
            "content": "architecture documentation",
            "metadata": {"phase_results": [{"phase_id": "discover-source", "operation": "search", "ok": True}]},
        }
        generic_phase_acceptance = resource_result_satisfaction(
            request=generic_phase_request,
            tool="generic_search",
            result=generic_phase_result,
        )
        assert_ok(
            not generic_phase_acceptance.satisfied and "read-source" in (generic_phase_acceptance.relevance.get("missing_phases") or []),
            "a generic complex task must not complete while a required phase is missing",
        )
        explicit_search_envelope = normalize_resource_envelope(
            {
                "domain": "resource",
                "action": "search",
                "summary": "read README despite explicit search-only operation",
                "target": "resource fixture",
                "resource": {"kind": "github_project", "execution": {"operations": ["repository_search"], "deliverables": ["candidates"]}},
            }
        )
        explicit_search_request = build_delegation_from_envelope(explicit_search_envelope)["request"]
        assert_ok(
            source_execution_plan(explicit_search_request, "github_project")["operations"] == ["repository_search"],
            "explicit structured operations must override conflicting natural-language wording",
        )
        known_docs_request = ResourceBrokerRequest(
            url="https://docs.github.com/en/rest/repos/contents",
            task="read official GitHub REST contents documentation",
            intent=ResourceIntent.DOCUMENTATION_LOOKUP,
            auto_owner=True,
            metadata={"batch_item_contract": {"acceptance": {"consumable_required": True}}},
        )
        known_docs_plan = strategy_plan_for_request(known_docs_request, route_for_request(known_docs_request))
        assert_ok(
            known_docs_plan[0]["tool"] == "markitdown" and known_docs_plan[0]["stage"] == ResourceStage.PREVIEW,
            "known documentation URLs must return bounded content instead of being misrouted to a library resolver",
        )
        results["github_complex_execution"] = "ok"
        auto_owner_docs = ResourceBrokerRequest(
            url="https://learn.microsoft.com/windows/win32/winhttp/proxycfg-exe--a-proxy-configuration-tool",
            task="fetch microsoft docs resource",
            intent=ResourceIntent.DOCUMENTATION_LOOKUP,
            auto_owner=True,
        )
        docs_owner_plan = strategy_plan_for_request(auto_owner_docs, route_for_request(auto_owner_docs))
        assert_ok(
            next(item for item in docs_owner_plan if item["tool"] == "microsoftdocs")["executable_by_broker"],
            "auto-owner microsoftdocs step should be executable by broker",
        )
        auto_owner_browser = ResourceBrokerRequest(
            url="https://example.com",
            task="render page browser evidence",
            intent=ResourceIntent.DOCUMENTATION_LOOKUP,
            auto_owner=True,
        )
        browser_owner_plan = strategy_plan_for_request(auto_owner_browser, route_for_request(auto_owner_browser))
        assert_ok(
            next(item for item in browser_owner_plan if item["tool"] == "playwright")["executable_by_broker"],
            "auto-owner playwright step should be executable by broker",
        )
        auto_owner_markdown = ResourceBrokerRequest(
            url="https://example.com",
            task="convert page to markdown",
            intent=ResourceIntent.DOCUMENTATION_LOOKUP,
            auto_owner=True,
        )
        markdown_owner_plan = strategy_plan_for_request(auto_owner_markdown, route_for_request(auto_owner_markdown))
        assert_ok(
            next(item for item in markdown_owner_plan if item["tool"] == "markitdown")["executable_by_broker"],
            "auto-owner markitdown step should be executable by broker",
        )
        original_execute_owner_tool = resource_broker.execute_owner_tool
        original_acquire_resource_with_policy = resource_broker.acquire_resource_with_policy
        try:
            def fake_low_relevance_owner(**_kwargs):
                return {
                    "ok": True,
                    "status": "completed",
                    "source": "context7",
                    "result_kind": "context7_docs",
                    "content": "jOOQ SQL builder manual",
                    "metadata": {"library_id": "/jooq/jooq", "resolved_from": "look"},
                    "next_action": "consume_resource",
                }

            def fake_fallback_acquire(_request, *, intent, stage):
                return ResourceResult(
                    ok=True,
                    source="url",
                    name="json.html",
                    metadata={"preview_text": "Python json module documentation", "intent": intent, "stage": stage},
                    intent=intent,
                    next_action="consume_resource",
                )

            resource_broker.execute_owner_tool = fake_low_relevance_owner
            resource_broker.acquire_resource_with_policy = fake_fallback_acquire
            relevance_fallback = isolated_handle_request(
                ResourceBrokerRequest(
                    target="python json module",
                    task="look up python json documentation",
                    intent=ResourceIntent.DOCUMENTATION_LOOKUP,
                    auto_owner=True,
                    metadata={"validation_profile": "quick"},
                ),
                event_log=broker_events,
                receipt_log=broker_receipts,
                store_root=broker_cache,
            )
        finally:
            resource_broker.execute_owner_tool = original_execute_owner_tool
            resource_broker.acquire_resource_with_policy = original_acquire_resource_with_policy
        assert_ok(relevance_fallback.ok, "low-relevance owner result should fall back to resource_cli")
        low_relevance_attempt = next(item for item in relevance_fallback.attempts if item["tool"] == "context7")
        assert_ok(low_relevance_attempt["status"] == "degraded", "low relevance owner result should be degraded")
        assert_ok(low_relevance_attempt["error_class"] == "low_relevance", "low relevance should expose error class")
        low_relevance_recovery = (low_relevance_attempt["result"].get("metadata") or {}).get("recovery_decision") or {}
        assert_ok(low_relevance_recovery.get("fallback_allowed"), "low relevance should carry fallback recovery decision")
        assert_ok(relevance_fallback.strategy_summary.get("low_relevance_count") == 1, "strategy summary should count low relevance")
        assert_ok(relevance_fallback.strategy_summary.get("recoverable_count", 0) >= 1, "strategy summary should count recoverable attempts")
        simple_docs_sufficiency = owner_result_sufficiency(
            request={"task": "look up python json documentation"},
            tool="context7",
            result={
                "ok": True,
                "status": "completed",
                "source": "context7",
                "result_kind": "context7_docs",
                "content": "Python json module documentation",
                "metadata": {"library_id": "/python/cpython"},
            },
        )
        assert_ok(simple_docs_sufficiency.ok, "simple docs lookup should not require multi-source coverage")

        original_execute_owner_tool = resource_broker.execute_owner_tool
        original_acquire_resource_with_policy = resource_broker.acquire_resource_with_policy
        try:
            def fake_narrow_research_owner(**_kwargs):
                return {
                    "ok": True,
                    "status": "completed",
                    "source": "context7",
                    "result_kind": "context7_docs",
                    "content": "Workflow routing best practices overview from one library.",
                    "metadata": {"library_id": "/workflow/single-docs"},
                    "next_action": "consume_resource",
                }

            def fake_no_direct_replacement(_request, *, intent, stage):
                return ResourceResult(
                    ok=False,
                    source="url",
                    error="no source URL available for direct acquire",
                    policy_reason="source_required",
                    decision="failed",
                    metadata={"intent": intent, "stage": stage},
                    intent=intent,
                    next_action="refine_resource_delegation_and_retry",
                )

            resource_broker.execute_owner_tool = fake_narrow_research_owner
            resource_broker.acquire_resource_with_policy = fake_no_direct_replacement
            narrow_research = isolated_handle_request(
                ResourceBrokerRequest(
                    task="research mature workflow routing best practices and compare alternatives",
                    intent=ResourceIntent.DOCUMENTATION_LOOKUP,
                    auto_owner=True,
                    metadata={"validation_profile": "quick", "required_source_count": 2},
                ),
                event_log=broker_events,
                receipt_log=broker_receipts,
                store_root=broker_cache,
            )
        finally:
            resource_broker.execute_owner_tool = original_execute_owner_tool
            resource_broker.acquire_resource_with_policy = original_acquire_resource_with_policy
        assert_ok(not narrow_research.ok, "narrow multi-source research should not complete from one owner result")
        assert_ok(narrow_research.status == "deferred", "narrow multi-source research should defer for refined delegation")
        assert_ok(narrow_research.error_class == "insufficient_coverage", "narrow result should expose insufficient_coverage")
        assert_ok(
            narrow_research.codex_guidance.get("codex_next_action") == "refine_resource_delegation_and_retry",
            "narrow result should tell Codex to refine resource delegation",
        )
        assert_ok(
            narrow_research.codex_guidance.get("same_need_fetch_allowed") is False,
            "narrow result must not release Codex to direct same-need fetch",
        )
        assert_ok(
            narrow_research.strategy_summary.get("insufficient_coverage_count", 0) >= 1,
            "strategy summary should count insufficient coverage",
        )
        no_result_relevance = owner_result_relevance(
            request={"task": "中国机构 人工智能 开放获取 论文 可下载", "target": "中国 人工智能 论文"},
            tool="context7",
            result={
                "ok": True,
                "status": "completed",
                "source": "context7",
                "content": 'No libraries found for "中国机构 人工智能 开放获取 论文 可下载". Try a different search term.',
            },
        )
        assert_ok(not no_result_relevance.ok, "owner no-results text should not pass relevance")
        assert_ok(no_result_relevance.reason == "owner_no_results", "owner no-results should expose stable reason")
        handoff_recovery = recovery_decision_for_attempt({"status": "handoff_required", "error_class": "handoff_required_for_owner_tool"})
        assert_ok(handoff_recovery.fallback_allowed and not handoff_recovery.terminal, "handoff should remain non-terminal and fallback-capable")
        media_403_recovery = recovery_decision_for_error(
            {"error_type": "http_status", "http_status": 403, "url": "https://upload.wikimedia.org/example.jpg"}
        )
        assert_ok(media_403_recovery.recoverable and not media_403_recovery.terminal, "media CDN 403 should allow candidate/API fallback")
        zero_candidate_satisfaction = resource_result_satisfaction(
            request={"target": "next draw superversion cloakbrowser", "task": "search GitHub repositories", "metadata": {}},
            tool="github",
            result={
                "ok": True,
                "status": "completed",
                "source": "github",
                "result_kind": "github_repository_search",
                "content": "",
                "metadata": {"query": "next draw superversion cloakbrowser", "items": [], "total_count": 0},
            },
        )
        assert_ok(not zero_candidate_satisfaction.satisfied, "zero-candidate discovery must not satisfy a resource request")
        assert_ok(zero_candidate_satisfaction.reason == "minimum_candidates_not_met", "zero-candidate discovery should request item refinement")
        metadata_only_request = {
            "url": "https://example.com/probe",
            "task": "probe availability only",
            "metadata": {"batch_item_contract": {"acceptance": {"consumable_required": False}}},
        }
        metadata_only_satisfaction = resource_result_satisfaction(
            request=metadata_only_request,
            tool="resource_cli",
            result={"ok": True, "status": "completed", "source": "url", "metadata": {"url": "https://example.com/probe"}},
        )
        assert_ok(metadata_only_satisfaction.satisfied, "explicit metadata-only probe acceptance should remain supported")
        assert_ok(metadata_only_satisfaction.result_kind == "metadata", "metadata-only probe should report metadata result kind")
        consumable_probe_satisfaction = resource_result_satisfaction(
            request={
                **metadata_only_request,
                "metadata": {"batch_item_contract": {"acceptance": {"consumable_required": True}}},
            },
            tool="resource_cli",
            result={"ok": True, "status": "completed", "source": "url", "metadata": {"url": "https://example.com/probe"}},
        )
        assert_ok(not consumable_probe_satisfaction.satisfied, "URL identity metadata must not satisfy a consumable request")
        assert_ok(consumable_probe_satisfaction.reason == "no_consumable_content_or_artifact", "consumable probe rejection should expose a stable reason")
        empty_json_satisfaction = resource_result_satisfaction(
            request={"task": "read structured result", "metadata": {}},
            tool="resource_cli",
            result={"ok": True, "status": "completed", "source": "url", "content": '{"results": []}'},
        )
        assert_ok(not empty_json_satisfaction.satisfied, "structurally empty JSON must not satisfy a consumable request")
        assert_ok(empty_json_satisfaction.reason == "no_consumable_content_or_artifact", "empty JSON should use the canonical empty-result reason")

        original_openai_docs_gateway = resource_owner_executor.call_mcp_gateway_tool
        try:
            resource_owner_executor.call_mcp_gateway_tool = lambda *_args, **_kwargs: {
                "ok": True,
                "content": [{"type": "text", "text": '{"hits": []}'}],
            }
            empty_openai_docs = resource_owner_executor.execute_openai_docs(
                {"task": "OpenAI feature public documentation", "metadata": {"validation_profile": "quick"}},
                {},
                5,
            )
        finally:
            resource_owner_executor.call_mcp_gateway_tool = original_openai_docs_gateway
        assert_ok(not empty_openai_docs["ok"], "empty OpenAI Docs search must not complete")
        assert_ok(empty_openai_docs["status"] == "degraded", "empty OpenAI Docs search should degrade forward")
        assert_ok(
            empty_openai_docs["error_class"] == "openai_docs_search_requires_fetch",
            "OpenAI Docs search-only evidence should expose the canonical fetch requirement",
        )

        openai_docs_calls: list[tuple[str, dict[str, object]]] = []
        try:
            def fake_openai_docs_gateway(_profile, tool, arguments, **_kwargs):
                openai_docs_calls.append((tool, arguments))
                if tool == "search_openai_docs":
                    return {
                        "ok": True,
                        "content": [
                            {
                                "type": "text",
                                "text": '{"hits":[{"url":"https://developers.openai.com/learn/docs-mcp","content":"Docs MCP"}]}',
                            }
                        ],
                    }
                return {
                    "ok": True,
                    "content": [{"type": "text", "text": "# Docs MCP\nOfficial OpenAI documentation body."}],
                }

            resource_owner_executor.call_mcp_gateway_tool = fake_openai_docs_gateway
            fetched_openai_docs = resource_owner_executor.execute_openai_docs(
                {"task": "OpenAI Docs MCP setup", "metadata": {"validation_profile": "quick"}},
                {},
                5,
            )
        finally:
            resource_owner_executor.call_mcp_gateway_tool = original_openai_docs_gateway
        assert_ok(fetched_openai_docs["ok"] and fetched_openai_docs["status"] == "completed", "OpenAI Docs search plus fetch should complete")
        assert_ok([item[0] for item in openai_docs_calls] == ["search_openai_docs", "fetch_openai_doc"], "OpenAI Docs owner must search then fetch")
        assert_ok(
            fetched_openai_docs["metadata"].get("official_openai_provenance") is True,
            "OpenAI Docs completion must preserve official provenance",
        )

        original_execute_microsoftdocs = resource_owner_executor.execute_microsoftdocs
        empty_owner_calls = {"count": 0}
        try:
            def fake_empty_microsoftdocs(_request, _gateway_plan, _timeout):
                empty_owner_calls["count"] += 1
                return {
                    "ok": True,
                    "status": "completed",
                    "source": "microsoftdocs",
                    "result_kind": "microsoft_docs_search",
                    "content": '{"results": []}',
                    "metadata": {"query": "unique-empty-owner-regression"},
                }

            resource_owner_executor.execute_microsoftdocs = fake_empty_microsoftdocs
            empty_owner_request = {"task": "unique-empty-owner-regression", "metadata": {"validation_profile": "quick"}}
            empty_owner_first = resource_owner_executor.execute_owner_tool(
                tool="microsoftdocs",
                request=empty_owner_request,
                gateway_plan={},
                timeout=3,
            )
            empty_owner_second = resource_owner_executor.execute_owner_tool(
                tool="microsoftdocs",
                request=empty_owner_request,
                gateway_plan={},
                timeout=3,
            )
        finally:
            resource_owner_executor.execute_microsoftdocs = original_execute_microsoftdocs
        assert_ok(not empty_owner_first.get("ok") and empty_owner_first.get("status") == "degraded", "empty completed owner result must be downgraded before return")
        assert_ok(empty_owner_calls["count"] == 2, "rejected empty owner results must not enter owner caches")
        assert_ok(not empty_owner_second.get("ok"), "repeated empty owner result must remain rejected")

        original_gateway_call = resource_owner_executor.call_mcp_gateway_tool
        context7_calls: list[tuple[str, int]] = []
        try:
            def fake_context7_gateway(_profile, tool, _arguments, *, timeout):
                context7_calls.append((tool, timeout))
                if tool == "resolve_library_id":
                    time.sleep(1.05)
                    return {"ok": True, "content": [{"type": "text", "text": "Context7-compatible library ID: /open-telemetry/opentelemetry-specification"}]}
                return {"ok": True, "content": [{"type": "text", "text": "OpenTelemetry semantic convention content"}]}

            resource_owner_executor.call_mcp_gateway_tool = fake_context7_gateway
            context7_budget_result = resource_owner_executor.execute_context7(
                {"target": "OpenTelemetry", "task": "read OpenTelemetry semantic conventions", "metadata": {}},
                {},
                5,
            )
        finally:
            resource_owner_executor.call_mcp_gateway_tool = original_gateway_call
        assert_ok(context7_budget_result.get("ok"), "Context7 multi-stage lookup should complete within its shared budget")
        assert_ok([tool for tool, _timeout in context7_calls] == ["resolve_library_id", "query_docs"], "Context7 should run resolver then docs query")
        assert_ok(context7_calls[0][1] <= 2, "Context7 resolver must reserve time for the docs query")
        assert_ok(context7_calls[1][1] < 5, "Context7 docs query must receive only the remaining total budget")

        consumable_preview_request = ResourceBrokerRequest(
            url="https://example.com/docs",
            task="read URL content",
            intent=ResourceIntent.EXPLICIT_USER_URL,
            metadata={"batch_item_contract": {"acceptance": {"consumable_required": True}}},
        )
        consumable_preview_plan = strategy_plan_for_request(consumable_preview_request, route_for_request(consumable_preview_request))
        resource_cli_plan = next(item for item in consumable_preview_plan if item["tool"] == "resource_cli")
        assert_ok(resource_cli_plan["stage"] == ResourceStage.PREVIEW, "consumable URL requests must use preview instead of probe")
        results["resource_owner_executor"] = "ok"
        structured_batch_payload = {
            "schema": "resource.batch_request.v1",
            "batch_name": "three-project-analysis",
            "items": [
                {
                    "item_id": "next-ai-draw-io",
                    "target": "DayuanJiang/next-ai-draw-io",
                    "task": "read repository metadata and README",
                    "intent": ResourceIntent.EXTERNAL_DEPENDENCY,
                    "auto_owner": True,
                    "acceptance": {"minimum_candidates": 1, "provenance_required": True},
                },
                {
                    "item_id": "superversion",
                    "target": "superversion",
                    "task": "search GitHub repository",
                    "intent": ResourceIntent.EXTERNAL_DEPENDENCY,
                    "auto_owner": True,
                    "acceptance": {"minimum_candidates": 1, "provenance_required": True},
                },
                {
                    "item_id": "cloakbrowser",
                    "target": "CloakHQ/CloakBrowser",
                    "task": "read repository metadata and license",
                    "intent": ResourceIntent.EXTERNAL_DEPENDENCY,
                    "auto_owner": True,
                    "acceptance": {"minimum_candidates": 1, "provenance_required": True},
                },
            ],
            "execution": {"max_active": 3, "per_host_limit": 2, "fail_fast": False},
        }
        structured_requests = requests_from_payload(structured_batch_payload)
        assert_ok(len(structured_requests) == 3, "structured batch must preserve three independent resource needs")
        fake_pythonw = root / "pythonw.exe"
        fake_python = root / "python.exe"
        fake_pythonw.write_bytes(b"")
        fake_python.write_bytes(b"")
        assert_ok(
            resource_scheduler.console_python_executable(fake_pythonw) == str(fake_python),
            "background pythonw owners must launch stdout-producing Python CLIs through python.exe",
        )
        original_subprocess_run = resource_scheduler.subprocess.run
        try:
            resource_scheduler.subprocess.run = lambda *_args, **_kwargs: SimpleNamespace(
                returncode=0,
                stdout="",
                stderr="",
            )
            empty_stdout_result = resource_scheduler.run_json_command(["python.exe", "owner.py"], timeout=1)
        finally:
            resource_scheduler.subprocess.run = original_subprocess_run
        assert_ok(
            empty_stdout_result.get("reason") == "command_returned_empty_stdout",
            "owner subprocess success without JSON output must remain a diagnosable failure",
        )
        original_gateway_batch_plan = resource_scheduler.codex_network_gateway.batch_plan
        try:
            resource_scheduler.codex_network_gateway.batch_plan = lambda requests, **_kwargs: {
                "ok": True,
                "request_count": len(requests),
                "results": [],
            }
            owner_api_payload, owner_api_mode = resource_scheduler.network_gateway_batch_plan(
                [{"target_kind": "github", "target": "https://api.github.com/"}]
            )
        finally:
            resource_scheduler.codex_network_gateway.batch_plan = original_gateway_batch_plan
        assert_ok(owner_api_payload.get("ok") and owner_api_mode == "in_process_owner_api", "network batch planning should prefer the gateway owner API")
        assert_ok(
            [item.metadata["batch_item_contract"]["item_id"] for item in structured_requests]
            == ["next-ai-draw-io", "superversion", "cloakbrowser"],
            "structured batch item ids must not be flattened into one natural-language query",
        )
        original_scheduler_handle_request = resource_scheduler.handle_request
        original_scheduler_network_plans = resource_scheduler.precompute_network_plans
        try:
            resource_scheduler.precompute_network_plans = lambda scheduled, **_kwargs: (
                scheduled,
                {"schema": "resource_scheduler.network_batch.v1", "ok": True, "skipped": True, "reason": "test"},
            )
            structured_plan = resource_scheduler.execute_batch(
                structured_requests,
                config=ResourceBatchConfig(max_active=3, per_host_limit=2, plan_only=True),
                event_log=broker_events,
                receipt_log=broker_receipts,
                store_root=root / "structured-batch-plan-store",
            )

            def fake_batch_handle_request(request, **_kwargs):
                item_id = request.metadata["batch_item_contract"]["item_id"]
                if item_id == "next-ai-draw-io":
                    return SimpleNamespace(
                        ok=True,
                        status="completed",
                        request_id="res_next",
                        result_kind="github_repository_search",
                        manifest_path="next.json",
                        artifact_path="",
                        next_action="consume_resource",
                        error_class="",
                        network_summary={},
                        satisfaction={"satisfied": True, "reason": "completion_predicate_satisfied"},
                        attempts=[
                            {
                                "result": {
                                    "source": "github",
                                    "result_kind": "github_repository_search",
                                    "metadata": {"items": [{"full_name": "DayuanJiang/next-ai-draw-io", "html_url": "https://github.com/DayuanJiang/next-ai-draw-io"}]},
                                }
                            }
                        ],
                    )
                if item_id == "superversion":
                    return SimpleNamespace(
                        ok=False,
                        status="deferred",
                        request_id="res_superversion",
                        result_kind="metadata",
                        manifest_path="superversion.json",
                        artifact_path="",
                        next_action="refine_resource_delegation_and_retry",
                        error_class="minimum_candidates_not_met",
                        network_summary={},
                        satisfaction={"satisfied": False, "reason": "minimum_candidates_not_met"},
                        attempts=[{"result": {"source": "github", "result_kind": "github_repository_search", "metadata": {"items": []}}}],
                    )
                return SimpleNamespace(
                    ok=False,
                    status="failed",
                    request_id="res_cloakbrowser",
                    result_kind="none",
                    manifest_path="cloakbrowser.json",
                    artifact_path="",
                    next_action="try_next_route",
                    error_class="timeout",
                    network_summary={},
                    satisfaction={"satisfied": False, "reason": "timeout"},
                    attempts=[{"result": {"ok": False, "source": "github"}}],
                )

            resource_scheduler.handle_request = fake_batch_handle_request
            structured_batch = resource_scheduler.execute_batch(
                structured_requests,
                config=ResourceBatchConfig(max_active=3, per_host_limit=2),
                event_log=broker_events,
                receipt_log=broker_receipts,
                store_root=root / "structured-batch-store",
            )
        finally:
            resource_scheduler.handle_request = original_scheduler_handle_request
            resource_scheduler.precompute_network_plans = original_scheduler_network_plans
        structured_plan_progress = progress_for_batch(Path(structured_plan["manifest_path"]), include_items=True, limit=3)
        assert_ok(structured_plan["required_count"] == 3, "structured plan must preserve required-item count")
        assert_ok(
            [item["item_id"] for item in structured_plan_progress["items"]]
            == ["next-ai-draw-io", "superversion", "cloakbrowser"],
            "planned progress must expose stable structured item ids for follow-up refinement",
        )
        assert_ok(structured_batch["status"] == "partial" and not structured_batch["ok"], "one accepted item must not satisfy a three-item required batch")
        assert_ok(structured_batch["accepted_item_ids"] == ["next-ai-draw-io"], "batch receipt should expose accepted item ids")
        assert_ok(set(structured_batch["failed_item_ids"]) == {"superversion", "cloakbrowser"}, "batch receipt should expose failed item boundaries")
        assert_ok(structured_batch["unmet_required_count"] == 2, "aggregate completion must require every required item")
        batch_plan = execute_batch(
            [
                ResourceBrokerRequest(path=str(source), intent=ResourceIntent.EXPLICIT_LOCAL_FILE),
                ResourceBrokerRequest(
                    target="ruff",
                    task="inspect python package dependency",
                    intent=ResourceIntent.PACKAGE_DEPENDENCY,
                    auto_owner=True,
                    metadata={"validation_profile": "quick"},
                ),
            ],
            config=ResourceBatchConfig(plan_only=True),
            event_log=broker_events,
            receipt_log=broker_receipts,
            store_root=broker_cache,
        )
        assert_ok(batch_plan["status"] == "planned", "resource batch plan-only should not execute")
        assert_ok(batch_plan["required_count"] == 2, "plan-only batch must preserve required-item count from request contracts")
        assert_ok(
            [item["queue_class"] for item in batch_plan["planned"]] == ["local_light", "package_metadata"],
            "resource batch should classify queue classes",
        )
        assert_ok(
            batch_plan["planned"][-1]["host_key"] == "package:pypi",
            "package metadata batch item should use semantic package host key",
        )
        npm_batch_plan = execute_batch(
            [
                ResourceBrokerRequest(
                    target="left-pad",
                    task="inspect npm package dependency",
                    intent=ResourceIntent.PACKAGE_DEPENDENCY,
                    auto_owner=True,
                    metadata={"package_ecosystem": "npm"},
                ),
            ],
            config=ResourceBatchConfig(plan_only=True),
            event_log=broker_events,
            receipt_log=broker_receipts,
            store_root=broker_cache,
        )
        assert_ok(npm_batch_plan["planned"][0]["host_key"] == "package:npm", "npm package should use npm semantic host key")
        batch_run = execute_batch(
            [
                ResourceBrokerRequest(path=str(source), intent=ResourceIntent.EXPLICIT_LOCAL_FILE),
                ResourceBrokerRequest(
                    target="ruff",
                    task="inspect python package dependency",
                    intent=ResourceIntent.PACKAGE_DEPENDENCY,
                    auto_owner=True,
                    metadata={"validation_profile": "quick"},
                ),
            ],
            config=ResourceBatchConfig(max_active=2, per_host_limit=1),
            event_log=broker_events,
            receipt_log=broker_receipts,
            store_root=broker_cache,
        )
        assert_ok(batch_run["request_count"] == 2, "resource batch should include both requests")
        assert_ok(Path(batch_run["manifest_path"]).exists(), "resource batch manifest missing")
        assert_ok(batch_run["completed_count"] >= 1, "resource batch should complete at least local request")
        assert_ok(batch_run["results"][-1]["host_key"] == "package:pypi", "package result should retain semantic host key")
        assert_ok(
            batch_run["results"][-1].get("network_summary", {}).get("route_mode") == "validation_profile_skipped",
            "quick python package metadata should skip live network probe",
        )
        batch_status = batch_status_from_manifest(Path(batch_run["manifest_path"]))
        assert_ok(batch_status.get("read_ok") is True and "batch_ok" in batch_status, "batch-status should distinguish read and batch outcome")
        batch_progress = progress_for_batch(Path(batch_run["manifest_path"]), include_items=True, limit=1)
        assert_ok(batch_progress["kind"] == "batch" and batch_progress["counts"]["request"] == 2, "batch progress counts mismatch")
        assert_ok(batch_progress["item_total"] == 2 and len(batch_progress["items"]) == 1, "batch progress item limit mismatch")
        original_budget_handle_request = resource_scheduler.handle_request
        budget_handle_calls = {"count": 0}
        try:
            def fake_budget_handle_request(_request, **_kwargs):
                budget_handle_calls["count"] += 1
                time.sleep(1.1)
                return SimpleNamespace(
                    ok=True,
                    status="completed",
                    request_id=f"res_budget_{budget_handle_calls['count']}",
                    result_kind="artifact",
                    manifest_path="budget.json",
                    artifact_path=str(source),
                    next_action="consume_resource",
                    error_class="",
                    network_summary={},
                    satisfaction={"satisfied": True, "reason": "completion_predicate_satisfied"},
                    attempts=[{"result": {"ok": True, "source": "local", "local_path": str(source)}}],
                    execution_budget={},
                )

            resource_scheduler.handle_request = fake_budget_handle_request
            budget_batch = resource_scheduler.execute_batch(
                [
                    ResourceBrokerRequest(path=str(source), intent=ResourceIntent.EXPLICIT_LOCAL_FILE),
                    ResourceBrokerRequest(path=str(source), intent=ResourceIntent.EXPLICIT_LOCAL_FILE),
                ],
                config=ResourceBatchConfig(max_active=2, per_host_limit=1, total_budget_seconds=1),
                event_log=broker_events,
                receipt_log=broker_receipts,
                store_root=root / "budget-batch-store",
            )
        finally:
            resource_scheduler.handle_request = original_budget_handle_request
        assert_ok(not budget_batch["ok"], "batch must not report completed after its total budget is exhausted")
        assert_ok(budget_handle_calls["count"] == 1, "waiting batch items must not start after the shared deadline")
        assert_ok(
            any(item.get("error_class") == "total_budget_exhausted" for item in budget_batch["results"]),
            "batch deadline rejection should expose total_budget_exhausted",
        )
        original_fail_fast_handle = resource_scheduler.handle_request
        fail_fast_calls = {"count": 0}
        try:
            def fake_fail_fast_handle(_request, **_kwargs):
                fail_fast_calls["count"] += 1
                return SimpleNamespace(
                    ok=False,
                    status="failed",
                    request_id="res_fail_fast",
                    result_kind="none",
                    manifest_path="fail-fast.json",
                    artifact_path="",
                    next_action="surface_resource_failure",
                    error_class="test_required_failure",
                    network_summary={},
                    satisfaction={"satisfied": False, "reason": "test_required_failure"},
                    attempts=[{"result": {"ok": False, "source": "local"}}],
                    execution_budget={},
                )

            resource_scheduler.handle_request = fake_fail_fast_handle
            fail_fast_batch = resource_scheduler.execute_batch(
                [ResourceBrokerRequest(path=str(source), intent=ResourceIntent.EXPLICIT_LOCAL_FILE) for _index in range(3)],
                config=ResourceBatchConfig(max_active=1, per_host_limit=1, fail_fast=True),
                event_log=broker_events,
                receipt_log=broker_receipts,
                store_root=root / "fail-fast-batch-store",
            )
        finally:
            resource_scheduler.handle_request = original_fail_fast_handle
        assert_ok(not fail_fast_batch["ok"], "fail-fast batch must preserve the first required failure")
        assert_ok(fail_fast_calls["count"] == 1, "fail-fast must cancel pending work without killing the running item")
        assert_ok(
            sum(1 for item in fail_fast_batch["results"] if item.get("error_class") == "fail_fast_cancelled") == 2,
            "fail-fast cancellation must leave explicit item receipts",
        )
        batch_cli_payload = root / "batch-cli-payload.json"
        batch_cli_payload.write_text(
            json.dumps(
                {
                    "requests": [
                        {
                            "path": str(source),
                            "intent": ResourceIntent.EXPLICIT_LOCAL_FILE,
                            "need_materialization": True,
                            "allow_filesystem_write": True,
                            "target_dir": str(root / "batch-cli-cache"),
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        batch_cli_stdout = io.StringIO()
        with contextlib.redirect_stdout(batch_cli_stdout):
            batch_cli_code = resource_cli_main(
                [
                    "request-batch",
                    "--payload-file",
                    str(batch_cli_payload),
                    "--max-active",
                    "1",
                    "--per-host-limit",
                    "1",
                    "--store-root",
                    str(root / "batch-cli-store"),
                    "--event-log",
                    str(root / "batch-cli-events.jsonl"),
                    "--receipt-log",
                    str(root / "batch-cli-receipts.jsonl"),
                    "--no-resource-log",
                    "--json",
                ]
            )
        batch_cli_result = json.loads(batch_cli_stdout.getvalue())
        assert_ok(batch_cli_code == 0, "resource_cli request-batch compact should succeed")
        assert_ok(batch_cli_result["receipt_detail"] == "compact", "request-batch should default to compact stdout")
        assert_ok("planned" not in batch_cli_result and "results" not in batch_cli_result, "compact request-batch stdout should omit verbose planned/results")
        assert_ok(Path(batch_cli_result["full_manifest_path"]).exists(), "compact request-batch should keep full manifest path")
        results["resource_scheduler"] = "ok"
        attached_package = attach_result_to_request(
            request_id=package_request.request_id,
            source_tool="uv",
            result_kind="package_install_receipt",
            content="simulated package manager receipt: ruff already available",
            metadata={"package": "ruff", "risk": "install_side_effect", "executed": False},
            receipt_log=broker_receipts,
        )
        assert_ok(attached_package["ok"] and attached_package["status"] == "completed", "package owner result attach should complete request")
        network_blocked = isolated_handle_request(
            ResourceBrokerRequest(
                url="https://example.invalid/resource.txt",
                task="preview blocked network URL",
                intent=ResourceIntent.EXPLICIT_USER_URL,
                allow_network=False,
            ),
            event_log=broker_events,
            receipt_log=broker_receipts,
        )
        assert_ok(network_blocked.status == "failed", "network-disabled URL should fail closed")
        assert_ok(network_blocked.error_class == "network_not_allowed", "network-disabled URL should report network_not_allowed")
        network_blocked_progress = progress_for_request(network_blocked.request_id, receipt_log=broker_receipts)
        assert_ok(network_blocked_progress["exception"]["has_exception"] is True, "failed progress should expose exception")
        assert_ok(network_blocked_progress["exception"]["error_class"] == "network_not_allowed", "failed progress should expose error class")
        assert_ok(network_blocked_progress["exception"]["reason"] == "network_not_allowed", "failed progress should expose reason")
        assert_ok(network_blocked.route["primary_tool"] == "resource_cli", "plain no-network URL should not overmatch browser tooling")
        materialize_blocked = isolated_handle_request(
            ResourceBrokerRequest(
                url="https://example.invalid/resource.txt",
                task="materialize without write grant",
                intent=ResourceIntent.EXPLICIT_USER_URL,
                need_materialization=True,
                allow_filesystem_write=False,
                timeout_seconds=2,
            ),
            event_log=broker_events,
            receipt_log=broker_receipts,
        )
        assert_ok(materialize_blocked.status == "failed", "materialization without write grant should fail closed")
        assert_ok(materialize_blocked.error_class == "filesystem_write_not_allowed", "materialization block should expose filesystem_write_not_allowed")
        original_execute_owner_tool = resource_broker.execute_owner_tool
        try:
            def fake_github_missing_owner(**_kwargs):
                return {
                    "ok": False,
                    "status": "failed",
                    "source": "github",
                    "result_kind": "github_repo_metadata",
                    "error_class": "http_status",
                    "reason": "http_status=404",
                    "metadata": {
                        "http_status": 404,
                        "owner_execution_route": "test_fixture_github_api",
                    },
                    "next_action": "surface_terminal_http_status",
                }

            resource_broker.execute_owner_tool = fake_github_missing_owner
            github_missing = isolated_handle_request(
                ResourceBrokerRequest(
                    url="https://github.com/openai/this-repo-should-not-exist-codex-resource-test",
                    task="inspect missing github repo metadata",
                    intent=ResourceIntent.EXTERNAL_DEPENDENCY,
                    auto_owner=True,
                    timeout_seconds=10,
                ),
                event_log=broker_events,
                receipt_log=broker_receipts,
            )
        finally:
            resource_broker.execute_owner_tool = original_execute_owner_tool
        assert_ok(github_missing.status == "failed", "missing GitHub owner failure should not be masked by resource_cli discover")
        assert_ok(github_missing.error_class == "http_status", "missing GitHub repo should preserve owner http_status failure")
        npm_handoff = isolated_handle_request(
            ResourceBrokerRequest(
                target="left-pad",
                task="install npm package dependency",
                intent=ResourceIntent.PACKAGE_DEPENDENCY,
                auto_owner=True,
                metadata={"package_ecosystem": "npm", "package_action": "install"},
                timeout_seconds=10,
            ),
            event_log=broker_events,
            receipt_log=broker_receipts,
        )
        assert_ok(npm_handoff.status == "handoff_required", "npm package install should require explicit approval")
        assert_ok(npm_handoff.error_class == "install_requires_explicit_approval", "npm package adapter must preserve the install approval boundary")
        windows_tool_plan = isolated_handle_request(
            ResourceBrokerRequest(
                target="aria2",
                task="inspect Windows tool package plan",
                intent=ResourceIntent.PACKAGE_DEPENDENCY,
                auto_owner=True,
                metadata={
                    "package_ecosystem": "windows_tool",
                    "validation_profile": "quick",
                    "windows_package_manager": "choco",
                },
                timeout_seconds=5,
            ),
            event_log=broker_events,
            receipt_log=broker_receipts,
        )
        assert_ok(windows_tool_plan.status == "completed", "windows tool auto-owner should return a deterministic plan")
        windows_tool_owner_result = windows_tool_plan.attempts[0]["result"]
        assert_ok(windows_tool_owner_result.get("result_kind") == "windows_package_manager_plan", "windows tool plan should expose package-manager plan kind")
        assert_ok(windows_tool_owner_result.get("metadata", {}).get("will_install") is False, "windows tool plan should not install by default")
        windows_install_gate = isolated_handle_request(
            ResourceBrokerRequest(
                target="aria2",
                task="install Windows tool without approval",
                intent=ResourceIntent.PACKAGE_DEPENDENCY,
                auto_owner=True,
                metadata={
                    "package_ecosystem": "windows_tool",
                    "validation_profile": "quick",
                    "windows_package_manager": "choco",
                    "package_action": "install",
                },
                timeout_seconds=5,
            ),
            event_log=broker_events,
            receipt_log=broker_receipts,
        )
        assert_ok(windows_install_gate.status == "handoff_required", "windows tool install should require explicit approval")
        assert_ok(windows_install_gate.error_class == "install_requires_explicit_approval", "windows tool install gate should expose approval reason")
        python_install_target = root / "python-package-target"
        original_python_install_run = resource_python_package_installer.subprocess.run
        try:
            def fake_python_install_run(command, **_kwargs):
                if str(command[0]).lower().endswith("icacls"):
                    return SimpleNamespace(returncode=0, stdout="acl inheritance enabled", stderr="")
                target = Path(command[command.index("--target") + 1])
                dist_info = target / "example_pkg-1.2.3.dist-info"
                dist_info.mkdir(parents=True, exist_ok=True)
                (dist_info / "METADATA").write_text("Name: example-pkg\nVersion: 1.2.3\n", encoding="utf-8")
                return SimpleNamespace(returncode=0, stdout="installed example-pkg", stderr="")

            resource_python_package_installer.subprocess.run = fake_python_install_run
            python_install = resource_package_owner.execute_package_metadata(
                {
                    "target": "example-pkg==1.2.3",
                    "target_dir": str(python_install_target),
                    "allow_filesystem_write": True,
                    "metadata": {
                        "package_ecosystem": "python",
                        "package_action": "install",
                        "package_target_dir_explicit": True,
                        "install_approved": True,
                        "validation_profile": "quick",
                    },
                },
                {"ok": True, "plan": {"route_mode": "probe_selected_direct", "target_kind": "package", "env": {}, "unset_env": []}},
                10,
                lambda **payload: payload,
            )
        finally:
            resource_python_package_installer.subprocess.run = original_python_install_run
        assert_ok(python_install.get("status") == "completed", f"approved Python install should execute: {python_install}")
        assert_ok(python_install.get("result_kind") == "python_package_install", "Python install should not complete as metadata")
        assert_ok(python_install.get("metadata", {}).get("installed_version") == "1.2.3", "Python install should verify distribution metadata")
        assert_ok(python_install.get("metadata", {}).get("runtime_acl", {}).get("ok") is True, "managed Python install should normalize runtime ACLs")
        winget_search = resource_windows_package_manager._search_command(
            "winget",
            "example",
            "winget",
            {},
            {"metadata": {"winget_id": "Vendor.Example", "accept_winget_agreements": True}},
        )
        assert_ok(winget_search[1] == "search", "WinGet search must place the subcommand before options")
        assert_ok(winget_search[2:5] == ["--id", "Vendor.Example", "--exact"], "WinGet ID search should be exact")
        assert_ok("--source" in winget_search and "winget" in winget_search, "WinGet ID search should avoid unrelated sources")
        assert_ok("--disable-interactivity" in winget_search, "WinGet search should remain noninteractive")
        assert_ok("--accept-source-agreements" in winget_search, "Approved WinGet search should accept source agreements")
        winget_install = resource_windows_package_manager._install_command(
            "winget",
            "example",
            "winget",
            {},
            {
                "metadata": {
                    "winget_id": "Vendor.Example",
                    "accept_winget_agreements": True,
                }
            },
        )
        assert_ok(winget_install[1] == "install", "WinGet install must place the subcommand before options")
        assert_ok(winget_install.count("install") == 1, "WinGet install command must contain one install subcommand")
        assert_ok(winget_install[-2:] == ["--id", "Vendor.Example"], "WinGet install should preserve the exact package id")
        winget_probe = resource_broker.package_probe_target_for_request(
            ResourceBrokerRequest(
                target="Vendor.Example",
                metadata={"package_ecosystem": "windows", "windows_package_manager": "winget"},
            )
        )
        choco_probe = resource_broker.package_probe_target_for_request(
            ResourceBrokerRequest(
                target="example",
                metadata={"package_ecosystem": "windows", "windows_package_manager": "choco"},
            )
        )
        assert_ok(winget_probe == "https://cdn.winget.microsoft.com/cache", "WinGet package requests must probe the WinGet source")
        assert_ok(choco_probe == "https://community.chocolatey.org/api/v2/", "Chocolatey package requests must probe the Chocolatey source")
        original_windows_run = resource_windows_package_manager._run
        original_windows_availability = resource_windows_package_manager.availability
        try:
            resource_windows_package_manager.availability = lambda: {
                "choco_available": True,
                "choco_path": "choco",
                "winget_available": False,
                "winget_path": "",
            }
            resource_windows_package_manager._run = lambda *_args, **_kwargs: {
                "ok": True,
                "returncode": 0,
                "stdout": "ok",
                "stderr": "",
                "error_class": "",
                "reason": "",
            }
            quick_windows_install = resource_windows_package_manager.execute_windows_package_request(
                {
                    "target": "aria2",
                    "metadata": {
                        "package_ecosystem": "windows_tool",
                        "validation_profile": "quick",
                        "windows_package_manager": "choco",
                        "package_action": "install",
                        "install_approved": True,
                    },
                },
                "aria2",
                "windows_tool",
                {"ok": True, "route_mode": "probe_selected_direct", "target_kind": "package", "env": {}, "unset_env": []},
                10,
                lambda **payload: payload,
            )
            resource_windows_package_manager.availability = lambda: {
                "choco_available": False,
                "choco_path": "",
                "winget_available": True,
                "winget_path": "winget",
            }
            live_windows_search = resource_windows_package_manager.execute_windows_package_request(
                {
                    "target": "Vendor.Example",
                    "metadata": {
                        "package_ecosystem": "windows",
                        "validation_profile": "full",
                        "windows_package_manager": "winget",
                        "winget_id": "Vendor.Example",
                        "package_action": "search",
                    },
                },
                "Vendor.Example",
                "windows",
                {"ok": True, "route_mode": "probe_selected_direct", "target_kind": "package", "env": {}, "unset_env": []},
                10,
                lambda **payload: payload,
            )
        finally:
            resource_windows_package_manager._run = original_windows_run
            resource_windows_package_manager.availability = original_windows_availability
        assert_ok(quick_windows_install.get("result_kind") == "windows_package_manager_install", "quick profile must not downgrade an approved Windows install")
        assert_ok(quick_windows_install.get("metadata", {}).get("will_install") is True, "approved Windows install should preserve write semantics")
        assert_ok(live_windows_search.get("status") == "completed", "successful WinGet search should complete")
        assert_ok(live_windows_search.get("metadata", {}).get("candidate_count") == 1, "non-empty WinGet search should report one or more candidates")
        ambiguous_request = isolated_handle_request(
            ResourceBrokerRequest(
                url="https://example.invalid/resource.txt",
                path=str(source),
                task="ambiguous source",
            ),
            event_log=broker_events,
            receipt_log=broker_receipts,
        )
        assert_ok(ambiguous_request.status == "failed", "ambiguous url+path request should fail closed")
        assert_ok(tuple(ambiguous_request.route["risk_flags"]) == ("ambiguous_reference",), "ambiguous request should expose risk flag")
        missing_local = isolated_handle_request(
            ResourceBrokerRequest(
                path=str(root / "missing.txt"),
                task="read missing local file",
                intent=ResourceIntent.EXPLICIT_LOCAL_FILE,
            ),
            event_log=broker_events,
            receipt_log=broker_receipts,
        )
        assert_ok(missing_local.status == "failed", "missing local file should fail closed")
        assert_ok(bool(missing_local.error_class), "missing local file should expose error class")
        missing_local_progress = progress_for_request(missing_local.request_id, receipt_log=broker_receipts)
        assert_ok(
            missing_local_progress["codex_next_action"] == "inspect_error_and_retry_or_escalate",
            "missing local file progress should not suggest copy/verify",
        )
        attached_docs = attach_result_to_request(
            request_id=broker_docs.request_id,
            source_tool="context7",
            result_kind="docs",
            content="json module documentation summary",
            metadata={"owner_tool_result": "simulated"},
            receipt_log=broker_receipts,
        )
        assert_ok(attached_docs["ok"] and attached_docs["status"] == "completed", "owner tool attach should complete resource request")
        assert_ok(Path(attached_docs["content_ref"]).exists(), "owner tool attached content missing")
        attach_stdout = io.StringIO()
        with contextlib.redirect_stdout(attach_stdout):
            attach_code = resource_cli_main(
                [
                    "attach-result",
                    "--request-id",
                    broker_docs.request_id,
                    "--source-tool",
                    "context7",
                    "--result-kind",
                    "docs",
                    "--content",
                    "updated json module documentation summary",
                    "--receipt-log",
                    str(broker_receipts),
                    "--json",
                ]
            )
        assert_ok(attach_code == 0 and json.loads(attach_stdout.getvalue())["ok"], "resource_cli attach-result failed")
        hub_tool_names = {item["name"] for item in LocalMcpHub().tools_list({})["tools"]}
        assert_ok("resource.progress" in hub_tool_names, "Hub resource.progress tool missing")
        assert_ok("resource.request_batch" in hub_tool_names, "Hub resource.request_batch tool missing")
        assert_ok("resource.attach_result" in hub_tool_names, "Hub resource.attach_result tool missing")
        assert_ok("resource_search.text" in hub_tool_names, "Hub resource_search.text tool missing")
        assert_ok("resource_search.health" in hub_tool_names, "Hub resource_search.health tool missing")
        hub_batch = LocalMcpHub().resource_request_batch(
            {
                "schema": "resource.batch_request.v1",
                "batch_name": "hub-compact-receipt-test",
                "items": [
                    {
                        "item_id": "local-source-a",
                        "path": str(source),
                        "task": "inspect local source A",
                        "intent": ResourceIntent.EXPLICIT_LOCAL_FILE,
                    },
                    {
                        "item_id": "local-source-b",
                        "path": str(source),
                        "task": "inspect local source B",
                        "intent": ResourceIntent.EXPLICIT_LOCAL_FILE,
                    },
                ],
                "execution": {"plan_only": True, "max_active": 2, "per_host_limit": 1},
                "store_root": str(root / "hub-batch-store"),
            }
        )
        assert_ok(hub_batch.get("receipt_detail") == "compact", "Hub batch requests should default to compact conversation receipts")
        assert_ok(
            [item.get("item_id") for item in hub_batch.get("items", [])] == ["local-source-a", "local-source-b"],
            "Hub compact batch receipts must preserve stable item ids",
        )
        request_stdout = io.StringIO()
        with contextlib.redirect_stdout(request_stdout):
            request_code = resource_cli_main(
                [
                    "request",
                    "--path",
                    str(source),
                    "--intent",
                    ResourceIntent.EXPLICIT_LOCAL_FILE,
                    "--need-materialization",
                    "--allow-filesystem-write",
                    "--target-dir",
                    str(root / "cli-broker-cache"),
                    "--store-root",
                    str(root / "cli-broker-store"),
                    "--event-log",
                    str(root / "cli-broker-events.jsonl"),
                    "--receipt-log",
                    str(root / "cli-broker-receipts.jsonl"),
                    "--no-resource-log",
                    "--json",
                ]
            )
        request_payload = json.loads(request_stdout.getvalue())
        assert_ok(request_code == 0 and request_payload["ok"], "resource_cli request failed")
        assert_ok(Path(request_payload["manifest_path"]).exists(), "resource_cli request manifest missing")
        status_stdout = io.StringIO()
        with contextlib.redirect_stdout(status_stdout):
            status_code = resource_cli_main(
                [
                    "status",
                    "--request-id",
                    request_payload["request_id"],
                    "--receipt-log",
                    str(root / "cli-broker-receipts.jsonl"),
                    "--json",
                ]
            )
        assert_ok(status_code == 0, "resource_cli status failed")
        assert_ok(json.loads(status_stdout.getvalue())["status"] == "completed", "resource_cli status receipt mismatch")
        progress_stdout = io.StringIO()
        with contextlib.redirect_stdout(progress_stdout):
            progress_code = resource_cli_main(
                [
                    "progress",
                    "--request-id",
                    request_payload["request_id"],
                    "--receipt-log",
                    str(root / "cli-broker-receipts.jsonl"),
                    "--json",
                ]
            )
        progress_payload = json.loads(progress_stdout.getvalue())
        assert_ok(progress_code == 0 and progress_payload["summary"].startswith("completed"), "resource_cli progress failed")
        assert_ok(
            progress_payload["resource_layer_terminal"] is True and progress_payload["end_to_end_terminal"] is False,
            "completed unread progress should be resource-layer terminal but not end-to-end terminal",
        )
        assert_ok(progress_payload["consume_required"] is True, "completed unread progress should require Codex consumption")
        unrelated_consumption = mark_request_consumed(
            request_id=request_payload["request_id"],
            consumed_path=str(source),
            receipt_log=root / "cli-broker-receipts.jsonl",
        )
        assert_ok(
            unrelated_consumption.get("reason") == "consumed_path_not_owned_by_request",
            "resource consumption should reject paths outside the request-owned result set",
        )
        consume_stdout = io.StringIO()
        with contextlib.redirect_stdout(consume_stdout):
            consume_code = resource_cli_main(
                [
                    "job",
                    "consume",
                    "--request-id",
                    request_payload["request_id"],
                    "--consumed-path",
                    request_payload["manifest_path"],
                    "--receipt-log",
                    str(root / "cli-broker-receipts.jsonl"),
                    "--json",
                ]
            )
        consume_payload = json.loads(consume_stdout.getvalue())
        assert_ok(consume_code == 0, "resource job consume should accept a request-owned result path")
        assert_ok(consume_payload["end_to_end_terminal"] is True, "consumed resource should become end-to-end terminal")
        assert_ok(consume_payload["consume_required"] is False, "consumed resource should clear the consume requirement")
        assert_ok(
            consume_payload["receipt"]["consumption"]["mode"] == "path_consumed",
            "resource job consume should persist path-consumed evidence",
        )
        job_stdout = io.StringIO()
        with contextlib.redirect_stdout(job_stdout):
            job_code = resource_cli_main(
                [
                    "job",
                    "submit",
                    "--task",
                    "resource job facade test",
                    "--path",
                    str(source),
                    "--intent",
                    ResourceIntent.EXPLICIT_LOCAL_FILE,
                    "--need-materialization",
                    "--allow-filesystem-write",
                    "--target-dir",
                    str(root / "job-broker-cache"),
                    "--store-root",
                    str(root / "job-broker-store"),
                    "--event-log",
                    str(root / "job-broker-events.jsonl"),
                    "--receipt-log",
                    str(root / "job-broker-receipts.jsonl"),
                    "--no-resource-log",
                    "--foreground",
                    "--json",
                ]
            )
        job_payload = json.loads(job_stdout.getvalue())
        assert_ok(job_code == 0 and job_payload["mode"] == "foreground", "resource job foreground submit failed")
        assert_ok(job_payload["resource_layer_terminal"] is True, "resource job foreground should return terminal receipt")
        assert_ok(job_payload["resource_need_satisfied"] is True, "completed resource job should satisfy resource need")
        assert_ok(job_payload["same_need_fetch_allowed"] is False, "completed resource job should not need a replacement fetch")
        job_run_stdout = io.StringIO()
        with contextlib.redirect_stdout(job_run_stdout):
            job_run_code = resource_cli_main(
                [
                    "job",
                    "run",
                    "--task",
                    "resource job blocking run test",
                    "--path",
                    str(source),
                    "--intent",
                    ResourceIntent.EXPLICIT_LOCAL_FILE,
                    "--need-materialization",
                    "--allow-filesystem-write",
                    "--target-dir",
                    str(root / "job-run-broker-cache"),
                    "--store-root",
                    str(root / "job-run-broker-store"),
                    "--event-log",
                    str(root / "job-run-broker-events.jsonl"),
                    "--receipt-log",
                    str(root / "job-run-broker-receipts.jsonl"),
                    "--no-resource-log",
                    "--json",
                ]
            )
        job_run_payload = json.loads(job_run_stdout.getvalue())
        assert_ok(job_run_code == 0 and job_run_payload["mode"] == "blocking", "resource job run failed")
        assert_ok(job_run_payload["acquisition_owner"] == "resource_layer", "resource job run should expose acquisition owner")
        assert_ok(job_run_payload["status_summary"]["state"] == "completed", "resource job run should expose status summary")
        assert_ok(job_run_payload["progress"]["percent"] == 100, "resource job run should expose progress percent")
        assert_ok(job_run_payload["codex_next_action"] == "consume_resource", "resource job run should expose Codex next action")
        assert_ok(
            job_run_payload["ownership"]["duplicate_fetch_policy"]["same_need"]
            == "do_not_start_direct_fetch_while_resource_layer_owns_request",
            "resource job run should expose same-need duplicate fetch policy",
        )
        assert_ok(job_run_payload["resource_layer_terminal"] is True, "resource job run should block until terminal receipt")
        assert_ok(job_run_payload["resource_need_satisfied"] is True, "completed job run should satisfy resource need")
        assert_ok(job_run_payload["same_need_fetch_allowed"] is False, "completed job run should not allow duplicate fetch")
        assert_ok(job_run_payload["receipt"]["status"] == "completed", "resource job run should return the terminal receipt")
        no_read_stdout = io.StringIO()
        with contextlib.redirect_stdout(no_read_stdout):
            no_read_code = resource_cli_main(
                [
                    "job",
                    "consume",
                    "--request-id",
                    job_run_payload["request_id"],
                    "--no-read-needed-reason",
                    "local copy result verified from the terminal receipt",
                    "--receipt-log",
                    str(root / "job-run-broker-receipts.jsonl"),
                    "--json",
                ]
            )
        no_read_payload = json.loads(no_read_stdout.getvalue())
        assert_ok(no_read_code == 0, "resource job consume should accept explicit no-read-needed evidence")
        assert_ok(no_read_payload["end_to_end_terminal"] is True, "no-read-needed evidence should complete the end-to-end lifecycle")
        assert_ok(
            no_read_payload["receipt"]["consumption"]["mode"] == "no_read_needed",
            "resource job consume should persist no-read-needed evidence",
        )
        original_get_call_hub_tool = resource_owner_executor.call_hub_tool
        try:
            def fake_get_github_search_hub_tool(tool, arguments, *, timeout=30):
                if tool == "github.api" and arguments.get("path") == "/search/repositories":
                    return {
                        "ok": True,
                        "result": {
                            "total_count": 1,
                            "incomplete_results": False,
                            "items": [
                                {
                                    "full_name": "IBM/mcp-context-forge",
                                    "description": "MCP gateway test fixture",
                                    "html_url": "https://github.com/IBM/mcp-context-forge",
                                    "language": "Python",
                                    "stargazers_count": 4058,
                                    "forks_count": 741,
                                    "open_issues_count": 1055,
                                    "updated_at": "2026-07-08T22:07:29Z",
                                    "topics": ["mcp", "gateway"],
                                }
                            ],
                        },
                        "hub_transport": "test_hub",
                        "token_source": "test",
                    }
                return original_get_call_hub_tool(tool, arguments, timeout=timeout)

            resource_owner_executor.call_hub_tool = fake_get_github_search_hub_tool
            get_stdout = io.StringIO()
            with contextlib.redirect_stdout(get_stdout):
                get_code = resource_cli_main(
                    [
                        "get",
                        "--task",
                        "find GitHub projects for local MCP gateway",
                        "--target",
                        "mcp gateway",
                        "--event-log",
                        str(root / "get-github-events.jsonl"),
                        "--receipt-log",
                        str(root / "get-github-receipts.jsonl"),
                        "--store-root",
                        str(root / "get-github-store"),
                        "--no-resource-log",
                        "--json",
                    ]
                )
        finally:
            resource_owner_executor.call_hub_tool = original_get_call_hub_tool
        get_payload = json.loads(get_stdout.getvalue())
        assert_ok(get_code == 0 and get_payload["schema"] == "resource_get.result.v1", "resource get command should return resource_get schema")
        assert_ok(get_payload["status"] == "completed", "resource get should complete GitHub search through resource layer")
        assert_ok(get_payload["result_kind"] == "github_repository_search", "resource get should preserve owner result kind")
        assert_ok(get_payload["consume_required"] is False, "resource get --read-result should satisfy the consume requirement")
        assert_ok(get_payload["receipt"]["consumption"]["satisfied"] is True, "resource get should persist result consumption")
        assert_ok(get_payload["text_result"]["consumption_recorded"] is True, "resource get should expose automatic consumption recording")
        assert_ok("IBM/mcp-context-forge" in get_payload["text_result"]["excerpt"], "resource get should return readable owner-result excerpt")
        package_install_stdout = io.StringIO()
        with contextlib.redirect_stdout(package_install_stdout):
            package_install_code = resource_cli_main(
                [
                    "job",
                    "run",
                    "--task",
                    "install aria2 Windows tool",
                    "--target",
                    "aria2",
                    "--intent",
                    ResourceIntent.PACKAGE_DEPENDENCY,
                    "--package-ecosystem",
                    "windows_tool",
                    "--package-action",
                    "install",
                    "--windows-package-manager",
                    "choco",
                    "--validation-profile",
                    "quick",
                    "--store-root",
                    str(root / "job-package-install-store"),
                    "--event-log",
                    str(root / "job-package-install-events.jsonl"),
                    "--receipt-log",
                    str(root / "job-package-install-receipts.jsonl"),
                    "--no-resource-log",
                    "--json",
                ]
            )
        package_install_payload = json.loads(package_install_stdout.getvalue())
        assert_ok(package_install_code == 0, "resource job package install gate should return a usable receipt")
        assert_ok(package_install_payload["status"] == "handoff_required", "package install without approval should be resource-layer handoff")
        assert_ok(package_install_payload["receipt"]["error_class"] == "install_requires_explicit_approval", "package install handoff should expose approval gate")
        assert_ok(package_install_payload["same_need_fetch_allowed"] is False, "package install handoff should keep resource ownership")
        compact_stdout = io.StringIO()
        with contextlib.redirect_stdout(compact_stdout):
            compact_code = resource_cli_main(
                [
                    "job",
                    "run",
                    "--task",
                    "resource job compact receipt test",
                    "--path",
                    str(source),
                    "--intent",
                    ResourceIntent.EXPLICIT_LOCAL_FILE,
                    "--need-materialization",
                    "--allow-filesystem-write",
                    "--target-dir",
                    str(root / "job-compact-broker-cache"),
                    "--store-root",
                    str(root / "job-compact-store"),
                    "--event-log",
                    str(root / "job-compact-events.jsonl"),
                    "--receipt-log",
                    str(root / "job-compact-receipts.jsonl"),
                    "--receipt-detail",
                    "compact",
                    "--no-resource-log",
                    "--json",
                ]
            )
        compact_payload = json.loads(compact_stdout.getvalue())
        assert_ok(compact_code == 0 and compact_payload["receipt_detail"] == "compact", "compact receipt job run should succeed")
        assert_ok(compact_payload["receipt"]["request_id"] == compact_payload["request_id"], "compact receipt should keep request id")
        assert_ok("progress_events" not in compact_payload["receipt"], "compact receipt should omit verbose progress events")
        assert_ok(compact_payload["receipt"]["attempts"][0]["tool"], "compact receipt should keep attempt summaries")
        with staged_http_server() as fast_url:
            fast_stdout = io.StringIO()
            with contextlib.redirect_stdout(fast_stdout):
                fast_code = resource_cli_main(
                    [
                        "materialize-url",
                        f"{fast_url}/probe.txt",
                        "--task",
                        "resource fast materialize test",
                        "--name",
                        "fast-probe.txt",
                        "--target-dir",
                        str(root / "fast-materialize-cache"),
                        "--store-root",
                        str(root / "fast-materialize-store"),
                        "--receipt-log",
                        str(root / "fast-materialize-receipts.jsonl"),
                        "--resource-log",
                        str(root / "fast-materialize-resource.jsonl"),
                        "--json",
                    ]
                )
        fast_payload = json.loads(fast_stdout.getvalue())
        assert_ok(fast_code == 0 and fast_payload["mode"] == "lightweight", "fast materialize URL should use lightweight mode")
        assert_ok(fast_payload["resource_need_satisfied"] is True, "fast materialize should satisfy resource need")
        assert_ok(Path(fast_payload["receipt"]["artifact_path"]).exists(), "fast materialize artifact missing")
        assert_ok(Path(fast_payload["receipt"]["manifest_path"]).exists(), "fast materialize manifest missing")
        assert_ok(fast_payload["receipt"]["route"]["primary_tool"] == "resource_cli", "fast materialize should stay in resource layer")
        fast_delegate_stdout = io.StringIO()
        with contextlib.redirect_stdout(fast_delegate_stdout):
            fast_delegate_code = resource_cli_main(
                [
                    "delegate",
                    "--task",
                    "download resolved URL",
                    "--url",
                    "https://example.com/file.jpg",
                    "--name",
                    "file.jpg",
                    "--intent",
                    ResourceIntent.EXPLICIT_USER_URL,
                    "--need-materialization",
                    "--allow-filesystem-write",
                    "--json",
                ]
            )
        fast_delegate_payload = json.loads(fast_delegate_stdout.getvalue())
        assert_ok(fast_delegate_code == 0, "fast materialize delegation should build")
        assert_ok(fast_delegate_payload["fast_materialize"]["allowed"] is True, "resolved URL delegation should expose fast path")
        assert_ok("materialize-url" in fast_delegate_payload["fast_materialize"]["command"], "fast path command should use materialize-url")
        photo_route = build_intent_resource_route("下载一张苹果总部建筑照片")
        photo_contract = photo_route["resource_layer_contract"]
        assert_ok(photo_contract["task_class"] == "materialization_needs_source_selection", "download without URL should route source selection to resource layer")
        assert_ok(photo_contract["codex_url_discovery_allowed"] is False, "download without URL should not default to Codex URL discovery")
        assert_ok(photo_contract["resource_layer_source_selection_required"] is True, "download without URL should require resource-layer source selection")
        assert_ok(photo_contract["resource_layer_source_discovery_required"] is True, "download without URL should require resource-layer URL discovery")
        assert_ok(photo_contract["source_discovery_owner"] == "resource_layer", "source discovery should be owned by resource layer")
        assert_ok(photo_contract["candidate_review_before_materialization"] is True, "download without URL should return candidates before materialization")
        assert_ok(photo_contract["candidate_review_owner"] == "codex", "Codex should review candidates before follow-up materialization")
        assert_ok(photo_contract["materialization_requires_resource_layer"] is True, "download without URL should still require resource materialization")
        assert_ok(photo_contract["direct_resource_delegation_preferred"] is True, "download without URL should still prefer direct resource delegation")
        assert_ok(
            photo_contract["unsuitable_result_policy"]["default_action"] == "refine_resource_delegation_and_retry",
            "unsuitable resource results should refine delegation before Codex direct fetch",
        )
        assert_ok(
            photo_contract["result_iteration_policy"]["resource_layer_keeps_first_priority"] is True,
            "resource layer should remain first priority after unsuitable results",
        )
        docs_route = build_intent_resource_route("联网搜索 GitHub 上适合本机的网络网关项目")
        docs_contract = docs_route["resource_layer_contract"]
        assert_ok(docs_contract["direct_resource_delegation_preferred"] is True, "research-only lookup should go directly to resource layer")
        assert_ok(docs_contract["codex_url_discovery_allowed"] is False, "research-only lookup should not trigger Codex URL discovery phase")
        assert_ok(docs_contract["resource_layer_source_discovery_required"] is True, "research-only lookup should delegate URL/source discovery")
        assert_ok(docs_contract["source_discovery_owner"] == "resource_layer", "research-only source discovery owner mismatch")
        photo_delegation = build_delegation(
            task="下载一张苹果总部建筑照片",
            need_materialization=True,
            allow_filesystem_write=True,
        )
        photo_expectation = photo_delegation["request"]["metadata"]["codex_expectation"]
        assert_ok(photo_expectation["task_class"] == "materialization_needs_source_selection", "delegation should expose resource source-selection task class")
        assert_ok(photo_expectation["codex_url_discovery_allowed"] is False, "delegation should not expose default URL discovery allowance")
        assert_ok(photo_expectation["resource_layer_source_selection_required"] is True, "delegation should expose source-selection owner")
        assert_ok(photo_expectation["resource_layer_source_discovery_required"] is True, "delegation should expose source-discovery owner")
        assert_ok(photo_expectation["source_discovery_owner"] == "resource_layer", "delegation source-discovery owner mismatch")
        assert_ok(photo_expectation["candidate_review_before_materialization"] is True, "delegation should expose candidate-review gate")
        assert_ok(photo_expectation["candidate_review_owner"] == "codex", "delegation should keep candidate decision with Codex")
        assert_ok(
            photo_delegation["request"]["metadata"].get("source_selection_only") is True,
            "download without URL should default to source-selection-only before materialization",
        )
        assert_ok(
            photo_delegation["request"]["metadata"].get("candidate_review_next_action")
            == "codex_selects_candidate_or_refines_request_then_resubmits",
            "candidate-first delegation should expose follow-up action",
        )
        assert_ok(photo_expectation["materialization_requires_resource_layer"] is True, "delegation should keep materialization in resource layer")
        assert_ok(
            photo_expectation["unsuitable_result_policy"]["do_not_default_to_codex_direct_fetch"] is True,
            "delegation should keep unsuitable-result retries inside resource layer first",
        )
        paper_get_payload = build_get_payload(
            SimpleNamespace(
                task="查找并下载一篇关于人工智能的中国区论文",
                target="中国 人工智能 论文 PDF 开放获取",
                url="",
                path="",
                name="",
                intent="unknown",
                need_materialization=False,
                download=True,
                allow_network=True,
                allow_filesystem_write=False,
                max_bytes=None,
                sha256="",
                timeout=20,
                retries=1,
                target_dir="",
                auto_owner=True,
                owner_execution_mode="read_only",
                purpose="paper get payload default target",
                validation_profile="quick",
                fast=True,
                runtime="generic",
                download_backend="",
                resume_download=False,
                package_ecosystem="",
                package_action="",
                windows_package_manager="",
                package_id="",
                winget_id="",
                verify_binary="",
                install_approved=False,
                accept_winget_agreements=False,
            )
        )
        assert_ok(
            paper_get_payload["request"]["target_dir"] == str((RESOURCE_LIBRARY_ROOT / "论文").resolve()),
            "resource get paper downloads should default to desktop paper library",
        )
        with staged_http_server() as default_fast_url:
            default_fast_stdout = io.StringIO()
            with contextlib.redirect_stdout(default_fast_stdout):
                default_fast_code = resource_cli_main(
                    [
                        "materialize-url",
                        f"{default_fast_url}/photo.jpg",
                        "--task",
                        "user-facing default resource library test",
                        "--name",
                        "photo.jpg",
                        "--store-root",
                        str(root / "default-fast-store"),
                        "--receipt-log",
                        str(root / "default-fast-receipts.jsonl"),
                        "--no-resource-log",
                        "--json",
                    ]
                )
        default_fast_payload = json.loads(default_fast_stdout.getvalue())
        default_artifact = Path(default_fast_payload["receipt"]["artifact_path"])
        assert_ok(default_fast_code == 0, "default fast materialize should succeed")
        assert_ok(
            str(default_artifact).startswith(str(RESOURCE_LIBRARY_ROOT / "图片")),
            "user-facing URL materialization should default to desktop resource library picture folder",
        )
        try:
            default_artifact.unlink(missing_ok=True)
        except OSError:
            pass
        job_wait_stdout = io.StringIO()
        with contextlib.redirect_stdout(job_wait_stdout):
            job_wait_code = resource_cli_main(
                [
                    "job",
                    "wait",
                    "--request-id",
                    job_payload["request_id"],
                    "--receipt-log",
                    str(root / "job-broker-receipts.jsonl"),
                    "--timeout",
                    "1",
                    "--json",
                ]
            )
        job_wait_payload = json.loads(job_wait_stdout.getvalue())
        assert_ok(job_wait_code == 0 and job_wait_payload["resource_layer_terminal"] is True, "resource job wait failed")
        job_progress_stdout = io.StringIO()
        with contextlib.redirect_stdout(job_progress_stdout):
            job_progress_code = resource_cli_main(
                [
                    "job",
                    "progress",
                    "--request-id",
                    job_payload["request_id"],
                    "--receipt-log",
                    str(root / "job-broker-receipts.jsonl"),
                    "--json",
                ]
            )
        assert_ok(
            job_progress_code == 0 and json.loads(job_progress_stdout.getvalue())["job_schema"] == "resource_job.progress.v1",
            "resource job progress facade failed",
        )
        results["resource_strategy_review"] = "ok"
        results["resource_cli_strategy_review_filters"] = "ok"
        results["resource_cli_classify_url"] = "ok"
        results["resource_router"] = "ok"
        results["resource_cli_route"] = "ok"
        results["resource_broker"] = "ok"
        results["resource_cli_request_status"] = "ok"
        results["resource_cli_progress"] = "ok"
        results["resource_cli_job_facade"] = "ok"
        results["resource_cli_fast_materialize"] = "ok"
        results["resource_url_discovery_split_policy"] = "ok"
        results["resource_owner_tool_attach"] = "ok"
        assert_ok(
            any(root.key == "resource_request_manifests" for root in RECORD_ROOTS),
            "record-store should include resource request manifests root",
        )
        results["record_store_resource_root"] = "ok"
        results["resource_edge_cases"] = "ok"
        scenario_validation = validate_scenario_smoke()
        assert_ok(scenario_validation.get("ok"), "resource scenario smoke definitions should validate")
        source_strategy_validation = validate_source_strategy()
        assert_ok(source_strategy_validation.get("ok"), "resource source strategy should validate")
        source_executor_validation = validate_source_executor()
        assert_ok(source_executor_validation.get("ok"), "resource source executor should validate")
        results["resource_source_strategy"] = "ok"
        results["resource_source_executor"] = "ok"
        runtime_cache_validation = validate_runtime_cache()
        assert_ok(runtime_cache_validation.get("ok"), "resource runtime cache should validate")
        results["resource_request_runtime_cache"] = "ok"
        owner_disk_cache_validation = validate_owner_result_disk_cache()
        assert_ok(owner_disk_cache_validation.get("ok"), "resource owner result disk cache should validate")
        results["resource_owner_result_disk_cache"] = "ok"
        assert_ok(
            gateway_failure_reason({"ok": False, "gateway_status": "gateway_tool_call_failed", "result": {"reason": "tool_call_response_missing"}})
            == "gateway_tool_call_failed",
            "gateway failure reason should prefer gateway_status when reason is absent",
        )
        assert_ok(
            gateway_failure_is_recoverable({"ok": False, "gateway_status": "gateway_tool_call_failed", "result": {"reason": "tool_call_response_missing"}}),
            "gateway tool call failures with missing responses should allow same-boundary fallback",
        )
        assert_ok(
            gateway_failure_is_recoverable({"ok": False, "reason": "TimeoutError", "error": "timed out while waiting for owner MCP"}),
            "timeout failures should allow same-boundary fallback",
        )
        assert_ok(
            not gateway_failure_is_recoverable({"ok": False, "reason": "fallback_ack_required"}),
            "policy failures should not allow same-boundary fallback",
        )
        original_call_hub_tool = resource_owner_hub_adapter.call_hub_tool
        original_run_gateway_cli = resource_owner_hub_adapter._run_gateway_cli
        fallback_calls: list[tuple[str, str, dict[str, object]]] = []
        try:
            resource_owner_hub_adapter.call_hub_tool = lambda *_args, **_kwargs: {
                "ok": False,
                "gateway_status": "gateway_tool_call_failed",
                "result": {"reason": "tool_call_response_missing"},
            }

            def fake_gateway_cli(profile: str, tool: str, arguments: dict[str, object], *, timeout: int) -> dict[str, object]:
                fallback_calls.append((profile, tool, arguments))
                return {"ok": True, "content": [{"type": "text", "text": "fallback owner result"}]}

            resource_owner_hub_adapter._run_gateway_cli = fake_gateway_cli
            fallback_result = resource_owner_hub_adapter.call_mcp_gateway_tool(
                "context7",
                "query_docs",
                {"libraryId": "/curl/curl", "query": "retry timeout"},
                timeout=3,
            )
        finally:
            resource_owner_hub_adapter.call_hub_tool = original_call_hub_tool
            resource_owner_hub_adapter._run_gateway_cli = original_run_gateway_cli
        assert_ok(fallback_result["ok"] is True, "recoverable hub gateway failure should use local gateway fallback")
        assert_ok(fallback_result["owner_execution_route"] == "local_gateway_cli_after_hub_attempt", "fallback route should be explicit")
        assert_ok(bool(fallback_calls), "local gateway fallback should be invoked")
        assert_ok(
            fallback_result["hub_attempt"]["recoverable_by_same_boundary_fallback"] is True,
            "fallback receipt should expose recoverable classification",
        )
        results["resource_owner_gateway_fallback_classification"] = "ok"
        scenario_smoke = run_scenario_smoke(mode="quick", tmp_root=root / "scenario-smoke")
        assert_ok(scenario_smoke.get("ok"), "resource scenario smoke quick mode should pass")
        results["resource_scenario_smoke"] = "ok"

        isolation = production_store_contamination(
            DEFAULT_STORE_ROOT,
            production_store_before,
            test_root=root,
            test_request_ids=request_ids_below(root),
        )
        assert_ok(
            not isolation["contaminated_paths"],
            f"resource regression suite modified the production request store: {isolation['contaminated_paths']}",
        )
        results["production_resource_store_isolation"] = "ok"
        if isolation["changed_paths"]:
            results["concurrent_production_updates_ignored"] = str(len(isolation["changed_paths"]))

    return results


def main() -> int:
    results = run_resource_fetcher_tests()
    print(json.dumps({"ok": True, "tests": results}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
