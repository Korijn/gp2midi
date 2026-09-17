import os
from pathlib import Path

import pytest

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def load_env(path: Path = ENV_FILE) -> None:
    """Read KEY=VALUE lines from .env, so a path to the scores can be kept out of the repository.

    Anything already in the environment wins, and unreadable or malformed lines are ignored:
    this only feeds the optional checks in test_guitar_pro.py.
    """
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_env()  # before pytest imports the test modules, which read the environment as they load


@pytest.fixture(autouse=True)
def isolated_folder(tmp_path, monkeypatch):
    """Run every test in an empty folder, so a gp2midi.toml where pytest is started is not picked up."""
    monkeypatch.chdir(tmp_path)
    return tmp_path
