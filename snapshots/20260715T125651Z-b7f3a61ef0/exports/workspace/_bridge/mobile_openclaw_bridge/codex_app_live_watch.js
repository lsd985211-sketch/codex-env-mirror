#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");

function parseArgs(argv) {
  const args = {
    host: "127.0.0.1",
    port: 18791,
    config: path.join(__dirname, "config.local.json"),
    output: path.join(__dirname, "runtime", "dashboard_live_state.json"),
    activeFile: "",
    activeWindowMs: 90000,
    inactiveHeartbeatWriteMs: 300000,
    heartbeatMs: 1000,
    reconnectMs: 1500,
    syncMs: 4000,
    heartbeatWriteMs: 30000,
    idleSyncMs: 30000,
    idleAfterMs: 120000,
    turnLimit: 6,
    rpcTimeoutMs: 12000,
    maxConsecutiveSyncTimeouts: 2,
    maxThreadTimeouts: 2,
    threadBackoffMs: 300000,
    globalBackoffMs: 120000,
  };
  for (let i = 2; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--host") args.host = argv[++i] || args.host;
    else if (arg === "--port") args.port = Number(argv[++i]);
    else if (arg === "--config") args.config = argv[++i] || args.config;
    else if (arg === "--output") args.output = argv[++i] || args.output;
    else if (arg === "--active-file") args.activeFile = argv[++i] || args.activeFile;
    else if (arg === "--active-window-ms") args.activeWindowMs = Number(argv[++i]);
    else if (arg === "--inactive-heartbeat-write-ms") args.inactiveHeartbeatWriteMs = Number(argv[++i]);
    else if (arg === "--heartbeat-ms") args.heartbeatMs = Number(argv[++i]);
    else if (arg === "--reconnect-ms") args.reconnectMs = Number(argv[++i]);
    else if (arg === "--sync-ms") args.syncMs = Number(argv[++i]);
    else if (arg === "--heartbeat-write-ms") args.heartbeatWriteMs = Number(argv[++i]);
    else if (arg === "--idle-sync-ms") args.idleSyncMs = Number(argv[++i]);
    else if (arg === "--idle-after-ms") args.idleAfterMs = Number(argv[++i]);
    else if (arg === "--turn-limit") args.turnLimit = Number(argv[++i]);
    else if (arg === "--rpc-timeout-ms") args.rpcTimeoutMs = Number(argv[++i]);
    else if (arg === "--max-consecutive-sync-timeouts") args.maxConsecutiveSyncTimeouts = Number(argv[++i]);
    else if (arg === "--max-thread-timeouts") args.maxThreadTimeouts = Number(argv[++i]);
    else if (arg === "--thread-backoff-ms") args.threadBackoffMs = Number(argv[++i]);
    else if (arg === "--global-backoff-ms") args.globalBackoffMs = Number(argv[++i]);
    else throw new Error(`Unknown argument: ${arg}`);
  }
  if (!args.host || /[/:]/.test(args.host)) throw new Error("Invalid --host");
  if (!Number.isInteger(args.port) || args.port <= 0) throw new Error("Invalid --port");
  if (!args.activeFile) args.activeFile = path.join(path.dirname(args.output), "dashboard_activity.json");
  if (!Number.isInteger(args.activeWindowMs) || args.activeWindowMs < 10000) args.activeWindowMs = 90000;
  if (!Number.isInteger(args.inactiveHeartbeatWriteMs) || args.inactiveHeartbeatWriteMs < args.heartbeatWriteMs) args.inactiveHeartbeatWriteMs = Math.max(args.heartbeatWriteMs, 300000);
  if (!Number.isInteger(args.syncMs) || args.syncMs < 1000) args.syncMs = 4000;
  if (!Number.isInteger(args.heartbeatWriteMs) || args.heartbeatWriteMs < 5000) args.heartbeatWriteMs = 30000;
  if (!Number.isInteger(args.idleSyncMs) || args.idleSyncMs < args.syncMs) args.idleSyncMs = Math.max(args.syncMs, 30000);
  if (!Number.isInteger(args.idleAfterMs) || args.idleAfterMs < 30000) args.idleAfterMs = 120000;
  if (!Number.isInteger(args.turnLimit) || args.turnLimit < 1) args.turnLimit = 6;
  if (!Number.isInteger(args.rpcTimeoutMs) || args.rpcTimeoutMs < 3000) args.rpcTimeoutMs = 12000;
  if (!Number.isInteger(args.maxConsecutiveSyncTimeouts) || args.maxConsecutiveSyncTimeouts < 1) args.maxConsecutiveSyncTimeouts = 2;
  if (!Number.isInteger(args.maxThreadTimeouts) || args.maxThreadTimeouts < 1) args.maxThreadTimeouts = 2;
  if (!Number.isInteger(args.threadBackoffMs) || args.threadBackoffMs < 30000) args.threadBackoffMs = 300000;
  if (!Number.isInteger(args.globalBackoffMs) || args.globalBackoffMs < args.idleSyncMs) args.globalBackoffMs = Math.max(args.idleSyncMs, 120000);
  return args;
}

