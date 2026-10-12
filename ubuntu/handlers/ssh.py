"""SSH connection information and diagnostics."""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, filters

from ubuntu.context import AppContext
from ubuntu.keyboards.main_menu import with_back
from ubuntu.utils.formatting import escape_html, pre

UNAVAILABLE = "Unavailable"


def _ssh_guide(ctx: AppContext, runtime: dict) -> str:
    s = ctx.settings
    host = s.ssh_hostname or UNAVAILABLE
    ip = s.tailscale_ip or UNAVAILABLE
    user = s.ssh_username or UNAVAILABLE
    port = s.ssh_port

    observed_ip = runtime.get("tailscale_ip") or ip
    observed_host = runtime.get("hostname") or host

    lines = [
        "🔐 <b>SSH connection guide</b>",
        "",
        f"Device name: <code>{escape_html(runtime.get('device_name') or 'hp-ungu')}</code>",
        f"Hostname: <code>{escape_html(observed_host)}</code>",
        f"Tailscale IP: <code>{escape_html(observed_ip)}</code>",
        f"Username: <code>{escape_html(user)}</code>",
        f"Port: <code>{escape_html(port)}</code> (not 22)",
        "",
        "<b>Windows PowerShell / CMD</b>",
        pre(f"ssh -p {port} {user}@{observed_host}"),
        pre(f"ssh -p {port} {user}@{observed_ip}"),
        "<b>Copy a file from the phone</b>",
        pre(f"scp -P {port} {user}@{observed_host}:/path/on/phone ./"),
        "<b>SSH config entry (~/.ssh/config)</b>",
        pre(
            f"Host hp-ungu\n"
            f"    HostName {observed_host}\n"
            f"    User {user}\n"
            f"    Port {port}\n"
            f"    ServerAliveInterval 30"
        ),
        "<b>Notes</b>",
        "• Termux sshd listens on 8022, not the default 22.",
        "• The hostname only resolves when Tailscale is up on Windows.",
        "• If DNS fails, use the 100.x.y.z IP form.",
        "• Passwords and private keys are never printed here.",
    ]
    return "\n".join(lines)


def _status_text(ctx: AppContext, status: dict) -> str:
    s = ctx.settings
    if status.get("available") is False:
        return (
            "🔎 <b>SSH status</b>\n\n"
            f"🔴 Bridge unavailable: {escape_html(status.get('error'))}\n\n"
            "<b>Configured (unverified)</b>\n"
            + pre(
                f"hostname: {s.ssh_hostname}\n"
                f"port: {s.ssh_port}\n"
                f"username: {s.ssh_username}\n"
                f"tailscale_ip: {s.tailscale_ip}"
            )
        )

    observed_port = status.get("configured_port", s.ssh_port)
    port_open = status.get("port_open")
    daemon = status.get("sshd_running")
    pids = status.get("sshd_pids") or []

    lines = [
        "🔎 <b>SSH status</b>",
        "",
        f"sshd process: {'🟢 running' if daemon else '🔴 not detected'}"
        + (f" (pids {escape_html(', '.join(map(str, pids)))})" if pids else ""),
        f"Configured port: <code>{escape_html(observed_port)}</code>",
        f"127.0.0.1:{escape_html(observed_port)} connect: "
        f"{'🟢 open' if port_open else '🔴 refused'}",
        f"PM2 sshd entry: {escape_html(status.get('pm2_sshd_status') or UNAVAILABLE)}",
        "",
        "<b>Tailscale</b>",
        f"Client: {escape_html(status.get('tailscale_status') or UNAVAILABLE)}",
        f"IPv4: <code>{escape_html(status.get('tailscale_ip') or s.tailscale_ip or UNAVAILABLE)}</code>",
        f"Hostname: <code>{escape_html(status.get('hostname') or s.ssh_hostname or UNAVAILABLE)}</code>",
        "",
        "<b>Interpretation</b>",
    ]
    if not daemon:
        lines.append("• sshd is not detected. Restart it manually: <code>pm2 restart sshd</code>.")
    if not port_open:
        lines.append(
            "• Nothing is listening on the loopback port. Check for a port mismatch "
            "or a crashed sshd."
        )
    if status.get("tailscale_status") in (None, UNAVAILABLE, "not-installed"):
        lines.append(
            "• The Tailscale CLI is not visible from Termux; the Android app may still "
            "be routing traffic. Verify from Windows with <code>tailscale status</code>."
        )
    lines.append("• The bot never restarts or kills sshd automatically.")
    return "\n".join(lines)


async def h_ssh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    if update.callback_query:
        await update.callback_query.answer()
    status = await ctx.processes.ssh_status()
    text = _ssh_guide(ctx, status if isinstance(status, dict) else {})
    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔎 Live SSH status", callback_data="m:sshstatus")],
            [InlineKeyboardButton("⬅️ Back", callback_data="m:main")],
        ]
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup
        )
    else:
        await update.effective_message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup
        )


async def h_sshstatus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    if update.callback_query:
        await update.callback_query.answer()
    status = await ctx.processes.ssh_status()
    text = _status_text(ctx, status if isinstance(status, dict) else {})
    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔐 Connection guide", callback_data="m:ssh")],
            [InlineKeyboardButton("⬅️ Back", callback_data="m:main")],
        ]
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup
        )
    else:
        await update.effective_message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup
        )


def register(application: Application, ctx: AppContext) -> None:
    user_filter = filters.User(list(ctx.settings.telegram_admin_ids))
    application.add_handler(CommandHandler("ssh", h_ssh, filters=user_filter))
    application.add_handler(CommandHandler("sshstatus", h_sshstatus, filters=user_filter))

    ctx.register_action("ssh", h_ssh)
    ctx.register_action("sshstatus", h_sshstatus)
