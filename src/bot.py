#!/usr/bin/env python3
"""
hntldr — Hacker News TL;DR Telegram Bot
Fetches HN stories, summarizes them with a concise hook, posts to Telegram.
"""

import logging
import re
import sys
import time
import html
from secrets import token_hex

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import config
from config_manager import CATEGORY_ORDER, EDITABLE_KEY_SPECS, config_manager
from fetcher import fetch_hn_item, fetch_article_content, close_http_session
from summarizer import summarize_article, Summary
from scheduler import start_scheduler
from formatter import format_telegram_message

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    stream=sys.stdout
)
logger = logging.getLogger("hntldr.bot")

SETTINGS_TTL_SECONDS = 600
LOOKUP_CACHE_TTL_SECONDS = 1800

SETTINGS_SESSIONS: dict[int, dict] = {}
SETTINGS_PENDING_INPUTS: dict[int, dict] = {}
SETTINGS_PENDING_RESTART: set[str] = set()
ENTITY_LOOKUP_CACHE: dict[int, tuple[str, float]] = {}


class SettingsInputFilter(filters.MessageFilter):
    """Only match text messages from users currently in settings input mode."""

    def filter(self, message):
        if not message or not message.from_user:
            return False
        return message.from_user.id in SETTINGS_PENDING_INPUTS


def _new_settings_session(user_id: int, chat_id: int) -> dict:
    sid = token_hex(4)
    session = {
        "sid": sid,
        "chat_id": chat_id,
        "message_id": None,
        "expires_at": time.time() + SETTINGS_TTL_SECONDS,
    }
    SETTINGS_SESSIONS[user_id] = session
    SETTINGS_PENDING_INPUTS.pop(user_id, None)
    return session


def _touch_settings_session(user_id: int):
    session = SETTINGS_SESSIONS.get(user_id)
    if session:
        session["expires_at"] = time.time() + SETTINGS_TTL_SECONDS


def _validate_settings_session(user_id: int, chat_id: int, message_id: int, sid: str) -> bool:
    session = SETTINGS_SESSIONS.get(user_id)
    if not session:
        return False
    if session.get("sid") != sid:
        return False
    if session.get("chat_id") != chat_id:
        return False
    if session.get("message_id") != message_id:
        return False
    if time.time() > session.get("expires_at", 0):
        return False
    return True


def _is_dm(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and chat.type == "private")


def _format_value(env_key: str) -> str:
    value = config_manager.get_value(env_key)
    spec = EDITABLE_KEY_SPECS[env_key]
    if spec.value_type == "bool":
        return "ON" if value else "OFF"
    if spec.value_type == "int_set":
        if not value:
            return "(empty)"
        return ", ".join(str(v) for v in sorted(value))
    if spec.value_type == "str":
        return value or "(empty)"
    return str(value)


def _format_display_name(name: str, entity_id: int) -> str:
    trimmed = " ".join(name.split())
    if len(trimmed) > 28:
        trimmed = trimmed[:25] + "..."
    return f"{trimmed} ({entity_id})"


async def _resolve_entity_name(bot, entity_id: int) -> str:
    cached = ENTITY_LOOKUP_CACHE.get(entity_id)
    now = time.time()
    if cached and (now - cached[1] < LOOKUP_CACHE_TTL_SECONDS):
        return cached[0]

    label = str(entity_id)
    try:
        chat = await bot.get_chat(entity_id)
        display = ""
        if getattr(chat, "title", ""):
            display = chat.title
        else:
            first = getattr(chat, "first_name", "") or ""
            last = getattr(chat, "last_name", "") or ""
            username = getattr(chat, "username", "") or ""
            full = f"{first} {last}".strip()
            display = full or (f"@{username}" if username else "")
        if display:
            label = _format_display_name(display, entity_id)
    except Exception:
        label = str(entity_id)

    ENTITY_LOOKUP_CACHE[entity_id] = (label, now)
    return label


async def _format_set_value_with_names(bot, env_key: str) -> str:
    values = sorted(config_manager.get_value(env_key))
    if not values:
        return "(empty)"

    labels: list[str] = []
    for entity_id in values:
        labels.append(await _resolve_entity_name(bot, entity_id))
    return ", ".join(labels)


def _main_settings_text() -> str:
    lines = [
        "<b>Bot Settings</b>",
        "DM-only admin panel.",
    ]
    if SETTINGS_PENDING_RESTART:
        lines.append("")
        lines.append("<b>Restart needed</b> for some saved changes.")
    return "\n".join(lines)


