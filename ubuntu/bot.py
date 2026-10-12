"""Entry point for the hp-ungu Telegram bot (runs inside Ubuntu)."""
from __future__ import annotations

import asyncio
import signal
import sys

from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, ApplicationBuilder, Defaults

from ubuntu import __version__
from ubuntu.config import ConfigError, Settings, load_settings
from ubuntu.context import AppContext
from ubuntu.handlers import register_all
from ubuntu.services.command_runner import CommandRunner
from ubuntu.services.device_monitor import DeviceMonitor
from ubuntu.services.file_manager import FileManager
from ubuntu.services.job_manager import JobManager
from ubuntu.services.process_manager import ProcessManager
from ubuntu.services.state_store import StateStore
from ubuntu.services.termux_bridge_client import TermuxBridgeClient
from ubuntu.utils.formatting import escape_html
from ubuntu.utils.logging_config import get_logger, setup_logging

BOT_COMMANDS = [
    BotCommand("start", "Main menu"),
    BotCommand("help", "Command reference"),
    BotCommand("status", "Ubuntu + Android summary"),
    BotCommand("health", "Component health"),
    BotCommand("cmd", "Run a command (argv mode)"),
    BotCommand("sh", "Run a shell command (gated)"),
    BotCommand("cwd", "Show working directory"),
    BotCommand("cd", "Change working directory"),
    BotCommand("jobs", "List jobs"),
    BotCommand("cancel", "Cancel jobs"),
    BotCommand("processes", "Ubuntu processes"),
    BotCommand("pm2", "Native PM2 status"),
    BotCommand("pm2logs", "PM2 logs"),
    BotCommand("logs", "Bot log tail"),
    BotCommand("battery", "Battery status"),
    BotCommand("device", "Android device info"),
    BotCommand("network", "Network info"),
    BotCommand("storage", "Storage usage"),
    BotCommand("termuxapi", "Termux:API features"),
    BotCommand("termux", "Call a bridge operation"),
    BotCommand("files", "File manager"),
    BotCommand("ssh", "SSH connection guide"),
    BotCommand("sshstatus", "SSH diagnostics"),
    BotCommand("settings", "Per-admin settings"),
]


def build_context(settings: Settings) -> AppContext:
    state = StateStore(settings.data_dir / "state.json")
    jobs = JobManager(max_concurrent=settings.max_concurrent_jobs)
    runner = CommandRunner(settings, state)
    bridge = TermuxBridgeClient(settings)
    monitor = DeviceMonitor(settings, bridge)
    files = FileManager(settings)
    processes = ProcessManager(settings, bridge)
    return AppContext(
        settings=settings,
        state=state,
        jobs=jobs,
        runner=runner,
        monitor=monitor,
        files=files,
        processes=processes,
        bridge=bridge,
    )


async def _post_init(application: Application) -> None:
    await application.bot.set_my_commands(BOT_COMMANDS)
    try:
        await application.bot.set_my_description(
            "hp-ungu — private remote administration for an Android phone."
        )
    except TelegramError:  # pragma: no cover - optional API call
        pass


async def _error_handler(update: object, context) -> None:
    log = get_logger("ubuntu.bot")
    log.exception("Unhandled exception while processing update", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message is not None:
        try:
            await update.effective_message.reply_text(
                f"❌ Internal error: {escape_html(type(context.error).__name__)}: "
                f"{escape_html(context.error)}",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError:
            pass


async def run() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"[hp-ungu] Configuration error: {exc}", file=sys.stderr)
        return 2

    setup_logging(settings.log_level, settings.log_file)
    log = get_logger("ubuntu.bot")
    log.info("Starting hp-ungu %s", __version__)
    log.info("Bridge target: %s", settings.bridge_base_url)
    log.info("Allowed file roots: %s", ", ".join(str(r) for r in settings.allowed_file_roots))
    log.info("Shell master switch: %s", settings.enable_unrestricted_shell)

    ctx = build_context(settings)

    defaults = Defaults(parse_mode=ParseMode.HTML, tzinfo=None)
    application = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .defaults(defaults)
        .post_init(_post_init)
        .concurrent_updates(True)
        .build()
    )
    application.bot_data["ctx"] = ctx
    application.add_error_handler(_error_handler)

    registered = register_all(application, ctx)
    log.info("Registered handler modules: %s", ", ".join(registered))

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):  # pragma: no cover
            pass

    await application.initialize()
    await application.start()
    await application.updater.start_polling(
        allowed_updates=Update.ALL_TYPES, drop_pending_updates=False
    )
    log.info("Bot is polling. Press Ctrl+C to stop.")

    try:
        await stop_event.wait()
    finally:
        log.info("Shutting down…")
        await ctx.jobs.shutdown()
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
        await ctx.bridge.aclose()
    return 0


def main() -> None:
    try:
        raise SystemExit(asyncio.run(run()))
    except KeyboardInterrupt:  # pragma: no cover
        raise SystemExit(0)


if __name__ == "__main__":
    main()
