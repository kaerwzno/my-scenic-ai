#!/usr/bin/env python3
"""
时间看门狗 —— 真实的"时间驱动"主动事件源

它做什么
--------
持续观察 Fay 里**真实的**对话数据（消息时间戳、消息长度），
当配置的条件满足时，向 Fay 推一个 observation，让它主动开口。

为什么这不算"预制"
------------------
事件是根据**真实观察到的状态**产生的，不是念稿子：
  · 用户真的多久没说话了（读的是真实时间戳）
  · 用户最近两次回复真的有多短（读的是真实消息内容）
唯一"人工设定"的是阈值（多少分钟算久）——那是产品参数，不是内容。

依赖
----
只用 Python 标准库，任何 Python 3.8+ 都能跑。

用法
----
    python proactive_watchdog.py                    # 正常运行
    python proactive_watchdog.py --once             # 只跑一轮，便于调试
    python proactive_watchdog.py --dry-run          # 只打印不发送（安全测试）
    python proactive_watchdog.py --dump             # 打印它观察到的原始数据
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib import error, request


def _setup_console_encoding() -> None:
    """Windows 控制台默认是 GBK，中文会乱码。这里强制切到 UTF-8。"""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass


_setup_console_encoding()


DEFAULT_CONFIG = {
    "fay_base": "http://127.0.0.1:5000",
    "username": "User",
    "poll_interval_sec": 30,
    # 主动说完一句后，多久内不再主动（防打扰）
    "global_cooldown_sec": 300,
    # 用户刚说过话时，B/C 类规则先不打扰（D 类不受此限）
    "quiet_after_user_sec": 60,
    # 判断"本次游览开始"的静默间隔：超过这么久没说话，就认为是新的一次
    "session_gap_hours": 2,
    "park_close_time": "17:30",
    "rules": {
        "B_long_stay": {
            "enabled": True,
            "after_minutes": 30,
            "cooldown_sec": 1800,
            "priority": 20,
        },
        "C_closing_soon": {
            "enabled": True,
            "before_minutes": 30,
            "cooldown_sec": 3600,
            "priority": 100,
        },
        "D_short_replies": {
            "enabled": True,
            "max_chars": 4,
            "count": 2,
            "cooldown_sec": 900,
            "priority": 60,
        },
    },
}


# ─────────────────────────────────────────────────────────────
# HTTP
# ─────────────────────────────────────────────────────────────

def _post_json(base: str, path: str, payload: dict, timeout: float = 10.0) -> dict:
    """发送 JSON 请求。中文用 UTF-8 字节，避免编码问题。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        f"{base.rstrip('/')}{path}",
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw}


def fetch_messages(base: str, username: str, limit: int = 40) -> list[dict]:
    """读该用户最近的消息记录（升序返回）。"""
    data = _post_json(base, "/api/get-msg", {"username": username, "limit": limit, "offset": 0})
    items = data.get("list") or []
    # 有的版本返回降序，这里统一成升序
    items = [
        {
            "type": str(it.get("type") or ""),
            "content": str(it.get("content") or ""),
            "ts": _to_epoch_ms(it.get("createtime")),
        }
        for it in items
    ]
    items = [it for it in items if it["ts"] > 0]
    items.sort(key=lambda it: it["ts"])
    return items


def _to_epoch_ms(value) -> int:
    """兼容秒级/毫秒级时间戳。"""
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return 0
    if ts <= 0:
        return 0
    # 10^12 以上按毫秒，以下按秒
    return ts if ts > 10**12 else ts * 1000


def push_observation(base: str, username: str, observation: str) -> dict:
    """把观测推给 Fay，让它主动开口。"""
    return _post_json(base, "/to-greet", {"username": username, "observation": observation})


