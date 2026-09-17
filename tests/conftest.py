import pytest


@pytest.fixture(autouse=True)
def isolated_folder(tmp_path, monkeypatch):
    """Run every test in an empty folder, so a gp2midi.toml where pytest is started is not picked up."""
    monkeypatch.chdir(tmp_path)
    return tmp_path
