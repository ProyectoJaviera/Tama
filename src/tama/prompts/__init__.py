"""Prompts de Tama, versionados como archivos Markdown junto al código."""

from functools import cache
from importlib.resources import files


@cache
def load(name: str) -> str:
    """Devuelve el contenido de `prompts/<name>.md`."""
    return files(__package__).joinpath(f"{name}.md").read_text(encoding="utf-8").strip()


def identity() -> str:
    return load("identidad")
