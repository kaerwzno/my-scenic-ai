import { NextRequest, NextResponse } from "next/server";

/**
 * 数字人问答 —— 转发给 Fay 框架。
 *
 * ── 这个文件以前是什么 ──────────────────────────────────────
 * 作者原本在这里查自己的 Postgres（knowledge_docs 表）做 RAG，
 * 再调 DeepSeek / 阶跃 生成，用 SSE 把 delta 流回前端。
 *
 * ── 现在改成什么 ────────────────────────────────────────────
 * 知识库不用它那套了，改成把问题丢给 Fay：
 *   Fay 那边挂着我们的知识库插件（灵山 / 拈花湾 / 图谱增强问答）、
 *   长期记忆、闭园提醒那套主动机制。
 * 这样用户端数字人说的话，就真的是我们知识库里的内容。
 *
 * ── 两个必须记住的坑 ────────────────────────────────────────
 * 1. Fay 的 /api/send **只认 form-data**，字段名固定叫 data，
 *    内容是 JSON 字符串 {username, msg}。发 JSON body 会回"未提供数据"。
 *
 * 2. **回答不从这个接口返回。** 它是异步的：Fay 收到问题后会走
 *    「知识库检索 → 大模型生成 → TTS 合成」，然后把结果从 WebSocket
 *    （10002 端口）推回来。前端那条连接在 src/lib/fay/client.ts。
 *    所以这里只返回一个"收到了"的回执。
 *
 * 原来的实现备份在 D:\PROJECT\scenic_ai\_backup\qa-chat-route.original.ts。
 */

const FAY_BASE = process.env.FAY_BASE_URL || "http://127.0.0.1:5000";
const FAY_USERNAME = process.env.FAY_USERNAME || "User";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  let question = "";
  try {
    const body = await request.json();
    question = String(body?.question || "").trim();
  } catch {
    return NextResponse.json({ answer: "问题格式不对。" }, { status: 400 });
  }

  if (!question) {
    return NextResponse.json({ answer: "您还没有输入问题。" }, { status: 400 });
  }

  const form = new URLSearchParams();
  form.append("data", JSON.stringify({ username: FAY_USERNAME, msg: question }));

  try {
    const res = await fetch(`${FAY_BASE}/api/send`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: form.toString(),
      cache: "no-store",
    });
    const text = await res.text();

    // Fay 成功时返回 {"result":"successful"}
    if (!res.ok || !text.includes("successful")) {
      console.error("[qa/chat] Fay 返回异常:", res.status, text.slice(0, 200));
      return NextResponse.json(
        { answer: "小玉刚才没接住这句话，请再说一次。" },
        { status: 502 }
      );
    }

    // 答案稍后会从 WebSocket 推过来，这里只回执
    return NextResponse.json({ ok: true, via: "fay" });
  } catch (err: any) {
    console.error("[qa/chat] 连不上 Fay:", err?.message);
    return NextResponse.json(
      { answer: "数字人服务没在跑（Fay 未启动）。" },
      { status: 502 }
    );
  }
}

/**
 * 历史记录：以前是从 qa_logs 表查的。
 * 暂时没有本地库，先返回空数组 —— 前端拿不到历史不会报错。
 * 以后要接的话，这里接 Fay 的 /api/get-msg 或者我们自己的库。
 */
export async function GET() {
  return NextResponse.json({ messages: [] });
}

/**
 * "新建对话"会打这里。以前是清数据库里的会话。
 * 现在 Fay 那边有自己的会话管理，这里只回个成功。
 */
export async function DELETE() {
  return NextResponse.json({ ok: true });
}
