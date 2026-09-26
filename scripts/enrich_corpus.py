#!/usr/bin/env python3
"""知识块增强：为每个知识块生成 tags（语义标签）和 questions（游客可能的问法）。

为什么要补这两个字段
--------------------
旧项目的检索质量很大程度上靠它们：
  · questions → find_structured_answer 里的 exact_alias 匹配（用户一问就精确命中）
  · tags      → 同义说法匹配（"多高" / "通高" / "高度" 都能对上）

资料包里没有这两栏，所以要用 LLM 批量生成（见 docs/decisions.md 的 D7）。

特点
----
· 断点续跑：已有 tags 的块默认跳过，中断后重跑不会白费
· 并发：默认 4 路
· 容错：模型可能输出 ```json 包裹或带解释文字，都能剥出来
· 只读 system.conf 拿 key，不硬编码密钥

用法
----
    python enrich_corpus.py --limit 5           # 先跑 5 条看看风格
    python enrich_corpus.py                     # 跑完剩余的
    python enrich_corpus.py --force             # 全部重跑（覆盖已有）
    python enrich_corpus.py --dry-run --limit 3 # 只打印不写文件
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import re
import sys
import time
from pathlib import Path
from urllib import error, request


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
CORPUS_PATH = PROJECT / "plugins" / "kb-lingshan" / "corpus" / "knowledge.json"
FAY_CONF = Path(r"D:\PROJECT\scenic_ai\重构数字人\Fay-main\system.conf")

MODEL = "deepseek-v4-flash"
PROMPT = """你是景区知识库建设助手。请为下面这条知识生成 tags 和 questions。

【景点】{spot}
【信息类型】{info_type}
【内容】{content}

【要求】
1. tags：3-6 个短词标签，帮助检索匹配同义说法（不要重复景点名）
2. questions：2-4 条游客可能怎么问，口语化，像真人提问

只输出 JSON，不要解释：{{"tags":["..."],"questions":["..."]}}"""


def read_fay_conf() -> tuple[str, str]:
    key = base = ""
    for line in FAY_CONF.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("gpt_api_key="):
            key = line.split("=", 1)[1].strip()
        elif line.startswith("gpt_base_url="):
            base = line.split("=", 1)[1].strip()
    if not key or not base:
        raise SystemExit("没能从 Fay 的 system.conf 里读到 gpt_api_key / gpt_base_url")
    return key, base


def parse_json_loose(text: str) -> dict | None:
    """模型可能输出 ```json 包裹或带前后解释，这里尽力把 JSON 抠出来。"""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", t, flags=re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


def call_llm(key: str, base: str, model: str, chunk: dict, retries: int = 2) -> dict | None:
    prompt = PROMPT.format(
        spot=chunk.get("spot_name") or chunk.get("scenic_name") or "",
        info_type=chunk.get("info_type") or "",
        content=(chunk.get("content") or "")[:1200],
    )
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
        },
        ensure_ascii=False,
    ).encode("utf-8")

    for attempt in range(retries + 1):
        try:
            req = request.Request(
                f"{base.rstrip('/')}/chat/completions",
                data=body,
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "Authorization": f"Bearer {key}",
                },
                method="POST",
            )
            with request.urlopen(req, timeout=90) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
            content = payload["choices"][0]["message"]["content"]
            parsed = parse_json_loose(content)
            if parsed and isinstance(parsed.get("tags"), list) and isinstance(parsed.get("questions"), list):
                return {
                    "tags": [str(t).strip() for t in parsed["tags"] if str(t).strip()][:6],
                    "questions": [str(q).strip() for q in parsed["questions"] if str(q).strip()][:4],
                }
        except (error.URLError, OSError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
            if attempt >= retries:
                print(f"    [失败] {chunk.get('spot_name')}/{chunk.get('info_type')}: {exc}", flush=True)
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 条（0=全部）")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--force", action="store_true", help="已有 tags 的也重跑")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--model", default=MODEL)
    args = parser.parse_args()

    if not CORPUS_PATH.is_file():
        print(f"找不到知识库文件：{CORPUS_PATH}")
        return 2
    data = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    chunks = data["chunks"]
    key, base = read_fay_conf()

    todo = chunks if args.force else [c for c in chunks if not c.get("tags")]
    if args.limit:
        todo = todo[: args.limit]

    print("=" * 70)
    print(f"模型      : {args.model}")
    print(f"知识块总数: {len(chunks)}")
    print(f"待处理    : {len(todo)}  （已有 tags 的跳过，--force 可全部重跑）")
    print(f"并发      : {args.concurrency}")
    print("=" * 70)

    if not todo:
        print("没有需要处理的，收工。")
        return 0

    started = time.time()
    done = 0

    def work(chunk: dict) -> tuple[dict, dict | None]:
        return chunk, call_llm(key, base, args.model, chunk)

    with futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for chunk, result in pool.map(work, todo):
            done += 1
            if result:
                chunk["tags"] = result["tags"]
                chunk["questions"] = result["questions"]
            label = f"{chunk.get('spot_name')} · {chunk.get('info_type')}"
            if result:
                print(f"[{done}/{len(todo)}] {label}")
                print(f"    tags      : {result['tags']}")
                print(f"    questions : {result['questions']}")
            else:
                print(f"[{done}/{len(todo)}] {label}  ✗ 未生成")

    elapsed = time.time() - started
    filled = len([c for c in chunks if c.get("tags")])
    print("=" * 70)
    print(f"完成：{done} 条，用时 {elapsed:.0f} 秒")
    print(f"知识库中已有 tags 的块：{filled} / {len(chunks)}")
    print("=" * 70)

    if args.dry_run:
        print("(dry-run：未写文件)")
        return 0

    data["chunk_count"] = len(chunks)
    CORPUS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写回：{CORPUS_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
