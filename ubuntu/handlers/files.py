"""Administrator file manager."""
from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Any, Dict, List, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ubuntu.context import AppContext
from ubuntu.keyboards.main_menu import confirm_keyboard, pagination, with_back
from ubuntu.utils.formatting import escape_html, human_bytes, pre, shorten
from ubuntu.utils.security import PathSecurityError

PAGE_SIZE = 12
MAX_INLINE_READ = 3000


def _keyboard(listing: Dict[str, Any], page: int) -> InlineKeyboardMarkup:
    entries: List[Dict[str, Any]] = listing["entries"]
    total_pages = max(1, (len(entries) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    slice_ = entries[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

    rows: List[List[InlineKeyboardButton]] = []
    for offset, entry in enumerate(slice_):
        index = page * PAGE_SIZE + offset
        icon = "📁" if entry["is_dir"] else "📄"
        label = f"{icon} {shorten(entry['name'], 40)}"
        rows.append([InlineKeyboardButton(label, callback_data=f"f:o:{index}")])

    nav = pagination(page, total_pages, "f:p")
    if nav:
        rows.append(nav)
    rows.append(
        [
            InlineKeyboardButton("⬆️ Parent", callback_data="f:up"),
            InlineKeyboardButton("🏠 Root", callback_data="f:root"),
        ]
    )
    rows.append([InlineKeyboardButton("🔎 Search", callback_data="f:search")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="m:main")])
    return InlineKeyboardMarkup(rows)


def _header(path: str, entries: List[Dict[str, Any]], page: int) -> str:
    total_pages = max(1, (len(entries) + PAGE_SIZE - 1) // PAGE_SIZE)
    return (
        f"📁 <b>File Manager</b>\n\n<code>{escape_html(path)}</code>\n"
        f"{len(entries)} entries — page {page + 1}/{total_pages}"
    )


async def show_directory(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         path: Optional[str] = None, page: int = 0) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    admin_id = update.effective_user.id

    if path is None:
        listing = ctx.state.get_listing(admin_id)
        path = listing["path"] if listing else str(ctx.files.default_root())

    try:
        entries = [e.to_dict() for e in ctx.files.list_dir(path)]
    except (PathSecurityError, PermissionError, NotADirectoryError, FileNotFoundError) as exc:
        text = f"❌ {escape_html(exc)}"
        if update.callback_query:
            await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML,
                                                          reply_markup=with_back([], "m:main"))
        else:
            await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)
        return

    ctx.state.set_listing(admin_id, path, entries)
    text = _header(path, entries, page)
    markup = _keyboard({"entries": entries}, page)

    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML,
                                                      reply_markup=markup)
    else:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML,
                                                  reply_markup=markup)