function nowIso() {
  return new Date().toISOString();
}

function readJson(file, fallback) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return fallback;
  }
}

function errorText(error) {
  if (!error) return "unknown error";
  if (error.stack) return String(error.stack);
  if (error.message) return String(error.message);
  try {
    return JSON.stringify(error);
  } catch {
    return String(error);
  }
}

function lockPathForOutput(output) {
  return `${output}.lock.json`;
}

function processAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0 || pid === process.pid) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function acquireSingleInstanceLock(output) {
  const lockPath = lockPathForOutput(output);
  fs.mkdirSync(path.dirname(lockPath), { recursive: true });
  const existing = readJson(lockPath, null);
  const existingPid = Number(existing?.pid || 0);
  if (processAlive(existingPid)) {
    throw new Error(`codex_app_live_watch already running for ${output} as pid ${existingPid}`);
  }
  fs.writeFileSync(lockPath, JSON.stringify({ pid: process.pid, output, started_at: nowIso() }, null, 2), "utf8");
  const release = () => {
    const current = readJson(lockPath, null);
    if (Number(current?.pid || 0) === process.pid) {
      try { fs.unlinkSync(lockPath); } catch {}
    }
  };
  process.once("exit", release);
  process.once("SIGINT", () => { release(); process.exit(130); });
  process.once("SIGTERM", () => { release(); process.exit(143); });
}

function isTimeoutError(error) {
  const text = errorText(error).toLowerCase();
  return text.includes("timeout");
}

function dashboardActivity(activeFile, activeWindowMs) {
  const nowMs = Date.now();
  const data = readJson(activeFile, null);
  let lastSeenMs = Number(data?.last_seen_epoch_ms || 0);
  if (!lastSeenMs) {
    try {
      lastSeenMs = fs.statSync(activeFile).mtimeMs;
    } catch {
      lastSeenMs = 0;
    }
  }
  const ageMs = lastSeenMs ? nowMs - lastSeenMs : Number.POSITIVE_INFINITY;
  return {
    active: Boolean(lastSeenMs && ageMs <= activeWindowMs),
    ageMs: Number.isFinite(ageMs) ? Math.max(0, Math.round(ageMs)) : null,
    lastSeenAt: data?.last_seen_at || "",
  };
}

function threadMap(configPath) {
  const config = readJson(configPath, {});
  const items = config?.threads?.items;
  const map = {};
  if (Array.isArray(items)) {
    for (const item of items) {
      if (!item || !item.thread_id) continue;
      map[String(item.thread_id)] = {
        id: String(item.id || ""),
        name: String(item.name || item.id || item.thread_id),
      };
    }
  }
  return map;
}

function safeText(value, limit = 800) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  return text.length > limit ? `${text.slice(0, limit - 1)}...` : text;
}

function itemText(item) {
  if (!item || typeof item !== "object") return "";
  if (typeof item.text === "string") return item.text;
  if (typeof item.content === "string") return item.content;
  if (Array.isArray(item.content)) {
    return item.content
      .map(part => part?.text || part?.content || "")
      .filter(Boolean)
      .join("\n");
  }
  if (item.type === "mcpToolCall") {
    const result = item.result ? safeText(JSON.stringify(item.result), 500) : "";
    const error = item.error ? safeText(JSON.stringify(item.error), 500) : "";
    return [result, error].filter(Boolean).join("\n");
  }
  if (item.type === "webSearch") {
    return [item.query, item.action].filter(Boolean).join(" · ");
  }
  return "";
}

function summarizeItem(item) {
  if (!item || typeof item !== "object") return null;
  const type = String(item.type || item.kind || "");
  const phase = String(item.phase || item.status || "");
  const title = String(
    item.title
    || item.name
    || item.toolName
    || item.tool
    || item.command
    || item.query
    || ""
  );
  return {
    id: String(item.id || ""),
    type,
    phase,
    title,
    server: String(item.server || ""),
    duration_ms: Number.isFinite(item.durationMs) ? item.durationMs : null,
    text: safeText(itemText(item), 700),
  };
}

