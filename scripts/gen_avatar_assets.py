#!/usr/bin/env python3
"""生成数字人形象的占位素材（古风半身立绘）。

为什么走百炼而不是内置绘图工具
------------------------------
本会话没有内置绘图工具。而且即使用得上，这个项目也该用百炼——
方案里本来就定的是 qwen-image / wan 系列，用户已有 key，风格也统一。
**注意：OpenAI 兼容端点（compatible-mode/v1）没有图像生成路由（实测 404），
必须走 DashScope 原生端点。**

关于"占位"两个字
----------------
这是占位素材，不是最终美术。目标是**把链路跑通**：选形象 → 显示 → 说话 → 嘴动。
所以刻意不去追求"生成对齐的分层素材"（身体/眼睛/嘴巴分开且像素对齐）——
那是文本生成图像做不到的，硬做只会浪费时间。

用一个统一构图规格换取可复用的锚点：
    正面、半身、面部居中偏上、纯色背景
这样小程序的 canvas 可以用同一套比例去定位嘴部和眼睛。
嘴型在占位阶段用**程序化绘制**（按包络画一个椭圆），不需要素材分层。

用法
----
    python gen_avatar_assets.py --list              # 看有哪些形象
    python gen_avatar_assets.py --only lingling     # 只生成一个
    python gen_avatar_assets.py --all               # 全部（默认）
    python gen_avatar_assets.py --dry-run           # 只打印提示词，不调用

产物
----
    assets/avatars/<id>/base.png     立绘
    assets/avatars/<id>/meta.json    锚点与提示词记录（便于复现）
"""

from __future__ import annotations

import argparse
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
OUT_DIR = ROOT / "assets" / "avatars"
FAY_CONF = ROOT.parent / "Fay-main" / "system.conf"

CREATE_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"
QUERY_URL = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
MODEL = "wan2.2-t2i-flash"          # 实测可用；wanx2.1-t2i-turbo 也可以

# 统一构图规格：所有形象的提示词都带这一段，保证锚点可复用
FRAMING = (
    "正面半身像，面朝镜头，面部位于画面中央偏上，肩膀入画，"
    "纯色渐变背景，柔和均匀的光线，画面干净，"
    "不要文字，不要水印，不要多人，不要侧脸，不要夸张表情"
)

# 嘴部与眼睛的锚点（相对图片宽高的比例），供小程序 canvas 使用。
# 因为构图规格统一，先给一组保守的默认值，接入时按实际微调即可。
# ★ 头身比不同的形象要单独给：Q 版是大头，脸占画面比例大得多，
#   沿用半身像的锚点会把嘴画到脖子上。
ANCHORS = {
    "face": {"x": 0.50, "y": 0.34},
    "mouth": {"x": 0.50, "y": 0.40},
    "eyes": {"x": 0.50, "y": 0.31, "gap": 0.10},
    "face_height": 0.26,
}

ANCHORS_Q = {
    "face": {"x": 0.50, "y": 0.40},
    "mouth": {"x": 0.50, "y": 0.47},
    "eyes": {"x": 0.50, "y": 0.36, "gap": 0.16},
    "face_height": 0.40,
}

