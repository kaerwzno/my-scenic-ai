from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = PROJECT_ROOT / "dist"
BUILD_DIR = PROJECT_ROOT / "build"
MCP_DIST_DIR = DIST_DIR / "mcp_bin"
MCP_WORK_DIR = BUILD_DIR / "mcp_bin"
MCP_SPEC_DIR = BUILD_DIR / "mcp_specs"
RELEASE_MEMORY_DIR = PROJECT_ROOT / "packaging" / "release" / "memory"

DEFAULT_MAIN_PYTHON = Path(r"C:\Users\Lenovo\anaconda3\envs\fay312\python.exe")
DEFAULT_RAG_PYTHON = Path(r"C:\Users\Lenovo\anaconda3\envs\rag\python.exe")


@dataclass
class BuildTarget:
    name: str
    script: Path
    python_kind: str = "main"
    extra_args: List[str] = field(default_factory=list)
    rewrite_suffix: Optional[str] = None


TARGETS: List[BuildTarget] = [
    BuildTarget(
        name="mcp_stdio_example",
        script=PROJECT_ROOT / "test" / "mcp_stdio_example.py",
        extra_args=["--collect-all", "mcp"],
        rewrite_suffix="test/mcp_stdio_example.py",
    ),
    BuildTarget(
        name="schedule_manager_mcp",
        script=PROJECT_ROOT / "mcp_servers" / "schedule_manager" / "server.py",
        extra_args=["--collect-all", "mcp"],
        rewrite_suffix="mcp_servers/schedule_manager/server.py",
    ),
    BuildTarget(
        name="schedule_manager_web",
        script=PROJECT_ROOT / "mcp_servers" / "schedule_manager" / "web_server.py",
        extra_args=[
            "--hidden-import",
            "flask",
            "--hidden-import",
            "flask_cors",
            "--hidden-import",
            "psutil",
            "--add-data",
            f"{PROJECT_ROOT / 'mcp_servers' / 'schedule_manager' / 'schedule_web.html'};.",
        ],
    ),
    BuildTarget(
        name="logseq_mcp",
        script=PROJECT_ROOT / "mcp_servers" / "logseq" / "server.py",
        extra_args=["--collect-all", "mcp"],
        rewrite_suffix="mcp_servers/logseq/server.py",
    ),
    BuildTarget(
        name="window_capture_mcp",
        script=PROJECT_ROOT / "mcp_servers" / "window_capture" / "server.py",
        extra_args=["--collect-all", "mcp"],
        rewrite_suffix="mcp_servers/window_capture/server.py",
    ),
    BuildTarget(
        name="todo_server_mcp",
        script=PROJECT_ROOT / "mcp_servers" / "mcp-todo-server" / "server.py",
        rewrite_suffix="mcp_servers/mcp-todo-server/server.py",
    ),
    BuildTarget(
        name="elderly_mcp_server",
        script=PROJECT_ROOT / "mcp_servers" / "elderly_mcp" / "server.py",
        extra_args=["--collect-all", "mcp"],
        rewrite_suffix="mcp_servers/elderly_mcp/server.py",
    ),
    BuildTarget(
        name="yueshen_rag_mcp",
        script=PROJECT_ROOT / "mcp_servers" / "yueshen_rag" / "server.py",
        python_kind="rag",
        extra_args=[
            "--hidden-import",
            "typing_extensions",
            "--collect-all",
            "mcp",
            "--collect-all",
            "chromadb",
            "--collect-all",
            "docx",
            "--collect-all",
            "pdfplumber",
            "--collect-all",
            "tornado",
        ],
        rewrite_suffix="mcp_servers/yueshen_rag/server.py",
    ),
]


def _pick_python(preferred: Path, fallback: str) -> str:
    if preferred.exists():
        return str(preferred)
    return fallback


def _run(command: List[str]) -> None:
    print(" ".join(f'"{part}"' if " " in part else part for part in command))
    subprocess.run(command, cwd=str(PROJECT_ROOT), check=True)


