"""Collects Ubuntu userspace and Android host metrics."""
from __future__ import annotations

import asyncio
import os
import platform
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from ubuntu.config import Settings
from ubuntu.services.termux_bridge_client import BridgeError, TermuxBridgeClient
from ubuntu.utils.logging_config import get_logger

log = get_logger(__name__)

UNAVAILABLE = "Unavailable"


def _read_text(path: str) -> Optional[str]:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _first_line(path: str) -> Optional[str]:
    text = _read_text(path)
    if text is None:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return None


class DeviceMonitor:
    """Read-only monitoring facade."""

    def __init__(self, settings: Settings, bridge: TermuxBridgeClient) -> None:
        self.settings = settings
        self.bridge = bridge

    # ---------------------------------------------------------------- ubuntu
    def os_release(self) -> Dict[str, str]:
        info: Dict[str, str] = {}
        text = _read_text("/etc/os-release") or ""
        for line in text.splitlines():
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            info[key.strip()] = value.strip().strip('"')
        return info

    def memory(self) -> Dict[str, Optional[float]]:
        """Memory in bytes. Inside PRoot this reflects the Android host."""
        text = _read_text("/proc/meminfo") or ""
        values: Dict[str, int] = {}
        for line in text.splitlines():
            if ":" not in line:
                continue
            key, _, rest = line.partition(":")
            parts = rest.strip().split()
            if not parts:
                continue
            try:
                values[key.strip()] = int(parts[0]) * 1024
            except ValueError:
                continue
        total = values.get("MemTotal")
        available = values.get("MemAvailable")
        if total is None:
            return {"total": None, "available": None, "used": None, "percent": None}
        if available is None:
            available = values.get("MemFree")
        used = None if available is None else total - available
        percent = None if (used is None or not total) else used / total * 100.0
        return {"total": total, "available": available, "used": used, "percent": percent}

    def load_average(self) -> Optional[tuple[float, float, float]]:
        line = _first_line("/proc/loadavg")
        if not line:
            return None
        parts = line.split()
        try:
            return float(parts[0]), float(parts[1]), float(parts[2])
        except (IndexError, ValueError):
            return None

    def uptime_seconds(self) -> Optional[float]:
        line = _first_line("/proc/uptime")
        if not line:
            return None
        try:
            return float(line.split()[0])
        except (IndexError, ValueError):
            return None

    def disk(self, path: Path) -> Dict[str, Optional[float]]:
        try:
            usage = shutil.disk_usage(str(path))
        except OSError:
            return {"total": None, "used": None, "free": None, "percent": None}
        percent = usage.used / usage.total * 100.0 if usage.total else 0.0
        return {
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "percent": percent,
        }

    async def _tool_version(self, *args: str, timeout: float = 5.0) -> Optional[str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except (FileNotFoundError, PermissionError):
            return None
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return None
        if proc.returncode != 0:
            return None
        return stdout.decode("utf-8", "replace").strip().splitlines()[0]

    async def ubuntu_status(self) -> Dict[str, Any]:
        release = self.os_release()
        node_version = await self._tool_version("node", "--version")
        return {
            "distribution": release.get("PRETTY_NAME") or release.get("NAME") or UNAVAILABLE,
            "version_id": release.get("VERSION_ID") or UNAVAILABLE,
            "kernel": platform.release() or UNAVAILABLE,
            "architecture": platform.machine() or UNAVAILABLE,
            "hostname": platform.node() or UNAVAILABLE,
            "python": platform.python_version(),
            "node": node_version or UNAVAILABLE,
            "cpu_count": os.cpu_count(),
            "load": self.load_average(),
            "memory": self.memory(),
            "disk": self.disk(self.settings.default_working_directory),
            "uptime": self.uptime_seconds(),
            "proot": "PRoot metrics reflect the Android host kernel and are not "
                     "device-wide Android statistics.",
        }

    # --------------------------------------------------------------- android
    async def android_status(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {"available": False, "error": None}
        try:
            features = await self.bridge.features()
        except BridgeError as exc:
            result["error"] = str(exc)
            return result

        result["available"] = True
        result["features"] = features.get("available", {})
        result["unsupported"] = features.get("unsupported", [])

        for key, coro in (
            ("battery", self.bridge.battery()),
            ("device", self.bridge.device_info()),
            ("wifi", self.bridge.wifi_info()),
        ):
            try:
                result[key] = await coro
            except BridgeError as exc:
                result[key] = {"error": str(exc)}
        return result

    async def bridge_health(self) -> Dict[str, Any]:
        try:
            return {"online": True, **(await self.bridge.health())}
        except BridgeError as exc:
            return {"online": False, "error": str(exc), "code": exc.code}
