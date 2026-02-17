"""Admin error notifications with deduplication."""

import hashlib
import html
import logging
import time

from config import config

logger = logging.getLogger("hntldr.errors")

# {hash_prefix: timestamp} for dedup
_sent: dict[str, float] = {}
_DEDUP_TTL = 3600  # 1 hour


async def notify_admin(bot, error_msg: str, context: str = ""):
    """Send an error DM to all admin users. Deduplicates within 1 hour."""
    if not config.admin_user_ids:
        return

    dedup_payload = f"{context}\n{error_msg}"
    key = hashlib.sha256(dedup_payload.encode()).hexdigest()[:16]
    now = time.time()

    # Prune stale entries
    stale = [k for k, ts in _sent.items() if now - ts > _DEDUP_TTL]
    for k in stale:
        del _sent[k]

    # Dedup check
    if key in _sent:
        return
    _sent[key] = now

    # Keep in-memory dedup map bounded
    if len(_sent) > 1000:
        oldest_key = min(_sent, key=_sent.get)
        del _sent[oldest_key]

    ctx = f"\n<b>Context:</b> {html.escape(context)}" if context else ""
    text = f"<b>hntldr error</b>{ctx}\n<pre>{html.escape(error_msg[:1500])}</pre>"

    for admin_id in config.admin_user_ids:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=text,
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning(f"Failed to notify admin {admin_id}: {e}")
