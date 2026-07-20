#!/usr/bin/env python3
"""Codex Desktop model-picker runtime diagnostics and narrow repairs.

Ownership: Codex config guard uses this module to compare CC Switch's model
catalog injection with the model state the running Codex Desktop UI can see.

Non-goals: this module must not edit Codex config files, patch app.asar,
kill/restart Codex, or add/remove CC Switch catalog models.

State behavior: runtime diagnostics are read-only. The explicit catalog repair
action is a narrow, backup-required caller path that only adds Desktop-compatible
fields and reasoning efforts to existing catalog entries.
The explicit bridge shim action is a runtime-only compatibility path for a
missing Electron fetch handler; it reads the active catalog and does not persist
inside Codex Desktop's application files.

Caller context: safe for guard snapshot/validate/doctor. If Desktop is not
running or CDP is unavailable, return skipped diagnostics instead of blocking
startup.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import socket
import struct
import time
import urllib.request
from pathlib import Path
from typing import Any

try:
    from shared.codex_desktop_package import query_desktop_host_processes
except ModuleNotFoundError:  # Package-style imports from the workspace root.
    from _bridge.shared.codex_desktop_package import query_desktop_host_processes

try:
    import codex_appserver_model_bridge
except ModuleNotFoundError:  # Package-style imports from the workspace root.
    from _bridge import codex_appserver_model_bridge


DEFAULT_ENABLED_REASONING_EFFORTS = {"low", "medium", "high", "xhigh", "ultra"}
SAFE_CATALOG_REASONING_LEVELS = ("none", "low", "medium", "high", "xhigh", "ultra")
DESKTOP_REASONING_EFFORT_ORDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
REASONING_DIRECTORY_EFFORTS = tuple(item for item in DESKTOP_REASONING_EFFORT_ORDER if item != "none")
REASONING_FEATURE_GATES = {"ultra": "1186680773"}
REASONING_DESCRIPTIONS = {
    "none": "Disable Thinking",
    "low": "Low Thinking",
    "medium": "Medium Thinking",
    "high": "High Thinking",
    "xhigh": "Extra High Thinking",
    "ultra": "Ultra Thinking",
}
MODEL_CONFIG_ID = "107580212"
MODEL_PICKER_HOST_KEY = "composer-model-picker-menu-view-v1"
MODEL_PICKER_VIEW_KEY = "codex:persisted-atom:composer-model-picker-menu-view-v1"
MODEL_PICKER_SYNC_KEY = "codex:model-picker-view-sync-signature:v1"
MODEL_PICKER_SYNC_ATTEMPT_KEY = "codex:model-picker-view-sync-attempt-v1"
MODEL_PICKER_SYNC_RETRY_COOLDOWN_SECONDS = 30.0
PERSISTED_STATE_API_ASSET_PATTERN = re.compile(r"^webview/assets/vscode-api-[^/]+\.js$")


def discover_persisted_state_host_module() -> dict[str, Any]:
    """Locate Desktop's versioned persisted-state message API module."""
    errors: list[str] = []
    for asar_path in codex_appserver_model_bridge.candidate_app_asars():
        try:
            files = codex_appserver_model_bridge._asar_files(asar_path)
            relative = next((name for name in files if PERSISTED_STATE_API_ASSET_PATTERN.match(name)), "")
            if relative:
                return {
                    "ok": True,
                    "asar_path": str(asar_path),
                    "asset_path": relative,
                    "module_specifier": f"./assets/{Path(relative).name}",
                    "reason": "",
                }
            errors.append(f"{asar_path}:persisted_state_module_missing")
        except Exception as exc:
            errors.append(f"{asar_path}:{type(exc).__name__}")
    return {
        "ok": False,
        "asar_path": "",
        "asset_path": "",
        "module_specifier": "",
        "reason": "persisted_state_module_discovery_failed",
        "errors": errors,
    }


def _wait_for_codex_page(wait_seconds: float = 0.0) -> tuple[int | None, str, list[dict[str, Any]], str]:
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        port, ws_url, pages, reason = _find_codex_page()
        if ws_url or time.monotonic() >= deadline:
            return port, ws_url, pages, reason
        time.sleep(0.5)


def _http_json(url: str, timeout_seconds: float = 2.0) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - localhost only
        return json.loads(response.read().decode("utf-8", errors="replace"))


def _discover_cdp_ports() -> list[int]:
    rows = query_desktop_host_processes(main_only=True)
    ports: list[int] = []
    for row in rows:
        command_line = str(row.get("CommandLine") or "") if isinstance(row, dict) else ""
        match = re.search(r"--remote-debugging-port=(\d+)", command_line)
        if match:
            port = int(match.group(1))
            if port not in ports:
                ports.append(port)
    for port in (9231, 9229):
        if port not in ports:
            ports.append(port)
    return ports


def _select_codex_page(pages: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [
        page
        for page in pages
        if isinstance(page, dict)
        and str(page.get("type") or "") == "page"
        and str(page.get("url") or "").startswith("app://")
        and str(page.get("webSocketDebuggerUrl") or "")
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda page: (
            str(page.get("url") or "") == "app://-/index.html",
            str(page.get("title") or "").casefold() == "codex",
        ),
    )


def _find_codex_page() -> tuple[int | None, str, list[dict[str, Any]], str]:
    errors: list[str] = []
    for port in _discover_cdp_ports():
        try:
            pages = _http_json(f"http://127.0.0.1:{port}/json/list")
        except Exception as exc:
            errors.append(f"{port}:{type(exc).__name__}")
            continue
        if not isinstance(pages, list):
            errors.append(f"{port}:pages_not_list")
            continue
        page = _select_codex_page(pages)
        if page is not None:
            return port, str(page.get("webSocketDebuggerUrl") or ""), pages, ""
        errors.append(f"{port}:codex_page_not_found")
    return None, "", [], ";".join(errors)


def _read_ws_frame(sock: socket.socket) -> str:
    header = sock.recv(2)
    if len(header) < 2:
        raise RuntimeError("websocket_frame_header_short")
    first, second = header
    opcode = first & 0x0F
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", sock.recv(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", sock.recv(8))[0]
    masked = bool(second & 0x80)
    mask = sock.recv(4) if masked else b""
    payload = bytearray()
    while len(payload) < length:
        chunk = sock.recv(length - len(payload))
        if not chunk:
            raise RuntimeError("websocket_frame_payload_short")
        payload.extend(chunk)
    if masked:
        payload = bytearray(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    if opcode == 8:
        raise RuntimeError("websocket_closed")
    return payload.decode("utf-8", errors="replace")


def _send_ws_frame(sock: socket.socket, text: str) -> None:
    payload = text.encode("utf-8")
    mask = os.urandom(4)
    header = bytearray([0x81])
    if len(payload) < 126:
        header.append(0x80 | len(payload))
    elif len(payload) <= 0xFFFF:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", len(payload)))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", len(payload)))
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    sock.sendall(bytes(header) + mask + masked)


class _CdpClient:
    def __init__(self, ws_url: str, timeout_seconds: float = 4.0) -> None:
        match = re.match(r"ws://127\.0\.0\.1:(\d+)(/.*)", ws_url)
        if not match:
            raise ValueError("unsupported_cdp_websocket_url")
        self.port = int(match.group(1))
        self.path = match.group(2)
        self.timeout_seconds = timeout_seconds
        self.sock = socket.create_connection(("127.0.0.1", self.port), timeout=timeout_seconds)
        self.sock.settimeout(timeout_seconds)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode("ascii"))
        response = self.sock.recv(4096).decode("iso-8859-1", errors="replace")
        if " 101 " not in response.split("\r\n", 1)[0]:
            raise RuntimeError("websocket_handshake_failed")
        self._next_id = 0

    def close(self) -> None:
        try:
            self.sock.close()
        except Exception:
            pass

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        _send_ws_frame(self.sock, json.dumps({"id": request_id, "method": method, "params": params or {}}))
        deadline = time.time() + self.timeout_seconds
        while time.time() < deadline:
            message = json.loads(_read_ws_frame(self.sock))
            if message.get("id") == request_id:
                return message
        raise TimeoutError("cdp_request_timeout")

    def evaluate(self, expression: str) -> Any:
        response = self.call(
            "Runtime.evaluate",
            {"expression": expression, "awaitPromise": True, "returnByValue": True},
        )
        result = response.get("result") if isinstance(response, dict) else None
        if not isinstance(result, dict):
            return response
        if result.get("exceptionDetails"):
            return {"exceptionDetails": result.get("exceptionDetails")}
        value = result.get("result") if isinstance(result.get("result"), dict) else {}
        return value.get("value")


def _app_post_expression() -> str:
    return r"""
(async () => {
  function appPost(method, body) {
    return new Promise((resolve) => {
      const requestId = crypto.randomUUID();
      const timer = setTimeout(() => { cleanup(); resolve({timeout:true}); }, 5000);
      function cleanup(){ clearTimeout(timer); window.removeEventListener('message', onMsg); }
      function onMsg(ev){
        const d = ev.data;
        if (!d || d.type !== 'fetch-response' || d.requestId !== requestId) return;
        cleanup(); resolve(d);
      }
      window.addEventListener('message', onMsg);
      window.electronBridge.sendMessageFromView({
        type:'fetch', requestId, method:'POST', url:'vscode://codex/'+method,
        body: body == null ? undefined : JSON.stringify(body)
      }).catch(e => { cleanup(); resolve({bridgeError:String(e)}); });
    });
  }
  const statsig = [];
  for (const k of Object.keys(localStorage)) {
    if (!k.startsWith('statsig.cached.evaluations')) continue;
    try {
      const outer = JSON.parse(localStorage.getItem(k));
      const data = JSON.parse(outer.data);
      const cfg = data.dynamic_configs && data.dynamic_configs['107580212'];
      statsig.push({key:k, cfg});
    } catch (e) {
      statsig.push({key:k, error:String(e)});
    }
  }
  const settings = await appPost('get-settings', null);
  const listModelsForHost = await appPost('list-models-for-host', {hostId:'local', includeHidden:true, cursor:null, limit:100});
  let modelPickerView = '';
  try {
    const raw = localStorage.getItem('codex:persisted-atom:composer-model-picker-menu-view-v1');
    const parsed = raw == null ? '' : JSON.parse(raw);
    modelPickerView = typeof parsed === 'string' ? parsed : '';
  } catch {}
  const modelPickerViewSyncSignature = String(
    localStorage.getItem('codex:model-picker-view-sync-signature:v1') || ''
  );
  return {statsig, settings, listModelsForHost, modelPickerView, modelPickerViewSyncSignature};
})()
"""


