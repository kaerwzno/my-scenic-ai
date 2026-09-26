#!/usr/bin/env python3
"""探测这个 key 到底能调哪些数字人相关的模型。

为什么需要探测，而不是查文档
----------------------------
1. 兼容端点的 `/models` 只列 OpenAI 兼容面的模型（261 个），里面没有任何
   口型/视频类模型——但前面已经证明同一个 key 在原生端点能跑图像生成。
   所以那份清单**不是**能力清单。
2. 我们真正要回答的是"**这个账号**能用哪些"，不是"阿里云有什么"。

探测原理：拿错误码当信号
------------------------
故意发一个最小请求，从返回码分辨三种情况：

    404 / url error          → 这个服务或模型不存在        ❌ 不可用
    400 Model not exist      → 模型没开这个账号            ❌ 不可用
    400 InvalidParameter     → **路由和模型都在**，只是参数不对 → ✅ 可用
    200 / task_id            → 直接就成了                  ✅ 可用

之前探图像生成就是靠这个：拿到 400「input.prompt 不能为空」而不是 401/404，
才确定 key 有效、路由存在，然后按正确的请求体格式重发就成功了。

⚠️ 关于费用（第一版这里写错了，更正）
----------------------------------
我原本写的是"不产生费用"，**这是错的**。实测：

    · 被拒的请求（400/403/404）确实不计费；
    · 但视频生成 / 图像生成 / videoretalk 这类异步接口，**只要返回 200 就已经
      受理成任务了**，任务会真的跑、会真的计费。第一轮探测试出了
      wan2.2-i2v-flash / plus / wanx2.1-i2v-turbo / videoretalk 各一个任务，
      以及若干次 t2i，这些都要算钱（量很小，但确实发生了）。

要避免花钱，就把 `--safe` 打开：它只探"路由是否存在"以外的信息不发实体请求，
或者干脆只跑同步接口。默认不加 `--safe` 时请自己清楚这一点。

用法
----
    python probe_dashscope_capabilities.py
    python probe_dashscope_capabilities.py --json     # 输出机器可读结果
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FAY_CONF = ROOT.parent / "Fay-main" / "system.conf"
OUT = ROOT / "docs" / "dashscope-capabilities.json"

BASE = "https://dashscope.aliyuncs.com"

# 候选清单：(分类, 服务路径, 模型名, 说明, 是否异步)
# 路径来自 DashScope 原生 API 的分组惯例；模型名是我们关心的数字人相关能力。
#
# ★ 异步标志必须逐条写对：视频/图像生成是异步任务（要 X-DashScope-Async 头 +
#   轮询 task_id），TTS/ASR 是同步接口。**给同步接口加异步头会被拒**，
#   报「current user api does not support asynchronous calls」——
#   那是请求方式错了，不是没权限。第一版脚本就因为这个把 TTS/ASR 误判成待确认。
CANDIDATES = [
    # ── 视频生成（图生视频 / 首尾帧 / 参考图）──
    ("视频生成", "/api/v1/services/aigc/video-generation/video-synthesis",
     "wan2.2-i2v-flash", "图生视频：一张图 + 提示词 → 短视频", True),
    ("视频生成", "/api/v1/services/aigc/video-generation/video-synthesis",
     "wan2.2-i2v-plus", "图生视频（高质）", True),
    ("视频生成", "/api/v1/services/aigc/video-generation/video-synthesis",
     "wanx2.1-i2v-turbo", "图生视频（上一代）", True),
    # ── 数字人 / 对口型 ──
    ("数字人对口型", "/api/v1/services/aigc/image2video/video-synthesis",
     "videoretalk", "视频对口型：给一段视频重配口型", True),
    ("数字人对口型", "/api/v1/services/aigc/image2video/video-synthesis",
     "emo-v1", "音频驱动说话脸（EMO）", True),
    ("数字人对口型", "/api/v1/services/aigc/image2video/face-chain",
     "liveportrait", "人像动态化（LivePortrait）", True),
    ("数字人对口型", "/api/v1/services/aigc/image2video/video-synthesis",
     "liveportrait", "人像动态化（换路径再试一次）", True),
    # ── 语音：同步接口，不能带异步头 ──
    ("语音合成", "/api/v1/services/aigc/multimodal-generation/generation",
     "qwen3-tts-flash", "语音合成（同步）", False),
    ("语音识别", "/api/v1/services/aigc/multimodal-generation/generation",
     "qwen3-asr-flash", "语音识别（同步）", False),
    ("语音合成", "/api/v1/services/aigc/multimodal-generation/generation",
     "qwen3-tts-instruct-flash", "语音合成 · 可指定语气（同步）", False),
    # ── 语音 ──
    ("语音合成", "/api/v1/services/aigc/multimodal-generation/generation",
     "qwen3-tts-flash", "语音合成（重复项，保留作对照）", False),
    ("语音识别", "/api/v1/services/aigc/multimodal-generation/generation",
     "qwen3-asr-flash", "语音识别（重复项，保留作对照）", False),
    # ── 图像（已知可用，作为对照组）──
    ("图像生成", "/api/v1/services/aigc/text2image/image-synthesis",
     "wan2.2-t2i-flash", "对照组：这个已经验证可用", True),
]


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


def probe(key: str, path: str, model: str, is_async: bool = True) -> dict:
    """发一个最小请求，按返回码判断可用性。"""
    if is_async:
        payload = {"model": model, "input": {"prompt": "test"},
                   "parameters": {"size": "1024*1024", "n": 1}}
    else:
        # 同步接口（TTS/ASR）走 multimodal-generation，messages 结构
        payload = {"model": model,
                   "input": {"messages": [{"role": "user",
                                           "content": [{"text": "测试"}]}]}}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if is_async:
        headers["X-DashScope-Async"] = "enable"
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(), method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            task = (data.get("output") or {}).get("task_id")
            return {"verdict": "可用", "http": resp.status,
                    "detail": f"已受理 task={task}" if task else "同步调用成功"}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            j = json.loads(raw)
            code = j.get("code") or ""
            msg = j.get("message") or ""
        except json.JSONDecodeError:
            code, msg = "", raw[:160]
        low = f"{code} {msg}".lower()
        # 路由/模型不存在
        if e.code == 404 or "url error" in low or "model not exist" in low \
                or "not found" in low or "unsupported" in low:
            verdict = "不可用"
        # 参数问题 = 路由和模型都在
        elif "invalidparameter" in low.replace(" ", "") or e.code == 400:
            verdict = "可用"
        else:
            verdict = "待确认"
        return {"verdict": verdict, "http": e.code,
                "detail": f"{code} {msg}".strip()[:170]}
    except Exception as e:                                   # noqa: BLE001
        return {"verdict": "待确认", "http": 0, "detail": f"{type(e).__name__}: {e}"[:170]}


def main() -> int:
    _fix_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    key = read_key()
    results = []
    for cat, path, model, desc, is_async in CANDIDATES:
        r = probe(key, path, model, is_async)
        r.update({"category": cat, "model": model, "desc": desc, "path": path,
                  "async": is_async})
        results.append(r)
        mark = {"可用": "✅", "不可用": "❌", "待确认": "❓"}.get(r["verdict"], "?")
        print(f"{mark} [{cat}] {model:24s} HTTP {r['http']:<4} {r['detail'][:90]}")

    ok = [r for r in results if r["verdict"] == "可用"]
    print(f"\n可用 {len(ok)} / {len(results)}")
    if ok:
        print("可以集成的：")
        for r in ok:
            print(f"  · {r['category']} / {r['model']} —— {r['desc']}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(
        {"probed_at": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
         "base": BASE, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n明细已存：{OUT}")
    if args.json:
        print(json.dumps(results, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
