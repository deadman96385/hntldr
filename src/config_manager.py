"""Runtime config editing and .env persistence helpers."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

from config import Config, config


_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
_ENV_WRITE_LOCK = threading.Lock()


@dataclass(frozen=True)
class ConfigKeySpec:
    env_key: str
    attr_name: str
    value_type: str
    category: str
    label: str
    restart_required: bool
    min_value: int | None = None
    max_value: int | None = None


EDITABLE_KEY_SPECS: dict[str, ConfigKeySpec] = {
    "SHOW_FLAMES": ConfigKeySpec(
        env_key="SHOW_FLAMES",
        attr_name="show_flames",
        value_type="bool",
        category="flames",
        label="Show flames",
        restart_required=False,
    ),
    "FLAME_THRESHOLD_1": ConfigKeySpec(
        env_key="FLAME_THRESHOLD_1",
        attr_name="flame_threshold_1",
        value_type="int",
        category="flames",
        label="Flame 1 threshold",
        restart_required=False,
        min_value=0,
    ),
    "FLAME_THRESHOLD_2": ConfigKeySpec(
        env_key="FLAME_THRESHOLD_2",
        attr_name="flame_threshold_2",
        value_type="int",
        category="flames",
        label="Flame 2 threshold",
        restart_required=False,
        min_value=0,
    ),
    "FLAME_THRESHOLD_3": ConfigKeySpec(
        env_key="FLAME_THRESHOLD_3",
        attr_name="flame_threshold_3",
        value_type="int",
        category="flames",
        label="Flame 3 threshold",
        restart_required=False,
        min_value=0,
    ),
    "POLL_INTERVAL_MINUTES": ConfigKeySpec(
        env_key="POLL_INTERVAL_MINUTES",
        attr_name="poll_interval_minutes",
        value_type="int",
        category="polling",
        label="Poll interval (minutes)",
        restart_required=True,
        min_value=1,
    ),
    "STORIES_PER_POLL": ConfigKeySpec(
        env_key="STORIES_PER_POLL",
        attr_name="stories_per_poll",
        value_type="int",
        category="polling",
        label="Stories per poll",
        restart_required=True,
        min_value=1,
    ),
    "TELEGRAM_CHANNEL_ID": ConfigKeySpec(
        env_key="TELEGRAM_CHANNEL_ID",
        attr_name="telegram_channel_id",
        value_type="str",
        category="posting",
        label="Channel ID",
        restart_required=True,
    ),
    "WHITELISTED_CHAT_IDS": ConfigKeySpec(
        env_key="WHITELISTED_CHAT_IDS",
        attr_name="whitelisted_chat_ids",
        value_type="int_set",
        category="whitelist",
        label="Whitelisted chat IDs",
        restart_required=False,
    ),
    "ADMIN_USER_ID": ConfigKeySpec(
        env_key="ADMIN_USER_ID",
        attr_name="admin_user_ids",
        value_type="int_set",
        category="admins",
        label="Admin user IDs",
        restart_required=False,
    ),
    "MIN_SCORE_DEFAULT": ConfigKeySpec(
        env_key="MIN_SCORE_DEFAULT",
        attr_name="min_score_default",
        value_type="int",
        category="min_scores",
        label="Min score default",
        restart_required=True,
        min_value=-1,
    ),
    "MIN_SCORE_SHOW_HN": ConfigKeySpec(
        env_key="MIN_SCORE_SHOW_HN",
        attr_name="min_score_show_hn",
        value_type="int",
        category="min_scores",
        label="Min score show_hn",
        restart_required=True,
        min_value=-1,
    ),
    "MIN_SCORE_ASK_HN": ConfigKeySpec(
        env_key="MIN_SCORE_ASK_HN",
        attr_name="min_score_ask_hn",
        value_type="int",
        category="min_scores",
        label="Min score ask_hn",
        restart_required=True,
        min_value=-1,
    ),
    "MIN_SCORE_LAUNCH_HN": ConfigKeySpec(
        env_key="MIN_SCORE_LAUNCH_HN",
        attr_name="min_score_launch_hn",
        value_type="int",
        category="min_scores",
        label="Min score launch_hn",
        restart_required=True,
        min_value=-1,
    ),
    "MIN_SCORE_TELL_HN": ConfigKeySpec(
        env_key="MIN_SCORE_TELL_HN",
        attr_name="min_score_tell_hn",
        value_type="int",
        category="min_scores",
        label="Min score tell_hn",
        restart_required=True,
        min_value=-1,
    ),
    "MIN_SCORE_JOBS": ConfigKeySpec(
        env_key="MIN_SCORE_JOBS",
        attr_name="min_score_jobs",
        value_type="int",
        category="min_scores",
        label="Min score jobs",
        restart_required=True,
        min_value=-1,
    ),
}


CATEGORY_ORDER = {
    "flames": ["SHOW_FLAMES", "FLAME_THRESHOLD_1", "FLAME_THRESHOLD_2", "FLAME_THRESHOLD_3"],
    "polling": ["POLL_INTERVAL_MINUTES", "STORIES_PER_POLL"],
    "posting": ["TELEGRAM_CHANNEL_ID"],
    "whitelist": ["WHITELISTED_CHAT_IDS"],
    "admins": ["ADMIN_USER_ID"],
    "min_scores": [
        "MIN_SCORE_DEFAULT",
        "MIN_SCORE_SHOW_HN",
        "MIN_SCORE_ASK_HN",
        "MIN_SCORE_LAUNCH_HN",
        "MIN_SCORE_TELL_HN",
        "MIN_SCORE_JOBS",
    ],
}


def _parse_bool(raw: str) -> bool:
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError("Expected boolean (1/0, true/false, yes/no, on/off)")


def _serialize_value(spec: ConfigKeySpec, value: bool | int | str | set[int]) -> str:
    if spec.value_type == "bool":
        return "1" if bool(value) else "0"
    if spec.value_type == "int":
        return str(int(value))
    if spec.value_type == "str":
        return str(value)
    if spec.value_type == "int_set":
        return ",".join(str(v) for v in sorted(value))
    raise ValueError(f"Unsupported value type: {spec.value_type}")


def _parse_input(spec: ConfigKeySpec, raw: str):
    text = raw.strip()
    if spec.value_type == "bool":
        return _parse_bool(text)
    if spec.value_type == "int":
        try:
            value = int(text)
        except ValueError as exc:
            raise ValueError("Expected an integer") from exc
        if spec.min_value is not None and value < spec.min_value:
            raise ValueError(f"Value must be >= {spec.min_value}")
        if spec.max_value is not None and value > spec.max_value:
            raise ValueError(f"Value must be <= {spec.max_value}")
        return value
    if spec.value_type == "str":
        return text
    if spec.value_type == "int_set":
        if not text:
            return set()
        values: set[int] = set()
        for token in text.split(","):
            token = token.strip()
            if not token:
                continue
            try:
                values.add(int(token))
            except ValueError as exc:
                raise ValueError(f"Invalid integer value: {token}") from exc
        return values
    raise ValueError(f"Unsupported value type: {spec.value_type}")


def _load_env_lines() -> list[str]:
    if _ENV_PATH.exists():
        return _ENV_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
    return []


def _write_env_lines(lines: list[str]):
    _ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=_ENV_PATH.parent, newline="\n") as tmp:
        tmp.writelines(lines)
        tmp_path = Path(tmp.name)
    tmp_path.replace(_ENV_PATH)


def _upsert_env_key(key: str, value: str):
    with _ENV_WRITE_LOCK:
        lines = _load_env_lines()
        output: list[str] = []
        found = False
        for line in lines:
            stripped = line.lstrip()
            if stripped.startswith(f"{key}="):
                output.append(f"{key}={value}\n")
                found = True
            else:
                output.append(line)

        if not found:
            if output and not output[-1].endswith("\n"):
                output[-1] = output[-1] + "\n"
            output.append(f"{key}={value}\n")

        _write_env_lines(output)


class ConfigManager:
    """Provides runtime config edits and persistence to .env."""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def has_admins(self) -> bool:
        return bool(self.cfg.admin_user_ids)

    def claim_first_admin(self, user_id: int) -> bool:
        if self.has_admins():
            return False
        self.set_value("ADMIN_USER_ID", {user_id})
        return True

    def keys_for_category(self, category: str) -> list[ConfigKeySpec]:
        keys = CATEGORY_ORDER.get(category, [])
        return [EDITABLE_KEY_SPECS[key] for key in keys]

    def get_value(self, env_key: str):
        spec = EDITABLE_KEY_SPECS[env_key]
        return getattr(self.cfg, spec.attr_name)

    def set_value(self, env_key: str, value) -> bool:
        spec = EDITABLE_KEY_SPECS[env_key]
        setattr(self.cfg, spec.attr_name, value)
        _upsert_env_key(spec.env_key, _serialize_value(spec, value))
        return spec.restart_required

    def set_from_input(self, env_key: str, raw_value: str) -> bool:
        spec = EDITABLE_KEY_SPECS[env_key]
        parsed = _parse_input(spec, raw_value)
        return self.set_value(env_key, parsed)

    def add_to_set(self, env_key: str, value: int) -> bool:
        spec = EDITABLE_KEY_SPECS[env_key]
        if spec.value_type != "int_set":
            raise ValueError(f"{env_key} is not a set value")
        current = set(self.get_value(env_key))
        current.add(value)
        return self.set_value(env_key, current)

    def remove_from_set(self, env_key: str, value: int) -> bool:
        spec = EDITABLE_KEY_SPECS[env_key]
        if spec.value_type != "int_set":
            raise ValueError(f"{env_key} is not a set value")
        current = set(self.get_value(env_key))
        current.discard(value)
        return self.set_value(env_key, current)


config_manager = ConfigManager(config)
