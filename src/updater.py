"""Live-update worker — edits posted messages with fresh scores and comment counts."""

import asyncio
import logging
from datetime import timedelta
from typing import cast

from telegram import Bot
from telegram.error import BadRequest, RetryAfter

from config import config
from fetcher import fetch_hn_item
from formatter import build_update_buttons, build_update_link_preview, format_update_text, HN_ITEM_URL
from store import Store
from summarizer import Summary
from errors import notify_admin

logger = logging.getLogger("hntldr.updater")


async def update_worker(bot: Bot):
    """Async worker that continuously processes message update tasks."""
    store = Store(config.db_path)
    logger.info("Update worker started")
    cycle = 0

    while True:
        try:
            task = store.get_next_update_task()
            if task is None:
                await asyncio.sleep(15)
                cycle += 1
                # Periodic cleanup every ~10 minutes
                if cycle % 40 == 0:
                    store.remove_expired_updates()
                continue

            hn_id = task["hn_id"]

            # Fetch fresh data
            item = await fetch_hn_item(hn_id)
            if not item:
                store.advance_update_task(hn_id, task["score"], task["comments"])
                await asyncio.sleep(1)
                continue

            new_score = item.get("points", 0)
            new_comments = item.get("num_comments", 0)

            # Only edit if something changed
            if new_score != task["score"] or new_comments != task["comments"]:
                summary = Summary(hook=task["hook"])
                hn_url = HN_ITEM_URL.format(hn_id=hn_id)
                link_url = task["url"] or hn_url

                text = format_update_text(task["title"], summary, new_score, link_url)
                buttons = build_update_buttons(task["url"], hn_url, new_comments)
                link_preview = build_update_link_preview(link_url)

                try:
                    await bot.edit_message_text(
                        chat_id=task["chat_id"],
                        message_id=task["message_id"],
                        text=text,
                        parse_mode="HTML",
                        reply_markup=buttons,
                        link_preview_options=link_preview,
                    )
                except RetryAfter as e:
                    retry_after_raw = e.retry_after
                    if isinstance(retry_after_raw, timedelta):
                        retry_after = retry_after_raw.total_seconds()
                    else:
                        retry_after = float(cast(int, retry_after_raw))
                    logger.warning(f"Rate limited, sleeping {retry_after}s")
                    await asyncio.sleep(retry_after)
                    continue
                except BadRequest as e:
                    if "message is not modified" not in str(e).lower():
                        logger.warning(f"Edit failed for {hn_id}: {e}")

            store.advance_update_task(hn_id, new_score, new_comments)
            await asyncio.sleep(1)  # pacing

        except asyncio.CancelledError:
            logger.info("Update worker cancelled")
            break
        except Exception as e:
            logger.error(f"Update worker error: {e}", exc_info=True)
            await notify_admin(bot, str(e), "update_worker")
            await asyncio.sleep(15)
