import os

import pytest

from tama.config import get_settings


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    """Los tests no leen el `.env` real, ni variables TAMA_*, ni API keys del entorno."""
    monkeypatch.chdir(tmp_path)
    for name in list(os.environ):
        if name.startswith("TAMA_") or name in {
            "OLLAMA_HOST",
            "OLLAMA_API_KEY",
            "ANTHROPIC_API_KEY",
        }:
            monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
