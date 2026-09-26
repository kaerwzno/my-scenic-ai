from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RELEASE_DATA_DIR = PROJECT_ROOT / "packaging" / "release" / "faymcp" / "data"
BASELINE_PATH = "faymcp/data/mcp_servers.json"
PYTHON_COMMANDS = {"python", "python.exe", "pythonw", "pythonw.exe"}
NODE_COMMANDS = {"npx", "npx.cmd", "npm", "npm.cmd", "node", "node.exe"}
PORTABLE_PYTHON_SCRIPTS = {
    "test/mcp_stdio_example.py",
    "mcp_servers/schedule_manager/server.py",
    "mcp_servers/window_capture/server.py",
    "mcp_servers/mcp-todo-server/server.py",
    "mcp_servers/elderly_mcp/server.py",
    "mcp_servers/fay_player_knowledge/fay_player_knowledge_base_mcp_server.py",
}
PORTABLE_NODE_PACKAGES = {
    "@browsermcp/mcp@latest",
}
ABSOLUTE_PATH_RE = re.compile(r"^(?:[a-zA-Z]:[\\/]|\\\\|/)")


def _run_git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(PROJECT_ROOT),
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _load_baseline_text() -> str:
    for ref in ("HEAD", "origin/main"):
        try:
            result = _run_git("show", f"{ref}:{BASELINE_PATH}")
            if result.stdout.strip():
                return result.stdout
        except Exception:
            continue

    return (PROJECT_ROOT / BASELINE_PATH).read_text(encoding="utf-8")


def _contains_absolute_path(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_absolute_path(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_absolute_path(item) for item in value)
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return False
    return bool(ABSOLUTE_PATH_RE.match(text))


def _normalize_command(command: Any) -> str:
    text = str(command or "").strip().lower()
    return Path(text).name.lower()


def _normalize_rel_path(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip().lower()


def _is_portable_python_server(server: Dict[str, Any]) -> bool:
    command = _normalize_command(server.get("command"))
    if command not in PYTHON_COMMANDS:
        return False
    if _contains_absolute_path(server.get("cwd")) or _contains_absolute_path(server.get("env")):
        return False
    args = server.get("args") or []
    if _contains_absolute_path(args):
        return False

    cwd = _normalize_rel_path(server.get("cwd"))
    for arg in args:
        if not isinstance(arg, str) or not arg.strip().endswith(".py"):
            continue
        arg_path = _normalize_rel_path(arg)
        if arg_path in PORTABLE_PYTHON_SCRIPTS:
            return True
        if cwd and f"{cwd}/{Path(arg_path).name}" in PORTABLE_PYTHON_SCRIPTS:
            return True
    return False


def _is_portable_node_server(server: Dict[str, Any]) -> bool:
    command = _normalize_command(server.get("command"))
    if command not in NODE_COMMANDS:
        return False
    if any(_contains_absolute_path(server.get(field)) for field in ("args", "cwd", "env")):
        return False
    args = [_normalize_rel_path(arg) for arg in (server.get("args") or []) if isinstance(arg, str)]
    return any(arg in PORTABLE_NODE_PACKAGES for arg in args)


def _rewrite_server(server: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    rewritten = {
        "id": server.get("id"),
        "name": server.get("name", ""),
        "ip": "",
        "connection_time": "",
        "key": "",
        "transport": server.get("transport", "stdio"),
        "command": server.get("command", ""),
        "args": list(server.get("args") or []),
        "cwd": server.get("cwd", "") or "",
        "env": dict(server.get("env") or {}),
    }

    if _is_portable_python_server(server):
        rewritten["command"] = "fay.exe"
        rewritten["args"] = ["--mcp-stdio-runner"] + list(server.get("args") or [])
        return rewritten

    if _is_portable_node_server(server):
        return rewritten

    return None


def main() -> int:
    baseline_servers = json.loads(_load_baseline_text())
    release_servers: List[Dict[str, Any]] = []
    for server in baseline_servers:
        rewritten = _rewrite_server(server)
        if rewritten is not None:
            release_servers.append(rewritten)

    RELEASE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    (RELEASE_DATA_DIR / "mcp_servers.json").write_text(
        json.dumps(release_servers, ensure_ascii=False, indent=4),
        encoding="utf-8",
    )
    (RELEASE_DATA_DIR / "mcp_prestart_tools.json").write_text("{}", encoding="utf-8")
    (RELEASE_DATA_DIR / "mcp_tool_states.json").write_text("{}", encoding="utf-8")
    print(f"Generated release MCP config with {len(release_servers)} servers from Git baseline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