def _main_settings_markup(sid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Flames", callback_data=f"settings:{sid}:view:flames")],
            [InlineKeyboardButton("Polling", callback_data=f"settings:{sid}:view:polling")],
            [InlineKeyboardButton("Posting", callback_data=f"settings:{sid}:view:posting")],
            [InlineKeyboardButton("Whitelist", callback_data=f"settings:{sid}:view:whitelist")],
            [InlineKeyboardButton("Admins", callback_data=f"settings:{sid}:view:admins")],
            [InlineKeyboardButton("Min scores", callback_data=f"settings:{sid}:view:min_scores")],
            [
                InlineKeyboardButton("Refresh", callback_data=f"settings:{sid}:main"),
                InlineKeyboardButton("Close", callback_data=f"settings:{sid}:close"),
            ],
        ]
    )


def _category_title(category: str) -> str:
    mapping = {
        "flames": "Flames",
        "polling": "Polling",
        "posting": "Posting",
        "whitelist": "Whitelist",
        "admins": "Admins",
        "min_scores": "Min scores",
    }
    return mapping.get(category, category)


async def _category_settings_text(category: str, bot) -> str:
    lines = [f"<b>{_category_title(category)}</b>"]
    for spec in config_manager.keys_for_category(category):
        if spec.value_type == "int_set":
            rendered = await _format_set_value_with_names(bot, spec.env_key)
        else:
            rendered = _format_value(spec.env_key)
        safe_value = html.escape(rendered)
        lines.append(f"- <code>{spec.env_key}</code>: <code>{safe_value}</code>")
    if any(EDITABLE_KEY_SPECS[key].restart_required for key in CATEGORY_ORDER.get(category, [])):
        lines.append("")
        lines.append("Some changes in this section require restart.")
    return "\n".join(lines)


async def _category_settings_markup(category: str, sid: str, user_id: int, bot) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for spec in config_manager.keys_for_category(category):
        value_text = _format_value(spec.env_key)
        if spec.value_type == "bool":
            rows.append(
                [
                    InlineKeyboardButton(
                        f"{spec.label}: {value_text}",
                        callback_data=f"settings:{sid}:toggle:{spec.env_key}",
                    )
                ]
            )
            continue

        if spec.value_type == "int_set":
            rows.append(
                [
                    InlineKeyboardButton(
                        f"Add to {spec.label}",
                        callback_data=f"settings:{sid}:prompt_add:{spec.env_key}",
                    )
                ]
            )
            current_values = sorted(config_manager.get_value(spec.env_key))
            for set_value in current_values:
                remove_cb = f"settings:{sid}:remove:{spec.env_key}:{set_value}"
                if spec.env_key == "ADMIN_USER_ID" and (set_value == user_id or len(current_values) <= 1):
                    remove_cb = f"settings:{sid}:noop"
                friendly = await _resolve_entity_name(bot, set_value)
                rows.append(
                    [
                        InlineKeyboardButton(
                            f"Remove {friendly}",
                            callback_data=remove_cb,
                        )
                    ]
                )
            continue

        rows.append(
            [
                InlineKeyboardButton(
                    f"Set {spec.label}: {value_text}",
                    callback_data=f"settings:{sid}:prompt:{spec.env_key}",
                )
            ]
        )

    rows.append([InlineKeyboardButton("Back", callback_data=f"settings:{sid}:main")])
    return InlineKeyboardMarkup(rows)


async def _show_settings_main(update: Update, context: ContextTypes.DEFAULT_TYPE, sid: str):
    if update.callback_query and update.callback_query.message:
        await update.callback_query.edit_message_text(
            _main_settings_text(),
            parse_mode="HTML",
            reply_markup=_main_settings_markup(sid),
        )
        return

    if not update.message:
        return
    sent = await update.message.reply_text(
        _main_settings_text(),
        parse_mode="HTML",
        reply_markup=_main_settings_markup(sid),
    )
    if update.effective_user and update.effective_user.id in SETTINGS_SESSIONS:
        SETTINGS_SESSIONS[update.effective_user.id]["message_id"] = sent.message_id


