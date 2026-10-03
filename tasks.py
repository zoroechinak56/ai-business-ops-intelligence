"""Windows-friendly command runner for common project tasks."""

import argparse
from collections.abc import Callable
from pathlib import Path
import shutil
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parent


def _run_python_module(module: str, *arguments: str) -> None:
    """Run a Python module using the current interpreter."""
    subprocess.run(
        [sys.executable, "-m", module, *arguments],
        cwd=PROJECT_ROOT,
        check=True,
    )


def setup() -> None:
    """Install the pinned project dependencies into the active environment."""
    _run_python_module("pip", "install", "-r", "requirements.txt")


def test() -> None:
    """Run the project's test suite."""
    _run_python_module("pytest")


def lint() -> None:
    """Run Ruff against application and test code."""
    _run_python_module("ruff", "check", "src", "tests", "tasks.py")


def clean() -> None:
    """Remove test/lint caches and Python bytecode from project code folders."""
    for cache_name in (".pytest_cache", ".ruff_cache"):
        cache_path = PROJECT_ROOT / cache_name
        if cache_path.exists():
            shutil.rmtree(cache_path)

    for folder_name in ("src", "tests"):
        folder_path = PROJECT_ROOT / folder_name
        if not folder_path.exists():
            continue
        for cache_path in folder_path.rglob("__pycache__"):
            shutil.rmtree(cache_path)


TASKS: dict[str, Callable[[], None]] = {
    "setup": setup,
    "test": test,
    "lint": lint,
    "clean": clean,
}


def main() -> None:
    """Parse and execute one supported project task."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", choices=TASKS)
    arguments = parser.parse_args()
    TASKS[arguments.task]()


if __name__ == "__main__":
    main()