"""Telegram handler modules."""
from __future__ import annotations

from typing import List

from telegram.ext import Application

from ubuntu.context import AppContext
from ubuntu.handlers import (
    callbacks,
    commands,
    files,
    monitoring,
    processes,
    ssh,
    terminal,
    termux_api,
)

MODULES = (
    commands,
    terminal,
    monitoring,
    processes,
    files,
    termux_api,
    ssh,
    callbacks,
)


def register_all(application: Application, ctx: AppContext) -> List[str]:
    registered = []
    for module in MODULES:
        module.register(application, ctx)
        registered.append(module.__name__.rsplit(".", 1)[-1])
    return registered
