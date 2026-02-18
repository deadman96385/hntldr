"""
Formatter — builds HTML Telegram messages with inline keyboard buttons.

Output format:
  <b>Title</b>

  Hook sentence.

  <b>142 points</b>
"""

import html

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions

from summarizer import Summary
from config import config

HN_ITEM_URL = "https://news.ycombinator.com/item?id={hn_id}"


def format_telegram_message(
    summary: Summary,
    title: str,
    url: str,
    hn_id: str,
    score: int = 0,
    comments: int = 0,
) -> dict:
    """
    Returns a dict with 'text', 'reply_markup' (InlineKeyboardMarkup), and metadata.
    """
    hn_url = HN_ITEM_URL.format(hn_id=hn_id)
    link_url = url or hn_url
    text = _build_text(title, summary, score, link_url)
    reply_markup = _build_buttons(url, hn_url, comments)
    link_preview_options = _build_link_preview(link_url)

    return {
        "text": text,
        "reply_markup": reply_markup,
        "link_preview_options": link_preview_options,
        "title": title,
        "url": url,
        "hn_url": hn_url,
        "hn_id": hn_id,
        "score": score,
        "comments": comments,
        "hook": summary.hook,
    }


def _build_text(title: str, summary: Summary, score: int, link_url: str = "") -> str:
    """Build the HTML message text."""
    safe_title = html.escape(title)
    safe_hook = html.escape(summary.hook) if summary.hook else ""

    if link_url:
        lines = [f'<b><a href="{html.escape(link_url)}">{safe_title}</a></b>']
    else:
        lines = [f"<b>{safe_title}</b>"]

    if safe_hook:
        lines.append("")
        lines.append(safe_hook)

    flame_text = _score_flames(score)
    if flame_text:
        lines.append("")
        lines.append(flame_text)

    return "\n".join(lines)


def _score_flames(score: int) -> str:
    """Convert score to flame emojis indicating popularity."""
    if not config.show_flames:
        return ""
    if score >= config.flame_threshold_3:
        return "\U0001f525" * 3
    if score >= config.flame_threshold_2:
        return "\U0001f525" * 2
    if score >= config.flame_threshold_1:
        return "\U0001f525"
    return ""


def _build_link_preview(url: str) -> LinkPreviewOptions:
    """Build link preview options for URL embeds."""
    return LinkPreviewOptions(
        url=url,
        prefer_small_media=True,
        show_above_text=False,
    )


def _build_buttons(url: str, hn_url: str, comments: int) -> InlineKeyboardMarkup:
    """Build inline keyboard buttons."""
    if url:
        buttons = [
            InlineKeyboardButton("Read", url=url),
            InlineKeyboardButton(f"{comments} Comments", url=hn_url),
        ]
    else:
        buttons = [InlineKeyboardButton("Read on HN", url=hn_url)]
    return InlineKeyboardMarkup([buttons])


def build_update_buttons(url: str, hn_url: str, comments: int) -> InlineKeyboardMarkup:
    """Public helper for updater — rebuilds buttons with fresh comment count."""
    return _build_buttons(url, hn_url, comments)


def format_update_text(title: str, summary: Summary, score: int, link_url: str = "") -> str:
    """Rebuild message text for edits without re-summarizing."""
    return _build_text(title, summary, score, link_url)


def build_update_link_preview(link_url: str) -> LinkPreviewOptions:
    """Public helper for updater — rebuilds link preview options."""
    return _build_link_preview(link_url)