async def h_files(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    path = " ".join(args) if args else None
    await show_directory(update, context, path)


async def cb_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    query = update.callback_query
    await query.answer()
    listing = ctx.state.get_listing(update.effective_user.id)
    if not listing:
        await query.edit_message_text("⚠️ Listing expired. Send /files again.",
                                      reply_markup=with_back([], "m:main"))
        return
    try:
        index = int(query.data.rsplit(":", 1)[1])
        entry = listing["entries"][index]
    except (ValueError, IndexError):
        await query.edit_message_text("⚠️ Stale entry. Send /files again.",
                                      reply_markup=with_back([], "m:main"))
        return

    if entry["is_dir"]:
        await show_directory(update, context, entry["path"], 0)
        return
    await _show_file(update, ctx, entry["path"])


async def _show_file(update: Update, ctx: AppContext, path: str) -> None:
    query = update.callback_query
    try:
        info = ctx.files.stat(path)
    except Exception as exc:  # noqa: BLE001
        await query.edit_message_text(f"❌ {escape_html(exc)}", parse_mode=ParseMode.HTML)
        return

    rows = [
        [InlineKeyboardButton("⬇️ Download", callback_data="f:dl")],
        [InlineKeyboardButton("🗑 Delete", callback_data="f:del")],
        [InlineKeyboardButton("✏️ Rename", callback_data="f:ren")],
        [InlineKeyboardButton("⬅️ Back to listing", callback_data="f:back")],
    ]
    ctx.state.set(update.effective_user.id, "selected", path)
    text = (
        f"📄 <b>{escape_html(info['name'])}</b>\n\n"
        f"Path: <code>{escape_html(info['path'])}</code>\n"
        f"Size: {escape_html(human_bytes(info['size']))}\n"
        f"Mode: <code>{escape_html(info['mode'])}</code>"
    )
    if info["size"] <= ctx.settings.max_read_bytes and not info["is_dir"]:
        try:
            payload = ctx.files.read_text(path, max_bytes=MAX_INLINE_READ)
            text += "\n\n" + pre(payload["text"])
            if payload["truncated"]:
                text += f"\n<i>(preview limited to {MAX_INLINE_READ} bytes)</i>"
        except Exception as exc:  # noqa: BLE001
            text += f"\n\n<i>Preview unavailable: {escape_html(exc)}</i>"
    await query.edit_message_text(text, parse_mode=ParseMode.HTML,
                                  reply_markup=InlineKeyboardMarkup(rows))


async def cb_up(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    await update.callback_query.answer()
    listing = ctx.state.get_listing(update.effective_user.id)
    if not listing:
        await show_directory(update, context)
        return
    parent = Path(listing["path"]).parent
    if not ctx.files.is_allowed(parent):
        await update.callback_query.answer("Already at the top of the allowed roots.",
                                           show_alert=True)
        return
    await show_directory(update, context, str(parent), 0)


async def cb_root(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    await update.callback_query.answer()
    await show_directory(update, context, str(ctx.files.default_root()), 0)


async def cb_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    query = update.callback_query
    await query.answer()
    try:
        page = int(query.data.rsplit(":", 1)[1])
    except ValueError:
        return
    listing = ctx.state.get_listing(update.effective_user.id)
    if not listing:
        await show_directory(update, context)
        return
    await query.edit_message_text(
        _header(listing["path"], listing["entries"], page),
        parse_mode=ParseMode.HTML,
        reply_markup=_keyboard(listing, page),
    )


async def cb_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    await update.callback_query.answer()
    listing = ctx.state.get_listing(update.effective_user.id)
    await show_directory(update, context, listing["path"] if listing else None, 0)


async def cb_download(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    query = update.callback_query
    await query.answer()
    path = ctx.state.get(update.effective_user.id, "selected")
    if not path:
        await query.edit_message_text("⚠️ No file selected.")
        return
    try:
        data = await asyncio.to_thread(ctx.files.read_bytes, path)
    except Exception as exc:  # noqa: BLE001
        await query.edit_message_text(f"❌ {escape_html(exc)}", parse_mode=ParseMode.HTML)
        return
    await query.message.reply_document(
        InputFile(io.BytesIO(data), filename=Path(path).name),
        caption=f"<code>{escape_html(path)}</code>",
        parse_mode=ParseMode.HTML,
    )


async def cb_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    query = update.callback_query
    await query.answer()
    path = ctx.state.get(update.effective_user.id, "selected")
    if not path:
        await query.edit_message_text("⚠️ No file selected.")
        return
    token = ctx.state.add_pending(update.effective_user.id, {"action": "delete", "path": path})
    await query.edit_message_text(
        f"⚠️ Delete <code>{escape_html(path)}</code>?\n"
        "Directories are removed recursively.",
        parse_mode=ParseMode.HTML,
        reply_markup=confirm_keyboard(token, "🗑 Delete", "❌ Keep"),
    )


async def cb_rename(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    query = update.callback_query
    await query.answer()
    path = ctx.state.get(update.effective_user.id, "selected")
    if not path:
        await query.edit_message_text("⚠️ No file selected.")
        return
    ctx.state.set(update.effective_user.id, "awaiting_rename", path)
    await query.edit_message_text(
        f"✏️ Send the new name for <code>{escape_html(Path(path).name)}</code>.\n"
        "Send /cancel to abort.",
        parse_mode=ParseMode.HTML,
    )


async def h_rename_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    admin_id = update.effective_user.id
    source = ctx.state.get(admin_id, "awaiting_rename")
    if not source:
        return
    ctx.state.delete(admin_id, "awaiting_rename")
    new_name = (update.effective_message.text or "").strip()
    if not new_name or "/" in new_name:
        await update.effective_message.reply_text("❌ Invalid name.")
        return
    target = str(Path(source).parent / new_name)
    try:
        await asyncio.to_thread(ctx.files.rename, source, target)
    except Exception as exc:  # noqa: BLE001
        await update.effective_message.reply_text(f"❌ {escape_html(exc)}",
                                                  parse_mode=ParseMode.HTML)
        return
    await update.effective_message.reply_text(
        f"✅ Renamed to <code>{escape_html(target)}</code>", parse_mode=ParseMode.HTML
    )


async def h_file_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Receives a Telegram document and stores it in the current directory."""
    ctx: AppContext = context.application.bot_data["ctx"]
    message = update.effective_message
    document = message.document
    if document is None:
        return
    if document.file_size and document.file_size > ctx.settings.max_upload_bytes:
        await message.reply_text(
            f"❌ File too large ({human_bytes(document.file_size)} > "
            f"{human_bytes(ctx.settings.max_upload_bytes)})."
        )
        return
    listing = ctx.state.get_listing(update.effective_user.id)
    if not listing:
        await message.reply_text("⚠️ Open /files first so a target directory is known.")
        return
    target_dir = listing["path"]
    file_obj = await document.get_file()
    data = bytes(await file_obj.download_as_bytearray())
    target = str(Path(target_dir) / (document.file_name or "upload.bin"))
    try:
        info = await asyncio.to_thread(ctx.files.write_bytes, target, data, False)
    except FileExistsError:
        token = ctx.state.add_pending(
            update.effective_user.id, {"action": "overwrite", "path": target, "data": data}
        )
        await message.reply_text(
            f"⚠️ <code>{escape_html(target)}</code> exists. Overwrite?",
            parse_mode=ParseMode.HTML,
            reply_markup=confirm_keyboard(token, "♻️ Overwrite", "❌ Cancel"),
        )
        return
    except Exception as exc:  # noqa: BLE001
        await message.reply_text(f"❌ {escape_html(exc)}", parse_mode=ParseMode.HTML)
        return
    await message.reply_text(
        f"✅ Saved {human_bytes(info['size'])} to <code>{escape_html(info['path'])}</code>",
        parse_mode=ParseMode.HTML,
    )


def register(application: Application, ctx: AppContext) -> None:
    user_filter = filters.User(list(ctx.settings.telegram_admin_ids))
    application.add_handler(CommandHandler("files", h_files, filters=user_filter))
    application.add_handler(
        CallbackQueryHandler(cb_open, pattern=r"^f:o:\d+$")
    )
    application.add_handler(CallbackQueryHandler(cb_up, pattern=r"^f:up$"))
    application.add_handler(CallbackQueryHandler(cb_root, pattern=r"^f:root$"))
    application.add_handler(CallbackQueryHandler(cb_page, pattern=r"^f:p:\d+$"))
    application.add_handler(CallbackQueryHandler(cb_back, pattern=r"^f:back$"))
    application.add_handler(CallbackQueryHandler(cb_download, pattern=r"^f:dl$"))
    application.add_handler(CallbackQueryHandler(cb_delete, pattern=r"^f:del$"))
    application.add_handler(CallbackQueryHandler(cb_rename, pattern=r"^f:ren$"))
    application.add_handler(
        MessageHandler(
            filters.User(list(ctx.settings.telegram_admin_ids)) & filters.Document.ALL,
            h_file_upload,
        )
    )
    application.add_handler(
        MessageHandler(
            filters.User(list(ctx.settings.telegram_admin_ids)) & filters.TEXT & ~filters.COMMAND,
            h_rename_message,
        ),
        group=5,
    )

    ctx.register_action("files", h_files)
