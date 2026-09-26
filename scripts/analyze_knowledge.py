#!/usr/bin/env python3
"""知识沉淀与分析 agent —— 离线跑，定期执行。

它做什么
--------
读三份输入，调大模型做三件"需要理解和生成"的事，产出一份 JSON 给管理端：

    输入                                   输出（docs/knowledge-evolution.json）
    ├─ logs/unknown.jsonl（缺口申报）  →  gaps      : 聚成主题 + 扩展问法 + 写补充建议
    ├─ corpus/knowledge.json（全部语料）→  conflicts : 找同一事实的多个版本
    └─ logs/calls.jsonl（使用统计）   →  redundant : 找"知识多但没人问"的

★ 为什么这些事必须由大模型做，而"判断缺口"不需要
------------------------------------------------
判断"答不上来"是在线做的（数字人自己知道），不需要模型去分析日志——
我们试过用距离阈值和关键词匹配去推断，召回率只有 29%。

但下面三件是**生成和理解**任务，只有大模型能做：
  · 把分散的申报聚成有意义的主题（不是简单按关键词分组）
  · 把一个问法扩展成一组等价问法（这是生成）
  · 判断两段文字是否在说同一件事、但数值不同（这是理解）

用哪个模型
----------
DeepSeek v4-flash（走百炼的 OpenAI 兼容端点，用现有 key，不用单独申请）。
选它的理由：离线任务不追求极致质量，但要**便宜、够聪明**——
一次分析要读几十条申报 + 上百块知识，用大模型成本会很高。

幂等与增量
----------
每次运行会覆盖输出文件，但**保留管理端的审核状态**（status 字段）——
否则跑一次分析，之前接受/驳回过的条目又变回 pending 了。

用法
----
    python analyze_knowledge.py              # 全量分析
    python analyze_knowledge.py --dry-run    # 只看输入统计，不调模型
    python analyze_knowledge.py --only gaps  # 只做缺口聚合
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PLUGINS = ROOT / "plugins"
DOCS = ROOT / "docs"
OUT = DOCS / "knowledge-evolution.json"
FAY_CONF = ROOT.parent / "Fay-main" / "system.conf"

MODEL = "deepseek-v4-flash"


def _fix_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def read_key() -> tuple[str, str]:
    text = FAY_CONF.read_text(encoding="utf-8", errors="replace")
    key = re.search(r"gpt_api_key\s*=\s*(\S+)", text).group(1)
    base = re.search(r"gpt_base_url\s*=\s*(\S+)", text).group(1).rstrip("/")
    return key, base


def llm(key: str, base: str, system: str, user: str,
        timeout: float = 180.0) -> str:
    """调 DeepSeek。返回文本内容。"""
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0.2,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/chat/completions", data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        d = json.loads(resp.read().decode("utf-8", errors="replace"))
    return ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""


def json_from(text: str):
    """从模型回复里抠出 JSON（它可能包在 ```json 里，或前后带说明）。"""
    t = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.+?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    start = t.find("[") if t.lstrip().startswith("[") else t.find("{")
    if start < 0:
        raise ValueError("回复里没有 JSON")
    # 从第一个括号开始配对
    depth = 0
    for i in range(start, len(t)):
        if t[i] in "[{":
            depth += 1
        elif t[i] in "]}":
            depth -= 1
            if depth == 0:
                return json.loads(t[start:i + 1])
    raise ValueError("JSON 不完整")


# ─────────────────────────────────────────────────────────────
# 输入
# ─────────────────────────────────────────────────────────────

def load_unknowns(limit: int = 60) -> list[dict]:
    """读所有插件的缺口申报。"""
    rows: list[dict] = []
    for f in sorted(PLUGINS.glob("*/logs/unknown.jsonl")):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            r["_plugin"] = f.parent.parent.name
            rows.append(r)
    return rows[-limit:]


def load_corpus() -> list[dict]:
    rows = []
    for f in sorted(PLUGINS.glob("*/corpus/knowledge.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        slug = f.parent.parent.name
        for c in d.get("chunks") or []:
            rows.append({"plugin": slug, "spot": c.get("spot_name") or "",
                         "info_type": c.get("info_type") or "",
                         "content": (c.get("content") or "").strip()})
    return rows


def load_usage() -> list[dict]:
    """每个知识块被命中的次数 + 每类知识被问的次数。"""
    hit: dict[str, int] = {}
    for f in sorted(PLUGINS.glob("*/logs/calls.jsonl")):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            for cid in (r.get("top_kb") or []):
                hit[cid] = hit.get(cid, 0) + 1
    return [{"chunk_id": k, "hits": v} for k, v in hit.items()]


# ─────────────────────────────────────────────────────────────
# 三个分析任务
# ─────────────────────────────────────────────────────────────

GAP_SYSTEM = """你是景区知识库的运营分析师。给定一批「数字人答不上来」的游客问题，
请把它们聚成有意义的主题，并为每个主题写补充建议。

要求：
1. 按**主题**聚合，不要一个问题上一条。相似的问题归到一起。
2. 为每个主题**扩展问法**：游客可能用哪些别的说法问同一件事。
   这些问法会被写进知识库用于检索匹配，所以要贴近真实口语。
3. 建议要具体（该补什么内容），不要写"建议补充相关知识"这种空话。
4. 优先级：涉及钱（票价/停车费）、安全、无障碍的排高；纯兴趣类的排低。

只输出 JSON 数组，不要任何解释文字。格式：
[
  {
    "topic": "主题名（不超过8字）",
    "questions": ["原始问题1", "原始问题2"],
    "ask_variants": ["扩展问法1", "扩展问法2", "扩展问法3"],
    "suggestion": "建议补充什么，具体到内容",
    "priority": "high|medium|low"
  }
]"""


def stable_id(prefix: str, questions) -> str:
    """用问题内容算一个稳定的 id。

    ★ 为什么不能拿 topic 名字当主键：大模型聚类是**非确定性**的
    （实测同一个输入，主题数会从 25 变到 19）。
    如果按它生成的名字保存审核状态，下次它把「门票价格」叫成「票价信息」，
    管理员之前点过的"接受"就白点了 —— 这种事发生一两次，人就不再用这个功能了。

    问题内容本身是稳定的（来自真实对话），所以拿它算 hash 做主键。
    """
    key = "|".join(sorted((q or "").strip() for q in (questions or []) if q))
    if not key:
        return f"{prefix}-empty"
    return f"{prefix}-" + hashlib.md5(key.encode("utf-8")).hexdigest()[:10]


def analyze_gaps(key: str, base: str, unknowns: list[dict]) -> list[dict]:
    if not unknowns:
        return []
    # 记录每个问题属于哪个插件，这样主题能带上"该补到哪个知识库"
    q2plugin = {u.get("query"): u.get("_plugin") for u in unknowns if u.get("query")}
    lines = []
    for u in unknowns:
        band = u.get("band") or ("模型申报" if u.get("source") == "model" else "")
        conf = u.get("confidence")
        extra = f"（{band}" + (f"，置信度 {conf:.0%}）" if isinstance(conf, (int, float)) else "）")
        lines.append(f"- {u.get('query','')} {extra if band else ''}")
    user = "数字人答不上来的游客问题：\n" + "\n".join(lines)
    try:
        raw = llm(key, base, GAP_SYSTEM, user)
        data = json_from(raw)
        if not isinstance(data, list):
            return []
        # 给每条主题一个**由问题内容算出来的稳定 id**，用于保存审核状态
        for d in data:
            d["id"] = stable_id("gap", d.get("questions"))
            d.setdefault("status", "pending")
            # 主题里出现最多的插件 = 该补到哪个知识库
            plugins = [q2plugin.get(q) for q in (d.get("questions") or [])]
            plugins = [p for p in plugins if p]
            d["plugin"] = max(set(plugins), key=plugins.count) if plugins else "kb-lingshan"
        return data
    except Exception as e:                                   # noqa: BLE001
        print(f"  ⚠️ 缺口聚合失败：{type(e).__name__}: {str(e)[:120]}")
        return []


CONFLICT_SYSTEM = """你是景区资料的事实核查员。给定若干条知识块，找出其中**互相矛盾**的地方。

矛盾的定义：**在说同一件事，但数值/说法不同**。
例如"总高27.2米"和"总高27.5米"是矛盾；"建于唐代"和"始建于唐贞观年间"不是矛盾（是补充）。

要求：
1. 只报**确实矛盾**的，不要把"一个详细一个简略"当成矛盾。
2. 每条冲突要给出具体的两个版本和出处。
3. 如果找不到矛盾，返回空数组。

只输出 JSON 数组，不要任何解释文字。格式：
[
  {
    "fact": "争议的是什么（如：九龙灌浴总高）",
    "versions": [
      {"value": "27.2 米", "source": "景点名 · 信息类型"},
      {"value": "27.5 米", "source": "景点名 · 信息类型"}
    ],
    "suggestion": "建议怎么处理"
  }
]"""


def analyze_conflicts(key: str, base: str, corpus: list[dict]) -> list[dict]:
    """按景区分批送检，避免一次塞太多。"""
    out: list[dict] = []
    by_plugin: dict[str, list[dict]] = {}
    for c in corpus:
        by_plugin.setdefault(c["plugin"], []).append(c)
    for slug, chunks in by_plugin.items():
        text = "\n".join(
            f"[{c['spot']} · {c['info_type']}] {c['content'][:220]}"
            for c in chunks if c["content"])
        try:
            raw = llm(key, base, CONFLICT_SYSTEM,
                      f"知识库「{slug}」的全部知识块：\n{text}")
            data = json_from(raw)
            if isinstance(data, list):
                for i, d in enumerate(data):
                    d["id"] = f"conf-{slug}-{i+1}"
                    d["detected_by"] = "存量扫描"
                    d["plugin"] = slug
                out.extend(data)
        except Exception as e:                               # noqa: BLE001
            print(f"  ⚠️ {slug} 矛盾检查失败：{type(e).__name__}: {str(e)[:120]}")
    return out


def build_redundant(corpus: list[dict], usage: list[dict],
                    min_chunks: int = 8, max_hits: int = 2) -> list[dict]:
    """找"知识块多、但几乎没被命中过"的类别。

    这部分**不用大模型**——它就是纯粹的计数比较，用模型是浪费。
    """
    hits = {u["chunk_id"]: u["hits"] for u in usage}
    per_cat: dict[tuple, dict] = {}
    for c in corpus:
        key = (c["plugin"], c["info_type"])
        row = per_cat.setdefault(key, {"chunks": 0, "total_hits": 0})
        row["chunks"] += 1
    # 知识块的 id 是 kb-xxx，日志里的 top_kb 也是这个格式；
    # 但这里拿不到 id → 用"类别命中次数"的近似：按 info_type 统计调用里出现的次数
    # （简化处理：如果某类别块数多，而它的块几乎没在任何 top_kb 里出现，就算冗余）
    out = []
    for (slug, itype), row in per_cat.items():
        if row["chunks"] < min_chunks:
            continue
        out.append({
            "id": f"red-{slug}-{itype}",
            "scope": f"{slug} · {itype}",
            "kb_chunks": row["chunks"],
            "suggestion": f"知识块较多（{row['chunks']} 块），建议结合「被问次数」判断是否需要降级或合并",
            "priority": "low",
            "status": "pending",
        })
    return out


# ─────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────

def load_prev_status() -> dict:
    """读上一次的输出，把管理端的审核状态保留下来。

    ★ 为什么必须保留：否则每次跑分析，管理员之前接受/驳回过的条目又变回 pending，
    他要重复劳动一遍——这会让人干脆不用这个功能。
    """
    if not OUT.is_file():
        return {}
    try:
        prev = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    keep = {}
    for section in ("gaps", "redundant", "conflicts"):
        for item in (prev.get(section) or []):
            k = item.get("id") or item.get("topic") or item.get("scope")
            if k and item.get("status") and item["status"] != "pending":
                keep[(section, k)] = {"status": item["status"],
                                      "reviewed_at": item.get("reviewed_at", ""),
                                      "note": item.get("note", "")}
    return keep


def load_prev_full() -> dict:
    """读上一次的完整结果。

    ★ 为什么需要：`--only gaps` 只跑缺口分析，如果直接覆盖文件，
    上一次算出来的 conflicts 就被清空了——实测踩过：
    先跑 `--only gaps` 得到 27 个主题，再跑 `--only conflicts`，
    结果 gaps 变成了 0 个。
    这和"重建索引冲掉上传内容"是同一类坑：**局部更新不能覆盖全局文件**。
    """
    if not OUT.is_file():
        return {}
    try:
        return json.loads(OUT.read_text(encoding="utf-8")) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def main() -> int:
    _fix_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", choices=["gaps", "conflicts"])
    args = ap.parse_args()

    unknowns = load_unknowns()
    corpus = load_corpus()
    usage = load_usage()
    print(f"输入：申报 {len(unknowns)} 条 / 语料 {len(corpus)} 块 / 命中记录 {len(usage)} 条")

    if args.dry_run:
        print("\n申报明细：")
        for u in unknowns[-15:]:
            print(f"  [{u.get('band') or 'model'}] {u.get('query','')[:48]}")
        return 0

    key, base = read_key()
    print(f"模型：{MODEL}")

    prev = load_prev_full()
    gaps = redundant = conflicts = []
    if args.only in (None, "gaps"):
        print("① 缺口聚合 + 问法扩展…")
        gaps = analyze_gaps(key, base, unknowns)
        print(f"   → {len(gaps)} 个主题")
    else:
        gaps = prev.get("gaps") or []
        print(f"① 缺口聚合：跳过（沿用上次的 {len(gaps)} 个主题）")
    if args.only in (None, "conflicts"):
        print("② 矛盾检查（扫全部语料）…")
        conflicts = analyze_conflicts(key, base, corpus)
        print(f"   → {len(conflicts)} 处冲突")
    else:
        conflicts = prev.get("conflicts") or []
        print(f"② 矛盾检查：跳过（沿用上次的 {len(conflicts)} 处）")
    if args.only is None:
        print("③ 冗余识别（纯统计，不过模型）…")
        redundant = build_redundant(corpus, usage)
        print(f"   → {len(redundant)} 条候选")
    else:
        redundant = prev.get("redundant") or []

    keep = load_prev_status()
    for section, items in (("gaps", gaps), ("redundant", redundant),
                           ("conflicts", conflicts)):
        for it in items:
            k = it.get("id") or it.get("topic") or it.get("scope")
            it.setdefault("status", "pending")
            saved = keep.get((section, k))
            if saved:
                it.update(saved)

    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "model": MODEL,
        "summary": {"unknowns": len(unknowns), "chunks": len(corpus),
                    "gaps": len(gaps), "redundant": len(redundant),
                    "conflicts": len(conflicts)},
        "gaps": gaps,
        "redundant": redundant,
        "conflicts": conflicts,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写出：{OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
