"""Monitoring commands: /status /processes /logs /battery /device /network /storage."""
from __future__ import annotations

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from ubuntu.context import AppContext
from ubuntu.keyboards.main_menu import main_menu, with_back
from ubuntu.utils.formatting import (
    escape_html,
    human_bytes,
    human_duration,
    pre,
    progress_bar,
)

UNAVAILABLE = "Unavailable"


def _mem_line(mem: dict) -> str:
    if mem.get("total") is None:
        return "Memory: Unavailable"
    percent = mem.get("percent") or 0.0
    return (
        f"Memory: {human_bytes(mem['used'])} / {human_bytes(mem['total'])} "
        f"{progress_bar(percent)}"
    )


def _disk_line(disk: dict, label: str = "Disk") -> str:
    if disk.get("total") is None:
        return f"{label}: Unavailable"
    percent = disk.get("percent") or 0.0
    return (
        f"{label}: {human_bytes(disk['used'])} / {human_bytes(disk['total'])} "
        f"{progress_bar(percent)} (free {human_bytes(disk['free'])})"
    )


async def ubuntu_status_text(ctx: AppContext) -> str:
    status = await ctx.monitor.ubuntu_status()
    load = status["load"]
    load_text = "Unavailable" if load is None else f"{load[0]:.2f} {load[1]:.2f} {load[2]:.2f}"
    lines = [
        "🐧 <b>Ubuntu (PRoot)</b>",
        f"Distribution: {escape_html(status['distribution'])}",
        f"Kernel: {escape_html(status['kernel'])} ({escape_html(status['architecture'])})",
        f"Hostname: {escape_html(status['hostname'])}",
        f"Python: {escape_html(status['python'])}",
        f"Node.js: {escape_html(status['node'])}",
        f"CPUs: {escape_html(status['cpu_count'])}",
        f"Load average: {escape_html(load_text)}",
        _mem_line(status["memory"]),
        _disk_line(status["disk"], "Disk (working dir)"),
        f"Uptime: {escape_html(human_duration(status['uptime']))}",
        "",
        "ℹ️ Memory, load and uptime come from the Android host kernel. "
        "They are not isolated Ubuntu PRoot statistics.",
    ]
    return "\n".join(lines)


async def android_status_text(ctx: AppContext) -> str:
    data = await ctx.monitor.android_status()
    if not data.get("available"):
        return (
            "📱 <b>Android</b>\n\n"
            f"🔴 Bridge unavailable: {escape_html(data.get('error') or 'unknown error')}"
        )

    lines = ["📱 <b>Android (Termux bridge)</b>", ""]

    battery = data.get("battery") or {}
    if "error" in battery:
        lines.append(f"Battery: Unavailable ({escape_html(battery['error'])})")
    else:
        pct = battery.get("percentage", UNAVAILABLE)
        state = battery.get("status", UNAVAILABLE)
        temp = battery.get("temperature")
        plugged = battery.get("plugged", UNAVAILABLE)
        lines.append(f"Battery: {escape_html(pct)}% — {escape_html(state)}")
        lines.append(f"Plugged: {escape_html(plugged)}")
        if temp is not None:
            lines.append(f"Temperature: {escape_html(temp)} °C")

    device = data.get("device") or {}
    if "error" in device:
        lines.append(f"Device: Unavailable ({escape_html(device['error'])})")
    else:
        for key in ("model", "manufacturer", "android_version", "sdk"):
            if device.get(key):
                lines.append(f"{key.replace('_', ' ').title()}: {escape_html(device[key])}")

    wifi = data.get("wifi") or {}
    if "error" in wifi:
        lines.append(f"Wi-Fi: Unavailable ({escape_html(wifi['error'])})")
    elif wifi:
        ssid = wifi.get("ssid") or wifi.get("SSID") or UNAVAILABLE
        ip = wifi.get("ip") or wifi.get("ip_address") or UNAVAILABLE
        lines.append(f"Wi-Fi: {escape_html(ssid)} ({escape_html(ip)})")

    supported = [k for k, v in (data.get("features") or {}).items() if v]
    if supported:
        lines.append("")
        lines.append(f"Termux:API available: {escape_html(', '.join(sorted(supported)))}")
    return "\n".join(lines)


async def h_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    text = "\n\n".join([await ubuntu_status_text(ctx), await android_status_text(ctx)])
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=with_back([], "m:main")
        )
    else:
        await update.effective_message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=main_menu()
        )


