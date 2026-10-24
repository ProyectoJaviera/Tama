import pytest

from tama.config import get_settings


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    """Los tests no leen el `.env` real ni variables TAMA_* del entorno."""
    monkeypatch.chdir(tmp_path)
    for name in (
        "OLLAMA_HOST",
        "TAMA_OLLAMA_HOST",
        "TAMA_LOCAL_MODEL",
        "TAMA_THINK",
        "TAMA_TIMEOUT",
        "TAMA_DATA_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
