"""Remote terminal: /cmd /sh /cwd /cd /jobs /cancel."""
from __future__ import annotations

import asyncio
import io
import time
from typing import Optional

from telegram import InputFile, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, filters

from ubuntu.context import AppContext
from ubuntu.keyboards.main_menu import terminal_keyboard, with_back
from ubuntu.services.job_manager import (
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_TIMEOUT,
    JobRecord,
)
from ubuntu.utils.formatting import (
    escape_html,
    human_duration,
    pre,
    shorten,
    truncate,
)

INLINE_LIMIT = 3500

STATUS_ICONS = {
    STATUS_QUEUED: "⏳",
    STATUS_RUNNING: "▶️",
    STATUS_DONE: "✅",
    STATUS_FAILED: "❌",
    STATUS_TIMEOUT: "⏰",
    STATUS_CANCELLED: "🚫",
}


def _progress_text(job: JobRecord) -> str:
    icon = STATUS_ICONS.get(job.status, "•")
    lines = [
        f"{icon} <b>Job</b> <code>{escape_html(job.short())}</code> — {escape_html(job.status)}",
        f"Command: {pre(shorten(job.command, 200))}",
        f"cwd: {escape_html(job.cwd)}",
        f"Elapsed: {escape_html(human_duration(job.duration))}",
    ]
    if job.status == STATUS_QUEUED:
        lines.append("Waiting for a free concurrency slot…")
    else:
        tail = truncate(job.stdout.strip()[-600:], 600, "\n…")
        if tail:
            lines.append(pre(tail))
    return "\n".join(lines)


def _result_text(job: JobRecord) -> str:
    icon = STATUS_ICONS.get(job.status, "•")
    header = [
        f"{icon} <b>{escape_html(job.status.upper())}</b> — job <code>{escape_html(job.short())}</code>",
        f"Command: {pre(shorten(job.command, 300))}",
        f"cwd: {escape_html(job.cwd)}",
        f"Exit code: {escape_html(job.exit_code if job.exit_code is not None else 'n/a')}",
        f"Duration: {escape_html(human_duration(job.duration))}",
    ]
    if job.note:
        header.append(f"Note: {escape_html(job.note)}")
    body: list[str] = []
    if job.stdout.strip():
        body.append("<b>stdout</b>\n" + pre(job.stdout))
    if job.stderr.strip():
        body.append("<b>stderr</b>\n" + pre(job.stderr))
    if job.truncated:
        body.append("⚠️ Output was truncated by MAX_OUTPUT_BYTES.")
    if not body:
        body.append("<i>(no output)</i>")
    return "\n".join(header) + "\n\n" + "\n\n".join(body)


async def _watch_progress(message, job: JobRecord, interval: int) -> None:
    while job.is_active:
        await asyncio.sleep(interval)
        if not job.is_active:
            break
        try:
            await message.edit_text(
                _progress_text(job), parse_mode=ParseMode.HTML,
                reply_markup=with_back([], "m:terminal"),
            )
        except BadRequest:
            pass
        except Exception:  # noqa: BLE001 - progress is best effort
            return


async def start_job(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    command: str,
    shell_mode: bool,
) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    user = update.effective_user
    chat = update.effective_chat

    if shell_mode and not ctx.settings.enable_unrestricted_shell:
        await update.effective_message.reply_text(
            "🚫 Shell mode is disabled by <code>ENABLE_UNRESTRICTED_SHELL=false</code> "
            "in <code>.env</code>.",
            parse_mode=ParseMode.HTML,
        )
        return

    if shell_mode and not bool(ctx.state.get(user.id, "shell_enabled", False)):
        await update.effective_message.reply_text(
            "🚫 Shell mode is disabled for your account. Enable it in /settings.",
        )
        return

    cwd = ctx.runner.get_cwd(user.id)
    job = ctx.jobs.new_job(
        admin_id=user.id,
        chat_id=chat.id,
        command=command,
        shell_mode=shell_mode,
        cwd=str(cwd),
    )
    message = await update.effective_message.reply_text(
        _progress_text(job), parse_mode=ParseMode.HTML
    )

    async def worker(record: JobRecord) -> None:
        await ctx.runner.execute(record)

    ctx.jobs.spawn(job, worker)
    watcher = asyncio.create_task(
        _watch_progress(message, job, ctx.settings.progress_interval_seconds)
    )

    try:
        if job.task is not None:
            await job.task
    finally:
        watcher.cancel()
        try:
            await watcher
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

    await _deliver_result(message, job, ctx)


