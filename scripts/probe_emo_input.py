#!/usr/bin/env python3
"""摸清 emo-v1 对输入图片的要求（尺寸 / 宽高比 / 取景）。

背景：调用 emo-v1 一直失败，一步步定位到：

    1. 直接用本地文件不行，要上传到临时存储拿 oss:// 路径   ✅ 已解决
    2. 不传人脸框会报 Invalid bbox input                    ← 需要先检测
    3. 检测接口是 face-detect + emo-detect-v1               ✅ 已确认
    4. 但检测又拒图片：
         oss:// 路径      -> DataInspection「media format is not supported」
         内联 base64      -> Ratio「request parameter is invalid」

第 4 条指向两个可能：宽高比不对、或者图片内容不符合它的检查。
这个脚本就是用来把这两个因素分开的。

试探的策略：把同一张图裁成几种宽高比（保持脸部在画面里），看哪种能通过。
**全部是校验型调用，不生成视频，不产生视频费用。**

用法
----
    python probe_emo_input.py                 # 用 lingling 试四种比例
    python probe_emo_input.py --avatar kaka   # 换一个形象试
    python probe_emo_input.py --urls-only     # 只测 URL 格式，不裁图
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import experiment_emo_avatar as E
from PIL import Image

DETECT_URL = E.BASE + "/api/v1/services/aigc/image2video/face-detect"


def _fix_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def detect(key: str, image_url: str) -> str:
    body = json.dumps({"model": "emo-detect-v1",
                       "input": {"image_url": image_url}}).encode()
    req = urllib.request.Request(
        DETECT_URL, data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            d = json.loads(resp.read().decode("utf-8", errors="replace"))
            out = d.get("output") or {}
            return "✅ " + json.dumps(out, ensure_ascii=False)[:220]
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            j = json.loads(raw)
            msg = f"{j.get('code') or ''} {j.get('message') or ''}".strip()
        except json.JSONDecodeError:
            msg = raw
        return f"HTTP {e.code} {msg[:130]}"
    except Exception as e:                                    # noqa: BLE001
        return f"{type(e).__name__}: {e}"[:130]


def main() -> int:
    _fix_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--avatar", default="lingling")
    ap.add_argument("--urls-only", action="store_true")
    ap.add_argument("--formats", action="store_true",
                    help="测不同图片格式/尺寸（PNG vs JPEG、原图 vs 缩小）")
    ap.add_argument("--file", help="直接测任意一张图（对照实验用）")
    args = ap.parse_args()

    key = E.read_key()
    src_path = Path(args.file) if args.file else (E.AVATAR_DIR / args.avatar / "base.png")
    if not src_path.is_file():
        print("找不到立绘：", src_path)
        return 1
    src = Image.open(src_path).convert("RGB")
    print(f"立绘：{src_path}  尺寸 {src.size[0]}x{src.size[1]}")

    tmp = E.OUT_DIR / "probe"
    tmp.mkdir(parents=True, exist_ok=True)

    if args.formats:
        print("\n== 测格式与尺寸 ==")
        cases = []
        for tag, size in (("原尺寸", None), ("缩到512", (512, 512))):
            im = src if size is None else src.resize(size, Image.LANCZOS)
            png = tmp / f"{args.avatar}_{tag}.png"
            jpg = tmp / f"{args.avatar}_{tag}.jpg"
            im.save(png)
            im.convert("RGB").save(jpg, quality=92)
            cases += [(f"{tag}-PNG", png), (f"{tag}-JPEG", jpg)]
        for name, path in cases:
            oss = E.upload_image(key, path)
            print(f"  {name:12s} {path.stat().st_size // 1024:>5}KB -> {detect(key, oss)}")
        return 0

    if args.urls_only:
        print("\n== 只测 URL 格式（原图）==")
        oss = E.upload_image(key, src_path)
        for name, ref in {"oss:// 前缀": oss, "裸 key": oss.replace("oss://", "")}.items():
            print(f"  {name:14s} -> {detect(key, ref)}")
        return 0

    # 裁成几种宽高比。取景偏上，因为生成时要求「面部位于画面中央偏上」。
    W, H = src.size
    cases = {
        "1_1_原样": (W, H),
        "3_4": (768, 1024),
        "9_16": (576, 1024),
        "2_3": (682, 1024),
    }
    print("\n== 按宽高比裁图后，逐个送检测 ==")
    for name, (w, h) in cases.items():
        w, h = min(w, W), min(h, H)
        left = (W - w) // 2
        top = max(0, (H - h) // 3)          # 偏上取景，尽量把脸框进来
        box = (left, top, left + w, top + h)
        crop = src.crop(box)
        path = tmp / f"{args.avatar}_{name}.png"
        crop.save(path)
        oss = E.upload_image(key, path)
        print(f"  {name:10s} {crop.size[0]}x{crop.size[1]:<6} -> {detect(key, oss)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
