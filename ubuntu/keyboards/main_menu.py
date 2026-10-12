"""Inline keyboard layouts for the hp-ungu bot."""
from __future__ import annotations

from typing import Iterable, List, Sequence

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

MAIN_MENU_ROWS: Sequence[Sequence[tuple[str, str]]] = (
    (("🖥 Terminal", "m:terminal"), ("🐧 Ubuntu Status", "m:ubuntu")),
    (("📱 Device Status", "m:device"), ("🔋 Battery", "m:battery")),
    (("💾 Storage", "m:storage"), ("🌐 Network", "m:network")),
    (("⚙️ Processes", "m:processes"), ("📦 PM2", "m:pm2")),
    (("🤖 Termux:API", "m:termuxapi"), ("📁 File Manager", "m:files")),
    (("📜 Logs", "m:logs"), ("⏳ Running Jobs", "m:jobs")),
    (("🔐 SSH Connection", "m:ssh"), ("🔎 SSH Status", "m:sshstatus")),
    (("🩺 Health", "m:health"), ("🛠 Settings", "m:settings")),
)


def main_menu() -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for row in MAIN_MENU_ROWS:
        rows.append([InlineKeyboardButton(label, callback_data=data) for label, data in row])
    return InlineKeyboardMarkup(rows)


def back_only(target: str = "m:main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=target)]])


def with_back(rows: Iterable[Iterable[InlineKeyboardButton]],
              target: str = "m:main") -> InlineKeyboardMarkup:
    built = [list(row) for row in rows]
    built.append([InlineKeyboardButton("⬅️ Back", callback_data=target)])
    return InlineKeyboardMarkup(built)


def confirm_keyboard(token: str, yes_label: str = "✅ Confirm",
                     no_label: str = "❌ Cancel") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(yes_label, callback_data=f"cf:{token}:y"),
            InlineKeyboardButton(no_label, callback_data=f"cf:{token}:n"),
        ]]
    )


def pagination(page: int, total_pages: int, prefix: str) -> List[InlineKeyboardButton]:
    buttons: List[InlineKeyboardButton] = []
    if page > 0:
        buttons.append(InlineKeyboardButton("⬅️", callback_data=f"{prefix}:{page - 1}"))
    buttons.append(
        InlineKeyboardButton(f"{page + 1}/{max(1, total_pages)}", callback_data="noop")
    )
    if page + 1 < total_pages:
        buttons.append(InlineKeyboardButton("➡️", callback_data=f"{prefix}:{page + 1}"))
    return buttons


def terminal_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📂 pwd", callback_data="t:pwd"),
         InlineKeyboardButton("📋 ls -la", callback_data="t:ls")],
        [InlineKeyboardButton("📊 df -h", callback_data="t:df"),
         InlineKeyboardButton("🧠 free -h", callback_data="t:free")],
        [InlineKeyboardButton("🐍 python3 --version", callback_data="t:pyver"),
         InlineKeyboardButton("🟢 node --version", callback_data="t:nodever")],
        [InlineKeyboardButton("⏳ Jobs", callback_data="m:jobs"),
         InlineKeyboardButton("🛑 Cancel all", callback_data="t:cancelall")],
        [InlineKeyboardButton("⬅️ Back", callback_data="m:main")],
    ]
    return InlineKeyboardMarkup(rows)


def settings_keyboard(shell_enabled: bool, master_enabled: bool) -> InlineKeyboardMarkup:
    state = "ON" if shell_enabled else "OFF"
    rows = [
        [InlineKeyboardButton(f"🔓 Shell mode: {state}", callback_data="s:toggleshell")],
        [InlineKeyboardButton("♻️ Reset working directory", callback_data="s:resetcwd")],
        [InlineKeyboardButton("🔌 Test bridge connection", callback_data="s:testbridge")],
        [InlineKeyboardButton("🧪 Run self-check", callback_data="s:selfcheck")],
        [InlineKeyboardButton("⬅️ Back", callback_data="m:main")],
    ]
    if not master_enabled:
        rows.insert(
            0,
            [InlineKeyboardButton("⛔ Shell disabled by .env", callback_data="noop")],
        )
    return InlineKeyboardMarkup(rows)
