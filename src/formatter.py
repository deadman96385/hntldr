"""
Formatter — builds HTML Telegram messages with inline keyboard buttons.

Output format:
  <b>Title</b>

  Hook sentence.

  <b>142 points</b>
"""

import html

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from summarizer import Summary

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
    text = _build_text(title, summary, score)
    reply_markup = _build_buttons(url, hn_url, comments)

    return {
        "text": text,
        "reply_markup": reply_markup,
        "title": title,
        "url": url,
        "hn_url": hn_url,
        "hn_id": hn_id,
        "score": score,
        "comments": comments,
        "hook": summary.hook,
    }


def _build_text(title: str, summary: Summary, score: int) -> str:
    """Build the HTML message text."""
    safe_title = html.escape(title)
    safe_hook = html.escape(summary.hook) if summary.hook else ""

    lines = [f"<b>{safe_title}</b>"]

    if safe_hook:
        lines.append("")
        lines.append(safe_hook)

    lines.append("")
    lines.append(f"<b>{score} points</b>")

    return "\n".join(lines)


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


def format_update_text(title: str, summary: Summary, score: int) -> str:
    """Rebuild message text for edits without re-summarizing."""
    return _build_text(title, summary, score)
