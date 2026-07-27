#!/usr/bin/env node
"use strict";

const ENDPOINT = "https://learn.microsoft.com/api/mcp";
const PROTOCOL_VERSION = "2025-06-18";
let remoteSessionId = "";
let remoteInitialized = false;
let remoteNextId = 1;

function writeJson(payload) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}

function textContent(text) {
  return { content: [{ type: "text", text: String(text ?? "") }] };
}

function parseSseJson(text) {
  const dataLines = [];
  for (const raw of String(text || "").split(/\r?\n/)) {
    if (raw.startsWith("data: ")) dataLines.push(raw.slice(6));
  }
  for (let i = dataLines.length - 1; i >= 0; i -= 1) {
    const parsed = JSON.parse(dataLines[i]);
    if (parsed && (parsed.result || parsed.error || parsed.id !== undefined)) return parsed;
  }
  throw new Error("Microsoft Learn MCP returned no SSE data payload");
}

async function remoteRequest(method, params = {}, id = remoteNextId++) {
  const headers = {
    accept: "application/json, text/event-stream",
    "content-type": "application/json",
  };
  if (remoteSessionId) {
    headers["mcp-session-id"] = remoteSessionId;
  }
  headers["mcp-protocol-version"] = PROTOCOL_VERSION;
  const response = await fetch(ENDPOINT, {
    method: "POST",
    headers,
    body: JSON.stringify({
      jsonrpc: "2.0",
      id,
      method,
      params,
    }),
  });
  const body = await response.text();
  if (response.status === 404 && remoteSessionId) {
    remoteSessionId = "";
    remoteInitialized = false;
    if (method !== "initialize") {
      await ensureRemoteInitialized();
      return remoteRequest(method, params, id);
    }
  }
  if (!response.ok) {
    throw new Error(`Microsoft Learn MCP HTTP ${response.status}: ${body.slice(0, 500)}`);
  }
  const message = parseSseJson(body);
  remoteSessionId = response.headers.get("mcp-session-id") || remoteSessionId;
  if (message.error) {
    throw new Error(`Microsoft Learn MCP ${method} failed: ${JSON.stringify(message.error)}`);
  }
  return message.result;
}

async function remoteNotify(method, params = {}) {
  const headers = {
    accept: "application/json, text/event-stream",
    "content-type": "application/json",
    "mcp-protocol-version": PROTOCOL_VERSION,
  };
  if (remoteSessionId) {
    headers["mcp-session-id"] = remoteSessionId;
  }
  const response = await fetch(ENDPOINT, {
    method: "POST",
    headers,
    body: JSON.stringify({
      jsonrpc: "2.0",
      method,
      params,
    }),
  });
  if (!response.ok && response.status !== 202) {
    const body = await response.text();
    throw new Error(`Microsoft Learn MCP notification ${method} HTTP ${response.status}: ${body.slice(0, 500)}`);
  }
}

async function ensureRemoteInitialized() {
  if (remoteInitialized) return;
  await remoteRequest("initialize", {
    protocolVersion: PROTOCOL_VERSION,
    capabilities: {},
    clientInfo: { name: "microsoftdocs-local-proxy", version: "1.0.0" },
  });
  remoteInitialized = true;
  try {
    await remoteNotify("notifications/initialized", {});
  } catch {
    // The search/fetch tools work without this on Microsoft Learn's endpoint; do not fail startup on notification handling drift.
  }
}

const TOOL_DEFINITIONS = [
  {
    name: "microsoft_docs_search",
    description:
      "Search official Microsoft/Azure documentation and return concise first-party Microsoft Learn results.",
    inputSchema: {
      type: "object",
      properties: {
        query: {
          type: "string",
          description: "A query or topic about Microsoft/Azure products, services, developer tools, frameworks, APIs, or SDKs.",
        },
      },
      required: ["query"],
      additionalProperties: false,
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true },
  },
  {
    name: "microsoft_code_sample_search",
    description:
      "Search official Microsoft Learn code snippets and examples for Microsoft/Azure related coding tasks.",
    inputSchema: {
      type: "object",
      properties: {
        query: {
          type: "string",
          description: "A descriptive query, SDK name, method name, or code topic.",
        },
        language: {
          type: "string",
          description: "Optional language filter, such as python, csharp, javascript, typescript, powershell, azurecli, java, go, or rust.",
        },
      },
      required: ["query"],
      additionalProperties: false,
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true },
  },
  {
    name: "microsoft_docs_fetch",
    description: "Fetch and convert a Microsoft Learn documentation URL to markdown.",
    inputSchema: {
      type: "object",
      properties: {
        url: {
          type: "string",
          description: "A Microsoft Learn or official Microsoft documentation URL to fetch.",
        },
      },
      required: ["url"],
      additionalProperties: false,
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true },
  },
];

async function callTool(name, args) {
  if (!TOOL_DEFINITIONS.some((tool) => tool.name === name)) {
    throw new Error(`Unknown tool: ${name}`);
  }
  await ensureRemoteInitialized();
  const result = await remoteRequest("tools/call", { name, arguments: args || {} });
  if (Array.isArray(result?.content)) {
    return result;
  }
  return textContent(JSON.stringify(result, null, 2));
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
        serverInfo: { name: "microsoftdocs-local-proxy", version: "1.0.0" },
        instructions:
          "Read-only proxy for Microsoft Learn MCP. Use microsoft_docs_search first, microsoft_code_sample_search for code examples, and microsoft_docs_fetch for full pages.",
      },
    };
  }
  if (method === "notifications/initialized") {
    return null;
  }
  if (method === "tools/list") {
    return { jsonrpc: "2.0", id, result: { tools: TOOL_DEFINITIONS } };
  }
  if (method === "tools/call") {
    const result = await callTool(params?.name, params?.arguments || {});
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
