#!/usr/bin/env python3
"""把所有实验数据汇总成一份文档，专供写论文用。

为什么要是脚本而不是手写文档
----------------------------
论文里的数字要反复用（摘要、实验章节、结论），一旦手抄就迟早对不上：
知识块从 130 变成 132、命中率从 60% 变成 68%，文档里还留着旧数。
这里每个数字都从原始来源现采，跑一次就是当前真值。

采集哪些
--------
    知识库规模      每个插件的知识块数、语料指纹、嵌入模型、受保护条数
    图谱规模        实体数、关系数、实体类型分布、度数最高的实体
    检索性能        插件自身耗时（向量 / 图谱分别统计）
    端到端延迟      首字 / 完整 / 检索，按题型分组（读 latency-raw.jsonl）
    问答质量        六类题型的完成情况与机械比对（读 test-results.jsonl）
    管理端统计      对话量、知识库调用与命中率、拒答分析、知识缺口（读 admin-api）
    已知限制        数据源冲突、数据污染提醒

用法
----
    python collect_experiment_data.py            # 采集并写出 docs/experiment-data.md
    python collect_experiment_data.py --stdout   # 只打印，不写文件

注意：管理端统计里会混进测试集自身产生的问答（172 条），
      文档里会把这件事标出来——论文里如果引用这组数，必须一并说明。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DOCS = ROOT / "docs"
PLUGINS = ROOT / "plugins"
OUT = DOCS / "experiment-data.md"

ADMIN_API = "http://127.0.0.1:5174"
G = "{http://graphml.graphdrawing.org/xmlns}"


def _fix_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def api(path: str) -> dict:
    try:
        with urllib.request.urlopen(f"{ADMIN_API}{path}", timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return {}


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = q * (len(xs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


# ─────────────────────────────────────────────────────────────
# 各部分采集
# ─────────────────────────────────────────────────────────────

def collect_kb() -> list[dict]:
    rows = []
    for d in sorted(PLUGINS.iterdir()):
        if not d.is_dir():
            continue
        meta = read_json(d / "index" / "index_meta.json", {}) or {}
        corpus = read_json(d / "corpus" / "knowledge.json", {}) or {}
        uploaded = read_json(d / "corpus" / "uploaded.json", {}) or {}
        plugin = read_json(d / "plugin.json", {}) or {}
        chunks = corpus.get("chunks") or []
        up_chunks = uploaded.get("chunks") or []
        if not chunks and not up_chunks and not meta:
            continue
        infos: dict[str, int] = {}
        spots = set()
        for c in chunks + up_chunks:
            it = c.get("info_type") or "未分类"
            infos[it] = infos.get(it, 0) + 1
            if c.get("spot_name"):
                spots.add(c["spot_name"])
        protected = sum(1 for c in chunks + up_chunks if c.get("protected"))
        rows.append({
            "slug": plugin.get("slug") or d.name,
            "name": plugin.get("name") or d.name,
            "chunks": len(chunks) + len(up_chunks),
            "chunks_from_file": len(chunks),
            "chunks_uploaded": len(up_chunks),
            "spots": len(spots),
            "protected": protected,
            "info_types": infos,
            "corpus_version": meta.get("corpus_version") or "",
            "embedding_model": meta.get("embedding_model") or "",
            "dim": meta.get("dim") or 0,
            "built_at": meta.get("built_at") or "",
        })
    return rows


def collect_graph() -> dict:
    files = list(PLUGINS.glob("kb-graph/graph/*.graphml"))
    if not files:
        return {}
    root = ET.parse(files[0]).getroot()

    nodes: dict[str, dict] = {}
    for nd in root.iter(G + "node"):
        data = {dd.get("key"): (dd.text or "") for dd in nd}
        nodes[nd.get("id")] = {"type": data.get("d1") or "UNKNOWN",
                               "desc": data.get("d2") or ""}

    degrees: dict[str, int] = {}
    edges = 0
    for e in root.iter(G + "edge"):
        edges += 1
        for end in (e.get("source"), e.get("target")):
            degrees[end] = degrees.get(end, 0) + 1

    types: dict[str, int] = {}
    for n in nodes.values():
        types[n["type"]] = types.get(n["type"], 0) + 1

    top = sorted(degrees.items(), key=lambda kv: -kv[1])[:10]
    return {
        "file": files[0].name,
        "nodes": len(nodes),
        "edges": edges,
        "types": types,
        "top_degree": [(k, v) for k, v in top],
    }


def collect_plugin_calls() -> dict:
    per_tool: dict[str, dict] = {}
    for f in sorted(PLUGINS.glob("*/logs/calls.jsonl")):
        plugin = f.parent.parent.name
        for rec in read_jsonl(f):
            tool = rec.get("tool") or "?"
            key = f"{plugin}/{tool}"
            row = per_tool.setdefault(key, {"n": 0, "ok": 0, "reliable": 0,
                                            "elapsed": [], "protected_hit": 0,
                                            "slow": 0})
            row["n"] += 1
            if rec.get("ok") is True:
                row["ok"] += 1
            if rec.get("reliable") is True:
                row["reliable"] += 1
            if rec.get("protected_hit"):
                row["protected_hit"] += 1
            ms = rec.get("elapsed_ms")
            if isinstance(ms, (int, float)):
                row["elapsed"].append(float(ms))
                if ms >= 1000:
                    row["slow"] += 1
    out = {}
    for k, v in per_tool.items():
        out[k] = {
            "n": v["n"], "ok": v["ok"], "reliable": v["reliable"],
            "protected_hit": v["protected_hit"],
            "elapsed_median": statistics.median(v["elapsed"]) if v["elapsed"] else 0,
            "elapsed_p90": pct(v["elapsed"], 0.9) if v["elapsed"] else 0,
            "elapsed_max": max(v["elapsed"]) if v["elapsed"] else 0,
            "elapsed_warm_median": statistics.median(
                [x for x in v["elapsed"] if x < 1000]) if any(
                x < 1000 for x in v["elapsed"]) else 0,
            "slow": v["slow"],
        }
    return out


def collect_testset() -> dict:
    items = (read_json(DOCS / "testset.json", {}) or {}).get("items") or []
    recs = read_jsonl(DOCS / "test-results.jsonl")
    by_id = {r["id"]: r for r in recs}
    per_cat: dict[str, dict] = {}
    for it in items:
        c = it["cat"]
        row = per_cat.setdefault(c, {"total": 0, "done": 0, "timeout": 0,
                                     "ratios": [], "verdicts": {}})
        row["total"] += 1
        r = by_id.get(it["id"])
        if not r:
            continue
        ans = r.get("answers") or []
        if ans and any(a.get("a") for a in ans):
            row["done"] += 1
            if r.get("auto_ratio") is not None and r.get("auto_detail") != "人工看":
                row["ratios"].append(r["auto_ratio"])
        else:
            row["timeout"] += 1
        v = (r.get("verdict") or "").strip() or "（待判定）"
        row["verdicts"][v] = row["verdicts"].get(v, 0) + 1
    return {"total": len(items), "records": len(recs), "per_cat": per_cat}


def collect_latency() -> dict:
    recs = read_jsonl(DOCS / "latency-raw.jsonl")
    if not recs:
        return {}
    per_cat: dict[str, dict] = {}
    for r in recs:
        c = r.get("cat") or "?"
        row = per_cat.setdefault(c, {"first": [], "final": [], "ret": []})
        if r.get("first_s") is not None:
            row["first"].append(float(r["first_s"]))
        if r.get("final_s") is not None:
            row["final"].append(float(r["final_s"]))
        if r.get("retrieval_ms"):
            row["ret"].append(float(r["retrieval_ms"]))

    def agg(row):
        return {
            "n": max(len(row["final"]), len(row["first"])),
            "first_med": statistics.median(row["first"]) if row["first"] else None,
            "final_med": statistics.median(row["final"]) if row["final"] else None,
            "final_p90": pct(row["final"], 0.9) if row["final"] else None,
            "final_min": min(row["final"]) if row["final"] else None,
            "final_max": max(row["final"]) if row["final"] else None,
            "ret_med": statistics.median(row["ret"]) if row["ret"] else None,
        }

    return {"per_cat": {k: agg(v) for k, v in per_cat.items()},
            "total": len(recs)}


def collect_admin() -> dict:
    return {
        "overview": api("/api/stats/overview"),
        "gaps": api("/api/stats/gaps"),
        "refusals": api("/api/stats/refusals"),
        "entities": api("/api/stats/entities"),
        "plugins": api("/api/plugins"),
    }


# ─────────────────────────────────────────────────────────────
# 渲染
# ─────────────────────────────────────────────────────────────

CAT_NAME = {"F": "事实型", "R": "关系型", "O": "概览型", "B": "边界题",
            "G": "知识缺口", "M": "多轮追问"}
LAT_NAME = {"CHAT": "闲聊（不查库）", "F": "事实型（向量检索）",
            "R": "关系型（图谱）", "O": "概览型（图谱）", "B": "边界题"}


def render(kb, graph, calls, testset, latency, admin) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    o = []
    w = o.append

    w("# 实验数据汇总（论文用）")
    w("")
    w(f"采集时间：**{now}**（全部数字由 `scripts/collect_experiment_data.py` 现采，可随时重跑刷新）")
    w("")
    w("这份文档是论文里所有数字的**唯一出处**。每个表下面都标了数据来源，"
      "正文引用时照抄，不要从别处再抄一份——两处数字对不上是最容易出的低级问题。")
    w("")
    w("配套文档：题目见 `test-questions.md`，逐条回答见 `test-results.md`，"
      "延迟明细见 `latency-report.md`。")
    w("")

    # ── 1 知识库规模 ──
    w("---")
    w("")
    w("## 一、知识库规模")
    w("")
    w("| 插件 | 知识块 | 景点/实体数 | 受保护块 | 语料指纹 | 嵌入模型 | 维度 |")
    w("|---|---|---|---|---|---|---|")
    total_chunks = 0
    for r in kb:
        total_chunks += r["chunks"]
        w(f"| {r['name']}（`{r['slug']}`） | {r['chunks']} | {r['spots']} | "
          f"{r['protected']} | `{r['corpus_version']}` | {r['embedding_model']} | {r['dim']} |")
    w(f"| **合计** | **{total_chunks}** |  |  |  |  |  |")
    w("")
    for r in kb:
        if r["chunks_uploaded"]:
            w(f"- `{r['slug']}` 中 {r['chunks_uploaded']} 块来自管理端上传（"
              f"{r['chunks_from_file']} 块来自资料包转换）")
    if any(r["chunks_uploaded"] for r in kb):
        w("")
    w("*数据来源：`plugins/<插件>/corpus/*.json` 与 `index/index_meta.json`*")
    w("")

    # 知识类型分布
    w("### 知识类型分布")
    w("")
    for r in kb:
        if not r["info_types"]:
            continue
        w(f"**{r['name']}**：" + "、".join(
            f"{k} {v}" for k, v in sorted(r["info_types"].items(), key=lambda kv: -kv[1])))
        w("")
    w("*这张表用来说明知识库是按「字段」结构化的，不是整篇文档切块。*")
    w("")

    # ── 2 图谱规模 ──
    if graph:
        w("---")
        w("")
        w("## 二、知识图谱规模")
        w("")
        w(f"- 实体 **{graph['nodes']}** 个，关系 **{graph['edges']}** 条"
          f"（平均每个实体 {graph['edges']/max(1,graph['nodes']):.1f} 条关系）")
        w(f"- 来源文件：`{graph['file']}`")
        w("")
        w("| 实体类型 | 数量 |")
        w("|---|---|")
        for k, v in sorted(graph["types"].items(), key=lambda kv: -kv[1]):
            w(f"| {k} | {v} |")
        w("")
        w("### 度数最高的实体（图谱的枢纽）")
        w("")
        w("| 实体 | 关联关系数 |")
        w("|---|---|")
        for name, deg in graph["top_degree"]:
            w(f"| {name} | {deg} |")
        w("")
        w("*数据来源：`plugins/kb-graph/graph/*.graphml`*")
        w("")

    # ── 3 检索性能 ──
    if calls:
        w("---")
        w("")
        w("## 三、检索性能（插件自身耗时，不含大模型）")
        w("")
        w("| 插件/工具 | 调用次数 | 成功 | 命中 | 受保护命中 | 耗时中位 | 去除冷启动后中位 | ≥1s 次数 | 最慢 |")
        w("|---|---|---|---|---|---|---|---|---|")
        for k, v in sorted(calls.items()):
            w(f"| `{k}` | {v['n']} | {v['ok']} | {v['reliable']} | {v['protected_hit']} | "
              f"{v['elapsed_median']:.0f}ms | {v['elapsed_warm_median']:.0f}ms | "
              f"{v['slow']} | {v['elapsed_max']:.0f}ms |")
        w("")
        w("> **⚠️ 报之前必须分开的两件事：**")
        w(">")
        w("> 「≥1s 次数」那些慢调用是**冷启动**——MCP 服务刚起来时要把嵌入模型加载进内存，"
          "第一次查询会慢到几秒；之后稳定在几十毫秒。")
        w(">")
        w("> 论文里应该报**去掉冷启动后的中位数**，并单独说明冷启动的存在。"
          "直接把最慢那一次当作「检索耗时」会把结论带偏——"
          "它既不能说明检索慢，也不能说明系统差，它只说明第一次要预热。")
        w("")
        w("> 这组数的真正用处是：**检索本身是几十毫秒级的，"
          "端到端十几秒的延迟不应该归因到检索层**。")
        w("")
        w("*数据来源：`plugins/<插件>/logs/calls.jsonl`（每次调用都埋点）*")
        w("")

    # ── 4 端到端延迟 ──
    w("---")
    w("")
    w("## 四、端到端延迟")
    w("")
    if not latency:
        w("**还没测。** 跑 `scripts/measure_latency.py` 后重新采集本文件。")
        w("")
        w("要测三个数，因为它们差一个数量级：")
        w("")
        w("| 指标 | 含义 |")
        w("|---|---|")
        w("| 首字延迟 | 点发送 → 屏幕上第一次出现东西 |")
        w("| 完整延迟 | 点发送 → 完整答案出现 |")
        w("| 检索耗时 | 知识库插件自己花的时间 |")
        w("")
    else:
        w(f"样本 {latency['total']} 次。")
        w("")
        w("| 题型 | 样本 | 首字中位 | 完整中位 | 完整 P90 | 最快 | 最慢 | 检索中位 |")
        w("|---|---|---|---|---|---|---|---|")
        for code, a in latency["per_cat"].items():
            name = LAT_NAME.get(code, code)
            fmt = lambda x, u="s": "—" if x is None else f"{x:.1f}{u}"
            w(f"| {name} | {a['n']} | {fmt(a['first_med'])} | {fmt(a['final_med'])} | "
              f"{fmt(a['final_p90'])} | {fmt(a['final_min'])} | {fmt(a['final_max'])} | "
              f"{fmt(a['ret_med'], 'ms')} |")
        w("")
        allf = [a["final_med"] for a in latency["per_cat"].values() if a["final_med"]]
        if allf:
            med = statistics.median(allf)
            rets = [a["ret_med"] for a in latency["per_cat"].values() if a["ret_med"]]
            ret = statistics.median(rets) / 1000 if rets else 0
            w(f"结论句可直接用：**一次回答平均约 {med:.1f} 秒，"
              f"其中知识库检索只占 {ret:.2f} 秒（{ret/max(med,0.01):.1%}），"
              f"其余为大模型生成。**")
            w("")
        w("*数据来源：`docs/latency-raw.jsonl`，见 `latency-report.md`*")
        w("")

    # ── 5 问答质量 ──
    w("---")
    w("")
    w("## 五、问答质量（标准测试集）")
    w("")
    if not testset["total"]:
        w("题库还没建立。")
        w("")
    else:
        w(f"题库共 **{testset['total']}** 条，已完成 **{testset['records']}** 条。")
        w("")
        w("| 代码 | 题型 | 题数 | 已完成 | 超时/无回答 | 机械比对平均 | 人工判定 |")
        w("|---|---|---|---|---|---|---|")
        for code in ("F", "R", "O", "B", "G", "M"):
            row = testset["per_cat"].get(code)
            if not row:
                continue
            avg = (sum(row["ratios"]) / len(row["ratios"])) if row["ratios"] else None
            verdicts = "；".join(f"{k} {v}" for k, v in row["verdicts"].items())
            w(f"| `{code}` | {CAT_NAME.get(code, code)} | {row['total']} | {row['done']} | "
              f"{row['timeout']} | " + ("—" if avg is None else f"{avg:.0%}") + f" | {verdicts} |")
        w("")
        w("> **机械比对不是成绩。** 它只是把期望要点里的数字和关键词在回答里找了一遍，"
          "用来提示可能对不上的地方。论文里报的命中率必须以「人工判定」列为准。")
        w("")
        w("*数据来源：`docs/test-results.jsonl`，逐条内容见 `test-results.md`*")
        w("")

    # ── 6 管理端统计 ──
    ov = admin.get("overview") or {}
    if ov:
        w("---")
        w("")
        w("## 六、管理端运行时统计")
        w("")
        conv = ov.get("conversation") or {}
        kb = ov.get("kb_calls") or {}
        w("| 指标 | 数值 |")
        w("|---|---|")
        w(f"| 累计对话消息 | {conv.get('total_messages', 0)} |")
        w(f"| 游客提问 | {conv.get('user_messages', 0)} |")
        w(f"| 活跃用户 | {conv.get('active_users', 0)} |")
        w(f"| 知识库调用 | {kb.get('total', 0)} 次（成功 {kb.get('ok', 0)}）|")
        w(f"| 知识库命中率 | {float(kb.get('reliable_rate') or 0):.1%} |")
        w(f"| 知识库调用平均耗时 | {kb.get('avg_elapsed_ms', 0)} ms |")
        w("")
        w("> ⚠️ **这组数含测试数据**：标准测试集的 172 条问答也写进了同一个对话记录，"
          "所以「对话消息数」不等于真实游客量。论文里引用这组数必须一并说明，"
          "或者只引用 `logs/calls.jsonl` 里剔除测试时段后的数据。")
        w("")

    g = admin.get("gaps") or {}
    if g.get("categories"):
        w("### 知识缺口分析（按问题类别）")
        w("")
        w("| 类别 | 被问 | 命中率 | 现有知识块 | 级别 |")
        w("|---|---|---|---|---|")
        for r in g["categories"]:
            w(f"| {r.get('category')} | {r.get('query_count')} | "
              f"{float(r.get('hit_rate') or 0):.0%} | {r.get('kb_chunks')} | {r.get('gap_level')} |")
        w("")

    rf = admin.get("refusals") or {}
    if rf:
        w("### 拒答分析")
        w("")
        w(f"- 问答配对 {rf.get('total_pairs', 0)} 组，拒答 {rf.get('refusal_count', 0)} 次"
          f"（{float(rf.get('refusal_rate') or 0):.1%}）")
        w(f"- 其中 **知识缺口拒答 {rf.get('knowledge_gap_count', 0)} 次**（要补知识）、"
          f"**域外拒答 {rf.get('out_of_domain_count', 0)} 次**（正确行为）")
        w("")
        w("> 判据：域外问题（问天气、写代码）拒答是对的；"
          "知识库确实没有、而游客又问到了，才算知识缺口。")
        w("")

    # ── 7 已知限制 ──
    w("---")
    w("")
    w("## 七、已知限制与要如实说明的地方")
    w("")
    w("论文里主动写出这些，比被评委问出来要好。")
    w("")
    w("1. **数据源冲突**：九龙灌浴总高（27.2m / 27.5m）、耗铜量（180t / 260t），"
      "灵山大佛铜壁板（2000 块 / 1560 块）——资料包内部两套来源不一致，"
      "答出任一都算对，但要在实验章节说明。")
    w("2. **游客行为分析数据是跨景区通用数据**（含宁波方特等），"
      "定位是行业参考，不是灵山运营数据，只用于演示管理端分析能力。")
    w("3. **测试集数据混入运行时统计**：见第六节提醒。")
    w("4. **数字人的语料来自公开资料包**，非景区官方口径，票价等信息需以官方发布为准。")
    w("5. **知识写入必须人工审核**，系统只做「提示该补哪一块」，不自动写入。")
    w("")

    # ── 8 数据来源对照 ──
    w("---")
    w("")
    w("## 八、数据来源对照表")
    w("")
    w("| 数据 | 原始来源 | 怎么重跑 |")
    w("|---|---|---|")
    w("| 知识块数 / 语料指纹 | `plugins/<插件>/corpus/*.json`、`index/index_meta.json` | "
      "`python scripts/build_index.py`（在插件目录下）|")
    w("| 图谱规模 | `plugins/kb-graph/graph/*.graphml` | 重建图谱后自动更新 |")
    w("| 检索耗时 | `plugins/<插件>/logs/calls.jsonl` | 每次调用自动埋点，无需重跑 |")
    w("| 端到端延迟 | `docs/latency-raw.jsonl` | `python scripts/measure_latency.py` |")
    w("| 问答质量 | `docs/test-results.jsonl` | `python scripts/run_testset.py --all` |")
    w("| 题目与类别 | `docs/testset.json` | 改题后 `python scripts/render_testset.py` |")
    w("| 管理端统计 | admin-api `/api/stats/*` | 保证 5174 端口在跑，重跑本脚本 |")
    w("")
    w("刷新本文档：")
    w("")
    w("```powershell")
    w("cd D:\\PROJECT\\scenic_ai\\重构数字人\\scripts")
    w("& 'D:\\PROJECT\\scenic_ai\\重构数字人\\Fay-main\\.venv\\Scripts\\python.exe' collect_experiment_data.py")
    w("```")
    w("")

    return "\n".join(o) + "\n"


def main() -> int:
    _fix_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--stdout", action="store_true", help="只打印，不写文件")
    args = ap.parse_args()

    print("采集知识库规模…")
    kb = collect_kb()
    print("采集图谱规模…")
    graph = collect_graph()
    print("采集插件调用埋点…")
    calls = collect_plugin_calls()
    print("采集测试集进度…")
    testset = collect_testset()
    print("采集延迟数据…")
    latency = collect_latency()
    print("采集管理端统计（需要 5174 端口在跑）…")
    admin = collect_admin()

    text = render(kb, graph, calls, testset, latency, admin)
    if args.stdout:
        print(text)
    else:
        OUT.write_text(text, encoding="utf-8")
        print("已写出：", OUT)
        print(f"  知识库 {len(kb)} 个插件 / 图谱 {graph.get('nodes', 0)} 实体 / "
              f"测试集 {testset['records']}/{testset['total']} / "
              f"延迟样本 {latency.get('total', 0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
