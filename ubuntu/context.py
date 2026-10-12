"""Shared application context passed through ``bot_data``."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict

from ubuntu.config import Settings
from ubuntu.services.command_runner import CommandRunner
from ubuntu.services.device_monitor import DeviceMonitor
from ubuntu.services.file_manager import FileManager
from ubuntu.services.job_manager import JobManager
from ubuntu.services.process_manager import ProcessManager
from ubuntu.services.state_store import StateStore
from ubuntu.services.termux_bridge_client import TermuxBridgeClient


@dataclass
class AppContext:
    settings: Settings
    state: StateStore
    jobs: JobManager
    runner: CommandRunner
    monitor: DeviceMonitor
    files: FileManager
    processes: ProcessManager
    bridge: TermuxBridgeClient
    menu_actions: Dict[str, Callable[[Any, Any, AppContext], Awaitable[None]]] = field(
        default_factory=dict
    )

    def register_action(self, key: str, handler) -> None:
        self.menu_actions[key] = handler
