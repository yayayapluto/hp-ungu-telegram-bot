"""Termux:API bridge commands and callbacks."""
from __future__ import annotations

import io
import json
from typing import Any, Dict

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, filters

from ubuntu.context import AppContext
from ubuntu.keyboards.main_menu import with_back
from ubuntu.utils.formatting import escape_html, human_bytes, pre

SUPPORTED_OPS = (
    "battery", "device", "notification", "vibrate", "torch", "clipboard_get",
    "clipboard_set", "location", "wifi", "toast", "tts",
)

SENSITIVE_OPS = {"camera", "microphone", "clipboard_get", "location"}


def _keyboard(features: Dict[str, bool]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🔋 Battery", callback_data="x:battery"),
         InlineKeyboardButton("📱 Device", callback_data="x:device")],
        [InlineKeyboardButton("📶 Wi-Fi", callback_data="x:wifi"),
         InlineKeyboardButton("📍 Location", callback_data="x:location")],
        [InlineKeyboardButton("📋 Clipboard", callback_data="x:clipboard_get"),
         InlineKeyboardButton("🔦 Torch toggle", callback_data="x:torch")],
        [InlineKeyboardButton("📷 Camera", callback_data="x:camera"),
         InlineKeyboardButton("🎙 Microphone 5s", callback_data="x:microphone")],
        [InlineKeyboardButton("📳 Vibrate 500ms", callback_data="x:vibrate"),
         InlineKeyboardButton("🔔 Test notification", callback_data="x:notification")],
        [InlineKeyboardButton("📋 Feature list", callback_data="x:features")],
        [InlineKeyboardButton("⬅️ Back", callback_data="m:main")],
    ]
    return InlineKeyboardMarkup(rows)


async def h_termuxapi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    if update.callback_query:
        await update.callback_query.answer()
    try:
        data = await ctx.bridge.features()
    except Exception as exc:  # noqa: BLE001
        text = f"🤖 <b>Termux:API</b>\n\n🔴 Bridge unavailable: {escape_html(exc)}"
        if update.callback_query:
            await update.callback_query.edit_message_text(
                text, parse_mode=ParseMode.HTML, reply_markup=with_back([], "m:main")
            )
        else:
            await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)
        return

    available = data.get("available", {})
    lines = ["🤖 <b>Termux:API</b>", ""]
    for name, ok in sorted(available.items()):
        lines.append(f"{'🟢' if ok else '⚪'} <code>{escape_html(name)}</code>")
    text = "\n".join(lines)
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=_keyboard(available)
        )
    else:
        await update.effective_message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=_keyboard(available)
        )


async def h_termux(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generic bridge call: /termux <op> [json]."""
    ctx: AppContext = context.application.bot_data["ctx"]
    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "Usage: <code>/termux &lt;op&gt; [json]</code>\n"
            f"Ops: {escape_html(', '.join(SUPPORTED_OPS))}",
            parse_mode=ParseMode.HTML,
        )
        return
    op = args[0]
    params: Dict[str, Any] = {}
    if len(args) > 1:
        try:
            params = json.loads(" ".join(args[1:]))
        except ValueError as exc:
            await update.effective_message.reply_text(f"❌ Invalid JSON: {escape_html(exc)}")
            return
    await _run_op(update, ctx, op, params)


async def _run_op(update: Update, ctx: AppContext, op: str, params: Dict[str, Any]) -> None:
    message = update.effective_message
    try:
        if op == "battery":
            result: Any = await ctx.bridge.battery()
        elif op == "device":
            result = await ctx.bridge.device_info()
        elif op == "wifi":
            result = await ctx.bridge.wifi_info()
        elif op == "location":
            result = await ctx.bridge.location(params.get("provider", "network"))
        elif op == "clipboard_get":
            result = await ctx.bridge.clipboard_get()
        elif op == "clipboard_set":
            result = await ctx.bridge.clipboard_set(str(params.get("text", "")))
        elif op == "vibrate":
            result = await ctx.bridge.vibrate(int(params.get("milliseconds", 500)))
        elif op == "torch":
            state = str(params.get("state", "toggle"))
            result = await ctx.bridge.torch(state)
        elif op == "notification":
            result = await ctx.bridge.notification(
                str(params.get("title", "hp-ungu")),
                str(params.get("content", "Test notification")),
            )
        elif op == "toast":
            result = await ctx.bridge.toast(str(params.get("text", "hp-ungu")))
        elif op == "tts":
            result = await ctx.bridge.tts(str(params.get("text", "hello")))
        elif op == "camera":
            blob = await ctx.bridge.camera_photo(int(params.get("camera", 0)))
            await message.reply_photo(
                InputFile(io.BytesIO(blob), filename="camera.jpg"),
                caption=f"📷 {human_bytes(len(blob))}",
            )
            return
        elif op == "microphone":
            seconds = int(params.get("seconds", 5))
            blob = await ctx.bridge.microphone_record(seconds)
            await message.reply_voice(
                InputFile(io.BytesIO(blob), filename="recording.m4a")
            )
            return
        else:
            await message.reply_text(f"❌ Unsupported operation: {escape_html(op)}")
            return
    except Exception as exc:  # noqa: BLE001
        await message.reply_text(f"❌ {escape_html(exc)}", parse_mode=ParseMode.HTML)
        return

    rendered = json.dumps(result, indent=2, ensure_ascii=False, default=str)
    await message.reply_text(
        f"✅ <b>{escape_html(op)}</b>\n\n{pre(rendered[:3000])}",
        parse_mode=ParseMode.HTML,
    )


async def cb_op(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: AppContext = context.application.bot_data["ctx"]
    query = update.callback_query
    op = query.data.split(":", 1)[1]
    await query.answer()

    if op == "features":
        await h_termuxapi(update, context)
        return

    if op in SENSITIVE_OPS:
        token = ctx.state.add_pending(update.effective_user.id, {"action": "termux_op", "op": op})
        await query.edit_message_text(
            f"🔐 <b>{escape_html(op)}</b> requires explicit confirmation and Android "
            "runtime permission.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("✅ Proceed", callback_data=f"cf:{token}:y"),
                        InlineKeyboardButton("❌ Cancel", callback_data=f"cf:{token}:n"),
                    ],
                    [InlineKeyboardButton("⬅️ Back", callback_data="m:termuxapi")],
                ]
            ),
        )
        return

    if op == "torch":
        new_state = "off" if bool(ctx.state.get(update.effective_user.id, "torch_on", False)) else "on"
        ctx.state.set(update.effective_user.id, "torch_on", new_state == "on")
        await _run_op(update, ctx, "torch", {"state": new_state})
        return

    await _run_op(update, ctx, op, {})


def register(application: Application, ctx: AppContext) -> None:
    user_filter = filters.User(list(ctx.settings.telegram_admin_ids))
    application.add_handler(CommandHandler("termuxapi", h_termuxapi, filters=user_filter))
    application.add_handler(CommandHandler("termux", h_termux, filters=user_filter))
    application.add_handler(CallbackQueryHandler(cb_op, pattern=r"^x:[a-z_]+$"))

    ctx.register_action("termuxapi", h_termuxapi)
