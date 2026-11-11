"""Executor backends + auto-router."""
from __future__ import annotations

from typing import Optional

from ..config import Config
from ..grid import BoardCalib
from .adb_exec import AdbExecutor
from .base import OpExecutor


def build(cfg: Config) -> OpExecutor:
    calib: Optional[BoardCalib] = None
    if cfg.calib_file.exists():
        calib = BoardCalib.load(cfg.calib_file)

    backend = cfg.backend
    if backend in ("auto", "frida"):
        try:
            from .frida_exec import FridaExecutor
            ex = FridaExecutor(cfg, calib)
            if ex.alive():
                return ex
        except Exception as e:  # noqa: BLE001
            if backend == "frida":
                raise
            print(f"[exec] frida unavailable ({e}), falling back to adb")
    return AdbExecutor(cfg.adb_serial, calib)


__all__ = ["OpExecutor", "AdbExecutor", "build"]