def _reset_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _has_pyinstaller(python_exe: str) -> bool:
    probe = subprocess.run(
        [python_exe, "-c", "import PyInstaller; print('ok')"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
    )
    return probe.returncode == 0


def _prepare_release_memory_snapshot() -> None:
    subprocess.run(
        ["git", "restore", "--source=HEAD", "--worktree", "--staged", "--", "memory"],
        cwd=str(PROJECT_ROOT),
        check=True,
    )
    subprocess.run(
        ["git", "clean", "-fd", "--", "memory"],
        cwd=str(PROJECT_ROOT),
        check=True,
    )
    if RELEASE_MEMORY_DIR.exists():
        shutil.rmtree(RELEASE_MEMORY_DIR)
    subprocess.run(
        [
            "git",
            "checkout-index",
            "--force",
            "--prefix=packaging/release/",
            "--",
            "memory/fay.db",
            "memory/user_profiles.db",
        ],
        cwd=str(PROJECT_ROOT),
        check=True,
    )


def _sync_release_mcp_config(python_exe: str) -> None:
    subprocess.run(
        [python_exe, str(PROJECT_ROOT / "packaging" / "sync_release_mcp_config.py")],
        cwd=str(PROJECT_ROOT),
        check=True,
    )


def _normalize_rel_path(path_value: Optional[str]) -> str:
    return str(path_value or "").replace("\\", "/").strip().lower()


def _path_matches(path_value: Optional[str], expected_suffix: str) -> bool:
    normalized = _normalize_rel_path(path_value)
    suffix = _normalize_rel_path(expected_suffix)
    if not normalized or not suffix:
        return False
    return normalized == suffix or normalized.endswith("/" + suffix)


def _build_target(target: BuildTarget, python_exe: str) -> Optional[str]:
    if not target.script.exists():
        print(f"[skip] missing script: {target.script}")
        return None

    command = [
        python_exe,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        "--onedir",
        "--distpath",
        str(MCP_DIST_DIR),
        "--workpath",
        str(MCP_WORK_DIR),
        "--specpath",
        str(MCP_SPEC_DIR),
        "--paths",
        str(PROJECT_ROOT),
        "--name",
        target.name,
    ]
    command.extend(target.extra_args)
    command.append(str(target.script))
    _run(command)
    return f"mcp_bin\\{target.name}\\{target.name}.exe"


def _rewrite_release_mcp_config(output_path: Path, built_targets: Dict[str, str]) -> None:
    source_path = PROJECT_ROOT / "packaging" / "release" / "faymcp" / "data" / "mcp_servers.json"
    servers = json.loads(source_path.read_text(encoding="utf-8"))

    for server in servers:
        args = server.get("args") or []
        arg_paths = [arg for arg in args if isinstance(arg, str) and arg and not arg.startswith("-")]
        rewrite_to = None
        for suffix, exe_relpath in built_targets.items():
            if any(_path_matches(arg, suffix) for arg in arg_paths):
                rewrite_to = exe_relpath
                break

            configured_cwd = server.get("cwd") or ""
            suffix_dir = str(Path(suffix).parent).replace("\\", "/")
            if suffix_dir and _path_matches(configured_cwd, suffix_dir):
                if not arg_paths or any(Path(arg).name.lower() == Path(suffix).name.lower() for arg in arg_paths):
                    rewrite_to = exe_relpath
                    break

        if rewrite_to:
            server["command"] = rewrite_to
            server["args"] = []
            server["cwd"] = ""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(servers, ensure_ascii=False, indent=4), encoding="utf-8")


def _copy_packaged_mcp_into_dist() -> None:
    target_dir = DIST_DIR / "fay" / "mcp_bin"
    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(MCP_DIST_DIR, target_dir)


def _find_iscc(explicit: Optional[str]) -> Optional[str]:
    if explicit:
        return explicit

    which_path = shutil.which("ISCC.exe")
    if which_path:
        return which_path

    for candidate in [
        Path(r"C:\Users\Lenovo\AppData\Local\Programs\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    ]:
        if candidate.exists():
            return str(candidate)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Fay Windows release with packaged MCP servers.")
    parser.add_argument("--main-python", default=_pick_python(DEFAULT_MAIN_PYTHON, sys.executable))
    parser.add_argument("--rag-python", default=str(DEFAULT_RAG_PYTHON) if DEFAULT_RAG_PYTHON.exists() else "")
    parser.add_argument("--skip-yueshen", action="store_true")
    parser.add_argument(
        "--targets",
        nargs="+",
        default=[],
        help="Only build the listed MCP targets, for example: --targets yueshen_rag_mcp",
    )
    parser.add_argument("--skip-main", action="store_true")
    parser.add_argument("--skip-installer", action="store_true")
    parser.add_argument("--iscc", default="")
    args = parser.parse_args()

    requested_targets = {name.strip() for name in (args.targets or []) if str(name).strip()}
    unknown_targets = sorted(requested_targets - {target.name for target in TARGETS})
    if unknown_targets:
        raise ValueError(f"Unknown MCP targets: {', '.join(unknown_targets)}")

    _reset_dir(MCP_DIST_DIR)
    _reset_dir(MCP_WORK_DIR)
    _reset_dir(MCP_SPEC_DIR)
    _prepare_release_memory_snapshot()
    _sync_release_mcp_config(args.main_python)

    built_targets: Dict[str, str] = {}
    for target in TARGETS:
        if requested_targets and target.name not in requested_targets:
            print(f"[skip] {target.name} not in --targets selection")
            continue

        if target.name == "yueshen_rag_mcp" and args.skip_yueshen:
            print("[skip] yueshen_rag_mcp disabled by flag")
            continue

        python_exe = args.main_python
        if target.python_kind == "rag":
            if not args.rag_python:
                print("[skip] rag python not configured, leave yueshen_rag as external server")
                continue
            python_exe = args.rag_python

        if not os.path.exists(python_exe):
            if target.python_kind == "rag":
                print(f"[skip] rag python not found: {python_exe}")
                continue
            raise FileNotFoundError(f"Main python not found: {python_exe}")

        if target.python_kind == "rag" and not _has_pyinstaller(python_exe):
            print(f"[skip] PyInstaller is not available in rag python: {python_exe}")
            continue

        exe_relpath = _build_target(target, python_exe)
        if exe_relpath and target.rewrite_suffix:
            built_targets[target.rewrite_suffix] = exe_relpath

    if args.skip_main:
        return 0

    _run([args.main_python, "-m", "PyInstaller", "--clean", "--noconfirm", "fay.spec"])
    _copy_packaged_mcp_into_dist()
    _rewrite_release_mcp_config(DIST_DIR / "fay" / "faymcp" / "data" / "mcp_servers.json", built_targets)

    if args.skip_installer:
        return 0

    iscc_path = _find_iscc(args.iscc or None)
    if not iscc_path:
        print("[warn] ISCC.exe not found, skip installer build")
        return 0

    _run([iscc_path, str(PROJECT_ROOT / "packaging" / "inno" / "fay.iss")])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