async def _menu_reply(update: Update, text: str) -> None:
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=with_back([], "m:main")
        )
    else:
        await update.effective_message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=main_menu()
        )


async def h_ubuntu_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    if update.callback_query:
        await update.callback_query.answer()
    await _menu_reply(update, await ubuntu_status_text(ctx))


async def h_device_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    if update.callback_query:
        await update.callback_query.answer()
    await _menu_reply(update, await android_status_text(ctx))


async def h_battery(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    if update.callback_query:
        await update.callback_query.answer()
    try:
        data = await ctx.bridge.battery()
    except Exception as exc:  # noqa: BLE001
        await _menu_reply(update, f"🔋 <b>Battery</b>\n\n🔴 {escape_html(exc)}")
        return
    percent = data.get("percentage", UNAVAILABLE)
    status = data.get("status", UNAVAILABLE)
    health = data.get("health", UNAVAILABLE)
    plugged = data.get("plugged", UNAVAILABLE)
    temperature = data.get("temperature")
    current = data.get("current")
    lines = [
        "🔋 <b>Battery</b>",
        f"Level: {escape_html(percent)}% {progress_bar(percent if isinstance(percent, (int, float)) else 0)}",
        f"Status: {escape_html(status)}",
        f"Health: {escape_html(health)}",
        f"Plugged: {escape_html(plugged)}",
    ]
    if temperature is not None:
        lines.append(f"Temperature: {escape_html(temperature)} °C")
    if current is not None:
        lines.append(f"Current: {escape_html(current)} µA")
    await _menu_reply(update, "\n".join(lines))


async def h_storage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    if update.callback_query:
        await update.callback_query.answer()
    status = await ctx.monitor.ubuntu_status()
    lines = ["💾 <b>Storage</b>", ""]
    lines.append(_disk_line(status["disk"], f"Ubuntu {ctx.settings.default_working_directory}"))
    lines.append("")
    lines.append(
        "ℹ️ Android device-wide storage is not exposed by Termux:API reliably on "
        "all ROMs. Use <code>df -h</code> via <code>/cmd</code> inside Ubuntu for "
        "the full mount table."
    )
    await _menu_reply(update, "\n".join(lines))


async def h_network(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    if update.callback_query:
        await update.callback_query.answer()
    lines = ["🌐 <b>Network</b>", ""]
    try:
        wifi = await ctx.bridge.wifi_info()
        if wifi.get("error"):
            lines.append(f"Wi-Fi: Unavailable ({escape_html(wifi['error'])})")
        else:
            for key, value in wifi.items():
                if isinstance(value, (str, int, float, bool)):
                    lines.append(f"{escape_html(key)}: {escape_html(value)}")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"Wi-Fi: Unavailable ({escape_html(exc)})")

    lines.append("")
    ssh = await ctx.processes.ssh_status()
    if ssh.get("available") is False:
        lines.append(f"SSH: Unavailable ({escape_html(ssh.get('error'))})")
    else:
        lines.append(f"SSH port {ctx.settings.ssh_port}: "
                     f"{'listening' if ssh.get('port_open') else 'closed'}")
        lines.append(f"Tailscale: {escape_html(ssh.get('tailscale_status', UNAVAILABLE))}")
    await _menu_reply(update, "\n".join(lines))


async def h_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    args = context.args or []
    lines = 60
    if args:
        try:
            lines = max(5, min(400, int(args[0])))
        except ValueError:
            pass
    if update.callback_query:
        await update.callback_query.answer()
    tail = ctx.processes.bot_log_tail(lines)
    text = f"📜 <b>Bot log (last {lines} lines)</b>\n\n{pre(tail[-3500:])}"
    await _menu_reply(update, text)


def register(application: Application, ctx: AppContext) -> None:
    user_filter = filters.User(list(ctx.settings.telegram_admin_ids))
    application.add_handler(CommandHandler("status", h_status, filters=user_filter))
    application.add_handler(CommandHandler("battery", h_battery, filters=user_filter))
    application.add_handler(CommandHandler("device", h_device_menu, filters=user_filter))
    application.add_handler(CommandHandler("network", h_network, filters=user_filter))
    application.add_handler(CommandHandler("storage", h_storage, filters=user_filter))
    application.add_handler(CommandHandler("logs", h_logs, filters=user_filter))

    ctx.register_action("ubuntu", h_ubuntu_menu)
    ctx.register_action("device", h_device_menu)
    ctx.register_action("battery", h_battery)
    ctx.register_action("storage", h_storage)
    ctx.register_action("network", h_network)
    ctx.register_action("logs", h_logs)