AVATARS = {
    "lingling": {
        "name": "灵灵",
        "tagline": "温柔的景区小导游",
        "scenic": "lingshan",
        "voice_hint": "轻柔、语速偏慢的女声",
        "style": "中国古风动漫插画风格，线条清晰，色彩雅致",
        "prompt": (
            "一位二十出头的中国女性导游，神情温柔亲切，嘴角带浅浅微笑，"
            "乌黑长发挽成简洁发髻，插一支素雅木簪，"
            "穿淡青色交领襦裙，外披浅米色纱质大袖衫，领口简净无繁复刺绣，"
            "气质沉静温和，像一位耐心的陪伴者"
        ),
    },
    "xiaoshani": {
        "name": "小沙弥",
        "tagline": "活泼的小沙弥，适合带孩子一起",
        "scenic": "lingshan",
        "voice_hint": "清亮、略带童声的男声",
        "style": "中国古风动漫插画风格，线条清晰，色彩雅致",
        "prompt": (
            "一位十岁左右的圆脸小和尚，光头，神情好奇又开心，露齿笑，"
            "穿灰白色对襟僧衣，戴一串木质念珠，"
            "体态小巧，气质干净憨厚，像个爱问问题的小朋友"
        ),
    },
    "zhiyuan": {
        "name": "智远",
        "tagline": "儒雅的讲解员，讲得深、讲得慢",
        "scenic": "lingshan",
        "voice_hint": "沉稳的中年男声",
        "style": "中国古风动漫插画风格，线条清晰，色彩雅致",
        "prompt": (
            "一位四十岁上下的中国男性讲解员，神态温和从容，"
            "短发整齐，蓄短须，戴一副细框圆眼镜，"
            "穿深青色立领长衫，外罩灰蓝色棉麻外褂，"
            "气质儒雅，像一位学问扎实又耐心的老师"
        ),
    },
    "lingxi": {
        "name": "灵犀",
        "tagline": "元气满满的二次元少女，活泼爱聊",
        "scenic": "lingshan",
        "voice_hint": "明亮、语速偏快的少女音",
        "style": (
            "日系二次元动漫风格，赛璐璐平涂上色，"
            "大而明亮的眼睛，干净利落的高光线，色彩明快鲜亮"
        ),
        "prompt": (
            "一位十六七岁的二次元少女，元气活泼，眼睛又大又亮，笑得灿烂，"
            "浅栗色头发扎成双马尾，额前有碎刘海，戴一个小巧的莲花发饰，"
            "穿白底青绿滚边的改良导游制服，领口系一条短丝巾，"
            "整体明快清爽，像个爱说话的邻家女孩"
        ),
    },
    "kaka": {
        "name": "卡卡",
        "tagline": "Q 版卡通小伙伴，适合孩子和想安静的人",
        "scenic": "lingshan",
        "voice_hint": "软糯、语速慢的童声",
        "style": (
            "Q 版卡通风格，二头身大头娃娃比例，圆润的造型，"
            "简洁的五官，柔和的马卡龙配色，粗描边，扁平化上色"
        ),
        "anchors": "q",
        "prompt": (
            "一个圆脑袋的 Q 版卡通小和尚形象，二头身，"
            "圆圆的脸蛋，弯弯的眯眯眼，小小的嘴巴，脸颊有淡粉色腮红，"
            "光头，穿浅杏色小僧袍，脖子上一串木珠，"
            "圆手圆脚，姿态安静，憨厚可爱，表情温和不夸张"
        ),
    },
}


def _fix_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def read_key() -> str:
    """从 Fay 的配置里读 key——不把密钥抄进这个脚本。"""
    if not FAY_CONF.is_file():
        raise SystemExit(f"找不到 Fay 配置：{FAY_CONF}")
    text = FAY_CONF.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"gpt_api_key\s*=\s*(\S+)", text)
    if not m:
        raise SystemExit("配置里没有 gpt_api_key")
    return m.group(1)


