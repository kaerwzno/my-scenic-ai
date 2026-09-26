#!/usr/bin/env python3
"""管理端后端：只读文件 + 转发调用，不改 Fay 也不依赖 Django。

为什么独立成一个服务
--------------------
· 塞进 Fay → 要改 Fay 代码，管理端还耦合到框架
· 复用旧 Django → 它连的是旧知识库，而且重
· 独立轻量服务 → 只读文件 + 转发调用，完全不影响 Fay 运行

它把所有数据源收在一处，前端只管取：
    plugins/<插件>/index/index_meta.json   插件状态
    plugins/<插件>/logs/calls.jsonl        调用埋点（热点查询、命中率、知识命中分布）
    plugins/kb-graph/graph/*.graphml       知识图谱
    Fay-main/memory/fay.db                 对话记录
    Fay-main/memory/user_profiles.db       用户画像
    示范景区公开资料包/*.xlsx               游客行为数据（140k 行）
    Fay 的 5010                            MCP 管理接口（用于触发入库）

启动
    python app.py            # 默认 127.0.0.1:5174
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS


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
PROJECT = HERE.parent
PLUGINS_DIR = PROJECT / "plugins"
SOURCE_DIR = PROJECT / "示范景区公开资料包"
WEB_DIR = PROJECT / "admin-web"
DOCS_DIR = PROJECT / "docs"
FAY_DIR = Path(os.getenv("FAY_DIR", r"D:\PROJECT\scenic_ai\重构数字人\Fay-main"))
FAY_MCP_API = os.getenv("FAY_MCP_API", "http://127.0.0.1:5010")

app = Flask(__name__)
CORS(app)


@app.after_request
def no_cache_for_frontend(resp):
    """前端文件禁止缓存。

    ★ 为什么必须加：开发时改了 index.html / app.js，浏览器会带着缓存标识来问
    "变了没"，Flask 回 304「没变」——浏览器就继续用旧文件，改动看不到。
    实测踩过：拆完前端后页面一直加载不出来，代码和资源都验证过是对的，
    最后发现是 304 缓存——**服务端是对的，浏览器在用旧的**。

    只对前端静态文件禁缓存，接口数据不设（那些本来就该实时取）。
    """
    path = request.path or ""
    if (path == "/" or path.endswith((".html", ".js", ".css"))
            or path.startswith("/tabs/")):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp


# ─────────────────────────────────────────────────────────────
# 前端静态页（与 API 同一个服务，用户只需记一个地址）
# ─────────────────────────────────────────────────────────────

@app.get("/")
def web_index():
    if not (WEB_DIR / "index.html").is_file():
        return jsonify({"error": f"找不到前端页面：{WEB_DIR / 'index.html'}"}), 404
    return send_from_directory(WEB_DIR, "index.html")


@app.get("/lib/<path:filename>")
def web_lib(filename):
    return send_from_directory(WEB_DIR / "lib", filename)


# 前端拆分成多个文件之后的静态资源路由。
#
# ★ 为什么必须显式加：原来整个前端就是一个 index.html，所以只要一条 "/" 路由就够。
#   拆出 app.js 和 tabs/*.js 之后，浏览器会去请求这些路径——
#   而管理端只管了 "/" 和 "/lib/"，所以 /app.js 和 /tabs/kb.js 全都 404，
#   页面一片空白（实测踩过）。加静态目录路由比一条条加文件路由省事，也不怕再加文件。
@app.get("/app.js")
def web_app_js():
    return send_from_directory(WEB_DIR, "app.js")


@app.get("/tabs/<path:filename>")
def web_tabs(filename):
    return send_from_directory(WEB_DIR / "tabs", filename)


# ─────────────────────────────────────────────────────────────
# 通用
# ─────────────────────────────────────────────────────────────

def read_json(path: Path, default=None):
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def write_json_local(path: Path, data) -> None:
    """原子写：先写临时文件再替换，避免写一半崩了把文件写坏。

    （管理端的审核状态是人工劳动成果，写坏了要重来一遍。）
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def each_plugin():
    """遍历所有插件目录（含 plugin.json 的）。"""
    if not PLUGINS_DIR.is_dir():
        return
    for d in sorted(PLUGINS_DIR.iterdir()):
        if d.is_dir() and (d / "plugin.json").is_file():
            yield d, read_json(d / "plugin.json", {}) or {}


def read_calls(plugin_dir: Path, limit: int = 2000) -> list[dict]:
    """读插件的调用埋点（JSONL，取最后 limit 行）。"""
    path = plugin_dir / "logs" / "calls.jsonl"
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


# ─────────────────────────────────────────────────────────────
# 健康检查 & 插件
# ─────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "lingshan-admin-api",
        "project": str(PROJECT),
        "fay_dir": str(FAY_DIR),
        "plugins": [m.get("slug") or d.name for d, m in each_plugin()],
    })


@app.get("/api/plugins")
def list_plugins():
    """所有插件的状态：条数、语料版本、受保护数、调用次数。"""
    items = []
    for d, manifest in each_plugin():
        meta = read_json(d / "index" / "index_meta.json", {}) or {}
        corpus = read_json(d / "corpus" / "knowledge.json", {}) or {}
        uploaded = read_json(d / "corpus" / "uploaded.json", {}) or {}
        calls = read_calls(d)
        graphml = list((d / "graph").glob("*.graphml")) if (d / "graph").is_dir() else []

        ready = bool(meta.get("chunk_count")) or bool(graphml)
        items.append({
            "dir": d.name,
            "slug": manifest.get("slug") or d.name,
            "name": manifest.get("name") or d.name,
            "description": manifest.get("description") or "",
            "kind": "graph" if graphml else "vector",
            "ready": ready,
            "chunk_count": int(meta.get("chunk_count") or corpus.get("chunk_count") or 0),
            "protected_count": int(meta.get("protected_count") or corpus.get("protected_count") or 0),
            "uploaded_count": len(uploaded.get("chunks") or []),
            "corpus_version": meta.get("corpus_version") or "",
            "built_at": meta.get("built_at") or "",
            "call_count": len(calls),
        })
    return jsonify({"plugins": items, "count": len(items)})