async def _show_claim_admin_screen(update: Update, sid: str):
    text = (
        "<b>Admin setup required</b>\n"
        "No admins are configured yet.\n\n"
        "Tap the button below to claim the first admin account for this bot."
    )
    markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Claim admin", callback_data=f"settings:{sid}:claim")]]
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
    elif update.message:
        sent = await update.message.reply_text(text, parse_mode="HTML", reply_markup=markup)
        if update.effective_user and update.effective_user.id in SETTINGS_SESSIONS:
            SETTINGS_SESSIONS[update.effective_user.id]["message_id"] = sent.message_id


async def _show_category(update: Update, category: str, sid: str, user_id: int):
    if not update.callback_query:
        return
    text = await _category_settings_text(category, update.get_bot())
    markup = await _category_settings_markup(category, sid, user_id, update.get_bot())
    await update.callback_query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=markup,
    )


async def _show_prompt(update: Update, sid: str, env_key: str, mode: str):
    if not update.callback_query or not update.effective_user:
        return
    spec = EDITABLE_KEY_SPECS[env_key]
    SETTINGS_PENDING_INPUTS[update.effective_user.id] = {
        "sid": sid,
        "chat_id": update.effective_chat.id if update.effective_chat else 0,
        "env_key": env_key,
        "mode": mode,
        "category": spec.category,
        "expires_at": time.time() + SETTINGS_TTL_SECONDS,
    }
    prompt = "Enter a value:" if mode == "set" else "Enter an integer ID to add:"
    await update.callback_query.edit_message_text(
        f"<b>{spec.label}</b>\n{prompt}\n\nReply in this DM to continue.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("Cancel", callback_data=f"settings:{sid}:cancel_input")]]
        ),
    )


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.effective_chat:
        return
    if not _is_dm(update):
        if update.message:
            await update.message.reply_text("Settings are only available in a private DM.")
        return

    session = _new_settings_session(update.effective_user.id, update.effective_chat.id)
    sid = session["sid"]

    if not config_manager.has_admins():
        await _show_claim_admin_screen(update, sid)
        return

    if not config.is_admin(update.effective_user.id):
        SETTINGS_SESSIONS.pop(update.effective_user.id, None)
        if update.message:
            await update.message.reply_text("This command is restricted.")
        return

    await _show_settings_main(update, context, sid)


async def cb_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query or not update.effective_user or not update.effective_chat:
        return

    query = update.callback_query
    data = query.data or ""
    parts = data.split(":", 4)
    if len(parts) < 3 or parts[0] != "settings":
        return

    sid = parts[1]
    action = parts[2]
    arg1 = parts[3] if len(parts) > 3 else ""
    arg2 = parts[4] if len(parts) > 4 else ""

    message_id = query.message.message_id if query.message else 0
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    if not _validate_settings_session(user_id, chat_id, message_id, sid):
        await query.answer("Session expired. Run /settings again.", show_alert=True)
        return

    if action == "claim":
        if config_manager.has_admins():
            if config.is_admin(user_id):
                await query.answer("Admin already configured.")
                await _show_settings_main(update, context, sid)
            else:
                await query.answer("Admin already configured.", show_alert=True)
                SETTINGS_SESSIONS.pop(user_id, None)
                await query.edit_message_text("This action is restricted.")
            return
        config_manager.claim_first_admin(user_id)
        await query.answer("Admin claimed.")
        await _show_settings_main(update, context, sid)
        return

    if not config.is_admin(user_id):
        await query.answer("This action is restricted.", show_alert=True)
        return

    _touch_settings_session(user_id)

    if action == "noop":
        await query.answer("Not allowed.")
        return
    if action == "close":
        SETTINGS_PENDING_INPUTS.pop(user_id, None)
        SETTINGS_SESSIONS.pop(user_id, None)
        await query.edit_message_text("Settings closed.")
        return
    if action == "main":
        await query.answer()
        await _show_settings_main(update, context, sid)
        return
    if action == "view" and arg1 in CATEGORY_ORDER:
        await query.answer()
        await _show_category(update, arg1, sid, user_id)
        return
    if action == "toggle" and arg1 in EDITABLE_KEY_SPECS:
        current = bool(config_manager.get_value(arg1))
        restart_required = config_manager.set_value(arg1, not current)
        if restart_required:
            SETTINGS_PENDING_RESTART.add(arg1)
        await query.answer("Saved")
        spec = EDITABLE_KEY_SPECS[arg1]
        await _show_category(update, spec.category, sid, user_id)
        return
    if action == "prompt" and arg1 in EDITABLE_KEY_SPECS:
        await query.answer()
        await _show_prompt(update, sid, arg1, mode="set")
        return
    if action == "prompt_add" and arg1 in EDITABLE_KEY_SPECS:
        await query.answer()
        await _show_prompt(update, sid, arg1, mode="add_set")
        return
    if action == "cancel_input":
        pending = SETTINGS_PENDING_INPUTS.pop(user_id, None)
        await query.answer("Cancelled")
        if pending:
            await _show_category(update, pending.get("category", "flames"), sid, user_id)
        else:
            await _show_settings_main(update, context, sid)
        return
    if action == "remove" and arg1 in EDITABLE_KEY_SPECS:
        spec = EDITABLE_KEY_SPECS[arg1]
        if spec.value_type != "int_set":
            await query.answer("Invalid action", show_alert=True)
            return
        try:
            remove_id = int(arg2)
        except ValueError:
            await query.answer("Invalid ID", show_alert=True)
            return
        current_values = set(config_manager.get_value(arg1))
        if remove_id not in current_values:
            await query.answer("Already removed")
            await _show_category(update, spec.category, sid, user_id)
            return
        if arg1 == "ADMIN_USER_ID" and (remove_id == user_id or len(current_values) <= 1):
            await query.answer("Cannot remove this admin.", show_alert=True)
            return
        restart_required = config_manager.remove_from_set(arg1, remove_id)
        if restart_required:
            SETTINGS_PENDING_RESTART.add(arg1)
        await query.answer("Removed")
        await _show_category(update, spec.category, sid, user_id)
        return

    await query.answer("Unsupported action", show_alert=True)