function writeState(output, state) {
  fs.mkdirSync(path.dirname(output), { recursive: true });
  const tmp = `${output}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(state, null, 2), "utf8");
  try {
    fs.renameSync(tmp, output);
  } catch (error) {
    if (error && error.code === "EPERM") {
      try {
        fs.copyFileSync(tmp, output);
        fs.unlinkSync(tmp);
        return;
      } catch (copyError) {
        try {
          fs.unlinkSync(tmp);
        } catch {}
        throw copyError;
      }
    }
    try {
      fs.unlinkSync(tmp);
    } catch {}
    throw error;
  }
}

function stableStateJson(state) {
  const clone = { ...state };
  delete clone.generated_at;
  delete clone.heartbeat_at;
  return JSON.stringify(clone);
}

function createStateWriter(output) {
  let lastStableJson = "";
  let lastHeartbeatWriteMs = 0;
  return function commitState(state, reason = "change", options = {}) {
    const nowMs = Date.now();
    const heartbeatOnly = Boolean(options.heartbeatOnly);
    state.heartbeat_at = nowIso();
    if (!heartbeatOnly) {
      state.generated_at = state.heartbeat_at;
    }
    const nextStableJson = stableStateJson(state);
    const stableChanged = nextStableJson !== lastStableJson;
    const minHeartbeatMs = Math.max(5000, Number(options.minHeartbeatMs || 30000));
    const heartbeatDue = nowMs - lastHeartbeatWriteMs >= minHeartbeatMs;
    if (!stableChanged && !(heartbeatOnly && heartbeatDue) && !options.force) {
      return false;
    }
    state.write_reason = reason;
    writeState(output, state);
    lastStableJson = stableStateJson(state);
    lastHeartbeatWriteMs = nowMs;
    return true;
  };
}

function initialState(args, threads) {
  return {
    ok: false,
    mode: "codex-app-server-live-watch",
    generated_at: nowIso(),
    connected: false,
    app_server: `ws://${args.host}:${args.port}`,
    watched_thread_count: Object.keys(threads).length,
    dashboard_active: false,
    dashboard_activity_age_ms: null,
    dashboard_activity_last_seen_at: "",
    dashboard_activity_file: args.activeFile,
    last_error: "",
    last_event_at: "",
    heartbeat_at: "",
    write_reason: "initial",
    event_count: 0,
    threads: {},
  };
}

function hasActiveWatchedThread(state) {
  for (const thread of Object.values(state.threads || {})) {
    const statusText = String(thread?.status || "");
    const activeTurnStatus = String(thread?.active_turn_status || "");
    if (activeTurnStatus && !["completed", "failed", "cancelled", "canceled"].includes(activeTurnStatus.toLowerCase())) {
      return true;
    }
    if (statusText.includes('"active"') || statusText.includes("inProgress") || statusText.includes("running")) {
      return true;
    }
  }
  return false;
}

function updateThread(state, threads, threadId, patch) {
  if (!threadId) return;
  const known = threads[threadId] || {};
  const current = state.threads[threadId] || {
    thread_id: threadId,
    route_id: known.id || "",
    name: known.name || threadId,
    status: "",
    active_turn_id: "",
    active_turn_status: "",
    final_turn_id: "",
    final_preview: "",
    delta_preview: "",
    native_events: [],
    token_usage: null,
    updated_at: "",
  };
  state.threads[threadId] = { ...current, ...patch, updated_at: nowIso() };
}

function appendNativeEvent(state, threads, threadId, event) {
  if (!threadId) return;
  const current = state.threads[threadId]?.native_events || [];
  const incoming = {
    at: nowIso(),
    ...event,
  };
  let next = current;
  if (incoming.key) {
    const filtered = current.filter(item => item?.key !== incoming.key);
    next = [...filtered, incoming];
  } else {
    next = [...current, incoming];
  }
  next = next.slice(-80);
  updateThread(state, threads, threadId, { native_events: next });
}

function finalTextFromTurn(turn) {
  const items = Array.isArray(turn?.items) ? turn.items : [];
  return items
    .filter(item => item?.type === "agentMessage" && item?.phase === "final_answer")
    .map(item => String(item.text || "").trim())
    .filter(Boolean)
    .join("\n\n");
}

function isTerminalTurnStatus(status) {
  return ["completed", "failed", "interrupted", "cancelled", "canceled"].includes(String(status || "").toLowerCase());
}

