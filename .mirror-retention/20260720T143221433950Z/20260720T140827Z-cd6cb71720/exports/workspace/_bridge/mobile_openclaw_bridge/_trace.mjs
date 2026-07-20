import { readFileSync } from "node:fs";

const stateDir = "C:/Users/45543/Downloads/mcsmanager_windows_release/mcsmanager/_tools/openclaw-codex/clean-install/state";
const account = JSON.parse(readFileSync(`${stateDir}/openclaw-weixin/accounts/087feb936bb1-im-bot.json`, "utf8"));
const pkg = JSON.parse(readFileSync(`${stateDir}/extensions/openclaw-weixin/package.json`, "utf8"));
const ctFile = `${stateDir}/openclaw-weixin/accounts/087feb936bb1-im-bot.context-tokens.json`;
const contextTokens = readFileSync(ctFile, "utf8") ? JSON.parse(readFileSync(ctFile, "utf8")) : {};
const contextToken = contextTokens["o9cq80_7_t7OGRYescsBdqz_4YrI@im.wechat"] || "";
import crypto from "node:crypto";
function buildClientVersion(version) {
  const parts = String(version || "0.0.0").split(".").map(p => parseInt(p, 10));
  return ((parts[0] & 0xff) << 16) | ((parts[1] & 0xff) << 8) | (parts[2] & 0xff);
}
function randomWechatUin() {
  const uint32 = crypto.randomBytes(4).readUInt32BE(0);
  return Buffer.from(String(uint32), "utf-8").toString("base64");
}

const body = {
  msg: { from_user_id: "", to_user_id: "o9cq80_7_t7OGRYescsBdqz_4YrI@im.wechat", client_id: "trace-"+Date.now(), message_type: 2, message_state: 2, item_list: [{type:1,text_item:{text:"[trace probe]"}}], context_token: contextToken || undefined },
  base_info: { channel_version: pkg.version || "unknown", bot_agent: "OpenClaw" },
};

const base = account.baseUrl || "https://ilinkai.weixin.qq.com";
const url = new URL("ilink/bot/sendmessage", base.endsWith("/") ? base : base + "/");

const headers = {
  "Content-Type": "application/json",
  AuthorizationType: "ilink_bot_token",
  "X-WECHAT-UIN": randomWechatUin(),
  "iLink-App-Id": pkg.ilink_appid || "",
  "iLink-App-ClientVersion": String(buildClientVersion(pkg.version || "0.0.0")),
  Authorization: `Bearer ${(account.token || "").trim()}`,
};

console.log("URL:", url.toString());
console.log("APPID:", pkg.ilink_appid || "(none)");
console.log("TOKEN:", account.token.substring(0, 20) + "...");
console.log("CTX:", contextToken ? "present("+contextToken.length+"chars)" : "missing");

try {
  const res = await fetch(url.toString(), {
    method: "POST", headers, body: JSON.stringify(body), signal: AbortSignal.timeout(15000)
  });
  const raw = await res.text();
  console.log("HTTP", res.status, "BODY", raw);
} catch(e) {
  console.log("ERR", e.message);
}
