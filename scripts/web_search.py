#!/usr/bin/env python3
"""联网检索（走 Bing 的 HTML 结果页）。

为什么是自己发请求
------------------
当前会话没有专门的搜索工具，但**网络是通的**——
前面下载人脸检测模型就成功了。所以直接请求搜索引擎的结果页即可。
实测：Bing 通（200），DuckDuckGo 超时（被墙）。

用法
----
    python web_search.py "anime talking head open source"
    python web_search.py "关键词" --count 12
    python web_search.py "关键词" --json
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT_DIR = ROOT / "docs" / "research"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def _fix_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def fetch(url: str, timeout: float = 30.0) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def strip_tags(s: str) -> str:
    return html_mod.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def search(query: str, count: int = 10) -> list[dict]:
    """查 Bing，返回 [{title, url, snippet}]。"""
    url = "https://www.bing.com/search?" + urllib.parse.urlencode(
        {"q": query, "count": max(count * 2, 20), "setlang": "zh-CN"})
    page = fetch(url)

    out: list[dict] = []
    # 每条结果在 <li class="b_algo"> 里
    for block in re.findall(r'<li class="b_algo".*?</li>', page, re.S):
        m = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not m:
            continue
        link = html_mod.unescape(m.group(1))
        title = strip_tags(m.group(2))
        snip_m = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        snippet = strip_tags(snip_m.group(1)) if snip_m else ""
        if not link.startswith("http"):
            continue
        out.append({"title": title, "url": link, "snippet": snippet[:300]})
        if len(out) >= count:
            break
    return out


def main() -> int:
    _fix_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    try:
        rows = search(args.query, args.count)
    except Exception as e:                                    # noqa: BLE001
        print(f"检索失败：{type(e).__name__}: {e}")
        return 1

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(f"=== {args.query} （{len(rows)} 条）===\n")
        for i, r in enumerate(rows, 1):
            print(f"{i}. {r['title']}")
            print(f"   {r['url']}")
            if r["snippet"]:
                print(f"   {r['snippet'][:200]}")
            print()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w\u4e00-\u9fff]+", "_", args.query)[:50]
    path = OUT_DIR / f"{datetime.now().strftime('%H%M%S')}_{safe}.json"
    path.write_text(json.dumps({"query": args.query, "at": datetime.now().isoformat(),
                                "results": rows}, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print(f"已存：{path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
