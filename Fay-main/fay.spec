# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from pathlib import Path

block_cipher = None


def _detect_project_root():
    spec_path = None
    for item in reversed(sys.argv):
        if isinstance(item, str) and item.lower().endswith(".spec"):
            spec_path = item
            break
    if spec_path is None:
        return os.getcwd()
    if os.path.isfile(spec_path):
        return os.path.dirname(spec_path)
    return spec_path


PROJECT_ROOT = os.path.abspath(_detect_project_root())
IS_WINDOWS = sys.platform.startswith("win")


def _project_path(*parts):
    return os.path.join(PROJECT_ROOT, *parts)


def _first_existing(*relative_paths):
    for rel_path in relative_paths:
        abs_path = _project_path(*rel_path.split("/"))
        if os.path.exists(abs_path):
            return abs_path
    return None


def _add_data_entry(data_items, source_path, target_dir):
    if source_path and os.path.exists(source_path):
        data_items.append((source_path, target_dir))


def _add_package_dlls(data_items, package_name, target_dir):
    try:
        package = __import__(package_name, fromlist=["__file__"])
        package_dir = Path(package.__file__).resolve().parent
    except Exception:
        return

    for dll_path in sorted(package_dir.glob("*.dll")):
        _add_data_entry(data_items, str(dll_path), target_dir)


datas = []

for rel_path, target_dir in [
    ("qa.csv", "."),
    ("verifier.json", "."),
    ("favicon.ico", "."),
    ("icon.png", "."),
    ("packaging/release/memory/fay.db", "memory"),
    ("packaging/release/memory/user_profiles.db", "memory"),
    ("新知识库", "新知识库"),
    ("gui/templates", "gui/templates"),
    ("gui/static", "gui/static"),
    ("gui/robot", "gui/robot"),
    ("faymcp/templates", "faymcp/templates"),
    ("faymcp/static", "faymcp/static"),
    ("faymcp/robot", "faymcp/robot"),
    ("packaging/release/faymcp/data", "faymcp/data"),
    ("test/mcp_stdio_example.py", "test"),
    ("mcp_servers/elderly_mcp", "mcp_servers/elderly_mcp"),
    ("mcp_servers/elderly_mcp.zip", "mcp_servers"),
    ("mcp_servers/fay_broadcast", "mcp_servers/fay_broadcast"),
    ("mcp_servers/logseq", "mcp_servers/logseq"),
    ("mcp_servers/mcp-todo-server", "mcp_servers/mcp-todo-server"),
    ("mcp_servers/schedule_manager", "mcp_servers/schedule_manager"),
    ("mcp_servers/window_capture", "mcp_servers/window_capture"),
    ("mcp_servers/yueshen_rag", "mcp_servers/yueshen_rag"),
    ("mcp_servers/fay_player_knowledge", "mcp_servers/fay_player_knowledge"),
    ("fay_player_knowledge", "fay_player_knowledge"),
    ("genagents/templates", "genagents/templates"),
    ("genagents/instruction.json", "genagents"),
    ("simulation_engine/prompt_template", "simulation_engine/prompt_template"),
]:
    _add_data_entry(datas, _project_path(*rel_path.split("/")), target_dir)

if IS_WINDOWS:
    _add_package_dlls(datas, "azure.cognitiveservices.speech", os.path.join("azure", "cognitiveservices", "speech"))
    _add_data_entry(datas, _project_path("test", "ovr_lipsync", "test_olipsync.py"), os.path.join("test", "ovr_lipsync"))
    _add_data_entry(datas, _project_path("test", "ovr_lipsync", "ffmpeg"), os.path.join("test", "ovr_lipsync", "ffmpeg"))
    _add_data_entry(
        datas,
        _project_path("test", "ovr_lipsync", "ovr_lipsync_exe", "ProcessWAV.exe"),
        os.path.join("test", "ovr_lipsync", "ovr_lipsync_exe"),
    )
    _add_data_entry(
        datas,
        _project_path("test", "ovr_lipsync", "ovr_lipsync_exe", "OVRLipSync.dll"),
        os.path.join("test", "ovr_lipsync", "ovr_lipsync_exe"),
    )

pathex = [PROJECT_ROOT]
if IS_WINDOWS:
    pathex.append(_project_path("test", "ovr_lipsync"))

hiddenimports = [
    "test_olipsync",
    "flask",
    "flask_cors",
    "requests",
    "requests.adapters",
    "numpy",
    "pyaudio",
    "websockets",
    "websocket",
    "websocket._core",
    "websocket._socket",
    "ws4py",
    "azure.cognitiveservices.speech",
    "aliyunsdkcore",
    "aliyunsdkcore.client",
    "aliyunsdkcore.request",
    "openpyxl",
    "flask_httpauth",
    "psutil",
    "langchain",
    "langchain_openai",
    "langgraph",
    "bs4",
    "bs4.element",
    "schedule",
    "mcp",
    "cv2",
    "pygame",
    "scipy",
    "pydub",
    "gevent",
    "gevent.pywsgi",
    "edge_tts",
    "sentence_transformers",
    "transformers",
    "faymcp.mcp_service",
    "genagents.genagents_flask",
]

a = Analysis(
    ["main.py"],
    pathex=pathex,
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[_project_path("packaging", "hooks", "rthook_block_tornado.py")],
    excludes=["tornado"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="fay",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    icon=_project_path("favicon.ico") if os.path.exists(_project_path("favicon.ico")) else None,
    contents_directory=".",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="fay",
)