function applyTurnSnapshot(state, threads, threadId, turn) {
  if (!threadId || !turn?.id) return;
  const items = Array.isArray(turn.items) ? turn.items.map(summarizeItem).filter(Boolean).slice(-30) : [];
  const finalPreview = safeText(finalTextFromTurn(turn), 1000);
  const status = String(turn.status || "");
  const turnId = String(turn.id || "");
  updateThread(state, threads, threadId, {
    active_turn_id: status && !isTerminalTurnStatus(status) ? turnId : "",
    active_turn_status: status,
    final_turn_id: finalPreview ? turnId : state.threads[threadId]?.final_turn_id || "",
    final_preview: finalPreview || state.threads[threadId]?.final_preview || "",
  });
  appendNativeEvent(state, threads, threadId, {
    key: `turn:${turnId}:items:snapshot`,
    method: "turn/items/snapshot",
    turn_id: turnId,
    status,
    summary: `Codex turn 快照：${status || "unknown"}，${items.length} 个可见条目。`,
    items,
  });
}

async function syncRecentTurns(state, threads, sendRpc, args, syncHealth) {
  const nowMs = Date.now();
  if (syncHealth.globalBackoffUntilMs && nowMs < syncHealth.globalBackoffUntilMs) {
    state.sync_backoff_until = new Date(syncHealth.globalBackoffUntilMs).toISOString();
    state.sync_backoff_reason = "global_thread_turns_list_timeout_backoff";
    return { skipped: true, reason: "global_backoff" };
  }

  let timeoutCount = 0;
  let successCount = 0;
  for (const threadId of Object.keys(threads)) {
    const threadHealth = syncHealth.threads.get(threadId) || { timeoutCount: 0, backoffUntilMs: 0 };
    if (threadHealth.backoffUntilMs && nowMs < threadHealth.backoffUntilMs) {
      updateThread(state, threads, threadId, {
        sync_error: `thread/turns/list backoff until ${new Date(threadHealth.backoffUntilMs).toISOString()}`,
      });
      continue;
    }

    const response = await sendRpc("thread/turns/list", {
      threadId,
      limit: args.turnLimit,
      sortDirection: "desc",
      itemsView: "full",
    }, args.rpcTimeoutMs).catch(error => ({
      error: { message: errorText(error), timeout: isTimeoutError(error) },
    }));
    if (response?.error) {
      const timedOut = Boolean(response.error.timeout);
      if (timedOut) {
        timeoutCount += 1;
        threadHealth.timeoutCount = Number(threadHealth.timeoutCount || 0) + 1;
        if (threadHealth.timeoutCount >= args.maxThreadTimeouts) {
          threadHealth.backoffUntilMs = Date.now() + args.threadBackoffMs;
        }
        syncHealth.threads.set(threadId, threadHealth);
      }
      updateThread(state, threads, threadId, {
        sync_error: safeText(JSON.stringify(response.error), 500),
        sync_backoff_until: threadHealth.backoffUntilMs ? new Date(threadHealth.backoffUntilMs).toISOString() : "",
      });
      continue;
    }
    threadHealth.timeoutCount = 0;
    threadHealth.backoffUntilMs = 0;
    syncHealth.threads.set(threadId, threadHealth);
    const turns = Array.isArray(response?.result?.data) ? response.result.data : [];
    for (const turn of turns.slice().reverse()) {
      applyTurnSnapshot(state, threads, threadId, turn);
    }
    successCount += 1;
    updateThread(state, threads, threadId, {
      sync_error: "",
      sync_backoff_until: "",
      last_turn_sync_at: nowIso(),
    });
  }

  if (timeoutCount > 0 && successCount === 0) {
    syncHealth.consecutiveFullTimeouts += 1;
  } else if (successCount > 0) {
    syncHealth.consecutiveFullTimeouts = 0;
    syncHealth.globalBackoffUntilMs = 0;
  }
  if (syncHealth.consecutiveFullTimeouts >= args.maxConsecutiveSyncTimeouts) {
    syncHealth.globalBackoffUntilMs = Date.now() + args.globalBackoffMs;
  }
  state.sync_timeout_count = timeoutCount;
  state.sync_success_count = successCount;
  state.sync_consecutive_full_timeouts = syncHealth.consecutiveFullTimeouts;
  state.sync_backoff_until = syncHealth.globalBackoffUntilMs ? new Date(syncHealth.globalBackoffUntilMs).toISOString() : "";
  state.sync_backoff_reason = syncHealth.globalBackoffUntilMs ? "global_thread_turns_list_timeout_backoff" : "";
  return { skipped: false, timeoutCount, successCount };
}

