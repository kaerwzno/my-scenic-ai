#!/usr/bin/env python3
"""小程序 BFF（Backend for Frontend）—— 小程序和 Fay 之间的那一层。

为什么不能让小程序直连 Fay
--------------------------
1. Fay 的 `/api/send` **没有鉴权**，暴露出去等于把大脑开放到公网；
2. Fay 的"用户"就是个明文字符串，没有账号体系，小程序必须有自己的用户概念；
3. ASR / 视觉 / TTS 都要用 API Key，而 **Key 绝不能进小程序包**（包可以被反编译）。

这一层负责：用户身份、形象档案、消息转发、回复轮询、素材下发。
ASR / 图片理解 / TTS 在后续阶段加进来，位置已经留好了。

关于"换形象不换记忆"（项目决策 D1）
-----------------------------------
Fay 的记忆按 username 存。所以这里**永远不动 username**，
换形象只改本地档案里的 avatar_id —— 记忆、偏好、行程全部自动保留。
这条规则后面所有代码都得守，改 username 等于清空用户记忆。

端口 5180（admin-api 是 5174，Fay 是 5000/5010）。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from ws_bridge import FaySubscriber


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
AVATARS_FILE = PROJECT / "avatars.json"
ASSET_DIR = PROJECT / "assets" / "avatars"
DATA_DIR = HERE / "data"
PROFILES_FILE = DATA_DIR / "profiles.json"
USERMAP_FILE = DATA_DIR / "user_map.json"

PORT = int(os.environ.get("FMP_PORT", "5180"))
FAY_BASE = os.environ.get("FAY_BASE", "http://127.0.0.1:5000")

# 换形象时要不要往 Fay 记忆里写一笔。
# 测试期间关掉（FMP_NOTIFY_FAY=0），否则会插进别的脚本正在跑的对话里。
NOTIFY_FAY = os.environ.get("FMP_NOTIFY_FAY", "1") == "1"

app = Flask(__name__)
CORS(app)

# 口型和表情只在 WebSocket 通道里（消息表没有），所以这里挂一个订阅器。
# 见 ws_bridge.py 顶部注释：消息表=只有文字，WS 10002=文字+口型+表情。
subscriber = FaySubscriber()


# ─────────────────────────────────────────────────────────────
# 读写小文件（都用临时文件替换，避免写一半崩了把配置写坏）
# ─────────────────────────────────────────────────────────────

def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def avatars_config() -> dict:
    return read_json(AVATARS_FILE, {"avatars": {}, "default_avatar": ""})


# ─────────────────────────────────────────────────────────────
# 和 Fay 说话
# ─────────────────────────────────────────────────────────────

def fay_post(path: str, form: dict, timeout: float = 20.0) -> str:
    """Fay 的 /api/send 和 /api/get-msg 都只认 form data，不是 JSON。"""
    body = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(f"{FAY_BASE}{path}", data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fay_username(user_id: str) -> str:
    """app 用户 → Fay 用户名的映射。

    ★ 这个映射一旦定了就不能改（改了等于清空该用户的记忆）。
      演示账号特意映射到 "User"，好继承网页端已经聊出来的记忆。
    """
    mapping = read_json(USERMAP_FILE, {})
    if user_id in mapping:
        return mapping[user_id]
    name = f"mp_{user_id}"
    mapping[user_id] = name
    write_json(USERMAP_FILE, mapping)
    return name


# ─────────────────────────────────────────────────────────────
# 形象档案
# ─────────────────────────────────────────────────────────────

def profile_of(user_id: str) -> dict:
    profiles = read_json(PROFILES_FILE, {})
    cfg = avatars_config()
    p = profiles.get(user_id)
    if not p:
        p = {"user_id": user_id,
             "avatar_id": cfg.get("default_avatar") or "",
             "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        profiles[user_id] = p
        write_json(PROFILES_FILE, profiles)
    return p


def save_profile(user_id: str, patch: dict) -> dict:
    profiles = read_json(PROFILES_FILE, {})
    p = profiles.get(user_id) or profile_of(user_id)
    p.update(patch)
    p["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    profiles[user_id] = p
    write_json(PROFILES_FILE, profiles)
    return p


def notify_fay_avatar_change(user_id: str, avatar_name: str) -> None:
    """把"用户换了形象"这件事写进记忆，让数字人自己知道。

    为什么值得写：这样它下次能自然提起（"你今天换小沙弥陪你啦"），
    管理端统计"哪个形象最受欢迎"时也有真实来源。
    发一条普通用户消息即可——不需要专门的接口。
    """
    if not NOTIFY_FAY:
        return

    def _worker() -> None:
        time.sleep(1.0)
        try:
            fay_post("/api/send", {"data": json.dumps({
                "username": fay_username(user_id),
                "msg": f"（我刚刚把数字人形象换成了{avatar_name}）",
            }, ensure_ascii=False)})
        except (urllib.error.URLError, OSError):
            pass          # 记不上不影响换形象本身

    threading.Thread(target=_worker, daemon=True).start()


# ─────────────────────────────────────────────────────────────
# 接口
# ─────────────────────────────────────────────────────────────

def proxy_audio_url(url: str) -> str:
    """把 Fay 的音频地址改写成 BFF 自己的地址。

    为什么必须改写：Fay 给的音频 URL 长这样——
        http://127.0.0.1:5000/audio/sample-1790345693016.wav
    小程序如果直连它，在**真机上 127.0.0.1 指向的是手机自己**，音频根本放不出来。
    而且让小程序绕过 BFF 直接访问 Fay，等于把没有鉴权的服务暴露出去。

    所以统一改成 /api/audio/<文件名>，由 BFF 去取，小程序只跟 BFF 说话。
    """
    if not url:
        return ""
    name = url.rstrip("/").rsplit("/", 1)[-1]
    return f"/api/audio/{name}" if name else ""


@app.get("/api/audio/<path:name>")
def audio_proxy(name: str):
    """把 Fay 生成的音频转给小程序。"""
    try:
        with urllib.request.urlopen(f"{FAY_BASE}/audio/{name}", timeout=30) as resp:
            data = resp.read()
            ctype = resp.headers.get("Content-Type") or "audio/wav"
    except (urllib.error.URLError, OSError) as exc:
        return jsonify({"ok": False, "message": f"取音频失败：{exc}"}), 502
    return app.response_class(data, mimetype=ctype)


@app.get("/api/health")
def health():
    cfg = avatars_config()
    fay_ok = False
    try:
        fay_post("/api/get-msg", {"data": json.dumps({"username": "User", "limit": 1})})
        fay_ok = True
    except Exception:
        pass
    return jsonify({
        "ok": True,
        "port": PORT,
        "fay_base": FAY_BASE,
        "fay_reachable": fay_ok,
        "avatar_count": len(cfg.get("avatars") or {}),
        "notify_fay": NOTIFY_FAY,
    })


@app.get("/api/avatars")
def list_avatars():
    """给小程序：可选的（已上架）形象列表，按 order 排序。"""
    cfg = avatars_config()
    rows = []
    for aid, a in (cfg.get("avatars") or {}).items():
        if not a.get("enabled", True):
            continue
        render = a.get("render") or {}
        # 表情差分：从 assets/avatars/<id>/expr/manifest.json 读。
        # 这些图由 scripts/gen_avatar_expressions.py 生成（图像编辑模型派生的），
        # key 跟 Fay 的 affect 取值对齐，客户端直接按 affect 查表。
        expr_urls = {}
        manifest = read_json(ASSET_DIR / aid / "expr" / "manifest.json", {}) or {}
        for e in (manifest.get("expressions") or []):
            if e.get("key") and e.get("file"):
                expr_urls[e["key"]] = f"/api/assets/avatars/{aid}/expr/{e['file']}"
        rows.append({
            "id": aid,
            "name": a.get("name") or aid,
            "tagline": a.get("tagline") or "",
            "scenic": a.get("scenic") or "",
            "placeholder": a.get("placeholder", True),
            "portrait_url": f"/api/assets/avatars/{render.get('base', '')}",
            "anchors": render.get("anchors") or {},
            "expressions": expr_urls,
            "voice_hint": (a.get("voice") or {}).get("hint") or "",
            "order": a.get("order", 99),
        })
    rows.sort(key=lambda r: r["order"])
    return jsonify({"avatars": rows,
                    "default_avatar": cfg.get("default_avatar") or "",
                    "count": len(rows)})


@app.get("/api/assets/avatars/<path:sub>")
def avatar_asset(sub: str):
    return send_from_directory(ASSET_DIR, sub)


@app.get("/api/user/<user_id>")
def get_user(user_id: str):
    p = profile_of(user_id)
    cfg = avatars_config()
    a = (cfg.get("avatars") or {}).get(p.get("avatar_id")) or {}
    return jsonify({
        "user_id": user_id,
        "avatar_id": p.get("avatar_id"),
        "avatar_name": a.get("name") or "",
        "fay_user": fay_username(user_id),
        "created_at": p.get("created_at"),
    })


@app.post("/api/user/<user_id>/avatar")
def set_user_avatar(user_id: str):
    """换形象。★ 不动 Fay 的 username，所以记忆一定保留。"""
    body = request.get_json(silent=True) or {}
    aid = str(body.get("avatar_id") or "").strip()
    cfg = avatars_config()
    a = (cfg.get("avatars") or {}).get(aid)
    if not a:
        return jsonify({"ok": False, "message": f"没有这个形象：{aid}"}), 404
    if not a.get("enabled", True):
        return jsonify({"ok": False, "message": f"这个形象已下架：{aid}"}), 409

    before = profile_of(user_id).get("avatar_id")
    p = save_profile(user_id, {"avatar_id": aid})
    if before != aid:
        notify_fay_avatar_change(user_id, a.get("name") or aid)
    return jsonify({
        "ok": True,
        "avatar_id": aid,
        "avatar_name": a.get("name") or aid,
        "changed": before != aid,
        "fay_user": fay_username(user_id),
        "message": f"已换成{a.get('name') or aid}，记忆和偏好都保留着",
    })


@app.post("/api/chat/send")
def chat_send():
    """把用户说的话转给 Fay。"""
    body = request.get_json(silent=True) or {}
    user_id = str(body.get("user_id") or "").strip()
    text = str(body.get("text") or "").strip()
    if not user_id or not text:
        return jsonify({"ok": False, "message": "user_id 和 text 都不能为空"}), 400
    try:
        fay_post("/api/send", {"data": json.dumps(
            {"username": fay_username(user_id), "msg": text}, ensure_ascii=False)})
    except (urllib.error.URLError, OSError) as exc:
        return jsonify({"ok": False, "message": f"连不上 Fay：{exc}"}), 503
    return jsonify({"ok": True, "sent_at": int(time.time() * 1000)})


@app.get("/api/chat/poll")
def chat_poll():
    """取回复。

    Fay 是推送式的（WebSocket 给网页端），小程序没有长连接，
    所以这里代它去读消息表——这也是为什么需要 BFF。

    ★ 这里还要顺手做一层清洗，理由是我们实测过 Fay 的落库方式：
    一条回复会落两到三行——
        1. "我来帮你查一下，稍等…"                      ← 占位
        2. "<think> 执行耗时… 命中 5 条… </think>"       ← 检索轨迹（结尾正好是 </think>）
        3. "<think> … </think> 灵山大照壁长 39.8 米…"    ← 正文在 </think> 之后
    这些都该在这一层处理掉，不该让客户端去猜。否则小程序会先把工具调用日志
    当成回答显示出来，界面上全是 {"query": ...} 这种东西。
    """
    user_id = str(request.args.get("user_id") or "").strip()
    since = int(request.args.get("since") or 0)
    if not user_id:
        return jsonify({"ok": False, "message": "缺 user_id"}), 400
    try:
        raw = fay_post("/api/get-msg", {"data": json.dumps(
            {"username": fay_username(user_id), "limit": 30, "offset": 0})})
        items = (json.loads(raw) or {}).get("list") or []
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        return jsonify({"ok": False, "message": f"读取失败：{exc}"}), 503

    pending = False
    answer = None
    for m in items:
        try:
            ts = int(float(m.get("createtime") or 0))
        except (TypeError, ValueError):
            ts = 0
        ts_ms = ts if ts > 10 ** 12 else ts * 1000
        if ts_ms <= since:
            continue
        mtype = str(m.get("type") or "")
        if mtype.lower() not in ("fay", "robot", "system"):
            continue                      # 只关心数字人说的话
        text = str(m.get("content") or "")
        if text.strip().endswith("</think>"):
            continue                      # 检索轨迹，丢掉
        said = clean_reply(text)
        if not said:
            pending = True                # 只有占位语，说明它还在想
            continue
        if answer is None or ts_ms > answer["ts"]:
            answer = {"ts": ts_ms, "content": said}

    # ★ 把口型和表情一起带上。
    #   它们不在消息表里，只能从 WS 订阅缓存里取。
    #   一条回复会被 Fay 切成多段推送（每段一个 audio 消息、各带自己的口型），
    #   所以这里返回的是一个**段落列表**，客户端按顺序播。
    subscriber.ensure(fay_username(user_id))
    since_wall = time.time() - 120      # 只取最近两分钟内的，避免拿到上一条回复的
    segs = [m for m in subscriber.drain(fay_username(user_id), since_wall)
            if m.get("key") == "audio" and (m.get("lips") or m.get("text"))]
    speech = [{"text": s.get("text") or "",
               # 改写成 BFF 自己的地址，见 proxy_audio_url 的说明
               "audio_url": proxy_audio_url(s.get("audio_url") or ""),
               "lips": s.get("lips") or [],
               "action": s.get("action"),
               "sentiment": s.get("sentiment"),
               "is_first": s.get("is_first"),
               "is_end": s.get("is_end")} for s in segs]
    total_lips = sum(len(s["lips"]) for s in speech)

    return jsonify({"ok": True,
                    "pending": pending and answer is None,
                    "answer": answer,
                    "speech": speech,
                    "speech_segments": len(speech),
                    "total_lips": total_lips})


@app.get("/api/ws/status")
def ws_status():
    """看一眼订阅器的状态——口型/表情拿不到时先查这里。"""
    return jsonify(subscriber.status())


# ─────────────────────────────────────────────────────────────
# 锚点标定页
# ─────────────────────────────────────────────────────────────
#
# 为什么要有这个页面
# ------------------
# 立绘是文本生成图像出的，嘴**已经烤进图片里**了，我们是在上面盖一个会动的嘴。
# 盖在哪、多大，就是锚点。这件事**必须看着图定**，写代码的人猜不出来——
# 我第一版给的 (0.50, 0.40) 就是盲猜的，结果画到了鼻子和下巴之间。
#
# 坐标口径：锚点是**相对图片自身**的比例（0~1），不是相对画布。
# 所以标定页直接把原图铺满一个方框，点哪儿就是哪儿的比例，
# 不需要复刻小程序那套 cover 缩放——这也是为什么它很好写。

CALIBRATE_HTML = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>数字人锚点标定</title>
<style>
 body{font-family:"Microsoft YaHei",sans-serif;background:#F6F2E9;margin:0;padding:24px;color:#2f2a24}
 h1{font-size:18px;margin:0 0 6px} .tip{color:#7a7368;font-size:13px;margin-bottom:18px;line-height:1.7}
 .row{display:flex;gap:24px;flex-wrap:wrap}
 .stage{position:relative;width:420px;height:420px;background:#fff;border-radius:10px;overflow:hidden;
        box-shadow:0 2px 10px rgba(0,0,0,.08);cursor:crosshair}
 .stage img{width:100%;height:100%;display:block;-webkit-user-drag:none}
 .mark{position:absolute;border:2px solid #C1604C;border-radius:50%;pointer-events:none;
       transform:translate(-50%,-50%);background:rgba(193,96,76,.22)}
 .panel{background:#fff;border-radius:10px;padding:16px 18px;min-width:280px;
        box-shadow:0 2px 10px rgba(0,0,0,.08)}
 .panel label{display:block;font-size:13px;color:#7a7368;margin:12px 0 6px}
 .panel output{font-family:Consolas,monospace;color:#4E327D}
 input[type=range]{width:100%}
 .tabs{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap}
 .tabs button{border:1px solid #cfc4e2;background:#fff;color:#4E327D;border-radius:999px;
              padding:7px 16px;cursor:pointer;font-size:13px}
 .tabs button.on{background:#4E327D;color:#fff}
 .save{margin-top:16px;background:#4E327D;color:#fff;border:0;border-radius:999px;
       padding:11px 0;width:100%;font-size:14px;cursor:pointer}
 .msg{margin-top:10px;font-size:13px;color:#3e8e5a;min-height:18px}
 code{background:#f1ece1;padding:1px 6px;border-radius:4px}
</style></head><body>
<h1>数字人锚点标定</h1>
<div class="tip">
  在图上<strong>点一下真实嘴巴的位置</strong>，红圈就是小程序会说嘴巴画到的地方。<br>
  用滑杆调嘴的大小，调到红圈和图上原本的嘴差不多大。满意了按「保存」。<br>
  保存后<strong>小程序刷新就生效</strong>，不用重启服务。
</div>
<div class="tabs" id="tabs"></div>
<div class="row">
  <div class="stage" id="stage"><img id="img"><div class="mark" id="mark"></div></div>
  <div class="panel">
    <div style="font-size:13px;color:#7a7368">当前形象：<b id="who">-</b></div>
    <label>嘴巴位置 x（左右）<output id="ox">0.50</output></label>
    <input type="range" id="sx" min="0" max="1" step="0.002" value="0.5">
    <label>嘴巴位置 y（上下）<output id="oy">0.40</output></label>
    <input type="range" id="sy" min="0" max="1" step="0.002" value="0.4">
    <label>脸高（决定嘴的大小）<output id="of">0.26</output></label>
    <input type="range" id="sf" min="0.08" max="0.6" step="0.005" value="0.26">
    <button class="save" id="save">保存</button>
    <div class="msg" id="msg"></div>
    <div class="tip" style="margin-top:14px">
      坐标口径：<code>x</code>/<code>y</code> 是相对<strong>图片自身</strong>的比例，
      跟小程序里的画布大小无关，所以手机和电脑上看到的位置一致。
    </div>
  </div>
</div>
<script>
let avatars = [], cur = null;
const $ = (id) => document.getElementById(id);

function draw() {
  const x = +$('sx').value, y = +$('sy').value, f = +$('sf').value;
  $('ox').textContent = x.toFixed(3);
  $('oy').textContent = y.toFixed(3);
  $('of').textContent = f.toFixed(3);
  const W = $('stage').clientWidth;
  const rw = f * W * 0.085;          // 和小程序 draw() 里同一套公式
  const rh = Math.max(rw * 0.35, rw * 0.18);
  const m = $('mark');
  m.style.left = (x * 100) + '%';
  m.style.top = (y * 100) + '%';
  m.style.width = (rw * 2) + 'px';
  m.style.height = (rh * 2) + 'px';
}

function pick(id) {
  cur = avatars.find(a => a.id === id);
  $('who').textContent = cur.name + '（' + id + '）';
  $('img').src = '/api/assets/avatars/' + id + '/base.png';
  const an = cur.anchors || {};
  $('sx').value = (an.mouth && an.mouth.x) ?? 0.5;
  $('sy').value = (an.mouth && an.mouth.y) ?? 0.4;
  $('sf').value = an.face_height ?? 0.26;
  document.querySelectorAll('.tabs button').forEach(b =>
    b.className = (b.dataset.id === id ? 'on' : ''));
  $('msg').textContent = '';
  draw();
}

$('stage').addEventListener('click', (e) => {
  const r = $('stage').getBoundingClientRect();
  $('sx').value = ((e.clientX - r.left) / r.width).toFixed(3);
  $('sy').value = ((e.clientY - r.top) / r.height).toFixed(3);
  draw();
});
['sx', 'sy', 'sf'].forEach(id => $(id).addEventListener('input', draw));

$('save').addEventListener('click', async () => {
  if (!cur) return;
  const body = {
    anchors: {
      face: { x: +$('sx').value, y: Math.max(0, +$('sy').value - 0.06) },
      mouth: { x: +$('sx').value, y: +$('sy').value },
      eyes: { x: +$('sx').value, y: Math.max(0, +$('sy').value - 0.09), gap: 0.10 },
      face_height: +$('sf').value
    }
  };
  const r = await fetch('/api/avatars/' + cur.id + '/anchors',
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const j = await r.json();
  $('msg').textContent = j.ok ? '✅ 已保存，小程序刷新即生效' : ('❌ ' + (j.message || '保存失败'));
});

fetch('/api/avatars').then(r => r.json()).then(j => {
  avatars = j.avatars || [];
  $('tabs').innerHTML = avatars.map(a =>
    '<button data-id="' + a.id + '">' + a.name + '</button>').join('');
  document.querySelectorAll('.tabs button').forEach(b =>
    b.addEventListener('click', () => pick(b.dataset.id)));
  if (avatars.length) pick(avatars[0].id);
});
</script></body></html>"""


