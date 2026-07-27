#!/usr/bin/env node
"use strict";

const ENDPOINT = "https://developers.openai.com/mcp";
const PROTOCOL_VERSION = "2025-06-18";
const ALLOWED_TOOLS = new Set([
  "search_openai_docs",
  "fetch_openai_doc",
  "list_openai_docs",
  "get_openapi_spec",
]);

let remoteSessionId = "";
let remoteInitialized = false;
let remoteNextId = 1;
let toolCache = null;

function writeJson(payload) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}

function parseRemoteMessage(text) {
  const body = String(text || "").trim();
  if (body.startsWith("{")) return JSON.parse(body);
  const dataLines = body
    .split(/\r?\n/)
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trim());
  for (let index = dataLines.length - 1; index >= 0; index -= 1) {
    const parsed = JSON.parse(dataLines[index]);
    if (parsed && (parsed.result || parsed.error || parsed.id !== undefined)) return parsed;
  }
  throw new Error("OpenAI Docs MCP returned no JSON-RPC payload");
}

function requestHeaders() {
  const headers = {
    accept: "application/json, text/event-stream",
    "content-type": "application/json",
    "mcp-protocol-version": PROTOCOL_VERSION,
  };
  if (remoteSessionId) headers["mcp-session-id"] = remoteSessionId;
  return headers;
}

async function remoteRequest(method, params = {}, id = remoteNextId++) {
  const response = await fetch(ENDPOINT, {
    method: "POST",
    headers: requestHeaders(),
    body: JSON.stringify({ jsonrpc: "2.0", id, method, params }),
  });
  const body = await response.text();
  if (response.status === 404 && remoteSessionId && method !== "initialize") {
    remoteSessionId = "";
    remoteInitialized = false;
    toolCache = null;
    await ensureRemoteInitialized();
    return remoteRequest(method, params, id);
  }
  if (!response.ok) {
    throw new Error(`OpenAI Docs MCP HTTP ${response.status}: ${body.slice(0, 500)}`);
  }
  remoteSessionId = response.headers.get("mcp-session-id") || remoteSessionId;
  const message = parseRemoteMessage(body);
  if (message.error) {
    throw new Error(`OpenAI Docs MCP ${method} failed: ${JSON.stringify(message.error)}`);
  }
  return message.result;
}

async function remoteNotify(method, params = {}) {
  const response = await fetch(ENDPOINT, {
    method: "POST",
    headers: requestHeaders(),
    body: JSON.stringify({ jsonrpc: "2.0", method, params }),
  });
  if (!response.ok && response.status !== 202) {
    const body = await response.text();
    throw new Error(`OpenAI Docs MCP notification ${method} HTTP ${response.status}: ${body.slice(0, 500)}`);
  }
}

async function ensureRemoteInitialized() {
  if (remoteInitialized) return;
  await remoteRequest("initialize", {
    protocolVersion: PROTOCOL_VERSION,
    capabilities: {},
    clientInfo: { name: "openai-docs-local-proxy", version: "1.0.0" },
  });
  remoteInitialized = true;
  try {
    await remoteNotify("notifications/initialized", {});
  } catch {
    // Some stateless endpoints do not require the notification.
  }
}

async function remoteTools() {
  await ensureRemoteInitialized();
  if (toolCache) return toolCache;
  const result = await remoteRequest("tools/list", {});
  const tools = Array.isArray(result?.tools) ? result.tools : [];
  toolCache = tools.filter((tool) => ALLOWED_TOOLS.has(tool?.name));
  if (toolCache.length === 0) {
    throw new Error("OpenAI Docs MCP exposed none of the allowlisted documentation tools");
  }
  return toolCache;
}

async function handle(message) {
  const { id, method, params } = message;
  if (method === "initialize") {
    return {
      jsonrpc: "2.0",
      id,
      result: {
        protocolVersion: PROTOCOL_VERSION,
        capabilities: { tools: {} },
        serverInfo: { name: "openai-docs-local-proxy", version: "1.0.0" },
        instructions: "Read-only Hub-managed proxy for the official OpenAI Developer Docs MCP.",
      },
    };
  }
  if (method === "notifications/initialized") return null;
  if (method === "tools/list") {
    return { jsonrpc: "2.0", id, result: { tools: await remoteTools() } };
  }
  if (method === "tools/call") {
    const name = String(params?.name || "");
    if (!ALLOWED_TOOLS.has(name)) {
      throw new Error(`OpenAI Docs tool is not allowlisted: ${name}`);
    }
    await remoteTools();
    const result = await remoteRequest("tools/call", {
      name,
      arguments: params?.arguments || {},
    });
    return { jsonrpc: "2.0", id, result };
  }
  return {
    jsonrpc: "2.0",
    id,
    error: { code: -32601, message: `Unknown method: ${method}` },
  };
}

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  input += chunk;
  const lines = input.split(/\r?\n/);
  input = lines.pop() || "";
  for (const line of lines) {
    const text = line.trim();
    if (!text) continue;
    Promise.resolve()
      .then(() => handle(JSON.parse(text)))
      .then((response) => {
        if (response) writeJson(response);
      })
      .catch((error) => {
        let id = null;
        try {
          id = JSON.parse(text).id ?? null;
        } catch {}
        writeJson({
          jsonrpc: "2.0",
          id,
          error: { code: -32000, message: String(error?.message || error) },
        });
      });
  }
});
