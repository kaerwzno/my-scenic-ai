#!/usr/bin/env python3
"""从基础立绘生成表情差分（同一人物、不同表情）。

为什么需要这个
--------------
路线 B（小程序 canvas 实时渲染）要满足「口型 + 表情都同步」，而表情的实现方式是
**按 Fay 给的 affect 语义切换表情图**。Fay 的 config/action_rules.csv 里每条回复
都带一个 Action，affect 就是表情：

    greeting.hello  -> affect=smile      欢迎/打招呼
    guidance.invite -> affect=warm       请进、跟我来
    dialogue.think  -> affect=neutral    让我想想
    dialogue.reject -> affect=serious    拒绝、严肃提醒
    dialogue.question -> affect=curious  提问

所以每个形象需要 6-8 张表情图。**不需要美术外包**——用图像编辑模型从基础立绘派生，
关键是提示词要强调「保持人物完全不变，只改表情」，否则会变成另一个人。

实测要点
--------
qwen-image-edit 系列是**同步**接口（不是异步任务）：
    调异步方式会报 403「current user api does not support asynchronous calls」
    这个 403 反而证明模型存在可用——排查时拿它当"存在性证据"很好使。

用法
----
    python gen_avatar_expressions.py --avatar lingling --only smile   # 先试一张
    python gen_avatar_expressions.py --avatar lingling                # 全套
    python gen_avatar_expressions.py --list
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FAY_CONF = ROOT.parent / "Fay-main" / "system.conf"
AVATAR_DIR = ROOT / "assets" / "avatars"

BASE = "https://dashscope.aliyuncs.com"
EDIT_URL = "/api/v1/services/aigc/multimodal-generation/generation"
EDIT_MODEL = "qwen-image-edit-plus"

# 表情清单。key 要跟 Fay 的 affect 取值对齐，这样运行期直接按 affect 查表。
EXPRESSIONS = {
    "neutral": ("平静", "保持自然平静的表情，嘴角平缓，眼神温和"),
    "smile": ("微笑", "露出温和友好的微笑，嘴角上扬，眼睛带着笑意"),
    "warm": ("亲切", "表情温暖亲切，像在欢迎客人，眉眼舒展"),
    "serious": ("认真", "表情认真专注，收敛笑意，目光沉稳"),
    "curious": ("好奇", "表情带一点好奇和疑问，眉毛微微上扬，眼睛睁大一些"),
    "think": ("思考", "若有所思的表情，视线略微上移，嘴微抿"),
    "concern": ("关切", "表情关切体贴，眉头微蹙，眼神柔和专注"),
}

GUARD = ("保持画面中人物的身份、脸型、发型、五官、服装、配色、构图和背景完全不变，"
         "只改变面部表情。不要改变画风，不要添加任何文字或水印。")


def _fix_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def read_key() -> str:
    text = FAY_CONF.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"gpt_api_key\s*=\s*(\S+)", text)
    if not m:
        raise SystemExit("system.conf 里没有 gpt_api_key")
    return m.group(1)


def call_edit(key: str, image_b64: str, prompt: str, timeout: float = 300.0) -> dict:
    """同步调用图像编辑。同步接口**不能带** X-DashScope-Async 头。"""
    payload = {
        "model": EDIT_MODEL,
        "input": {"messages": [{"role": "user", "content": [
            {"image": image_b64},
            {"text": prompt},
        ]}]},
        "parameters": {"watermark": False, "negative_prompt": "文字, 水印, 多人"},
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BASE + EDIT_URL, data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def extract_image_url(resp: dict) -> str:
    """把返回体里能当图片用的东西抠出来（不同版本字段位置不一样）。"""
    out = resp.get("output") or {}
    for path in (("choices", 0, "message", "content"),):
        cur = out
        for step in path:
            cur = (cur or {})[step] if isinstance(cur, dict) else (
                cur[step] if isinstance(cur, list) and isinstance(step, int) else {})
        if isinstance(cur, list):
            for item in cur:
                if isinstance(item, dict) and item.get("image"):
                    return item["image"]
    for k in ("image_url", "image", "url"):
        if isinstance(out.get(k), str):
            return out[k]
    for ch in (out.get("choices") or []):
        msg = ch.get("message") or {}
        if isinstance(msg.get("content"), str) and msg["content"].startswith("http"):
            return msg["content"]
        for item in (msg.get("content") or []):
            if isinstance(item, dict) and item.get("image"):
                return item["image"]
    return ""


def download(url: str, dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    last = None
    for _ in range(3):
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                data = resp.read()
            dest.write_bytes(data)
            return len(data)
        except Exception as e:                                # noqa: BLE001
            last = e
            time.sleep(3)
    raise RuntimeError(f"下载失败：{last}")


def main() -> int:
    _fix_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--avatar", default="lingling")
    ap.add_argument("--only", help="只生成某一个表情")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        for k, (name, _) in EXPRESSIONS.items():
            print(f"{k:10s} {name}")
        return 0

    key = read_key()
    base_path = AVATAR_DIR / args.avatar / "base.png"
    if not base_path.is_file():
        print("找不到基础立绘：", base_path)
        return 1
    img_b64 = "data:image/png;base64," + base64.b64encode(base_path.read_bytes()).decode()
    out_dir = AVATAR_DIR / args.avatar / "expr"
    print(f"形象：{args.avatar}    基础立绘：{base_path.stat().st_size // 1024} KB")
    print(f"输出目录：{out_dir}\n")

    picked = {args.only: EXPRESSIONS[args.only]} if args.only else EXPRESSIONS
    if args.only and args.only not in EXPRESSIONS:
        print(f"没有这个表情：{args.only}；可选：{', '.join(EXPRESSIONS)}")
        return 1

    done = []
    for i, (name_key, (cn, desc)) in enumerate(picked.items(), 1):
        prompt = f"把这张图里人物的表情改成：{desc}。{GUARD}"
        print(f"[{i}/{len(picked)}] {name_key}（{cn}）")
        try:
            resp = call_edit(key, img_b64, prompt)
            url = extract_image_url(resp)
            if not url:
                print("  返回里没找到图片，原始响应：")
                print("  " + json.dumps(resp, ensure_ascii=False)[:500])
                continue
            dest = out_dir / f"{name_key}.png"
            size = download(url, dest)
            print(f"  已保存：{dest.name}（{size // 1024} KB）")
            done.append({"key": name_key, "cn": cn, "file": dest.name,
                         "size_kb": size // 1024})
        except urllib.error.HTTPError as e:
            print(f"  HTTP {e.code}：{e.read().decode('utf-8', 'replace')[:250]}")
        except Exception as e:                                # noqa: BLE001
            print(f"  失败：{type(e).__name__}: {e}")

    if done:
        manifest = out_dir / "manifest.json"
        manifest.write_text(json.dumps({
            "avatar": args.avatar, "model": EDIT_MODEL,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "expressions": done,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n完成 {len(done)}/{len(picked)}，清单：{manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
