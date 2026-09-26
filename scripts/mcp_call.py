#!/usr/bin/env python3
"""直接调用某个 MCP 插件的工具（不经过 Fay，测试快）。

用途：验证插件本身是否正常，避免每次测试都等 Fay 的 LLM。

用法：
    python mcp_call.py kb-lingshan query "灵山大佛有多高"
    python mcp_call.py kb-graph  query "拈花湾有哪些景点"
    python mcp_call.py kb-lingshan stats
    python mcp_call.py kb-lingshan tools          # 只列工具
"""

from __future__ import annotations

import asyncio
import json
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

HERE = Path(__file__).resolve().parent
PLUGINS = HERE.parent / "plugins"
FAY_PY = Path(r"D:\PROJECT\scenic_ai\重构数字人\Fay-main\.venv\Scripts\python.exe")


async def run(plugin: str, action: str, arg: str) -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    server_py = PLUGINS / plugin / "server.py"
    if not server_py.is_file():
        print(f"插件不存在：{server_py}")
        return 2

    params = StdioServerParameters(command=str(FAY_PY), args=[str(server_py)])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = [t.name for t in (await s.list_tools()).tools]

            if action == "tools":
                print(f"[{plugin}] 工具：{tools}")
                return 0

            # 工具名是 <slug>_<action>
            slug = json.loads((PLUGINS / plugin / "plugin.json").read_text(encoding="utf-8"))["slug"] \
                if (PLUGINS / plugin / "plugin.json").is_file() else plugin.replace("-", "_")
            tool = f"{slug}_{action}"
            if tool not in tools:
                print(f"[{plugin}] 没有工具 {tool}，可用：{tools}")
                return 2

            # 参数名从工具的 inputSchema 里取——各插件不一样（向量版用 query，图谱版用 question），
            # 这里自适应，免得调用方还要记每个插件的习惯
            tool_def = next(t for t in (await s.list_tools()).tools if t.name == tool)
            # 注意：mcp 2.x 是 snake_case（input_schema），1.x 是 camelCase（inputSchema）
            schema = getattr(tool_def, "input_schema", None) or getattr(tool_def, "inputSchema", None) or {}
            props = list((schema.get("properties") if isinstance(schema, dict) else {}) or {})
            args = {props[0]: arg} if (arg and props) else {}

            res = await s.call_tool(tool, args)
            print(f"[{plugin}] {tool}")
            print("-" * 68)
            print(res.content[0].text)
            return 0


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    plugin, action = sys.argv[1], sys.argv[2]
    arg = sys.argv[3] if len(sys.argv) > 3 else ""
    return asyncio.run(run(plugin, action, arg))


if __name__ == "__main__":
    sys.exit(main())
