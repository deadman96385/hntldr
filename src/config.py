"""Configuration — loaded from environment variables or .env file."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root if present
load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env", override=False)


def _parse_int_csv(raw: str, label: str) -> set[int]:
    """Parse comma-separated integer values into a set."""
    if not raw.strip():
        return set()

    parsed: set[int] = set()
    invalid: list[str] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            parsed.add(int(token))
        except ValueError:
            invalid.append(token)

    if invalid:
        raise ValueError(f"{label} contains invalid values: {', '.join(invalid)}")

    return parsed


def _parse_bool(raw: str, default: bool = False) -> bool:
    """Parse a boolean environment variable value."""
    value = raw.strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


@dataclass
class Config:
    # --- Required ---
    telegram_token: str = field(default_factory=lambda: os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    llm_api_key: str = field(default_factory=lambda: os.environ.get("LLM_API_KEY", ""))

    # --- LLM provider ---
    llm_provider: str = field(default_factory=lambda: os.environ.get("LLM_PROVIDER", "claude"))
    llm_model: str = field(default_factory=lambda: os.environ.get("LLM_MODEL", "claude-haiku-4-5-20251001"))
    llm_max_tokens: int = field(default_factory=lambda: int(os.environ.get("LLM_MAX_TOKENS", "300")))

    # --- Admin (comma-separated user IDs, e.g. "1234,4321,5555") ---
    admin_user_ids: set[int] = field(default_factory=lambda: _parse_int_csv(os.environ.get("ADMIN_USER_ID", ""), "ADMIN_USER_ID"))

    # --- Whitelisted channels/groups where anyone can use the bot ---
    whitelisted_chat_ids: set[int] = field(default_factory=lambda: _parse_int_csv(os.environ.get("WHITELISTED_CHAT_IDS", ""), "WHITELISTED_CHAT_IDS"))

    # --- Optional ---
    telegram_channel_id: str = field(default_factory=lambda: os.environ.get("TELEGRAM_CHANNEL_ID", ""))

    poll_interval_minutes: int = field(
        default_factory=lambda: int(os.environ.get("POLL_INTERVAL_MINUTES", "60"))
    )

    # --- Flame emoji thresholds (score for 🔥, 🔥🔥, 🔥🔥🔥) ---
    show_flames: bool = field(default_factory=lambda: _parse_bool(os.environ.get("SHOW_FLAMES", "1"), default=True))
    flame_threshold_1: int = field(default_factory=lambda: int(os.environ.get("FLAME_THRESHOLD_1", "50")))
    flame_threshold_2: int = field(default_factory=lambda: int(os.environ.get("FLAME_THRESHOLD_2", "100")))
    flame_threshold_3: int = field(default_factory=lambda: int(os.environ.get("FLAME_THRESHOLD_3", "200")))
    min_score_default: int = field(default_factory=lambda: int(os.environ.get("MIN_SCORE_DEFAULT", "100")))
    min_score_show_hn: int = field(default_factory=lambda: int(os.environ.get("MIN_SCORE_SHOW_HN", "50")))
    min_score_ask_hn: int = field(default_factory=lambda: int(os.environ.get("MIN_SCORE_ASK_HN", "100")))
    min_score_launch_hn: int = field(default_factory=lambda: int(os.environ.get("MIN_SCORE_LAUNCH_HN", "75")))
    min_score_tell_hn: int = field(default_factory=lambda: int(os.environ.get("MIN_SCORE_TELL_HN", "100")))
    min_score_jobs: int = field(default_factory=lambda: int(os.environ.get("MIN_SCORE_JOBS", "-1")))

    stories_per_poll: int = field(
        default_factory=lambda: int(os.environ.get("STORIES_PER_POLL", "3"))
    )

    db_path: str = field(
        default_factory=lambda: os.environ.get("DB_PATH", "hntldr.db")
    )

    max_article_chars: int = field(
        default_factory=lambda: int(os.environ.get("MAX_ARTICLE_CHARS", "4000"))
    )

    request_timeout: int = field(
        default_factory=lambda: int(os.environ.get("REQUEST_TIMEOUT", "15"))
    )

    def score_threshold_for(self, topic: str) -> int:
        """Return the score threshold for a detected topic."""
        mapping = {
            "show_hn": self.min_score_show_hn,
            "ask_hn": self.min_score_ask_hn,
            "launch_hn": self.min_score_launch_hn,
            "tell_hn": self.min_score_tell_hn,
            "jobs": self.min_score_jobs,
        }
        return mapping.get(topic, self.min_score_default)

    def is_admin(self, user_id: int) -> bool:
        """Check if a user ID is in the admin set. Unrestricted if no admins configured."""
        if not self.admin_user_ids:
            return True
        return user_id in self.admin_user_ids

    def is_whitelisted_chat(self, chat_id: int) -> bool:
        """Check if a chat is whitelisted. Always True for DMs (positive IDs)."""
        if chat_id > 0:
            return True
        return chat_id in self.whitelisted_chat_ids

    def validate(self):
        """Fail fast on missing or invalid configuration."""
        errors = []
        if not self.telegram_token:
            errors.append("TELEGRAM_BOT_TOKEN is not set")
        if not self.llm_api_key:
            errors.append("LLM_API_KEY is not set")
        if self.llm_provider not in ("claude", "openai"):
            errors.append(f"LLM_PROVIDER must be 'claude' or 'openai', got '{self.llm_provider}'")
        if not self.llm_model:
            errors.append("LLM_MODEL is not set")
        if self.llm_max_tokens <= 0:
            errors.append("LLM_MAX_TOKENS must be > 0")
        if self.poll_interval_minutes <= 0:
            errors.append("POLL_INTERVAL_MINUTES must be > 0")
        if self.stories_per_poll <= 0:
            errors.append("STORIES_PER_POLL must be > 0")
        if self.max_article_chars <= 0:
            errors.append("MAX_ARTICLE_CHARS must be > 0")
        if self.request_timeout <= 0:
            errors.append("REQUEST_TIMEOUT must be > 0")
        if errors:
            raise SystemExit("Configuration error:\n  " + "\n  ".join(errors))


try:
    config = Config()
    config.validate()
except ValueError as e:
    raise SystemExit(f"Configuration error:\n  {e}")
