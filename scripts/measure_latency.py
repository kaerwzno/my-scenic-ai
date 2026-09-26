#!/usr/bin/env python3
"""端到端延迟测量 —— 用户到底等了多久。

测三个数，分开测
----------------
    首字延迟    用户点发送 → 屏幕上第一次出现东西（「我来帮你查一下，稍等…」）
    完整延迟    用户点发送 → 完整答案出现
    检索耗时    这一次回答里，知识库插件自己花掉的时间（向量 / 图谱）

为什么不能只报一个"响应时间"
----------------------------
因为这三个数差了一个数量级，混在一起报哪个都不对：
    首字 ~3 秒（体感"它理我了"）
    完整 ~15 秒（体感"它答完了"）
    检索 ~0.02-0.7 秒（几乎全是白送的，瓶颈根本不在这）
答辩被问「你这系统快不快」，能答出这个拆解，比说"挺快的"有用得多。

计时口径
--------
以 Fay 消息落库时间为准（轮询 0.4 秒一次，所以误差在 ±0.4 秒内）。
前端走 WebSocket 可能比落库早一点点，这个差在亚秒级，不影响结论。

用法
----
    python measure_latency.py                    # 默认样本，每类 2 条，跑 2 轮
    python measure_latency.py --repeat 3         # 跑 3 轮取更稳的分布
    python measure_latency.py --per-cat 3        # 每类抽 3 条
    python measure_latency.py --report-only      # 只重新生成报告

⚠️ 别和 run_testset.py 同时跑：两个脚本都在读同一张消息表，
   会互相把对方的问题当成自己的回答。脚本开头有检查。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib import error

import run_testset as rt          # 复用发消息 / 读消息 / 判「答完了没」的逻辑

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RAW_LOG = ROOT / "docs" / "latency-raw.jsonl"
OUT_MD = ROOT / "docs" / "latency-report.md"
RUN_LOG = ROOT / "logs" / "testset-run.log"

POLL = 0.4                        # 计时精度靠它，别调大
QUIET_AFTER_FINAL = 3.0
MAX_WAIT = 180.0

# 不查知识库的闲聊，用来当"最快的下限"基线
CHAT_QUESTIONS = ["你好", "你叫什么名字？", "谢谢你"]


def _fix_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def pct(values: list[float], q: float) -> float:
    """分位数（线性插值那种，够用）。"""
    if not values:
        return 0.0
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = q * (len(xs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def other_script_running() -> bool:
    """run_testset 是否正在跑。同时在跑会把两次测量搅在一起。"""
    if not RUN_LOG.is_file():
        return False
    return (time.time() - RUN_LOG.stat().st_mtime) < 30


# ─────────────────────────────────────────────────────────────
# 一次性问一句，把三个时间点都记下来
# ─────────────────────────────────────────────────────────────

_floor = 0


def ask_timed(msg: str) -> dict:
    """发一句，返回 {首字延迟, 完整延迟, 回答, 检索耗时, 检索明细}。"""
    global _floor

    marks = rt.read_call_marks()
    try:
        msgs = rt.fetch_messages()
        _floor = max(_floor, max((m["ts"] for m in msgs), default=0))
    except (error.URLError, OSError):
        pass

    t0 = time.perf_counter()
    rt._post(rt.FAY_BASE, "/api/send",
             {"data": json.dumps({"username": rt.USERNAME, "msg": msg}, ensure_ascii=False)})

    t_first: float | None = None
    t_final: float | None = None
    seen_final_at: float | None = None
    collected: list[dict] = []
    signature: tuple = ()

    while (time.perf_counter() - t0) < MAX_WAIT:
        time.sleep(POLL)
        try:
            fresh = [m for m in rt.fetch_messages()
                     if m["ts"] > _floor and not rt.is_user_msg(m["type"])
                     and m["content"].strip()]
        except (error.URLError, OSError, TimeoutError):
            continue

        now = time.perf_counter()
        sig = tuple((m["ts"], m["content"]) for m in fresh)
        changed = sig != signature
        if fresh and t_first is None:
            t_first = now - t0
        if changed:
            signature = sig
            collected = fresh
            if fresh and rt.looks_final(fresh[-1]["content"]):
                t_final = now - t0
                seen_final_at = now
        if seen_final_at and (now - seen_final_at) >= QUIET_AFTER_FINAL:
            break

    if collected:
        _floor = max(m["ts"] for m in collected)

    calls = rt.summarize_calls(marks)
    all_calls = []
    for f in sorted(rt.PLUGIN_DIR.glob("*/logs/calls.jsonl")):
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines[marks.get(str(f), 0):]:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(rec.get("tool") or "").endswith("_query"):
                all_calls.append({"plugin": f.parent.parent.name,
                                  "tool": rec.get("tool"),
                                  "elapsed_ms": rec.get("elapsed_ms"),
                                  "ok": rec.get("ok")})

    return {
        "q": msg,
        "first_s": round(t_first, 2) if t_first is not None else None,
        "final_s": round(t_final, 2) if t_final is not None else None,
        "answer": rt.clean_answer("\n".join(m["content"] for m in collected))[:400],
        "retrieval_ms": sum(c["elapsed_ms"] or 0 for c in all_calls),
        "calls": all_calls,
    }


# ─────────────────────────────────────────────────────────────
# 取样
# ─────────────────────────────────────────────────────────────

def build_sample(per_cat: int, include_chat: bool) -> list[dict]:
    items = json.loads(rt.TEST_SET.read_text(encoding="utf-8"))["items"]
    picked: list[dict] = []

    if include_chat:
        for q in CHAT_QUESTIONS[:max(1, per_cat)]:
            picked.append({"id": "CHAT", "cat": "CHAT", "q": q})

    for code in ("F", "R", "O", "B"):
        group = [i for i in items if i["cat"] == code]
        # 均匀抽：跨首尾取，别都落在同一个景点上
        if per_cat >= len(group):
            take = group
        else:
            step = len(group) / per_cat
            take = [group[int(i * step)] for i in range(per_cat)]
        picked.extend({"id": i["id"], "cat": code, "q": i["q"]} for i in take)

    return picked


CAT_NAME = {"CHAT": "闲聊（不查库）", "F": "事实型（向量检索）", "R": "关系型（图谱）",
            "O": "概览型（图谱）", "B": "边界题"}


# ─────────────────────────────────────────────────────────────
# 跑
# ─────────────────────────────────────────────────────────────

def read_raw() -> list[dict]:
    if not RAW_LOG.is_file():
        return []
    out = []
    for line in RAW_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def render(records: list[dict]) -> str:
    by_cat: dict[str, list[dict]] = {}
    for r in records:
        by_cat.setdefault(r["cat"], []).append(r)

    out: list[str] = []
    w = out.append
    w("# 端到端延迟测量")
    w("")
    w(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ｜ 样本 {len(records)} 次")
    w("")
    w("三个数分开看，因为它们差一个数量级，混在一起报哪个都不对。")
    w("")
    w("| 指标 | 含义 | 用户体感 |")
    w("|---|---|---|")
    w("| **首字延迟** | 点发送 → 屏幕上第一次出现东西 | 「它理我了」 |")
    w("| **完整延迟** | 点发送 → 完整答案出现 | 「它答完了」 |")
    w("| **检索耗时** | 知识库插件自己花的时间 | 用户看不到，但决定了瓶颈在哪 |")
    w("")
    w("> 计时以 Fay 消息落库为准，轮询 0.4 秒一次，误差 ±0.4 秒。"
      "前端走 WebSocket 可能再早一点点，亚秒级，不影响结论。")
    w("")

    w("## 一、按题型汇总")
    w("")
    w("| 题型 | 样本 | 首字中位 | 完整中位 | 完整 P90 | 检索中位 |")
    w("|---|---|---|---|---|---|")
    order = ["CHAT", "F", "R", "O", "B"]
    for code in order + [c for c in by_cat if c not in order]:
        rows = by_cat.get(code) or []
        firsts = [r["first_s"] for r in rows if r.get("first_s") is not None]
        finals = [r["final_s"] for r in rows if r.get("final_s") is not None]
        rets = [r["retrieval_ms"] for r in rows if r.get("retrieval_ms")]
        if not rows:
            continue
        name = CAT_NAME.get(code, code)
        med_first = f"{statistics.median(firsts):.1f}s" if firsts else "—"
        med_final = f"{statistics.median(finals):.1f}s" if finals else "—"
        p90_final = f"{pct(finals, 0.9):.1f}s" if finals else "—"
        med_ret = f"{statistics.median(rets):.0f}ms" if rets else "—"
        w(f"| {name} | {len(rows)} | {med_first} | {med_final} | {p90_final} | {med_ret} |")
    w("")

    allf = [r["final_s"] for r in records if r.get("final_s") is not None]
    allfirst = [r["first_s"] for r in records if r.get("first_s") is not None]
    allret = [r["retrieval_ms"] for r in records if r.get("retrieval_ms")]
    if allf:
        w(f"**全部样本**：首字中位 {statistics.median(allfirst):.1f} 秒，"
          f"完整中位 {statistics.median(allf):.1f} 秒、"
          f"最快 {min(allf):.1f} 秒、最慢 {max(allf):.1f} 秒；"
          + (f"检索中位 {statistics.median(allret):.0f} 毫秒。" if allret else ""))
        w("")
        total = statistics.median(allf)
        ret = statistics.median(allret) / 1000 if allret else 0
        if total > 0:
            w(f"也就是说：一次回答约 **{total:.1f} 秒**，其中检索只占 "
              f"**{ret:.2f} 秒（{ret/total:.1%}）**，"
              f"剩下 {total-ret:.1f} 秒全在大模型上。瓶颈不在检索。")
            w("")

    w("## 二、逐次记录")
    w("")
    for code in order + [c for c in by_cat if c not in order]:
        rows = by_cat.get(code) or []
        if not rows:
            continue
        w(f"### {CAT_NAME.get(code, code)}（{code}）")
        w("")
        w("| 题号 | 问题 | 首字 | 完整 | 检索 | 调用的插件 |")
        w("|---|---|---|---|---|---|")
        for r in rows:
            plugins = "、".join(sorted({c["plugin"] for c in (r.get("calls") or [])})) or "（无）"
            first = f"{r['first_s']:.1f}s" if r.get("first_s") is not None else "—"
            final = f"{r['final_s']:.1f}s" if r.get("final_s") is not None else "超时"
            ret = f"{r['retrieval_ms']}ms" if r.get("retrieval_ms") else "—"
            q = r["q"].replace("|", "\\|")[:40]
            w(f"| `{r['id']}` | {q} | {first} | {final} | {ret} | {plugins} |")
        w("")

    w("## 三、这些数字怎么用")
    w("")
    w("1. 论文里报**两个**延迟，别只报一个：首字延迟（有没有反应）和完整延迟（答案多久到）。")
    w("2. 检索耗时单独列，用来说明「慢的不是检索，是大模型」——这直接引出优化方向。")
    w("3. 闲聊和查库分开报，两者的差距就是「查知识库的代价」。")
    w("4. 想改进的话，按这个顺序：先减大模型往返次数（意图判断别交给大模型），"
      "再考虑流式输出接语音合成，最后才是换模型。")
    w("")
    return "\n".join(out) + "\n"


def main() -> int:
    _fix_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=2, help="每条问题重复几次（默认 2）")
    ap.add_argument("--per-cat", type=int, default=2, help="每类抽几条（默认 2）")
    ap.add_argument("--no-chat", action="store_true", help="不测闲聊基线")
    ap.add_argument("--report-only", action="store_true", help="只重新生成报告")
    args = ap.parse_args()

    if args.report_only:
        OUT_MD.write_text(render(read_raw()), encoding="utf-8")
        print("已重新生成：", OUT_MD)
        return 0

    if other_script_running():
        print("⚠️ run_testset.py 好像还在跑（日志 30 秒内有更新）。")
        print("   两个脚本会抢同一张消息表，测出来的数不能用。等它跑完再来。")
        return 1

    sample = build_sample(args.per_cat, not args.no_chat)
    total = len(sample) * args.repeat
    est = total * 20 / 60
    print(f"样本 {len(sample)} 条 × {args.repeat} 轮 = {total} 次，"
          f"预计 {est:.0f} 分钟左右。")
    print("（跑的时候别在网页端跟数字人聊天，会串成它的回答。）")
    print()

    done = 0
    started = time.time()
    for rep in range(1, args.repeat + 1):
        for item in sample:
            done += 1
            print(f"[{done}/{total}] 第{rep}轮 {item['id']} {item['q'][:36]}", flush=True)
            try:
                rec = ask_timed(item["q"])
            except KeyboardInterrupt:
                print("\n手动中断，已测的都在。")
                OUT_MD.write_text(render(read_raw()), encoding="utf-8")
                return 0
            except Exception as exc:                      # noqa: BLE001
                print(f"    出错：{type(exc).__name__}: {exc}", flush=True)
                continue
            rec.update({"id": item["id"], "cat": item["cat"], "repeat": rep,
                        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
            with RAW_LOG.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f = f"{rec['first_s']:.1f}s" if rec["first_s"] is not None else "—"
            s = f"{rec['final_s']:.1f}s" if rec["final_s"] is not None else "超时"
            print(f"      首字 {f}  完整 {s}  检索 {rec['retrieval_ms']}ms", flush=True)
            OUT_MD.write_text(render(read_raw()), encoding="utf-8")
            time.sleep(1.0)

    print(f"\n测完 {done} 次，用时 {(time.time()-started)/60:.1f} 分钟")
    print("原始记录：", RAW_LOG)
    print("可读报告：", OUT_MD)
    return 0


if __name__ == "__main__":
    sys.exit(main())
