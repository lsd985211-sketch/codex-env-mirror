import { readFileSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const runtimeRoot = process.env.CODEX_OPENCLAW_RUNTIME_ROOT || path.join(process.env.LOCALAPPDATA, "Codex", "openclaw");
const stateDir = path.join(runtimeRoot, "clean-install", "state");
const sendModule = pathToFileURL(path.join(stateDir, "extensions", "openclaw-weixin", "dist", "src", "messaging", "send.js")).href;
const { sendMessageWeixin } = await import(sendModule);
const account = JSON.parse(readFileSync(`${stateDir}/openclaw-weixin/accounts/087feb936bb1-im-bot.json`, "utf8"));
const ctFile = `${stateDir}/openclaw-weixin/accounts/087feb936bb1-im-bot.context-tokens.json`;
const contextTokens = readFileSync(ctFile, "utf8") ? JSON.parse(readFileSync(ctFile, "utf8")) : {};
const contextToken = contextTokens["o9cq80_7_t7OGRYescsBdqz_4YrI@im.wechat"] || "";

try {
  const result = await sendMessageWeixin({
    to: "o9cq80_7_t7OGRYescsBdqz_4YrI@im.wechat",
    text: "【ctx 测试】带 contextToken 的发送测试。收到请回复。",
    opts: {
      baseUrl: account.baseUrl || "https://ilinkai.weixin.qq.com",
      token: account.token,
      contextToken: contextToken || undefined,
      timeoutMs: 15000,
    },
  });
  console.log(JSON.stringify({ ok: true, hasCtx: !!contextToken, result }, null, 2));
} catch (e) {
  console.log(JSON.stringify({ ok: false, hasCtx: !!contextToken, error: String(e && e.message ? e.message : e) }, null, 2));
}