async def handle_settings_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user or not update.effective_chat:
        return

    user_id = update.effective_user.id
    pending = SETTINGS_PENDING_INPUTS.get(user_id)
    if not pending:
        return

    if time.time() > pending.get("expires_at", 0):
        SETTINGS_PENDING_INPUTS.pop(user_id, None)
        await update.message.reply_text("Input session expired. Run /settings again.")
        return

    if pending.get("chat_id") != update.effective_chat.id:
        return

    if not config.is_admin(user_id):
        SETTINGS_PENDING_INPUTS.pop(user_id, None)
        await update.message.reply_text("This action is restricted.")
        return

    text = (update.message.text or "").strip()
    if text.lower() in {"cancel", "/cancel"}:
        SETTINGS_PENDING_INPUTS.pop(user_id, None)
        await update.message.reply_text("Cancelled.")
        return

    env_key = pending["env_key"]
    mode = pending["mode"]
    try:
        if mode == "set":
            restart_required = config_manager.set_from_input(env_key, text)
        elif mode == "add_set":
            try:
                add_value = int(text)
            except ValueError as exc:
                raise ValueError("Expected an integer ID") from exc
            restart_required = config_manager.add_to_set(env_key, add_value)
        else:
            raise ValueError("Unsupported input mode")
    except ValueError as exc:
        await update.message.reply_text(f"Invalid value: {exc}")
        return

    if restart_required:
        SETTINGS_PENDING_RESTART.add(env_key)

    SETTINGS_PENDING_INPUTS.pop(user_id, None)
    _touch_settings_session(user_id)

    restart_hint = " Restart bot to apply fully." if restart_required else ""
    await update.message.reply_text(f"Saved {env_key}.{restart_hint}")

    session = SETTINGS_SESSIONS.get(user_id)
    if not session:
        return

    sid = session["sid"]
    category = pending.get("category", "flames")

    try:
        await context.bot.edit_message_text(
            chat_id=session["chat_id"],
            message_id=session["message_id"],
            text=await _category_settings_text(category, context.bot),
            parse_mode="HTML",
            reply_markup=await _category_settings_markup(category, sid, user_id, context.bot),
        )
    except Exception:
        pass


