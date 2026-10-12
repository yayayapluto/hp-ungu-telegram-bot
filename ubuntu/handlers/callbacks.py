"""Generic callback routing, confirmations, settings and unauthorized fallback."""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ubuntu.context import AppContext
from ubuntu.keyboards.main_menu import main_menu, settings_keyboard, with_back
from ubuntu.utils.formatting import escape_html, pre
from ubuntu.utils.security import is_admin

UNAUTHORIZED_TEXT = (
    "🚫 You are not authorized to use this bot.\n"
    "If you believe this is an error, add your numeric Telegram user ID to "
    "<code>TELEGRAM_ADMIN_IDS</code> in <code>.env</code>."
)


# ------------------------------------------------------------------- menu
async def cb_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    query = update.callback_query
    key = query.data.split(":", 1)[1]
    handler = ctx.menu_actions.get(key)
    if handler is None:
        await query.answer("This section is unavailable.", show_alert=True)
        return
    try:
        await handler(update, context, ctx)
    except Exception as exc:  # noqa: BLE001
        await query.answer("Action failed.", show_alert=False)
        try:
            await query.edit_message_text(
                f"❌ {escape_html(exc)}", parse_mode=ParseMode.HTML,
                reply_markup=with_back([], "m:main"),
            )
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------- confirmations
async def cb_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    if len(parts) != 3:
        await query.edit_message_text("⚠️ Malformed confirmation.")
        return
    _, token, decision = parts
    payload = ctx.state.pop_pending(update.effective_user.id, token)
    if payload is None:
        await query.edit_message_text(
            "⚠️ This confirmation expired or was already used.",
            reply_markup=with_back([], "m:main"),
        )
        return

    if decision != "y":
        await query.edit_message_text("❌ Cancelled.", reply_markup=with_back([], "m:main"))
        return

    action = payload.get("action")
    try:
        if action == "delete":
            await _do_delete(ctx, query, payload)
        elif action == "overwrite":
            await _do_overwrite(ctx, query, payload)
        elif action == "termux_op":
            from ubuntu.handlers.termux_api import _run_op

            await query.edit_message_text(
                f"▶️ Running <code>{escape_html(payload['op'])}</code>…",
                parse_mode=ParseMode.HTML,
            )
            await _run_op(update, ctx, payload["op"], {})
        elif action == "pm2":
            data = await ctx.processes.pm2_action(payload["pm2_action"], payload["name"])
            await query.edit_message_text(
                f"✅ PM2 {escape_html(payload['pm2_action'])} "
                f"{escape_html(payload['name'])}\n\n{pre(str(data)[:1500])}",
                parse_mode=ParseMode.HTML,
                reply_markup=with_back([], "m:main"),
            )
        else:
            await query.edit_message_text("⚠️ Unknown confirmed action.",
                                          reply_markup=with_back([], "m:main"))
    except Exception as exc:  # noqa: BLE001
        await query.edit_message_text(
            f"❌ {escape_html(exc)}", parse_mode=ParseMode.HTML,
            reply_markup=with_back([], "m:main"),
        )


async def _do_delete(ctx: AppContext, query, payload: dict) -> None:
    import asyncio

    info = await asyncio.to_thread(ctx.files.delete, payload["path"], True)
    await query.edit_message_text(
        f"🗑 Removed <code>{escape_html(info['path'])}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=with_back([], "m:main"),
    )


async def _do_overwrite(ctx: AppContext, query, payload: dict) -> None:
    import asyncio

    info = await asyncio.to_thread(
        ctx.files.write_bytes, payload["path"], payload["data"], True
    )
    await query.edit_message_text(
        f"✅ Overwrote <code>{escape_html(info['path'])}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=with_back([], "m:main"),
    )


# -------------------------------------------------------------- settings
def _settings_text(ctx: AppContext, admin_id: int) -> str:
    master = ctx.settings.enable_unrestricted_shell
    per_admin = bool(ctx.state.get(admin_id, "shell_enabled", False))
    cwd = ctx.runner.get_cwd(admin_id)
    return "\n".join(
        [
            "🛠 <b>Settings</b>",
            "",
            f"Working directory: <code>{escape_html(str(cwd))}</code>",
            f"Shell master switch (.env): {'🟢 enabled' if master else '🔴 disabled'}",
            f"Shell per-admin switch: {'🟢 enabled' if per_admin else '🔴 disabled'}",
            f"Command timeout: {ctx.settings.command_timeout_seconds}s",
            f"Max concurrent jobs: {ctx.settings.max_concurrent_jobs}",
            f"Max output bytes: {ctx.settings.max_output_bytes}",
            f"Allowed file roots: <code>{escape_html(':'.join(str(r) for r in ctx.settings.allowed_file_roots))}</code>",
            f"Bridge: <code>{escape_html(ctx.settings.bridge_base_url)}</code>",
        ]
    )


