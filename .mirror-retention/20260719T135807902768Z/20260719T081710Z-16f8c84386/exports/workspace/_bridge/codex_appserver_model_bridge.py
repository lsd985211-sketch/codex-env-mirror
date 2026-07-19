#!/usr/bin/env python3
"""Build the current Codex Desktop AppServer model-list compatibility shim.

Ownership: discover the active Desktop webview AppServer module and generate a
provider-catalog-backed ``list-models-for-host`` adapter.
Non-goals: choose providers, edit config/catalog files, restart Codex, patch
``app.asar``, or own CDP transport and watcher lifecycle.
State behavior: read-only Desktop asset discovery; generated JavaScript changes
only the active renderer and invalidates its model query cache.
Caller context: ``codex_desktop_model_runtime`` and provider watcher tests.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
from pathlib import Path
from typing import Any

try:
    from shared.codex_desktop_package import running_desktop_executable_paths
except ModuleNotFoundError:  # Package-style imports from the workspace root.
    from _bridge.shared.codex_desktop_package import running_desktop_executable_paths


APP_ASAR_NAME = "app.asar"
HOST_MODULE_PATTERN = re.compile(r"^webview/assets/use-host-config-[^/]+\.js$")
SHIM_VERSION = "codex-appserver-model-shim/v3"


def _running_codex_executables() -> list[Path]:
    return running_desktop_executable_paths()


def candidate_app_asars() -> list[Path]:
    candidates: list[Path] = []
    for executable in _running_codex_executables():
        path = executable.parent / "resources" / APP_ASAR_NAME
        if path.is_file() and path not in candidates:
            candidates.append(path)
    windows_apps = Path(r"C:\Program Files\WindowsApps")
    if windows_apps.is_dir():
        fallback = sorted(
            windows_apps.glob(r"OpenAI.Codex_*\app\resources\app.asar"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        candidates.extend(path for path in fallback if path not in candidates)
    return candidates


def _asar_files(path: Path) -> dict[str, dict[str, Any]]:
    with path.open("rb") as handle:
        header = handle.read(16)
        if len(header) != 16:
            raise ValueError("asar_header_short")
        _, _, _, json_size = struct.unpack("<4I", header)
        root = json.loads(handle.read(json_size).decode("utf-8"))
    result: dict[str, dict[str, Any]] = {}

    def walk(node: dict[str, Any], prefix: str = "") -> None:
        for name, metadata in node.get("files", {}).items():
            relative = f"{prefix}/{name}" if prefix else name
            if isinstance(metadata, dict) and "files" in metadata:
                walk(metadata, relative)
            elif isinstance(metadata, dict):
                result[relative] = metadata

    walk(root)
    return result


def discover_host_module() -> dict[str, Any]:
    errors: list[str] = []
    for asar_path in candidate_app_asars():
        try:
            files = _asar_files(asar_path)
            relative = next((name for name in files if HOST_MODULE_PATTERN.match(name)), "")
            if relative:
                return {
                    "ok": True,
                    "asar_path": str(asar_path),
                    "asset_path": relative,
                    "module_specifier": f"./assets/{Path(relative).name}",
                    "reason": "",
                }
            errors.append(f"{asar_path}:host_module_missing")
        except Exception as exc:
            errors.append(f"{asar_path}:{type(exc).__name__}")
    return {
        "ok": False,
        "asar_path": "",
        "asset_path": "",
        "module_specifier": "",
        "reason": "host_module_discovery_failed",
        "errors": errors,
    }


def build_shim_source(models: list[dict[str, Any]], module_specifier: str) -> str:
    catalog_signature = hashlib.sha256(
        json.dumps(models, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    payload = json.dumps(
        {
            "models": models,
            "moduleSpecifier": module_specifier,
            "version": SHIM_VERSION,
            "catalogSignature": catalog_signature,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"""
