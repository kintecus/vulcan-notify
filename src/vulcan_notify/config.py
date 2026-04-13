"""Configuration via environment variables and .env file."""

from pathlib import Path

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

    # Polling
    poll_interval: int = 300  # seconds

    # Storage
    db_path: Path = Path("vulcan_notify.db")

    # Message filtering (comma-separated sender names, empty = show all)
    message_sender_whitelist: list[str] = []

    # LLM (optional - all providers use OpenAI-compatible API)
    llm_base_url: str = "https://api.cerebras.ai/v1"
    llm_api_key: str | None = None
    llm_model: str = "qwen-3-235b-a22b-instruct-2507"
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

    # Logging
    log_level: str = "INFO"


settings = Settings()
