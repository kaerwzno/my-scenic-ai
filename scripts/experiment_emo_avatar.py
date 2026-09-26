#!/usr/bin/env python3
"""决定性实验：拿我们的立绘去试「音频驱动说话脸」，看哪些风格能驱动。

要回答的问题
------------
云端的音频驱动模型（emo-v1 / liveportrait / videoretalk）都是在真人脸上训练的。
我们的形象池里有古风插画、日系二次元、Q 版二头身。

    能驱动插画   -> 一条云 API 路线覆盖全部形象
    只驱动写实向 -> 分两条驱动，各管各的风格
    都不行       -> 回到 canvas 分层，云 API 只给管理端演示

这个实验一次就能定掉整个技术路线。

注意：会真的花钱
----------------
每次调用都会真的生成视频、真的计费。所以默认只跑一个形象先验格式，
确认无误再 --all。

流程
----
    1. qwen3-tts-flash  合成一句话（得到音频 URL）
    2. 上传立绘到 DashScope 临时存储（本地文件不能直接当 URL 用）
    3. emo-v1           立绘 + 音频 -> 说话视频
    4. 轮询任务 -> 下载视频

用法
----
    python experiment_emo_avatar.py --only lingling
    python experiment_emo_avatar.py --all
    python experiment_emo_avatar.py --tts-only
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
import uuid
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FAY_CONF = ROOT.parent / "Fay-main" / "system.conf"
AVATAR_DIR = ROOT / "assets" / "avatars"
OUT_DIR = ROOT / "docs" / "research" / "emo-experiment"

BASE = "https://dashscope.aliyuncs.com"
TTS_MODEL = "qwen3-tts-flash"
EMO_MODEL = "emo-v1"
TEST_LINE = "你好呀，我是灵灵，欢迎来到灵山胜境。这边请跟我走。"

GEN_URL = "/api/v1/services/aigc/multimodal-generation/generation"
VIDEO_SUBMIT = "/api/v1/services/aigc/image2video/video-synthesis"
DETECT_PATHS = [
    "/api/v1/services/aigc/image2video/face-detect",
    "/api/v1/services/aigc/image2video/video-synthesis",   # 兜底：有的版本合成检测同路径
]
DETECT_MODEL = "emo-detect-v1"
B64_LIMIT = 8 * 1024 * 1024


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


def call(url: str, payload, key: str, method: str = "POST",
         async_header: bool = False, timeout: float = 60.0):
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if async_header:
        headers["X-DashScope-Async"] = "enable"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            j = json.loads(raw)
            return {"_error": True, "http": e.code,
                    "code": j.get("code"), "message": j.get("message") or raw[:300]}
        except json.JSONDecodeError:
            return {"_error": True, "http": e.code, "message": raw[:300]}


def make_audio(key: str, text: str, voice: str = "Cherry") -> str:
    """合成一句话，返回音频 URL。TTS 是同步接口，不能带异步头。"""
    r = call(f"{BASE}{GEN_URL}",
             {"model": TTS_MODEL,
              "input": {"text": text, "voice": voice},
              "parameters": {"language_type": "Chinese"}},
             key)
    if r.get("_error"):
        raise RuntimeError(f"TTS 失败：HTTP {r.get('http')} {r.get('code')} {r.get('message')}")
    link = ((r.get("output") or {}).get("audio") or {}).get("url") or ""
    if not link:
        raise RuntimeError(f"TTS 没返回音频 URL：{json.dumps(r, ensure_ascii=False)[:400]}")
    return link


def upload_image(key: str, path: Path) -> str:
    """上传图片到 DashScope 临时存储，返回 oss:// 路径。

    为什么要这一步：emo-v1 要的是能公开访问的 URL，本地文件不行；
    而 base64 塞进 JSON 又大又容易超限。
    """
    policy = call(f"{BASE}/api/v1/uploads?action=getPolicy&model={EMO_MODEL}",
                  None, key, method="GET")
    if policy.get("_error"):
        raise RuntimeError(f"取上传凭证失败：{policy.get('message')}")
    d = policy.get("data") or {}
    host, key_dir = d.get("upload_host"), d.get("upload_dir")
    if not host or not key_dir:
        raise RuntimeError(f"凭证缺字段：{json.dumps(policy, ensure_ascii=False)[:300]}")

    object_key = f"{key_dir}/{path.name}"
    boundary = "----fmp" + uuid.uuid4().hex
    # 字段名要照着凭证来：是 oss_access_key_id，不是 accessid。
    # 第一次写错成 accessid，OSSAccessKeyId 传了空值，OSS 直接 InvalidArgument。
    fields = {
        "OSSAccessKeyId": d.get("oss_access_key_id", ""),
        "policy": d.get("policy", ""),
        "signature": d.get("signature", ""),
        "key": object_key,
        "success_action_status": "200",
    }
    if d.get("x_oss_object_acl"):
        fields["x-oss-object-acl"] = d["x_oss_object_acl"]
    if d.get("x_oss_forbid_overwrite"):
        fields["x-oss-forbid-overwrite"] = d["x_oss_forbid_overwrite"]
    body = bytearray()
    for k, v in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n"
                 f"{v}\r\n").encode("utf-8")
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
             f"filename=\"{path.name}\"\r\nContent-Type: image/png\r\n\r\n").encode("utf-8")
    body += path.read_bytes()
    body += f"\r\n--{boundary}--\r\n".encode("utf-8")

    req = urllib.request.Request(host, data=bytes(body), method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"上传失败：HTTP {e.code} {e.read().decode('utf-8', 'replace')[:200]}")
    return f"oss://{object_key}"


def image_ref(key: str, path: Path) -> str:
    try:
        ref = upload_image(key, path)
        print(f"  上传成功：{ref}")
        return ref
    except Exception as e:                                    # noqa: BLE001
        size = path.stat().st_size
        print(f"  上传失败（{type(e).__name__}: {str(e)[:90]}），改用 base64")
        if size > B64_LIMIT:
            raise RuntimeError(f"图片太大（{size // 1024}KB），base64 兜底不适用")
        return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def detect_face(key: str, img_ref: str) -> tuple[list, str]:
    """先检测人脸框——emo-v1 是拿这个框去驱动的，不给就报 Invalid bbox input。

    这一步也顺便回答了实验的主要问题：**检测不到人脸的形象，就是这条路线不支持的**。
    而且检测比生成视频便宜得多，所以先过一遍检测再决定要不要花钱生成。

    返回 (bbox, 用的路径)。bbox 为空表示检测不到。
    """
    last_err = ""
    for path in DETECT_PATHS:
        r = call(BASE + path,
                 {"model": DETECT_MODEL, "input": {"image_url": img_ref}},
                 key)
        if r.get("_error"):
            last_err = f"HTTP {r.get('http')} {r.get('code')} {r.get('message')}"
            if r.get("http") == 404 or "url error" in str(r.get("message", "")).lower():
                continue                       # 路径不对，试下一个
            continue
        out = r.get("output") or {}
        for field in ("face_bbox", "bbox", "detected_faces"):
            v = out.get(field)
            if isinstance(v, list) and v and isinstance(v[0], (int, float)):
                return [float(x) for x in v[:4]], path
            if isinstance(v, list) and v and isinstance(v[0], list):
                return [float(x) for x in v[0][:4]], path
        if out:
            return [], f"{path}（返回里没有 bbox 字段：{list(out.keys())}）"
    return [], last_err or "所有候选路径都失败"


def submit_emo(key: str, img_ref: str, audio_url: str, bbox: list) -> str:
    r = call(BASE + VIDEO_SUBMIT,
             {"model": EMO_MODEL,
              "input": {"image_url": img_ref, "audio_url": audio_url,
                        "face_bbox": bbox},
              "parameters": {}},
             key, async_header=True)
    if r.get("_error"):
        raise RuntimeError(f"提交失败：HTTP {r.get('http')} {r.get('code')} {r.get('message')}")
    task = ((r.get("output") or {}).get("task_id")) or ""
    if not task:
        raise RuntimeError(f"没拿到 task_id：{json.dumps(r, ensure_ascii=False)[:300]}")
    return task


def wait_task(key: str, task_id: str, timeout: float = 600.0) -> dict:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        time.sleep(8)
        r = call(f"{BASE}/api/v1/tasks/{task_id}", None, key, method="GET")
        out = r.get("output") or {}
        status = out.get("task_status") or r.get("message") or "?"
        if status != last:
            print(f"    状态：{status}")
            last = status
        if status == "SUCCEEDED":
            return out
        if status in ("FAILED", "CANCELED", "UNKNOWN"):
            raise RuntimeError(f"任务失败：{json.dumps(out, ensure_ascii=False)[:400]}")
    raise RuntimeError("轮询超时")


def download(url: str, dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    last = None
    for _ in range(3):
        try:
            with urllib.request.urlopen(url, timeout=90) as resp:
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
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--only")
    ap.add_argument("--tts-only", action="store_true")
    ap.add_argument("--line", default=TEST_LINE)
    args = ap.parse_args()

    key = read_key()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("第一步：合成实验音频")
    audio_url = make_audio(key, args.line)
    print(f"  音频：{audio_url[:90]}…")

    if args.tts_only:
        print("\n只测 TTS，结束。")
        return 0

    picked = []
    for m in sorted(AVATAR_DIR.glob("*/meta.json")):
        meta = json.loads(m.read_text(encoding="utf-8"))
        aid = meta.get("id") or m.parent.name
        if args.only and aid != args.only:
            continue
        picked.append((aid, meta.get("name") or aid, m.parent / "base.png"))
    if not picked:
        print("没有匹配到形象")
        return 1

    print(f"\n第二步：为 {len(picked)} 个形象生成说话视频（会真的计费）")
    results = []
    for aid, name, img in picked:
        print(f"\n[{aid}] {name}")
        rec = {"id": aid, "name": name,
               "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        try:
            ref = image_ref(key, img)
            rec["image_ref_kind"] = "oss" if ref.startswith("oss://") else "base64"

            print("  检测人脸…")
            bbox, used = detect_face(key, ref)
            rec["bbox"] = bbox
            rec["detect_path"] = used
            if not bbox:
                rec.update({"verdict": "检测不到人脸",
                            "error": f"该风格不被支持：{used}"[:200]})
                print(f"  检测不到人脸 -> 这个风格这条路线走不通（{used[:80]}）")
                results.append(rec)
                continue
            print(f"  人脸框：{bbox}")

            task = submit_emo(key, ref, audio_url, bbox)
            rec["task_id"] = task
            print(f"  task={task}")
            out = wait_task(key, task)
            vurl = ((out.get("results") or {}).get("video_url")
                    or out.get("video_url") or "")
            if not vurl:
                raise RuntimeError(f"成功但没视频地址：{json.dumps(out, ensure_ascii=False)[:300]}")
            dest = OUT_DIR / f"{aid}.mp4"
            size = download(vurl, dest)
            rec.update({"verdict": "生成成功", "video": str(dest), "size_kb": size // 1024})
            print(f"  已保存：{dest}（{size // 1024} KB）")
        except Exception as e:                                # noqa: BLE001
            rec.update({"verdict": "失败", "error": f"{type(e).__name__}: {e}"[:300]})
            print(f"  失败：{rec['error']}")
        results.append(rec)

    summary = OUT_DIR / "summary.json"
    summary.write_text(json.dumps({"line": args.line, "audio_url": audio_url,
                                   "results": results}, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    print("\n" + "=" * 60)
    for r in results:
        print(f"{r['verdict']:6s} {r['id']:11s} {r['name']:6s} "
              f"{r.get('size_kb', '')} {r.get('error', '')[:90]}")
    print(f"\n汇总：{summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
