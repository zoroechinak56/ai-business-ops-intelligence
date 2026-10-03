"""Reusable application logging configuration."""

import logging

from src.config import settings


def setup_logging(level: str | None = None) -> None:
    """Configure standard-library logging once for the current process."""
    level_name = (level or settings.log_level).upper()
    numeric_level = getattr(logging, level_name, None)
    if not isinstance(numeric_level, int):
        raise ValueError(f"Unsupported log level: {level_name}")

    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def get_logger(name: str) -> logging.Logger:
    """Return a named logger after ensuring application logging is configured."""
    setup_logging()
    return logging.getLogger(name)