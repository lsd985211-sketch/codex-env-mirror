#!/usr/bin/env python3
"""Regression checks for provider-scoped Codex model runtime behavior."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import codex_desktop_model_runtime as runtime
import codex_appserver_model_bridge as appserver_bridge
import codex_config_guard as config_guard
import codex_model_provider_watcher as watcher


class ModelProviderRuntimeTests(unittest.TestCase):
    def test_unreferenced_cc_switch_catalog_is_ignored_as_stale_derived_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.toml"
            catalog_path = Path(temp_dir) / config_guard.CC_SWITCH_CATALOG_NAME
            catalog_path.write_text(json.dumps({"models": [{"slug": "stale-model"}]}), encoding="utf-8")
            config = {
                "model": "active-model",
                "model_provider": "custom",
                "model_providers": {"custom": {"name": "CC Switch", "base_url": "http://127.0.0.1:15721/v1"}},
            }
            state = config_guard.cc_switch_catalog_state(config, config_path, {"ok": True, "model_count": 0})
            expected = config_guard.expected_desktop_model_ids(config, {"model_ids": []}, state)
        self.assertTrue(state["ok"])
        self.assertTrue(state["skipped"])
        self.assertFalse(state["applicable"])
        self.assertEqual(state["ignored_catalog_models"], ["stale-model"])
        self.assertEqual(expected, ["active-model"])

    def test_empty_provider_model_endpoint_is_non_authoritative_advisory(self) -> None:
        response = mock.MagicMock()
        response.status = 200
        response.read.return_value = b'{"models": []}'
        response.__enter__.return_value = response
        config = {
            "model": "provider-a",
            "model_provider": "custom",
            "model_providers": {"custom": {"name": "Static Provider", "base_url": "https://example.invalid/v1"}},
        }
        with mock.patch.object(config_guard.urllib.request, "urlopen", return_value=response):
            state = config_guard.provider_model_list_state(config)
        self.assertTrue(state["ok"])
        self.assertTrue(state["degraded"])
        self.assertFalse(state["authoritative"])
        self.assertFalse(state["usable"])
        self.assertEqual(state["reason"], "provider_models_empty_non_authoritative")

    def test_reasoning_validation_accepts_each_models_declared_effort_set(self) -> None:
        contract = config_guard.app_server_reasoning_contract(
            {
                "defaultReasoningEffort": "xhigh",
                "supportedReasoningEfforts": [
                    {"reasoningEffort": "low"},
                    {"reasoningEffort": "high"},
                    {"reasoningEffort": "xhigh"},
                ],
            }
        )
        self.assertTrue(contract["ok"], contract)
        self.assertNotIn("none", contract["available_efforts"])
        self.assertEqual(contract["default_effort"], "xhigh")

    def test_config_guard_startup_validation_does_not_block_on_model_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.toml"
            config_path.write_text('model = "provider-a"\n', encoding="utf-8")
            snap = {
                "global_config": {"path": str(config_path)},
                "audit": {"critical_ok": True, "baseline_convergence_ok": True},
                "provider_model_list": {"ok": False, "skipped": False, "authoritative": False, "reason": "provider_models_unreachable"},
                "cc_switch_catalog": {"ok": True, "skipped": True},
                "desktop_app_model_list": {"ok": False, "skipped": False, "missing_expected_models": ["provider-a"]},
                "desktop_runtime_model_state": {"ok": False, "statsig_missing_expected_models": ["provider-a"]},
                "desktop_model_refresh": {"restart_required_for_desktop_model_refresh": True},
            }
            with mock.patch.object(config_guard, "safe_session_store_validate", return_value={"ok": True}):
                result = config_guard.validate_snapshot(snap)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["startup_integrity_ok"])
        self.assertFalse(result["model_runtime_ok"])
        self.assertFalse(result["provider_discovery_ok"])
        self.assertFalse(result["blockers"])
        self.assertTrue(any(item["scope"] == "model_runtime" for item in result["issues"]))

    def test_model_list_shim_uses_mutable_global_models(self) -> None:
        source = runtime._model_list_bridge_shim_source([{"slug": "provider-a"}])
        self.assertIn("codex-model-list-bridge-shim/v6", source)
        self.assertIn("window.__codexModelListShimModels = catalogModels", source)
        self.assertIn("window.__codexModelListShimModels", source)
        self.assertIn("url === 'vscode://codex/list-models-for-host'", source)
        self.assertIn("const data = activeModels.slice", source)
        self.assertIn("return originalSend(message)", source)
        self.assertIn("previousSignature !== signature", source)
        self.assertIn("query-cache-invalidate", source)
        self.assertIn("__codexModelListShimConsumedSignature", source)
        self.assertIn("consumer_unconfirmed", source)

    def test_statsig_protection_uses_mutable_provider_state(self) -> None:
        source = runtime._statsig_allowlist_protect_expression(["provider-a"], reload_if_changed=False)
        self.assertIn("codex-statsig-allowlist-protection/v4", source)
        self.assertIn("window.__codexStatsigAllowlistProtectionRequiredModels = requiredModels", source)
        self.assertIn("const merged = Array.from(new Set([...before, ...activeRequiredModels()]))", source)
        self.assertIn("window.__codexStatsigNativeSetItem = nativeSetItem", source)
        self.assertIn("Reflect.apply(nativeSetItem, localStorage", source)
        self.assertIn("__codexAllowlistGetDynamicConfigWrapperVersion", source)
        self.assertIn("window.__codexReasoningMergeStatsigOuter", source)

    def test_statsig_protection_replaces_stale_client_wrappers(self) -> None:
        source = runtime._statsig_allowlist_protect_expression(["provider-a"], reload_if_changed=False)
        self.assertIn("activeWrapperVersion === marker", source)
        self.assertIn("client.__codexAllowlistOriginalGetDynamicConfig", source)
        self.assertIn("wrapper.__codexAllowlistWrapperVersion = marker", source)
        self.assertIn("client.__codexAllowlistGetDynamicConfigWrapperVersion = marker", source)

    def test_statsig_live_probe_reports_exact_wrapper_coverage(self) -> None:
        source = runtime._statsig_allowlist_live_probe_expression()
        self.assertIn("clientsProtected", source)
        self.assertIn("client.__codexAllowlistGetDynamicConfigWrapper === client.getDynamicConfig", source)
        self.assertIn("__codexAllowlistGetDynamicConfigWrapperVersion", source)

    def test_appserver_shim_owns_current_model_rpc_and_local_cache_invalidation(self) -> None:
        source = appserver_bridge.build_shim_source(
            [{"slug": "provider-a", "model": "provider-a"}],
            "./assets/use-host-config-test.js",
        )
        self.assertNotIn("hostModule.Nn", source)
        self.assertIn("Object.entries(hostModule).find", source)
        self.assertIn("__codexAppServerModelShimConsumedModels", source)
        self.assertIn("method === 'list-models-for-host'", source)
        self.assertIn("type: 'ipc-broadcast'", source)
        self.assertIn("queryKey: ['models', 'list']", source)
        self.assertIn(appserver_bridge.SHIM_VERSION, source)

    def test_appserver_live_probe_is_paginated_and_does_not_count_as_ui_consumption(self) -> None:
        source = appserver_bridge.build_probe_source("./assets/use-host-config-test.js")
        self.assertIn('"maxPages":32', source)
        self.assertIn("__codexRuntimeProbe: true", source)
        self.assertIn("const seen = new Set()", source)
        self.assertIn("result.cursorLoop = true", source)
        self.assertIn("result.consumedGeneration === result.generation", source)

    def test_cdp_page_selection_prefers_the_main_codex_renderer(self) -> None:
        pages = [
            {"type": "page", "title": "Aux", "url": "app://-/aux.html", "webSocketDebuggerUrl": "ws://aux"},
            {"type": "worker", "title": "", "url": "", "webSocketDebuggerUrl": "ws://worker"},
            {"type": "page", "title": "Codex", "url": "app://-/index.html", "webSocketDebuggerUrl": "ws://main"},
        ]
        selected = runtime._select_codex_page(pages)
        self.assertIsNotNone(selected)
        self.assertEqual(selected["webSocketDebuggerUrl"], "ws://main")

    def test_current_desktop_appserver_module_is_discoverable(self) -> None:
        discovery = appserver_bridge.discover_host_module()
        self.assertTrue(discovery["ok"], discovery)
        self.assertRegex(discovery["module_specifier"], r"^\./assets/use-host-config-.+\.js$")

    def test_current_desktop_persisted_state_module_is_discoverable(self) -> None:
        discovery = runtime.discover_persisted_state_host_module()
        self.assertTrue(discovery["ok"], discovery)
        self.assertRegex(discovery["module_specifier"], r"^\./assets/vscode-api-.+\.js$")

    def test_statsig_sync_preserves_native_models_and_adds_provider_models(self) -> None:
        source = runtime._statsig_allowlist_sync_expression(["provider-b"], apply=True, reload_if_changed=True)
        self.assertIn("const synced = Array.from(new Set([...before, ...requiredModels]))", source)
        self.assertIn("const removed = []", source)
        self.assertNotIn("before.filter((item) => !synced.includes(item))", source)

    def test_model_picker_view_sync_tracks_signature_and_actual_view(self) -> None:
        source = runtime._model_picker_view_sync_expression("signature-a", apply=True)
        self.assertIn(runtime.MODEL_PICKER_VIEW_KEY, source)
        self.assertIn(runtime.MODEL_PICKER_HOST_KEY, source)
        self.assertIn(runtime.MODEL_PICKER_SYNC_KEY, source)
        self.assertIn(runtime.MODEL_PICKER_SYNC_ATTEMPT_KEY, source)
        self.assertIn("!result.signatureCurrent || !result.viewCurrent", source)
        self.assertIn("persisted-atom-sync-request", source)
        self.assertIn("persisted-atom-update", source)
        self.assertIn("persisted-atom-updated", source)
        self.assertIn("key: payload.hostKey", source)
        self.assertIn("value: 'advanced'", source)
        self.assertIn("deleted: false", source)
        self.assertIn("result.hostPersistenceConfirmed", source)
        self.assertIn("result.reloadSafe = result.hostPersistenceConfirmed", source)
        self.assertNotIn("new StorageEvent('storage'", source)
        self.assertNotIn("location.reload", source)

    def test_model_picker_sync_reapplies_full_view_for_current_catalog_signature(self) -> None:
        source = runtime._model_picker_view_sync_expression("signature-a", apply=True)
        self.assertIn("if (payload.apply && result.syncRequired)", source)
        self.assertIn("result.viewBefore !== 'advanced'", source)
        self.assertIn("model_picker_sync_retry_cooldown", source)

    def test_catalog_bridge_adds_desktop_reasoning_fields_without_mutating_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "catalog.json"
            path.write_text('{"models":[{"slug":"provider-a","supported_reasoning_levels":[{"effort":"high"}]}]}', encoding="utf-8")
            before = path.read_text(encoding="utf-8")
            models = runtime._catalog_bridge_models(path)
            self.assertEqual(models[0]["model"], "provider-a")
            self.assertFalse(models[0]["hidden"])
            self.assertEqual(
                [item["reasoningEffort"] for item in models[0]["supportedReasoningEfforts"]],
                list(runtime.SAFE_CATALOG_REASONING_LEVELS),
            )
            self.assertIn("ultra", [item["reasoningEffort"] for item in models[0]["supportedReasoningEfforts"]])
            self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_catalog_reasoning_state_reports_runtime_catalog_and_selectable_efforts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "catalog.json"
            entries = runtime.catalog_reasoning_entries()
            desktop_entries = runtime.desktop_reasoning_entries()
            path.write_text(
                json.dumps(
                    {
                        "models": [
                            {
                                "slug": "provider-a",
                                "model": "provider-a",
                                "displayName": "Provider A",
                                "hidden": False,
                                "isDefault": False,
                                "defaultReasoningEffort": "high",
                                "supported_reasoning_levels": entries,
                                "supportedReasoningEfforts": desktop_entries,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            state = runtime.catalog_reasoning_state(path, {"low", "medium", "high", "xhigh", "ultra"})
        self.assertTrue(state["ok"], state)
        self.assertIn("none", state["catalog_supported_reasoning_efforts"])
        self.assertNotIn("none", state["selectable_reasoning_efforts"])
        self.assertIn("ultra", state["selectable_reasoning_efforts"])
        self.assertEqual(state["runtime_enabled_reasoning_efforts"], ["high", "low", "medium", "ultra", "xhigh"])

    def test_catalog_reasoning_repair_detects_and_restores_missing_ultra(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "catalog.json"
            levels = tuple(level for level in runtime.SAFE_CATALOG_REASONING_LEVELS if level != "ultra")
            path.write_text(
                json.dumps(
                    {
                        "models": [
                            {
                                "slug": "provider-a",
                                "model": "provider-a",
                                "displayName": "Provider A",
                                "hidden": False,
                                "isDefault": False,
                                "defaultReasoningEffort": "high",
                                "supported_reasoning_levels": runtime.catalog_reasoning_entries(levels),
                                "supportedReasoningEfforts": runtime.desktop_reasoning_entries(levels),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            before = runtime.catalog_reasoning_state(path)
            applied = runtime.apply_catalog_reasoning_repair(path)
            after = runtime.catalog_reasoning_state(path)
        self.assertFalse(before["ok"])
        self.assertIn("desktop_reasoning_efforts_missing_runtime_efforts", before["models"][0]["desktop_compat_issues"])
        self.assertTrue(applied["changed"])
        self.assertTrue(after["ok"], after)
        self.assertIn("ultra", after["models"][0]["selectable_reasoning_efforts"])

    def test_catalog_requested_reasoning_efforts_supports_extended_provider_levels(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "catalog.json"
            path.write_text(
                json.dumps(
                    {
                        "models": [
                            {
                                "slug": "provider-a",
                                "supported_reasoning_levels": [
                                    {"effort": "none"},
                                    {"effort": "minimal"},
                                    {"effort": "max"},
                                    {"effort": "ultra"},
                                    {"effort": "provider-private"},
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            efforts = runtime.catalog_requested_reasoning_efforts(path)
            declared = runtime.catalog_declared_reasoning_efforts(path)
        self.assertEqual(efforts, ["minimal", "max", "ultra"])
        self.assertIn("provider-private", declared)

    def test_reasoning_hot_refresh_is_catalog_driven_and_gate_scoped(self) -> None:
        source = runtime._reasoning_hot_refresh_expression(
            ["low", "max", "ultra"],
            apply=True,
            reload_if_changed=True,
        )
        self.assertIn("codex-reasoning-capability-bridge/v1", source)
        self.assertIn('"1186680773":true', source)
        self.assertIn("appPost('set-setting', {key: settingKey, value: desired})", source)
        self.assertIn("requestedEfforts.forEach", source)
        self.assertIn("client.__codexReasoningOriginalCheckGate", source)
        self.assertIn("client.$emt({name: 'values_updated'", source)
        self.assertIn("Object.keys(overrides)", source)
        self.assertNotIn("data.feature_gates = gateOverrides", source)

    def test_reasoning_hot_refresh_does_not_force_ultra_without_catalog_support(self) -> None:
        source = runtime._reasoning_hot_refresh_expression(
            ["low", "medium", "max"],
            apply=True,
            reload_if_changed=False,
        )
        self.assertIn('"gateOverrides":{}', source)
        self.assertNotIn('"1186680773":true', source)

    def test_provider_watcher_has_bounded_runtime_binding_reconciliation(self) -> None:
        source = Path(watcher.__file__).read_text(encoding="utf-8")
        self.assertIn("drift_check_seconds", source)
        self.assertIn("def runtime_binding_state", source)
        self.assertIn("desktop_appserver_module", source)
        self.assertIn("apply_catalog_reasoning_repair", source)
        self.assertIn("apply_appserver_model_shim", source)
        self.assertIn("reasoning_hot_refresh_state", source)
        self.assertIn("model_picker_view_sync_state", source)
        self.assertIn("request_desktop_page_reload", source)
        self.assertIn("catalog_reasoning_efforts", source)
        self.assertIn("reasoning_bound", source)
        self.assertIn("modelListConsumedSignature", source)
        self.assertIn("appServerConsumedModels", source)
        self.assertIn('default=3.0', source)
        self.assertIn('max(1.0, drift_check_seconds)', source)
        self.assertIn('"legacy_model_list"', source)
        self.assertIn("if not appserver_supported and legacy_bound", source)
        self.assertIn('"reason": "appserver_primary_active"', source)
        self.assertIn('statsig_probe.get("clientsProtected")', source)
        self.assertIn('event": "runtime_binding_reconcile', source)
        self.assertIn("reconcile(reload_if_changed=True)", source)
        self.assertIn("RESTART_FOR_IMPLEMENTATION_CHANGE", source)
        self.assertIn('"event": "implementation_changed"', source)
        self.assertIn('"watcher_implementation_current"', source)

    def test_provider_watcher_fingerprints_all_loaded_runtime_modules(self) -> None:
        paths = watcher._implementation_paths()
        self.assertIn(Path(watcher.__file__).resolve(), paths)
        self.assertIn(Path(runtime.__file__).resolve(), paths)
        self.assertIn(Path(appserver_bridge.__file__).resolve(), paths)
        self.assertIn(Path(config_guard.__file__).resolve(), paths)
        fingerprint = watcher._implementation_fingerprint()
        self.assertEqual(len(fingerprint), 64)
        self.assertEqual(fingerprint, watcher._implementation_fingerprint())

    def test_provider_watcher_metadata_probe_avoids_rehash_without_changes(self) -> None:
        source = {
            "ok": True,
            "signature_hash": "signature-a",
            "config_projection_signature": "projection-a",
            "catalog_models": [],
            "catalog_reasoning_efforts": [],
        }

        class Lock:
            def close(self) -> None:
                return None

        with (
            mock.patch.object(watcher, "_acquire_lock", return_value=Lock()),
            mock.patch.object(
                watcher,
                "_read_state",
                return_value={
                    "last_seen_signature_hash": "signature-a",
                    "last_successful_signature_hash": "signature-a",
                    "last_config_projection_signature": "projection-a",
                },
            ),
            mock.patch.object(watcher, "_write_json"),
            mock.patch.object(watcher, "_append_event"),
            mock.patch.object(watcher, "_implementation_fingerprint", return_value="implementation-a") as full_hash,
            mock.patch.object(watcher, "_implementation_metadata_fingerprint", return_value="metadata-a"),
            mock.patch.object(watcher, "_source_probe_fingerprint", return_value="source-metadata-a"),
            mock.patch.object(watcher, "source_state", return_value=source) as source_read,
            mock.patch.object(watcher, "runtime_binding_state", return_value={"bound": True}),
            mock.patch.object(watcher.time, "sleep"),
        ):
            result = watcher.watch(
                poll_seconds=2.0,
                debounce_seconds=1.5,
                drift_check_seconds=999.0,
                max_iterations=4,
            )

        self.assertEqual(result, 0)
        self.assertEqual(full_hash.call_count, 1)
        self.assertEqual(source_read.call_count, 1)

    def test_provider_watcher_unreadable_source_is_debounced_without_reconcile(self) -> None:
        source = {
            "ok": False,
            "signature_hash": "empty-signature",
            "config_projection_signature": "unavailable:TOMLDecodeError",
            "catalog_models": [],
            "catalog_reasoning_efforts": [],
        }

        class Lock:
            def close(self) -> None:
                return None

        with (
            mock.patch.object(watcher, "_acquire_lock", return_value=Lock()),
            mock.patch.object(watcher, "_read_state", return_value={}),
            mock.patch.object(watcher, "_write_json"),
            mock.patch.object(watcher, "_append_event") as append_event,
            mock.patch.object(watcher, "_implementation_fingerprint", return_value="implementation-a"),
            mock.patch.object(watcher, "_implementation_metadata_fingerprint", return_value="metadata-a"),
            mock.patch.object(watcher, "_source_probe_fingerprint", return_value="source-metadata-a"),
            mock.patch.object(watcher, "source_state", return_value=source) as source_read,
            mock.patch.object(watcher, "reconcile") as reconcile,
            mock.patch.object(watcher.time, "sleep"),
        ):
            result = watcher.watch(
                poll_seconds=2.0,
                debounce_seconds=1.5,
                drift_check_seconds=3.0,
                max_iterations=3,
            )

        self.assertEqual(result, 0)
        self.assertEqual(source_read.call_count, 1)
        reconcile.assert_not_called()
        source_events = [
            call.args[0]
            for call in append_event.call_args_list
            if call.args[0].get("event") == "source_unavailable"
        ]
        self.assertEqual(len(source_events), 1)
        self.assertNotIn("desktop_host_module", source_events[0]["source"])

    def test_provider_watcher_supervisor_immediately_restarts_code_reload(self) -> None:
        completions = [
            watcher.subprocess.CompletedProcess([], watcher.RESTART_FOR_IMPLEMENTATION_CHANGE),
            watcher.subprocess.CompletedProcess([], 0),
        ]
        with (
            mock.patch.object(watcher.subprocess, "run", side_effect=completions) as run,
            mock.patch.object(watcher.time, "sleep") as sleep,
            mock.patch.object(watcher, "_append_event"),
        ):
            result = watcher.supervise(
                poll_seconds=2.0,
                debounce_seconds=1.5,
                drift_check_seconds=3.0,
                restart_delay_seconds=0.1,
            )
        self.assertEqual(result, 0)
        self.assertEqual(run.call_count, 2)
        self.assertIn("watch", run.call_args_list[0].args[0])
        sleep.assert_called_once_with(0.1)

    def test_provider_watcher_supervisor_bounds_reload_spin(self) -> None:
        completion = watcher.subprocess.CompletedProcess([], watcher.RESTART_FOR_IMPLEMENTATION_CHANGE)
        with (
            mock.patch.object(watcher.subprocess, "run", return_value=completion) as run,
            mock.patch.object(watcher.time, "sleep"),
            mock.patch.object(watcher, "_append_event") as append_event,
        ):
            result = watcher.supervise(
                poll_seconds=2.0,
                debounce_seconds=1.5,
                drift_check_seconds=3.0,
                restart_delay_seconds=0.1,
                max_restarts=2,
                restart_window_seconds=30.0,
            )
        self.assertEqual(result, watcher.RESTART_FOR_IMPLEMENTATION_CHANGE)
        self.assertEqual(run.call_count, 3)
        self.assertEqual(append_event.call_args.args[0]["event"], "supervisor_restart_exhausted")

    def test_provider_without_catalog_preserves_native_desktop_behavior(self) -> None:
        source = {
            "ok": True,
            "catalog_path": "",
            "catalog_models": [],
            "require_advanced_model_picker": False,
        }
        with (
            mock.patch.object(watcher, "source_state", return_value=source),
            mock.patch.object(runtime, "apply_catalog_reasoning_repair") as reasoning_repair,
            mock.patch.object(runtime, "model_picker_view_sync_state") as picker_sync,
            mock.patch.object(runtime, "request_desktop_page_reload") as reload_page,
        ):
            state = watcher.reconcile(reload_if_changed=True)
        self.assertTrue(state["ok"], state)
        self.assertEqual(state["reason"], "native_provider_without_catalog_no_runtime_override_required")
        reasoning_repair.assert_not_called()
        picker_sync.assert_not_called()
        reload_page.assert_not_called()

    def test_host_persistence_failure_does_not_authorize_reload(self) -> None:
        source = {
            "ok": True,
            "catalog_path": "catalog.json",
            "catalog_models": ["provider-a"],
            "signature_hash": "signature-a",
            "require_advanced_model_picker": True,
        }
        ready = {"ok": True, "skipped": False}
        picker_failure = {
            "ok": False,
            "skipped": False,
            "result": {
                "changed": False,
                "reloadSafe": False,
                "persistenceRoute": "desktop_host_unavailable",
                "persistenceConfirmed": False,
            },
        }
        with (
            mock.patch.object(watcher, "source_state", return_value=source),
            mock.patch.object(runtime, "catalog_reasoning_repair_plan", return_value=ready),
            mock.patch.object(runtime, "apply_catalog_reasoning_repair", return_value=ready),
            mock.patch.object(runtime, "apply_appserver_model_shim", return_value=ready),
            mock.patch.object(runtime, "apply_model_list_bridge_shim", return_value=ready) as legacy_bridge,
            mock.patch.object(runtime, "statsig_allowlist_protection_state", return_value=ready),
            mock.patch.object(runtime, "model_picker_view_sync_state", return_value=picker_failure),
            mock.patch.object(runtime, "reasoning_hot_refresh_state", return_value=ready),
            mock.patch.object(runtime, "request_desktop_page_reload") as reload_page,
        ):
            state = watcher.reconcile(reload_if_changed=True)
        self.assertFalse(state["ok"])
        self.assertEqual(state["page_reload"]["reason"], "reload_not_required")
        legacy_bridge.assert_not_called()
        reload_page.assert_not_called()

    def test_reconcile_uses_legacy_bridge_only_when_appserver_is_unavailable(self) -> None:
        source = {
            "ok": True,
            "catalog_path": "catalog.json",
            "catalog_models": ["provider-a"],
            "signature_hash": "signature-a",
            "require_advanced_model_picker": True,
        }
        ready = {"ok": True, "skipped": False, "result": {}}
        appserver_failed = {"ok": False, "skipped": False, "reason": "appserver_unavailable"}
        with (
            mock.patch.object(watcher, "source_state", return_value=source),
            mock.patch.object(runtime, "catalog_reasoning_repair_plan", return_value=ready),
            mock.patch.object(runtime, "apply_catalog_reasoning_repair", return_value=ready),
            mock.patch.object(runtime, "apply_appserver_model_shim", return_value=appserver_failed),
            mock.patch.object(runtime, "apply_model_list_bridge_shim", return_value=ready) as legacy_bridge,
            mock.patch.object(runtime, "statsig_allowlist_protection_state", return_value=ready),
            mock.patch.object(runtime, "model_picker_view_sync_state", return_value=ready),
            mock.patch.object(runtime, "reasoning_hot_refresh_state", return_value=ready),
        ):
            state = watcher.reconcile(reload_if_changed=False)
        self.assertTrue(state["ok"], state)
        legacy_bridge.assert_called_once()

    def test_runtime_binding_requires_the_full_consumed_response_without_a_next_page(self) -> None:
        expected_models = [f"provider-{index}" for index in range(8)]
        expected_efforts = ["low", "medium", "high", "xhigh"]

        class FakeCdpClient:
            def __init__(self, _ws_url: str, state: dict[str, object]) -> None:
                self.state = state

            def evaluate(self, expression: str) -> dict[str, object]:
                if "codex-appserver-model-shim/live-probe/v1" in expression:
                    consumed_models = list(self.state.get("appServerConsumedModels", []))
                    next_cursor = self.state.get("appServerConsumedNextCursor")
                    complete = consumed_models == expected_models and next_cursor is None
                    return {
                        "version": appserver_bridge.SHIM_VERSION,
                        "wrapperActive": True,
                        "models": expected_models,
                        "consumedModels": consumed_models,
                        "generation": "generation-a",
                        "consumedGeneration": "generation-a" if complete else "generation-old",
                        "consumedNextCursor": next_cursor,
                        "queryRefetchConfirmed": complete,
                    }
                if "statsig-live-probe/v1" in expression:
                    return {
                        "ok": True,
                        "version": "codex-statsig-allowlist-protection/v4",
                        "availableModels": expected_models,
                        "clientsFound": 1,
                        "clientsProtected": 1,
                        "nativeSetItemRecovered": True,
                        "storageWrapperActive": True,
                    }
                if "model-picker-view-sync-result/v2" in expression:
                    return {
                        "signatureAfter": "signature-a",
                        "viewAfter": "advanced",
                        "persistenceRoute": "desktop_host",
                        "persistenceConfirmed": True,
                    }
                return self.state

            def close(self) -> None:
                return None

        def binding_state(consumed_models: list[str], next_cursor: str | None) -> dict[str, object]:
            renderer_state: dict[str, object] = {
                "appServerVersion": appserver_bridge.SHIM_VERSION,
                "appServerModels": expected_models,
                "appServerConsumedModels": consumed_models,
                "appServerGeneration": "generation-a",
                "appServerConsumedGeneration": "generation-a" if next_cursor is None else "generation-old",
                "appServerConsumedNextCursor": next_cursor,
                "appServerConsumedAt": 1,
                "modelListVersion": "",
                "modelListModels": [],
                "modelListSignature": "",
                "modelListConsumedSignature": "",
                "modelListConsumedModels": [],
                "modelListConsumedNextCursor": None,
                "modelListConsumedAt": 0,
                "statsigVersion": "codex-statsig-allowlist-protection/v4",
                "statsigRequiredModels": expected_models,
                "reasoningVersion": "codex-reasoning-capability-bridge/v1",
                "reasoningEfforts": expected_efforts,
                "reasoningGates": {},
                "modelPickerView": "advanced",
                "modelPickerViewSyncSignature": "signature-a",
            }
            with (
                mock.patch.object(runtime, "_find_codex_page", return_value=(9222, "ws://main", [{}], "")),
                mock.patch.object(runtime, "_CdpClient", side_effect=lambda ws_url: FakeCdpClient(ws_url, renderer_state)),
                mock.patch.object(appserver_bridge, "discover_host_module", return_value={"ok": True}),
            ):
                return watcher.runtime_binding_state(expected_models, expected_efforts, "signature-a")

        partial = binding_state(expected_models[:6], "6")
        self.assertFalse(partial["bound"])
        self.assertFalse(partial["appserver_bound"])
        self.assertEqual(partial["appserver_consumed_models"], expected_models[:6])
        self.assertEqual(partial["appserver_consumed_next_cursor"], "6")

        complete = binding_state(expected_models, None)
        self.assertTrue(complete["bound"])
        self.assertTrue(complete["appserver_bound"])
        self.assertEqual(complete["appserver_consumed_models"], expected_models)
        self.assertIsNone(complete["appserver_consumed_next_cursor"])

    def test_runtime_binding_requires_current_picker_signature_and_full_view(self) -> None:
        expected_models = ["provider-a"]
        expected_efforts = ["high"]
        renderer_state = {
            "appServerVersion": appserver_bridge.SHIM_VERSION,
            "appServerModels": expected_models,
            "appServerConsumedModels": expected_models,
            "appServerGeneration": "generation-a",
            "appServerConsumedGeneration": "generation-a",
            "appServerConsumedNextCursor": None,
            "modelListVersion": "",
            "modelListModels": [],
            "modelListSignature": "",
            "modelListConsumedSignature": "",
            "modelListConsumedModels": [],
            "modelListConsumedNextCursor": None,
            "statsigVersion": "codex-statsig-allowlist-protection/v4",
            "statsigRequiredModels": expected_models,
            "reasoningVersion": "codex-reasoning-capability-bridge/v1",
            "reasoningEfforts": expected_efforts,
            "reasoningGates": {},
            "modelPickerView": "simple",
            "modelPickerViewSyncSignature": "new-signature",
        }

        class FakeCdpClient:
            def __init__(self, _ws_url: str) -> None:
                pass

            def evaluate(self, expression: str) -> dict[str, object]:
                if "codex-appserver-model-shim/live-probe/v1" in expression:
                    return {
                        "version": appserver_bridge.SHIM_VERSION,
                        "wrapperActive": True,
                        "models": expected_models,
                        "consumedModels": expected_models,
                        "generation": "generation-a",
                        "consumedGeneration": "generation-a",
                        "consumedNextCursor": None,
                        "queryRefetchConfirmed": True,
                    }
                if "statsig-live-probe/v1" in expression:
                    return {
                        "ok": True,
                        "version": "codex-statsig-allowlist-protection/v4",
                        "availableModels": expected_models,
                        "clientsFound": 1,
                        "clientsProtected": 1,
                        "nativeSetItemRecovered": True,
                        "storageWrapperActive": True,
                    }
                if "model-picker-view-sync-result/v2" in expression:
                    return {
                        "signatureAfter": "new-signature",
                        "viewAfter": "simple",
                        "persistenceRoute": "desktop_host",
                        "persistenceConfirmed": True,
                    }
                return renderer_state

            def close(self) -> None:
                return None

        with (
            mock.patch.object(runtime, "_find_codex_page", return_value=(9222, "ws://main", [{}], "")),
            mock.patch.object(runtime, "_CdpClient", FakeCdpClient),
            mock.patch.object(appserver_bridge, "discover_host_module", return_value={"ok": True}),
        ):
            state = watcher.runtime_binding_state(expected_models, expected_efforts, "new-signature")
        self.assertFalse(state["bound"])
        self.assertTrue(state["model_picker_view_sync_required"])
        self.assertTrue(state["model_picker_signature_bound"])
        self.assertFalse(state["model_picker_view_bound"])

    def test_source_catalog_schema_is_advisory_when_runtime_adapter_is_healthy(self) -> None:
        with (
            mock.patch.object(runtime, "desktop_runtime_state", return_value={"ok": True, "enabled_reasoning_efforts": ["high"]}),
            mock.patch.object(runtime, "catalog_reasoning_state", return_value={"ok": False, "skipped": False}),
        ):
            state = runtime.combined_state(["provider-a"], None)
        self.assertTrue(state["ok"])
        self.assertTrue(state["catalog_reasoning_advisory"])
        self.assertFalse(state["runtime_unhealthy"])

    def test_elevation_required_makes_independent_app_server_probe_non_blocking(self) -> None:
        elevation_error = OSError(22, "requested operation requires elevation", None, 740)
        with (
            mock.patch.object(config_guard, "expected_desktop_model_ids", return_value=["provider-a"]),
            mock.patch.object(config_guard, "desktop_app_server_executable", return_value=Path("codex.exe")),
            mock.patch.object(config_guard.subprocess, "Popen", side_effect=elevation_error),
        ):
            state = config_guard.desktop_app_model_list_state({}, Path("config.toml"))
        self.assertFalse(state["ok"])
        self.assertTrue(state["skipped"])
        self.assertTrue(state["route_unavailable"])
        self.assertEqual(state["winerror"], 740)
        self.assertEqual(state["reason"], "desktop_app_model_list_probe_route_unavailable")

    def test_other_app_server_probe_launch_errors_remain_failures(self) -> None:
        with (
            mock.patch.object(config_guard, "expected_desktop_model_ids", return_value=["provider-a"]),
            mock.patch.object(config_guard, "desktop_app_server_executable", return_value=Path("codex.exe")),
            mock.patch.object(config_guard.subprocess, "Popen", side_effect=OSError(5, "access denied")),
        ):
            state = config_guard.desktop_app_model_list_state({}, Path("config.toml"))
        self.assertFalse(state["ok"])
        self.assertFalse(state["skipped"])
        self.assertNotIn("route_unavailable", state)
        self.assertEqual(state["reason"], "desktop_app_model_list_probe_failed")


if __name__ == "__main__":
    unittest.main()