def post_json(url: str, payload: dict, key: str, timeout: float = 60.0) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "X-DashScope-Async": "enable"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def get_json(url: str, key: str, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def download(url: str, dest: Path) -> int:
    """下载图片。带重试——任务成功后拉图超时过一次，白等一轮不值得。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    last: Exception | None = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(url, timeout=90) as resp:
                data = resp.read()
            if not data:
                raise RuntimeError("下载到空文件")
            dest.write_bytes(data)
            return len(data)
        except Exception as e:                       # noqa: BLE001
            last = e
            print(f"  下载失败（第 {attempt}/3 次）：{type(e).__name__}: {e}")
            time.sleep(3)
    raise RuntimeError(f"下载失败：{last}")


def generate_one(avatar_id: str, spec: dict, key: str, size: str,
                 poll_timeout: float = 240.0) -> dict:
    style = spec.get("style") or "中国古风动漫插画风格，线条清晰，色彩雅致"
    prompt = f"{spec['prompt']}，{style}，{FRAMING}"
    anchors = ANCHORS_Q if spec.get("anchors") == "q" else ANCHORS
    print(f"  提交任务…")
    resp = post_json(CREATE_URL, {
        "model": MODEL,
        "input": {"prompt": prompt},
        "parameters": {"size": size, "n": 1},
    }, key)
    task_id = (resp.get("output") or {}).get("task_id")
    if not task_id:
        raise RuntimeError(f"没拿到 task_id：{json.dumps(resp, ensure_ascii=False)[:300]}")
    print(f"  task_id = {task_id}")

    deadline = time.time() + poll_timeout
    result_url = ""
    while time.time() < deadline:
        time.sleep(5)
        st = get_json(QUERY_URL.format(task_id=task_id), key)
        out = st.get("output") or {}
        status = out.get("task_status")
        print(f"  状态：{status}")
        if status == "SUCCEEDED":
            results = out.get("results") or []
            if not results:
                raise RuntimeError("任务成功但没有图片")
            result_url = results[0].get("url") or ""
            break
        if status in ("FAILED", "CANCELED", "UNKNOWN"):
            raise RuntimeError(f"任务失败：{json.dumps(out, ensure_ascii=False)[:300]}")

    if not result_url:
        raise RuntimeError("轮询超时")

    dest = OUT_DIR / avatar_id / "base.png"
    size_bytes = download(result_url, dest)
    meta = {
        "id": avatar_id, "name": spec["name"], "tagline": spec["tagline"],
        "scenic": spec["scenic"], "voice_hint": spec["voice_hint"],
        "placeholder": True,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "model": MODEL, "size": size, "prompt": prompt,
        "source_url": result_url, "anchors": anchors,
        "style": style,
        "render_hint": "占位阶段：立绘 + 程序化嘴型（按包络画椭圆），不需要分层素材",
    }
    (OUT_DIR / avatar_id / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  已保存：{dest}（{size_bytes//1024} KB）")
    return meta


def main() -> int:
    _fix_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="生成全部（默认）")
    ap.add_argument("--only", help="只生成指定 id")
    ap.add_argument("--list", action="store_true", help="列出形象清单")
    ap.add_argument("--size", default="1024*1024", help="图片尺寸，如 1024*1024")
    ap.add_argument("--dry-run", action="store_true", help="只打印提示词，不调用")
    args = ap.parse_args()

    if args.list:
        for k, v in AVATARS.items():
            print(f"{k:12s} {v['name']:6s} {v['tagline']}")
        return 0

    picked = {args.only: AVATARS[args.only]} if args.only else AVATARS
    if args.only and args.only not in AVATARS:
        print(f"没有这个形象：{args.only}；可选：{', '.join(AVATARS)}")
        return 1

    if args.dry_run:
        for k, v in picked.items():
            print(f"=== {k} / {v['name']} ===")
            print(f"{v['prompt']}，{FRAMING}")
            print()
        return 0

    key = read_key()
    print(f"模型 {MODEL}，尺寸 {args.size}，共 {len(picked)} 个形象")
    ok = 0
    for k, v in picked.items():
        print(f"[{k}] {v['name']}")
        try:
            generate_one(k, v, key, args.size)
            ok += 1
        except urllib.error.HTTPError as e:
            print(f"  HTTP {e.code}：{e.read().decode('utf-8', 'replace')[:200]}")
        except Exception as e:                       # noqa: BLE001
            print(f"  失败：{type(e).__name__}: {e}")

    print(f"\n完成 {ok}/{len(picked)}，输出目录：{OUT_DIR}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