async def _deliver_result(message, job: JobRecord, ctx: AppContext) -> None:
    text = _result_text(job)
    if len(text) <= INLINE_LIMIT:
        try:
            await message.edit_text(
                text, parse_mode=ParseMode.HTML, reply_markup=with_back([], "m:terminal")
            )
        except BadRequest:
            await message.edit_text(
                truncate(text, INLINE_LIMIT), parse_mode=ParseMode.HTML
            )
        return

    summary = _result_text(
        JobRecord(
            job_id=job.job_id,
            admin_id=job.admin_id,
            chat_id=job.chat_id,
            command=job.command,
            shell_mode=job.shell_mode,
            cwd=job.cwd,
            status=job.status,
            started_at=job.started_at,
            finished_at=job.finished_at,
            exit_code=job.exit_code,
            truncated=job.truncated,
            note=job.note,
        )
    )
    try:
        await message.edit_text(
            summary + "\n\n📎 Full output attached below.",
            parse_mode=ParseMode.HTML,
            reply_markup=with_back([], "m:terminal"),
        )
    except BadRequest:
        pass

    buffer = io.BytesIO()
    buffer.write(f"$ {job.command}\n".encode())
    buffer.write(f"cwd: {job.cwd}\nexit: {job.exit_code}\n".encode())
    buffer.write(b"--- stdout ---\n")
    buffer.write(job.stdout.encode("utf-8", "replace"))
    buffer.write(b"\n--- stderr ---\n")
    buffer.write(job.stderr.encode("utf-8", "replace"))
    buffer.seek(0)
    await message.reply_document(
        InputFile(buffer, filename=f"job-{job.short()}.txt"),
        caption=f"Output of job {job.short()}",
    )


# ---------------------------------------------------------------- commands
async def h_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "Usage: <code>/cmd &lt;command&gt; [args…]</code>\n"
            "Example: <code>/cmd ls -la</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    await start_job(update, context, " ".join(args), shell_mode=False)


async def h_sh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw = update.effective_message.text or ""
    command = raw.partition(" ")[2].strip()
    if not command:
        await update.effective_message.reply_text(
            "Usage: <code>/sh &lt;shell command&gt;</code>\n"
            "Example: <code>/sh ps aux | head -20</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    await start_job(update, context, command, shell_mode=True)


async def h_cwd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    cwd = ctx.runner.get_cwd(update.effective_user.id)
    await update.effective_message.reply_text(
        f"📂 Working directory:\n<code>{escape_html(str(cwd))}</code>",
        parse_mode=ParseMode.HTML,
    )


async def h_cd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    args = context.args or []
    if not args:
        await update.effective_message.reply_text("Usage: <code>/cd &lt;path&gt;</code>",
                                                  parse_mode=ParseMode.HTML)
        return
    target = " ".join(args)
    try:
        new_cwd = ctx.runner.set_cwd(update.effective_user.id, target)
    except (FileNotFoundError, NotADirectoryError, OSError) as exc:
        await update.effective_message.reply_text(f"❌ {escape_html(exc)}",
                                                  parse_mode=ParseMode.HTML)
        return
    await update.effective_message.reply_text(
        f"📂 <code>{escape_html(str(new_cwd))}</code>", parse_mode=ParseMode.HTML
    )


def _jobs_text(ctx: AppContext, admin_id: int) -> str:
    jobs = ctx.jobs.list(admin_id)
    if not jobs:
        return "No jobs recorded yet."
    lines = ["<b>Recent jobs</b>", ""]
    for job in reversed(jobs[-20:]):
        icon = STATUS_ICONS.get(job.status, "•")
        lines.append(
            f"{icon} <code>{escape_html(job.short())}</code> "
            f"{escape_html(shorten(job.command, 60))} "
            f"— {escape_html(job.status)} ({escape_html(human_duration(job.duration))})"
        )
    return "\n".join(lines)


async def h_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    text = _jobs_text(ctx, update.effective_user.id)
    markup = with_back([], "m:main")
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup
        )
    else:
        await update.effective_message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup
        )


