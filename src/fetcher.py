"""
Fetcher — pulls data from HN Firebase API and scrapes article content.
Uses trafilatura for best-in-class article extraction.
"""

import asyncio
import html as html_mod
import logging
import re
from typing import Optional
from urllib.parse import urlparse

import aiohttp
import trafilatura

from config import config

logger = logging.getLogger("hntldr.fetcher")

HN_API_BASE = "https://hacker-news.firebaseio.com/v0"
HN_ALGOLIA_BASE = "https://hn.algolia.com/api/v1"

_http_session: aiohttp.ClientSession | None = None
_http_session_lock = asyncio.Lock()


async def get_http_session() -> aiohttp.ClientSession:
    """Return a shared aiohttp session for all outbound requests."""
    global _http_session

    if _http_session is not None and not _http_session.closed:
        return _http_session

    async with _http_session_lock:
        if _http_session is None or _http_session.closed:
            timeout = aiohttp.ClientTimeout(total=config.request_timeout)
            connector = aiohttp.TCPConnector(limit=50, ttl_dns_cache=300)
            _http_session = aiohttp.ClientSession(timeout=timeout, connector=connector)
            logger.info("Created shared HTTP session")

    return _http_session


async def close_http_session():
    """Close the shared aiohttp session on shutdown."""
    global _http_session

    if _http_session is None:
        return

    async with _http_session_lock:
        if _http_session is not None and not _http_session.closed:
            await _http_session.close()
            logger.info("Closed shared HTTP session")
        _http_session = None


def _should_skip_url(url: str) -> bool:
    """Decide whether a URL is worth scraping."""
    if not url:
        return True

    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/")

    # Always skip these domains
    always_skip = {"twitter.com", "x.com", "youtube.com", "youtu.be", "reddit.com",
                   "news.ycombinator.com"}
    for domain in always_skip:
        if host == domain or host.endswith("." + domain):
            return True

    # arxiv abstracts
    if "arxiv.org" in host and path.startswith("/abs/"):
        return True

    # GitHub special handling
    if host == "github.com":
        # Always allow: /blog/*
        if path.startswith("/blog"):
            return False

        parts = [p for p in path.split("/") if p]

        # Need at least user/repo to apply repo-level rules
        if len(parts) < 2:
            return True  # e.g. github.com/user -- skip

        if len(parts) == 2:
            return True  # Repo root: github.com/user/repo

        action = parts[2] if len(parts) > 2 else ""

        # Skip tree browsing
        if action == "tree":
            return True

        # Blob: allow .md files only
        if action == "blob":
            return not path.endswith(".md")

        # Skip issues, PRs, actions, releases, commits, compare
        if action in ("issues", "pull", "pulls", "actions", "releases",
                      "commits", "commit", "compare"):
            return True

        return False

    # GitHub Pages, gists, raw -- always allow
    if host.endswith(".github.io") or host == "gist.github.com" or host == "raw.githubusercontent.com":
        return False

    return False


async def fetch_hn_item(item_id: str) -> Optional[dict]:
    """
    Fetch a single HN item. Returns normalized dict with:
    title, url, points, num_comments, by, time
    """
    # Try Algolia first (richer data, single call)
    try:
        session = await get_http_session()
        async with session.get(f"{HN_ALGOLIA_BASE}/items/{item_id}") as resp:
            if resp.status == 200:
                data = await resp.json()
                comments = data.get("num_comments")
                if comments is None:
                    comments = await _fetch_firebase_comment_count(item_id)
                return {
                    "id": str(item_id),
                    "title": data.get("title") or data.get("story_title", ""),
                    "url": data.get("url") or data.get("story_url", ""),
                    "points": data.get("points") or 0,
                    "num_comments": comments or 0,
                    "by": data.get("author", ""),
                    "text": data.get("text", ""),  # for Ask HN / text posts
                    "type": data.get("type", "story"),
                }
            logger.warning(f"Algolia fetch returned status {resp.status} for {item_id}")
    except Exception as e:
        logger.warning(f"Algolia fetch failed for {item_id}: {e}, falling back to Firebase")

    # Fallback: Firebase API
    try:
        session = await get_http_session()
        async with session.get(f"{HN_API_BASE}/item/{item_id}.json") as resp:
            if resp.status == 200:
                data = await resp.json()
                if not data:
                    return None
                if data.get("dead") or data.get("deleted"):
                    logger.debug(f"Skipping dead/deleted item {item_id}")
                    return None
                return {
                    "id": str(item_id),
                    "title": data.get("title", ""),
                    "url": data.get("url", ""),
                    "points": data.get("score", 0),
                    "num_comments": data.get("descendants") or len(data.get("kids", [])),
                    "by": data.get("by", ""),
                    "text": data.get("text", ""),
                    "type": data.get("type", "story"),
                }
            logger.warning(f"Firebase fetch returned status {resp.status} for {item_id}")
    except Exception as e:
        logger.error(f"Firebase fetch failed for {item_id}: {e}")

    return None


