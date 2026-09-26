#!/usr/bin/env python3
"""灵山知识库 · MCP 插件

它是什么
--------
把「灵山景区的 170 条结构化知识」封装成一个符合 MCP 协议的**可插拔知识库服务**。
Fay 启动时会按 faymcp/data/mcp_servers.json 里的配置把它拉起来，
之后 Fay 的 Agent 就能在需要时调用它。

为什么这样设计
--------------
· 独立进程 + stdio：与 Fay 主程序解耦。停掉它，Fay 只是"少了一项技能"，不会崩
· 返回里带 `context` 字段：格式化好的知识文本，可**直接作为上下文**喂给 LLM
· 保留"无证据拒答"：最佳结果都超过距离阈值时返回空，避免让模型硬编

工具
----
  query_knowledge(query, top_k, max_distance)  → 检索知识库
  kb_stats()                                    → 知识库状态

用法
----
  python server.py --selftest            # 不经过 Fay，直接自测两个工具
  python server.py                       # 以 MCP stdio server 运行（由 Fay 拉起）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
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
INDEX_DIR = HERE / "index"
META_PATH = INDEX_DIR / "index_meta.json"
CORPUS = HERE / "corpus" / "knowledge.json"
MANIFEST = HERE / "plugin.json"
LOG_PATH = HERE / "logs" / "calls.jsonl"


def log_call(**fields: Any) -> None:
    """把一次调用记成一行 JSONL（给管理端分析用）。

    ★ 两条原则：
      1. **绝不抛异常** —— 埋点失败不能影响问答主流程
      2. 记录 `top_kb`（命中了哪几条知识）—— 这是管理端做"知识块命中分布"
         和**自进化做"哪些知识没人问"**的唯一来源。Fay 侧看不到这个信息。
    """
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        fields["ts"] = datetime.now().isoformat(timespec="seconds")
        fields["plugin"] = MANIFEST_DATA.get("slug") or HERE.name
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(fields, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ── 缺口申报队列 ──
#
# 为什么单独一个文件，而不是复用 calls.jsonl：
#   calls.jsonl 是**全量日志**（每条查询都记），用于统计；
#   unknown.jsonl 是**待处理队列**（只记没答上的），是自进化的输入。
#   两者的用途不同，混在一起会让"这一条处理过没有"无从判断。
#
# ★ 关键设计：这里是**插件自己记**，不依赖大模型主动调用工具。
#   原因：模型的工具调用是不可靠的（它可能忘记调）。
#   而"检索有没有命中"是插件自己知道的事实，记下来是 100% 覆盖的。
#   模型侧的 report_unknown 工具只作为补充（用于"检索到了但答不了"的情况）。
UNKNOWN_PATH = HERE / "logs" / "unknown.jsonl"


def log_unknown(**fields: Any) -> None:
    """记一条"这次没答上"的申报。同样绝不抛异常。"""
    try:
        UNKNOWN_PATH.parent.mkdir(parents=True, exist_ok=True)
        fields["ts"] = datetime.now().isoformat(timespec="seconds")
        fields["plugin"] = MANIFEST_DATA.get("slug") or HERE.name
        fields.setdefault("status", "pending")      # 管理端审核后改成 accepted/rejected/done
        with UNKNOWN_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(fields, ensure_ascii=False) + "\n")
    except Exception:
        pass

# ★ collection 名从**目录名**派生，不写死。
#   这样"复制插件目录 → 换个语料"就能得到一个全新的知识库，代码一行不用改
#   （这是"可插拔"能成立的依据）
COLLECTION = HERE.name.replace("-", "_").replace(".", "_")


def load_manifest() -> dict:
    """读插件清单。没清单就用目录名兜底。

    ★ 为什么要清单：Fay 的工具注册表是**按工具名聚合**的
      （见 faymcp/tool_registry.py 的 _rebuild_cache_locked），
      两个插件若都叫 query_knowledge，会互相覆盖，只有一个能被调用。
      所以工具名必须逐个插件不同 —— 而这件事不该写死在代码里，
      否则复制一份插件就得改代码，"可插拔"就假了。
    """
    slug = HERE.name.replace("-", "_")
    data = {"slug": slug, "name": HERE.name, "description": ""}
    if MANIFEST.is_file():
        try:
            data.update(json.loads(MANIFEST.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    data["slug"] = str(data.get("slug") or slug)
    return data


MANIFEST_DATA = load_manifest()
QUERY_TOOL = f"{MANIFEST_DATA['slug']}_query"
STATS_TOOL = f"{MANIFEST_DATA['slug']}_stats"
INGEST_TOOL = f"{MANIFEST_DATA['slug']}_ingest"
REPORT_TOOL = f"{MANIFEST_DATA['slug']}_report_unknown"

# 本地 BGE 向量模型（旧项目现成的，不用联网下载）
EMBED_MODEL_DIR = Path(
    os.getenv(
        "LINGSHAN_EMBED_MODEL",
        r"D:\PROJECT\scenic_ai\灵山AI导游系统\backend\models\embeddings\BAAI--bge-small-zh-v1.5",
    )
)

# 超过这个余弦距离就认为"没有可靠证据"（宁可拒答，不许硬编）
#
# ★ 这个值从 0.55 改成了 0.35，依据是 212 次真实查询的分布统计：
#
#     阈值    真命中被判命中    假命中被误判为命中
#     0.30       66%              6%
#     0.35       86%             11%      ← 拐点
#     0.40       97%             56%      ← 断崖
#     0.55      100%            100%
#
#   "假命中" = 知识库里根本没有该主题（停车/公交/餐饮/轮椅/寄存/纪念品…），
#   向量检索仍返回"最像的 5 条"，而这些主题的最佳距离集中在 0.34-0.53。
#   0.55 的阈值把这 18 条**全部**判成了"可靠命中"——假命中率 100%。
#
#   代价对比（为什么宁可严一点）：
#     阈值太松 -> 数字人拿着不相关内容当依据，**会编造**（景区票价、停车场收费编错了很严重）
#     阈值太严 -> 数字人说"我不确定"，用户多问一次
#   对景区导览和孤独症陪伴场景，"不编造"远比"不漏答"重要。
#
#   历史遗留说明：0.55 是从旧项目 settings 的 RAG_DENSE_MAX_DISTANCE 继承来的，
#   当时只用少数几条样例测过（"精确问题 / 边缘 / 域外"三段），样本太小，
#   把中间那一大段（0.35-0.55）误判成了"边缘但合理"。
#
#   改动影响：数字人会更频繁地说"没有可靠依据"。**这是正确行为**，
#   但会让命中率从 100% 掉到 86% 左右——掉下去的那部分是原本就答错的。
# LOOSE（上限）：超过这个距离就完全不提供内容，直接说"知识库里没有"
DEFAULT_MAX_DISTANCE = float(os.getenv("LINGSHAN_MAX_DISTANCE", "0.50"))
# TIGHT（下限）：在这个距离以内是"铁证"，可以直接当依据用。
# 两者之间是"弱相关"档，交给模型判断（见 search() 里的注释）。
DEFAULT_TIGHT_DISTANCE = float(os.getenv("LINGSHAN_TIGHT_DISTANCE", "0.30"))

_model = None
_collection = None
# ★ 加载锁：预热线程和首次查询可能同时触发加载，
#   sentence_transformers / torch 的懒加载不是线程安全的（实测撞出过
#   `maximum recursion depth exceeded`）。用双检锁保证只加载一次。
_load_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────
# 懒加载：MCP server 是常驻进程，模型只加载一次
# ─────────────────────────────────────────────────────────────

def get_model():
    global _model
    if _model is None:
        with _load_lock:
            if _model is None:
                if not EMBED_MODEL_DIR.is_dir():
                    raise RuntimeError(f"找不到 BGE 模型目录：{EMBED_MODEL_DIR}")
                from sentence_transformers import SentenceTransformer

                _model = SentenceTransformer(str(EMBED_MODEL_DIR), device="cpu")
    return _model


def get_collection():
    global _collection
    if _collection is None:
        with _load_lock:
            if _collection is None:
                import chromadb

                if not (INDEX_DIR / "chroma.sqlite3").is_file():
                    raise RuntimeError(f"向量库不存在：{INDEX_DIR}（先跑 build_index.py）")
                client = chromadb.PersistentClient(path=str(INDEX_DIR))
                _collection = client.get_or_create_collection(
                    COLLECTION, metadata={"hnsw:space": "cosine"}
                )
    return _collection


def index_meta() -> dict:
    if META_PATH.is_file():
        try:
            return json.loads(META_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


# ─────────────────────────────────────────────────────────────
# 核心：检索
# ─────────────────────────────────────────────────────────────

def search(query: str, top_k: int = 5, max_distance: float = DEFAULT_MAX_DISTANCE,
           tight: float = DEFAULT_TIGHT_DISTANCE) -> dict:
    query = (query or "").strip()
    if not query:
        return {"query": query, "count": 0, "results": [], "context": "",
                "reliable": False, "reason": "空查询"}

    col = get_collection()
    if col.count() == 0:
        return {"query": query, "count": 0, "results": [], "context": "",
                "reliable": False, "reason": "知识库为空"}

    vec = get_model().encode([query], normalize_embeddings=True)[0].tolist()
    res = col.query(
        query_embeddings=[vec],
        n_results=max(1, int(top_k)),
        include=["documents", "metadatas", "distances"],
    )

    rows = []
    for chunk_id, doc, meta, dist in zip(
        res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0]
    ):
        rows.append(
            {
                "id": chunk_id,
                "spot": meta.get("spot_name") or meta.get("scenic_name") or "",
                "scenic": meta.get("scenic_name") or "",
                "info_type": meta.get("info_type") or "",
                "content": doc or "",
                "distance": round(float(dist), 4),
                "similarity": round(1.0 - float(dist), 4),
                "protected": bool(meta.get("protected")),
                "protect_reason": meta.get("protect_reason") or "",
                "tags": meta.get("tags") or "",
            }
        )

    # ── 检索结果分三档，而不是"命中/没命中"两档 ──
    #
    # ★ 为什么要分三档：实测数据摆在这儿，
    #
    #     知识库里【有】的问题：距离 0.120 – 0.460，中位 0.281
    #     知识库里【没有】的问题：距离 0.219 – 0.528，中位 0.392
    #
    #   两段在 0.30–0.45 之间**大量重叠**，所以：
    #     · 阈值 0.35 -> 86% 真命中通过，但**误伤** 14% 的真实知识
    #       （实测铁证：「五明桥横跨哪片水域」0.377，而答案就在库里第一句）
    #     · 阈值 0.55 -> 真命中 100%，但**假命中 100%**
    #       （停车场/公交/餐饮/轮椅/寄存，库里 0 块知识，全被判成命中）
    #
    #   一个距离阈值分不开这两类。硬分的结果必然是"要么误伤、要么放过"。
    #
    #   所以中间那一档**不丢也不当铁证**，交给模型自己判断相关性——
    #   这种"这段内容和问题到底相不相关"的判断，正是大模型擅长而距离度量不擅长的。
    best = rows[0]["distance"] if rows else None
    if best is not None and best <= tight:
        reliability = "strong"          # 铁证，直接用
    elif best is not None and best <= max_distance:
        reliability = "weak"            # 弱相关，给模型参考但要它自己判断
    else:
        reliability = "none"
    reliable = reliability == "strong"

    if reliability == "none":
        rows = []
        context = ""
    else:
        blocks = []
        for i, r in enumerate(rows, 1):
            lock = "（重要提示）" if r["protected"] else ""
            blocks.append(f"[{i}] {r['spot']} · {r['info_type']}{lock}\n{r['content']}")
        context = "\n\n".join(blocks)
        if reliability == "weak":
            context = (
                "⚠️ 以下内容与问题的相关性**不确定**（相似度处于中间区间）。"
                "请先判断这些内容是否真的回答了用户的问题：\n"
                "· 如果确实相关，就据此回答；\n"
                "· 如果不相关（比如用户问停车、你拿到的是景点介绍），"
                "**必须如实说知识库没有这方面的信息**，不要拿不相关的内容硬凑。\n\n"
                + context
            )

    return {
        "query": query,
        "count": len(rows),
        "results": rows,
        "context": context,
        "reliable": reliable,
        "reliability": reliability,
        "best_distance": best,
        "reason": "" if reliable else "知识库中没有足够可靠的依据，应如实说明无法确认",
        "max_distance": max_distance,
    }


def stats() -> dict:
    meta = index_meta()
    try:
        count = get_collection().count()
        ok = True
        err = ""
    except Exception as exc:
        count = 0
        ok = False
        err = str(exc)
    return {
        "ready": ok and count > 0,
        "collection": COLLECTION,
        "chunk_count": count,
        "index_dir": str(INDEX_DIR),
        "corpus_version": meta.get("corpus_version", ""),
        "built_at": meta.get("built_at", ""),
        "protected_count": meta.get("protected_count", 0),
        "embedding_model": EMBED_MODEL_DIR.name,
        "error": err,
    }


# ─────────────────────────────────────────────────────────────
# MCP 协议层
#
# 注意：这里用的是 mcp 2.x 的 MCPServer（在 1.x 里叫 FastMCP，已改名）。
# Fay 自己的范例 mcp_servers/yueshen_rag/server.py 用的是 1.x 的
# `from mcp.server import Server` + `@server.list_tools()` 写法，
# **在 Fay 当前装的 mcp 2.x 里会报 AttributeError: 'Server' object has no attribute 'list_tools'**
# —— 这个坑实测踩过（logs/mcp_stdio_python.EXE.log 里有完整堆栈）。
# ─────────────────────────────────────────────────────────────

def confidence_of(best_distance) -> float | None:
    """余弦距离 → 置信度（0~1）。距离越小越自信。

    为什么要换算：给模型和用户看，「置信度 88%」比「余弦距离 0.12」直观得多，
    而且模型能据此校准语气（高置信用肯定句，中置信用"资料里提到…"）。
    """
    if not isinstance(best_distance, (int, float)):
        return None
    return max(0.0, min(1.0, 1.0 - float(best_distance)))


def format_result(query: str, r: dict) -> str:
    """工具返回给 LLM 的是**可读文本**，不是 JSON。

    因为 tool result 最终是塞进 prompt 给模型读的——文本比 JSON 更好读，
    而且不必让模型去解析嵌套结构。
    """
    name = MANIFEST_DATA["name"]
    rel = r.get("reliability") or ("strong" if r.get("reliable") else "none")
    conf = confidence_of(r.get("best_distance"))
    pct = f"{conf:.0%}" if conf is not None else "未知"

    # ★ 置信度按三档给不同的语气指引。
    #   为什么不能让模型自己看着办：它看不到距离，只看到一堆文本，
    #   不知道这些文本到底有多相关。把置信度明说给它，它才能校准措辞。
    if rel == "none":
        return (
            f"【{name}】查询：{query}\n"
            f"检索置信度：{pct}（低）—— 知识库里没有这方面的内容。\n"
            f"请如实告诉游客资料里没有这方面的信息，建议咨询现场工作人员。\n"
            f"不要用常识、其它景区的经验或推测来补 —— "
            f"停车费、票价、开放时间这类信息编错了后果严重。"
        )

    if rel == "weak":
        head = (
            f"【{name}】查询：{query}\n"
            f"检索置信度：{pct}（中等）—— 找到了内容，但不一定对得上这个问题。\n"
            f"请先自己判断下面这些内容是否真的回答了问题：\n"
            f"  · 确实相关 → 用「资料里提到…」这类措辞回答，不要说成绝对；\n"
            f"  · 不相关 → 如实说资料里没有明确信息，不要拿不相关的内容硬凑。\n"
            f"（如果你判断是「不相关」，可以调用 {REPORT_TOOL} 把这个缺口报给管理员）"
        )
    else:
        head = (f"【{name}】查询：{query}\n"
                f"检索置信度：{pct}（高），命中 {r['count']} 条，按相关度排序：")

    lines = [head, ""]
    for i, item in enumerate(r["results"], 1):
        lock = "｜重要提示" if item["protected"] else ""
        lines.append(f"[{i}] {item['spot']} · {item['info_type']}（相关度 {item['similarity']}）{lock}")
        lines.append(item["content"])
        lines.append("")
    if rel == "strong":
        lines.append("请只依据以上内容回答，不要补充资料之外的信息。")
    return "\n".join(lines)


def build_server():
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "当前环境的 mcp 版本不支持 MCPServer（需要 mcp 2.x）。\n"
            "若你用的是 mcp 1.x，请把 build_server() 换成 FastMCP 写法。\n"
            f"原始错误：{exc}"
        )

    mcp = MCPServer("lingshan-kb")

    # ★ 后台预热：MCP server 是常驻进程，模型只加载一次。
    #   不预热的话，**第一次查询要把加载模型的 9 秒也算进去**——
    #   既让用户等，也让埋点里的 elapsed_ms 严重失真（实测踩过：8992ms）。
    def _warmup() -> None:
        try:
            get_model()
            get_collection()
        except Exception:
            pass

    threading.Thread(target=_warmup, name="kb-warmup", daemon=True).start()

    @mcp.tool(
        name=QUERY_TOOL,
        description=MANIFEST_DATA.get("description") or f"查询「{MANIFEST_DATA['name']}」知识库",
    )
    def query_knowledge(query: str, top_k: int = 5, max_distance: float = DEFAULT_MAX_DISTANCE) -> str:
        """查景区知识库。

        Args:
            query: 游客的问题或关键词
            top_k: 返回条数
            max_distance: 余弦距离阈值，超过则判定为无可靠依据
        """
        q = (query or "").strip()
        started = time.perf_counter()
        try:
            r = search(q, int(top_k or 5), float(max_distance or DEFAULT_MAX_DISTANCE))
        except Exception as exc:
            log_call(tool=QUERY_TOOL, query=q, ok=False, error=str(exc)[:200],
                     elapsed_ms=int((time.perf_counter() - started) * 1000))
            return f"【{MANIFEST_DATA['name']}】查询失败：{exc}"
        log_call(
            tool=QUERY_TOOL,
            query=q,
            ok=True,
            reliable=r["reliable"],
            reliability=r.get("reliability"),
            count=r["count"],
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            # 命中了哪几条知识 —— 管理端做"命中分布"、自进化做"哪些没人问"都靠它
            top_kb=[item["id"] for item in r["results"]],
            top_spots=[item["spot"] for item in r["results"]],
            max_distance=float(max_distance or DEFAULT_MAX_DISTANCE),
            best_distance=(r["results"][0]["distance"] if r["results"] else None),
            confidence=confidence_of(r.get("best_distance")),
            protected_hit=any(item["protected"] for item in r["results"]),
        )

        # ★ 缺口申报：没到铁证线就自动记一笔。
        #
        #   这里是**插件自己记**，不依赖大模型主动调工具 ——
        #   因为模型的工具调用不可靠（它可能忘记调），而"检索命中没有"
        #   是插件自己知道的事实，记下来是 100% 覆盖的。
        #   模型侧的 report_unknown 只作为补充（用于"检索到了但答不了"的情况）。
        if r.get("reliability") != "strong":
            log_unknown(
                query=q,
                band=r.get("reliability") or "none",
                best_distance=r.get("best_distance"),
                confidence=confidence_of(r.get("best_distance")),
                top_spots=[item["spot"] for item in r["results"]][:3],
                source="auto",
            )
        return format_result(q, r)

    @mcp.tool(
        name=STATS_TOOL,
        description=f"查看「{MANIFEST_DATA['name']}」知识库的状态（知识条数、语料版本、受保护知识数量等）",
    )
    def kb_stats() -> str:
        """知识库状态。"""
        s = stats()
        return (
            f"【{MANIFEST_DATA['name']}】状态\n"
            f"就绪：{s['ready']}\n"
            f"知识条数：{s['chunk_count']}\n"
            f"受保护知识：{s['protected_count']} 条\n"
            f"语料版本：{s['corpus_version']}\n"
            f"构建时间：{s['built_at']}\n"
            f"向量模型：{s['embedding_model']}\n"
            + (f"错误：{s['error']}" if s["error"] else "")
        )

    @mcp.tool(
        name=REPORT_TOOL,
        description=(
            "申报一个「知识库答不上来」的问题。"
            "当你查过资料后发现自己无法给出可靠回答时调用它，"
            "管理员会在「知识分析」页看到，后续补充相应知识。"
            "这只影响记录，不影响你对游客的回答——该说没有的信息还是要如实说。"
        ),
    )
    def report_unknown(question: str, topic: str = "", note: str = "") -> str:
        """申报知识缺口。

        Args:
            question: 游客问的原话，越原样越好——管理员要据此判断缺什么
            topic: 这一问属于什么主题（如「交通停车」「餐饮住宿」），不确定就留空
            note: 补充说明（如「库里有门票信息但完全没提停车」）
        """
        q = (question or "").strip()
        if not q:
            return "问题为空，没有记录。"
        log_call(tool=REPORT_TOOL, query=q, ok=True)
        log_unknown(query=q, topic=(topic or "").strip(),
                    note=(note or "").strip(), source="model")
        return "已记录，管理员会在「知识分析」里看到。请继续正常回答游客。"

    @mcp.tool(
        name=INGEST_TOOL,
        description=(
            f"向「{MANIFEST_DATA['name']}」知识库**新增**知识（管理员用）。"
            "传入文档正文，会切块、向量化并入库；同时落到语料文件，重建索引也不会丢。"
            "支持结构化语料（含 @@@RAG_CHUNK_START@@@ 标记）与普通 Markdown。"
        ),
    )
    def ingest_document(content: str, source: str = "管理员上传", dry_run: bool = False) -> str:
        """向知识库新增内容。

        Args:
            content: 文档正文
            source: 来源标识（会记进知识块的 source 字段，便于追溯）
            dry_run: 只预览切块结果，不真正写入
        """
        try:
            if str(HERE) not in sys.path:
                sys.path.insert(0, str(HERE))
            import importlib

            ingest_mod = importlib.import_module("ingest")
            importlib.reload(ingest_mod)  # 保证拿到最新代码
            started = time.perf_counter()
            result = ingest_mod.ingest(content, source, dry_run=bool(dry_run))
        except Exception as exc:
            log_call(tool=INGEST_TOOL, source=source, ok=False, error=str(exc)[:200])
            return f"【{MANIFEST_DATA['name']}】入库失败：{exc}"

        log_call(
            tool=INGEST_TOOL,
            source=source,
            ok=bool(result.get("success")),
            added=result.get("added", 0),
            skipped=result.get("skipped", 0),
            dry_run=bool(dry_run),
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )

        if not result.get("success"):
            return f"【{MANIFEST_DATA['name']}】入库失败：{result.get('message')}"
        lines = [
            f"【{MANIFEST_DATA['name']}】入库结果",
            f"新增：{result.get('added')} 条",
            f"跳过重复：{result.get('skipped', 0)} 条",
            f"库内当前条数：{result.get('collection_count', '?')}",
        ]
        if result.get("protected_added"):
            lines.append(f"其中被标记为受保护：{result['protected_added']} 条")
        if result.get("preview"):
            lines.append("预览：")
            lines += [f"  - {p}" for p in result["preview"]]
        return "\n".join(lines)

    return mcp


# ─────────────────────────────────────────────────────────────

def selftest() -> int:
    print("=" * 70)
    print("自测：kb_stats")
    print("=" * 70)
    print(json.dumps(stats(), ensure_ascii=False, indent=2))

    questions = [
        "灵山大佛有多高？",
        "门票多少钱？",
        "雨天要注意什么？",
        "拈花湾有什么好玩的？",
        "今天天气怎么样？",          # 域外问题，应触发"无证据"
    ]
    for q in questions:
        r = search(q, top_k=3)
        print()
        print("=" * 70)
        print(f"问：{q}")
        print(f"reliable={r['reliable']}  count={r['count']}  {r['reason']}")
        for i, item in enumerate(r["results"], 1):
            lock = "🔒" if item["protected"] else "  "
            print(f"  [{i}] {lock} {item['spot']} · {item['info_type']}  距离={item['distance']}")
            print(f"       {item['content'][:80]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", action="store_true", help="不经过 Fay，直接自测")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    build_server().run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