async def h_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    args = context.args or []
    admin_id = update.effective_user.id

    if not args or args[0].lower() == "all":
        cancelled = await ctx.jobs.cancel_all(admin_id)
        await update.effective_message.reply_text(f"🚫 Cancelled {cancelled} job(s).")
        return

    job = ctx.jobs.find(args[0])
    if job is None or job.admin_id != admin_id:
        await update.effective_message.reply_text("❌ No matching job for this account.")
        return
    if await ctx.jobs.cancel(job):
        await update.effective_message.reply_text(
            f"🚫 Job <code>{escape_html(job.short())}</code> cancelled.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.effective_message.reply_text("ℹ️ That job already finished.")


# --------------------------------------------------------------- callbacks
QUICK_COMMANDS = {
    "t:pwd": ("pwd", False),
    "t:ls": ("ls -la", False),
    "t:df": ("df -h", False),
    "t:free": ("free -h", False),
    "t:pyver": ("python3 --version", False),
    "t:nodever": ("node --version", False),
}


async def h_terminal_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    cwd = ctx.runner.get_cwd(update.effective_user.id)
    await update.callback_query.edit_message_text(
        f"🖥 <b>Terminal</b>\n\ncwd: <code>{escape_html(str(cwd))}</code>\n\n"
        "Send <code>/cmd &lt;argv…&gt;</code> or <code>/sh &lt;shell&gt;</code>, "
        "or use a shortcut:",
        parse_mode=ParseMode.HTML,
        reply_markup=terminal_keyboard(),
    )


async def h_quick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    entry = QUICK_COMMANDS.get(query.data)
    if entry is None:
        await query.edit_message_text("⚠️ Unknown shortcut.", reply_markup=terminal_keyboard())
        return
    command, shell_mode = entry
    await query.edit_message_text(
        f"▶️ Running <code>{escape_html(command)}</code>…", parse_mode=ParseMode.HTML
    )
    await start_job(update, context, command, shell_mode=shell_mode)


async def h_cancel_all_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    await update.callback_query.answer()
    cancelled = await ctx.jobs.cancel_all(update.effective_user.id)
    await update.callback_query.edit_message_text(
        f"🚫 Cancelled {cancelled} job(s).", reply_markup=terminal_keyboard()
    )


def register(application: Application, ctx: AppContext) -> None:
    user_filter = filters.User(list(ctx.settings.telegram_admin_ids))
    application.add_handler(CommandHandler("cmd", h_cmd, filters=user_filter))
    application.add_handler(CommandHandler("sh", h_sh, filters=user_filter))
    application.add_handler(CommandHandler("cwd", h_cwd, filters=user_filter))
    application.add_handler(CommandHandler("cd", h_cd, filters=user_filter))
    application.add_handler(CommandHandler("jobs", h_jobs, filters=user_filter))
    application.add_handler(CommandHandler("cancel", h_cancel, filters=user_filter))

    application.add_handler(
        CallbackQueryHandler(h_quick, pattern=r"^t:(pwd|ls|df|free|pyver|nodever)$")
    )
    application.add_handler(
        CallbackQueryHandler(h_cancel_all_cb, pattern=r"^t:cancelall$")
    )

    ctx.register_action("terminal", h_terminal_menu)
    ctx.register_action("jobs", lambda u, c, x: h_jobs(u, c))
