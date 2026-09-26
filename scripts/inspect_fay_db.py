#!/usr/bin/env python3
"""看 Fay 的数据库里有什么可做分析的数据。

用途：设计管理端的"数据分析"接口前，先摸清数据源。
Fay 的对话记录不在我们自己的库里，而在 Fay 自己的 sqlite 里。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


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

FAY_MEMORY = Path(r"D:\PROJECT\scenic_ai\重构数字人\Fay-main\memory")


def dump(db: Path) -> None:
    if not db.is_file():
        print(f"（不存在）{db}")
        return
    print("=" * 70)
    print(f"{db.name}   {db.stat().st_size / 1024:.1f} KB")
    print("=" * 70)
    conn = sqlite3.connect(str(db))
    try:
        tables = [r[0] for r in conn.execute(
            "select name from sqlite_master where type='table'")]
        for name in tables:
            cols = [r[1] for r in conn.execute(f"pragma table_info({name})")]
            count = conn.execute(f"select count(*) from {name}").fetchone()[0]
            print(f"  表 {name}   （{count} 行）")
            print(f"     列：{', '.join(cols)}")
            if count and count <= 5000:
                sample = conn.execute(f"select * from {name} limit 2").fetchall()
                for row in sample:
                    print(f"     样例：{str(row)[:150]}")
            print()
    finally:
        conn.close()


def main() -> int:
    for name in ("fay.db", "user_profiles.db"):
        dump(FAY_MEMORY / name)
        print()

    # 记忆流
    mem_dir = FAY_MEMORY / "User" / "memory_stream"
    for f in ("nodes.json", "embeddings.json"):
        p = mem_dir / f
        if p.is_file():
            print(f"  {f}: {p.stat().st_size / 1024:.1f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
