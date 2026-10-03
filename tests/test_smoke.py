"""Import smoke tests for project foundation modules."""

from src import config, logger


def test_foundation_modules_import() -> None:
    """Ensure configuration and logging modules import successfully."""
    assert config.settings is not None
    assert callable(logger.setup_logging)