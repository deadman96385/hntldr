#!/usr/bin/env python3
"""
hntldr — Hacker News TL;DR Telegram Bot
Fetches HN stories, summarizes them with a concise hook, posts to Telegram.
"""

import logging
import re
import sys

from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from config import config
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
        "/summarize <code>&lt;hn_url_or_id&gt;</code> -- summarize a specific story (admin only)\n"
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


async def cmd_summarize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /summarize <url_or_id> (admin only)"""
    if not update.message or not update.effective_user:
        return

    if not config.is_admin(update.effective_user.id):
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
        )
    except Exception as e:
        logger.error(f"Error summarizing {hn_id}: {e}", exc_info=True)
        await thinking.edit_text(f"Something went wrong: {str(e)[:200]}")


async def handle_hn_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Auto-detect HN URLs pasted into chat."""
    if not update.message:
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
            BotCommand("summarize", "Summarize an HN story (admin only)"),
        ])
        logger.info("Registered bot command menu entries")

    app.post_init = _post_init

    async def _post_shutdown(application: Application):
        await close_http_session()

    app.post_shutdown = _post_shutdown

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("summarize", cmd_summarize))
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