function applyNotification(state, threads, message) {
  const method = String(message?.method || "");
  const params = message?.params || {};
  const threadId = String(params.threadId || params.thread?.id || "");
  if (threadId && !threads[threadId]) return;
  state.event_count += 1;
  state.last_event_at = nowIso();

  if (method === "thread/status/changed") {
    const status = typeof params.status === "string" ? params.status : JSON.stringify(params.status || {});
    updateThread(state, threads, threadId, { status });
    appendNativeEvent(state, threads, threadId, {
      method,
      turn_id: String(params.turnId || ""),
      summary: `线程状态：${safeText(status, 160)}`,
    });
  } else if (method === "turn/started") {
    const turn = params.turn || {};
    updateThread(state, threads, threadId, {
      active_turn_id: String(turn.id || ""),
      active_turn_status: String(turn.status || "running"),
      delta_preview: "",
      final_preview: "",
    });
    appendNativeEvent(state, threads, threadId, {
      method,
      turn_id: String(turn.id || ""),
      status: String(turn.status || "running"),
      summary: "Codex 开始处理该 turn。",
    });
  } else if (method === "turn/completed") {
    const turn = params.turn || {};
    const items = Array.isArray(turn.items) ? turn.items.map(summarizeItem).filter(Boolean).slice(-20) : [];
    const finalItems = Array.isArray(turn.items)
      ? turn.items.filter(item => item?.type === "agentMessage" && item?.phase === "final_answer")
      : [];
    updateThread(state, threads, threadId, {
      active_turn_id: "",
      active_turn_status: String(turn.status || "completed"),
      final_turn_id: String(turn.id || ""),
      final_preview: safeText(finalItems.map(item => item.text || "").filter(Boolean).join("\n\n"), 1000),
      delta_preview: "",
    });
    appendNativeEvent(state, threads, threadId, {
      method,
      turn_id: String(turn.id || ""),
      status: String(turn.status || "completed"),
      summary: "Codex turn 已完成。",
      items,
    });
  } else if (method === "item/agentMessage/delta") {
    const current = state.threads[threadId]?.delta_preview || "";
    updateThread(state, threads, threadId, {
      active_turn_id: String(params.turnId || state.threads[threadId]?.active_turn_id || ""),
      active_turn_status: "streaming",
      delta_preview: safeText(`${current}${params.delta || ""}`, 1000),
    });
  } else if (method.startsWith("item/")) {
    const item = summarizeItem(params.item || params);
    appendNativeEvent(state, threads, threadId, {
      method,
      turn_id: String(params.turnId || state.threads[threadId]?.active_turn_id || ""),
      item,
      summary: safeText([method, item?.title, item?.text].filter(Boolean).join(" · "), 500),
    });
  } else if (method === "thread/tokenUsage/updated") {
    updateThread(state, threads, threadId, {
      active_turn_id: String(params.turnId || state.threads[threadId]?.active_turn_id || ""),
      token_usage: params.tokenUsage || null,
    });
    appendNativeEvent(state, threads, threadId, {
      method,
      turn_id: String(params.turnId || state.threads[threadId]?.active_turn_id || ""),
      summary: "Token 用量已更新。",
    });
  } else if (method === "thread/name/updated") {
    updateThread(state, threads, threadId, { name: String(params.threadName || threads[threadId]?.name || threadId) });
  }
}

