#!/usr/bin/env python3
"""文档入库：把一份文档切块、写进 Chroma，并**同时落到语料文件**。

★ 为什么要落语料文件
--------------------
只写 Chroma 的话，一旦重建索引（build_index.py --recreate）就会把上传的内容冲掉。
所以上传的块要追加到 `corpus/uploaded.json`，
build_index 重建时会把 `knowledge.json`（离线语料）+ `uploaded.json`（上传语料）一起读。

切块策略（与旧项目对齐）
------------------------
1. 文档里若有 `@@@RAG_CHUNK_START@@@` 标记 → 按标记解析（结构化语料，最理想）
2. 否则按 Markdown 标题切分；再长就按段落合并到目标长度

用法（一般由 MCP 工具调用，也可命令行单独跑）
    python ingest.py --file 某文档.md
    python ingest.py --text "灵山新增了夜游项目，18:30 开始。"
    python ingest.py --list            # 看已入库的上传内容
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path


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
CORPUS_DIR = HERE / "corpus"
UPLOADED_PATH = CORPUS_DIR / "uploaded.json"
MANIFEST = HERE / "plugin.json"
COLLECTION = HERE.name.replace("-", "_").replace(".", "_")
EMBED_MODEL_DIR = Path(
    os.getenv(
        "LINGSHAN_EMBED_MODEL",
        r"D:\PROJECT\scenic_ai\灵山AI导游系统\backend\models\embeddings\BAAI--bge-small-zh-v1.5",
    )
)

CHUNK_MARK_START = "@@@RAG_CHUNK_START@@@"
CHUNK_MARK_END = "@@@RAG_CHUNK_END@@@"
TARGET_CHUNK_CHARS = 350

# 保护标记：与 scripts/build_corpus.py 保持同一套判定
PROTECT_RULES = (
    ("safety",
     ("安全", "危险", "紧急", "疏散", "消防", "湿滑", "防滑", "滑倒", "跌落", "水深", "雷电", "拥挤"),
     ("需注意", "注意安全", "小心", "谨防", "避免", "务必", "切勿")),
    ("service",
     ("无障碍", "轮椅", "行动不便", "医疗", "救护", "医务", "急救", "AED",
      "失物", "投诉", "母婴", "寄存", "求助", "服务台", "便民"),
     ()),
    ("conduct",
     ("禁止", "严禁", "请勿", "不得", "不可"),
     ("喧哗", "触摸", "攀爬", "拍照", "闪光灯", "吸烟", "踩踏", "采摘",
      "游泳", "垂钓", "投喂", "追逐", "打闹", "携带", "乱扔", "戏水")),
)


def detect_protection(text: str) -> tuple[bool, str]:
    for reason, triggers, prompts in PROTECT_RULES:
        if not any(kw in text for kw in triggers):
            continue
        if prompts and not any(kw in text for kw in prompts):
            continue
        return True, reason
    return False, ""


def plugin_name() -> str:
    if MANIFEST.is_file():
        try:
            return str(json.loads(MANIFEST.read_text(encoding="utf-8")).get("name") or HERE.name)
        except (json.JSONDecodeError, OSError):
            pass
    return HERE.name


# ─────────────────────────────────────────────────────────────
# 切块
# ─────────────────────────────────────────────────────────────

def split_document(text: str, source: str) -> list[dict]:
    """文档 → 知识块列表。"""
    text = (text or "").replace("\r\n", "\n").strip()
    if not text:
        return []
    if CHUNK_MARK_START in text:
        return _split_marked(text, source)
    return _split_markdown(text, source)


def _split_marked(text: str, source: str) -> list[dict]:
    """按 @@@RAG_CHUNK_START@@@ 标记解析（结构化语料）。"""
    chunks = []
    for raw in text.split(CHUNK_MARK_START)[1:]:
        block = raw.split(CHUNK_MARK_END, 1)[0]

        def field(label: str) -> str:
            m = re.search(rf"{label}：\s*(.+)", block)
            return m.group(1).strip() if m else ""

        content_m = re.search(r"内容：\s*(.+?)(?:$)", block, flags=re.DOTALL)
        content = re.sub(r"\s+", " ", content_m.group(1)).strip() if content_m else ""
        if len(content) < 4:
            continue
        questions = [q.strip() for q in re.findall(r"^-\s*(.+)$", block, flags=re.MULTILINE)]
        protected, reason = detect_protection(content)
        chunks.append({
            "spot_id": field("景点ID"),
            "spot_name": field("景点名称"),
            "scenic_name": field("景区") or plugin_name(),
            "info_type": field("信息类型") or "上传文档",
            "source_field": source,
            "content": content,
            "protected": protected,
            "protect_reason": reason,
            "priority": 1_000_000_000,
            "tags": [t.strip() for t in re.split(r"[、,，]", field("语义标签")) if t.strip()],
            "questions": questions,
        })
    return chunks


def _split_markdown(text: str, source: str) -> list[dict]:
    """按标题切分，段落合并到目标长度。"""
    # 先按标题切成小节
    sections: list[tuple[str, list[str]]] = []
    current_title, current_lines = "", []
    for line in text.split("\n"):
        if re.match(r"^#{1,6}\s+", line):
            if current_lines:
                sections.append((current_title, current_lines))
            current_title, current_lines = re.sub(r"^#{1,6}\s+", "", line).strip(), []
        else:
            current_lines.append(line)
    if current_lines:
        sections.append((current_title, current_lines))

    chunks: list[dict] = []
    for title, lines in sections:
        buf = ""
        for para in [p.strip() for p in "\n".join(lines).split("\n\n") if p.strip()]:
            if buf and len(buf) + len(para) > TARGET_CHUNK_CHARS:
                chunks.append(_make_chunk(title, buf, source))
                buf = para
            else:
                buf = f"{buf}\n{para}".strip()
        if buf:
            chunks.append(_make_chunk(title, buf, source))
    return [c for c in chunks if c]


def _make_chunk(title: str, body: str, source: str) -> dict:
    content = body.strip()
    protected, reason = detect_protection(f"{title} {content}")
    return {
        "spot_id": "",
        "spot_name": title or "",
        "scenic_name": plugin_name(),
        "info_type": "上传文档",
        "source_field": source,
        "content": f"{title}\n{content}" if title else content,
        "protected": protected,
        "protect_reason": reason,
        "priority": 1_000_000_000,
        "tags": [],
        "questions": [],
    }


def chunk_id(c: dict) -> str:
    raw = f"{c.get('source_field')}|{c.get('spot_name')}|{c.get('info_type')}|{c.get('content')}"
    return "up-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


# ─────────────────────────────────────────────────────────────
# 落库
# ─────────────────────────────────────────────────────────────

def load_uploaded() -> list[dict]:
    if UPLOADED_PATH.is_file():
        try:
            return json.loads(UPLOADED_PATH.read_text(encoding="utf-8")).get("chunks", [])
        except (json.JSONDecodeError, OSError):
            pass
    return []


def save_uploaded(chunks: list[dict]) -> None:
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADED_PATH.write_text(
        json.dumps({"version": 1, "chunks": chunks}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def embed_text(c: dict) -> str:
    return " ".join(
        p for p in (
            c.get("scenic_name") or "", c.get("spot_name") or "", c.get("info_type") or "",
            "、".join(c.get("tags") or []), " ".join(c.get("questions") or []), c.get("content") or "",
        ) if p
    )


def ingest(text: str, source: str, dry_run: bool = False) -> dict:
    from sentence_transformers import SentenceTransformer  # noqa: F401  (延迟导入)

    new_chunks = split_document(text, source)
    if not new_chunks:
        return {"success": False, "message": "文档为空或无法切块", "added": 0}

    existing = load_uploaded()
    existing_ids = {chunk_id(c) for c in existing}
    added = [c for c in new_chunks if chunk_id(c) not in existing_ids]
    skipped = len(new_chunks) - len(added)

    if not added:
        return {"success": True, "message": "内容已存在，未新增", "added": 0,
                "skipped": skipped, "total_uploaded": len(existing)}

    if dry_run:
        return {"success": True, "message": "dry-run，未写入", "added": len(added),
                "skipped": skipped, "preview": [c["content"][:80] for c in added[:3]]}

    # 1) 写 Chroma（增量，不重建）
    import chromadb

    if not (INDEX_DIR / "chroma.sqlite3").is_file():
        return {"success": False, "message": f"向量库不存在：{INDEX_DIR}，请先建索引", "added": 0}

    model = SentenceTransformer(str(EMBED_MODEL_DIR), device="cpu")
    client = chromadb.PersistentClient(path=str(INDEX_DIR))
    col = client.get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})

    ids = [chunk_id(c) for c in added]
    docs = [c["content"] for c in added]
    vectors = model.encode([embed_text(c) for c in added], normalize_embeddings=True)
    metas = [{
        "source": f"upload/{source}",
        "scenic_name": c.get("scenic_name") or "",
        "spot_id": c.get("spot_id") or "",
        "spot_name": c.get("spot_name") or "",
        "info_type": c.get("info_type") or "",
        "tags": "、".join(c.get("tags") or []),
        "questions_json": json.dumps(c.get("questions") or [], ensure_ascii=False),
        "priority": float(c.get("priority") or 1_000_000_000),
        "is_fact": True,
        "protected": bool(c.get("protected")),
        "protect_reason": c.get("protect_reason") or "",
        "index_version": "uploaded",
        "uploaded": True,
    } for c in added]
    col.add(ids=ids, documents=docs, metadatas=metas, embeddings=[v.tolist() for v in vectors])

    # 2) 落语料文件（这样重建索引不会丢）
    save_uploaded(existing + added)

    protected = [c for c in added if c.get("protected")]
    return {
        "success": True,
        "message": f"已入库 {len(added)} 条",
        "added": len(added),
        "skipped": skipped,
        "total_uploaded": len(existing) + len(added),
        "protected_added": len(protected),
        "collection_count": col.count(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default="")
    parser.add_argument("--text", default="")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.list:
        up = load_uploaded()
        print(f"已上传入库：{len(up)} 条")
        for c in up:
            lock = "🔒" if c.get("protected") else "  "
            print(f"  {lock} [{c.get('source_field')}] {c.get('spot_name')} · {c.get('info_type')}")
            print(f"      {c.get('content','')[:70]}")
        return 0

    if args.file:
        path = Path(args.file)
        if not path.is_file():
            print(f"文件不存在：{path}")
            return 2
        text, source = path.read_text(encoding="utf-8", errors="ignore"), path.name
    elif args.text:
        text, source = args.text, "命令行输入"
    else:
        parser.print_help()
        return 2

    result = ingest(text, source, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
