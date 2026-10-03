"""Environment-backed application settings."""

from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    """Typed settings loaded from environment variables and an optional .env file."""

    database_url: str | None
    llm_api_key: str | None
    llm_model: str
    data_raw_dir: Path | None
    data_processed_dir: Path | None
    log_level: str

    @classmethod
    def from_environment(cls) -> "Settings":
        """Load settings without requiring local secrets to exist."""
        load_dotenv()

        raw_dir = os.getenv("DATA_RAW_DIR")
        processed_dir = os.getenv("DATA_PROCESSED_DIR")

        return cls(
            database_url=os.getenv("DATABASE_URL"),
            llm_api_key=os.getenv("LLM_API_KEY"),
            llm_model=os.getenv("LLM_MODEL", "claude-sonnet-5-5"),
            data_raw_dir=Path(raw_dir).expanduser() if raw_dir else None,
            data_processed_dir=(
                Path(processed_dir).expanduser() if processed_dir else None
            ),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )


settings = Settings.from_environment()