async function connectLoop(args) {
  acquireSingleInstanceLock(args.output);
  const threads = threadMap(args.config);
  const state = initialState(args, threads);
  const commitState = createStateWriter(args.output);
  let lastActivityMs = Date.now();
  for (const [threadId, info] of Object.entries(threads)) {
    updateThread(state, threads, threadId, { route_id: info.id, name: info.name });
  }
  commitState(state, "initial", { force: true });

  let ws = null;
  let nextId = 1;
  const send = (method, params = {}) => ws.send(JSON.stringify({ id: nextId++, method, params }));

  const connectOnce = () => new Promise((resolve, reject) => {
    ws = new WebSocket(`ws://${args.host}:${args.port}`);
    ws.addEventListener("open", () => resolve(), { once: true });
    ws.addEventListener("error", event => reject(event?.error || event || new Error("websocket error")), { once: true });
  });

  for (;;) {
    try {
      await connectOnce();
      state.ok = true;
      state.connected = true;
      state.last_error = "";
      state.generated_at = nowIso();
      commitState(state, "connected", { force: true });
      const heartbeat = setInterval(() => {
        const activity = dashboardActivity(args.activeFile, args.activeWindowMs);
        state.ok = true;
        state.connected = true;
        state.dashboard_active = activity.active;
        state.dashboard_activity_age_ms = activity.ageMs;
        state.dashboard_activity_last_seen_at = activity.lastSeenAt;
        commitState(state, "heartbeat", {
          heartbeatOnly: true,
          minHeartbeatMs: activity.active ? args.heartbeatWriteMs : args.inactiveHeartbeatWriteMs,
        });
      }, Math.max(500, args.heartbeatMs || 1000));
      const pending = new Map();
      const sendRpc = (method, params = {}, timeoutMs = 10000) => {
        const id = nextId++;
        ws.send(JSON.stringify({ id, method, params }));
        return new Promise((resolve, reject) => {
          const timer = setTimeout(() => {
            pending.delete(id);
            reject(new Error(`timeout ${method}`));
          }, timeoutMs);
          pending.set(id, { resolve, reject, timer });
        });
      };
      let syncInFlight = false;
      const syncHealth = {
        consecutiveFullTimeouts: 0,
        globalBackoffUntilMs: 0,
        threads: new Map(),
      };
      const runSync = async () => {
        if (syncInFlight || ws.readyState !== WebSocket.OPEN) return;
        const activity = dashboardActivity(args.activeFile, args.activeWindowMs);
        state.dashboard_active = activity.active;
        state.dashboard_activity_age_ms = activity.ageMs;
        state.dashboard_activity_last_seen_at = activity.lastSeenAt;
        if (!activity.active && !hasActiveWatchedThread(state)) {
          state.sync_backoff_reason = "dashboard_inactive";
          commitState(state, "dashboard_inactive", {
            heartbeatOnly: true,
            minHeartbeatMs: args.inactiveHeartbeatWriteMs,
          });
          return;
        }
        if (!activity.active) {
          state.sync_backoff_reason = "dashboard_inactive_but_active_thread_sync";
        }
        syncInFlight = true;
        try {
          await syncRecentTurns(state, threads, sendRpc, args, syncHealth);
          commitState(state, "sync");
        } catch (error) {
          state.last_error = errorText(error);
          commitState(state, "sync_error", { force: true });
        } finally {
          syncInFlight = false;
        }
      };
      let syncTimer = null;
      const scheduleNextSync = () => {
        if (syncTimer) clearTimeout(syncTimer);
        const idleForMs = Date.now() - lastActivityMs;
        const intervalMs = idleForMs >= args.idleAfterMs ? args.idleSyncMs : args.syncMs;
        syncTimer = setTimeout(async () => {
          await runSync();
          scheduleNextSync();
        }, Math.max(1000, intervalMs));
      };
      ws.addEventListener("message", event => {
        try {
          const message = JSON.parse(event.data);
          if (message.id && pending.has(message.id)) {
            const entry = pending.get(message.id);
            pending.delete(message.id);
            clearTimeout(entry.timer);
            entry.resolve(message);
            return;
          }
          if (message.method) {
            lastActivityMs = Date.now();
            applyNotification(state, threads, message);
            commitState(state, "notification");
          }
        } catch (error) {
          state.last_error = errorText(error);
          commitState(state, "message_error", { force: true });
        }
      });
      await sendRpc("initialize", {
        clientInfo: { name: "mobile-dashboard-live-watch", version: "0.1.0" },
        capabilities: { experimentalApi: true },
      });
      await runSync();
      scheduleNextSync();
      await new Promise(resolve => {
        ws.addEventListener("close", resolve, { once: true });
      });
      clearInterval(heartbeat);
      if (syncTimer) clearTimeout(syncTimer);
      for (const entry of pending.values()) {
        clearTimeout(entry.timer);
        entry.reject(new Error("websocket closed"));
      }
      pending.clear();
      state.ok = false;
      state.connected = false;
      state.last_error = "websocket closed";
      commitState(state, "closed", { force: true });
    } catch (error) {
      state.ok = false;
      state.connected = false;
      state.last_error = errorText(error);
      commitState(state, "connect_error", { force: true });
    }
    await new Promise(resolve => setTimeout(resolve, args.reconnectMs));
  }
}

connectLoop(parseArgs(process.argv)).catch(error => {
  process.stderr.write(`${String(error && error.stack ? error.stack : error)}\n`);
  process.exit(1);
});
