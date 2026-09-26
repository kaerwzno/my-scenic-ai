#!/usr/bin/env python3
"""景区知识图谱 · MCP 插件（图谱增强问答）

它解决什么问题
--------------
向量检索擅长"精确问题 → 精确知识块"，但对**概览/关系类问题**无力——
比如「拈花湾有什么好玩的」，向量检索只会返回语义最像的一块（拈花广场），
而正确答案需要"把拈花湾下的景点聚起来"。

**这正是知识图谱的主场**：图谱里有"包含"和"相关"关系，可以顺着关系把一片内容捞出来。

设计要点
--------
· 插件**只给证据**，不生成答案 —— Fay 才是大脑（与 query_knowledge 的分工一致）
· 证据分两类：**实体说明**（这个实体是什么）+ **关系**（A 和 B 是什么关系）
· 图谱文件按 mtime 缓存，改了图谱不用重启

来源：逻辑移植自旧项目 `灵山AI导游系统/apps/graph_api/views.py`
     的 `_rank_nodes` / `_collect_graph_evidence` / `_node_payload`。

用法
----
    python server.py --selftest     # 直接自测
    python server.py                # 以 MCP stdio server 运行（由 Fay 拉起）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


def setup_console() -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:
            pass
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass


setup_console()

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "plugin.json"
LOG_PATH = HERE / "logs" / "calls.jsonl"

GRAPH_FILE = Path(
    os.getenv("GRAPH_FILE", str(HERE / "graph" / "graph_chunk_entity_relation.graphml"))
)

# LightRAG 的描述里用 <SEP> 分隔多段，必须清掉，否则会原样喂给 LLM
SEP_RE = re.compile(r"<SEP>|<sep>|[\r\n\|]+")
SPACE_RE = re.compile(r"\s+")

_graph = None
_graph_mtime = 0.0


def load_manifest() -> dict:
    data = {"slug": HERE.name.replace("-", "_"), "name": HERE.name, "description": ""}
    if MANIFEST.is_file():
        try:
            data.update(json.loads(MANIFEST.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    return data


MANIFEST_DATA = load_manifest()
QUERY_TOOL = f"{MANIFEST_DATA['slug']}_query"
STATS_TOOL = f"{MANIFEST_DATA['slug']}_stats"


def log_call(**fields: Any) -> None:
    """调用埋点（JSONL）。绝不抛异常——埋点失败不能影响问答主流程。"""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        fields["ts"] = datetime.now().isoformat(timespec="seconds")
        fields["plugin"] = MANIFEST_DATA.get("slug") or HERE.name
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(fields, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# 图谱加载（按 mtime 缓存）
# ─────────────────────────────────────────────────────────────

def get_graph():
    global _graph, _graph_mtime
    if not GRAPH_FILE.is_file():
        raise RuntimeError(f"图谱文件不存在：{GRAPH_FILE}")
    mtime = GRAPH_FILE.stat().st_mtime
    if _graph is None or mtime != _graph_mtime:
        import networkx as nx

        _graph = nx.read_graphml(str(GRAPH_FILE))
        _graph_mtime = mtime
    return _graph


# ─────────────────────────────────────────────────────────────
# 检索：从问题找到相关实体，再顺着关系捞证据
# ─────────────────────────────────────────────────────────────

def clean_text(value: Any, limit: int = 160) -> str:
    text = str(value or "").replace('"', "").replace("'", "").strip()
    text = SEP_RE.sub("；", text)
    text = SPACE_RE.sub(" ", text)
    return text[:limit]


def query_chars(value: Any) -> set[str]:
    return {
        ch.lower()
        for ch in str(value or "")
        if "\u4e00" <= ch <= "\u9fff" or ch.isalnum()
    }


def rank_nodes(graph, question: str, top_n: int = 5) -> list[tuple[float, str]]:
    """给每个实体打分：名字直接出现在问题里 → 高分；字符重叠 → 中分；度数高 → 微加分。"""
    q = query_chars(question)
    ranked: list[tuple[float, str]] = []
    for node_id, data in graph.nodes(data=True):
        name = str(node_id)
        if len(name.strip()) <= 1:
            continue
        name_chars = query_chars(name)
        if not name_chars:
            continue

        score = 0.0
        if name in question:
            score += 4.0 + min(len(name) / 6, 1.5)

        overlap = len(q & name_chars)
        # ★ 单字重叠不算命中。
        #   反例：问「今天天气怎么样」时，"天井""飞天"只因共用一个"天"字就被选中，
        #   于是域外问题也能捞出一堆无关实体。要求至少两个字符重叠可以挡掉这种情况。
        if name not in question and overlap < 2:
            continue
        score += (overlap / max(len(name_chars), 1)) * 2.2

        desc_chars = query_chars(str(data.get("description") or "")[:260])
        if desc_chars:
            score += (len(q & desc_chars) / max(len(q), 1)) * 0.7

        score += min(graph.degree(node_id), 8) * 0.03
        if score >= 0.75:
            ranked.append((score, node_id))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[:top_n]


def collect_evidence(graph, ranked_nodes, max_edges: int = 12) -> list[dict]:
    """证据 = 相关实体的说明 + 它们的关系边（每实体取度数最高的 4 条）。"""
    evidence: list[dict] = []
    seen: set[tuple] = set()

    for score, node_id in ranked_nodes:
        data = graph.nodes[node_id]
        description = clean_text(data.get("description"), 220)
        if description:
            seen.add(("node", str(node_id)))
            evidence.append({
                "type": "entity",
                "score": round(score, 3),
                "source": str(node_id),
                "target": "",
                "relation": "实体说明",
                "description": description,
                "category": str(data.get("entity_type") or data.get("category") or "Entity"),
                "degree": int(graph.degree(node_id)),
            })

        neighbors = []
        for source, target, edge_data in graph.edges(node_id, data=True):
            other = target if source == node_id else source
            neighbors.append((graph.degree(other), source, target, edge_data))
        neighbors.sort(key=lambda item: item[0], reverse=True)

        for _, source, target, edge_data in neighbors[:4]:
            key = tuple(sorted([str(source), str(target)]))
            if key in seen:
                continue
            seen.add(key)
            relation = clean_text(
                edge_data.get("description") or edge_data.get("relation") or "相关", 180
            )
            evidence.append({
                "type": "relation",
                "score": round(score, 3),
                "source": str(source),
                "target": str(target),
                "relation": relation,
                "description": clean_text(edge_data.get("description"), 220),
                "category": "",
                "degree": 0,
            })
            if len(evidence) >= max_edges:
                return evidence
    return evidence


def format_evidence(question: str, entities: list[dict], evidence: list[dict]) -> str:
    """把图谱证据格式化成 LLM 好读的文本。"""
    if not entities and not evidence:
        return (
            f"【{MANIFEST_DATA['name']}】查询：{question}\n"
            f"图谱中没有找到足够明确的关联实体。\n"
            f"请如实告诉游客无法确认，或改用具体景点名再问。"
        )

    lines = [f"【{MANIFEST_DATA['name']}】查询：{question}", ""]

    if entities:
        lines.append(f"■ 命中实体（{len(entities)} 个，按相关度）")
        for e in entities:
            lines.append(f"  · {e['source']}（{e['category']}，关联度 {e['degree']}）")
            if e["description"]:
                lines.append(f"      {e['description']}")
        lines.append("")

    relations = [e for e in evidence if e["type"] == "relation"]
    if relations:
        lines.append(f"■ 关系证据（{len(relations)} 条）")
        for r in relations:
            lines.append(f"  · {r['source']} → {r['target']}：{r['relation']}")
        lines.append("")

    lines.append("请依据以上实体与关系回答；涉及具体参数（高度、票价、时间）时，")
    lines.append("应另用对应景区的知识库查询核实，不要仅凭图谱推断。")
    return "\n".join(lines)


def graph_query(question: str, max_entities: int = 5, max_edges: int = 12) -> str:
    q = (question or "").strip()
    if not q:
        return f"【{MANIFEST_DATA['name']}】问题为空。"
    try:
        graph = get_graph()
        ranked = rank_nodes(graph, q, top_n=max(1, int(max_entities)))
        entities = [
            {
                "source": str(node_id),
                "category": str(graph.nodes[node_id].get("entity_type")
                                or graph.nodes[node_id].get("category") or "Entity"),
                "description": clean_text(graph.nodes[node_id].get("description"), 220),
                "degree": int(graph.degree(node_id)),
                "score": round(score, 3),
            }
            for score, node_id in ranked
        ]
        evidence = collect_evidence(graph, ranked, max_edges=max(1, int(max_edges)))
        return format_evidence(q, entities, evidence)
    except Exception as exc:
        return f"【{MANIFEST_DATA['name']}】图谱查询失败：{exc}"


def graph_stats() -> str:
    try:
        graph = get_graph()
        types: dict[str, int] = {}
        for _, data in graph.nodes(data=True):
            t = str(data.get("entity_type") or data.get("category") or "Entity")
            types[t] = types.get(t, 0) + 1
        top = sorted(types.items(), key=lambda kv: kv[1], reverse=True)[:6]
        return (
            f"【{MANIFEST_DATA['name']}】\n"
            f"实体：{len(graph.nodes)} 个\n"
            f"关系：{len(graph.edges)} 条\n"
            f"实体类型分布（前 6）：" + "、".join(f"{k} {v}" for k, v in top) + "\n"
            f"图谱文件：{GRAPH_FILE.name}"
        )
    except Exception as exc:
        return f"【{MANIFEST_DATA['name']}】读取失败：{exc}"


# ─────────────────────────────────────────────────────────────
# MCP 协议层（与 kb-lingshan 一致：mcp 2.x 的 MCPServer）
# ─────────────────────────────────────────────────────────────

def build_server():
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(f"当前 mcp 版本不支持 MCPServer（需要 2.x）：{exc}")

    mcp = MCPServer("kb-graph")

    @mcp.tool(
        name=QUERY_TOOL,
        description=MANIFEST_DATA.get("description") or "查询景区知识图谱的实体与关系",
    )
    def query_graph_tool(question: str, max_entities: int = 5, max_edges: int = 12) -> str:
        """查知识图谱。

        Args:
            question: 游客的问题
            max_entities: 最多返回几个相关实体
            max_edges: 最多返回几条关系证据
        """
        q = (question or "").strip()
        started = time.perf_counter()
        text = graph_query(q, max_entities, max_edges)
        try:
            graph = get_graph()
            ranked = rank_nodes(graph, q, top_n=max(1, int(max_entities)))
            hit_entities = [e[1] for e in ranked]
        except Exception:
            hit_entities = []
        log_call(
            tool=QUERY_TOOL,
            query=q,
            ok=bool(hit_entities),
            count=len(hit_entities),
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            hit_entities=hit_entities,
        )
        return text

    @mcp.tool(name=STATS_TOOL, description=f"查看「{MANIFEST_DATA['name']}」的规模与实体类型分布")
    def graph_stats_tool() -> str:
        """图谱统计。"""
        return graph_stats()

    return mcp


def selftest() -> int:
    print("=" * 70)
    print(graph_stats())
    for q in [
        "拈花湾有什么好玩的？",
        "灵山梵宫和灵山大佛之间怎么衔接？",
        "五印坛城和藏传佛教是什么关系？",
        "今天天气怎么样？",
    ]:
        print()
        print("=" * 70)
        print(graph_query(q, max_entities=3, max_edges=6))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    build_server().run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