(() => {{
  const payload = {payload};
  const models = Array.isArray(payload.models) ? payload.models : [];
  window.__codexAppServerModelShimModels = models;
  window.__codexAppServerModelShimCatalogSignature = String(payload.catalogSignature || '');
  function invalidateModelQuery(reason) {{
    window.__codexAppServerModelShimInvalidatedGeneration = String(window.__codexAppServerModelShimGeneration || '');
    window.__codexAppServerModelShimInvalidatedAt = Date.now();
    window.__codexAppServerModelShimInvalidationReason = reason;
    window.dispatchEvent(new MessageEvent('message', {{ data: {{
      type: 'ipc-broadcast', method: 'query-cache-invalidate',
      params: {{ queryKey: ['models', 'list'] }}, sourceClientId: 'codex-appserver-model-shim',
    }} }}));
  }}
  async function install() {{
    try {{
      const hostModule = await import(payload.moduleSpecifier);
      const bridgeEntry = Object.entries(hostModule).find(([, value]) =>
        value && typeof value === 'object' && typeof value.sendRequest === 'function'
      );
      const bridge = bridgeEntry && bridgeEntry[1];
      if (!bridge) {{
        const exports = Object.entries(hostModule).map(([name, value]) => ({{
          name,
          type: typeof value,
          hasSendRequest: Boolean(value && typeof value.sendRequest === 'function'),
        }}));
        window.__codexAppServerModelShimExportDiagnostics = exports;
        throw new Error('appserver_bridge_unavailable');
      }}
      const previousVersion = String(window.__codexAppServerModelShimVersion || '');
      const previousSignature = String(window.__codexAppServerModelShimInstalledCatalogSignature || '');
      const previousGeneration = String(window.__codexAppServerModelShimGeneration || '');
      const installedWrapper = window.__codexAppServerModelShimWrapperSendRequest;
      const wrapperActive = bridge === window.__codexAppServerModelShimBridge
        && typeof installedWrapper === 'function'
        && bridge.sendRequest === installedWrapper;
      const catalogChanged = previousSignature !== String(payload.catalogSignature || '');
      const generation = wrapperActive && !catalogChanged
        ? String(window.__codexAppServerModelShimGeneration || '')
        : crypto.randomUUID();
      window.__codexAppServerModelShimGeneration = generation;
      window.__codexAppServerModelShimInstalledCatalogSignature = String(payload.catalogSignature || '');
      if (!wrapperActive) {{
        const originalSend = bridge.sendRequest.bind(bridge);
        const wrapper = async function(method, params) {{
          if (method === 'list-models-for-host') {{
            const active = Array.isArray(window.__codexAppServerModelShimModels)
              ? window.__codexAppServerModelShimModels
              : [];
            const cursor = params && typeof params.cursor === 'string' ? params.cursor : null;
            const rawLimit = Number(params && params.limit);
            const limit = Number.isFinite(rawLimit) && rawLimit > 0 ? Math.floor(rawLimit) : Math.max(1, active.length);
            const start = cursor ? Math.max(0, Number(cursor) || 0) : 0;
            const data = active.slice(start, start + limit);
            const nextIndex = start + data.length;
            const nextCursor = nextIndex < active.length ? String(nextIndex) : null;
            const ids = data.map((item) => item.model || item.slug || '');
            const activeGeneration = String(window.__codexAppServerModelShimGeneration || '');
            if (params && params.__codexRuntimeProbe === true) {{
              window.__codexAppServerModelShimProbeModels = start === 0
                ? ids
                : [...(window.__codexAppServerModelShimProbeModels || []), ...ids];
              window.__codexAppServerModelShimProbeGeneration = activeGeneration;
              window.__codexAppServerModelShimProbeNextCursor = nextCursor;
              window.__codexAppServerModelShimProbeAt = Date.now();
            }} else {{
              window.__codexAppServerModelShimConsumedModels = start === 0
                ? ids
                : [...(window.__codexAppServerModelShimConsumedModels || []), ...ids];
              window.__codexAppServerModelShimConsumedGeneration = activeGeneration;
              window.__codexAppServerModelShimConsumedNextCursor = nextCursor;
              window.__codexAppServerModelShimConsumedAt = Date.now();
            }}
            return {{ data, nextCursor }};
          }}
          return originalSend(method, params);
        }};
        bridge.__codexAppServerModelShimOriginalSendRequest = originalSend;
        bridge.__codexAppServerModelShimWrapperSendRequest = wrapper;
        bridge.sendRequest = wrapper;
        window.__codexAppServerModelShimBridge = bridge;
        window.__codexAppServerModelShimWrapperSendRequest = wrapper;
        window.__codexAppServerModelShimOriginalSendRequest = originalSend;
      }}
      bridge.__codexAppServerModelShimGeneration = generation;
      bridge.__codexAppServerModelShimCatalogSignature = String(payload.catalogSignature || '');
      window.__codexAppServerModelShimVersion = payload.version;
      window.__codexAppServerModelShimExportName = bridgeEntry[0];
      window.__codexAppServerModelShimInstalledAt = Date.now();
      window.__codexAppServerModelShimError = '';
      const activeWrapper = bridge === window.__codexAppServerModelShimBridge
        && typeof window.__codexAppServerModelShimWrapperSendRequest === 'function'
        && bridge.sendRequest === window.__codexAppServerModelShimWrapperSendRequest;
      const consumedGeneration = String(window.__codexAppServerModelShimConsumedGeneration || '');
      const consumedNextCursor = window.__codexAppServerModelShimConsumedNextCursor ?? null;
      const queryRefetchConfirmed = consumedGeneration === generation && consumedNextCursor == null;
      const invalidatedGeneration = String(window.__codexAppServerModelShimInvalidatedGeneration || '');
      const invalidatedAt = Number(window.__codexAppServerModelShimInvalidatedAt || 0);
      const generationChanged = previousGeneration !== generation;
      const retryInvalidation = !queryRefetchConfirmed
        && (invalidatedGeneration !== generation || Date.now() - invalidatedAt >= 30000);
      const localQueryInvalidated = generationChanged || catalogChanged || !wrapperActive || retryInvalidation;
      if (localQueryInvalidated) {{
        invalidateModelQuery(
          !wrapperActive ? 'bridge_replaced'
            : catalogChanged ? 'catalog_changed'
            : generationChanged ? 'generation_changed'
            : 'consumer_unconfirmed'
        );
      }}
      return {{
        ok: true,
        installed: true,
        alreadyInstalled: previousVersion === payload.version && wrapperActive && !catalogChanged,
        version: payload.version,
        modelCount: models.length,
        wrapperActive: activeWrapper,
        generation,
        consumedGeneration,
        consumedNextCursor,
        queryRefetchConfirmed,
        localQueryInvalidated,
      }};
    }} catch (error) {{
      window.__codexAppServerModelShimError = String(error);
      return {{ ok: false, installed: false, reason: 'appserver_model_shim_install_failed', error: String(error) }};
    }}
  }}
  window.__codexInstallAppServerModelShim = install;
  return install();
}})()
"""


def build_probe_source(module_specifier: str) -> str:
    """Build a bounded live probe for the active AppServer model bridge."""
    payload = json.dumps(
        {"moduleSpecifier": module_specifier, "version": SHIM_VERSION, "maxPages": 32},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"""
