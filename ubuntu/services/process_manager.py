"""Ubuntu process inspection and PM2 / SSH diagnostics via the bridge."""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from ubuntu.config import Settings
from ubuntu.services.termux_bridge_client import BridgeError, TermuxBridgeClient
from ubuntu.utils.logging_config import get_logger

log = get_logger(__name__)

PROTECTED_PATTERNS = ("sshd", "pm2", "termux", "init", "proot", "hp-ungu-bridge")


class ProcessManager:
    def __init__(self, settings: Settings, bridge: TermuxBridgeClient) -> None:
        self.settings = settings
        self.bridge = bridge

    # --------------------------------------------------------------- ubuntu
    async def ubuntu_processes(self, limit: int = 25) -> List[Dict[str, Any]]:
        """List processes visible inside Ubuntu, sorted by CPU usage."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ps", "-eo", "pid,ppid,pcpu,pmem,etime,comm,args", "--sort=-pcpu",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return []
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        except asyncio.TimeoutError:
            proc.kill()
            return []

        lines = stdout.decode("utf-8", "replace").splitlines()
        if len(lines) < 2:
            return []
        results: List[Dict[str, Any]] = []
        for line in lines[1 : limit + 1]:
            parts = line.split(None, 6)
            if len(parts) < 6:
                continue
            pid, ppid, pcpu, pmem, etime, comm = parts[:6]
            args = parts[6] if len(parts) > 6 else ""
            results.append(
                {
                    "pid": pid,
                    "ppid": ppid,
                    "cpu": pcpu,
                    "mem": pmem,
                    "etime": etime,
                    "comm": comm,
                    "args": args,
                }
            )
        return results

    def is_protected(self, comm: str, args: str) -> bool:
        haystack = f"{comm} {args}".lower()
        return any(pattern in haystack for pattern in PROTECTED_PATTERNS)

    async def kill(self, pid: int, signal_name: str = "SIGTERM") -> Dict[str, Any]:
        """Kill a process *inside Ubuntu only*. Protected processes are refused."""
        listing = await self.ubuntu_processes(limit=200)
        target = next((p for p in listing if p["pid"] == str(pid)), None)
        if target is None:
            raise PermissionError(f"PID {pid} is not a visible Ubuntu process.")
        if self.is_protected(target["comm"], target["args"]):
            raise PermissionError(
                f"Refusing to signal protected process {pid} ({target['comm']})."
            )
        proc = await asyncio.create_subprocess_exec(
            "kill", f"-{signal_name}", str(pid),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", "replace").strip() or "kill failed")
        return {"pid": pid, "signal": signal_name}

    # ------------------------------------------------------------------ pm2
    async def pm2_list(self) -> Dict[str, Any]:
        try:
            return await self.bridge.pm2_list()
        except BridgeError as exc:
            return {"available": False, "error": str(exc), "processes": []}

    async def pm2_action(self, action: str, name: str) -> Dict[str, Any]:
        return await self.bridge.pm2_action(action, name)

    async def pm2_logs(self, name: str, lines: int = 40) -> Dict[str, Any]:
        return await self.bridge.pm2_logs(name, lines)

    # ------------------------------------------------------------------ ssh
    async def ssh_status(self) -> Dict[str, Any]:
        try:
            return await self.bridge.ssh_status()
        except BridgeError as exc:
            return {"available": False, "error": str(exc), "code": exc.code}

    # ---------------------------------------------------------------- logs
    def bot_log_tail(self, lines: int = 60) -> str:
        path = self.settings.log_file
        if not path.exists():
            return "No log file yet."
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                content = handle.readlines()
        except OSError as exc:
            return f"Cannot read log file: {exc}"
        return "".join(content[-lines:])