def wait_for_reply(base: str, username: str, since_ms: int, timeout: float = 20.0) -> str:
    """等 Fay 把话说完，从消息库里读回它真正说了什么。

    为什么不用 /to-greet 的返回值：那条路径下生成的文本走 WebSocket 推给面板，
    HTTP 返回体里的 data 常常是空的。消息本身会落库，所以从库里读才可靠。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1.5)
        try:
            msgs = fetch_messages(base, username, limit=10)
        except (error.URLError, OSError, TimeoutError):
            continue
        fresh = [
            m for m in msgs
            if m["ts"] >= since_ms and not _is_user(m["type"]) and m["content"].strip()
        ]
        if fresh:
            return fresh[-1]["content"]
    return ""


# ─────────────────────────────────────────────────────────────
# 观察：从真实数据里算出状态
# ─────────────────────────────────────────────────────────────

class Observation:
    """一次观察算出来的事实（不是判断）。"""

    def __init__(self, messages: list[dict], now_ms: int, cfg: dict):
        self.messages = messages
        self.now_ms = now_ms
        self.cfg = cfg

        self.user_msgs = [m for m in messages if _is_user(m["type"])]
        self.has_any = bool(messages)
        self.last_msg_ts = messages[-1]["ts"] if messages else 0
        self.last_msg_is_user = bool(messages) and _is_user(messages[-1]["type"])

        self.session_start_ms = self._find_session_start_ms()

    # ---- 本次"游览"从什么时候开始 ----
    def _find_session_start_ms(self) -> int:
        """以"最后一次长静默之后的第一条消息"作为本次游览起点。

        这是一个启发式：我们没有真实的"入园时间"，只能用对话活动来近似。
        等小程序接入后，应该换成真实的事件（用户点"开始导览"）。
        """
        if not self.messages:
            return 0
        gap_ms = int(float(self.cfg.get("session_gap_hours", 2)) * 3600 * 1000)
        start = self.messages[0]["ts"]
        for prev, cur in zip(self.messages, self.messages[1:]):
            if cur["ts"] - prev["ts"] > gap_ms:
                start = cur["ts"]
        return start

    # ---- B：已游览多久 ----
    @property
    def elapsed_minutes(self) -> float:
        if not self.session_start_ms:
            return 0.0
        return (self.now_ms - self.session_start_ms) / 60000.0

    # ---- C：距闭园还有多久 ----
    def minutes_to_close(self) -> float | None:
        raw = str(self.cfg.get("park_close_time") or "").strip()
        if not raw:
            return None
        try:
            hh, mm = (int(x) for x in raw.split(":", 1))
            now = datetime.fromtimestamp(self.now_ms / 1000)
            close = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        except (ValueError, TypeError):
            return None
        return (close - now).total_seconds() / 60.0

    # ---- D：最近几条用户消息有多短 ----
    def recent_user_lengths(self, count: int) -> list[int]:
        return [len(m["content"].strip()) for m in self.user_msgs[-count:]]

    def describe(self) -> str:
        mins = self.elapsed_minutes
        to_close = self.minutes_to_close()
        lens = self.recent_user_lengths(2)
        return (
            f"消息总数={len(self.messages)} 用户消息={len(self.user_msgs)} "
            f"本次游览≈{mins:.1f}分钟 "
            f"距闭园={('N/A' if to_close is None else f'{to_close:.1f}分钟')} "
            f"最近两条用户消息长度={lens}"
        )

    def quiet_reason(self, now_ms: int) -> str:
        """当前是否处于"不打扰"状态，以及原因。"""
        quiet_ms = int(float(self.cfg.get("quiet_after_user_sec", 60)) * 1000)
        if not self.last_msg_is_user:
            return ""
        gap = now_ms - self.last_msg_ts
        if gap < quiet_ms:
            return f"用户 {gap / 1000:.0f} 秒前刚说过话，B/C 规则暂不打扰"
        return ""


def _is_user(msg_type: str) -> bool:
    """Fay 里用户消息 type='member'，数字人 type='fay'。"""
    t = (msg_type or "").lower()
    return t not in {"fay", "robot", "system"}


# ─────────────────────────────────────────────────────────────
# 判断：哪些规则该触发
# ─────────────────────────────────────────────────────────────

def evaluate(obs: Observation, fired: dict, now_ms: int) -> list[tuple[str, str]]:
    """返回 [(rule_id, observation_text), ...]"""
    cfg = obs.cfg
    rules = cfg.get("rules") or {}
    out: list[tuple[str, str]] = []

    quiet_ms = int(float(cfg.get("quiet_after_user_sec", 60)) * 1000)
    user_recently_active = bool(obs.last_msg_is_user) and (now_ms - obs.last_msg_ts) < quiet_ms

    def due(rule_id: str) -> bool:
        spec = rules.get(rule_id) or {}
        if not spec.get("enabled"):
            return False
        last = fired.get(rule_id, 0)
        return (now_ms - last) >= float(spec.get("cooldown_sec", 1800)) * 1000

    # ── B：已在景区游览超过 N 分钟 ──
    spec_b = rules.get("B_long_stay") or {}
    if due("B_long_stay") and not user_recently_active:
        if obs.elapsed_minutes >= float(spec_b.get("after_minutes", 30)):
            out.append((
                "B_long_stay",
                f"用户已经连续游览了约 {obs.elapsed_minutes:.0f} 分钟。",
            ))

    # ── C：快到闭园时间 ──
    spec_c = rules.get("C_closing_soon") or {}
    if due("C_closing_soon") and not user_recently_active:
        to_close = obs.minutes_to_close()
        if to_close is not None and 0 < to_close <= float(spec_c.get("before_minutes", 30)):
            out.append((
                "C_closing_soon",
                f"距离景区闭园只剩约 {to_close:.0f} 分钟，用户仍在景区内。",
            ))

    # ── D：用户连续几次回复都很短 ──
    # 注意：D 不吃"用户刚活跃就不打扰"这条，因为它的触发场景本来就是"用户刚说话"
    spec_d = rules.get("D_short_replies") or {}
    if due("D_short_replies"):
        need = int(spec_d.get("count", 2))
        max_chars = int(spec_d.get("max_chars", 4))
        lens = obs.recent_user_lengths(need)
        if len(lens) >= need and all(n <= max_chars for n in lens):
            # 观察到的**事实**，把判断留给 Fay
            out.append((
                "D_short_replies",
                f"用户最近 {need} 次回复都很简短（分别是 {', '.join(str(n) for n in lens)} 个字）。",
            ))

    # 多条同时命中时，让"更紧急"的先说。
    # 默认优先级：C 快闭园(100) > D 用户状态(60) > B 逛久了(20)
    out.sort(
        key=lambda item: float((rules.get(item[0]) or {}).get("priority", 0)),
        reverse=True,
    )
    return out


# ─────────────────────────────────────────────────────────────
# 主循环
# ─────────────────────────────────────────────────────────────

def load_config(path: Path) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # 深拷贝默认值
    if path.is_file():
        user_cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg.update({k: v for k, v in user_cfg.items() if k != "rules"})
        merged_rules = dict(DEFAULT_CONFIG["rules"])
        merged_rules.update(user_cfg.get("rules") or {})
        cfg["rules"] = merged_rules
    return cfg


def run_once(cfg: dict, fired: dict, dry_run: bool, verbose: bool) -> int:
    now_ms = int(time.time() * 1000)
    base = cfg["fay_base"]
    username = cfg["username"]

    try:
        messages = fetch_messages(base, username)
    except (error.URLError, OSError, TimeoutError) as exc:
        print(f"[看门狗] 读消息失败（Fay 在跑吗？）：{exc}", flush=True)
        return 0

    obs = Observation(messages, now_ms, cfg)
    if verbose:
        print(f"[观察] {obs.describe()}", flush=True)

    triggered = evaluate(obs, fired, now_ms)
    if not triggered:
        if verbose:
            reason = obs.quiet_reason(now_ms)
            print(f"[看门狗] {reason}" if reason else "[看门狗] 暂无规则满足触发条件", flush=True)
        return 0

    # 全局冷却：同一次主动后，间隔 global_cooldown_sec 再说下一句
    last_any = max(fired.values()) if fired else 0
    global_cd_ms = float(cfg.get("global_cooldown_sec", 300)) * 1000
    if fired and (now_ms - last_any) < global_cd_ms:
        if verbose:
            left = (global_cd_ms - (now_ms - last_any)) / 1000
            print(f"[看门狗] 全局冷却中，还要等 {left:.0f} 秒", flush=True)
        return 0

    rule_id, observation = triggered[0]
    others = [rid for rid, _ in triggered[1:]]

    if dry_run:
        print(f"[DRY-RUN] 本应触发 {rule_id}，推送内容：{observation}", flush=True)
        if others:
            print(f"[DRY-RUN] 同时命中的其它规则（优先级更低，本轮不发）：{others}", flush=True)
        return 0

    since_ms = int(time.time() * 1000)
    try:
        push_observation(base, username, observation)
    except (error.URLError, OSError, TimeoutError) as exc:
        print(f"[看门狗] 推送失败：{exc}", flush=True)
        return 0

    fired[rule_id] = now_ms
    fired["_last_any"] = now_ms
    print(f"[主动] 触发 {rule_id}，观测：{observation}", flush=True)
    if others:
        print(f"[看门狗] 同时命中的其它规则（本轮不发）：{others}", flush=True)

    reply = wait_for_reply(base, username, since_ms)
    if reply:
        print(f"[灵灵] {reply}", flush=True)
    else:
        print("[灵灵] （等超时了，没读到回复；去 Fay 控制台看日志）", flush=True)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="时间看门狗：真实的主动事件源")
    parser.add_argument("--config", default=str(Path(__file__).with_name("watchdog.config.json")))
    parser.add_argument("--once", action="store_true", help="只跑一轮")
    parser.add_argument("--dry-run", action="store_true", help="只打印不发送")
    parser.add_argument("--verbose", "-v", action="store_true", help="打印每轮观察到的数据")
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    interval = float(cfg.get("poll_interval_sec", 30))

    print("=" * 66, flush=True)
    print("时间看门狗已启动", flush=True)
    print(f"  目标        : {cfg['fay_base']}  (用户 {cfg['username']})", flush=True)
    print(f"  轮询间隔    : {interval:.0f} 秒", flush=True)
    print(f"  全局冷却    : {cfg.get('global_cooldown_sec')} 秒", flush=True)
    print(f"  闭园时间    : {cfg.get('park_close_time')}", flush=True)
    for rid, spec in (cfg.get("rules") or {}).items():
        flag = "启用" if spec.get("enabled") else "停用"
        print(f"  规则 {rid:18} {flag}  {json.dumps({k: v for k, v in spec.items() if k != 'enabled'}, ensure_ascii=False)}", flush=True)
    print(f"  DRY-RUN     : {args.dry_run}", flush=True)
    print("=" * 66, flush=True)

    fired: dict = {}
    while True:
        try:
            run_once(cfg, fired, args.dry_run, args.verbose)
        except KeyboardInterrupt:
            print("\n[看门狗] 已停止", flush=True)
            return 0
        except Exception as exc:  # 单轮失败不影响后续轮次
            print(f"[看门狗] 本轮异常：{exc}", flush=True)
        if args.once:
            return 0
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
