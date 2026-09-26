#!/usr/bin/env python3
"""订阅 Fay 的数字人 WebSocket，把口型和表情缓存下来给小程序取。

为什么必须订阅，不能只读消息表
------------------------------
Fay 有两条输出通道：

    通道 A  消息表（fay.db 的 T_Msg）      —— 只有文字，BFF 原来读的是这个
    通道 B  WebSocket 10002（HumanServer） —— 文字 + **口型(Lips)** + **表情(Action)**

口型和表情**只在通道 B 里**。所以要让小程序做出"嘴在动、表情在变"，
BFF 就必须连上 10002。

订阅要做四件事（对应下面四个方法）
---------------------------------
    1. 连      ws://127.0.0.1:10002
    2. 报身份  连上后发 {"Username": "<Fay用户名>"}
               服务端按这个字段过滤（见 wsa_server.py 的 __producer_handler：
               消息里的 Username 是谁，就只发给注册成这个名字的连接）。
               **不发就默认是 "User"。**
    3. 收      {"Topic":"human","Data":{...},"Username":"..."}
               Data 里有：Key(text/audio)、Text、Value(base64音频)、HttpValue(音频URL)、
               Lips(口型序列)、Action(表情语义)、Sentiment、IsFirst、IsEnd
    4. 缓存    按用户存最近几条，供小程序的轮询接口取走

★ 一条连接只能代表一个用户
--------------------------
所以这里按用户懒开连接：哪个用户开始轮询了，就给它开一条。
闲置久了自动断开，避免连接泄漏。

★ 音频只留 URL，不留 base64
---------------------------
Data 里有两个音频字段，处理方式完全不同：

    Value      base64 音频正文，一条能到几百 KB   -> 丢掉（内存会爆）
    HttpValue  音频的 URL，几十字节                -> **必须留**

为什么必须留 HttpValue：口型要跟**真实语音**同步，就得让客户端边放音频边按时间轴换嘴型。
没有音频的话，嘴巴只是在空动。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import deque
from typing import Any

import websockets

WS_URL = "ws://127.0.0.1:10002"
KEEP = 40                 # 每个用户最多缓存多少条
IDLE_CLOSE_SEC = 1800     # 闲置多久断开连接（30 分钟）


class FaySubscriber:
    """按用户订阅 Fay 的数字人消息，缓存在内存里。"""

    def __init__(self, ws_url: str = WS_URL) -> None:
        self.ws_url = ws_url
        self._buffers: dict[str, deque] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._stop: dict[str, bool] = {}
        self._last_use: dict[str, float] = {}
        self._status: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._counter = 0

    # ── 对外：确保某用户有连接 ──
    def ensure(self, user: str) -> None:
        with self._lock:
            self._last_use[user] = time.time()
            self._buffers.setdefault(user, deque(maxlen=KEEP))
            th = self._threads.get(user)
            if th and th.is_alive():
                return
            self._stop[user] = False
            th = threading.Thread(target=self._run, args=(user,), daemon=True,
                                  name=f"fay-ws-{user}")
            self._threads[user] = th
            th.start()

    def drain(self, user: str, since_ts: float = 0.0) -> list[dict]:
        """取走该用户的新消息（不删除，按时间戳过滤，可重复读）。"""
        with self._lock:
            buf = self._buffers.get(user) or deque()
            return [m for m in list(buf) if m["_at"] > since_ts]

    def latest(self, user: str, since_ts: float = 0.0) -> dict | None:
        """取最新一条（小程序要的就是"这句话对应的口型和表情"）。"""
        rows = self.drain(user, since_ts)
        return rows[-1] if rows else None

    def status(self) -> dict:
        with self._lock:
            return {
                "ws_url": self.ws_url,
                "users": {u: {"alive": (self._threads.get(u) or threading.Thread()).is_alive(),
                              "buffered": len(self._buffers.get(u) or []),
                              "state": self._status.get(u, {}).get("state", "?"),
                              "last_error": self._status.get(u, {}).get("error", ""),
                              "last_at": self._status.get(u, {}).get("last_at", 0)}
                          for u in self._threads},
            }

    def stop_all(self) -> None:
        with self._lock:
            for u in list(self._stop):
                self._stop[u] = True

    # ── 内部 ──
    def _set_status(self, user: str, **kw) -> None:
        with self._lock:
            st = self._status.setdefault(user, {})
            st.update(kw)

    def _run(self, user: str) -> None:
        """一条连接一个线程。断了就退避重连，直到被要求停止或闲置太久。"""
        backoff = 2.0
        while not self._stop.get(user):
            try:
                asyncio.run(self._loop(user))
                backoff = 2.0                     # 正常结束，重置退避
            except Exception as exc:              # noqa: BLE001
                self._set_status(user, state="error",
                                 error=f"{type(exc).__name__}: {exc}"[:160])
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
            if time.time() - self._last_use.get(user, 0) > IDLE_CLOSE_SEC:
                self._set_status(user, state="idle-closed")
                return

    async def _loop(self, user: str) -> None:
        async with websockets.connect(self.ws_url, max_size=None,
                                      ping_interval=20, ping_timeout=20) as ws:
            # ★ 第 2 步：报身份。服务端据此过滤消息。
            await ws.send(json.dumps({"Username": user}, ensure_ascii=False))
            self._set_status(user, state="connected", error="", last_at=time.time())
            async for raw in ws:
                if self._stop.get(user):
                    break
                self._handle(user, raw)

    def _handle(self, user: str, raw: Any) -> None:
        try:
            msg = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(msg, dict):
            return
        # 消息里带 Username 的只发给对应用户；不带的是群发（比如面板状态）
        owner = msg.get("Username")
        if owner is not None and owner != user:
            return
        data = msg.get("Data") or {}
        if not isinstance(data, dict):
            return
        # 只留我们要的字段：口型、表情、文字、分段标记。
        # **故意丢掉 Value/HttpValue** —— 那是 base64 音频，几百 KB 一条，
        # 存下来只会把内存吃光，而小程序要的是口型时间轴不是音频本身。
        slim = {
            "key": data.get("Key") or "",
            "text": data.get("Text") or "",
            # ★ 音频只留 URL。Value 是 base64 正文（几百 KB），丢掉。
            "audio_url": data.get("HttpValue") or "",
            "lips": data.get("Lips") or [],
            "action": data.get("Action") or None,
            "sentiment": data.get("Sentiment"),
            "is_first": data.get("IsFirst"),
            "is_end": data.get("IsEnd"),
            "images": data.get("Images") or [],
            "topic": msg.get("Topic") or "",
            "_at": time.time(),
        }
        with self._lock:
            self._counter += 1
            self._buffers.setdefault(user, deque(maxlen=KEEP)).append(slim)
            self._last_use[user] = time.time()
        self._set_status(user, state="receiving", last_at=time.time())


if __name__ == "__main__":
    import sys

    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass

    user = sys.argv[1] if len(sys.argv) > 1 else "User"
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
    sub = FaySubscriber()
    sub.ensure(user)
    print(f"订阅 {WS_URL}，身份 {user}，观察 {seconds:.0f} 秒…")
    seen: set[int] = set()
    t0 = time.time()
    try:
        while time.time() - t0 < seconds:
            time.sleep(1.0)
            for m in sub.drain(user):
                if id(m) in seen:
                    continue
                seen.add(id(m))
                lips = m.get("lips") or []
                act = m.get("action") or {}
                print(f"[{m['key']}] 文本={m['text'][:40]!r} "
                      f"口型={len(lips)}段 "
                      f"表情={(act.get('affect') if isinstance(act, dict) else '')!r} "
                      f"动作={(act.get('behavior') if isinstance(act, dict) else '')!r}")
    finally:
        print("\n状态：", json.dumps(sub.status(), ensure_ascii=False)[:400])
        sub.stop_all()