@app.get("/api/plugins/<slug>/calls")
def plugin_calls(slug):
    for d, m in each_plugin():
        if (m.get("slug") or d.name) == slug:
            return jsonify({"slug": slug, "calls": list(reversed(read_calls(d)))})
    return jsonify({"error": "插件不存在"}), 404


@app.post("/api/plugins/<slug>/ingest")
def plugin_ingest(slug):
    """上传文档入库 —— 转调 Fay 的 MCP 接口，让对应插件自己处理。"""
    import urllib.error
    import urllib.request

    body = request.get_json(silent=True) or {}
    content = (body.get("content") or "").strip()
    source = (body.get("source") or "管理端上传").strip()
    dry_run = bool(body.get("dry_run"))
    if not content:
        return jsonify({"success": False, "message": "内容不能为空"}), 400

    # 1) 找到 Fay 里对应的 MCP server id
    try:
        with urllib.request.urlopen(f"{FAY_MCP_API}/api/mcp/servers", timeout=10) as resp:
            servers = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return jsonify({"success": False, "message": f"连不上 Fay 的 MCP 服务（{FAY_MCP_API}）：{exc}"}), 503

    server = next((s for s in servers
                   if str(s.get("args", [""])[0]).replace("\\", "/").endswith(f"plugins/{slug}/server.py")
                   or slug in str(s.get("args"))), None)
    if not server:
        return jsonify({"success": False, "message": f"Fay 里没有找到 slug={slug} 的插件，请先在 MCP 页面连接"}), 404

    # 2) 调它的 ingest 工具
    #
    # ★ 工具名必须用 plugin.json 里的 slug，**不是目录名**。
    #   目录叫 kb-lingshan，但插件注册的工具叫 lingshan_ingest。
    #   之前这里拼成了 kb-lingshan_ingest，结果是 "Unknown tool"——
    #   而且外层还返回 success=true，前端显示"入库成功"，其实一条没进。
    #   （这个错误一直存在，只是之前的内容都是从命令行加进去的，所以没暴露。）
    plugin_meta = read_json(PLUGINS_DIR / slug / "plugin.json", {}) or {}
    tool_prefix = plugin_meta.get("slug") or slug
    payload = json.dumps({
        "method": f"{tool_prefix}_ingest",
        "params": {"content": content, "source": source, "dry_run": dry_run},
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{FAY_MCP_API}/api/mcp/servers/{server['id']}/call",
        data=payload, headers={"Content-Type": "application/json; charset=utf-8"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return jsonify({"success": False, "message": f"入库调用失败 HTTP {exc.code}：{detail[:300]}"}), 502
    except Exception as exc:
        return jsonify({"success": False, "message": f"入库调用失败：{exc}"}), 502

    # ★ 检查 MCP 返回里的 is_error —— 光看 HTTP 状态不够。
    #   MCP 调用本身成功（HTTP 200、外层 success），但工具内部可能报错
    #   （比如工具名不存在）。不查 is_error 就会把"Unknown tool"显示成"入库成功"。
    inner = (result or {}).get("result") or {}
    if inner.get("is_error"):
        texts = [c.get("text") for c in (inner.get("content") or []) if isinstance(c, dict)]
        return jsonify({"success": False, "server": server.get("name"),
                        "message": "插件报错：" + " ".join(t for t in texts if t)[:300],
                        "result": result}), 502

    return jsonify({"success": True, "server": server.get("name"), "result": result})


# ─────────────────────────────────────────────────────────────
# 知识图谱（给可视化用）
# ─────────────────────────────────────────────────────────────

@app.get("/api/graph/extract")
def graph_extract():
    """读 GraphML → ECharts 力导向图格式（逻辑同旧项目 extract_graph_data）。"""
    graph_files = []
    for d, _ in each_plugin():
        if (d / "graph").is_dir():
            graph_files += list((d / "graph").glob("*.graphml"))
    if not graph_files:
        return jsonify({"nodes": [], "links": [], "categories": [],
                        "error": "没有找到图谱文件"}), 404

    try:
        import networkx as nx

        g = nx.read_graphml(str(graph_files[0]))
        total_nodes, total_links = len(g.nodes), len(g.edges)

        min_degree = int(request.args.get("min_degree", 2))
        if min_degree > 0:
            g = g.subgraph([n for n, deg in g.degree() if deg >= min_degree])
        max_nodes = int(request.args.get("max_nodes", 300))
        if len(g.nodes) > max_nodes:
            top = [n for n, _ in sorted(g.degree(), key=lambda x: x[1], reverse=True)[:max_nodes]]
            g = g.subgraph(top)

        nodes, links, cats = [], [], set()
        for node_id, data in g.nodes(data=True):
            category = str(data.get("entity_type") or data.get("category") or "Entity")
            cats.add(category)
            degree = g.degree(node_id)
            nodes.append({
                "id": str(node_id),
                "name": str(node_id),
                "category": category,
                "symbolSize": min(max(20 + degree * 2, 20), 80),
                "description": str(data.get("description") or "")[:400],
                "degree": degree,
            })
        for source, target, data in g.edges(data=True):
            links.append({
                "source": str(source),
                "target": str(target),
                "name": str(data.get("description") or "related_to")[:200],
            })
        return jsonify({
            "nodes": nodes,
            "links": links,
            "categories": [{"name": c} for c in sorted(cats)],
            "stats": {
                "total_nodes": total_nodes,
                "total_links": total_links,
                "shown_nodes": len(nodes),
                "shown_links": len(links),
                "min_degree": min_degree,
                "max_nodes": max_nodes,
            },
        })
    except Exception as exc:
        return jsonify({"error": f"解析图谱失败：{exc}"}), 500


# ─────────────────────────────────────────────────────────────
# 数据分析
# ─────────────────────────────────────────────────────────────

def _fay_msg_rows() -> list[tuple]:
    db = FAY_DIR / "memory" / "fay.db"
    if not db.is_file():
        return []
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = conn.execute(
            "select type, way, content, createtime, username from T_Msg order by id"
        ).fetchall()
        conn.close()
        return rows
    except sqlite3.Error:
        return []


def _median(values) -> int:
    if not values:
        return 0
    xs = sorted(values)
    mid = len(xs) // 2
    if len(xs) % 2:
        return int(xs[mid])
    return int((xs[mid - 1] + xs[mid]) / 2)


def _latency_summary() -> dict:
    """端到端延迟的中位数，数据来自 scripts/measure_latency.py 的测量结果。

    这件事单独记一笔的原因：管理端原来那个「平均响应」只统计检索，
    不含大模型，而用户真正等的是十几秒。两个数摆在一起才不会被误读。
    """
    path = DOCS_DIR / "latency-raw.jsonl"
    if not path.is_file():
        return {"available": False}

    # 一行一条记录，不是合法 JSON 整体，所以逐行读
    raw = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not raw:
        return {"available": False}

    first = [r["first_s"] for r in raw if isinstance(r.get("first_s"), (int, float))]
    final = [r["final_s"] for r in raw if isinstance(r.get("final_s"), (int, float))]
    if not final and not first:
        return {"available": False}
    return {
        "available": True,
        "sample": len(raw),
        "first_median_s": round(_median_f(first), 1),
        "final_median_s": round(_median_f(final), 1),
        "measured_at": max((r.get("ts") or "") for r in raw),
    }


def _median_f(values) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    mid = len(xs) // 2
    if len(xs) % 2:
        return float(xs[mid])
    return (xs[mid - 1] + xs[mid]) / 2


@app.get("/api/stats/overview")
def stats_overview():
    """总览：对话量、活跃用户、趋势、知识库调用与命中率、耗时。"""
    rows = _fay_msg_rows()
    day = Counter()
    hour = Counter()
    users = set()
    user_msgs = 0
    fay_msgs = 0
    for mtype, _way, _content, ts, username in rows:
        if username:
            users.add(username)
        if mtype == "fay":
            fay_msgs += 1
        else:
            user_msgs += 1
        ts_sec = ts / 1000 if ts and ts > 10**12 else (ts or 0)
        if ts_sec:
            dt = datetime.fromtimestamp(ts_sec)
            day[dt.strftime("%Y-%m-%d")] += 1
            hour[dt.strftime("%H")] += 1

    # 插件调用统计
    total_calls = ok_calls = reliable_calls = error_calls = 0
    elapsed = []
    per_plugin: dict[str, dict] = {}
    for d, _ in each_plugin():
        slug = (read_json(d / "plugin.json", {}) or {}).get("slug") or d.name
        pp = per_plugin.setdefault(slug, {"total": 0, "ok": 0, "errors": 0, "reliable": 0})
        for c in read_calls(d):
            if not str(c.get("tool", "")).endswith("_query"):
                continue
            total_calls += 1
            pp["total"] += 1
            if c.get("ok"):
                ok_calls += 1
                pp["ok"] += 1
            else:
                # ★ 系统报错单独计数，**不进命中率的分母**。
                #   同一个错误在这个项目里已经出现过两次（缺口分析、查询分析都修过），
                #   数据看板一直漏着：一次"模型加载失败"会被算成"这次没命中"，
                #   于是命中率凭空下降，看着像知识不够。
                error_calls += 1
                pp["errors"] += 1
                continue
            if call_is_reliable(c):
                reliable_calls += 1
                pp["reliable"] += 1
            if isinstance(c.get("elapsed_ms"), (int, float)):
                elapsed.append(c["elapsed_ms"])

    # 命中率的分母 = 跑通的调用（排除报错）
    served_calls = total_calls - error_calls
    for slug, pp in per_plugin.items():
        denom = pp["total"] - pp["errors"]
        pp["rate"] = round(pp["reliable"] / denom, 4) if denom else 0.0

    # ★ 耗时口径：报中位数，不报平均值。
    #   原因：MCP 服务刚启动时要把嵌入模型加载进内存，头几次调用要 9-11 秒，
    #   而稳定后只有 19 毫秒。4 次冷启动能把平均值从 19ms 拉到 800ms 以上——
    #   一个把"系统正常"说成"系统很慢"的数字，比不报还糟。
    warm = [x for x in elapsed if x < 1000]
    slow_count = len(elapsed) - len(warm)

    return jsonify({
        "conversation": {
            "total_messages": len(rows),
            "user_messages": user_msgs,
            "fay_messages": fay_msgs,
            "active_users": len(users),
        },
        "trend_by_day": [{"date": k, "count": v} for k, v in sorted(day.items())],
        "trend_by_hour": [{"hour": f"{h:02d}", "count": hour.get(f"{h:02d}", 0)} for h in range(24)],
        "kb_calls": {
            "total": total_calls,
            "ok": ok_calls,
            "reliable": reliable_calls,
            "reliable_rate": round(reliable_calls / total_calls, 4) if total_calls else 0.0,
            "elapsed_median_ms": _median(elapsed),
            "elapsed_warm_median_ms": _median(warm),
            "elapsed_warm_avg_ms": round(sum(warm) / len(warm)) if warm else 0,
            "slow_count": slow_count,
            # 保留平均值为兼容旧前端，但新界面不再展示它
            "avg_elapsed_ms": round(sum(elapsed) / len(elapsed)) if elapsed else 0,
        },
        "latency": _latency_summary(),
    })


@app.get("/api/stats/queries")
def stats_queries():
    """热点查询 + 答不上来的问题（自进化的直接输入）。"""
    hot = Counter()
    missed = []
    errors = []
    per_plugin = Counter()
    for d, m in each_plugin():
        slug = m.get("slug") or d.name
        for c in read_calls(d):
            q = (c.get("query") or "").strip()
            if not q or not str(c.get("tool", "")).endswith("_query"):
                continue
            hot[q] += 1
            per_plugin[slug] += 1
            if c.get("ok") is False:
                # ★ 系统报错（模型加载失败、文件被占用…）不是知识缺口，要分开列。
                #   混在一起会误导管理者去补一条本来不缺的知识（实测踩过）。
                errors.append({"query": q, "plugin": slug, "ts": c.get("ts", ""),
                               "error": c.get("error") or "执行失败"})
                continue
            # 按当前阈值重判（阈值改过，老日志里的 reliable 是旧口径）
            if not call_is_reliable(c):
                missed.append({"query": q, "plugin": slug, "ts": c.get("ts", ""),
                               "best_distance": c.get("best_distance"),
                               "max_distance": c.get("max_distance")})
    return jsonify({
        "hot_queries": [{"query": q, "count": n} for q, n in hot.most_common(30)],
        "missed_queries": missed[-50:],
        "missed_count": len(missed),
        "error_calls": errors[-50:],
        "error_count": len(errors),
        "calls_by_plugin": dict(per_plugin),
    })


@app.get("/api/stats/kb-usage")
def stats_kb_usage():
    """知识块命中分布：哪些知识常被用到、哪些从没被命中（降级候选）。"""
    result = []
    for d, m in each_plugin():
        if (d / "graph").is_dir():
            continue
        corpus = read_json(d / "corpus" / "knowledge.json", {}) or {}
        uploaded = read_json(d / "corpus" / "uploaded.json", {}) or {}
        # ★ 必须把上传的语料也算上，否则"知识块命中分布"会漏掉通过 ingest 进来的内容
        #   （实测踩过：灵山明明 130 块，这里只显示 128）
        chunks = (corpus.get("chunks") or []) + (uploaded.get("chunks") or [])
        if not chunks:
            continue
        hits = Counter()
        for c in read_calls(d):
            for kb_id in (c.get("top_kb") or []):
                hits[kb_id] += 1

        import hashlib

        def vec_id(chunk: dict) -> str:
            raw = (f"{chunk.get('spot_id')}|{chunk.get('spot_name')}|"
                   f"{chunk.get('info_type')}|{chunk.get('source_field')}")
            return "kb-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]

        rows = []
        never = 0
        for chunk in chunks:
            vid = vec_id(chunk)
            n = hits.get(vid, 0)
            if n == 0:
                never += 1
            rows.append({
                "id": vid,
                "spot": chunk.get("spot_name") or chunk.get("scenic_name") or "",
                "info_type": chunk.get("info_type") or "",
                "protected": bool(chunk.get("protected")),
                "hit_count": n,
            })
        rows.sort(key=lambda r: r["hit_count"], reverse=True)
        result.append({
            "slug": m.get("slug") or d.name,
            "name": m.get("name") or d.name,
            "chunk_count": len(chunks),
            "never_hit": never,
            "never_hit_ratio": round(never / len(chunks), 4) if chunks else 0.0,
            "top": rows[:15],
            "bottom": [r for r in rows if r["hit_count"] == 0][:15],
        })
    return jsonify({"plugins": result})


_TOURISM_CACHE: dict = {}


# ═════════════════════════════════════════════════════════════
# 知识缺口分析：按"问题类别"看，而不是按"知识块"看
# ═════════════════════════════════════════════════════════════
#
# 为什么换个视角：按知识块看只能得到"130 块里 120 块没被用过"——这个数字没意义
# （大部分块刚入库，还没被问过）。按问题类别看才有指导性：
#     住宿类问题 23 条查询 → 知识库里只有 1 块  ← 明显缺口
#     交通类问题  9 条查询 → 知识库里 0 块      ← 完全空白

# 类别规则放在独立文件里 —— 管理端能看、能改，改完立即生效。
# 理由：这是**领域知识**（灵山游客会问什么），换景区要换，不该由代码写死。
CATEGORIES_FILE = HERE / "categories.json"

# ── 检索质量的三档判定 ──
#
# ★ 必须和 plugins/kb-lingshan/server.py 的
#   DEFAULT_TIGHT_DISTANCE / DEFAULT_MAX_DISTANCE 一致
#   （两处读同一组环境变量来保持一致）。
#
# 为什么分三档而不是"命中/没命中"：
#   实测两组数据的距离**大量重叠**——
#     知识库里有：0.120 – 0.460（中位 0.281）
#     知识库里没有：0.219 – 0.528（中位 0.392）
#   单一阈值分不开：0.35 会误伤真实知识，0.55 会漏掉全部假命中。
#   所以中间那档交给模型判断相关性（插件里已经在工具返回里加了提示）。
#
# 统计口径（这一条很关键）：
#   命中率 = **铁证率** = (strong + 图谱成功) / 跑通的调用
#   弱相关**不算命中** —— 那档是"模型可能答可能不答"，算进来会让命中率虚高。
#   实测："给到依据率"（含弱相关）几乎全是 100%，完全没有区分度；
#         只有铁证率能分出好坏（位置指引 42% vs 规模参数 85%）。
TIGHT_MAX_DISTANCE = float(os.getenv("LINGSHAN_TIGHT_DISTANCE", "0.30"))
LOOSE_MAX_DISTANCE = float(os.getenv("LINGSHAN_MAX_DISTANCE", "0.50"))


def call_band(call: dict) -> str:
    """判定一次调用属于哪一档：strong / weak / none。

    有 best_distance 就现算（老日志也能按新口径重判）；
    图谱插件不产生距离（它命中实体和关系，不是"最近的几条文本"），
    所以 best_distance 恒为 None，用 ok 兜底。

    ⚠️ 这个兜底不能省：漏了它，图谱的每一次调用都会判成"没命中"——
    实测踩过一次，「景点介绍」命中率因此从 100% 掉到 31%，
    而那一类的问题大多是图谱在答。
    """
    d = call.get("best_distance")
    if isinstance(d, (int, float)):
        if d <= TIGHT_MAX_DISTANCE:
            return "strong"
        if d <= LOOSE_MAX_DISTANCE:
            return "weak"
        return "none"
    if call.get("reliable") is None:
        return "strong" if call.get("ok") else "none"
    return "strong" if call.get("reliable") is True else "none"


def call_is_reliable(call: dict) -> bool:
    """只统计"铁证"。弱相关不算命中，理由见上面统计口径那段。"""
    return call_band(call) == "strong"


# ── 缺口分级：绿 / 黄 / 红 ──
#
# 分界线按用户意见定：**80% 以下就不算充足了**。
#
#   命中率 ≥ 80%            -> 充足（绿）——答得稳
#   60% ≤ 命中率 < 80%      -> 待完善（黄）——基本能用，但明显有掉链子的时候
#   命中率 < 60%：
#       知识库里有这一类知识  -> 待完善（黄）——**知识在，是检索/切分的问题**，
#                                             行动项是改进检索，不是去补内容
#       知识库里没有这一类知识 -> 严重（红）——真的缺知识，要喂资料
#
# 红黄的分界不看命中率、看**有没有知识支撑**：因为管理者能采取的行动不同。
# 命中率本身在界面上是单独一列，严重程度不会被藏起来。
OK_RATE = 0.80
WARN_RATE = 0.60

# 样本少于这个数就不下结论。
#
# ★ 为什么要这条：页面上原来出现过「摄影打卡 命中率 100% -> 充足」，
#   而那一类**只被问过 1 次**。用 1 次样本说"充足"和用 1 次说"严重"一样不可靠——
#   下一次游客问个别的，这个类别可能立刻变成 0%。
#   趋势判断必须建立在足够样本上，否则这一页就是在用噪声指导决策。
MIN_SAMPLE = 3

_FALLBACK_CATEGORIES = {
    "categories": {
        "景点介绍": {"keywords": ["介绍", "有什么", "看点"], "info_types": ["详细介绍"]},
        "门票票价": {"keywords": ["门票", "票价", "多少钱"], "info_types": ["票价"]},
    },
    "out_of_domain": [r"天气预报", r"\bpython\b", r"编程"],
    "refusal_patterns": ["无法确认", "没有可靠", "暂时没有", "不太清楚", "建议咨询"],
}


def load_categories() -> dict:
    data = read_json(CATEGORIES_FILE, None)
    if not isinstance(data, dict) or not data.get("categories"):
        return dict(_FALLBACK_CATEGORIES)
    return data


def categories_rules() -> dict:
    """返回 {类别: {keywords, info_types, note}}，按文件顺序。"""
    cats = load_categories().get("categories") or {}
    out = {}
    for name, spec in cats.items():
        if not isinstance(spec, dict):
            continue
        out[str(name)] = {
            "keywords": tuple(spec.get("keywords") or ()),
            "info_types": tuple(spec.get("info_types") or ()),
            "note": str(spec.get("note") or ""),
        }
    return out


def refusal_patterns() -> tuple[str, ...]:
    return tuple(load_categories().get("refusal_patterns") or _FALLBACK_CATEGORIES["refusal_patterns"])


_FALLBACK_OOD = {
    "scenic_intent": "怎么玩|怎么安排|怎么走|游览|参观|好玩|值得|适合|能去|需要注意|要带|准备|推荐",
    "recent_time": "今天|明天|现在|这周|周末|最近",
    "weather_status": "天气|气温|温度|多少度|热不热|冷不冷|下雨吗|会下雨",
    "weather_param": "天气预报|气温|温度|多少度",
    "patterns": [],
}


def ood_rules() -> dict:
    raw = load_categories().get("out_of_domain")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):          # 兼容旧格式（纯 patterns 列表）
        merged = dict(_FALLBACK_OOD)
        merged["patterns"] = raw
        return merged
    return dict(_FALLBACK_OOD)


def is_out_of_domain(text: str) -> bool:
    """判断是否「不该答」的问题。

    ★ 这里是全项目最容易判错的地方 —— 天气类问题横跨"域外"和"景区问题"两边：

        「今天天气怎么样」   → 域外（问实时状况，知识库答不了，拒答是对的）
        「灵山明天下雨吗」   → 域外（同上）
        「下雨天怎么玩」     → **景区问题**（知识库有"雨天注意"）
        「哪个季节去最好」   → **景区问题**
        「夏天去热不热」     → **景区问题**（问的是体验，不是气温）

    判据不是"有没有天气两个字"，而是"**在问实时状况，还是在问游玩建议**"。
    所以顺序很关键：

      ① 先看有没有**景区意图**（怎么玩/怎么安排/适合…）——有就绝不算域外
      ② 再看**近期时间词 + 天气词**是否同时出现 → 域外
      ③ 最后看**气象参数词**（气温/温度/天气预报）→ 域外

    第 ① 步放在最前面，是为了"雨天怎么玩"这种问题不被误杀。
    """
    t = text or ""
    rules = ood_rules()

    # ① 景区意图优先 —— 在问"怎么玩/怎么安排"的一律不算域外
    scenic = rules.get("scenic_intent")
    if scenic and re.search(scenic, t):
        return False

    # ② 近期时间词 + 天气状况词 同时出现 → 在问实时天气
    recent = rules.get("recent_time")
    status = rules.get("weather_status")
    if recent and status and re.search(recent, t) and re.search(status, t):
        return True

    # ③ 明确的气象参数词 → 只可能在问实时气象
    param = rules.get("weather_param")
    if param and re.search(param, t):
        return True

    # ④ 其他域外模式（编程、股票…）
    for pattern in (rules.get("patterns") or []):
        try:
            if re.search(pattern, t, flags=re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


@app.route("/api/categories", methods=["GET", "PUT"])
def api_categories():
    """类别规则的读写。管理端改完 PUT 回来即可生效（无需重启）。"""
    if request.method == "GET":
        data = load_categories()
        data["_file"] = str(CATEGORIES_FILE)
        data["_exists"] = CATEGORIES_FILE.is_file()
        return jsonify(data)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("categories"), dict):
        return jsonify({"success": False, "message": "格式不对：需要 {categories: {...}}"}), 400
    CATEGORIES_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify({"success": True, "message": "已保存，立即生效",
                    "categories": list((payload.get("categories") or {}).keys())})


def classify_query(query: str) -> str:
    """把一个问题归到某个类别。命中多个时按 CATEGORY_RULES 的顺序取第一个。"""
    q = query or ""
    for category, rule in categories_rules().items():
        if any(kw in q for kw in rule["keywords"]):
            return category
    return "其他"


def conversation_pairs() -> list[dict]:
    """把 Fay 的对话记录配成"问-答"对。

    为什么要配对：判断"答得好不好"必须同时看问题和回答。
    T_Msg 按 id 有序，用户消息后面跟的就是 Fay 的回答，所以按顺序配对即可。
    """
    pairs: list[dict] = []
    pending = None
    for mtype, _way, content, ts, username in _fay_msg_rows():
        if mtype == "fay":
            if pending:
                pairs.append({"q": pending[0], "a": content or "", "ts": pending[1],
                              "username": pending[2]})
                pending = None
        else:
            pending = (content or "", ts, username)
    return pairs


def kb_chunk_count_by_info_type() -> dict[str, int]:
    """统计各类知识块的数量（按 info_type），用来判断"这一类有没有知识支撑"。"""
    counts: dict[str, int] = {}
    for d, _ in each_plugin():
        if (d / "graph").is_dir():
            continue
        for name in ("knowledge.json", "uploaded.json"):
            data = read_json(d / "corpus" / name, {}) or {}
            for chunk in (data.get("chunks") or []):
                it = chunk.get("info_type") or "未分类"
                counts[it] = counts.get(it, 0) + 1
    return counts


@app.get("/api/stats/gaps")
def stats_gaps():
    """知识缺口分析：按问题类别聚合，指出该补哪一块知识。

    这是"知识库自进化"的输入 —— 告诉管理者该补什么，而不是列一堆没人问的知识块。
    """
    # 1) 收集所有查询及其结果
    queries: list[dict] = []
    for d, m in each_plugin():
        for c in read_calls(d):
            if not str(c.get("tool", "")).endswith("_query"):
                continue
            q = (c.get("query") or "").strip()
            if not q:
                continue
            # ★ 只统计"真的跑通了"的调用。
            #   ok=false 是系统报错（如模型加载失败），拿它算命中率会把"系统故障"
            #   误报成"知识缺口"（实测踩过：一次报错让门票类命中率掉到 50%）
            if c.get("ok") is False:
                continue
            # 归一化"是否可靠"：向量插件写 reliable，图谱插件写 ok
            # （实测踩过：图谱那 1 次没有 reliable 字段，被当成"没命中"，
            #   导致"景点介绍"类命中率假性 0%）
            # 按当前阈值重判，理由见 call_is_reliable 的注释
            band = call_band(c)
            reliable = (band == "strong")
            queries.append({
                "query": q,
                "plugin": m.get("slug") or d.name,
                "reliable": reliable,
                "band": band,
                "out_of_domain": is_out_of_domain(q),
            })

    info_type_counts = kb_chunk_count_by_info_type()

    # 2) 按类别聚合
    buckets: dict[str, dict] = {}
    for item in queries:
        cat = classify_query(item["query"])
        b = buckets.setdefault(cat, {"category": cat, "queries": set(), "reliable": 0,
                                     "total": 0, "missed": [], "domain_total": 0,
                                     "weak": 0})
        b["queries"].add(item["query"])
        b["total"] += 1
        if item["reliable"] is True:
            b["reliable"] += 1
        elif item.get("band") == "weak":
            b["weak"] += 1
        else:
            if not item["out_of_domain"]:
                b["domain_total"] += 1
                b["missed"].append(item["query"])

    # 3) 算缺口
    #   类别规则从 categories.json 读（管理端「分类规则」页可改），不再写死在代码里
    cats = categories_rules()
    rows = []
    for cat, b in buckets.items():
        info_types = cats.get(cat, {}).get("info_types", ())
        kb_count = sum(info_type_counts.get(it, 0) for it in info_types)
        hit_rate = (b["reliable"] / b["total"]) if b["total"] else 0.0

        # ── 缺口判定 ──
        #
        # ★ 这里修过一个会误导人的逻辑。原来的写法是：
        #       gap = (not has_kb) or (hit_rate < 0.6)
        #   只要 kb_count == 0 就判「严重」，**完全无视命中率**。
        #   结果「交通停车」被问了 3 次、命中率 100%，却报「严重缺口」——
        #   自相矛盾，而且会让人去补一条本来不缺的知识。
        #
        #   命中率是我们手里唯一反映"实际效果"的信号，
        #   不能因为"找不到它对应哪种知识类型"就把它否掉。
        #
        # ★ 另一个坑：「其他」是**没有命中任何分类规则**的问题集合，
        #   不是一个知识领域。对它说"建议补充这一类知识"毫无意义——
        #   该补的是 categories.json 里的关键词。
        has_kb = kb_count > 0
        is_uncategorized = (cat == "其他")
        missed_n = b["total"] - b["reliable"]
        weak_n = b["weak"]
        hard_miss = missed_n - weak_n        # 距离超上限、内容根本没给出去的那部分

        # ── 三级判定：绿 / 黄 / 红（分界线见文件上方 OK_RATE / WARN_RATE）──
        #
        # ★ 先过样本量这一关：次数太少就不判级别，显示"样本不足"。
        #   否则会出现"问过 1 次、恰好命中 -> 充足"这种噪声结论。
        if b["total"] < MIN_SAMPLE:
            level = "unknown"
            advice = (f"这一类只被问过 {b['total']} 次，样本太少，"
                      f"现在下任何结论都不可靠（命中率 {hit_rate:.0%}）。"
                      f"多积累几次查询再看。")

        elif hit_rate >= OK_RATE:
            level = "ok"
            advice = (f"「{cat}」命中率 {hit_rate:.0%}，现有 {kb_count} 块知识，答得稳")

        elif hit_rate >= WARN_RATE:
            level = "medium"
            advice = (f"「{cat}」被问 {b['total']} 次，命中率 {hit_rate:.0%}"
                      f"（{missed_n} 次没拿到铁证）。"
                      + (f"知识有 {kb_count} 块，可以再补细一点，或把常问的问题单独做成知识块。"
                         if has_kb else
                         "知识库里没有对应知识块，建议补充这一类内容。"))

        elif not has_kb and not is_uncategorized:
            # 命中率低 **且** 知识库里确实没有这一类 —— 这才是"要喂资料"的真缺口
            level = "high"
            advice = (f"「{cat}」被问 {b['total']} 次，命中率只有 {hit_rate:.0%}，"
                      f"而且知识库里没有对应知识块 —— 这一类要补充内容。")

        else:
            # 命中率低，但知识是有的（或者这一类根本没被规则归类）：
            # 问题不在"缺内容"，而在检索/切分/归类，行动项完全不同。
            level = "medium"
            if is_uncategorized:
                advice = (f"{b['total']} 个问题里 {missed_n} 次没拿到铁证，"
                          f"而且这些问题都没被现有类别规则命中。"
                          f"第一步：去「分类规则」补关键词，把它们归到具体类别——"
                          f"归好类才看得出来该补什么知识。")
            elif weak_n > hard_miss:
                # 多数是"弱相关"而不是"完全没有" —— 这两种的情况完全不同：
                #   弱相关 = 检索找到了东西，只是不够准 -> 多半是问法/切分问题
                #   完全没有 = 检索一无所获 -> 多半是真没有这块内容
                # 注意：这些字符串会**原样显示在页面上**（Element Plus 表格单元格，
                # 不走 markdown 渲染），所以不能写 **加粗** 这类标记——
                # 之前写了一次，页面上直接把星号显示出来了。
                advice = (f"「{cat}」被问 {b['total']} 次，命中率 {hit_rate:.0%}，"
                          f"其中 {weak_n} 次是弱相关（相似度在中间档，不算命中），"
                          f"只有 {hard_miss} 次完全没找到。"
                          f"知识库里有 {kb_count} 块 —— 说明内容大致在，"
                          f"但检索匹配得不够准。可以从两处查："
                          f"① 用户是不是一次问了好几件事（复合问题向量匹配天然差）；"
                          f"② 这一类的知识块是不是切得太粗。")
            else:
                advice = (f"「{cat}」被问 {b['total']} 次，命中率 {hit_rate:.0%}"
                          f"（{missed_n} 次没拿到铁证），但知识库里有 {kb_count} 块。"
                          f"缺的不是内容，是检索没找准 —— "
                          f"可以看这一类的知识块切分是否太粗、或关键词是否需要补充。")

        rows.append({
            "category": cat,
            "query_count": b["total"],
            "unique_queries": len(b["queries"]),
            "hit_rate": round(hit_rate, 4),
            "weak_count": b["weak"],
            "kb_chunks": kb_count,
            "info_types": list(info_types),
            "gap_level": level,
            "advice": advice,
            "missed_examples": b["missed"][:5],
        })

    order = {"high": 0, "medium": 1, "ok": 2}
    rows.sort(key=lambda r: (order.get(r["gap_level"], 9), -r["query_count"]))

    gaps = [r for r in rows if r["gap_level"] in ("high", "medium")]
    return jsonify({
        "categories": rows,
        "gap_count": len(gaps),
        "high_gap_count": len([r for r in rows if r["gap_level"] == "high"]),
        "total_queries": len(queries),
        "out_of_domain_queries": len([q for q in queries if q["out_of_domain"]]),
        "info_type_counts": info_type_counts,
    })


def looks_like_refusal(answer: str) -> bool:
    """回答里出现"无法确认 / 暂时没有"这类措辞 → 算一次拒答。

    措辞列表在 categories.json 的 refusal_patterns 里。数字人换了说法
    （比如改成"我不太确定"），在那里加一条就行，不用改代码。
    """
    a = answer or ""
    return any(p in a for p in refusal_patterns())


@app.get("/api/stats/refusals")
def stats_refusals():
    """拒答分析：把「正确拒答」和「知识缺口」分开。

    ★ 为什么要分开：
      问"今天天气怎么样"被拒答是**正确行为**；
      问"有没有轮椅租借"被拒答才是**真缺口**。
      不区分的话，自进化会去补"灵山天气"这种垃圾知识。
    """
    pairs = conversation_pairs()
    refusals = []
    for p in pairs:
        if not looks_like_refusal(p["a"]):
            continue
        ood = is_out_of_domain(p["q"]) or is_out_of_domain(p["a"])
        refusals.append({
            "question": p["q"],
            "answer": (p["a"] or "")[:160],
            "ts": p["ts"],
            "type": "out_of_domain" if ood else "knowledge_gap",
            "category": classify_query(p["q"]),
        })

    knowledge_gaps = [r for r in refusals if r["type"] == "knowledge_gap"]
    ood = [r for r in refusals if r["type"] == "out_of_domain"]

    return jsonify({
        "total_pairs": len(pairs),
        "refusal_count": len(refusals),
        "refusal_rate": round(len(refusals) / len(pairs), 4) if pairs else 0.0,
        "knowledge_gap_count": len(knowledge_gaps),
        "out_of_domain_count": len(ood),
        "knowledge_gaps": knowledge_gaps,
        "out_of_domain": ood,
        "note": "域外问题（问天气、写代码）拒答是对的，不用补；知识库确实没有、游客又问到的，才要补。",
    })


@app.get("/api/stats/entities")
def stats_entities():
    """按**景点**看关注度 —— 和"按问题类别"是两个互补的视角。

        类别视角：该补哪一类知识（交通、住宿…）
        景点视角：该补哪个景点的知识（灵山大佛被问很多，鹿鸣谷没人问）
    """
    entities: dict[str, dict] = {}

    # 向量插件：埋点里有 top_spots（这次命中了哪些景点）
    for d, m in each_plugin():
        if (d / "graph").is_dir():
            continue
        slug = m.get("slug") or d.name
        for c in read_calls(d):
            if not str(c.get("tool", "")).endswith("_query"):
                continue
            for spot in (c.get("top_spots") or [])[:1]:   # 只取最相关的那一条
                if not spot:
                    continue
                e = entities.setdefault(spot, {"name": spot, "queries": 0, "hit": 0,
                                               "plugins": set(), "categories": {}})
                e["queries"] += 1
                if c.get("reliable") is not False:
                    e["hit"] += 1
                e["plugins"].add(slug)
                cat = classify_query(c.get("query") or "")
                e["categories"][cat] = e["categories"].get(cat, 0) + 1

    # 图谱插件：hit_entities
    for d, m in each_plugin():
        if not (d / "graph").is_dir():
            continue
        for c in read_calls(d):
            for ent in (c.get("hit_entities") or [])[:1]:
                e = entities.setdefault(ent, {"name": ent, "queries": 0, "hit": 0,
                                              "plugins": set(), "categories": {}})
                e["queries"] += 1
                e["hit"] += 1
                e["plugins"].add(m.get("slug") or d.name)

    rows = []
    for e in entities.values():
        top_cat = max(e["categories"].items(), key=lambda kv: kv[1])[0] if e["categories"] else ""
        rows.append({
            "name": e["name"],
            "queries": e["queries"],
            "hit": e["hit"],
            "hit_rate": round(e["hit"] / e["queries"], 4) if e["queries"] else 0.0,
            "plugin": ",".join(sorted(e["plugins"])),
            "top_category": top_cat,
        })
    rows.sort(key=lambda r: r["queries"], reverse=True)
    return jsonify({
        "entities": rows,
        "count": len(rows),
        "note": "按景点关注度排序。冷门景点不一定有问题，但'被问了很多却命中率低'的景点值得补知识",
    })


@app.get("/api/stats/tourism")
def stats_tourism():
    """游客行为数据分析（来自资料包的 xlsx，14 万行，聚合后缓存）。

    ⚠️ 这份数据是**跨景区通用数据**（含宁波方特等），定位是"行业参考/游客画像"，
       不是灵山的运营数据。前端要标注清楚。
    """
    global _TOURISM_CACHE
    if _TOURISM_CACHE:
        return jsonify(_TOURISM_CACHE)

    files = list(SOURCE_DIR.glob("*.xlsx")) if SOURCE_DIR.is_dir() else []
    if not files:
        return jsonify({"error": "没有找到 xlsx 数据文件"}), 404
    try:
        from openpyxl import load_workbook

        wb = load_workbook(files[0], read_only=True, data_only=True)
        ws = wb[wb.sheetnames[0]]
        rows = ws.iter_rows(values_only=True)
        header = [str(h or "").strip() for h in next(rows)]
        idx = {name: i for i, name in enumerate(header)}

        def col(row, name):
            i = idx.get(name)
            return row[i] if i is not None and i < len(row) else None

        total = 0
        attraction_type = Counter()
        top_attractions = Counter()
        gender = Counter()
        age_buckets = Counter()
        stay_buckets = Counter()
        ticket_buckets = Counter()
        month = Counter()

        for row in rows:
            total += 1
            attraction_type[str(col(row, "attraction_type") or "未知")] += 1
            top_attractions[str(col(row, "attraction_name") or "未知")] += 1
            gender[str(col(row, "gender") or "未知")] += 1
            try:
                age = float(col(row, "age") or 0)
                age_buckets[f"{int(age // 10) * 10}-{int(age // 10) * 10 + 9}"] += 1
            except (TypeError, ValueError):
                pass
            try:
                stay = float(col(row, "stay_duration") or 0)
                stay_buckets[f"{int(stay // 2) * 2}-{int(stay // 2) * 2 + 2}小时"] += 1
            except (TypeError, ValueError):
                pass
            try:
                price = float(col(row, "ticket_cost") or 0)
                ticket_buckets[f"{int(price // 50) * 50}-{int(price // 50) * 50 + 50}元"] += 1
            except (TypeError, ValueError):
                pass
            raw_date = str(col(row, "visit_date") or "")
            if len(raw_date) >= 7:
                month[raw_date[:7]] += 1
        wb.close()

        def top(counter, n=12):
            return [{"name": k, "value": v} for k, v in counter.most_common(n)]

        _TOURISM_CACHE = {
            "source": files[0].name,
            "disclaimer": "这是跨景区通用行为数据（含宁波方特等），属于行业参考，不是灵山的运营数据",
            "total_rows": total,
            "columns": header,
            "attraction_type": top(attraction_type, 15),
            "top_attractions": top(top_attractions, 15),
            "gender": top(gender, 10),
            "age_buckets": sorted(({"name": k, "value": v} for k, v in age_buckets.items()),
                                  key=lambda x: int(x["name"].split("-")[0])),
            "stay_buckets": sorted(({"name": k, "value": v} for k, v in stay_buckets.items()),
                                   key=lambda x: int(x["name"].split("-")[0])),
            "ticket_buckets": sorted(({"name": k, "value": v} for k, v in ticket_buckets.items()),
                                     key=lambda x: int(x["name"].split("-")[0])),
            "by_month": [{"name": k, "value": v} for k, v in sorted(month.items())],
        }
        return jsonify(_TOURISM_CACHE)
    except Exception as exc:
        return jsonify({"error": f"解析 xlsx 失败：{exc}"}), 500


# ─────────────────────────────────────────────────────────────
# 知识库自进化：分析结果 + 审核操作
# ─────────────────────────────────────────────────────────────
#
# 数据来自 离线分析 agent（scripts/analyze_knowledge.py，用 DeepSeek v4-flash）
# 产出的 docs/knowledge-evolution.json。管理端只读它 + 改审核状态。
#
# 为什么把"分析"和"审核"分开：
#   分析是离线的、可以重跑、结果会被覆盖；
#   审核是人工的决定，**必须持久**。
#   所以分析脚本每次运行都会保留已审核条目的状态（见脚本里的 load_prev_status）。

EVOLUTION_FILE = DOCS_DIR / "knowledge-evolution.json"


def load_evolution() -> dict:
    data = read_json(EVOLUTION_FILE, None)
    if not isinstance(data, dict):
        return {"available": False, "gaps": [], "redundant": [], "conflicts": []}
    data["available"] = True
    return data


@app.get("/api/evolution")
def get_evolution():
    """给管理端：缺口主题 + 冗余候选 + 矛盾清单。"""
    return jsonify(load_evolution())


@app.post("/api/evolution/<section>/<path:item_id>/status")
def set_evolution_status(section: str, item_id: str):
    """审核一条：accepted / rejected / done（标记已补）。"""
    if section not in ("gaps", "redundant", "conflicts"):
        return jsonify({"ok": False, "message": f"不认识的分类：{section}"}), 400
    body = request.get_json(silent=True) or {}
    status = str(body.get("status") or "").strip()
    if status not in ("pending", "accepted", "rejected", "done"):
        return jsonify({"ok": False, "message": f"不认识的状态：{status}"}), 400

    data = read_json(EVOLUTION_FILE, None)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "message": "还没有分析结果，先跑 analyze_knowledge.py"}), 404

    found = None
    for item in (data.get(section) or []):
        if str(item.get("id") or item.get("topic") or item.get("scope")) == item_id:
            item["status"] = status
            item["reviewed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if body.get("note"):
                item["note"] = str(body["note"])[:300]
            found = item
            break
    if found is None:
        return jsonify({"ok": False, "message": f"没找到：{section}/{item_id}"}), 404

    write_json_local(EVOLUTION_FILE, data)
    return jsonify({"ok": True, "item": found})


if __name__ == "__main__":
    port = int(os.getenv("ADMIN_API_PORT", "5174"))
    print("=" * 66)
    print(f"灵山管理端后端  http://127.0.0.1:{port}")
    print(f"  项目目录 : {PROJECT}")
    print(f"  Fay 目录 : {FAY_DIR}")
    print(f"  MCP 接口 : {FAY_MCP_API}")
    print("=" * 66)
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