def extract_hn_id(text: str) -> str | None:
    """Extract HN item ID from a URL or raw ID."""
    patterns = [
        r"news\.ycombinator\.com/item\?id=(\d+)",
        r"hn\.algolia\.com/.*id=(\d+)",
        r"^(\d{6,12})$",
    ]
    for pattern in patterns:
        m = re.search(pattern, text.strip())
        if m:
            return m.group(1)
    return None


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    await update.message.reply_text(
        "<b>hntldr</b> -- HN without the useless titles\n\n"
        "Send me any HN link or item ID and I'll give you a real summary.\n\n"
        "Commands:\n"
        "/summarize <code>&lt;hn_url_or_id&gt;</code> -- summarize a specific story\n"
        "/settings -- admin settings menu (DM only)\n"
        "/help -- show this message",
        parse_mode="HTML"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


async def process_hn_item(hn_id: str) -> dict | None:
    """Full pipeline: HN ID -> formatted message dict."""
    item = await fetch_hn_item(hn_id)
    if not item:
        return None

    url = item.get("url", "")
    text_content = item.get("text", "")

    # Skip LLM for posts with no URL and minimal text
    if not url and len(text_content or "") < 100:
        summary = Summary(hook="")
    else:
        article_text = await fetch_article_content(url) if url else ""
        content_for_summary = article_text or (text_content[:config.max_article_chars] if text_content else "")
        summary = await summarize_article(
            title=item.get("title", ""),
            url=url,
            content=content_for_summary,
            score=item.get("points", 0),
            comments=item.get("num_comments", 0),
        )

    return format_telegram_message(
        summary=summary,
        title=item.get("title", ""),
        url=url,
        hn_id=hn_id,
        score=item.get("points", 0),
        comments=item.get("num_comments", 0),
    )


def _is_allowed(update: Update) -> bool:
    """Check if the user is allowed to use the bot in this chat."""
    chat_id = update.effective_chat.id if update.effective_chat else 0
    user_id = update.effective_user.id if update.effective_user else 0

    # Group/channel: must be whitelisted
    if chat_id < 0:
        return config.is_whitelisted_chat(chat_id)

    # DM: admin check
    return config.is_admin(user_id)


async def cmd_summarize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /summarize <url_or_id>"""
    if not update.message or not update.effective_user:
        return

    if not _is_allowed(update):
        await update.message.reply_text("This command is restricted.")
        return

    args = " ".join(context.args).strip() if context.args else ""
    if not args:
        await update.message.reply_text("Usage: /summarize <hn_url_or_item_id>")
        return

    hn_id = extract_hn_id(args)
    if not hn_id:
        await update.message.reply_text("Could not find a valid HN item ID in that URL.")
        return

    thinking = await update.message.reply_text("Reading...")

    try:
        result = await process_hn_item(hn_id)
        if not result:
            await thinking.edit_text("Could not fetch that HN item. Check the ID/URL.")
            return
        await thinking.edit_text(
            result["text"],
            parse_mode="HTML",
            reply_markup=result["reply_markup"],
            link_preview_options=result["link_preview_options"],
        )
    except Exception as e:
        logger.error(f"Error summarizing {hn_id}: {e}", exc_info=True)
        await thinking.edit_text(f"Something went wrong: {str(e)[:200]}")


async def handle_hn_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Auto-detect HN URLs pasted into chat."""
    if not update.message:
        return

    if not _is_allowed(update):
        return

    text = update.message.text or ""
    hn_id = extract_hn_id(text)
    if not hn_id:
        return

    thinking = await update.message.reply_text("Reading...")
    try:
        result = await process_hn_item(hn_id)
        if result:
            await thinking.edit_text(
                result["text"],
                parse_mode="HTML",
                reply_markup=result["reply_markup"],
                link_preview_options=result["link_preview_options"],
            )
        else:
            await thinking.delete()
    except Exception as e:
        logger.error(f"Auto-detect error: {e}", exc_info=True)
        await thinking.edit_text(f"Could not fetch: {str(e)[:200]}")


def main():
    # Validation is handled by config.validate() at import time

    logger.info("Starting hntldr bot...")

    app = (
        Application.builder()
        .token(config.telegram_token)
        .build()
    )

    async def _post_init(application: Application):
        await application.bot.set_my_commands([
            BotCommand("start", "Show intro and usage"),
            BotCommand("help", "Show help"),
            BotCommand("summarize", "Summarize an HN story"),
            BotCommand("settings", "Admin settings menu (DM)"),
        ])
        logger.info("Registered bot command menu entries")

    app.post_init = _post_init

    async def _post_shutdown(application: Application):
        await close_http_session()

    app.post_shutdown = _post_shutdown

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("summarize", cmd_summarize))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CallbackQueryHandler(cb_settings, pattern=r"^settings:"))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND & SettingsInputFilter(), handle_settings_input))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.Regex(r"ycombinator\.com/item"),
        handle_hn_url
    ))

    # Start the auto-polling scheduler if channel is configured
    if config.telegram_channel_id:
        logger.info(f"Auto-posting to channel: {config.telegram_channel_id}")
        start_scheduler(app)
    else:
        logger.info("No TELEGRAM_CHANNEL_ID set -- auto-posting disabled")

    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
