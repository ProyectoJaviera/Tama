from tama import prompts


def test_identity_is_loaded_from_file():
    text = prompts.identity()
    assert text.startswith("Eres Tama")
    assert "Renzo" in text


def test_identity_is_short():
    # ~4 caracteres por token en español: 400 tokens ≈ 1.600 caracteres.
    assert len(prompts.identity()) < 1600
