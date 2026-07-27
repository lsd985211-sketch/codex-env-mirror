import { readFileSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const runtimeRoot = process.env.CODEX_OPENCLAW_RUNTIME_ROOT || path.join(process.env.LOCALAPPDATA, "Codex", "openclaw");
const stateDir = path.join(runtimeRoot, "clean-install", "state");
const sendModule = pathToFileURL(path.join(stateDir, "extensions", "openclaw-weixin", "dist", "src", "messaging", "send.js")).href;
const { sendMessageWeixin } = await import(sendModule);
const account = JSON.parse(readFileSync(path.join(stateDir, "openclaw-weixin", "accounts", "087feb936bb1-im-bot.json"), "utf8"));

try {
  const result = await sendMessageWeixin({
    to: "o9cq80_7_t7OGRYescsBdqz_4YrI@im.wechat",
    text: "【Extension Send 测试】通过 OpenClaw 扩展 sendMessageWeixin 发送。收到请回复。",
    opts: {
      baseUrl: account.baseUrl || "https://ilinkai.weixin.qq.com",
      token: account.token,
      timeoutMs: 15000,
    },
  });
  console.log(JSON.stringify({ ok: true, result }, null, 2));
} catch (e) {
  console.log(JSON.stringify({ ok: false, error: String(e && e.message ? e.message : e) }, null, 2));
}
