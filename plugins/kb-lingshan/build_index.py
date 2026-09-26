#!/usr/bin/env python3
"""把 knowledge.json 向量化并写进 Chroma。

★ 最要紧的一件事：metadata 的字段名必须和旧项目检索层对齐
--------------------------------------------------------------
我们要复用旧项目的 vector_retriever，它的 _fact_from_metadata 读的是固定字段名：
    source / scenic_name / spot_id / spot_name / info_type
    tags / questions_json / priority / is_fact
字段名对不上 = 检索层读不到 = 白干。所以下面 METADATA 的映射不是随便起的。

另外两点设计
------------
1. **嵌入的文本 ≠ 存进库的文本**
   · 嵌入用「字段拼接」后的长文本（景点名+信息类型+标签+问法+正文）
     —— 这是旧项目的技巧，让"用户怎么问"也能被匹配到
   · 存库里的是**纯正文** —— 因为它是最终喂给 LLM 的东西，不该带一堆标签
2. **写一份 index_meta.json** 记录语料指纹，用来判断索引是否过期

用法
----
    python build_index.py              # 重建索引
    python build_index.py --stats      # 只看当前索引状态
    python build_index.py --query "灵山大佛有多高"   # 顺手查一条试试
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
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
PROJECT = HERE.parent.parent
CORPUS = HERE / "corpus" / "knowledge.json"
UPLOADED = HERE / "corpus" / "uploaded.json"
INDEX_DIR = HERE / "index"
META_PATH = INDEX_DIR / "index_meta.json"
# 与 server.py 保持一致：从目录名派生，复制插件目录即得到独立知识库
COLLECTION = HERE.name.replace("-", "_").replace(".", "_")

EMBED_MODEL_DIR = Path(
    r"D:\PROJECT\scenic_ai\灵山AI导游系统\backend\models\embeddings\BAAI--bge-small-zh-v1.5"
)


def corpus_fingerprint(chunks: list[dict]) -> str:
    h = hashlib.sha256()
    for c in chunks:
        h.update((c.get("content") or "").encode("utf-8"))
        h.update((c.get("info_type") or "").encode("utf-8"))
    return h.hexdigest()[:16]


def load_all_chunks() -> list[dict]:
    """离线语料 + 上传语料。

    ★ 必须带上 uploaded.json，否则重建索引会把通过 ingest 上传的内容冲掉。
    """
    chunks: list[dict] = []
    for path in (CORPUS, UPLOADED):
        if not path.is_file():
            continue
        try:
            chunks.extend(json.loads(path.read_text(encoding="utf-8")).get("chunks", []))
        except (json.JSONDecodeError, OSError):
            pass
    return chunks


def chunk_id(c: dict, n: int) -> str:
    """稳定 ID：同一块知识重建索引后 ID 不变。"""
    raw = f"{c.get('spot_id')}|{c.get('spot_name')}|{c.get('info_type')}|{c.get('source_field')}"
    return "kb-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def embed_text(c: dict) -> str:
    """嵌入用的文本 = 字段拼接（旧项目的"低成本字段加权"技巧）。

    把景点名、信息类型、标签、以及"游客可能怎么问"都拼进去，
    这样用户用口语问也能命中——因为那些问法本来就在这段文本里。
    """
    parts = [
        c.get("scenic_name") or "",
        c.get("spot_name") or "",
        c.get("info_type") or "",
        "、".join(c.get("tags") or []),
        " ".join(c.get("questions") or []),
        c.get("content") or "",
    ]
    return " ".join(p for p in parts if p)


def build_metadata(c: dict, index_version: str) -> dict:
    """★ 字段名对齐旧项目 vector_retriever._fact_from_metadata。不要随便改名。"""
    return {
        # ── 旧项目检索层要读的字段 ──
        "source": f"lingshan/{c.get('source_field') or 'kb'}",
        "scenic_name": c.get("scenic_name") or "灵山胜境",
        "spot_id": c.get("spot_id") or "",
        "spot_name": c.get("spot_name") or "",
        "info_type": c.get("info_type") or "",
        "tags": "、".join(c.get("tags") or []),          # 旧项目存的是字符串，顿号分隔
        "questions_json": json.dumps(c.get("questions") or [], ensure_ascii=False),
        "priority": float(c.get("priority") or 100),
        "is_fact": True,                                  # 我们这批全是结构化事实
        # ── 本项目新增 ──
        "protected": bool(c.get("protected")),
        "protect_reason": c.get("protect_reason") or "",
        "index_version": index_version,
    }


def load_model():
    if not EMBED_MODEL_DIR.is_dir():
        raise SystemExit(f"找不到 BGE 模型目录：{EMBED_MODEL_DIR}")
    from sentence_transformers import SentenceTransformer

    device = "cpu"
    try:
        import torch

        if torch.cuda.is_available():
            device = "cuda"
    except Exception:
        pass
    print(f"加载 BGE 模型…（device={device}）")
    return SentenceTransformer(str(EMBED_MODEL_DIR), device=device)


def open_collection(recreate: bool = False):
    import chromadb

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    if recreate and (INDEX_DIR / "chroma.sqlite3").exists():
        for child in INDEX_DIR.iterdir():
            if child.name == "index_meta.json":
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    client = chromadb.PersistentClient(path=str(INDEX_DIR))
    return client.get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})


def do_build() -> int:
    chunks = load_all_chunks()
    if not chunks:
        print(f"没有语料可索引。请确认 {CORPUS} 存在。")
        return 2
    version = corpus_fingerprint(chunks)

    model = load_model()
    col = open_collection(recreate=True)

    ids, docs, metas, texts = [], [], [], []
    for i, c in enumerate(chunks):
        ids.append(chunk_id(c, i))
        docs.append(c.get("content") or "")
        metas.append(build_metadata(c, version))
        texts.append(embed_text(c))

    print(f"向量化 {len(texts)} 条…")
    t0 = time.time()
    vectors = model.encode(
        texts,
        normalize_embeddings=True,   # 归一化后，余弦相似度 = 点积
        batch_size=32,
        show_progress_bar=True,
    )
    took = time.time() - t0

    print("写入 Chroma…")
    for start in range(0, len(ids), 100):
        end = start + 100
        col.add(
            ids=ids[start:end],
            documents=docs[start:end],
            metadatas=metas[start:end],
            embeddings=[v.tolist() for v in vectors[start:end]],
        )

    META_PATH.write_text(
        json.dumps(
            {
                "collection": COLLECTION,
                "chunk_count": len(ids),
                "dim": int(vectors.shape[1]),
                "corpus_version": version,
                "embedding_model": EMBED_MODEL_DIR.name,
                "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "protected_count": sum(1 for c in chunks if c.get("protected")),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("=" * 66)
    print(f"完成：{len(ids)} 条，向量维度 {vectors.shape[1]}，耗时 {took:.1f} 秒")
    print(f"索引目录：{INDEX_DIR}")
    print(f"语料指纹：{version}")
    print("=" * 66)
    return 0


def do_stats() -> int:
    if not META_PATH.is_file():
        print("还没有索引。先跑 python build_index.py")
        return 0
    meta = json.loads(META_PATH.read_text(encoding="utf-8"))
    col = open_collection()
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"Chroma 实际条数：{col.count()}")
    return 0


def do_query(text: str, top_k: int = 5) -> int:
    model = load_model()
    col = open_collection()
    vec = model.encode([text], normalize_embeddings=True)[0].tolist()
    res = col.query(query_embeddings=[vec], n_results=top_k,
                    include=["documents", "metadatas", "distances"])
    print(f"\n查询：{text}\n" + "─" * 66)
    for i, (doc, meta, dist) in enumerate(
        zip(res["documents"][0], res["metadatas"][0], res["distances"][0]), 1
    ):
        label = meta.get("spot_name") or meta.get("scenic_name")
        flag = "🔒" if meta.get("protected") else "  "
        print(f"[{i}] {flag} {label} · {meta.get('info_type')}   距离={dist:.4f}")
        print(f"     {doc[:110]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--query", default="")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    if args.stats:
        return do_stats()
    if args.query:
        return do_query(args.query, args.top_k)
    return do_build()


if __name__ == "__main__":
    sys.exit(main())
