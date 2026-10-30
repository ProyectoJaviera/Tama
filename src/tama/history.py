"""Recorte del historial de la sesión para mantener el prompt corto (regla 6 de CLAUDE.md)."""

from tama.providers import Message


def estimate_tokens(text: str) -> int:
    # En español, ~3,5 caracteres por token en los tokenizadores de Qwen y Claude.
    return int(len(text) / 3.5) + 4


def trim(messages: list[Message], max_tokens: int) -> list[Message]:
    """Conserva los mensajes de sistema y los turnos más recientes que caben en `max_tokens`.

    El último mensaje siempre se conserva, aunque por sí solo supere el límite.
    """
    system = [m for m in messages if m["role"] == "system"]
    dialog = [m for m in messages if m["role"] != "system"]
    kept: list[Message] = []
    used = 0
    for message in reversed(dialog):
        cost = estimate_tokens((message.get("content") or "") + str(message.get("tool_calls", "")))
        if kept and used + cost > max_tokens:
            break
        kept.append(message)
        used += cost
    kept.reverse()
    while len(kept) > 1 and kept[0]["role"] != "user":
        kept.pop(0)  # que el diálogo empiece por el usuario
    return system + kept
