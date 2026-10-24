from tama.config import Settings


def test_defaults_point_to_local_ollama():
    assert Settings().ollama_host == "http://127.0.0.1:11434"


def test_ollama_host_env_is_accepted(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://host.docker.internal:11434")
    assert Settings().ollama_host == "http://host.docker.internal:11434"


def test_tama_prefix_wins_and_scheme_is_added(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://otro:11434")
    monkeypatch.setenv("TAMA_OLLAMA_HOST", "127.0.0.1:11434/")
    assert Settings().ollama_host == "http://127.0.0.1:11434"
