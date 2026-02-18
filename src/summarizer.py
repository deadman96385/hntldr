"""
Summarizer — the core of hntldr.
Calls the configured LLM to produce a concise hook summary.
"""

import logging
import re
from dataclasses import dataclass

from config import config
from llm import get_provider

logger = logging.getLogger("hntldr.summarizer")


@dataclass
class Summary:
    hook: str


SUMMARY_PROMPT = """You are summarizing Hacker News stories for a technical audience that is tired of clickbait and vague titles.

Story title: {title}
Article URL: {url}
{content_section}
HN score: {score} points | {comments} comments

Write a summary in exactly this format:
HOOK: 1-2 sentences (prefer 1; 20-45 words total) that plainly state what is being posted and why it matters.

Rules:
- No "This article...", "The author...", "A new...", or filler openers
- No hedging ("might", "could", "seems to")
- Be direct and slightly opinionated — write like a smart friend texting you
- If it's a tool/project: name what it does concretely
- If it's an essay/opinion: state the actual argument
- If it's news: say what changed and who it affects

Output ONLY the HOOK: line. Nothing else."""


CONTENT_SECTION_TEMPLATE = "Article content (first {chars} chars):\n{content}"


def _parse_summary(raw: str, title: str) -> Summary:
    """Parse HOOK format from LLM output. Falls back gracefully."""
    hook = ""

    for line in raw.strip().splitlines():
        line = line.strip()
        if line.upper().startswith("HOOK:"):
            hook = line[5:].strip().strip('"\'')

    # If parsing failed, treat the whole response as plain text
    if not hook:
        text = raw.strip().strip('"\'')
        if text:
            sentences = re.split(r'(?<=[.!?])\s+', text)
            hook = sentences[0]

    # Keep hook concise: at most two sentences
    if hook:
        sentences = re.split(r'(?<=[.!?])\s+', hook)
        hook = " ".join(s for s in sentences[:2] if s).strip()

    # Last resort fallback
    if not hook:
        hook = _title_fallback(title)

    return Summary(hook=hook)


async def summarize_article(
    title: str,
    url: str,
    content: str = "",
    score: int = 0,
    comments: int = 0,
) -> Summary:
    """Generate a concise hook summary using the configured LLM."""
    if not title:
        return Summary(hook="No title available.")

    content_section = ""
    if content and len(content) > 100:
        content_section = CONTENT_SECTION_TEMPLATE.format(
            chars=len(content),
            content=content
        )

    prompt = SUMMARY_PROMPT.format(
        title=title,
        url=url or "N/A",
        content_section=content_section,
        score=score,
        comments=comments,
    )

    try:
        provider = get_provider()
        raw = await provider.complete(
            prompt=prompt,
            max_tokens=config.llm_max_tokens,
            temperature=0.4,
        )

        if len(raw) < 15:
            logger.warning(f"Summary too short ({len(raw)} chars), using title fallback")
            return Summary(hook=_title_fallback(title))

        return _parse_summary(raw, title)

    except Exception as e:
        logger.error(f"Summarizer error: {e}", exc_info=True)
        return Summary(hook=_title_fallback(title))


def _title_fallback(title: str) -> str:
    """Last resort: clean up the title a bit."""
    title = re.sub(r'\s*\(\d{4}\)\s*$', '', title)  # "(2024)" year suffixes
    title = re.sub(r'\s*\[pdf\]\s*$', ' (PDF)', title, flags=re.IGNORECASE)
    return title
