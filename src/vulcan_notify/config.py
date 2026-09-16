"""Configuration via environment variables and .env file."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    # Session file path (cookies from browser login)
    session_file: Path = Path("session.json")

    # Auto-login credentials (optional - enables headless auth)
    # Can also be read from macOS Keychain (service: vulcan-notify)
    vulcan_login: str | None = None
    vulcan_password: str | None = None

    # ntfy.sh
    ntfy_topic: str = "vulcan-notify"
    ntfy_server: str = "https://ntfy.sh"

    # Sync
    sync_attendance_days: int = 90  # how far back to sync attendance
    sync_message_backfill_batch: int = 10  # messages to backfill per cycle
    sync_history_keep_days: int = 90  # sync_runs / sync_sections retention

    # Polling. sync-loop.sh reads POLL_INTERVAL from the environment, so this is the
    # single source of truth for both the loop and the staleness threshold below.
    poll_interval: int = 1800  # seconds

    # Quiet window, in the container's local time. sync-loop.sh reads these same two
    # env vars to decide when to pause; they live here too so /api/health can subtract
    # the pause from data age. Without that the two disagreed and a normal overnight
    # sleep read as an outage -- see freshness.py.
    # Evaluated in this zone, not the container's. The LXC runs on UTC, which quietly
    # turned a 00:00-05:00 window into 02:00-07:00 local -- the loop went quiet two
    # hours after midnight and resumed half an hour before the kids left, so the
    # morning schedule was always five hours stale.
    quiet_hours_tz: str = "Europe/Warsaw"
    quiet_hours_start: int = Field(default=0, ge=0, le=23)
    quiet_hours_end: int = Field(default=5, ge=0, le=23)

    # Data older than this is reported stale by /api/health and the `_meta` block.
    # Two missed cycles: one late sync is normal, two means something is wrong.
    # Measured with quiet hours excluded, so this stays a tight daytime threshold.
    stale_after_seconds: int = 3600

    # Storage
    db_path: Path = Path("vulcan_notify.db")

    # Message filtering (comma-separated sender names, empty = show all)
    message_sender_whitelist: list[str] = []

    # LLM (optional - all providers use OpenAI-compatible API)
    llm_base_url: str = "https://api.cerebras.ai/v1"
    llm_api_key: str | None = None
    llm_model: str = "gpt-oss-120b"
    prompts_file: Path = Path("prompts.toml")

    # Calendar (macOS Calendar via AppleScript, empty map = disabled)
    calendar_map: dict[str, str] = {}  # student name -> calendar name
    calendar_reminder_hours: int = 24  # alarm trigger (hours before event)

    # MQTT (optional - publish changes to Mosquitto broker)
    mqtt_enabled: bool = False
    mqtt_broker: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_topic_prefix: str = "school"
    mqtt_status_suffix: str = "status"  # retained heartbeat topic under the prefix

    # Short display names for push notifications (full name -> nickname).
    # Keeps the notification title terse on a watch / lock screen.
    display_name_map: dict[str, str] = {}

    # Logging
    log_level: str = "INFO"


settings = Settings()
