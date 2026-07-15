const fs = require("node:fs");
const p = "C:/Users/45543/Downloads/mcsmanager_windows_release/mcsmanager/_tools/openclaw-codex/clean-install/state/openclaw-weixin/accounts/087feb936bb1-im-bot.json";
const acc = JSON.parse(fs.readFileSync(p, "utf8"));
const token = acc.token || "";
(async () => {
  const body = { msg: { from_user_id: "", to_user_id: "o9cq80_7_t7OGRYescsBdqz_4YrI@im.wechat", client_id: "t-"+Date.now(), message_type: 2, message_state: 2, item_list: [{type:1,text_item:{text:"[diag] test"}}] }, base_info: { channel_version: "0.0.0", bot_agent: "Codex" } };
  async function ts(authHeader, label) {
    try {
      const res = await fetch("https://ilinkai.weixin.qq.com/ilink/bot/sendmessage", {
        method:"POST", headers:{"Content-Type":"application/json",AuthorizationType:"ilink_bot_token","iLink-App-Id":"","iLink-App-ClientVersion":"0",Authorization:authHeader}, body:JSON.stringify(body), signal:AbortSignal.timeout(10000)
      });
      const raw = await res.text(); let j; try{j=JSON.parse(raw)}catch(_){j={}}; let ec=j.errcode!==undefined?j.errcode:j.ret;
      console.log(label, "HTTP", res.status, "ec:", ec, "msg:", (j.errmsg||j.message||"").slice(0,80));
    } catch(e) { console.log(label, "ERR", e.message); }
  }
  await ts("Bearer "+token, "Bearer");
  await ts("ilink_bot_token "+token, "ilink_bot_token");
  await ts(token, "raw-token");
  await ts("Bearer "+token.split(":").pop(), "Bearer-hex-only");
  console.log("done");
})();