@app.get("/calibrate")
def calibrate_page():
    return app.response_class(CALIBRATE_HTML, mimetype="text/html")


@app.post("/api/avatars/<avatar_id>/anchors")
def set_anchors(avatar_id: str):
    """保存锚点。同时写两处：avatars.json（BFF 实际读的）和 meta.json（留档）。"""
    body = request.get_json(silent=True) or {}
    anchors = body.get("anchors")
    if not isinstance(anchors, dict) or "mouth" not in anchors:
        return jsonify({"ok": False, "message": "anchors.mouth 必填"}), 400

    cfg = avatars_config()
    a = (cfg.get("avatars") or {}).get(avatar_id)
    if not a:
        return jsonify({"ok": False, "message": f"没有这个形象：{avatar_id}"}), 404
    a.setdefault("render", {})["anchors"] = anchors
    write_json(AVATARS_FILE, cfg)

    meta_path = ASSET_DIR / avatar_id / "meta.json"
    meta = read_json(meta_path, {}) or {}
    if meta:
        meta["anchors"] = anchors
        meta["anchors_calibrated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        write_json(meta_path, meta)
    return jsonify({"ok": True, "avatar_id": avatar_id, "anchors": anchors})


PLACEHOLDER = "我来帮你查一下，稍等…"


def clean_reply(text: str) -> str:
    """剥掉占位语、检索轨迹、预启动块，只留数字人真正说出口的话。

    ★ 关于 <prestart>：Fay 的「预启动」机制会把知识库检索结果**注入到回复流**里，
    格式是 <prestart keep="true">…</prestart>。如果不剥掉，小程序上会把
    一大段检索原文当成数字人的回答显示出来——实测踩过：
    用户看到的是"[1] 灵山胜境·祈福场所（相关度 0.75）…"这种检索结果，不是人话。
    """
    t = (text or "").strip()
    if PLACEHOLDER in t:
        t = t.split(PLACEHOLDER, 1)[1].strip()
    # 去掉所有 <prestart ...>…</prestart> 区块（可能有多个，一个个来）
    while "<prestart" in t:
        head, _, rest = t.partition("<prestart")
        _, _, tail = rest.partition("</prestart>")
        t = (head + " " + tail).strip()
    if "</think>" in t:
        t = t.rsplit("</think>", 1)[1].strip()
    return t


if __name__ == "__main__":
    cfg = avatars_config()
    print(f"小程序 BFF 启动： http://127.0.0.1:{PORT}")
    print(f"  Fay          : {FAY_BASE}")
    print(f"  形象池        : {len(cfg.get('avatars') or {})} 个"
          f"（默认 {cfg.get('default_avatar')}）")
    print(f"  换形象写记忆  : {'开' if NOTIFY_FAY else '关（测试模式）'}")
    app.run(host="127.0.0.1", port=PORT, debug=False)
