#!/usr/bin/env python3
"""把各个形象的 meta.json 汇总成一份 avatars.json（形象池的唯一数据源）。

为什么要有这一步，而不是每个形象各管各的
----------------------------------------
形象池有好几个使用方：小程序（要知道有哪些可选）、管理端（要能上下架、排序）、
BFF（要按 id 取素材和音色）。如果三方各自去翻目录，迟早不一致。

为什么不能直接覆盖生成
----------------------
meta.json 是脚本生成的（提示词、尺寸、锚点），但 `voice` / `enabled` / `order`
是**人工定的**，生成脚本不知道。直接覆盖会把它们冲掉——
这和之前「重建索引冲掉上传内容」是同一类坑。
所以这里的规则是：**生成的字段刷新，人工的字段保留**。

用法
----
    python build_avatars_config.py            # 生成/刷新 avatars.json
    python build_avatars_config.py --check    # 只检查有没有形象漏了
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
AVATAR_DIR = ROOT / "assets" / "avatars"
OUT = ROOT / "avatars.json"

# 人工字段：生成时保留已有值，没有就给默认
DEFAULT_VOICE = {"model": "qwen3-tts-flash", "voice_id": "", "speed": 1.0}
ORDER = ["lingling", "xiaoshani", "zhiyuan", "lingxi", "kaka"]


def _fix_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def load_existing() -> dict:
    if not OUT.is_file():
        return {}
    try:
        return (json.loads(OUT.read_text(encoding="utf-8")).get("avatars") or {})
    except (OSError, json.JSONDecodeError):
        return {}


def build() -> dict:
    existing = load_existing()
    avatars: dict[str, dict] = {}

    metas = sorted(AVATAR_DIR.glob("*/meta.json"))
    if not metas:
        raise SystemExit(f"没有找到任何形象素材：{AVATAR_DIR}")

    for meta_path in metas:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        aid = meta.get("id") or meta_path.parent.name
        old = existing.get(aid) or {}

        avatars[aid] = {
            # ── 生成的字段：每次刷新 ──
            "name": meta.get("name") or aid,
            "tagline": meta.get("tagline") or "",
            "scenic": meta.get("scenic") or "",
            "placeholder": bool(meta.get("placeholder", True)),
            "render": {
                "type": "static_procedural_mouth",
                "base": f"{aid}/base.png",
                "anchors": meta.get("anchors") or {},
                "note": "占位阶段：立绘 + 程序化嘴型，不需要分层素材",
            },
            "style": meta.get("style") or "",
            "generated_at": meta.get("generated_at") or "",
            # ── 人工字段：保留已有值 ──
            "voice": old.get("voice") or dict(
                DEFAULT_VOICE, hint=meta.get("voice_hint") or ""),
            "enabled": old.get("enabled", True),
            "order": old.get("order", ORDER.index(aid) + 1 if aid in ORDER else 99),
        }

    return {
        "_说明": "数字人形象池。生成的字段由 scripts/build_avatars_config.py 刷新，"
                 "voice / enabled / order 是人工字段，重新生成时会保留。",
        "_生成时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "default_avatar": (json.loads(OUT.read_text(encoding="utf-8")).get("default_avatar")
                           if OUT.is_file() else None) or "lingling",
        "avatars": avatars,
    }


def main() -> int:
    _fix_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    data = build()
    names = {k: v["name"] for k, v in sorted(
        data["avatars"].items(), key=lambda kv: kv[1]["order"])}
    print(f"形象池共 {len(names)} 个：")
    for k, v in names.items():
        row = data["avatars"][k]
        print(f"  {k:11s} {v:6s} 上架={row['enabled']} 排序={row['order']} "
              f"音色={row['voice'].get('hint') or row['voice'].get('voice_id') or '（未配）'}")

    if args.check:
        return 0
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写出：{OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