def _model_list_bridge_shim_source(models: list[dict[str, Any]]) -> str:
    signature = hashlib.sha256(
        json.dumps(models, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    payload = json.dumps(
        {"models": models, "signature": signature},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"""
(() => {{
  const shimVersion = 'codex-model-list-bridge-shim/v6';
  const payload = {payload};
  const catalogModels = Array.isArray(payload.models) ? payload.models : [];
  const signature = String(payload.signature || '');
  function invalidateModelQuery(reason) {{
    window.__codexModelListShimInvalidatedSignature = signature;
    window.__codexModelListShimInvalidatedAt = Date.now();
    window.__codexModelListShimInvalidationReason = reason;
    window.dispatchEvent(new MessageEvent('message', {{ data: {{
      type: 'ipc-broadcast',
      method: 'query-cache-invalidate',
      params: {{ queryKey: ['models', 'list'] }},
      sourceClientId: 'codex-model-list-bridge-shim',
    }} }}));
  }}
  function install() {{
    const previousSignature = String(window.__codexModelListShimSignature || '');
    window.__codexModelListShimModels = catalogModels;
    window.__codexModelListShimSignature = signature;
    if (window.__codexModelListShimVersion === shimVersion) {{
      const consumedSignature = String(window.__codexModelListShimConsumedSignature || '');
      if (previousSignature !== signature || consumedSignature !== signature) {{
        invalidateModelQuery(previousSignature !== signature ? 'catalog_changed' : 'consumer_unconfirmed');
      }}
      return {{
        ok: true,
        installed: true,
        alreadyInstalled: true,
        updated: previousSignature !== signature,
        previousSignature,
        signature,
        modelCount: catalogModels.length,
      }};
    }}
    if (typeof window.__codexModelListShimHandler === 'function') {{
      window.removeEventListener('message', window.__codexModelListShimHandler, true);
    }}
    const bridge = window.electronBridge;
    if (!bridge || typeof bridge.sendMessageFromView !== 'function') {{
      return {{ ok: false, installed: false, reason: 'electron_bridge_not_ready' }};
    }}
    const originalSend = typeof window.__codexModelListShimOriginalSendMessageFromView === 'function'
      ? window.__codexModelListShimOriginalSendMessageFromView
      : bridge.sendMessageFromView.bind(bridge);
    window.__codexModelListShimOriginalSendMessageFromView = originalSend;
    bridge.sendMessageFromView = async function(message) {{
      try {{
        const url = String(message && message.url || '');
        const method = String(message && message.method || 'GET').toUpperCase();
        if (message && message.type === 'fetch' && method === 'POST' && url === 'vscode://codex/list-models-for-host') {{
          let body = {{}};
          try {{ body = JSON.parse(message.body || '{{}}'); }} catch {{ body = {{}}; }}
          const activeModels = Array.isArray(window.__codexModelListShimModels)
            ? window.__codexModelListShimModels
            : [];
          const cursor = body && typeof body.cursor === 'string' ? body.cursor : null;
          const rawLimit = Number(body && body.limit);
          const limit = Number.isFinite(rawLimit) && rawLimit > 0 ? Math.floor(rawLimit) : activeModels.length;
          const start = cursor ? Math.max(0, Number(cursor) || 0) : 0;
          const data = activeModels.slice(start, start + limit);
          const nextIndex = start + data.length;
          const nextCursor = nextIndex < activeModels.length ? String(nextIndex) : null;
          window.__codexModelListShimConsumedSignature = String(window.__codexModelListShimSignature || '');
          window.__codexModelListShimConsumedModels = data.map((item) => item.slug || item.model || '');
          window.__codexModelListShimConsumedNextCursor = nextCursor;
          window.__codexModelListShimConsumedAt = Date.now();
          const responseBody = {{ data, nextCursor }};
          queueMicrotask(() => window.dispatchEvent(new MessageEvent('message', {{
            data: {{
              type: 'fetch-response',
              responseType: 'success',
              requestId: message.requestId,
              status: 200,
              headers: {{ 'content-type': 'application/json' }},
              bodyJsonString: JSON.stringify(responseBody),
            }},
          }})));
          return;
        }}
      }} catch (error) {{
        queueMicrotask(() => window.dispatchEvent(new MessageEvent('message', {{
          data: {{
            type: 'fetch-response',
            responseType: 'error',
            requestId: message && message.requestId,
            status: 500,
            error: String(error),
          }},
        }})));
        return;
      }}
      return originalSend(message);
    }};
    const listener = function(event) {{
      try {{
        const message = event && event.data;
        if (!message || message.type !== 'fetch-response') return;
        const pending = window.__codexModelListShimPendingRequestIds;
        const requestId = String(message.requestId || '');
        const tracked = pending instanceof Set && pending.has(requestId);
        const error = String(message.error || '');
        const unsupported = message.responseType === 'error' && error.includes('list-models-for-host not implemented');
        if (!tracked && !unsupported) return;
        if (tracked) pending.delete(requestId);
        event.stopImmediatePropagation();
        const activeModels = Array.isArray(window.__codexModelListShimModels)
          ? window.__codexModelListShimModels
          : [];
        const responseBody = {{ data: activeModels, nextCursor: null }};
        queueMicrotask(() => window.dispatchEvent(new MessageEvent('message', {{
          data: {{
            type: 'fetch-response',
            responseType: 'success',
            requestId: message.requestId,
            status: 200,
            headers: {{ 'content-type': 'application/json' }},
            bodyJsonString: JSON.stringify(responseBody),
          }},
        }})));
      }} catch (error) {{
        console.warn('[codex-model-list-bridge-shim] failed to handle model-list response', error);
      }}
    }};
    window.addEventListener('message', listener, true);
    window.__codexModelListShimHandler = listener;
    window.__codexModelListShimVersion = shimVersion;
    window.__codexModelListShimInstalledAt = Date.now();
    invalidateModelQuery('shim_installed');
    return {{
      ok: true,
      installed: true,
      alreadyInstalled: false,
      updated: previousSignature !== signature,
      previousSignature,
      signature,
      modelCount: catalogModels.length,
    }};
  }}
  window.__codexInstallModelListBridgeShim = install;
  return install();
}})()
"""


def apply_model_list_bridge_shim(
    catalog_path: Path | None,
    reload_page: bool = False,
    wait_seconds: float = 0.0,
) -> dict[str, Any]:
    models = _catalog_bridge_models(catalog_path)
    state: dict[str, Any] = {
        "schema": "codex-desktop-model-runtime/model-list-bridge-shim/v1",
        "ok": False,
        "catalog_path": str(catalog_path) if catalog_path else "",
        "model_count": len(models),
        "models": [_catalog_model_id(item) for item in models],
        "applied": False,
        "skipped": False,
        "reload_requested": False,
        "wait_seconds": wait_seconds,
        "reason": "",
    }
    if not models:
        return {**state, "reason": "catalog_models_unavailable"}
    deadline = time.monotonic() + max(0.0, wait_seconds)
    port = 0
    ws_url = ""
    pages: list[dict[str, Any]] = []
    reason = ""
    while True:
        port, ws_url, pages, reason = _find_codex_page()
        if ws_url or time.monotonic() >= deadline:
            break
        time.sleep(0.5)
    state["cdp_port"] = port
    state["page_count"] = len(pages)
    if not ws_url:
        return {**state, "ok": True, "skipped": True, "reason": reason or "codex_desktop_cdp_unavailable"}
    client: _CdpClient | None = None
    try:
        client = _CdpClient(ws_url)
        client.call("Runtime.enable")
        client.call("Page.enable")
        source = _model_list_bridge_shim_source(models)
        client.call("Page.addScriptToEvaluateOnNewDocument", {"source": source})
        immediate = client.evaluate(source)
        if isinstance(immediate, dict) and immediate.get("exceptionDetails"):
            return {
                **state,
                "ok": False,
                "applied": False,
                "reason": "model_list_bridge_shim_runtime_exception",
                "immediate": immediate,
            }
        if reload_page:
            client.call("Page.reload", {"ignoreCache": True})
            state["reload_requested"] = True
        return {**state, "ok": True, "applied": True, "immediate": immediate}
    except Exception as exc:
        return {
            **state,
            "ok": False,
            "applied": False,
            "reason": "model_list_bridge_shim_apply_failed",
            "error": repr(exc),
        }
    finally:
        if client is not None:
            client.close()


def apply_appserver_model_shim(
    catalog_path: Path | None,
    wait_seconds: float = 0.0,
) -> dict[str, Any]:
    models = _catalog_bridge_models(catalog_path)
    discovery = codex_appserver_model_bridge.discover_host_module()
    state: dict[str, Any] = {
        "schema": "codex-desktop-model-runtime/appserver-model-shim/v1",
        "ok": False,
        "catalog_path": str(catalog_path) if catalog_path else "",
        "model_count": len(models),
        "models": [_catalog_model_id(item) for item in models],
        "module_discovery": discovery,
        "applied": False,
        "skipped": False,
        "wait_seconds": wait_seconds,
        "reason": "",
    }
    if not models:
        return {**state, "reason": "catalog_models_unavailable"}
    if not discovery.get("ok"):
        return {**state, "ok": True, "skipped": True, "reason": "appserver_module_unavailable"}
    port, ws_url, pages, reason = _wait_for_codex_page(wait_seconds)
    state["cdp_port"] = port
    state["page_count"] = len(pages)
    if not ws_url:
        return {**state, "ok": True, "skipped": True, "reason": reason or "codex_desktop_cdp_unavailable"}
    client: _CdpClient | None = None
    try:
        client = _CdpClient(ws_url)
        client.call("Runtime.enable")
        client.call("Page.enable")
        source = codex_appserver_model_bridge.build_shim_source(
            models,
            str(discovery.get("module_specifier") or ""),
        )
        registration = client.call("Page.addScriptToEvaluateOnNewDocument", {"source": source})
        immediate = client.evaluate(source)
        probe = client.evaluate(
            codex_appserver_model_bridge.build_probe_source(
                str(discovery.get("module_specifier") or "")
            )
        )
        probe = probe if isinstance(probe, dict) else {"raw": probe}
        probe_models = [str(item) for item in probe.get("models", []) if item]
        expected_models = [str(item) for item in state.get("models", []) if item]
        installed = bool(
            isinstance(immediate, dict)
            and immediate.get("ok")
            and immediate.get("wrapperActive")
            and probe.get("ok")
            and probe.get("wrapperActive")
            and probe.get("version") == codex_appserver_model_bridge.SHIM_VERSION
            and probe_models == expected_models
        )
        return {
            **state,
            "ok": installed,
            "applied": installed,
            "registration_id": str(registration.get("result", {}).get("identifier") or ""),
            "immediate": immediate,
            "probe": probe,
            "reason": "" if installed else (
                "appserver_model_shim_model_mismatch"
                if probe.get("ok") and probe.get("wrapperActive") and probe_models != expected_models
                else "appserver_model_shim_apply_failed"
            ),
        }
    except Exception as exc:
        return {
            **state,
            "ok": False,
            "reason": "appserver_model_shim_apply_failed",
            "error": repr(exc),
        }
    finally:
        if client is not None:
            client.close()


def _model_picker_view_sync_expression(
    signature: str,
    *,
    apply: bool,
    require_advanced: bool = True,
    retry_cooldown_seconds: float = MODEL_PICKER_SYNC_RETRY_COOLDOWN_SECONDS,
) -> str:
    module_discovery = discover_persisted_state_host_module()
    payload = json.dumps(
        {
            "signature": signature,
            "apply": apply,
            "requireAdvanced": require_advanced,
            "retryCooldownMs": max(0, int(retry_cooldown_seconds * 1000)),
            "hostKey": MODEL_PICKER_HOST_KEY,
            "legacyViewKey": MODEL_PICKER_VIEW_KEY,
            "syncKey": MODEL_PICKER_SYNC_KEY,
            "attemptKey": MODEL_PICKER_SYNC_ATTEMPT_KEY,
            "moduleSpecifier": str(module_discovery.get("module_specifier") or ""),
            "hostTimeoutMs": 1200,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    template = r"""
(async () => {
  const payload = __PAYLOAD__;
  const result = {
    schema: 'codex-desktop-model-runtime/model-picker-view-sync-result/v2',
    ok: true,
    signature: String(payload.signature || ''),
    previousSignature: String(localStorage.getItem(payload.syncKey) || ''),
    signatureAfter: '',
    viewBefore: '',
    viewAfter: '',
    requireAdvanced: Boolean(payload.requireAdvanced),
    signatureCurrent: false,
    viewCurrent: false,
    syncRequired: false,
    retryDeferred: false,
    retryAfterMs: 0,
    lastAttemptAt: Number(localStorage.getItem(payload.attemptKey) || 0),
    attemptedAt: 0,
    moduleSpecifier: String(payload.moduleSpecifier || ''),
    nativeHostAvailable: false,
    hostSyncReceived: false,
    hostUpdateObserved: false,
    hostPersistenceConfirmed: false,
    persistenceConfirmed: false,
    persistenceRoute: '',
    legacyFallbackUsed: false,
    reloadSafe: false,
    applied: false,
    changed: false,
    converged: false,
    reason: '',
  };

  function legacyView() {
    try {
      const raw = localStorage.getItem(payload.legacyViewKey);
      const parsed = raw == null ? '' : JSON.parse(raw);
      return typeof parsed === 'string' ? parsed : '';
    } catch {
      return '';
    }
  }

  async function loadHostApi() {
    if (!payload.moduleSpecifier) return null;
    const module = await import(payload.moduleSpecifier);
    const entry = Object.entries(module).find(([, value]) =>
      value && typeof value === 'object'
        && typeof value.dispatchMessage === 'function'
        && typeof value.subscribe === 'function'
    );
    return entry ? entry[1] : null;
  }

  function requestHostState(api) {
    return new Promise((resolve) => {
      let settled = false;
      let timer = 0;
      let unsubscribe = () => {};
      const finish = (value) => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timer);
        unsubscribe();
        resolve(value);
      };
      unsubscribe = api.subscribe('persisted-atom-sync', (message) => {
        const state = message && typeof message.state === 'object' && message.state !== null
          ? message.state
          : {};
        finish({received: true, state});
      });
      timer = window.setTimeout(
        () => finish({received: false, state: {}}),
        Number(payload.hostTimeoutMs || 1200),
      );
      api.dispatchMessage('persisted-atom-sync-request', {});
    });
  }

  function waitForHostUpdate(api) {
    return new Promise((resolve) => {
      let settled = false;
      let timer = 0;
      let unsubscribe = () => {};
      const finish = (observed) => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timer);
        unsubscribe();
        resolve(observed);
      };
      unsubscribe = api.subscribe('persisted-atom-updated', (message) => {
        if (!message || message.key !== payload.hostKey) return;
        finish(message.deleted !== true && message.value === 'advanced');
      });
      timer = window.setTimeout(() => finish(false), Math.min(600, Number(payload.hostTimeoutMs || 1200)));
    });
  }

  result.signatureAfter = result.previousSignature;
  let hostApi = null;
  try {
    hostApi = await loadHostApi();
  } catch (error) {
    result.reason = 'model_picker_host_api_import_failed';
    result.error = String(error);
  }
  result.nativeHostAvailable = Boolean(hostApi);
  if (hostApi) {
    const before = await requestHostState(hostApi);
    result.hostSyncReceived = Boolean(before.received);
    if (before.received) {
      const value = before.state[payload.hostKey];
      result.viewBefore = typeof value === 'string' ? value : '';
      result.persistenceRoute = 'desktop_host';
    } else if (!result.reason) {
      result.reason = 'model_picker_host_sync_timeout';
    }
  }
  if (!result.persistenceRoute) {
    if (!payload.moduleSpecifier) {
      result.persistenceRoute = 'legacy_local_storage';
      result.legacyFallbackUsed = true;
      result.viewBefore = legacyView();
    } else {
      result.persistenceRoute = 'desktop_host_unavailable';
    }
  }
  result.viewAfter = result.viewBefore;
  result.signatureCurrent = Boolean(result.signature) && result.previousSignature === result.signature;
  result.viewCurrent = !result.requireAdvanced || result.viewBefore === 'advanced';
  result.syncRequired = Boolean(result.signature) && (!result.signatureCurrent || !result.viewCurrent);
  if (payload.apply && result.syncRequired) {
    const now = Date.now();
    const elapsed = result.lastAttemptAt > 0 ? now - result.lastAttemptAt : Number.POSITIVE_INFINITY;
    if (result.requireAdvanced && result.viewBefore !== 'advanced' && elapsed < payload.retryCooldownMs) {
      result.retryDeferred = true;
      result.retryAfterMs = Math.max(0, payload.retryCooldownMs - elapsed);
      result.reason = 'model_picker_sync_retry_cooldown';
    } else {
      if (result.requireAdvanced && result.viewBefore !== 'advanced') {
        const encoded = JSON.stringify('advanced');
        localStorage.setItem(payload.attemptKey, String(now));
        result.attemptedAt = now;
        if (hostApi && result.hostSyncReceived) {
          const updateObserved = waitForHostUpdate(hostApi);
          hostApi.dispatchMessage('persisted-atom-update', {
            key: payload.hostKey,
            value: 'advanced',
            deleted: false,
          });
          result.hostUpdateObserved = await updateObserved;
          const confirmed = await requestHostState(hostApi);
          result.hostSyncReceived = result.hostSyncReceived || Boolean(confirmed.received);
          const confirmedValue = confirmed.received ? confirmed.state[payload.hostKey] : undefined;
          result.viewAfter = typeof confirmedValue === 'string' ? confirmedValue : '';
          result.hostPersistenceConfirmed = result.viewAfter === 'advanced';
          result.persistenceConfirmed = result.hostPersistenceConfirmed;
          result.reloadSafe = result.hostPersistenceConfirmed;
          result.changed = result.hostPersistenceConfirmed;
          if (!result.hostPersistenceConfirmed) {
            result.ok = false;
            result.reason = 'model_picker_host_persistence_unconfirmed';
          }
        } else if (!payload.moduleSpecifier) {
          localStorage.setItem(payload.legacyViewKey, encoded);
          result.viewAfter = legacyView();
          result.persistenceConfirmed = result.viewAfter === 'advanced';
          result.reloadSafe = result.persistenceConfirmed;
          result.changed = result.persistenceConfirmed;
        } else {
          result.ok = false;
          result.reason = result.reason || 'model_picker_native_host_unavailable';
        }
      } else {
        result.persistenceConfirmed = !result.requireAdvanced || result.hostSyncReceived || !payload.moduleSpecifier;
        result.hostPersistenceConfirmed = result.persistenceRoute === 'desktop_host'
          && result.hostSyncReceived
          && (!result.requireAdvanced || result.viewAfter === 'advanced');
      }
      if ((!result.requireAdvanced || result.viewAfter === 'advanced') && result.persistenceConfirmed) {
        localStorage.setItem(payload.syncKey, result.signature);
        result.signatureAfter = result.signature;
        result.applied = true;
      }
    }
  } else {
    result.persistenceConfirmed = !result.requireAdvanced
      || (result.persistenceRoute === 'desktop_host' && result.hostSyncReceived)
      || (!payload.moduleSpecifier && result.viewAfter === 'advanced');
    result.hostPersistenceConfirmed = result.persistenceRoute === 'desktop_host'
      && result.hostSyncReceived
      && (!result.requireAdvanced || result.viewAfter === 'advanced');
  }
  if (!result.signature) {
    result.ok = false;
    result.reason = 'model_picker_sync_signature_missing';
  }
  result.converged = Boolean(result.signature)
    && result.signatureAfter === result.signature
    && (!result.requireAdvanced || result.viewAfter === 'advanced')
    && result.persistenceConfirmed;
  window.__codexModelPickerPersistedHostView = result.viewAfter;
  window.__codexModelPickerPersistedHostConfirmed = result.persistenceConfirmed;
  window.__codexModelPickerPersistedRoute = result.persistenceRoute;
  return result;
})()
"""
    return template.replace("__PAYLOAD__", payload)


def model_picker_view_sync_state(
    signature: str,
    *,
    apply: bool = False,
    require_advanced: bool = True,
    retry_cooldown_seconds: float = MODEL_PICKER_SYNC_RETRY_COOLDOWN_SECONDS,
    wait_seconds: float = 0.0,
) -> dict[str, Any]:
    """Converge catalog-backed providers on Desktop's full model-picker view.

    The provider signature and the host-persisted view are independent health
    signals. Bounded retry state and host readback prevent a persistence race
    from causing an unbounded reload loop.
    """
    state: dict[str, Any] = {
        "schema": "codex-desktop-model-runtime/model-picker-view-sync/v2",
        "ok": True,
        "signature": signature,
        "apply": apply,
        "module_discovery": discover_persisted_state_host_module(),
        "cdp_port": None,
        "page_count": 0,
        "skipped": False,
        "result": {},
        "reason": "",
    }
    if not signature:
        return {**state, "ok": False, "reason": "model_picker_sync_signature_missing"}
    port, ws_url, pages, reason = _wait_for_codex_page(wait_seconds)
    state["cdp_port"] = port
    state["page_count"] = len(pages)
    if not ws_url:
        return {**state, "ok": True, "skipped": True, "reason": reason or "codex_desktop_cdp_unavailable"}
    client: _CdpClient | None = None
    try:
        client = _CdpClient(ws_url)
        client.call("Runtime.enable")
        result = client.evaluate(
            _model_picker_view_sync_expression(
                signature,
                apply=apply,
                require_advanced=require_advanced,
                retry_cooldown_seconds=retry_cooldown_seconds,
            )
        )
    except Exception as exc:
        return {**state, "ok": False, "reason": "model_picker_view_sync_failed", "error": repr(exc)}
    finally:
        if client is not None:
            client.close()
    result = result if isinstance(result, dict) else {"raw": result}
    return {
        **state,
        "ok": bool(result.get("ok")),
        "result": result,
        "reason": str(result.get("reason") or ""),
    }


def request_desktop_page_reload(*, wait_seconds: float = 0.0) -> dict[str, Any]:
    state: dict[str, Any] = {
        "schema": "codex-desktop-model-runtime/page-reload/v1",
        "ok": True,
        "requested": False,
        "cdp_port": None,
        "page_count": 0,
        "skipped": False,
        "reason": "",
    }
    port, ws_url, pages, reason = _wait_for_codex_page(wait_seconds)
    state["cdp_port"] = port
    state["page_count"] = len(pages)
    if not ws_url:
        return {**state, "ok": True, "skipped": True, "reason": reason or "codex_desktop_cdp_unavailable"}
    client: _CdpClient | None = None
    try:
        client = _CdpClient(ws_url)
        client.call("Page.enable")
        client.call("Page.reload", {"ignoreCache": True})
        return {**state, "requested": True}
    except Exception as exc:
        return {**state, "ok": False, "reason": "codex_desktop_page_reload_failed", "error": repr(exc)}
    finally:
        if client is not None:
            client.close()


def _decode_body_json_string(response: Any) -> Any:
    if not isinstance(response, dict):
        return None
    body = response.get("bodyJsonString")
    if not isinstance(body, str):
        return None
    try:
        return json.loads(body)
    except Exception:
        return None


def _statsig_value(statsig_entries: Any) -> dict[str, Any]:
    entries = statsig_entries if isinstance(statsig_entries, list) else []
    for entry in entries:
        cfg = entry.get("cfg") if isinstance(entry, dict) else None
        if isinstance(cfg, dict) and cfg.get("name") == MODEL_CONFIG_ID:
            value = cfg.get("value")
            return value if isinstance(value, dict) else {}
    return {}


def _settings_enabled_efforts(settings_response: Any) -> set[str]:
    body = _decode_body_json_string(settings_response)
    if not isinstance(body, dict):
        return set(DEFAULT_ENABLED_REASONING_EFFORTS)
    values = body.get("values") if isinstance(body.get("values"), dict) else {}
    raw = values.get("enabled-reasoning-efforts")
    if not isinstance(raw, list):
        return set(DEFAULT_ENABLED_REASONING_EFFORTS)
    efforts = {item for item in raw if isinstance(item, str) and item.strip()}
    return efforts or set(DEFAULT_ENABLED_REASONING_EFFORTS)


def _catalog_visible_model_ids(catalog_path: Path | None) -> list[str]:
    if not catalog_path or not catalog_path.exists():
        return []
    try:
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    raw_models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(raw_models, list):
        return []
    model_ids: list[str] = []
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        model_id = _catalog_model_id(item)
        if model_id and item.get("hidden") is not True and model_id not in model_ids:
            model_ids.append(model_id)
    return model_ids


def catalog_declared_reasoning_efforts(catalog_path: Path | None) -> set[str]:
    if not catalog_path or not catalog_path.is_file():
        return set()
    try:
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        return set()
    declared: set[str] = set()
    for item in models:
        if not isinstance(item, dict) or item.get("hidden") is True:
            continue
        declared.update(reasoning_efforts_for_key(item, "supported_reasoning_levels"))
        declared.update(reasoning_efforts_for_key(item, "supportedReasoningEfforts"))
    return declared


def catalog_requested_reasoning_efforts(catalog_path: Path | None) -> list[str]:
    """Return catalog-declared selectable efforts understood by this Desktop build.

    ``none`` remains valid model metadata but is not an intensity directory item.
    Existing Desktop settings are preserved; this list only supplies missing
    provider-declared capabilities such as ``minimal``, ``max``, or ``ultra``.
    """
    declared = catalog_declared_reasoning_efforts(catalog_path)
    return [item for item in REASONING_DIRECTORY_EFFORTS if item in declared]


def _catalog_bridge_models(catalog_path: Path | None) -> list[dict[str, Any]]:
    """Return catalog entries in the shape Desktop's model-list query expects."""
    if not catalog_path or not catalog_path.exists():
        return []
    try:
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in models:
        if not isinstance(item, dict):
            continue
        model_id = _catalog_model_id(item)
        if not model_id or model_id in seen or item.get("hidden") is True:
            continue
        seen.add(model_id)
        next_item = dict(item)
        next_item.setdefault("slug", model_id)
        next_item.setdefault("model", model_id)
        next_item.setdefault("display_name", model_id)
        next_item.setdefault("displayName", next_item.get("display_name") or model_id)
        next_item.setdefault("description", next_item.get("display_name") or model_id)
        next_item.setdefault("hidden", False)
        next_item.setdefault("isDefault", False)
        default_effort = next_item.get("defaultReasoningEffort") or next_item.get("default_reasoning_level") or "high"
        if isinstance(default_effort, str) and default_effort:
            next_item["defaultReasoningEffort"] = default_effort
        efforts = next_item.get("supportedReasoningEfforts")
        if not isinstance(efforts, list) or not efforts:
            next_item["supportedReasoningEfforts"] = [
                {"reasoningEffort": effort, "description": REASONING_DESCRIPTIONS.get(effort, effort)}
                for effort in SAFE_CATALOG_REASONING_LEVELS
            ]
        result.append(next_item)
    return result


def _statsig_allowlist_sync_expression(model_ids: list[str], *, apply: bool, reload_if_changed: bool) -> str:
    payload = json.dumps({"modelIds": model_ids, "apply": apply, "reloadIfChanged": reload_if_changed})
    return f"""
(async () => {{
  const payload = {payload};
  const requiredModels = Array.from(new Set(payload.modelIds || []));
  const result = {{
    schema: 'codex-desktop-model-runtime/statsig-allowlist-sync-result/v1',
    ok: true,
    applied: false,
    changed: false,
    reloadRequested: false,
    statsigKeys: [],
    before: [],
    after: [],
    added: [],
    removed: [],
    missingAfter: [],
    reason: '',
  }};
  if (!requiredModels.length) {{
    result.ok = false;
    result.reason = 'no_catalog_models';
    return result;
  }}
  const keys = Object.keys(localStorage).filter((key) => key.startsWith('statsig.cached.evaluations'));
  result.statsigKeys = keys;
  for (const key of keys) {{
    try {{
      const outer = JSON.parse(localStorage.getItem(key));
      const data = JSON.parse(outer.data);
      const cfg = data.dynamic_configs && data.dynamic_configs['{MODEL_CONFIG_ID}'];
      const value = cfg && cfg.value;
      if (!value || !Array.isArray(value.available_models)) continue;
      const before = value.available_models.filter((item) => typeof item === 'string');
      const synced = Array.from(new Set([...before, ...requiredModels]));
      const added = synced.filter((item) => !before.includes(item));
      const removed = [];
      result.before = before;
      result.after = synced;
      result.added = added;
      result.removed = removed;
      result.missingAfter = requiredModels.filter((item) => !synced.includes(item));
      result.changed = added.length > 0 || removed.length > 0 || before.length !== synced.length;
      if (payload.apply && result.changed) {{
        value.available_models = synced;
        outer.data = JSON.stringify(data);
        localStorage.setItem(key, JSON.stringify(outer));
        result.applied = true;
        if (payload.reloadIfChanged) {{
          result.reloadRequested = true;
          setTimeout(() => location.reload(), 50);
        }}
      }}
      return result;
    }} catch (error) {{
      result.lastError = String(error);
    }}
  }}
  result.ok = false;
  result.reason = 'statsig_model_config_not_found';
  return result;
}})()
"""


def statsig_allowlist_sync_state(
    catalog_path: Path | None,
    *,
    apply: bool = False,
    reload_if_changed: bool = False,
    wait_seconds: float = 0.0,
) -> dict[str, Any]:
    """Sync Desktop's cached model allowlist to the active CC Switch catalog.

    This is a compatibility shim for Desktop builds that first fetch models from
    Codex app-server but then filter them through Statsig dynamic config 107580212.
    The sync only adds active-provider catalog models to Desktop's current
    allowlist. It preserves native models and unrelated Statsig configuration,
    and never changes the default model, auth, provider, or app package files.
    """
    model_ids = _catalog_visible_model_ids(catalog_path)
    state: dict[str, Any] = {
        "schema": "codex-desktop-model-runtime/statsig-allowlist-sync/v1",
        "ok": True,
        "catalog_path": str(catalog_path) if catalog_path else "",
        "catalog_models": model_ids,
        "apply": apply,
        "reload_if_changed": reload_if_changed,
        "wait_seconds": wait_seconds,
        "cdp_port": None,
        "result": {},
        "reason": "",
        "attempts": [],
    }
    if not model_ids:
        return {**state, "ok": False, "reason": "no_catalog_models"}
    deadline = time.monotonic() + max(0.0, wait_seconds)
    last_state: dict[str, Any] = state
    while True:
        port, ws_url, pages, reason = _find_codex_page()
        state["cdp_port"] = port
        state["page_count"] = len(pages)
        attempt: dict[str, Any] = {
            "cdp_port": port,
            "page_count": len(pages),
            "find_reason": reason,
            "result_reason": "",
            "ok": False,
        }
        if not ws_url:
            attempt["result_reason"] = reason or "codex_desktop_cdp_unavailable"
            state["attempts"].append(attempt)
            last_state = {**state, "ok": False, "reason": attempt["result_reason"]}
        else:
            client: _CdpClient | None = None
            try:
                client = _CdpClient(ws_url)
                client.call("Runtime.enable")
                result = client.evaluate(
                    _statsig_allowlist_sync_expression(
                        model_ids,
                        apply=apply,
                        reload_if_changed=reload_if_changed,
                    )
                )
            except Exception as exc:
                attempt["result_reason"] = "statsig_allowlist_sync_probe_failed"
                attempt["error"] = repr(exc)
                state["attempts"].append(attempt)
                last_state = {**state, "ok": False, "reason": attempt["result_reason"], "error": repr(exc)}
            finally:
                if client is not None:
                    client.close()
            if "result" in locals():
                result = result if isinstance(result, dict) else {"raw": result}
                missing_after = result.get("missingAfter") if isinstance(result.get("missingAfter"), list) else []
                ok = bool(result.get("ok")) and not missing_after
                reason_text = str(result.get("reason") or "")
                attempt["ok"] = ok
                attempt["result_reason"] = reason_text
                attempt["changed"] = bool(result.get("changed"))
                attempt["applied"] = bool(result.get("applied"))
                state["attempts"].append(attempt)
                last_state = {**state, "ok": ok, "result": result, "reason": reason_text}
                if ok or reason_text not in {"statsig_model_config_not_found", ""}:
                    return last_state
                del result

        if time.monotonic() >= deadline:
            return last_state
        time.sleep(1.0)


def _statsig_allowlist_protect_expression(model_ids: list[str], *, reload_if_changed: bool) -> str:
    signature = hashlib.sha256("\n".join(model_ids).encode("utf-8")).hexdigest()
    payload = json.dumps(
        {"modelIds": model_ids, "reloadIfChanged": reload_if_changed, "signature": signature}
    )
    return f"""
(() => {{
  const payload = {payload};
  const requiredModels = Array.from(new Set((payload.modelIds || []).filter((item) => typeof item === 'string' && item.length > 0)));
  const signature = String(payload.signature || '');
  const marker = 'codex-statsig-allowlist-protection/v4';
  const previousSignature = String(window.__codexStatsigAllowlistProtectionSignature || '');
  window.__codexStatsigAllowlistProtectionRequiredModels = requiredModels;
  window.__codexStatsigAllowlistProtectionSignature = signature;
  const result = {{
    schema: 'codex-desktop-model-runtime/statsig-allowlist-protection-result/v1',
    ok: true,
    installed: false,
    alreadyInstalled: false,
    repairedNow: false,
    changedNow: false,
    reloadRequested: false,
    requiredModels,
    before: [],
    after: [],
    added: [],
    removed: [],
    missingAfter: [],
    statsigKeys: [],
    clientsFound: 0,
    clientsWrapped: 0,
    valuesUpdatedEmitted: 0,
    liveAvailableModels: [],
    nativeSetItemRecovered: false,
    storageWrapperActive: false,
    reason: '',
  }};
  if (!requiredModels.length) {{
    result.ok = false;
    result.reason = 'no_catalog_models';
    return result;
  }}

  function activeRequiredModels() {{
    return Array.from(new Set(
      (window.__codexStatsigAllowlistProtectionRequiredModels || [])
        .filter((item) => typeof item === 'string' && item.length > 0)
    ));
  }}

  function mergeOuter(raw) {{
    if (typeof raw !== 'string' || raw.length === 0) return {{ raw, changed: false, touched: false }};
    let outer;
    try {{ outer = JSON.parse(raw); }} catch (error) {{ return {{ raw, changed: false, touched: false }}; }}
    if (!outer || typeof outer.data !== 'string') return {{ raw, changed: false, touched: false }};
    let data;
    try {{ data = JSON.parse(outer.data); }} catch (error) {{ return {{ raw, changed: false, touched: false }}; }}
    const cfg = data.dynamic_configs && data.dynamic_configs['{MODEL_CONFIG_ID}'];
    const value = cfg && cfg.value;
    if (!value || !Array.isArray(value.available_models)) return {{ raw, changed: false, touched: false }};
    const before = value.available_models.filter((item) => typeof item === 'string');
    const merged = Array.from(new Set([...before, ...activeRequiredModels()]));
    const added = merged.filter((item) => !before.includes(item));
    const removed = [];
    if (!added.length && !removed.length && before.length === merged.length) {{
      return {{ raw, changed: false, touched: true, before, after: merged, added, removed }};
    }}
    value.available_models = merged;
    outer.data = JSON.stringify(data);
    return {{ raw: JSON.stringify(outer), changed: true, touched: true, before, after: merged, added, removed }};
  }}

  window.__codexStatsigAllowlistMergeOuter = mergeOuter;

  function resolveNativeSetItem() {{
    if (typeof window.__codexStatsigNativeSetItem === 'function') {{
      result.nativeSetItemRecovered = true;
      return window.__codexStatsigNativeSetItem;
    }}
    let frame = null;
    try {{
      frame = document.createElement('iframe');
      frame.style.display = 'none';
      document.documentElement.appendChild(frame);
      const nativeSetItem = frame.contentWindow
        && frame.contentWindow.Storage
        && frame.contentWindow.Storage.prototype.setItem;
      if (typeof nativeSetItem === 'function') {{
        window.__codexStatsigNativeSetItem = nativeSetItem;
        result.nativeSetItemRecovered = true;
        return nativeSetItem;
      }}
    }} catch (error) {{
      result.nativeSetItemError = String(error);
    }} finally {{
      if (frame && typeof frame.remove === 'function') frame.remove();
    }}
    result.nativeSetItemRecovered = false;
    return Storage.prototype.setItem;
  }}

  const nativeSetItem = resolveNativeSetItem();

  function repairExisting() {{
    const keys = Object.keys(localStorage).filter((key) => key.startsWith('statsig.cached.evaluations'));
    result.statsigKeys = keys;
    let changed = false;
    for (const key of keys) {{
      try {{
        const merged = mergeOuter(localStorage.getItem(key));
        if (!merged.touched) continue;
        result.before = merged.before || [];
        result.after = merged.after || [];
        result.added = merged.added || [];
        result.removed = merged.removed || [];
        result.missingAfter = activeRequiredModels().filter((item) => !result.after.includes(item));
        if (merged.changed) {{
          Reflect.apply(nativeSetItem, localStorage, [key, merged.raw]);
          changed = true;
        }}
      }} catch (error) {{
        result.lastRepairError = String(error);
      }}
    }}
    return changed;
  }}

  function patchDynamicConfig(config) {{
    if (!config || typeof config !== 'object' || !config.value || typeof config.value !== 'object') return config;
    if (!Array.isArray(config.value.available_models)) return config;
    const before = config.value.available_models.filter((item) => typeof item === 'string');
    config.value.available_models = Array.from(new Set([...before, ...activeRequiredModels()]));
    return config;
  }}

  function activeStatsigClients() {{
    const root = window.__STATSIG__;
    if (!root || typeof root !== 'object') return [];
    const candidates = [root.firstInstance, ...Object.values(root.instances || {{}})];
    return Array.from(new Set(candidates.filter((item) => item && typeof item === 'object')));
  }}

  function wrapStatsigClient(client) {{
    if (!client || typeof client.getDynamicConfig !== 'function') return false;
    const activeWrapper = client.__codexAllowlistGetDynamicConfigWrapper;
    const activeWrapperVersion = String(client.__codexAllowlistGetDynamicConfigWrapperVersion || '');
    if (activeWrapper === client.getDynamicConfig && activeWrapperVersion === marker) return false;
    const original = activeWrapper === client.getDynamicConfig
      && typeof client.__codexAllowlistOriginalGetDynamicConfig === 'function'
      ? client.__codexAllowlistOriginalGetDynamicConfig
      : client.getDynamicConfig.bind(client);
    const wrapper = function(name, ...args) {{
      const config = original(name, ...args);
      return String(name) === '{MODEL_CONFIG_ID}' ? patchDynamicConfig(config) : config;
    }};
    wrapper.__codexAllowlistWrapperVersion = marker;
    client.__codexAllowlistOriginalGetDynamicConfig = original;
    client.__codexAllowlistGetDynamicConfigWrapper = wrapper;
    client.__codexAllowlistGetDynamicConfigWrapperVersion = marker;
    client.getDynamicConfig = wrapper;
    return true;
  }}

  function wrapActiveStatsigClients() {{
    const clients = activeStatsigClients();
    result.clientsFound = clients.length;
    let wrapped = 0;
    for (const client of clients) {{
      if (wrapStatsigClient(client)) wrapped += 1;
      try {{
        const config = client.getDynamicConfig && client.getDynamicConfig('{MODEL_CONFIG_ID}');
        patchDynamicConfig(config);
        if (config && config.value && Array.isArray(config.value.available_models)) {{
          result.liveAvailableModels = config.value.available_models.filter((item) => typeof item === 'string');
        }}
      }} catch (error) {{
        result.lastClientRepairError = String(error);
      }}
    }}
    result.clientsWrapped += wrapped;
    return clients;
  }}

  function emitValuesUpdated(clients) {{
    let emitted = 0;
    for (const client of clients) {{
      try {{
        if (typeof client.$emt === 'function') {{
          client.$emt({{ name: 'values_updated', status: client.loadingStatus, values: null }});
          emitted += 1;
        }}
      }} catch (error) {{
        result.lastEmitError = String(error);
      }}
    }}
    window.dispatchEvent(new CustomEvent('codex-statsig-values-updated', {{
      detail: {{ source: marker, requiredModels: activeRequiredModels() }},
    }}));
    window.dispatchEvent(new MessageEvent('message', {{ data: {{
      type: 'ipc-broadcast', method: 'query-cache-invalidate',
      params: {{ queryKey: ['models', 'list'] }}, sourceClientId: marker,
    }} }}));
    result.valuesUpdatedEmitted += emitted;
  }}

  const activeStorageWrapper = window.__codexStatsigAllowlistStorageWrapper;
  if (
    window.__codexStatsigAllowlistProtectionVersion === marker
    && typeof activeStorageWrapper === 'function'
    && Storage.prototype.setItem === activeStorageWrapper
  ) {{
    result.alreadyInstalled = true;
  }} else {{
    const storageWrapper = function(key, value) {{
      let nextValue = value;
      try {{
        if (this === window.localStorage && typeof key === 'string' && key.startsWith('statsig.cached.evaluations')) {{
          const merged = mergeOuter(String(value));
          nextValue = merged.raw;
          const reasoningMerge = window.__codexReasoningMergeStatsigOuter;
          if (typeof reasoningMerge === 'function') nextValue = reasoningMerge(nextValue).raw;
        }}
      }} catch (error) {{
        console.warn('[codex-statsig-allowlist-protection] merge failed', error);
      }}
      return Reflect.apply(nativeSetItem, this, [key, nextValue]);
    }};
    window.__codexStatsigAllowlistStorageWrapper = storageWrapper;
    Storage.prototype.setItem = storageWrapper;
    window.__codexStatsigAllowlistProtectionVersion = marker;
    window.__codexStatsigAllowlistProtectionInstalledAt = Date.now();
    result.installed = true;
  }}
  result.storageWrapperActive = Storage.prototype.setItem === window.__codexStatsigAllowlistStorageWrapper;
  result.updated = previousSignature !== signature;
  result.previousSignature = previousSignature;
  result.signature = signature;
  const changedNow = repairExisting();
  const clients = wrapActiveStatsigClients();
  if (window.__codexStatsigAllowlistProtectionTimer) {{
    clearInterval(window.__codexStatsigAllowlistProtectionTimer);
  }}
  window.__codexStatsigAllowlistProtectionTimer = setInterval(() => {{
    try {{ wrapActiveStatsigClients(); }} catch (error) {{
      window.__codexStatsigAllowlistProtectionLastTimerError = String(error);
    }}
  }}, 2000);
  if (typeof window.__codexStatsigAllowlistProtectionTimer.unref === 'function') {{
    window.__codexStatsigAllowlistProtectionTimer.unref();
  }}
  emitValuesUpdated(clients);
  result.changedNow = changedNow;
  result.repairedNow = changedNow || result.alreadyInstalled || result.installed;
  if (changedNow && payload.reloadIfChanged) {{
    result.reloadRequested = true;
    setTimeout(() => location.reload(), 50);
  }}
  return result;
}})()
"""


def _statsig_allowlist_live_probe_expression() -> str:
    """Read the active Statsig model config and compatibility-wrapper identity."""
    return f"""
(() => {{
  const result = {{
    schema: 'codex-desktop-model-runtime/statsig-live-probe/v1',
    ok: false,
    version: String(window.__codexStatsigAllowlistProtectionVersion || ''),
    requiredModels: window.__codexStatsigAllowlistProtectionRequiredModels || [],
    clientsFound: 0,
    clientsProtected: 0,
    availableModels: [],
    storageWrapperActive: false,
    nativeSetItemRecovered: typeof window.__codexStatsigNativeSetItem === 'function',
    reason: '',
  }};
  const root = window.__STATSIG__;
  const clients = root && typeof root === 'object'
    ? Array.from(new Set([root.firstInstance, ...Object.values(root.instances || {{}})]
        .filter((item) => item && typeof item.getDynamicConfig === 'function')))
    : [];
  result.clientsFound = clients.length;
  result.clientsProtected = clients.filter((client) =>
    client.__codexAllowlistGetDynamicConfigWrapper === client.getDynamicConfig
      && String(client.__codexAllowlistGetDynamicConfigWrapperVersion || '') === result.version
  ).length;
  result.storageWrapperActive = typeof window.__codexStatsigAllowlistStorageWrapper === 'function'
    && Storage.prototype.setItem === window.__codexStatsigAllowlistStorageWrapper;
  for (const client of clients) {{
    try {{
      const config = client.getDynamicConfig('{MODEL_CONFIG_ID}');
      const available = config && config.value && config.value.available_models;
      if (!Array.isArray(available)) continue;
      result.availableModels = available.filter((item) => typeof item === 'string');
      result.ok = true;
      return result;
    }} catch (error) {{
      result.lastError = String(error);
    }}
  }}
  result.reason = clients.length ? 'statsig_model_config_not_found' : 'statsig_client_unavailable';
  return result;
}})()
"""


def statsig_allowlist_protection_state(
    catalog_path: Path | None,
    *,
    apply: bool = False,
    reload_if_changed: bool = False,
    wait_seconds: float = 0.0,
) -> dict[str, Any]:
    """Protect custom-provider model allowlist from Statsig refresh shrinkage.

    This keeps Statsig dynamic config enabled but prevents network refreshes from
    dropping models declared by the active provider catalog. Native entries are
    preserved, so the compatibility layer only adds missing provider models. It
    is intentionally runtime-scoped and provider/catalog-driven: no Codex app
    package files, config.toml, auth, or non-model Statsig fields are changed.
    """
    model_ids = _catalog_visible_model_ids(catalog_path)
    state: dict[str, Any] = {
        "schema": "codex-desktop-model-runtime/statsig-allowlist-protection/v1",
        "ok": True,
        "catalog_path": str(catalog_path) if catalog_path else "",
        "catalog_models": model_ids,
        "apply": apply,
        "reload_if_changed": reload_if_changed,
        "wait_seconds": wait_seconds,
        "cdp_port": None,
        "page_count": 0,
        "protection": {},
        "sync": {},
        "reason": "",
    }
    if not model_ids:
        return {**state, "ok": False, "reason": "no_catalog_models"}
    if not apply:
        return statsig_allowlist_sync_state(
            catalog_path,
            apply=False,
            reload_if_changed=reload_if_changed,
            wait_seconds=wait_seconds,
        ) | {"schema": state["schema"], "protection_mode": "plan_only_exact_provider_models"}

    port, ws_url, pages, reason = _wait_for_codex_page(wait_seconds)
    state["cdp_port"] = port
    state["page_count"] = len(pages)
    if not ws_url:
        return {**state, "ok": False, "reason": reason or "codex_desktop_cdp_unavailable"}
    client: _CdpClient | None = None
    try:
        client = _CdpClient(ws_url)
        client.call("Runtime.enable")
        client.call("Page.enable")
        source = _statsig_allowlist_protect_expression(model_ids, reload_if_changed=reload_if_changed)
        client.call("Page.addScriptToEvaluateOnNewDocument", {"source": source})
        protection = client.evaluate(source)
    except Exception as exc:
        return {**state, "ok": False, "reason": "statsig_allowlist_protection_apply_failed", "error": repr(exc)}
    finally:
        if client is not None:
            client.close()
    protection = protection if isinstance(protection, dict) else {"raw": protection}
    missing_after = protection.get("missingAfter") if isinstance(protection.get("missingAfter"), list) else []
    return {
        **state,
        "ok": bool(protection.get("ok", True)) and not missing_after,
        "protection": protection,
        "live_available_models": protection.get("liveAvailableModels", []),
        "reason": str(protection.get("reason") or ""),
    }


def _reasoning_hot_refresh_expression(
    requested_efforts: list[str],
    *,
    apply: bool,
    reload_if_changed: bool,
) -> str:
    gate_overrides = {
        REASONING_FEATURE_GATES[effort]: True
        for effort in requested_efforts
        if effort in REASONING_FEATURE_GATES
    }
    signature = hashlib.sha256(
        json.dumps(
            {"efforts": requested_efforts, "gates": gate_overrides},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    payload = json.dumps(
        {
            "requestedEfforts": requested_efforts,
            "gateOverrides": gate_overrides,
            "apply": apply,
            "reloadIfChanged": reload_if_changed,
            "signature": signature,
            "effortOrder": list(DESKTOP_REASONING_EFFORT_ORDER),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    template = r"""
(async () => {
  const payload = __PAYLOAD__;
  const marker = 'codex-reasoning-capability-bridge/v1';
  const settingKey = 'enabled-reasoning-efforts';
  const requestedEfforts = Array.from(new Set(payload.requestedEfforts || []));
  const gateOverrides = Object.assign({}, payload.gateOverrides || {});
  const previousSignature = String(window.__codexReasoningCapabilitySignature || '');
  if (payload.apply) {
    window.__codexReasoningCapabilityEfforts = requestedEfforts;
    window.__codexReasoningCapabilityGateOverrides = gateOverrides;
    window.__codexReasoningCapabilitySignature = String(payload.signature || '');
  }

  const result = {
    schema: 'codex-desktop-model-runtime/reasoning-hot-refresh-result/v1',
    ok: true,
    installed: false,
    alreadyInstalled: false,
    updated: previousSignature !== String(payload.signature || ''),
    requestedEfforts,
    gateOverrides,
    settingBefore: [],
    settingAfter: [],
    settingChanged: false,
    settingApplied: false,
    gateCacheChanged: false,
    statsigClientsProtected: 0,
    statsigEventEmitted: false,
    modelQueryInvalidated: false,
    reloadRequested: false,
    reason: '',
  };

  function appPost(method, body) {
    return new Promise((resolve) => {
      const requestId = crypto.randomUUID();
      const timer = setTimeout(() => { cleanup(); resolve({timeout: true}); }, 5000);
      function cleanup() { clearTimeout(timer); window.removeEventListener('message', onMessage); }
      function onMessage(event) {
        const data = event.data;
        if (!data || data.type !== 'fetch-response' || data.requestId !== requestId) return;
        cleanup();
        resolve(data);
      }
      window.addEventListener('message', onMessage);
      window.electronBridge.sendMessageFromView({
        type: 'fetch', requestId, method: 'POST', url: 'vscode://codex/' + method,
        body: body == null ? undefined : JSON.stringify(body),
      }).catch((error) => { cleanup(); resolve({bridgeError: String(error)}); });
    });
  }

  function responseBody(response) {
    if (!response || response.responseType !== 'success' || typeof response.bodyJsonString !== 'string') return null;
    try { return JSON.parse(response.bodyJsonString); } catch { return null; }
  }

  function activeGateOverrides() {
    return Object.assign({}, window.__codexReasoningCapabilityGateOverrides || {});
  }

  function mergeStatsigOuter(raw) {
    if (typeof raw !== 'string' || raw.length === 0) return {raw, changed: false, touched: false};
    let outer;
    try { outer = JSON.parse(raw); } catch { return {raw, changed: false, touched: false}; }
    if (!outer || typeof outer.data !== 'string') return {raw, changed: false, touched: false};
    let data;
    try { data = JSON.parse(outer.data); } catch { return {raw, changed: false, touched: false}; }
    const overrides = activeGateOverrides();
    const gateIds = Object.keys(overrides).filter((key) => overrides[key] === true);
    if (!gateIds.length) return {raw, changed: false, touched: false};
    if (!data.feature_gates || typeof data.feature_gates !== 'object') data.feature_gates = {};
    let changed = false;
    const before = {};
    const after = {};
    for (const gateId of gateIds) {
      const current = data.feature_gates[gateId];
      before[gateId] = current && typeof current === 'object' ? current.value : undefined;
      if (!current || typeof current !== 'object') {
        data.feature_gates[gateId] = {
          name: gateId, rule_id: 'codex-provider-catalog', secondary_exposures: [],
          version: 0, id_type: 'userID', value: true,
        };
        changed = true;
      } else if (current.value !== true) {
        data.feature_gates[gateId] = Object.assign({}, current, {value: true});
        changed = true;
      }
      after[gateId] = true;
    }
    if (changed) outer.data = JSON.stringify(data);
    return {raw: changed ? JSON.stringify(outer) : raw, changed, touched: true, before, after};
  }

  window.__codexReasoningMergeStatsigOuter = mergeStatsigOuter;

  function repairGateCache() {
    let changed = false;
    for (const key of Object.keys(localStorage)) {
      if (!key.startsWith('statsig.cached.evaluations')) continue;
      const merged = mergeStatsigOuter(localStorage.getItem(key));
      if (merged.changed) {
        localStorage.setItem(key, merged.raw);
        changed = true;
      }
    }
    return changed;
  }

  function wrapStatsigClient(client, emitUpdate) {
    if (!client || typeof client.checkGate !== 'function') return false;
    if (!client.__codexReasoningOriginalCheckGate) {
      client.__codexReasoningOriginalCheckGate = client.checkGate.bind(client);
      client.checkGate = function(gateName) {
        const overrides = activeGateOverrides();
        if (overrides[String(gateName)] === true) return true;
        return client.__codexReasoningOriginalCheckGate(gateName);
      };
    }
    if (emitUpdate && typeof client.$emt === 'function') {
      try {
        client.$emt({name: 'values_updated', status: client.loadingStatus, values: null});
        result.statsigEventEmitted = true;
      } catch (error) {
        result.statsigEventError = String(error);
      }
    }
    return true;
  }

  function protectStatsigClients(emitUpdate) {
    const statsig = window.__STATSIG__;
    if (!statsig) return 0;
    const clients = [];
    if (statsig.firstInstance) clients.push(statsig.firstInstance);
    if (typeof statsig.instance === 'function') {
      try { const current = statsig.instance(); if (current) clients.push(current); } catch {}
    }
    if (statsig.instances && typeof statsig.instances === 'object') {
      clients.push(...Object.values(statsig.instances));
    }
    let count = 0;
    for (const client of Array.from(new Set(clients))) {
      if (wrapStatsigClient(client, emitUpdate)) count += 1;
    }
    return count;
  }

  if (payload.apply) {
    if (window.__codexReasoningCapabilityVersion === marker) {
      result.alreadyInstalled = true;
    } else {
      const allowlistWrapperOwnsStorage = typeof window.__codexStatsigAllowlistStorageWrapper === 'function'
        && Storage.prototype.setItem === window.__codexStatsigAllowlistStorageWrapper;
      if (!allowlistWrapperOwnsStorage) {
        const originalSetItem = Storage.prototype.setItem;
        Storage.prototype.setItem = function(key, value) {
          let nextValue = value;
          try {
            if (this === window.localStorage && typeof key === 'string' && key.startsWith('statsig.cached.evaluations')) {
              nextValue = mergeStatsigOuter(String(value)).raw;
            }
          } catch (error) {
            console.warn('[codex-reasoning-capability-bridge] Statsig merge failed', error);
          }
          return originalSetItem.call(this, key, nextValue);
        };
      }
      window.__codexReasoningCapabilityVersion = marker;
      window.__codexReasoningCapabilityInstalledAt = Date.now();
      result.installed = true;
    }

    result.gateCacheChanged = repairGateCache();
    result.statsigClientsProtected = protectStatsigClients(result.updated || result.gateCacheChanged);
    if (!window.__codexReasoningCapabilityPoller) {
      let remaining = 100;
      window.__codexReasoningCapabilityPoller = setInterval(() => {
        protectStatsigClients(false);
        remaining -= 1;
        if (remaining <= 0) {
          clearInterval(window.__codexReasoningCapabilityPoller);
          window.__codexReasoningCapabilityPoller = null;
        }
      }, 100);
    }
  }

  const getSetting = await appPost('get-setting', {key: settingKey});
  const settingBody = responseBody(getSetting);
  const current = Array.isArray(settingBody && settingBody.value)
    ? settingBody.value.filter((item) => typeof item === 'string')
    : [];
  const valid = new Set(payload.effortOrder || []);
  const desiredSet = new Set(current.filter((item) => valid.has(item)));
  requestedEfforts.forEach((item) => { if (valid.has(item)) desiredSet.add(item); });
  const desired = (payload.effortOrder || []).filter((item) => desiredSet.has(item));
  result.settingBefore = current;
  result.settingAfter = desired;
  result.settingChanged = current.length !== desired.length || current.some((item, index) => item !== desired[index]);
  if (payload.apply && result.settingChanged) {
    const setSetting = await appPost('set-setting', {key: settingKey, value: desired});
    result.settingResponse = setSetting;
    result.settingApplied = Boolean(setSetting && setSetting.responseType === 'success');
    if (!result.settingApplied) {
      result.ok = false;
      result.reason = 'reasoning_setting_apply_failed';
    }
  }

  if (payload.apply) {
    window.dispatchEvent(new MessageEvent('message', {data: {
      type: 'ipc-broadcast', method: 'query-cache-invalidate',
      params: {queryKey: ['models', 'list']}, sourceClientId: 'codex-reasoning-capability-bridge',
    }}));
    result.modelQueryInvalidated = true;
  }
  if (payload.apply && payload.reloadIfChanged && result.settingChanged && result.settingApplied) {
    result.reloadRequested = true;
    setTimeout(() => location.reload(), 100);
  }
  return result;
})()
"""
    return template.replace("__PAYLOAD__", payload)


def reasoning_hot_refresh_state(
    catalog_path: Path | None,
    *,
    apply: bool = False,
    reload_if_changed: bool = False,
    wait_seconds: float = 0.0,
) -> dict[str, Any]:
    """Project provider reasoning capabilities into the active Desktop runtime.

    The bridge preserves native settings, adds only catalog-declared effort
    levels, and overrides only feature gates explicitly required by the active
    Desktop build. Other Statsig gates, configs, auth state, and app files are
    untouched.
    """
    requested_efforts = catalog_requested_reasoning_efforts(catalog_path)
    declared_efforts = catalog_declared_reasoning_efforts(catalog_path)
    unsupported_efforts = sorted(declared_efforts - set(DESKTOP_REASONING_EFFORT_ORDER))
    gate_overrides = {
        REASONING_FEATURE_GATES[effort]: True
        for effort in requested_efforts
        if effort in REASONING_FEATURE_GATES
    }
    state: dict[str, Any] = {
        "schema": "codex-desktop-model-runtime/reasoning-hot-refresh/v1",
        "ok": True,
        "catalog_path": str(catalog_path) if catalog_path else "",
        "requested_efforts": requested_efforts,
        "unsupported_efforts": unsupported_efforts,
        "gate_overrides": gate_overrides,
        "apply": apply,
        "reload_if_changed": reload_if_changed,
        "cdp_port": None,
        "page_count": 0,
        "result": {},
        "reason": "",
    }
    if not requested_efforts:
        return {**state, "ok": False, "reason": "no_catalog_reasoning_efforts"}
    port, ws_url, pages, reason = _wait_for_codex_page(wait_seconds)
    state["cdp_port"] = port
    state["page_count"] = len(pages)
    if not ws_url:
        return {**state, "ok": False, "reason": reason or "codex_desktop_cdp_unavailable"}
    client: _CdpClient | None = None
    try:
        client = _CdpClient(ws_url)
        client.call("Runtime.enable")
        client.call("Page.enable")
        source = _reasoning_hot_refresh_expression(
            requested_efforts,
            apply=apply,
            reload_if_changed=reload_if_changed,
        )
        if apply:
            client.call("Page.addScriptToEvaluateOnNewDocument", {"source": source})
        result = client.evaluate(source)
    except Exception as exc:
        return {**state, "ok": False, "reason": "reasoning_hot_refresh_failed", "error": repr(exc)}
    finally:
        if client is not None:
            client.close()
    result = result if isinstance(result, dict) else {"raw": result}
    return {
        **state,
        "ok": bool(result.get("ok", True)),
        "result": result,
        "reason": str(result.get("reason") or ""),
    }


def _parse_delay_list(raw: str) -> list[float]:
    delays: list[float] = []
    for item in raw.split(","):
        value = item.strip()
        if not value:
            continue
        delay = float(value)
        if delay < 0:
            raise ValueError("delay values must be non-negative")
        delays.append(delay)
    return sorted(set(delays))


def _append_jsonl(path: Path | None, entry: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def statsig_allowlist_stabilize_state(
    catalog_path: Path | None,
    *,
    delays: list[float],
    wait_seconds: float,
    reload_if_changed: bool,
    log_path: Path | None = None,
    stable_required: int = 2,
) -> dict[str, Any]:
    """Run finite post-start allowlist sync passes until the allowlist is stable.

    This handles Desktop builds that refresh Statsig after the first app page is
    available and can overwrite an earlier localStorage compatibility sync.
    """
    start = time.monotonic()
    state: dict[str, Any] = {
        "schema": "codex-desktop-model-runtime/statsig-allowlist-stabilize/v1",
        "ok": True,
        "catalog_path": str(catalog_path) if catalog_path else "",
        "delays": delays,
        "wait_seconds": wait_seconds,
        "reload_if_changed": reload_if_changed,
        "stable_required": stable_required,
        "log_path": str(log_path) if log_path else "",
        "passes": [],
        "stable_count": 0,
        "reason": "",
    }
    if not delays:
        delays = [0.0]
        state["delays"] = delays
    stable_count = 0
    last_sync: dict[str, Any] = {}
    for index, target_delay in enumerate(delays, start=1):
        remaining = target_delay - (time.monotonic() - start)
        if remaining > 0:
            time.sleep(remaining)
        sync = statsig_allowlist_sync_state(
            catalog_path,
            apply=True,
            reload_if_changed=reload_if_changed,
            wait_seconds=wait_seconds,
        )
        result = sync.get("result") if isinstance(sync.get("result"), dict) else {}
        changed = bool(result.get("changed"))
        ok = bool(sync.get("ok"))
        missing_after = result.get("missingAfter") if isinstance(result.get("missingAfter"), list) else []
        stable = ok and not changed and not missing_after
        stable_count = stable_count + 1 if stable else 0
        pass_entry = {
            "schema": "codex-desktop-model-runtime/statsig-allowlist-stabilize-pass/v1",
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "pass_index": index,
            "target_delay_seconds": target_delay,
            "elapsed_seconds": round(time.monotonic() - start, 3),
            "ok": ok,
            "changed": changed,
            "applied": bool(result.get("applied")),
            "stable": stable,
            "stable_count": stable_count,
            "reason": str(sync.get("reason") or ""),
            "before": result.get("before"),
            "after": result.get("after"),
            "added": result.get("added"),
            "removed": result.get("removed"),
            "missing_after": missing_after,
            "attempts": sync.get("attempts"),
        }
        state["passes"].append(pass_entry)
        _append_jsonl(log_path, pass_entry)
        last_sync = sync
        if stable_count >= max(1, stable_required):
            state["reason"] = "stable_required_reached"
            break
    state["stable_count"] = stable_count
    state["last_sync"] = last_sync
    state["ok"] = bool(last_sync.get("ok", True)) if last_sync else False
    if not state["reason"]:
        state["reason"] = "passes_exhausted"
    return state


def desktop_runtime_state(expected_models: list[str]) -> dict[str, Any]:
    """Return the running Desktop model-picker state visible through CDP."""
    state: dict[str, Any] = {
        "schema": "codex-desktop-model-runtime/state/v1",
        "ok": True,
        "skipped": False,
        "cdp_port": None,
        "expected_models": expected_models,
        "list_models_for_host_ok": None,
        "list_models_for_host_error": "",
        "statsig_available_models": [],
        "statsig_use_hidden_models": None,
        "statsig_default_model": "",
        "statsig_missing_expected_models": [],
        "model_picker_view": "",
        "model_picker_view_sync_signature": "",
        "model_picker_persistence_route": "",
        "model_picker_persistence_confirmed": False,
        "runtime_enabled_reasoning_efforts": sorted(DEFAULT_ENABLED_REASONING_EFFORTS),
        "enabled_reasoning_efforts": sorted(DEFAULT_ENABLED_REASONING_EFFORTS),
        "reason": "",
    }
    port, ws_url, pages, reason = _find_codex_page()
    state["cdp_port"] = port
    state["page_count"] = len(pages)
    if not ws_url:
        return {**state, "ok": True, "skipped": True, "reason": reason or "codex_desktop_cdp_unavailable"}
    client: _CdpClient | None = None
    picker_runtime: dict[str, Any] = {}
    try:
        client = _CdpClient(ws_url)
        client.call("Runtime.enable")
        runtime = client.evaluate(_app_post_expression())
        picker_value = client.evaluate(
            _model_picker_view_sync_expression(
                "runtime-probe",
                apply=False,
                require_advanced=True,
            )
        )
        picker_runtime = picker_value if isinstance(picker_value, dict) else {}
    except Exception as exc:
        return {**state, "ok": False, "reason": "codex_desktop_runtime_probe_failed", "error": repr(exc)}
    finally:
        if client is not None:
            client.close()

    runtime = runtime if isinstance(runtime, dict) else {}
    list_models = runtime.get("listModelsForHost") if isinstance(runtime.get("listModelsForHost"), dict) else {}
    list_ok = list_models.get("responseType") == "success" and int(list_models.get("status") or 0) == 200
    state["list_models_for_host_ok"] = bool(list_ok)
    if not list_ok:
        state["list_models_for_host_error"] = str(list_models.get("error") or list_models.get("bridgeError") or "")

    statsig_value = _statsig_value(runtime.get("statsig"))
    available = statsig_value.get("available_models")
    if isinstance(available, list):
        state["statsig_available_models"] = [str(item) for item in available if isinstance(item, str)]
    use_hidden = statsig_value.get("use_hidden_models")
    if isinstance(use_hidden, bool):
        state["statsig_use_hidden_models"] = use_hidden
    default_model = statsig_value.get("default_model")
    if isinstance(default_model, str):
        state["statsig_default_model"] = default_model
    if state["statsig_use_hidden_models"] is True and state["statsig_available_models"]:
        allowed = set(state["statsig_available_models"])
        state["statsig_missing_expected_models"] = [model for model in expected_models if model not in allowed]
    state["model_picker_view"] = str(
        picker_runtime.get("viewAfter") or picker_runtime.get("viewBefore") or runtime.get("modelPickerView") or ""
    )
    state["model_picker_view_sync_signature"] = str(
        picker_runtime.get("signatureAfter")
        or picker_runtime.get("previousSignature")
        or runtime.get("modelPickerViewSyncSignature")
        or ""
    )
    state["model_picker_persistence_route"] = str(picker_runtime.get("persistenceRoute") or "")
    state["model_picker_persistence_confirmed"] = bool(picker_runtime.get("persistenceConfirmed"))

    enabled = _settings_enabled_efforts(runtime.get("settings"))
    state["runtime_enabled_reasoning_efforts"] = sorted(enabled)
    state["enabled_reasoning_efforts"] = sorted(enabled)

    advisory_findings: list[str] = []
    if not list_ok:
        advisory_findings.append("electron_list_models_for_host_handler_unavailable")
    if state["statsig_missing_expected_models"]:
        advisory_findings.append("desktop_statsig_model_whitelist_missing_expected_models")
    # CC Switch can expose third-party model catalogs through the Codex
    # app-server/provider model list even when Desktop's cached Statsig model
    # allowlist does not include those models. Keep this evidence visible for
    # debugging, but do not turn it into a hard startup/config failure.
    return {**state, "ok": True, "advisory_findings": advisory_findings, "reason": ";".join(advisory_findings)}


def _catalog_model_id(item: dict[str, Any]) -> str:
    for key in ("slug", "model", "id", "name"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def reasoning_efforts_for_key(item: dict[str, Any], key: str) -> set[str]:
    efforts: set[str] = set()
    raw = item.get(key)
    if not isinstance(raw, list):
        return efforts
    for entry in raw:
        if isinstance(entry, str) and entry.strip():
            efforts.add(entry.strip())
        elif isinstance(entry, dict):
            for effort_key in ("effort", "reasoningEffort", "value", "id"):
                value = entry.get(effort_key)
                if isinstance(value, str) and value.strip():
                    efforts.add(value.strip())
                    break
    return efforts


def _catalog_efforts(item: dict[str, Any]) -> set[str]:
    return reasoning_efforts_for_key(item, "supported_reasoning_levels") | reasoning_efforts_for_key(
        item, "supportedReasoningEfforts"
    )


def desktop_reasoning_entries(levels: tuple[str, ...] = SAFE_CATALOG_REASONING_LEVELS) -> list[dict[str, str]]:
    return [
        {"reasoningEffort": level, "description": REASONING_DESCRIPTIONS.get(level, level)}
        for level in levels
    ]


def _desktop_reasoning_entries(levels: tuple[str, ...] = SAFE_CATALOG_REASONING_LEVELS) -> list[dict[str, str]]:
    return desktop_reasoning_entries(levels)


def _desktop_compat_issues(item: dict[str, Any], model_id: str, required_efforts: set[str]) -> list[str]:
    issues: list[str] = []
    if not model_id:
        issues.append("missing_model_id")
        return issues
    if item.get("model") != model_id:
        issues.append("missing_desktop_model_field")
    if not isinstance(item.get("displayName"), str) or not item.get("displayName"):
        issues.append("missing_desktop_display_name")
    if item.get("hidden") is not False:
        issues.append("hidden_not_false")
    if "isDefault" not in item:
        issues.append("missing_is_default")
    if not isinstance(item.get("defaultReasoningEffort"), str) or not item.get("defaultReasoningEffort"):
        issues.append("missing_default_reasoning_effort")
    raw_desktop_efforts = item.get("supportedReasoningEfforts")
    if not isinstance(raw_desktop_efforts, list) or not raw_desktop_efforts:
        issues.append("missing_supported_reasoning_efforts")
    catalog_efforts = reasoning_efforts_for_key(item, "supported_reasoning_levels")
    desktop_efforts = reasoning_efforts_for_key(item, "supportedReasoningEfforts")
    if not catalog_efforts:
        issues.append("missing_supported_reasoning_levels")
    if required_efforts - catalog_efforts:
        issues.append("catalog_reasoning_levels_missing_runtime_efforts")
    if required_efforts - desktop_efforts:
        issues.append("desktop_reasoning_efforts_missing_runtime_efforts")
    return issues


def catalog_reasoning_state(catalog_path: Path | None, enabled_efforts: set[str] | None = None) -> dict[str, Any]:
    enabled = set(DEFAULT_ENABLED_REASONING_EFFORTS) if enabled_efforts is None else set(enabled_efforts)
    state: dict[str, Any] = {
        "schema": "codex-desktop-model-runtime/catalog-reasoning/v1",
        "ok": True,
        "skipped": False,
        "path": str(catalog_path) if catalog_path else "",
        "runtime_enabled_reasoning_efforts": sorted(enabled),
        "enabled_reasoning_efforts": sorted(enabled),
        "catalog_supported_reasoning_efforts": [],
        "selectable_reasoning_efforts": [],
        "model_count": 0,
        "models": [],
        "models_with_single_enabled_reasoning_effort": [],
        "models_with_desktop_compat_issues": [],
        "reason": "",
    }
    if not catalog_path:
        return {**state, "ok": True, "skipped": True, "reason": "catalog_path_missing"}
    if not catalog_path.exists():
        return {**state, "ok": False, "reason": "catalog_missing"}
    try:
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {**state, "ok": False, "reason": "catalog_parse_failed", "error": repr(exc)}
    raw_models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(raw_models, list):
        return {**state, "ok": False, "reason": "catalog_models_missing"}
    model_summaries: list[dict[str, Any]] = []
    narrow: list[dict[str, Any]] = []
    compat_issues: list[dict[str, Any]] = []
    catalog_supported: set[str] = set()
    selectable: set[str] = set()
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        model_id = _catalog_model_id(item)
        efforts = _catalog_efforts(item)
        enabled_visible = sorted(efforts & enabled)
        catalog_supported.update(efforts)
        selectable.update(efforts & enabled)
        desktop_issues = _desktop_compat_issues(item, model_id, enabled)
        summary = {
            "model": model_id,
            "catalog_supported_reasoning_efforts": sorted(efforts),
            "selectable_reasoning_efforts": enabled_visible,
            "reasoning_efforts": sorted(efforts),
            "enabled_visible_reasoning_efforts": enabled_visible,
            "desktop_compat_issues": desktop_issues,
        }
        model_summaries.append(summary)
        if model_id and len(enabled_visible) <= 1:
            narrow.append(summary)
        if model_id and desktop_issues:
            compat_issues.append(summary)
    reasons: list[str] = []
    if narrow:
        reasons.append("catalog_reasoning_levels_narrow_for_desktop_picker")
    if compat_issues:
        reasons.append("catalog_desktop_compat_fields_missing")
    reason = ";".join(reasons)
    return {
        **state,
        "ok": not narrow and not compat_issues,
        "catalog_supported_reasoning_efforts": sorted(catalog_supported),
        "selectable_reasoning_efforts": sorted(selectable),
        "model_count": len(model_summaries),
        "models": model_summaries,
        "models_with_single_enabled_reasoning_effort": narrow,
        "models_with_desktop_compat_issues": compat_issues,
        "reason": reason,
    }


def catalog_reasoning_entries(levels: tuple[str, ...] = SAFE_CATALOG_REASONING_LEVELS) -> list[dict[str, str]]:
    return [
        {"description": REASONING_DESCRIPTIONS.get(level, level), "effort": level}
        for level in levels
    ]


def _reasoning_entries(levels: tuple[str, ...] = SAFE_CATALOG_REASONING_LEVELS) -> list[dict[str, str]]:
    return catalog_reasoning_entries(levels)


def catalog_reasoning_repair_plan(catalog_path: Path | None) -> dict[str, Any]:
    state = catalog_reasoning_state(catalog_path)
    planned_models = sorted(
        {
            item.get("model")
            for group_key in ("models_with_single_enabled_reasoning_effort", "models_with_desktop_compat_issues")
            for item in state.get(group_key, [])
            if isinstance(item, dict) and item.get("model")
        }
    )
    return {
        "schema": "codex-desktop-model-runtime/catalog-reasoning-repair-plan/v1",
        "ok": bool(state.get("ok")) or bool(planned_models),
        "dry_run": True,
        "would_apply": bool(planned_models),
        "catalog_path": str(catalog_path) if catalog_path else "",
        "planned_models": planned_models,
        "target_reasoning_levels": list(SAFE_CATALOG_REASONING_LEVELS),
        "before": state,
        "apply_command": "python _bridge\\codex_desktop_model_runtime.py catalog-reasoning-apply --catalog-path <path>",
        "policy": "only expands reasoning effort fields and Desktop-compatible fields for existing catalog entries; does not add models, change provider, edit config.toml, mutate Desktop cache, or patch app.asar",
    }


def apply_catalog_reasoning_repair(catalog_path: Path | None) -> dict[str, Any]:
    plan = catalog_reasoning_repair_plan(catalog_path)
    if not catalog_path or not catalog_path.exists():
        return {**plan, "applied": False, "ok": False, "reason": "catalog_missing"}
    if not plan.get("would_apply"):
        return {**plan, "applied": False, "changed": False}
    try:
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {**plan, "applied": False, "ok": False, "reason": "catalog_parse_failed", "error": repr(exc)}
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        return {**plan, "applied": False, "ok": False, "reason": "catalog_models_missing"}

    planned = set(plan.get("planned_models") or [])
    touched: list[str] = []
    target_entries = _reasoning_entries()
    target_desktop_entries = _desktop_reasoning_entries()
    for item in models:
        if not isinstance(item, dict):
            continue
        model_id = _catalog_model_id(item)
        if model_id not in planned:
            continue
        item["supported_reasoning_levels"] = target_entries
        default_effort = item.get("default_reasoning_level")
        if not isinstance(default_effort, str) or default_effort not in SAFE_CATALOG_REASONING_LEVELS:
            default_effort = "high"
            item["default_reasoning_level"] = default_effort
        item["model"] = model_id
        if not isinstance(item.get("displayName"), str) or not item.get("displayName"):
            item["displayName"] = item.get("display_name") or model_id
        item["hidden"] = False
        if "isDefault" not in item:
            item["isDefault"] = False
        item["defaultReasoningEffort"] = default_effort
        item["supportedReasoningEfforts"] = target_desktop_entries
        touched.append(model_id)
    catalog_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    after = catalog_reasoning_state(catalog_path)
    return {
        **plan,
        "dry_run": False,
        "applied": True,
        "changed": bool(touched),
        "touched_models": touched,
        "touched_count": len(touched),
        "after": after,
    }


def combined_state(expected_models: list[str], catalog_path: Path | None) -> dict[str, Any]:
    runtime = desktop_runtime_state(expected_models)
    enabled = set(runtime.get("enabled_reasoning_efforts") or DEFAULT_ENABLED_REASONING_EFFORTS)
    reasoning = catalog_reasoning_state(catalog_path, enabled)
    runtime_unhealthy = bool(runtime) and not bool(runtime.get("ok")) and not bool(runtime.get("skipped"))
    reasoning_unhealthy = bool(reasoning) and not bool(reasoning.get("ok")) and not bool(reasoning.get("skipped"))
    return {
        "schema": "codex-desktop-model-runtime/combined-state/v1",
        "ok": not runtime_unhealthy,
        "runtime_unhealthy": runtime_unhealthy,
        "catalog_reasoning_unhealthy": reasoning_unhealthy,
        "catalog_reasoning_advisory": reasoning_unhealthy and not runtime_unhealthy,
        "runtime": runtime,
        "catalog_reasoning": reasoning,
        "policy": "runtime health is authoritative; CC Switch source-catalog reasoning/Desktop schema findings are advisory when the runtime adapter supplies compatible fields; do not mutate Codex Desktop cache, app.asar, config.toml, or CC Switch catalog from this probe",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Codex Desktop model-picker runtime diagnostics")
    parser.add_argument(
        "action",
        choices=[
            "runtime",
            "page-reload",
            "catalog-reasoning-plan",
            "catalog-reasoning-apply",
            "statsig-allowlist-plan",
            "statsig-allowlist-apply",
            "statsig-allowlist-protect",
            "statsig-allowlist-stabilize",
            "reasoning-hot-refresh-plan",
            "reasoning-hot-refresh-apply",
            "appserver-model-shim-apply",
            "model-list-bridge-shim-apply",
        ],
    )
    parser.add_argument("--catalog-path", default="", help="Path to cc-switch-model-catalog.json")
    parser.add_argument("--expected-model", action="append", default=[], help="Expected model id, repeatable")
    parser.add_argument("--reload-if-changed", action="store_true", help="Reload Desktop UI after an applied allowlist sync")
    parser.add_argument("--wait-seconds", type=float, default=0.0, help="Wait for Desktop page and Statsig config readiness")
    parser.add_argument("--startup-delay-seconds", type=float, default=0.0, help="Sleep before running the selected action")
    parser.add_argument("--stabilize-delays", default="20,60,120", help="Comma-separated absolute seconds for stabilize passes")
    parser.add_argument("--stable-required", type=int, default=2, help="Consecutive stable stabilize passes before early exit")
    parser.add_argument("--log-jsonl", default="", help="Append stabilize pass records to this JSONL path")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args()

    if args.startup_delay_seconds > 0:
        time.sleep(args.startup_delay_seconds)

    catalog_path = Path(args.catalog_path) if args.catalog_path else None
    if args.action == "page-reload":
        result = request_desktop_page_reload(wait_seconds=args.wait_seconds)
    elif args.action == "runtime":
        result = combined_state(args.expected_model, catalog_path)
    elif args.action == "catalog-reasoning-plan":
        result = catalog_reasoning_repair_plan(catalog_path)
    elif args.action == "catalog-reasoning-apply":
        result = apply_catalog_reasoning_repair(catalog_path)
    elif args.action == "statsig-allowlist-plan":
        result = statsig_allowlist_sync_state(catalog_path, apply=False, wait_seconds=args.wait_seconds)
    elif args.action == "statsig-allowlist-apply":
        result = statsig_allowlist_sync_state(
            catalog_path,
            apply=True,
            reload_if_changed=args.reload_if_changed,
            wait_seconds=args.wait_seconds,
        )
    elif args.action == "statsig-allowlist-protect":
        result = statsig_allowlist_protection_state(
            catalog_path,
            apply=True,
            reload_if_changed=args.reload_if_changed,
            wait_seconds=args.wait_seconds,
        )
    elif args.action == "statsig-allowlist-stabilize":
        result = statsig_allowlist_stabilize_state(
            catalog_path,
            delays=_parse_delay_list(args.stabilize_delays),
            wait_seconds=args.wait_seconds,
            reload_if_changed=args.reload_if_changed,
            log_path=Path(args.log_jsonl) if args.log_jsonl else None,
            stable_required=args.stable_required,
        )
    elif args.action == "reasoning-hot-refresh-plan":
        result = reasoning_hot_refresh_state(
            catalog_path,
            apply=False,
            wait_seconds=args.wait_seconds,
        )
    elif args.action == "reasoning-hot-refresh-apply":
        result = reasoning_hot_refresh_state(
            catalog_path,
            apply=True,
            reload_if_changed=args.reload_if_changed,
            wait_seconds=args.wait_seconds,
        )
    elif args.action == "appserver-model-shim-apply":
        result = apply_appserver_model_shim(catalog_path, wait_seconds=args.wait_seconds)
    else:
        result = apply_model_list_bridge_shim(
            catalog_path,
            reload_page=args.reload_if_changed,
            wait_seconds=args.wait_seconds,
        )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    return 0 if bool(result.get("ok", True)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