(async () => {{
  const payload = {payload};
  const result = {{
    schema: 'codex-appserver-model-shim/live-probe/v1',
    ok: false,
    wrapperActive: false,
    version: String(window.__codexAppServerModelShimVersion || ''),
    generation: String(window.__codexAppServerModelShimGeneration || ''),
    consumedGeneration: String(window.__codexAppServerModelShimConsumedGeneration || ''),
    consumedNextCursor: window.__codexAppServerModelShimConsumedNextCursor ?? null,
    consumedModels: window.__codexAppServerModelShimConsumedModels || [],
    models: [],
    pageCount: 0,
    nextCursor: null,
    cursorLoop: false,
    queryRefetchConfirmed: false,
    reason: '',
  }};
  try {{
    const hostModule = await import(payload.moduleSpecifier);
    const bridgeEntry = Object.entries(hostModule).find(([, value]) =>
      value && typeof value === 'object' && typeof value.sendRequest === 'function'
    );
    const bridge = bridgeEntry && bridgeEntry[1];
    if (!bridge) {{
      result.reason = 'appserver_bridge_unavailable';
      return result;
    }}
    result.wrapperActive = bridge === window.__codexAppServerModelShimBridge
      && typeof window.__codexAppServerModelShimWrapperSendRequest === 'function'
      && bridge.sendRequest === window.__codexAppServerModelShimWrapperSendRequest;
    if (!result.wrapperActive) {{
      result.reason = 'appserver_wrapper_not_active';
      return result;
    }}
    const seen = new Set();
    let cursor = null;
    for (let page = 0; page < Number(payload.maxPages || 32); page += 1) {{
      const response = await bridge.sendRequest('list-models-for-host', {{
        hostId: 'local', includeHidden: true, cursor, limit: 100, __codexRuntimeProbe: true,
      }});
      const data = response && Array.isArray(response.data) ? response.data : [];
      result.models.push(...data.map((item) => item && (item.model || item.slug || '')).filter(Boolean));
      result.pageCount += 1;
      const nextCursor = response && response.nextCursor != null ? String(response.nextCursor) : null;
      result.nextCursor = nextCursor;
      if (nextCursor == null || nextCursor === '') break;
      if (seen.has(nextCursor)) {{
        result.cursorLoop = true;
        result.reason = 'appserver_cursor_loop';
        break;
      }}
      seen.add(nextCursor);
      cursor = nextCursor;
    }}
    result.consumedGeneration = String(window.__codexAppServerModelShimConsumedGeneration || '');
    result.consumedNextCursor = window.__codexAppServerModelShimConsumedNextCursor ?? null;
    result.consumedModels = window.__codexAppServerModelShimConsumedModels || [];
    result.queryRefetchConfirmed = result.consumedGeneration === result.generation
      && result.consumedNextCursor == null;
    result.ok = !result.cursorLoop && result.nextCursor == null;
    if (!result.ok && !result.reason) result.reason = 'appserver_probe_incomplete';
    return result;
  }} catch (error) {{
    result.reason = 'appserver_live_probe_failed';
    result.error = String(error);
    return result;
  }}
}})()
"""