async def h_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    if update.callback_query:
        await update.callback_query.answer()
    text = _settings_text(ctx, update.effective_user.id)
    markup = settings_keyboard(
        bool(ctx.state.get(update.effective_user.id, "shell_enabled", False)),
        ctx.settings.enable_unrestricted_shell,
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup
        )
    else:
        await update.effective_message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup
        )


async def cb_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    query = update.callback_query
    action = query.data.split(":", 1)[1]
    admin_id = update.effective_user.id

    if action == "toggleshell":
        if not ctx.settings.enable_unrestricted_shell:
            await query.answer(
                "Shell is disabled globally. Set ENABLE_UNRESTRICTED_SHELL=true in .env.",
                show_alert=True,
            )
            return
        new_value = ctx.state.toggle(admin_id, "shell_enabled", False)
        await query.answer(f"Shell mode {'enabled' if new_value else 'disabled'}.")
    elif action == "resetcwd":
        ctx.runner.reset_cwd(admin_id)
        await query.answer("Working directory reset.")
    elif action == "testbridge":
        health = await ctx.monitor.bridge_health()
        await query.answer(
            "Bridge online" if health.get("online") else f"Bridge offline: {health.get('error')}",
            show_alert=True,
        )
        return
    elif action == "selfcheck":
        await query.answer("Running self-check…")
        ubuntu = await ctx.monitor.ubuntu_status()
        health = await ctx.monitor.bridge_health()
        pm2 = await ctx.processes.pm2_list()
        ssh = await ctx.processes.ssh_status()
        text = "\n".join(
            [
                "🧪 <b>Self-check</b>",
                "",
                f"Ubuntu: {escape_html(ubuntu['distribution'])}",
                f"Python: {escape_html(ubuntu['python'])}",
                f"Bridge: {'🟢 online' if health.get('online') else '🔴 ' + str(health.get('error'))}",
                f"PM2 entries: {len(pm2.get('processes', [])) if pm2.get('available', True) else 'Unavailable'}",
                f"SSH port open: {'yes' if ssh.get('port_open') else 'no'}",
                f"Active jobs: {len(ctx.jobs.active())}",
            ]
        )
        await query.edit_message_text(
            text, parse_mode=ParseMode.HTML,
            reply_markup=settings_keyboard(
                bool(ctx.state.get(admin_id, "shell_enabled", False)),
                ctx.settings.enable_unrestricted_shell,
            ),
        )
        return
    elif action == "pm2restart":
        ctx.state.set(admin_id, "pm2_restart_requested", True)
        await query.answer("Use /pm2 and confirm from the PM2 section.")
        return
    await h_settings(update, context)


async def cb_noop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()


# --------------------------------------------------------- unauthorized
async def h_unauthorized_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.effective_message.reply_text(
        UNAUTHORIZED_TEXT + f"\n\nYour ID: <code>{user.id}</code>",
        parse_mode=ParseMode.HTML,
    )


async def h_unauthorized_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer("Not authorized.", show_alert=True)


def register(application: Application, ctx: AppContext) -> None:
    admin_ids = list(ctx.settings.telegram_admin_ids)
    admin_filter = filters.User(admin_ids)

    application.add_handler(CommandHandler("settings", h_settings, filters=admin_filter))
    application.add_handler(CallbackQueryHandler(cb_menu, pattern=r"^m:[a-z_]+$"))
    application.add_handler(CallbackQueryHandler(cb_confirm, pattern=r"^cf:[A-Za-z0-9_\-]+:[yn]$"))
    application.add_handler(CallbackQueryHandler(cb_settings, pattern=r"^s:[a-z]+$"))
    application.add_handler(CallbackQueryHandler(cb_noop, pattern=r"^noop$"))

    # Fallback group: only reached when no admin-filtered handler matched.
    application.add_handler(
        MessageHandler(filters.ALL, h_unauthorized_message), group=90
    )
    application.add_handler(
        CallbackQueryHandler(h_unauthorized_callback), group=90
    )

    ctx.register_action("settings", h_settings)
