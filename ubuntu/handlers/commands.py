"""Core commands: /start /help /health."""
from __future__ import annotations

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from ubuntu.context import AppContext
from ubuntu.keyboards.main_menu import main_menu
from ubuntu.utils.formatting import escape_html, human_bytes, human_duration, pre

HELP_TEXT = """<b>hp-ungu — remote administration</b>

<b>Terminal</b>
/cmd &lt;argv…&gt; — run a command (safe argv mode)
/sh &lt;shell string&gt; — shell mode (gated)
/cwd — show working directory
/cd &lt;path&gt; — change working directory
/jobs — list jobs
/cancel &lt;job-id|all&gt; — cancel jobs

<b>Monitoring</b>
/status — Ubuntu + Android summary
/health — component health
/processes — Ubuntu processes
/logs [n] — bot log tail

<b>Android</b>
/battery — battery status
/device — device information
/network — Wi-Fi information
/storage — storage usage
/termuxapi — supported Termux:API features
/termux &lt;op&gt; [json] — call a bridge operation

<b>PM2</b>
/pm2 — native PM2 process list
/pm2logs &lt;name&gt; [n] — PM2 logs

<b>SSH</b>
/ssh — connection guide
/sshstatus — live SSH diagnostics

<b>Files</b>
/files — file manager

<b>Other</b>
/settings — per-admin settings
/help — this message
"""


async def h_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    user = update.effective_user
    text = (
        f"👋 Hello <b>{escape_html(user.first_name or 'admin')}</b>.\n\n"
        f"<b>hp-ungu</b> remote administration is online.\n"
        f"Working directory: <code>{escape_html(str(ctx.runner.get_cwd(user.id)))}</code>\n\n"
        "Choose a section below or send /help."
    )
    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=main_menu()
    )


async def h_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        HELP_TEXT, parse_mode=ParseMode.HTML, reply_markup=main_menu()
    )


async def h_health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    health = await ctx.monitor.bridge_health()
    ubuntu = await ctx.monitor.ubuntu_status()
    mem = ubuntu["memory"]
    disk = ubuntu["disk"]
    pm2 = await ctx.processes.pm2_list()
    jobs = ctx.jobs.active()

    bridge_line = "🟢 online" if health.get("online") else f"🔴 offline — {escape_html(health.get('error'))}"
    pm2_count = len(pm2.get("processes", [])) if pm2.get("available", True) else "Unavailable"

    lines = [
        "<b>🩺 Component health</b>",
        "",
        f"Ubuntu bot: 🟢 running ({escape_html(ubuntu['python'])})",
        f"Termux bridge: {bridge_line}",
        f"PM2 processes: {escape_html(pm2_count)}",
        f"Active jobs: {len(jobs)}",
        "",
        f"Memory used: {escape_html(human_bytes(mem['used']))} / "
        f"{escape_html(human_bytes(mem['total']))}",
        f"Disk used: {escape_html(human_bytes(disk['used']))} / "
        f"{escape_html(human_bytes(disk['total']))}",
        f"Uptime: {escape_html(human_duration(ubuntu['uptime']))}",
    ]
    if health.get("online"):
        lines.append(f"Bridge uptime: {escape_html(human_duration(health.get('uptime')))}")
    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=main_menu()
    )


def register(application: Application, ctx: AppContext) -> None:
    admin = ctx.settings.telegram_admin_ids
    from telegram.ext import filters

    user_filter = filters.User(list(admin))
    application.add_handler(CommandHandler("start", h_start, filters=user_filter))
    application.add_handler(CommandHandler("help", h_help, filters=user_filter))
    application.add_handler(CommandHandler("health", h_health, filters=user_filter))

    async def menu_cb(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(
            "Choose a section:", parse_mode=ParseMode.HTML, reply_markup=main_menu()
        )

    ctx.register_action("main", menu_cb)
    ctx.register_action("health", lambda u, c, x: h_health(u, c))
