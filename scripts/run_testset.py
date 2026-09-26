#!/usr/bin/env python3
"""把标准测试问题集逐条喂给数字人，把回答记下来。

它做什么
--------
读 docs/testset.json，一条一条发给 Fay（走和前端输入框一样的接口），
等它答完，把「问题 + 系统回答 + 这次调用了哪些知识库工具」记成两样东西：

    docs/test-results.jsonl   原始记录（追加，随时可以接着跑）
    docs/test-results.md      给人看的文档，逐条对应题号

为什么要走 Fay 而不是直接问知识库插件
--------------------------------------
论文要报的是「这套系统答得怎么样」，不是「向量检索排得准不准」。
只测插件会漏掉大模型改写、拒答措辞、记忆注入这些环节。
但插件侧的调用日志也会一起记下来（tools 列），
这样才能区分「检索没给到」和「给了但它没说对」——这两种错的修法完全不同。

关于自动比对
------------
只做最机械的那种：把「期望要点」里的数字和引号里的词抠出来，看回答里有没有。
它只是给人工核对递个提示，不能当结论用——所以结果文档里留了「人工判定」一列。

用法
----
    python run_testset.py --ids F01,F02,F03      # 试跑三条
    python run_testset.py --cat F --limit 10     # 事实型前 10 条
    python run_testset.py --cat B                # 只跑边界题
    python run_testset.py --all                  # 全量（172 条，要跑挺久）
    python run_testset.py --all --resume         # 接着上次跑，跳过已完成的
    python run_testset.py --render-only          # 不改数据，只重新生成 md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib import error, parse, request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TEST_SET = ROOT / "docs" / "testset.json"
RAW_LOG = ROOT / "docs" / "test-results.jsonl"
OUT_MD = ROOT / "docs" / "test-results.md"
PLUGIN_DIR = ROOT / "plugins"

FAY_BASE = "http://127.0.0.1:5000"
USERNAME = "User"
ANSWER_TIMEOUT = 180.0        # 单条最多等多久
POLL_INTERVAL = 1.5
SETTLE_SECONDS = 4.5          # 看到正式回答后，再多等这两轮，防止还有补充
PLACEHOLDER = "我来帮你查一下，稍等…"

# 上一条已经读到的消息时间戳。用来把「这一题的回复」和「上一题迟到的那句」分开——
# 光靠「发送前的当前时间」不够，Fay 落库的时间戳可能是请求发起那一刻的。
_last_ts = 0


def _fix_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


# ─────────────────────────────────────────────────────────────
# 和 Fay 说话
# ─────────────────────────────────────────────────────────────

def _post(base: str, path: str, form: dict, timeout: float = 20.0) -> str:
    body = parse.urlencode(form).encode("utf-8")   # /api/send 只认 form data
    req = request.Request(f"{base}{path}", data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_messages(limit: int = 15) -> list[dict]:
    """读该用户最近的消息（升序）。"""
    raw = _post(FAY_BASE, "/api/get-msg",
                {"data": json.dumps({"username": USERNAME, "limit": limit, "offset": 0})})
    items = (json.loads(raw) or {}).get("list") or []

    def ts_of(m: dict) -> int:
        try:
            v = int(float(m.get("createtime") or 0))
        except (TypeError, ValueError):
            return 0
        return v if v > 10 ** 12 else v * 1000

    rows = [{"type": str(m.get("type") or ""), "content": str(m.get("content") or ""),
             "ts": ts_of(m)} for m in items]
    rows.sort(key=lambda r: r["ts"])
    return rows


def is_user_msg(mtype: str) -> bool:
    """Fay 里用户消息 type=member，数字人 type=fay。"""
    return mtype.lower() not in ("fay", "robot", "system")


def looks_final(text: str) -> bool:
    """这条落库内容是不是「正式回答」。

    Fay 一条回答会落成好几行，靠肉眼都能分出来：
        1. 「我来帮你查一下，稍等…」          —— 占位
        2. 「<think> 执行耗时… 命中 5 条…」    —— 检索轨迹，结尾正好是 </think>
        3. 「<think> … </think> 灵山大照壁长 39.8 米…」 —— 正文在 </think> 之后
    判据就是第 3 种：</think> 后面还有内容。
    """
    t = (text or "").strip()
    if not t or t == PLACEHOLDER:
        return False
    return not t.endswith("</think>")


def clean_answer(text: str) -> str:
    """剥掉占位语和检索轨迹，只留数字人真正说出口的话。

    必须剥：轨迹里原样带着知识库的原文（数字、尺寸都在里面），
    不剥的话机械比对会把「检索到了」当成「答对了」。
    """
    t = (text or "").strip()
    if PLACEHOLDER in t:
        t = t.split(PLACEHOLDER, 1)[1].strip()
    if "</think>" in t:
        t = t.rsplit("</think>", 1)[1].strip()
    return t


def ask(msg: str, since_ms: int) -> str:
    """发一句话，等它说完，返回回答文本（超时返回已经收到的那部分）。

    Fay 的回答不是一个原子事件：它会先把「我来帮你查一下，稍等…」落库，
    中间还可能落一条工具执行记录，最后才落真正的回答。
    所以判据不是「有没有新消息」，而是「已经安静下来了」——
    否则会把上一题的回复当成这一题的答案（实测踩过）。
    """
    global _last_ts
    floor = max(since_ms, _last_ts)
    _post(FAY_BASE, "/api/send",
          {"data": json.dumps({"username": USERNAME, "msg": msg}, ensure_ascii=False)})

    collected: list[dict] = []
    signature: tuple = ()
    seen_final_at = 0.0
    deadline = time.time() + ANSWER_TIMEOUT
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL)
        try:
            msgs = fetch_messages()
        except (error.URLError, OSError, TimeoutError):
            continue
        fresh = [m for m in msgs
                 if m["ts"] > floor and not is_user_msg(m["type"]) and m["content"].strip()]
        now_sig = tuple((m["ts"], m["content"]) for m in fresh)
        changed = now_sig != signature
        if changed:
            signature = now_sig
            collected = fresh
        # 等的是「正文出现」，不是「安静下来」——
        # 工具轨迹和正式回答之间隔着好几秒（模型在生成），
        # 用固定静默时长会在轨迹那一步就收工。
        if collected and looks_final(collected[-1]["content"]):
            if not changed and seen_final_at and (time.time() - seen_final_at) >= SETTLE_SECONDS:
                break
            if changed:
                seen_final_at = time.time()
        elif changed:
            seen_final_at = 0.0

        if collected and seen_final_at and (time.time() - seen_final_at) >= SETTLE_SECONDS + 6:
            break

    if collected:
        _last_ts = max(m["ts"] for m in collected)
    return "\n".join(m["content"].strip() for m in collected)


def now_ms() -> int:
    return int(time.time() * 1000)


# ─────────────────────────────────────────────────────────────
# 这次回答动用了哪些知识库工具
# ─────────────────────────────────────────────────────────────

def read_call_marks() -> dict[str, int]:
    """记下每个插件调用日志当前有多少行，用作「跑之前」的基线。

    必须按文件各记各的：不同插件的日志文件之间没有先后关系，
    混在一起取「最后 N 行」会张冠李戴。
    """
    marks: dict[str, int] = {}
    for f in PLUGIN_DIR.glob("*/logs/calls.jsonl"):
        try:
            marks[str(f)] = len(f.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError:
            marks[str(f)] = 0
    return marks


def summarize_calls(marks: dict[str, int]) -> list[dict]:
    """把这次新增的调用压成能看的摘要。"""
    out: list[dict] = []
    for f in sorted(PLUGIN_DIR.glob("*/logs/calls.jsonl")):
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        new_lines = lines[marks.get(str(f), 0):]
        plugin = f.parent.parent.name
        for line in new_lines:
            line = line.strip()
            if not line:
                continue
            try:
                c = json.loads(line)
            except json.JSONDecodeError:
                continue
            tool = c.get("tool") or ""
            if not str(tool).endswith("_query"):
                continue
            item = {"plugin": plugin, "tool": tool,
                    "reliable": c.get("reliable"), "ok": c.get("ok")}
            spots = c.get("top_spots") or c.get("hit_entities") or []
            if spots:
                item["hit"] = [s for s in spots[:3] if s]
            out.append(item)
    return out


# ─────────────────────────────────────────────────────────────
# 机械比对（只给提示，不下结论）
# ─────────────────────────────────────────────────────────────

NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def key_tokens(expect: str) -> list[str]:
    toks: list[str] = []
    for t in NUM_RE.findall(expect):
        if t not in toks:
            toks.append(t)
    for t in re.findall(r"「([^」]+)」", expect):
        if t not in toks:
            toks.append(t)
    return toks


def auto_compare(expect: str, answer: str) -> tuple[str, float]:
    toks = key_tokens(expect)
    if not toks:
        return "人工看", 0.0
    hit = [t for t in toks if t in answer]
    miss = [t for t in toks if t not in answer]
    ratio = len(hit) / len(toks)
    detail = f"{len(hit)}/{len(toks)}"
    if miss:
        detail += "；缺：" + "、".join(miss[:6])
    return detail, ratio


# ─────────────────────────────────────────────────────────────
# 跑
# ─────────────────────────────────────────────────────────────

def load_items() -> list[dict]:
    return json.loads(TEST_SET.read_text(encoding="utf-8"))["items"]


def load_done() -> set[str]:
    if not RAW_LOG.is_file():
        return set()
    done = set()
    for line in RAW_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("answers"):
            done.add(rec["id"])
    return done


def append_raw(rec: dict) -> None:
    with RAW_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def run_one(item: dict) -> dict:
    turns = item.get("turns") or [item["q"]]
    answers: list[dict] = []
    for i, turn in enumerate(turns, 1):
        marks = read_call_marks()
        since = now_ms()
        print(f"    轮 {i}/{len(turns)}：{turn[:40]}", flush=True)
        ans = ask(turn, since)
        calls = summarize_calls(marks)
        said = clean_answer(ans)
        answers.append({"turn": i, "q": turn, "a": said, "raw": ans, "tools": calls})
        print(f"      → {'（超时，无回答）' if not said else said[:60].replace(chr(10), ' ')}",
              flush=True)
        time.sleep(1.0)

    joined = "\n".join(a["a"] for a in answers)
    detail, ratio = auto_compare(item["expect"], joined)
    return {
        "id": item["id"], "cat": item["cat"], "q": item["q"],
        "expect": item["expect"], "note": item.get("note") or "",
        "answers": answers,
        "auto_detail": detail, "auto_ratio": round(ratio, 3),
        "verdict": "", "comment": "",
        "ran_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def read_raw() -> list[dict]:
    if not RAW_LOG.is_file():
        return []
    out = []
    for line in RAW_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def render_md(items: list[dict], records: list[dict]) -> str:
    by_id: dict[str, dict] = {}
    for r in records:                     # 同一条跑多次时，后跑的覆盖先跑的
        by_id[r["id"]] = r

    cat_meta = {c["code"]: c for c in
                json.loads(TEST_SET.read_text(encoding="utf-8"))["categories"]}

    out: list[str] = []
    w = out.append
    w("# 标准测试 · 回答与核对")
    w("")
    w(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ｜ "
      f"已完成 **{len(by_id)}** / {len(items)} 条")
    w("")
    w("`系统回答` 是数字人真实说出来的话；`机械比对` 只是把期望要点里的数字和关键词"
      "在回答里找了一遍，用来提示可能对不上的地方——**结论以「人工判定」为准**。")
    w("")
    w("判定用：✅ 命中 ／ ⚠️ 部分 ／ ❌ 未命中 ／ 🚫 编造 ／ ➖ 数据冲突")
    w("")

    # 汇总
    w("## 一、进度与自动比对概览")
    w("")
    w("| 代码 | 类别 | 已跑 | 应跑 | 机械比对平均 |")
    w("|---|---|---|---|---|")
    for code, meta in cat_meta.items():
        rows = [by_id[i["id"]] for i in items if i["cat"] == code and i["id"] in by_id]
        avg = (sum(r["auto_ratio"] for r in rows if r["auto_detail"] != "人工看")
               / max(1, len([r for r in rows if r["auto_detail"] != "人工看"])))
        w(f"| `{code}` | {meta['name']} | {len(rows)} | {meta['count']} | "
          f"{avg:.0%} |")
    w("")
    w("> 机械比对低不一定是答错。比如它答「长 39.8 米、高 7 米」，"
      "如果期望要点里还写了「青石」，而回答里只给了尺寸，机械比对就会显示缺一个。"
      "这类要人看一眼再定。")
    w("")

    # 逐条
    w("## 二、逐条记录")
    w("")
    cur_cat = None
    for item in items:
        rec = by_id.get(item["id"])
        if item["cat"] != cur_cat:
            cur_cat = item["cat"]
            meta = cat_meta.get(cur_cat, {})
            w(f"### {meta.get('name', cur_cat)}（{cur_cat}）")
            w("")
        w(f"**`{item['id']}`** {item['q']}")
        w("")
        w(f"- 期望要点：{item['expect']}")
        if not rec:
            w("- 系统回答：（还没跑）")
            w("")
            continue
        for a in rec["answers"]:
            label = f"轮 {a['turn']}" if len(rec["answers"]) > 1 else "系统回答"
            text = (a["a"] or "（超时，无回答）").replace("\n", " ").strip()
            w(f"- {label}：{text}")
            tools = a.get("tools") or []
            if tools:
                desc = "；".join(
                    f"{t['plugin']}/{t['tool']}"
                    + ("✓" if t.get("reliable") else "✗")
                    + ("（" + "、".join(t["hit"]) + "）" if t.get("hit") else "")
                    for t in tools)
                w(f"  - 调用的知识库：{desc}")
        w(f"- 机械比对：{rec['auto_detail']}")
        w(f"- 人工判定：{rec['verdict'] or '（待填）'} {rec['comment']}")
        w("")

    return "\n".join(out) + "\n"


def main() -> int:
    _fix_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", help="只跑这些题号，逗号分隔")
    ap.add_argument("--cat", help="只跑这个类别（F/R/O/B/G/M）")
    ap.add_argument("--limit", type=int, help="最多跑多少条")
    ap.add_argument("--all", action="store_true", help="跑全部")
    ap.add_argument("--resume", action="store_true", help="跳过已经跑过的")
    ap.add_argument("--render-only", action="store_true", help="只重新生成 md")
    args = ap.parse_args()

    items = load_items()

    if args.render_only:
        OUT_MD.write_text(render_md(items, read_raw()), encoding="utf-8")
        print("已重新生成：", OUT_MD)
        return 0

    picked = items
    if args.ids:
        want = {s.strip().upper() for s in args.ids.split(",") if s.strip()}
        picked = [i for i in items if i["id"].upper() in want]
    elif args.cat:
        picked = [i for i in items if i["cat"].upper() == args.cat.upper()]
    elif not args.all:
        print("没指定范围。用 --ids / --cat / --all，或 --render-only。")
        return 1

    if args.resume:
        done = load_done()
        picked = [i for i in picked if i["id"] not in done]

    if args.limit:
        picked = picked[:args.limit]

    if not picked:
        print("没有要跑的了。")
        return 0

    print(f"准备跑 {len(picked)} 条 → {FAY_BASE}（用户 {USERNAME}）")
    started = time.time()
    for n, item in enumerate(picked, 1):
        print(f"[{n}/{len(picked)}] {item['id']} {item['q'][:46]}", flush=True)
        try:
            rec = run_one(item)
        except KeyboardInterrupt:
            print("\n手动中断，已跑完的记录都保住了。")
            break
        except Exception as exc:                      # noqa: BLE001
            print(f"    出错：{type(exc).__name__}: {exc}", flush=True)
            rec = {"id": item["id"], "cat": item["cat"], "q": item["q"],
                   "expect": item["expect"], "note": item.get("note") or "",
                   "answers": [], "auto_detail": "出错", "auto_ratio": 0.0,
                   "verdict": "", "comment": f"运行出错：{exc}",
                   "ran_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        append_raw(rec)
        OUT_MD.write_text(render_md(items, read_raw()), encoding="utf-8")

    spent = time.time() - started
    print(f"\n跑完 {len(picked)} 条，用时 {spent/60:.1f} 分钟")
    print("原始记录：", RAW_LOG)
    print("可读文档：", OUT_MD)
    return 0


if __name__ == "__main__":
    sys.exit(main())
