"""Processes, PM2 and /pm2logs."""
from __future__ import annotations

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from ubuntu.context import AppContext
from ubuntu.keyboards.main_menu import main_menu, with_back
from ubuntu.utils.formatting import escape_html, pre, shorten

UNAVAILABLE = "Unavailable"


async def ubuntu_processes_text(ctx: AppContext) -> str:
    processes = await ctx.processes.ubuntu_processes(limit=25)
    if not processes:
        return "⚙️ <b>Ubuntu processes</b>\n\nUnavailable (ps produced no output)."
    lines = ["⚙️ <b>Ubuntu processes</b> (top 25 by CPU)", ""]
    for proc in processes:
        lines.append(
            f"<code>{escape_html(proc['pid']):>7}</code> "
            f"cpu {escape_html(proc['cpu']):>5} "
            f"mem {escape_html(proc['mem']):>5} "
            f"{escape_html(proc['etime']):>10} "
            f"{escape_html(shorten(proc['comm'], 20))}"
        )
    return "\n".join(lines)


async def pm2_text(ctx: AppContext) -> str:
    data = await ctx.processes.pm2_list()
    if not data.get("available", True):
        return f"📦 <b>PM2</b>\n\n🔴 Unavailable: {escape_html(data.get('error'))}"
    processes = data.get("processes") or []
    if not processes:
        return "📦 <b>PM2</b>\n\nNo PM2 processes reported."
    lines = ["📦 <b>Native Termux PM2</b>", ""]
    for proc in processes:
        icon = "🟢" if proc.get("status") == "online" else "🔴"
        lines.append(
            f"{icon} <b>{escape_html(proc.get('name'))}</b> "
            f"(id {escape_html(proc.get('pm_id'))}, pid {escape_html(proc.get('pid'))})"
        )
        lines.append(
            f"    status {escape_html(proc.get('status'))} · "
            f"cpu {escape_html(proc.get('cpu'))}% · "
            f"mem {escape_html(proc.get('memory'))} · "
            f"restarts {escape_html(proc.get('restarts'))}"
        )
    return "\n".join(lines)


async def _reply(update: Update, text: str) -> None:
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=with_back([], "m:main")
        )
    else:
        await update.effective_message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=main_menu()
        )


async def h_processes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    if update.callback_query:
        await update.callback_query.answer()
    await _reply(update, await ubuntu_processes_text(ctx))


async def h_pm2(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    if update.callback_query:
        await update.callback_query.answer()
    await _reply(update, await pm2_text(ctx))


async def h_pm2logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "Usage: <code>/pm2logs &lt;name&gt; [lines]</code>", parse_mode=ParseMode.HTML
        )
        return
    name = args[0]
    lines = 40
    if len(args) > 1:
        try:
            lines = max(5, min(300, int(args[1])))
        except ValueError:
            pass
    try:
        data = await ctx.processes.pm2_logs(name, lines)
    except Exception as exc:  # noqa: BLE001
        await update.effective_message.reply_text(
            f"❌ {escape_html(exc)}", parse_mode=ParseMode.HTML
        )
        return
    output = (data.get("output") or "").strip() or "(no output)"
    await update.effective_message.reply_text(
        f"📜 <b>PM2 logs — {escape_html(name)}</b>\n\n{pre(output[-3500:])}",
        parse_mode=ParseMode.HTML,
    )


def register(application: Application, ctx: AppContext) -> None:
    user_filter = filters.User(list(ctx.settings.telegram_admin_ids))
    application.add_handler(CommandHandler("processes", h_processes, filters=user_filter))
    application.add_handler(CommandHandler("pm2", h_pm2, filters=user_filter))
    application.add_handler(CommandHandler("pm2logs", h_pm2logs, filters=user_filter))

    ctx.register_action("processes", h_processes)
    ctx.register_action("pm2", h_pm2)
