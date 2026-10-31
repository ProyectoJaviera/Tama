"""Memoria de sesión: el prompt inicial con identidad, perfil y un recuerdo de la última charla."""

from pathlib import Path

from tama import prompts
from tama.events import EventStore

MAX_PROFILE_CHARS = 1100  # ~300 tokens
MAX_RECALL_CHARS = 200
MAX_AUTO_RECALL_CHARS = 700  # ~200 tokens


def read_profile(memory_dir: Path) -> str:
    """Perfil de Renzo (`memoria/nucleo.md`), recortado para cuidar el contexto."""
    path = memory_dir / "nucleo.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()[:MAX_PROFILE_CHARS]


def last_conversation_line(store: EventStore) -> str:
    """Una línea sobre la conversación anterior, a partir de lo último que dijo Renzo."""
    last = store.last_conversation()
    if last is None:
        return ""
    day, messages = last
    said = " / ".join(" ".join(m.split()) for m in messages)
    if len(said) > MAX_RECALL_CHARS:
        said = said[:MAX_RECALL_CHARS] + "..."
    return f"Última conversación ({day}), Renzo dijo: {said}"


def recall_context(tools, text: str) -> str:
    """Busca en la memoria con las palabras del mensaje; devuelve lo hallado o nada.

    Lo usa solo la capa local: ese modelo casi no llama a search_nodes por sí mismo (ver spec 3).
    No interpreta la intención: si no hay coincidencias, no agrega nada al prompt.
    """
    words = " ".join(w for w in text.split() if len(w.strip("¿?¡!.,;:")) > 3)
    if not words:
        return ""
    outcome = tools.call("search_nodes", {"query": words})
    if not outcome.ok or outcome.text.startswith("No hay"):
        return ""
    return f"De tu memoria:\n{outcome.text[:MAX_AUTO_RECALL_CHARS]}"


def session_prompt(memory_dir: Path, store: EventStore) -> str:
    """Identidad + perfil + recuerdo: lo único que va siempre en el prompt."""
    parts = [prompts.identity()]
    profile = read_profile(memory_dir)
    if profile:
        parts.append(f"Perfil de Renzo:\n{profile}")
    line = last_conversation_line(store)
    if line:
        parts.append(line)
    return "\n\n".join(parts)