async def _fetch_firebase_comment_count(item_id: str) -> int | None:
    """Fetch comment count from Firebase (`kids`) as a consistency fallback."""
    try:
        session = await get_http_session()
        async with session.get(f"{HN_API_BASE}/item/{item_id}.json") as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            if not data:
                return None
            kids = data.get("kids")
            if not isinstance(kids, list):
                return 0
            return len(kids)
    except Exception:
        return None


async def fetch_top_story_ids(limit: int = 30) -> list[int]:
    """Fetch the current top story IDs from HN."""
    try:
        session = await get_http_session()
        async with session.get(f"{HN_API_BASE}/topstories.json") as resp:
            if resp.status == 200:
                ids = await resp.json()
                return ids[:limit]
            logger.warning(f"topstories returned status {resp.status}")
    except Exception as e:
        logger.error(f"Failed to fetch top stories: {e}")
    return []


def detect_topic(title: str, item_type: str = "story") -> str:
    """Detect the topic of an HN story from its title and type.

    Returns one of: "show_hn", "ask_hn", "launch_hn", "tell_hn", "jobs", "default".
    """
    if item_type == "job":
        return "jobs"
    lower = title.lower().strip()
    if lower.startswith("show hn:"):
        return "show_hn"
    if lower.startswith("ask hn:"):
        return "ask_hn"
    if lower.startswith("launch hn:"):
        return "launch_hn"
    if lower.startswith("tell hn:"):
        return "tell_hn"
    return "default"


async def fetch_top_stories_with_details(limit: int = 30) -> list[dict]:
    """Fetch top story IDs and their details. Returns all stories; caller filters."""
    ids = await fetch_top_story_ids(limit)
    if not ids:
        return []

    # Fetch details concurrently (batched to avoid hammering the API)
    batch_size = 10
    stories = []
    for i in range(0, len(ids), batch_size):
        batch = ids[i:i + batch_size]
        tasks = [fetch_hn_item(str(hn_id)) for hn_id in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, dict) and r.get("title"):
                stories.append(r)
        await asyncio.sleep(0.2)  # gentle rate limiting

    return stories


async def fetch_article_content(url: str) -> str:
    """
    Fetch and extract clean article text from a URL.
    Returns empty string on failure (LLM will summarize from title only).
    """
    if _should_skip_url(url):
        if url:
            logger.debug(f"Skipping scrape for URL: {url}")
        return ""

    try:
        # Run trafilatura in a thread (it's synchronous)
        loop = asyncio.get_running_loop()
        content = await loop.run_in_executor(
            None,
            _scrape_with_trafilatura,
            url
        )
        if content:
            # Trim to configured max to keep LLM costs predictable
            return content[:config.max_article_chars]
        return ""
    except Exception as e:
        logger.warning(f"Article fetch failed for {url}: {e}")
        return ""


def _scrape_with_trafilatura(url: str) -> str:
    """Synchronous scraper — runs in thread pool."""
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return ""
        text = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=False,
            no_fallback=False,
            favor_precision=True,
        )
        return text or ""
    except Exception as e:
        logger.debug(f"trafilatura error: {e}")
        return ""


def _strip_html(text: str) -> str:
    """Strip HTML tags, unescape entities, and normalize whitespace."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_mod.unescape(text)
    return " ".join(text.split())


async def fetch_first_comment(item_id: str) -> Optional[dict]:
    """Fetch the top comment for an HN item via Algolia. Returns {author, text} or None."""
    try:
        session = await get_http_session()
        async with session.get(f"{HN_ALGOLIA_BASE}/items/{item_id}") as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            children = data.get("children")
            if not children:
                return None
            first = children[0]
            raw_text = first.get("text") or ""
            author = first.get("author") or "anon"
            clean = _strip_html(raw_text)
            if not clean:
                return None
            # Truncate to fit Telegram popup (200 char limit, leave room for author)
            prefix = f"{author}: "
            max_text = 200 - len(prefix)
            if len(clean) > max_text:
                clean = clean[: max_text - 1] + "\u2026"
            return {"author": author, "text": clean}
    except Exception as e:
        logger.warning(f"Failed to fetch first comment for {item_id}: {e}")
        return None
