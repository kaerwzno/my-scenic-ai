/**
 * 端到端验证：问题 → Next 路由 → Fay → WebSocket 回答
 *
 * 用法（先确保 Fay 在跑、Next dev 在跑）：
 *   node scripts/fay-e2e-test.mjs "五印坛城在哪"
 *
 * 只想起 Fay、还没起前端时，直接打 Fay 的接口：
 *   node scripts/fay-e2e-test.mjs --direct "五印坛城在哪"
 *
 * 它做的事就是前端做的事：
 *   1. 连上 Fay 的 WebSocket，登记 {Username, Output:true}
 *   2. 把问题 POST 给 /api/qa/chat
 *   3. 把 WebSocket 推回来的东西打出来（文字 / 音频地址）
 */

const args = process.argv.slice(2);
const DIRECT = args.includes("--direct");
const QUESTION = args.find((a) => !a.startsWith("--")) || "五印坛城在哪";

const WS_URL = process.env.NEXT_PUBLIC_FAY_WS || "ws://127.0.0.1:10002";
const FAY_BASE = process.env.FAY_BASE_URL || "http://127.0.0.1:5000";
const CHAT_URL =
  process.env.CHAT_URL || (DIRECT ? `${FAY_BASE}/api/send` : "http://127.0.0.1:3000/api/qa/chat");
const WAIT_MS = Number(process.env.WAIT_MS || 45000);

console.log(`问题   : ${QUESTION}`);
console.log(`WebSocket: ${WS_URL}`);
console.log(`接口   : ${CHAT_URL}${DIRECT ? "  (直连 Fay，不经前端)" : ""}`);
console.log("─".repeat(60));

const t0 = Date.now();
const stamp = () => `+${((Date.now() - t0) / 1000).toFixed(1)}s`;

let gotText = 0;
let gotAudio = 0;

const ws = new WebSocket(WS_URL);

ws.onopen = async () => {
  ws.send(JSON.stringify({ Username: "User", Output: true }));
  console.log(`${stamp()} [ws] 已连接并登记 User`);

  try {
    // 直连 Fay 时必须是 form-data，字段名 data，内容是 {username, msg}
    const res = DIRECT
      ? await fetch(CHAT_URL, {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: new URLSearchParams({
            data: JSON.stringify({ username: "User", msg: QUESTION }),
          }).toString(),
        })
      : await fetch(CHAT_URL, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: QUESTION }),
        });
    const body = await res.text();
    console.log(`${stamp()} [http] ${res.status} ${body}`);
  } catch (e) {
    console.error(`${stamp()} [http] 失败:`, e.message);
  }
};

ws.onmessage = (ev) => {
  let msg;
  try {
    msg = JSON.parse(String(ev.data));
  } catch {
    return;
  }
  const d = msg.Data || {};
  if (!["text", "audio", "question"].includes(d.Key)) return;

  if (d.Key === "text") {
    gotText += 1;
    console.log(
      `${stamp()} [text] first=${d.IsFirst} end=${d.IsEnd} ${JSON.stringify(d.Text || d.Value)}`
    );
  } else if (d.Key === "audio") {
    gotAudio += 1;
    console.log(`${stamp()} [audio] ${d.HttpValue}`);
  } else {
    console.log(`${stamp()} [question] ${JSON.stringify(d.Text || d.Value)}`);
  }
};

ws.onerror = () => console.error(`${stamp()} [ws] 连接出错（Fay 没在跑？）`);

setTimeout(() => {
  console.log("─".repeat(60));
  console.log(`文字分片 ${gotText} 段，音频 ${gotAudio} 段`);
  console.log(gotText === 0 ? "❌ 没收到文字回答" : "✅ 收到文字回答");
  console.log(gotAudio === 0 ? "⚠️  没收到音频（口型会没数据）" : "✅ 收到音频");
  process.exit(0);
}, WAIT_MS);
