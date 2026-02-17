"""
Scheduler — periodically polls HN top stories and auto-posts new ones to Telegram channel.
Uses APScheduler for reliable async scheduling.
"""

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from telegram.ext import Application

from config import config
from fetcher import fetch_top_stories_with_details, fetch_article_content, detect_topic
from summarizer import summarize_article, Summary
from store import Store
from formatter import format_telegram_message
from errors import notify_admin
from updater import update_worker

logger = logging.getLogger("hntldr.scheduler")


async def poll_and_post(app: Application):
    """
    Main polling job — fetches top HN stories, filters new ones, summarizes & posts.
    """
    logger.info("Polling HN top stories...")
    store = Store(config.db_path)

    try:
        stories = await fetch_top_stories_with_details(limit=50)
        logger.info(f"Fetched {len(stories)} stories")

        posted_count = 0
        for story in stories:
            if posted_count >= config.stories_per_poll:
                break

            hn_id = story.get("id", "")
            if not hn_id or store.has_been_posted(hn_id):
                continue

            title = story.get("title", "")
            url = story.get("url", "")
            score = story.get("points", 0)
            comments = story.get("num_comments", 0)
            text_content = story.get("text", "")
            item_type = story.get("type", "story")

            topic = detect_topic(title, item_type)
            threshold = config.score_threshold_for(topic)
            if threshold == -1 or score < threshold:
                logger.debug(f"Skipping [{topic}] {title[:50]} ({score}pts < {threshold})")
                continue

            logger.info(f"Summarizing: [{topic}] [{score}pts] {title[:60]}")

            try:
                # Skip LLM for posts with no URL and minimal text
                if not url and len(text_content or "") < 100:
                    summary = Summary(hook="")
                else:
                    article_text = await fetch_article_content(url) if url else ""
                    content_for_summary = article_text or (text_content[:config.max_article_chars] if text_content else "")
                    summary = await summarize_article(
                        title=title,
                        url=url,
                        content=content_for_summary,
                        score=score,
                        comments=comments,
                    )

                # Format message
                result = format_telegram_message(
                    summary=summary,
                    title=title,
                    url=url,
                    hn_id=hn_id,
                    score=score,
                    comments=comments,
                )

                # Post to channel
                sent_msg = await app.bot.send_message(
                    chat_id=config.telegram_channel_id,
                    text=result["text"],
                    parse_mode="HTML",
                    reply_markup=result["reply_markup"],
                )

                # Register for live updates
                store.add_update_task(
                    hn_id=hn_id,
                    message_id=sent_msg.message_id,
                    chat_id=str(config.telegram_channel_id),
                    title=title,
                    hook=result.get("hook", ""),
                    url=url,
                    score=score,
                    comments=comments,
                )

                # Mark as posted
                store.mark_posted(hn_id, title=title, score=score)
                posted_count += 1
                logger.info(f"Posted: {title[:60]}")

                # Polite delay between posts
                await asyncio.sleep(3)

            except Exception as e:
                logger.error(f"Failed to process story {hn_id}: {e}", exc_info=True)
                await notify_admin(app.bot, str(e), f"poll_and_post story {hn_id}")
                continue

        if posted_count > 0:
            logger.info(f"Poll cycle complete — posted {posted_count} new stories")
        else:
            logger.info("Poll cycle complete — no new stories to post")

        # Housekeeping
        store.prune_old(days=30)

    except Exception as e:
        logger.error(f"Poll cycle failed: {e}", exc_info=True)
        await notify_admin(app.bot, str(e), "poll_and_post")


def start_scheduler(app: Application):
    """Start the background polling scheduler."""
    scheduler = AsyncIOScheduler()

    scheduler.add_job(
        poll_and_post,
        trigger=IntervalTrigger(minutes=config.poll_interval_minutes),
        args=[app],
        id="hn_poll",
        name="HN Top Stories Poller",
        replace_existing=True,
        misfire_grace_time=300,  # 5 min grace if job misfires
    )

    async def _start_scheduler(application: Application):
        scheduler.start()
        logger.info(
            f"Scheduler started — polling every {config.poll_interval_minutes} minutes, "
            f"default min score: {config.min_score_default}, "
            f"stories per cycle: {config.stories_per_poll}"
        )

        # Launch the update worker
        asyncio.create_task(update_worker(app.bot))

        # Also run once immediately on startup (with a short delay)
        asyncio.get_running_loop().call_later(
            5,
            lambda: asyncio.ensure_future(poll_and_post(app))
        )

    app.post_init = _start_scheduler

    return scheduler
