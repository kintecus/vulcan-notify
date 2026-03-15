"""Configuration via environment variables and .env file."""

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    # Session file path (cookies from browser login)
    session_file: Path = Path("session.json")

    # ntfy.sh
    ntfy_topic: str = "vulcan-notify"
    ntfy_server: str = "https://ntfy.sh"

    # Polling
    poll_interval: int = 300  # seconds

    # Storage
    db_path: Path = Path("vulcan_notify.db")

    # Message filtering (comma-separated sender names, empty = show all)
    message_sender_whitelist: list[str] = []

    # Logging
    log_level: str = "INFO"


settings = Settings()
