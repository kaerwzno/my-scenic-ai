#!/usr/bin/env python3
"""从 index.html.bak 生成外壳 index.html。

外壳 = 原来的 head（含 lib 脚本和 CSS）+ 页签脚本 + app.js，
body 里只留一个空的 #app —— 模板由 app.js 塞进去（走 DOM 内联模板，
因为实测这个 vue 构建是 runtime-only，不认 createApp 的 template 选项）。
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TABS = ["dash", "kb", "graph", "tourism", "rules", "manage"]


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass

    bak = (HERE / "index.html.bak").read_text(encoding="utf-8")
    cut = bak.index("</style>") + len("</style>")
    head = bak[:cut]                      # 到 </style> 为止（含原本的三个 lib 脚本）

    tags = "\n".join(f'<script src="tabs/{k}.js"></script>' for k in TABS)
    new = (head + "\n" + tags + '\n<script src="app.js"></script>\n'
           + "</head>\n<body>\n<div id=\"app\"></div>\n</body>\n</html>\n")
    (HERE / "index.html").write_text(new, encoding="utf-8")

    print(f"外壳写完：{len(new.splitlines())} 行")
    for line in new.splitlines():
        if "<script" in line or "</style>" in line or "<body" in line or 'id="app"' in line:
            print("   ", line.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
