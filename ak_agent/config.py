"""Agent runtime config — everything env-driven."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = AGENT_ROOT.parent
CALIB_DIR = AGENT_ROOT / "calib"
FRIDA_AGENT_JS = AGENT_ROOT / "frida" / "dist" / "agent.js"
LOCAL_STATE_READER = AGENT_ROOT / "runtime" / "mread" / "state_reader.py"
WORKSPACE_STATE_READER = PROJECT_ROOT / "tools" / "mread" / "state_reader.py"
DEFAULT_STATE_READER = (LOCAL_STATE_READER if LOCAL_STATE_READER.is_file()
                        else WORKSPACE_STATE_READER)


@dataclass(frozen=True)
class Config:
    # device / backend
    adb_serial: str            # "" → default device
    backend: str               # "mread" (default) | "frida" (experimental) | "adb"
    frida_host: str            # e.g. "127.0.0.1:27042" or "usb"
    frida_target: str          # process name to attach/spawn
    spawn_game: bool           # frida spawn (cold) vs attach
    # grid calibration file for adb backend (per-resolution)
    calib_file: Path
    state_reader: Path
    mread_port: int
    adb_path: str
    # llm routing — passed to claude-agent-sdk env
    anthropic_base_url: str    # e.g. http://127.0.0.1:3456 (claude-code-router)
    anthropic_auth_token: str
    model: str                 # model name the router resolves, e.g. "deepseek-chat"


def load() -> Config:
    return Config(
        adb_serial=os.environ.get("AK_ADB_SERIAL", ""),
        backend=os.environ.get("AK_BACKEND", "mread"),
        frida_host=os.environ.get("AK_FRIDA", "usb"),   # usb device or host:port
        frida_target=os.environ.get(
            "AK_FRIDA_TARGET", "com.hypergryph.arknights.bilibili"
        ),
        spawn_game=os.environ.get("AK_SPAWN", "0") == "1",
        calib_file=Path(os.environ.get(
            "AK_CALIB", str(CALIB_DIR / "board_default.json"))),
        state_reader=Path(os.environ.get(
            "AK_STATE_READER", str(DEFAULT_STATE_READER))),
        mread_port=int(os.environ.get("AK_MREAD_PORT", "27047")),
        adb_path=os.environ.get("AK_ADB", "adb.exe" if os.name == "nt" else "adb"),
        anthropic_base_url=os.environ.get("ANTHROPIC_BASE_URL", ""),
        anthropic_auth_token=os.environ.get("ANTHROPIC_AUTH_TOKEN", ""),
        model=os.environ.get("AK_MODEL", "deepseek-chat"),
    )
