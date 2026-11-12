"""Executor backends + auto-router."""
from __future__ import annotations

import subprocess
from typing import Optional

from ..config import Config
from ..grid import BoardCalib
from .adb_exec import AdbExecutor
from .base import OpExecutor


def _resolve_serial(adb_path: str, requested: str) -> str:
    if requested:
        return requested
    result = subprocess.run([adb_path, "devices"], capture_output=True, text=True,
                            timeout=10)
    serials = [line.split()[0] for line in result.stdout.splitlines()
               if line.strip().endswith("\tdevice")]
    if len(serials) != 1:
        raise RuntimeError(f"AK_ADB_SERIAL is required when adb has {len(serials)} devices")
    return serials[0]


def build(cfg: Config) -> OpExecutor:
    calib: Optional[BoardCalib] = None
    if cfg.calib_file.exists():
        calib = BoardCalib.load(cfg.calib_file)

    backend = cfg.backend
    serial = _resolve_serial(cfg.adb_path, cfg.adb_serial)
    if backend in ("mread", "auto"):
        from .mread_exec import MreadExecutor
        return MreadExecutor(serial=serial, calib=calib,
                             state_reader=cfg.state_reader, mread_port=cfg.mread_port,
                             adb_path=cfg.adb_path)
    if backend == "frida":
        try:
            from .frida_exec import FridaExecutor
            ex = FridaExecutor(cfg, calib)
            if ex.alive():
                return ex
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"frida backend unavailable: {e}") from e
    if backend == "adb":
        return AdbExecutor(serial, calib, cfg.adb_path)
    raise ValueError(f"unknown AK_BACKEND: {backend}")


__all__ = ["OpExecutor", "AdbExecutor", "build"]
